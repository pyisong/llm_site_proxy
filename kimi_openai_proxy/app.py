from __future__ import annotations

import os
import json
import time
import uuid
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
MODELS = [
    {"id": "kimi-k3", "object": "model", "owned_by": "moonshot"},
    {"id": "kimi-k2.7-code", "object": "model", "owned_by": "moonshot"},
    {"id": "kimi-k2.7-code-highspeed", "object": "model", "owned_by": "moonshot"},
    {"id": "kimi-k2.6", "object": "model", "owned_by": "moonshot"},
    {"id": "kimi-k2.5", "object": "model", "owned_by": "moonshot"},
    {"id": "moonshot-v1-8k", "object": "model", "owned_by": "moonshot"},
    {"id": "moonshot-v1-32k", "object": "model", "owned_by": "moonshot"},
    {"id": "moonshot-v1-128k", "object": "model", "owned_by": "moonshot"},
]
BROWSER_MODELS = [
    {"id": "kimi-chat-web", "object": "model", "owned_by": "kimi-web"},
]
LOGGER_NAME = "kimi_openai_proxy"
logger = logging.getLogger(LOGGER_NAME)
_LOG_MAX_CHARS = max(0, int(os.getenv("KIMI_LOG_MAX_CHARS", "500")))


def _error(status_code: int, message: str, error_type: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type}},
    )


def _truncate(value: str, limit: int | None = None) -> str:
    max_chars = _LOG_MAX_CHARS if limit is None else limit
    if max_chars <= 0:
        return "<omitted>"
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}...<truncated {len(value) - max_chars} chars>"


def _json_for_log(payload: Any) -> str:
    return _truncate(json.dumps(_safe_json(payload), ensure_ascii=False))


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "cookie", "set-cookie"}:
            safe[key] = "<redacted>"
        else:
            safe[key] = value
    return safe


def _safe_json(payload: Any) -> Any:
    if isinstance(payload, dict):
        safe = {}
        for key, value in payload.items():
            if key.lower() in {"authorization", "cookie", "token", "api_key", "apikey", "password"}:
                safe[key] = "<redacted>"
            else:
                safe[key] = _safe_json(value)
        return safe
    if isinstance(payload, list):
        return [_safe_json(item) for item in payload]
    return payload


def _request_body_for_log(body: bytes) -> str:
    if not body:
        return ""
    try:
        return _json_for_log(json.loads(body))
    except Exception:
        return _truncate(body.decode("utf-8", errors="replace"))


def _require_local_key(authorization: str | None, local_api_key: str | None) -> None:
    if not local_api_key:
        return
    expected = f"Bearer {local_api_key}"
    if authorization != expected:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid API key", "type": "authentication_error"},
        )


async def _stream_response(response: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_bytes():
            if chunk:
                yield chunk
    finally:
        await response.aclose()


def _message_content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif item.get("type") == "image_url":
                image_url = item.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                parts.append(f"[image_url omitted: {url}]")
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def compose_browser_prompt(messages: list[dict[str, object]], *, reuse_session: bool = False) -> str:
    role_names = {
        "system": "System",
        "user": "User",
        "assistant": "Assistant",
        "tool": "Tool",
    }
    if reuse_session:
        system_text = ""
        last_user_text = ""
        for message in messages:
            role = str(message.get("role", "user"))
            content = _message_content_to_text(message.get("content"))
            if not content:
                continue
            if role == "system":
                system_text = content
            elif role == "user":
                last_user_text = content
        lines: list[str] = []
        if system_text:
            lines.append(f"{role_names['system']}: {system_text}")
        if last_user_text:
            lines.append(f"{role_names['user']}: {last_user_text}")
        return "\n\n".join(lines).strip()

    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = _message_content_to_text(message.get("content"))
        if content:
            lines.append(f"{role_names.get(role, role.title())}: {content}")
    return "\n\n".join(lines).strip()


def _parse_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "default"}:
            return None
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_web_mode(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid Kimi web mode: {value!r}")
    normalized = value.strip().lower()
    if normalized in {"", "default"}:
        return None
    aliases = {
        # 快速（页面显示「快速」，帮助中心称 K2.6）
        "fast": "fast",
        "quick": "fast",
        "normal": "fast",
        "k2.6": "fast",
        "k2.6-fast": "fast",
        "k2.6 quick": "fast",
        "快速": "fast",
        "快速模式": "fast",
        "k2.6 快速": "fast",
        # 快速 + 进阶思考
        "thinking": "thinking",
        "think": "thinking",
        "reasoning": "thinking",
        "reason": "thinking",
        "k2.6-thinking": "thinking",
        "k2.6 thinking": "thinking",
        "思考": "thinking",
        "思考模式": "thinking",
        "k2.6 思考": "thinking",
        "快速进阶": "thinking",
        # K3
        "k3": "k3",
        "agent": "k3",
        "kimi-k3": "k3",
        "k3-standard": "k3",
        "k3 standard": "k3",
        "k3_advanced": "k3_advanced",
        "k3-advanced": "k3_advanced",
        "k3 advanced": "k3_advanced",
        "k3进阶": "k3_advanced",
        "k3 进阶": "k3_advanced",
        "k3_extreme": "k3_extreme",
        "k3-extreme": "k3_extreme",
        "k3 extreme": "k3_extreme",
        "k3_max": "k3_extreme",
        "k3-max": "k3_extreme",
        "k3极致": "k3_extreme",
        "k3 极致": "k3_extreme",
        # K3 集群
        "k3_cluster": "k3_cluster",
        "k3-cluster": "k3_cluster",
        "k3 cluster": "k3_cluster",
        "k3集群": "k3_cluster",
        "k3 集群": "k3_cluster",
        "agent_group": "k3_cluster",
        "agent-group": "k3_cluster",
        "agents": "k3_cluster",
        "集群": "k3_cluster",
        "agent集群": "k3_cluster",
        "agent 集群": "k3_cluster",
        "k3_cluster_advanced": "k3_cluster_advanced",
        "k3-cluster-advanced": "k3_cluster_advanced",
        "k3_cluster_extreme": "k3_cluster_extreme",
        "k3-cluster-extreme": "k3_cluster_extreme",
        # 旧 K2.6 Agent 文案 → 映射到新模型
        "k2.6-agent": "k3",
        "k2.6 agent": "k3",
        "agent模式": "k3",
        "k2.6-agent-group": "k3_cluster",
        "k2.6 agent group": "k3_cluster",
        "k2.6 agent 集群": "k3_cluster",
    }
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"invalid Kimi web mode: {value!r}")


def _parse_reasoning_effort(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid Kimi reasoning effort: {value!r}")
    normalized = value.strip().lower()
    if normalized in {"", "default"}:
        return None
    aliases = {
        "standard": "standard",
        "low": "standard",
        "normal": "standard",
        "标准": "standard",
        "advanced": "advanced",
        "high": "advanced",
        "进阶": "advanced",
        "extreme": "extreme",
        "max": "extreme",
        "极致": "extreme",
    }
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"invalid Kimi reasoning effort: {value!r}")


def resolve_new_chat(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: bool,
) -> bool:
    """Resolve whether browser mode should start a fresh Kimi web session.

    Priority (first match wins):
    1. Request body ``new_chat``
    2. Request body ``metadata.new_chat`` (OpenAI SDK ``extra_body``)
    3. HTTP header ``X-Kimi-New-Chat``
    4. Environment default ``KIMI_NEW_CHAT_PER_REQUEST``
    """
    if "new_chat" in payload:
        parsed = _parse_bool(payload["new_chat"])
        if parsed is not None:
            return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "new_chat" in metadata:
        parsed = _parse_bool(metadata["new_chat"])
        if parsed is not None:
            return parsed

    if header is not None:
        parsed = _parse_bool(header)
        if parsed is not None:
            return parsed

    return default


def resolve_web_mode(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: str = "fast",
) -> str:
    """Resolve the Kimi web chat mode preset for browser mode.

    Priority mirrors ``new_chat``:
    1. Request body ``kimi_mode``
    2. Request body ``metadata.kimi_mode``
    3. HTTP header ``X-Kimi-Mode``
    4. Environment/backend default

    Presets map to page model + effort, e.g. ``fast``→快速/标准, ``k3``→K3/标准,
    ``thinking``→快速/进阶, ``k3_cluster``→K3 集群/标准. Legacy ``agent`` /
    ``agent_group`` map to ``k3`` / ``k3_cluster``.
    """
    if "kimi_mode" in payload:
        parsed = _parse_web_mode(payload["kimi_mode"])
        if parsed is not None:
            return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "kimi_mode" in metadata:
        parsed = _parse_web_mode(metadata["kimi_mode"])
        if parsed is not None:
            return parsed

    if header is not None:
        parsed = _parse_web_mode(header)
        if parsed is not None:
            return parsed

    parsed_default = _parse_web_mode(default)
    return parsed_default or "fast"


def resolve_reasoning_effort(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: str | None = None,
) -> str | None:
    """Resolve optional thinking-intensity override for browser mode.

    Priority:
    1. Request body ``reasoning_effort`` / ``kimi_effort``
    2. Request body ``metadata.reasoning_effort`` / ``metadata.kimi_effort``
    3. HTTP header ``X-Kimi-Reasoning-Effort``
    4. ``default`` (usually None → use preset from ``kimi_mode``)
    """
    for key in ("reasoning_effort", "kimi_effort"):
        if key in payload:
            parsed = _parse_reasoning_effort(payload[key])
            if parsed is not None:
                return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("reasoning_effort", "kimi_effort"):
            if key in metadata:
                parsed = _parse_reasoning_effort(metadata[key])
                if parsed is not None:
                    return parsed

    if header is not None:
        parsed = _parse_reasoning_effort(header)
        if parsed is not None:
            return parsed

    if default is None:
        return None
    return _parse_reasoning_effort(default)


def resolve_deep_thinking(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: bool = False,
) -> bool:
    """Resolve whether Kimi web deep thinking should be enabled.

    On the current Kimi UI this maps to thinking intensity 「进阶」 when the
    preset would otherwise stay at 「标准」. Explicit ``reasoning_effort`` wins.
    """
    if "deep_thinking" in payload:
        parsed = _parse_bool(payload["deep_thinking"])
        if parsed is not None:
            return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "deep_thinking" in metadata:
        parsed = _parse_bool(metadata["deep_thinking"])
        if parsed is not None:
            return parsed

    if header is not None:
        parsed = _parse_bool(header)
        if parsed is not None:
            return parsed

    return default


def extract_session_id(payload: dict[str, Any]) -> str | None:
    """Optional client session id for logging / correlation (browser backend)."""
    raw = payload.get("session_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        mid = metadata.get("session_id")
        if isinstance(mid, str) and mid.strip():
            return mid.strip()
    return None


def _openai_chat_response(model: str, content: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-browser-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


async def _browser_stream(model: str, content: str) -> AsyncIterator[bytes]:
    chunk = {
        "id": f"chatcmpl-browser-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
    done = {
        **chunk,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


def create_app(
    *,
    local_api_key: str | None = None,
    kimi_api_key: str | None = None,
    kimi_base_url: str = DEFAULT_BASE_URL,
    backend_mode: str | None = None,
    upstream_client: httpx.AsyncClient | None = None,
    browser_backend: Any | None = None,
) -> FastAPI:
    logging.basicConfig(level=os.getenv("KIMI_LOG_LEVEL", "INFO"))
    local_api_key = local_api_key if local_api_key is not None else os.getenv("KIMI_PROXY_API_KEY", "local-secret")
    kimi_api_key = kimi_api_key if kimi_api_key is not None else os.getenv("KIMI_API_KEY")
    kimi_base_url = os.getenv("KIMI_BASE_URL", kimi_base_url).rstrip("/")
    backend_mode = (backend_mode or os.getenv("KIMI_BACKEND", "browser")).lower()
    owns_client = upstream_client is None
    client = upstream_client or httpx.AsyncClient(timeout=None)
    owns_browser_backend = browser_backend is None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        routes = [
            {"path": route.path, "methods": sorted(getattr(route, "methods", []) or [])}
            for route in app.routes
        ]
        logger.info(
            "service.start backend=%s local_auth=%s routes=%s",
            backend_mode,
            "enabled" if local_api_key else "disabled",
            json.dumps(routes, ensure_ascii=False),
        )
        try:
            yield
        finally:
            if owns_client:
                await client.aclose()
            if browser_backend is not None and owns_browser_backend:
                await browser_backend.aclose()

    app = FastAPI(title="kimi-openai-proxy", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def log_http_requests(request: Request, call_next):
        start = time.perf_counter()
        body = await request.body()
        logger.info(
            "request.start method=%s path=%s query=%s headers=%s body=%s",
            request.method,
            request.url.path,
            _truncate(request.url.query or ""),
            _json_for_log(_safe_headers(dict(request.headers))),
            _request_body_for_log(body),
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request.error method=%s path=%s", request.method, request.url.path)
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request.end method=%s path=%s status=%s duration_ms=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
        return _error(exc.status_code, str(exc.detail), "api_error")

    @app.exception_handler(TimeoutError)
    async def handle_timeout_error(_: Request, exc: TimeoutError) -> JSONResponse:
        return _error(504, str(exc), "timeout_error")

    try:
        from .browser_client import KimiBusyError
    except ImportError:
        from browser_client import KimiBusyError

    @app.exception_handler(KimiBusyError)
    async def handle_busy_error(_: Request, exc: KimiBusyError) -> JSONResponse:
        return _error(503, str(exc), "rate_limit_error")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/__debug/routes")
    async def debug_routes(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _require_local_key(authorization, local_api_key)
        routes = [
            {
                "path": route.path,
                "name": getattr(route, "name", None),
                "methods": sorted(getattr(route, "methods", []) or []),
            }
            for route in app.routes
        ]
        return {"backend": backend_mode, "routes": routes}

    @app.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _require_local_key(authorization, local_api_key)
        return {"object": "list", "data": BROWSER_MODELS if backend_mode == "browser" else MODELS}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
        x_kimi_new_chat: str | None = Header(default=None, alias="X-Kimi-New-Chat"),
        x_kimi_mode: str | None = Header(default=None, alias="X-Kimi-Mode"),
        x_kimi_deep_thinking: str | None = Header(default=None, alias="X-Kimi-Deep-Thinking"),
        x_kimi_reasoning_effort: str | None = Header(default=None, alias="X-Kimi-Reasoning-Effort"),
    ):
        _require_local_key(authorization, local_api_key)
        payload = await request.json()
        logger.info("chat.request backend=%s payload=%s", backend_mode, _json_for_log(payload))

        if backend_mode == "browser":
            nonlocal browser_backend
            if browser_backend is None:
                try:
                    from .browser_client import BrowserKimiClient
                except ImportError:
                    from browser_client import BrowserKimiClient

                browser_backend = BrowserKimiClient.from_env()
            new_chat = resolve_new_chat(
                payload,
                header=x_kimi_new_chat,
                default=browser_backend.new_chat_per_request,
            )
            web_mode = resolve_web_mode(
                payload,
                header=x_kimi_mode,
                default=getattr(browser_backend, "default_web_mode", "fast"),
            )
            deep_thinking = resolve_deep_thinking(
                payload,
                header=x_kimi_deep_thinking,
                default=getattr(browser_backend, "default_deep_thinking", False),
            )
            reasoning_effort = resolve_reasoning_effort(
                payload,
                header=x_kimi_reasoning_effort,
            )
            session_id = extract_session_id(payload)
            logger.info(
                "chat.new_chat=%s session_id=%s web_mode=%s deep_thinking=%s reasoning_effort=%s",
                new_chat,
                session_id or "-",
                web_mode,
                deep_thinking,
                reasoning_effort or "-",
            )
            answer = await browser_backend.chat_completion(
                payload,
                new_chat=new_chat,
                session_id=session_id,
                web_mode=web_mode,
                deep_thinking=deep_thinking,
                reasoning_effort=reasoning_effort,
            )
            model = str(payload.get("model") or "kimi-chat-web")
            logger.info(
                "chat.response backend=browser model=%s content=%s",
                model,
                _truncate(answer),
            )
            if payload.get("stream"):
                return StreamingResponse(_browser_stream(model, answer), media_type="text/event-stream")
            return JSONResponse(content=_openai_chat_response(model, answer))

        if not kimi_api_key:
            return _error(500, "KIMI_API_KEY is not configured", "configuration_error")

        url = f"{kimi_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {kimi_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if payload.get("stream") else "application/json",
        }

        try:
            response = await client.request("POST", url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.exception("chat.upstream_error backend=official url=%s", url)
            return _error(502, f"Kimi upstream request failed: {exc}", "upstream_error")

        content_type = response.headers.get("content-type", "")
        if payload.get("stream") or content_type.startswith("text/event-stream"):
            logger.info(
                "chat.response backend=official stream=true status=%s content_type=%s",
                response.status_code,
                content_type,
            )
            return StreamingResponse(
                _stream_response(response),
                status_code=response.status_code,
                media_type="text/event-stream",
            )

        try:
            data = response.json()
        except ValueError:
            text = response.text
            await response.aclose()
            logger.info("chat.response backend=official status=%s non_json=%s", response.status_code, _truncate(text))
            return _error(response.status_code, text, "upstream_error")
        await response.aclose()
        logger.info(
            "chat.response backend=official status=%s payload=%s",
            response.status_code,
            _json_for_log(data),
        )
        return JSONResponse(status_code=response.status_code, content=data)

    return app
