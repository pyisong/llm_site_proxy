from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from models_map import PRIMARY_MODEL_IDS, resolve_search_profile
from storage_state import (
    extract_cookie_header,
    load_storage_state,
    storage_state_login_issue,
)
from web_client import (
    MetasoAuthError,
    MetasoRateLimitError,
    MetasoUpstreamError,
    MetasoWebClient,
    new_request_id,
)

LOGGER_NAME = "metaso_openai_proxy"
logger = logging.getLogger(LOGGER_NAME)
_LOG_MAX_CHARS = max(0, int(os.getenv("METASO_LOG_MAX_CHARS", "400")))


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
    return value[: max(0, max_chars - 3)] + "..."


def _require_local_key(authorization: str | None, local_api_key: str | None) -> None:
    if not local_api_key:
        return
    expected = f"Bearer {local_api_key}"
    if authorization != expected:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid API key", "type": "authentication_error"},
        )


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
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def last_user_query(messages: list[dict[str, object]]) -> str:
    last = ""
    for message in messages:
        if str(message.get("role", "")) == "user":
            text = _message_content_to_text(message.get("content"))
            if text.strip():
                last = text.strip()
    return last


def compose_browser_prompt(messages: list[dict[str, object]], *, reuse_session: bool = False) -> str:
    """与 DeepSeek/Kimi 等代理一致：把 system 拼进发给网页的提示。

    Metaso 上游只有单一 question 字段；若只传最后一条 user，会丢掉 JSON 约束等
    system 指令，导致 format / 配图提示词等结构化任务失败。
    """
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

    lines = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = _message_content_to_text(message.get("content"))
        if content:
            lines.append(f"{role_names.get(role, role.title())}: {content}")
    return "\n\n".join(lines).strip()


def completion_text_from_result(
    result: dict[str, Any],
    *,
    response_format: object | None = None,
) -> str:
    """优先返回无「参考来源」附录的 raw_content，避免破坏 JSON 结构化输出。"""
    raw = str(result.get("raw_content") or "").strip()
    display = str(result.get("content") or "").strip()
    want_json = (
        isinstance(response_format, dict)
        and str(response_format.get("type") or "").strip().lower() == "json_object"
    )
    stripped = raw.lstrip()
    if raw and (
        want_json
        or stripped.startswith("{")
        or stripped.startswith("[")
        or stripped.startswith("```")
        or ("参考来源" in display and "参考来源" not in raw)
    ):
        return raw
    return display or raw


def _parse_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def resolve_new_chat(
    payload: dict[str, Any],
    *,
    header: str | None,
    default: bool,
    session_id: str | None = None,
) -> bool:
    if "new_chat" in payload:
        return _parse_bool(payload.get("new_chat"), default)
    meta = payload.get("metadata")
    if isinstance(meta, dict) and "new_chat" in meta:
        return _parse_bool(meta.get("new_chat"), default)
    if header is not None:
        return _parse_bool(header, default)
    # 带了逻辑 session_id 且未显式指定时，默认续聊（避免 METASO_NEW_CHAT_PER_REQUEST=1 误开新会话）
    if session_id:
        return False
    return default


def _pick_str(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_profile_from_request(payload: dict[str, Any], headers: dict[str, str]):
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    scope = _pick_str(
        payload.get("metaso_scope"),
        meta.get("metaso_scope"),
        headers.get("x-metaso-scope"),
    )
    mode = _pick_str(
        payload.get("metaso_mode"),
        meta.get("metaso_mode"),
        headers.get("x-metaso-mode"),
    )
    return resolve_search_profile(payload.get("model"), scope=scope, mode=mode)


def openai_completion(content: str, *, model: str, request_id: str) -> dict[str, Any]:
    return {
        "id": request_id,
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
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def openai_chunk(
    delta: dict[str, Any],
    *,
    model: str,
    request_id: str,
    finish_reason: str | None = None,
) -> str:
    payload = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def map_upstream_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, MetasoAuthError):
        return _error(503, str(exc), "authentication_error")
    if isinstance(exc, MetasoRateLimitError):
        # 上游 searchV2 SSE 常以 200 + data:[TOO_MANY_REQUESTS] 返回；对外用 429
        msg = str(exc) or "秘塔限流"
        if "TOO_MANY_REQUESTS" in msg and "出口" not in msg:
            msg = (
                "秘塔限流 TOO_MANY_REQUESTS（登录态通常仍有效；"
                "机房出口易被封，请配置 METASO_HTTP_PROXY 或更换出口 IP）"
            )
        return _error(429, msg, "rate_limit_error")
    if isinstance(exc, MetasoUpstreamError):
        return _error(502, str(exc), "api_error")
    if isinstance(exc, ValueError):
        return _error(400, str(exc), "invalid_request_error")
    logger.exception("unexpected error")
    return _error(500, str(exc) or "internal error", "api_error")


def discover_storage_state_file() -> Path | None:
    explicit = (os.getenv("METASO_STORAGE_STATE_FILE") or "").strip()
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            Path("secrets/metaso_storage.json"),
            Path("/run/secrets/metaso_storage.json"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _resolve_outbound_proxy() -> str | None:
    """出站代理：优先 METASO_HTTP_PROXY，其次 HTTPS_PROXY / HTTP_PROXY。"""
    for key in ("METASO_HTTP_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def build_web_client_from_env() -> MetasoWebClient:
    path = discover_storage_state_file()
    if path is None:
        raise MetasoAuthError(
            "未找到 metaso_storage.json，请先运行 python3 -m save_storage_state"
        )
    state = load_storage_state(path)
    issue = storage_state_login_issue(state)
    if issue:
        raise MetasoAuthError(issue)
    cookie = extract_cookie_header(state)
    timeout = float(os.getenv("METASO_TIMEOUT", "300"))
    proxy = _resolve_outbound_proxy()
    if proxy:
        logger.info("metaso outbound proxy enabled: %s", proxy)
    return MetasoWebClient(cookie_header=cookie, timeout=timeout, proxy=proxy)


def create_app(
    *,
    web_client: MetasoWebClient | None = None,
    local_api_key: str | None = None,
    skip_storage: bool = False,
) -> FastAPI:
    if local_api_key is None:
        local_api_key = os.getenv("METASO_PROXY_API_KEY", "local-secret")
    default_new_chat = _parse_bool(os.getenv("METASO_NEW_CHAT_PER_REQUEST", "0"), False)

    state: dict[str, Any] = {"client": web_client}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = state.get("client")
        if client is None and not skip_storage:
            try:
                client = build_web_client_from_env()
                state["client"] = client
                await client.ensure_ready()
                logger.info("metaso web client ready")
            except Exception as exc:
                logger.warning("metaso web client not ready at startup: %s", exc)
        yield
        client = state.get("client")
        if client is not None:
            await client.aclose()

    app = FastAPI(title="Metaso OpenAI Proxy", lifespan=lifespan)

    try:
        from console_ingest import install_console_ingest_middleware
    except ImportError:
        from .console_ingest import install_console_ingest_middleware

    install_console_ingest_middleware(app, proxy_id="metaso-openai-proxy")

    def get_client() -> MetasoWebClient:
        client = state.get("client")
        if client is None:
            if skip_storage:
                raise MetasoAuthError("web client not configured")
            client = build_web_client_from_env()
            state["client"] = client
        return client

    @app.post("/v1/admin/reload-storage")
    async def reload_storage(authorization: str | None = Header(default=None)):
        """热加载 secrets 中的 storage（proxy-console 登录刷新后调用）。不重启进程。"""
        _require_local_key(authorization, local_api_key)
        if skip_storage:
            raise HTTPException(400, detail="storage disabled")
        old = state.get("client")
        try:
            client = build_web_client_from_env()
            await client.ensure_ready()
        except Exception as exc:
            return map_upstream_error(exc)
        state["client"] = client
        if old is not None and old is not client:
            try:
                await old.aclose()
            except Exception:  # noqa: BLE001
                pass
        logger.info("metaso storage reloaded")
        return {"ok": True, "reloaded": True}

    @app.get("/health")
    async def health():
        client = state.get("client")
        status = "ok" if client is not None or skip_storage else "degraded"
        return {"status": status, "service": "metaso-openai-proxy"}

    @app.get("/__debug/routes")
    async def debug_routes(authorization: str | None = Header(default=None)):
        _require_local_key(authorization, local_api_key)
        return {
            "routes": sorted(
                {
                    getattr(r, "path", "")
                    for r in app.routes
                    if getattr(r, "path", None)
                }
            )
        }

    @app.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)):
        _require_local_key(authorization, local_api_key)
        return {
            "object": "list",
            "data": [
                {"id": mid, "object": "model", "owned_by": "metaso-web"}
                for mid in PRIMARY_MODEL_IDS
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request, authorization: str | None = Header(default=None)):
        _require_local_key(authorization, local_api_key)
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid JSON body", "invalid_request_error")
        if not isinstance(payload, dict):
            return _error(400, "body must be object", "invalid_request_error")

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return _error(400, "messages is required", "invalid_request_error")

        model = str(payload.get("model") or "metaso-chat-web")
        profile = resolve_profile_from_request(payload, {k.lower(): v for k, v in request.headers.items()})
        session_id = _pick_str(
            payload.get("session_id"),
            (payload.get("metadata") or {}).get("session_id")
            if isinstance(payload.get("metadata"), dict)
            else None,
        )
        new_chat = resolve_new_chat(
            payload,
            header=request.headers.get("x-metaso-new-chat"),
            default=default_new_chat,
            session_id=session_id,
        )
        query = compose_browser_prompt(messages, reuse_session=not new_chat)
        if not query:
            return _error(400, "missing user/system message content", "invalid_request_error")

        stream = _parse_bool(payload.get("stream"), False)
        request_id = new_request_id()
        logger.info(
            "chat.request model=%s new_chat=%s session_id=%s stream=%s prompt_chars=%s",
            model,
            new_chat,
            session_id or "-",
            stream,
            len(query),
        )

        try:
            client = get_client()
            if stream:
                async def event_gen() -> AsyncIterator[bytes]:
                    yield openai_chunk({"role": "assistant", "content": ""}, model=model, request_id=request_id).encode()
                    citations: list[dict[str, Any]] = []
                    try:
                        async for event in client.chat_stream(
                            query,
                            profile,
                            session_id=session_id,
                            new_chat=new_chat,
                        ):
                            if event.get("type") == "text":
                                text = str(event.get("text") or "")
                                if text:
                                    yield openai_chunk(
                                        {"content": text},
                                        model=model,
                                        request_id=request_id,
                                    ).encode()
                            elif event.get("type") == "citation":
                                citations.append(event)
                            elif event.get("type") == "error":
                                msg = f"[{event.get('code')}]{event.get('msg')}"
                                yield openai_chunk(
                                    {"content": msg},
                                    model=model,
                                    request_id=request_id,
                                ).encode()
                        rf = payload.get("response_format")
                        want_json = (
                            isinstance(rf, dict)
                            and str(rf.get("type") or "").strip().lower() == "json_object"
                        )
                        if citations and not want_json:
                            appendix = "\n\n参考来源:\n" + "\n".join(
                                f"{i}. {c.get('title') or c.get('link')} — {c.get('link')}"
                                for i, c in enumerate(citations, 1)
                                if c.get("link")
                            )
                            yield openai_chunk(
                                {"content": appendix},
                                model=model,
                                request_id=request_id,
                            ).encode()
                        yield openai_chunk({}, model=model, request_id=request_id, finish_reason="stop").encode()
                        yield b"data: [DONE]\n\n"
                    except Exception as exc:
                        yield openai_chunk(
                            {"content": f"\n\n[error] {exc}"},
                            model=model,
                            request_id=request_id,
                            finish_reason="stop",
                        ).encode()
                        yield b"data: [DONE]\n\n"

                return StreamingResponse(event_gen(), media_type="text/event-stream")

            result = await client.chat(
                query,
                profile,
                session_id=session_id,
                new_chat=new_chat,
            )
            text = completion_text_from_result(
                result,
                response_format=payload.get("response_format"),
            )
            body = openai_completion(text, model=model, request_id=request_id)
            if result.get("session_id"):
                body["session_id"] = result["session_id"]
            return JSONResponse(body)
        except Exception as exc:
            return map_upstream_error(exc)

    @app.post("/v1/metaso/search")
    async def metaso_search(request: Request, authorization: str | None = Header(default=None)):
        _require_local_key(authorization, local_api_key)
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid JSON body", "invalid_request_error")
        if not isinstance(payload, dict):
            return _error(400, "body must be object", "invalid_request_error")
        q = str(payload.get("q") or "").strip()
        if not q:
            return _error(400, "q is required", "invalid_request_error")
        profile = resolve_search_profile(
            payload.get("model"),
            scope=payload.get("scope"),
            mode=payload.get("mode"),
        )
        size = int(payload.get("size") or 10)
        try:
            result = await get_client().search(q, profile, size=size)
            return JSONResponse(result)
        except Exception as exc:
            return map_upstream_error(exc)

    @app.post("/v1/metaso/reader")
    async def metaso_reader(request: Request, authorization: str | None = Header(default=None)):
        _require_local_key(authorization, local_api_key)
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid JSON body", "invalid_request_error")
        if not isinstance(payload, dict):
            return _error(400, "body must be object", "invalid_request_error")
        url = str(payload.get("url") or "").strip()
        if not url:
            return _error(400, "url is required", "invalid_request_error")
        fmt = str(payload.get("format") or "markdown")
        try:
            result = await get_client().reader(url, format=fmt)
            return JSONResponse(result)
        except Exception as exc:
            return map_upstream_error(exc)

    @app.post("/v1/metaso/chat")
    async def metaso_chat(request: Request, authorization: str | None = Header(default=None)):
        _require_local_key(authorization, local_api_key)
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid JSON body", "invalid_request_error")
        if not isinstance(payload, dict):
            return _error(400, "body must be object", "invalid_request_error")
        q = str(payload.get("q") or "").strip()
        if not q:
            return _error(400, "q is required", "invalid_request_error")
        profile = resolve_search_profile(
            payload.get("model"),
            scope=payload.get("scope"),
            mode=payload.get("mode"),
        )
        stream = _parse_bool(payload.get("stream"), False)
        new_chat = _parse_bool(payload.get("new_chat"), default_new_chat)
        session_id = _pick_str(payload.get("session_id"))
        request_id = new_request_id()
        model = f"metaso-{profile.mode}" + (f"-{profile.scope}" if profile.scope != "webpage" else "")
        try:
            client = get_client()
            if stream:
                async def event_gen() -> AsyncIterator[bytes]:
                    try:
                        async for event in client.chat_stream(
                            q, profile, session_id=session_id, new_chat=new_chat
                        ):
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                        yield b"data: [DONE]\n\n"
                    except Exception as exc:
                        yield f"data: {json.dumps({'type': 'error', 'msg': str(exc)}, ensure_ascii=False)}\n\n".encode()
                        yield b"data: [DONE]\n\n"

                return StreamingResponse(event_gen(), media_type="text/event-stream")

            result = await client.chat(q, profile, session_id=session_id, new_chat=new_chat)
            return JSONResponse({"id": request_id, "model": model, **result})
        except Exception as exc:
            return map_upstream_error(exc)

    return app
