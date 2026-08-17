"""Fire-and-forget 请求上报到 proxy_console ``/api/ingest/*``。

环境变量：
- ``CONSOLE_INGEST_URL``：如 ``http://proxy-console:18020/api/ingest/request``
  （空则整段禁用，不影响主链路）
- ``CONSOLE_PROXY_ID``：写入 ``request_events.proxy_id``（如 ``cursor-openai-bridge``）
- ``CONSOLE_SKILL_USAGE_INGEST_URL``：可选；默认由 request URL 推导
  ``.../api/ingest/skill-usage``
- ``CONSOLE_INGEST_MAX_STR``：单字段截断长度，默认 8000
- ``CONSOLE_INGEST_MAX_JSON``：整段 JSON 上限，默认 12000
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any

log = logging.getLogger("console_ingest")

_EXACT_POST_PATHS = frozenset(
    {
        "/v1/chat/completions",
        "/v1/messages",
        "/tts",
        "/v1/tts",
    }
)
_PREFIX_POST_PATHS = (
    "/v1/images/",
    "/v1/videos/",
)


def ingest_url() -> str:
    return (os.getenv("CONSOLE_INGEST_URL") or "").strip()


def proxy_id_from_env() -> str:
    return (os.getenv("CONSOLE_PROXY_ID") or "").strip()


def skill_usage_ingest_url() -> str:
    explicit = (os.getenv("CONSOLE_SKILL_USAGE_INGEST_URL") or "").strip()
    if explicit:
        return explicit
    base = ingest_url()
    suffix = "/api/ingest/request"
    if base.endswith(suffix):
        return base[: -len(suffix)] + "/api/ingest/skill-usage"
    return ""


def _max_str() -> int:
    try:
        return max(500, int(os.getenv("CONSOLE_INGEST_MAX_STR", "8000")))
    except ValueError:
        return 8000


def _max_json() -> int:
    try:
        return max(1000, int(os.getenv("CONSOLE_INGEST_MAX_JSON", "12000")))
    except ValueError:
        return 12000


def normalize_path(path: str) -> str:
    return (path or "").split("?", 1)[0]


def should_ingest(method: str, path: str) -> bool:
    if (method or "").upper() != "POST":
        return False
    p = normalize_path(path)
    if p in _EXACT_POST_PATHS:
        return True
    return any(p.startswith(pref) for pref in _PREFIX_POST_PATHS)


def infer_mode(path: str) -> str:
    p = normalize_path(path)
    if p.startswith("/v1/images/") or "/images/" in p:
        return "image"
    if p.startswith("/v1/videos/") or "/videos/" in p:
        return "video"
    if p in ("/tts", "/v1/tts") or p.endswith("/tts"):
        return "tts"
    return "chat"


def clip_text(text: str | None, limit: int | None = None) -> str | None:
    if text is None:
        return None
    lim = _max_str() if limit is None else limit
    if len(text) <= lim:
        return text
    return text[:lim] + f"…(+{len(text) - lim} chars)"


def extract_model_from_body(body: bytes | None) -> str | None:
    parsed = parse_json_bytes(body)
    if not isinstance(parsed, dict):
        return None
    model = parsed.get("model")
    return model if isinstance(model, str) and model.strip() else None


def parse_json_bytes(body: bytes | None) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _header_param(header: str, name: str) -> str | None:
    target = name.lower() + "="
    for part in header.split(";"):
        item = part.strip()
        if item.lower().startswith(target):
            return item.split("=", 1)[1].strip().strip('"')
    return None


def _split_multipart_headers(part: bytes) -> tuple[dict[str, str], bytes]:
    sep = b"\r\n\r\n"
    idx = part.find(sep)
    if idx < 0:
        sep = b"\n\n"
        idx = part.find(sep)
        if idx < 0:
            return {}, part
    header_blob = part[:idx].decode("utf-8", errors="replace")
    payload = part[idx + len(sep) :]
    headers: dict[str, str] = {}
    for line in header_blob.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers, payload


def parse_multipart_form(body: bytes | None, content_type: str) -> dict[str, Any] | None:
    """把 multipart 表单收成可落库的 dict；文件只保留 filename / content_type / bytes。"""
    if not body:
        return None
    boundary = _header_param(content_type or "", "boundary")
    if not boundary:
        return None
    delim = b"--" + boundary.encode("utf-8", errors="replace")
    out: dict[str, Any] = {}
    for raw_part in body.split(delim):
        part = raw_part
        if part.startswith(b"\r\n"):
            part = part[2:]
        elif part.startswith(b"\n"):
            part = part[1:]
        if not part or part == b"--" or part.startswith(b"--"):
            continue
        if part.endswith(b"\r\n"):
            part = part[:-2]
        elif part.endswith(b"\n"):
            part = part[:-1]
        headers, payload = _split_multipart_headers(part)
        disp = headers.get("content-disposition") or ""
        name = _header_param(disp, "name")
        if not name:
            continue
        filename = _header_param(disp, "filename")
        if filename is not None:
            out[name] = {
                "filename": filename,
                "content_type": headers.get("content-type") or "application/octet-stream",
                "bytes": len(payload),
            }
            continue
        text = payload.decode("utf-8", errors="replace")
        if name == "metadata":
            try:
                parsed_meta = json.loads(text)
            except json.JSONDecodeError:
                parsed_meta = None
            if parsed_meta is not None:
                out[name] = parsed_meta
                continue
        out[name] = text
    return out or None


def parse_request_obj(body: bytes | None, content_type: str = "") -> Any:
    """JSON 原样解析；multipart（如 images/edits）抽出文本字段 + 上传文件摘要。"""
    if not body:
        return None
    parsed = parse_json_bytes(body)
    if parsed is not None:
        return parsed
    if "multipart/form-data" in (content_type or "").lower():
        form = parse_multipart_form(body, content_type)
        if form is not None:
            return form
    return {
        "_raw": True,
        "content_type": content_type or None,
        "bytes": len(body),
    }


def extract_model_from_request(request_obj: Any) -> str | None:
    if not isinstance(request_obj, dict):
        return None
    model = request_obj.get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


def _redact_string(value: str, *, key: str = "") -> str:
    k = (key or "").lower()
    if k in {"image", "audio", "data", "file", "b64_json", "audiobase64"} or (
        len(value) > 256 and value[:32].count("/") + value[:32].count("+") > 8
    ):
        return f"<omitted {len(value)} chars>"
    if value.startswith("data:") and ";base64," in value[:128]:
        return f"<data-url omitted {len(value)} chars>"
    return clip_text(value) or value


def sanitize_for_ingest(obj: Any, *, _depth: int = 0, _key: str = "") -> Any:
    """截断长文本、抹掉 base64，控制落库体积。"""
    if _depth > 12:
        return "<max-depth>"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _redact_string(obj, key=_key)
    if isinstance(obj, list):
        if len(obj) > 40:
            head = [sanitize_for_ingest(x, _depth=_depth + 1, _key=_key) for x in obj[:20]]
            return head + [f"<omitted {len(obj) - 20} items>"]
        return [sanitize_for_ingest(x, _depth=_depth + 1, _key=_key) for x in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in list(obj.items())[:80]:
            sk = str(k)
            out[sk] = sanitize_for_ingest(v, _depth=_depth + 1, _key=sk)
        if len(obj) > 80:
            out["_omitted_keys"] = len(obj) - 80
        return out
    return clip_text(str(obj))


def fit_json_meta(value: Any) -> Any:
    """确保 JSON 序列化后不超过 CONSOLE_INGEST_MAX_JSON。"""
    try:
        raw = json.dumps(value, ensure_ascii=False)
    except TypeError:
        return clip_text(str(value))
    limit = _max_json()
    if len(raw) <= limit:
        return value
    return {"_truncated": True, "preview": clip_text(raw, min(4000, limit))}


def extract_response_text(payload: Any, *, content_type: str = "") -> str | None:
    """从 OpenAI / Anthropic / SSE 中尽量抽出助手文本。"""
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        if "text/event-stream" in (content_type or "") or text.startswith("data:"):
            parts: list[str] = []
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                piece = extract_response_text(obj) or ""
                if piece:
                    parts.append(piece)
            joined = "".join(parts).strip()
            return clip_text(joined) if joined else clip_text(text)
        return clip_text(text)

    if not isinstance(payload, dict):
        return None

    # OpenAI chat.completion
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0] if isinstance(choices[0], dict) else None
        if c0:
            msg = c0.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return clip_text(msg["content"])
            delta = c0.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                return clip_text(delta["content"])
            if isinstance(c0.get("text"), str):
                return clip_text(c0["text"])

    # Anthropic messages
    content = payload.get("content")
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                parts.append(blk["text"])
        if parts:
            return clip_text("\n".join(parts))
    if isinstance(content, str):
        return clip_text(content)

    err = payload.get("error")
    if isinstance(err, dict) and isinstance(err.get("message"), str):
        return clip_text(err["message"])
    if isinstance(err, str):
        return clip_text(err)
    return None


def build_io_meta(
    *,
    request_obj: Any = None,
    response_bytes: bytes | None = None,
    content_type: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"source": "proxy_live"}
    if extra:
        meta.update(extra)
    if request_obj is not None:
        meta["request"] = fit_json_meta(sanitize_for_ingest(request_obj))
    if response_bytes:
        ct = (content_type or "").lower()
        parsed = parse_json_bytes(response_bytes)
        if parsed is not None:
            meta["response"] = fit_json_meta(sanitize_for_ingest(parsed))
            text = extract_response_text(parsed, content_type=ct)
            if text:
                meta["response_text"] = text
        else:
            try:
                text_body = response_bytes.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text_body = f"<binary {len(response_bytes)} bytes>"
            text = extract_response_text(text_body, content_type=ct)
            if text:
                meta["response_text"] = text
            else:
                meta["response_text"] = clip_text(text_body)
            meta["response"] = {
                "_raw": True,
                "content_type": content_type or None,
                "preview": clip_text(text_body, 4000),
            }
    return meta


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 2.5) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def schedule_request_ingest(payload: dict[str, Any]) -> None:
    """后台线程 POST；失败只 debug，不抛到业务路径。"""
    url = ingest_url()
    if not url:
        return
    if not payload.get("proxy_id"):
        return

    def _run() -> None:
        try:
            _post_json(url, payload)
        except Exception as exc:  # noqa: BLE001
            log.debug("request ingest failed: %s", exc)

    threading.Thread(target=_run, name="console-ingest", daemon=True).start()


def schedule_skill_usage_ingest(
    *,
    skill_name: str,
    label: str = "evidenced",
    request_id: str | None = None,
) -> None:
    url = skill_usage_ingest_url()
    if not url or not skill_name:
        return
    payload = {
        "skill_name": skill_name,
        "label": label or "evidenced",
        "request_id": request_id,
        "created_at": time.time(),
    }

    def _run() -> None:
        try:
            _post_json(url, payload)
        except Exception as exc:  # noqa: BLE001
            log.debug("skill-usage ingest failed: %s", exc)

    threading.Thread(target=_run, name="console-skill-ingest", daemon=True).start()


def report_request(
    *,
    proxy_id: str | None = None,
    path: str,
    status_code: int | None,
    latency_ms: float,
    model: str | None = None,
    error: str | None = None,
    mode: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    pid = (proxy_id or proxy_id_from_env()).strip()
    if not pid or not ingest_url():
        return
    body: dict[str, Any] = {
        "proxy_id": pid,
        "mode": mode or infer_mode(path),
        "path": normalize_path(path),
        "status_code": status_code,
        "latency_ms": latency_ms,
        "model": model,
        "error": (error[:500] if isinstance(error, str) and error else None),
        "meta": meta if meta is not None else {"source": "proxy_live"},
    }
    schedule_request_ingest(body)


def _chunk_to_bytes(chunk: Any) -> bytes:
    if chunk is None:
        return b""
    if isinstance(chunk, memoryview):
        return chunk.tobytes()
    if isinstance(chunk, bytearray):
        return bytes(chunk)
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, str):
        return chunk.encode("utf-8")
    return bytes(chunk)


async def _buffer_response_body(response: Any) -> tuple[bytes, Any]:
    """读取并重建响应，便于落库；保持状态码/头/media_type。"""
    from starlette.responses import Response

    parts: list[bytes] = []
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is not None:
        async for chunk in body_iterator:
            parts.append(_chunk_to_bytes(chunk))
    else:
        existing = getattr(response, "body", None)
        if existing:
            parts.append(_chunk_to_bytes(existing))
    body = b"".join(parts)

    headers = dict(response.headers) if getattr(response, "headers", None) is not None else {}
    headers.pop("content-length", None)
    new_resp = Response(
        content=body,
        status_code=int(getattr(response, "status_code", 200) or 200),
        headers=headers,
        media_type=getattr(response, "media_type", None),
        background=getattr(response, "background", None),
    )
    return body, new_resp


def install_console_ingest_middleware(app: Any, *, proxy_id: str | None = None) -> None:
    """给 FastAPI / Starlette app 挂 HTTP middleware（含 request/response 落库）。"""

    @app.middleware("http")
    async def console_ingest_middleware(request: Any, call_next: Any) -> Any:
        path = str(getattr(request.url, "path", "") or "")
        method = str(getattr(request, "method", "") or "")
        pid = (proxy_id or proxy_id_from_env()).strip()
        if not ingest_url() or not pid or not should_ingest(method, path):
            return await call_next(request)

        raw = b""
        request_obj: Any = None
        model: str | None = None
        try:
            req_ct = str(request.headers.get("content-type") or "")
            raw = await request.body()
            request_obj = parse_request_obj(raw, req_ct)
            model = extract_model_from_request(request_obj) or extract_model_from_body(raw)
        except Exception:  # noqa: BLE001
            request_obj = None
            model = None

        start = time.perf_counter()
        status_code: int | None = None
        error: str | None = None
        response_bytes: bytes | None = None
        content_type = ""
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 0) or 0) or None
            if status_code is not None and status_code >= 400:
                error = f"HTTP {status_code}"
            try:
                content_type = str(response.headers.get("content-type") or "")
            except Exception:  # noqa: BLE001
                content_type = ""
            try:
                response_bytes, response = await _buffer_response_body(response)
            except Exception as exc:  # noqa: BLE001
                log.debug("response capture skipped: %s", exc)
                response_bytes = None
            return response
        except Exception as exc:
            error = str(exc)[:500]
            raise
        finally:
            report_request(
                proxy_id=pid,
                path=path,
                status_code=status_code,
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
                model=model,
                error=error,
                meta=build_io_meta(
                    request_obj=request_obj,
                    response_bytes=response_bytes,
                    content_type=content_type,
                ),
            )
