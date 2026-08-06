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
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

try:
    from .media_input import resolve_reference_image_to_path
except ImportError:
    from media_input import resolve_reference_image_to_path


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODELS = [
    {"id": "qwen-max", "object": "model", "owned_by": "qwen"},
    {"id": "qwen-plus", "object": "model", "owned_by": "qwen"},
    {"id": "qwen-turbo", "object": "model", "owned_by": "qwen"},
]
BROWSER_MODELS = [
    {"id": "qwen-chat-web", "object": "model", "owned_by": "qwen-web"},
    {"id": "qwen-image-web", "object": "model", "owned_by": "qwen-web"},
    {"id": "qwen-video-web", "object": "model", "owned_by": "qwen-web"},
]
LOGGER_NAME = "qwen_openai_proxy"
logger = logging.getLogger(LOGGER_NAME)
_LOG_MAX_CHARS = max(0, int(os.getenv("QWEN_LOG_MAX_CHARS", "400")))


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


def _parse_qwen_mode(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid Qwen mode: {value!r}")
    normalized = value.strip().lower()
    if normalized in {"", "default", "chat", "auto"}:
        return None
    aliases = {
        "image": "image",
        "t2i": "image",
        "text-to-image": "image",
        "生图": "image",
        "生成图像": "image",
        "video": "video",
        "t2v": "video",
        "text-to-video": "video",
        "生视频": "video",
        "创建视频": "video",
        "deep_research": "deep_research",
        "research": "deep_research",
        "深入研究": "deep_research",
        "web_dev": "web_dev",
        "网页开发": "web_dev",
    }
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"invalid Qwen mode: {value!r}")


def resolve_new_chat(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: bool,
) -> bool:
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


def resolve_qwen_mode(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: str = "chat",
) -> str:
    if "qwen_mode" in payload:
        parsed = _parse_qwen_mode(payload["qwen_mode"])
        if parsed is not None:
            return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "qwen_mode" in metadata:
        parsed = _parse_qwen_mode(metadata["qwen_mode"])
        if parsed is not None:
            return parsed

    if header is not None:
        parsed = _parse_qwen_mode(header)
        if parsed is not None:
            return parsed

    parsed_default = _parse_qwen_mode(default)
    return parsed_default or "chat"


def _parse_response_mode(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "thinking" if value else None
    if not isinstance(value, str):
        raise ValueError(f"invalid Qwen response mode: {value!r}")
    normalized = value.strip().lower()
    if normalized in {"", "default"}:
        return None
    aliases = {
        "auto": "auto",
        "automatic": "auto",
        "自动": "auto",
        "自动模式": "auto",
        "thinking": "thinking",
        "think": "thinking",
        "reasoning": "thinking",
        "reason": "thinking",
        "思考": "thinking",
        "思考模式": "thinking",
        "fast": "fast",
        "quick": "fast",
        "normal": "fast",
        "快速": "fast",
        "快速模式": "fast",
    }
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"invalid Qwen response mode: {value!r}")


def resolve_response_mode(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: str = "auto",
) -> str:
    """Resolve Qwen web response mode: auto / thinking / fast."""
    for key in ("response_mode", "qwen_response_mode"):
        if key in payload:
            parsed = _parse_response_mode(payload[key])
            if parsed is not None:
                return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("response_mode", "qwen_response_mode"):
            if key in metadata:
                parsed = _parse_response_mode(metadata[key])
                if parsed is not None:
                    return parsed

    if header is not None:
        parsed = _parse_response_mode(header)
        if parsed is not None:
            return parsed

    # Legacy boolean thinking=true → 思考；false 不覆盖默认
    for key in ("thinking", "enable_thinking", "deep_thinking"):
        if key in payload:
            parsed = _parse_response_mode(payload[key])
            if parsed is not None:
                return parsed

    if isinstance(metadata, dict):
        for key in ("thinking", "enable_thinking", "deep_thinking"):
            if key in metadata:
                parsed = _parse_response_mode(metadata[key])
                if parsed is not None:
                    return parsed

    parsed_default = _parse_response_mode(default)
    return parsed_default or "auto"


def resolve_thinking(
    payload: dict[str, Any],
    *,
    header: str | None = None,
    default: bool = False,
) -> bool:
    for key in ("thinking", "enable_thinking", "deep_thinking"):
        if key in payload:
            parsed = _parse_bool(payload[key])
            if parsed is not None:
                return parsed

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("thinking", "enable_thinking", "deep_thinking"):
            if key in metadata:
                parsed = _parse_bool(metadata[key])
                if parsed is not None:
                    return parsed

    if header is not None:
        parsed = _parse_bool(header)
        if parsed is not None:
            return parsed

    return default


def extract_session_id(payload: dict[str, Any]) -> str | None:
    raw = payload.get("session_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        mid = metadata.get("session_id")
        if isinstance(mid, str) and mid.strip():
            return mid.strip()
    return None


def _extract_reference_image_fields(payload: dict[str, Any]) -> dict[str, str | None]:
    candidates: list[dict[str, Any]] = [payload]
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        candidates.append(metadata)

    image_url: str | None = None
    image_path: str | None = None
    for item in candidates:
        if image_url is None and isinstance(item.get("image_url"), str) and item["image_url"].strip():
            image_url = item["image_url"].strip()
        if image_path is None and isinstance(item.get("image"), str) and item["image"].strip():
            image_path = item["image"].strip()
    return {"image_url": image_url, "image_path": image_path}


def _cleanup_temp_paths(paths: list[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _resolve_reference_image_paths(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    fields = _extract_reference_image_fields(payload)
    if not fields["image_url"] and not fields["image_path"]:
        return [], []
    path, should_cleanup = await resolve_reference_image_to_path(
        image_url=fields["image_url"],
        image_path=fields["image_path"],
    )
    cleanup = [path] if should_cleanup else []
    return [path], cleanup


def _openai_chat_response(model: str, content: str, *, image_urls: list[str] | None = None) -> dict[str, Any]:
    if image_urls:
        content_parts: list[dict[str, Any]] = []
        if content:
            content_parts.append({"type": "text", "text": content})
        for url in image_urls:
            content_parts.append({"type": "image_url", "image_url": {"url": url}})
        message_content: str | list[dict[str, Any]] = content_parts
    else:
        message_content = content

    return {
        "id": f"chatcmpl-browser-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": message_content},
                "finish_reason": "stop",
            }
        ],
    }


def _openai_image_response(prompt: str, image_urls: list[str], *, model: str = "qwen-image-web") -> dict[str, Any]:
    return {
        "created": int(time.time()),
        "data": [
            {"url": url, "revised_prompt": prompt}
            for url in image_urls
        ],
        "model": model,
    }


def _openai_video_response(prompt: str, video_urls: list[str], *, model: str = "qwen-video-web") -> dict[str, Any]:
    return {
        "id": f"video-gen-{uuid.uuid4().hex}",
        "object": "video.generation",
        "created": int(time.time()),
        "model": model,
        "data": [
            {"url": url, "revised_prompt": prompt}
            for url in video_urls
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
    qwen_api_key: str | None = None,
    qwen_base_url: str = DEFAULT_BASE_URL,
    backend_mode: str | None = None,
    upstream_client: httpx.AsyncClient | None = None,
    browser_backend: Any | None = None,
) -> FastAPI:
    try:
        from .logging_setup import configure_logging, is_quiet_http_path
    except ImportError:
        from logging_setup import configure_logging, is_quiet_http_path

    configure_logging(env_var="QWEN_LOG_LEVEL")

    local_api_key = local_api_key if local_api_key is not None else os.getenv("QWEN_PROXY_API_KEY", "local-secret")
    qwen_api_key = qwen_api_key if qwen_api_key is not None else os.getenv("QWEN_API_KEY")
    qwen_base_url = os.getenv("QWEN_BASE_URL", qwen_base_url).rstrip("/")
    backend_mode = (backend_mode or os.getenv("QWEN_BACKEND", "browser")).lower()
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

    app = FastAPI(title="qwen-openai-proxy", version="0.1.0", lifespan=lifespan)

    try:
        from .console_ingest import install_console_ingest_middleware
    except ImportError:
        from console_ingest import install_console_ingest_middleware

    install_console_ingest_middleware(app, proxy_id="qwen-openai-proxy")

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

    async def _get_browser_backend() -> Any:
        nonlocal browser_backend
        if browser_backend is None:
            try:
                from .browser_client import BrowserQwenClient
            except ImportError:
                from browser_client import BrowserQwenClient

            browser_backend = BrowserQwenClient.from_env()
        return browser_backend

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
        x_qwen_new_chat: str | None = Header(default=None, alias="X-Qwen-New-Chat"),
        x_qwen_mode: str | None = Header(default=None, alias="X-Qwen-Mode"),
        x_qwen_response_mode: str | None = Header(default=None, alias="X-Qwen-Response-Mode"),
        x_qwen_thinking: str | None = Header(default=None, alias="X-Qwen-Thinking"),
    ):
        _require_local_key(authorization, local_api_key)
        payload = await request.json()
        logger.info("chat.request backend=%s payload=%s", backend_mode, _json_for_log(payload))

        if backend_mode == "browser":
            backend = await _get_browser_backend()
            new_chat = resolve_new_chat(
                payload,
                header=x_qwen_new_chat,
                default=backend.new_chat_per_request,
            )
            qwen_mode = resolve_qwen_mode(
                payload,
                header=x_qwen_mode,
                default=getattr(backend, "default_qwen_mode", "chat"),
            )
            response_mode = resolve_response_mode(
                payload,
                header=x_qwen_response_mode or x_qwen_thinking,
                default=getattr(backend, "default_response_mode", "auto"),
            )
            session_id = extract_session_id(payload)
            logger.info(
                "chat.new_chat=%s session_id=%s qwen_mode=%s response_mode=%s",
                new_chat,
                session_id or "-",
                qwen_mode,
                response_mode,
            )
            result = await backend.chat_completion(
                payload,
                new_chat=new_chat,
                session_id=session_id,
                qwen_mode=qwen_mode,
                response_mode=response_mode,
            )
            model = str(payload.get("model") or "qwen-chat-web")
            logger.info(
                "chat.response backend=browser model=%s content=%s images=%s videos=%s",
                model,
                _truncate(result.text),
                len(result.image_urls),
                len(result.video_urls),
            )
            if payload.get("stream"):
                stream_content = result.text
                if result.image_urls:
                    stream_content += "\n" + "\n".join(result.image_urls)
                if result.video_urls:
                    stream_content += "\n" + "\n".join(result.video_urls)
                return StreamingResponse(_browser_stream(model, stream_content), media_type="text/event-stream")
            return JSONResponse(
                content=_openai_chat_response(
                    model,
                    result.text,
                    image_urls=result.image_urls or None,
                )
            )

        if not qwen_api_key:
            return _error(500, "QWEN_API_KEY is not configured", "configuration_error")

        url = f"{qwen_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {qwen_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if payload.get("stream") else "application/json",
        }

        try:
            response = await client.request("POST", url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.exception("chat.upstream_error backend=official url=%s", url)
            return _error(502, f"Qwen upstream request failed: {exc}", "upstream_error")

        content_type = response.headers.get("content-type", "")
        if payload.get("stream") or content_type.startswith("text/event-stream"):
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
            return _error(response.status_code, text, "upstream_error")
        await response.aclose()
        return JSONResponse(status_code=response.status_code, content=data)

    @app.post("/v1/images/generations")
    async def image_generations(
        request: Request,
        authorization: str | None = Header(default=None),
        x_qwen_new_chat: str | None = Header(default=None, alias="X-Qwen-New-Chat"),
    ):
        _require_local_key(authorization, local_api_key)
        payload = await request.json()
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return _error(400, "prompt is required", "invalid_request_error")

        if backend_mode != "browser":
            return _error(501, "Image generation is only supported in browser backend", "not_supported")

        backend = await _get_browser_backend()
        new_chat = resolve_new_chat(payload, header=x_qwen_new_chat, default=backend.new_chat_per_request)
        ref_paths, cleanup_paths = await _resolve_reference_image_paths(payload)
        logger.info(
            "image.request prompt=%s new_chat=%s refs=%s",
            _truncate(prompt),
            new_chat,
            len(ref_paths),
        )
        try:
            result = await backend.generate_image(
                prompt,
                new_chat=new_chat,
                reference_image_paths=ref_paths or None,
            )
        finally:
            _cleanup_temp_paths(cleanup_paths)
        if not result.image_urls:
            return _error(
                502,
                result.text or "No image URL found in Qwen response",
                "upstream_error",
            )
        model = str(payload.get("model") or "qwen-image-web")
        response_payload = _openai_image_response(prompt, result.image_urls, model=model)
        logger.info("image.response count=%s", len(result.image_urls))
        return JSONResponse(content=response_payload)

    @app.post("/v1/images/edits")
    async def image_edits(
        authorization: str | None = Header(default=None),
        x_qwen_new_chat: str | None = Header(default=None, alias="X-Qwen-New-Chat"),
        prompt: str = Form(...),
        image: UploadFile = File(...),
        model: str | None = Form(default=None),
        new_chat: str | None = Form(default=None),
    ):
        _require_local_key(authorization, local_api_key)
        prompt_text = prompt.strip()
        if not prompt_text:
            return _error(400, "prompt is required", "invalid_request_error")
        if not image.filename:
            return _error(400, "image file is required", "invalid_request_error")

        if backend_mode != "browser":
            return _error(501, "Image edit is only supported in browser backend", "not_supported")

        image_bytes = await image.read()
        if not image_bytes:
            return _error(400, "image file is empty", "invalid_request_error")

        backend = await _get_browser_backend()
        payload = {"new_chat": new_chat} if new_chat is not None else {}
        resolved_new_chat = resolve_new_chat(payload, header=x_qwen_new_chat, default=backend.new_chat_per_request)
        ref_path, should_cleanup = await resolve_reference_image_to_path(
            image_bytes=image_bytes,
            image_filename=image.filename,
        )
        logger.info(
            "image.edit.request prompt=%s new_chat=%s filename=%s",
            _truncate(prompt_text),
            resolved_new_chat,
            image.filename,
        )
        try:
            result = await backend.edit_image(
                prompt_text,
                reference_image_paths=[ref_path],
                new_chat=resolved_new_chat,
            )
        finally:
            if should_cleanup:
                _cleanup_temp_paths([ref_path])
        if not result.image_urls:
            return _error(
                502,
                result.text or "No image URL found in Qwen response",
                "upstream_error",
            )
        response_model = model or "qwen-image-web"
        response_payload = _openai_image_response(prompt_text, result.image_urls, model=response_model)
        logger.info("image.edit.response count=%s", len(result.image_urls))
        return JSONResponse(content=response_payload)

    @app.post("/v1/videos/generations")
    async def video_generations(
        request: Request,
        authorization: str | None = Header(default=None),
        x_qwen_new_chat: str | None = Header(default=None, alias="X-Qwen-New-Chat"),
    ):
        _require_local_key(authorization, local_api_key)
        payload = await request.json()
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return _error(400, "prompt is required", "invalid_request_error")

        if backend_mode != "browser":
            return _error(501, "Video generation is only supported in browser backend", "not_supported")

        backend = await _get_browser_backend()
        new_chat = resolve_new_chat(payload, header=x_qwen_new_chat, default=backend.new_chat_per_request)
        ref_paths, cleanup_paths = await _resolve_reference_image_paths(payload)
        logger.info(
            "video.request prompt=%s new_chat=%s refs=%s",
            _truncate(prompt),
            new_chat,
            len(ref_paths),
        )
        try:
            result = await backend.generate_video(
                prompt,
                new_chat=new_chat,
                reference_image_paths=ref_paths or None,
            )
        finally:
            _cleanup_temp_paths(cleanup_paths)
        if not result.video_urls:
            return _error(
                502,
                result.text or "No video URL found in Qwen response",
                "upstream_error",
            )
        model = str(payload.get("model") or "qwen-video-web")
        response_payload = _openai_video_response(prompt, result.video_urls, model=model)
        logger.info("video.response count=%s", len(result.video_urls))
        return JSONResponse(content=response_payload)

    return app
