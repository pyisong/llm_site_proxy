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


DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
MODELS = [
    {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
    {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
]
BROWSER_MODELS = [
    {"id": "deepseek-chat-web", "object": "model", "owned_by": "deepseek-web"},
]
LOGGER_NAME = "deepseek_openai_proxy"
logger = logging.getLogger(LOGGER_NAME)
_LOG_MAX_CHARS = max(0, int(os.getenv("DEEPSEEK_LOG_MAX_CHARS", "400")))


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
    total = len(value)
    probe = f"...<省略中间 omitted={total} total={total}>..."
    if len(probe) >= max_chars:
        return value[: max(0, max_chars - 3)] + "..."
    keep = max_chars - len(probe)
    head = keep // 2
    tail = keep - head
    omitted = total - head - tail
    marker = f"...<省略中间 omitted={omitted} total={total}>..."
    while head + tail + len(marker) > max_chars and (head > 0 or tail > 0):
        if head >= tail and head > 0:
            head -= 1
        elif tail > 0:
            tail -= 1
        else:
            break
        omitted = total - head - tail
        marker = f"...<省略中间 omitted={omitted} total={total}>..."
    if head + tail + len(marker) > max_chars:
        return value[: max(0, max_chars - 3)] + "..."
    if tail > 0:
        return f"{value[:head]}{marker}{value[-tail:]}"
    return f"{value[:head]}{marker}"



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
        raise ValueError(f"invalid DeepSeek web mode: {value!r}")
    normalized = value.strip().lower()
    if normalized in {"", "default"}:
        return None
    aliases = {
        "fast": "fast",
        "quick": "fast",
        "normal": "fast",
        "快速": "fast",
        "快速模式": "fast",
        "expert": "expert",
        "pro": "expert",
        "专家": "expert",
        "专家模式": "expert",
        "vision": "vision",
        "image": "vision",
        "image-understanding": "vision",
        "识图": "vision",
        "识图模式": "vision",
    }
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"invalid DeepSeek web mode: {value!r}")


def resolve_new_chat(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: bool,
) -> bool:
    """Resolve whether browser mode should start a fresh DeepSeek web session.

    Priority (first match wins):
    1. Request body ``new_chat``
    2. Request body ``metadata.new_chat`` (OpenAI SDK ``extra_body``)
    3. HTTP header ``X-DeepSeek-New-Chat``
    4. Environment default ``DEEPSEEK_NEW_CHAT_PER_REQUEST``
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
    """Resolve the DeepSeek web chat mode for browser mode.

    Priority mirrors ``new_chat``:
    1. Request body ``deepseek_mode``
    2. Request body ``metadata.deepseek_mode``
    3. HTTP header ``X-DeepSeek-Mode``
    4. Environment/backend default
    """
    if "deepseek_mode" in payload:
        parsed = _parse_web_mode(payload["deepseek_mode"])
        if parsed is not None:
            return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "deepseek_mode" in metadata:
        parsed = _parse_web_mode(metadata["deepseek_mode"])
        if parsed is not None:
            return parsed

    if header is not None:
        parsed = _parse_web_mode(header)
        if parsed is not None:
            return parsed

    parsed_default = _parse_web_mode(default)
    return parsed_default or "fast"


def resolve_deep_thinking(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: bool = False,
) -> bool:
    """Resolve whether DeepSeek web deep thinking should be enabled."""
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
    deepseek_api_key: str | None = None,
    deepseek_base_url: str = DEFAULT_BASE_URL,
    backend_mode: str | None = None,
    upstream_client: httpx.AsyncClient | None = None,
    browser_backend: Any | None = None,
) -> FastAPI:
    try:
        from .logging_setup import configure_logging, is_quiet_http_path
    except ImportError:
        from logging_setup import configure_logging, is_quiet_http_path

    configure_logging(env_var="DEEPSEEK_LOG_LEVEL")

    local_api_key = local_api_key if local_api_key is not None else os.getenv("DEEPSEEK_PROXY_API_KEY", "local-secret")
    deepseek_api_key = deepseek_api_key if deepseek_api_key is not None else os.getenv("DEEPSEEK_API_KEY")
    deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", deepseek_base_url).rstrip("/")
    backend_mode = (backend_mode or os.getenv("DEEPSEEK_BACKEND", "browser")).lower()
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

    app = FastAPI(title="deepseek-openai-proxy", version="0.1.0", lifespan=lifespan)

    try:
        from .console_ingest import install_console_ingest_middleware
    except ImportError:
        from console_ingest import install_console_ingest_middleware

    install_console_ingest_middleware(app, proxy_id="deepseek-openai-proxy")

    @app.middleware("http")
    async def log_http_requests(request: Request, call_next):
        quiet = is_quiet_http_path(request.method, request.url.path)
        start = time.perf_counter()
        body = await request.body()
        if not quiet:
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
        if not quiet:
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
        x_deepseek_new_chat: str | None = Header(default=None, alias="X-DeepSeek-New-Chat"),
        x_deepseek_mode: str | None = Header(default=None, alias="X-DeepSeek-Mode"),
        x_deepseek_deep_thinking: str | None = Header(default=None, alias="X-DeepSeek-Deep-Thinking"),
    ):
        _require_local_key(authorization, local_api_key)
        payload = await request.json()
        logger.info("chat.request backend=%s payload=%s", backend_mode, _json_for_log(payload))

        if backend_mode == "browser":
            nonlocal browser_backend
            if browser_backend is None:
                try:
                    from .browser_client import BrowserDeepSeekClient
                except ImportError:
                    from browser_client import BrowserDeepSeekClient

                browser_backend = BrowserDeepSeekClient.from_env()
            new_chat = resolve_new_chat(
                payload,
                header=x_deepseek_new_chat,
                default=browser_backend.new_chat_per_request,
            )
            web_mode = resolve_web_mode(
                payload,
                header=x_deepseek_mode,
                default=getattr(browser_backend, "default_web_mode", "fast"),
            )
            deep_thinking = resolve_deep_thinking(
                payload,
                header=x_deepseek_deep_thinking,
                default=getattr(browser_backend, "default_deep_thinking", False),
            )
            session_id = extract_session_id(payload)
            logger.info(
                "chat.new_chat=%s session_id=%s web_mode=%s deep_thinking=%s",
                new_chat,
                session_id or "-",
                web_mode,
                deep_thinking,
            )
            answer = await browser_backend.chat_completion(
                payload,
                new_chat=new_chat,
                session_id=session_id,
                web_mode=web_mode,
                deep_thinking=deep_thinking,
            )
            model = str(payload.get("model") or "deepseek-chat-web")
            logger.info(
                "chat.response backend=browser model=%s content=%s",
                model,
                _truncate(answer),
            )
            if payload.get("stream"):
                return StreamingResponse(_browser_stream(model, answer), media_type="text/event-stream")
            return JSONResponse(content=_openai_chat_response(model, answer))

        if not deepseek_api_key:
            return _error(500, "DEEPSEEK_API_KEY is not configured", "configuration_error")

        url = f"{deepseek_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {deepseek_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if payload.get("stream") else "application/json",
        }

        try:
            response = await client.request("POST", url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.exception("chat.upstream_error backend=official url=%s", url)
            return _error(502, f"DeepSeek upstream request failed: {exc}", "upstream_error")

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
