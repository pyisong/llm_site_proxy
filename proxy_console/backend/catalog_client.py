"""Probe catalog services and persist connectivity."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from db import MODES, PROXIES, ingest_request, is_idle_since, save_connectivity

log = logging.getLogger("proxy_console.probe")

PROBE_TIMEOUT = float(os.getenv("CONSOLE_PROBE_TIMEOUT", "3.0"))
CHAT_PROBE_TIMEOUT = float(os.getenv("CONSOLE_CHAT_PROBE_TIMEOUT", "600"))
CHAT_PROBE_MESSAGE = (os.getenv("CONSOLE_CHAT_PROBE_MESSAGE") or "hi").strip() or "hi"
# Auto keepalive only when no traffic for this long (default 2 days)
KEEPALIVE_IDLE_SEC = float(os.getenv("CONSOLE_KEEPALIVE_IDLE_SEC", str(2 * 86400)))

PROBE_MODE = (os.getenv("CONSOLE_PROBE_MODE") or "host").strip().lower()

PUBLIC_HOST = os.getenv("CATALOG_PUBLIC_HOST", "127.0.0.1")
PROXY_PORTS = {
    "deepseek-openai-proxy": int(os.getenv("DEEPSEEK_PROXY_PORT", "18002")),
    "kimi-openai-proxy": int(os.getenv("KIMI_PROXY_PORT", "18003")),
    "stepfun-openai-proxy": int(os.getenv("STEPFUN_PROXY_PORT", "18004")),
    "qwen-openai-proxy": int(os.getenv("QWEN_PROXY_PORT", "18005")),
    "cursor-openai-bridge": int(os.getenv("CURSOR_BRIDGE_PORT", "8765")),
    "azure-tts-http-api": int(
        os.getenv("AZURE_TTS_HTTP_PORT") or os.getenv("AZURE_TTS_PORT", "8787")
    ),
}

PROXY_DOCKER_ROOTS = {
    "deepseek-openai-proxy": "http://deepseek-openai-proxy:8000",
    "kimi-openai-proxy": "http://kimi-openai-proxy:8000",
    "stepfun-openai-proxy": "http://stepfun-openai-proxy:8000",
    "qwen-openai-proxy": "http://qwen-openai-proxy:8000",
    "cursor-openai-bridge": "http://cursor-openai-bridge:8765",
    "azure-tts-http-api": "http://azure-tts-http-api:8787",
}

DEFAULT_CHAT_MODELS = {
    "deepseek-openai-proxy": "deepseek-chat-web",
    "kimi-openai-proxy": "kimi-chat-web",
    "stepfun-openai-proxy": "stepfun-chat-web",
    "qwen-openai-proxy": "qwen-chat-web",
    "cursor-openai-bridge": "composer-2",
}


def _roots(proxy_id: str) -> str:
    if PROBE_MODE == "docker":
        return PROXY_DOCKER_ROOTS[proxy_id]
    return f"http://{PUBLIC_HOST}:{PROXY_PORTS[proxy_id]}"


def _auth_headers(proxy_id: str) -> dict[str, str]:
    if proxy_id == "cursor-openai-bridge":
        key = (os.getenv("CURSOR_OPENAI_BRIDGE_API_KEY") or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}
    if proxy_id == "azure-tts-http-api":
        return {}
    env_map = {
        "deepseek-openai-proxy": "DEEPSEEK_PROXY_API_KEY",
        "kimi-openai-proxy": "KIMI_PROXY_API_KEY",
        "stepfun-openai-proxy": "STEPFUN_PROXY_API_KEY",
        "qwen-openai-proxy": "QWEN_PROXY_API_KEY",
    }
    key = (os.getenv(env_map.get(proxy_id, ""), "") or "local-secret").strip()
    return {"Authorization": f"Bearer {key}"}


def _clip(text: str | None, limit: int = 8000) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit} chars)"


def _record_request(
    *,
    proxy_id: str,
    mode: str,
    path: str,
    status_code: int | None,
    latency_ms: float,
    model: str | None,
    error: str | None,
    source: str,
    request_body: dict[str, Any] | None = None,
    response_body: Any = None,
    response_text: str | None = None,
) -> None:
    meta: dict[str, Any] = {"source": source, "probe": True}
    if request_body is not None:
        meta["request"] = request_body
    if response_text is not None:
        meta["response_text"] = _clip(response_text)
    if response_body is not None:
        # Keep structured body when small; otherwise store a compact summary
        try:
            raw = json.dumps(response_body, ensure_ascii=False)
            if len(raw) <= 12000:
                meta["response"] = response_body
            else:
                meta["response"] = {"_truncated": True, "preview": _clip(raw, 4000)}
        except TypeError:
            meta["response"] = _clip(str(response_body))
    ingest_request(
        {
            "proxy_id": proxy_id,
            "mode": mode,
            "path": path,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "model": model,
            "error": error,
            "meta": meta,
        }
    )


async def _probe_chat(
    client: httpx.AsyncClient,
    proxy_id: str,
    root: str,
) -> tuple[bool, str, int | None, str | None, str, dict[str, Any], Any, str | None]:
    """Send a real chat/TTS request.

    Returns: ok, detail, status, model, path, request_body, response_body, response_text
    """
    headers = _auth_headers(proxy_id)

    if proxy_id == "azure-tts-http-api":
        path = "/tts"
        url = f"{root}{path}"
        req_body: dict[str, Any] = {
            "text": CHAT_PROBE_MESSAGE,
            "return_json": True,
        }
        resp = await client.post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            json=req_body,
        )
        ok = resp.status_code < 400
        detail = f"tts HTTP {resp.status_code}"
        resp_body: Any = None
        resp_text: str | None = None
        try:
            resp_body = resp.json()
            if isinstance(resp_body, dict):
                # Drop huge base64 audio from stored detail
                slim = {
                    k: v
                    for k, v in resp_body.items()
                    if k not in {"audioBase64", "audio"}
                }
                if "audioBase64" in resp_body:
                    slim["audioBase64"] = f"<omitted {len(str(resp_body.get('audioBase64') or ''))} chars>"
                resp_body = slim
                resp_text = str(resp_body.get("meta") or resp_body.get("provider") or "tts ok")
        except Exception:  # noqa: BLE001
            resp_text = (resp.text or "")[:2000]
            resp_body = {"raw": _clip(resp.text, 2000)}
        if not ok:
            detail = f"{detail}: {(resp_text or '')[:200]}"
        return ok, detail, resp.status_code, None, path, req_body, resp_body, resp_text

    path = "/v1/chat/completions"
    model = DEFAULT_CHAT_MODELS.get(proxy_id, "default")
    url = f"{root}{path}"
    req_body = {
        "model": model,
        "messages": [{"role": "user", "content": CHAT_PROBE_MESSAGE}],
        "stream": False,
        "max_tokens": 64,
    }
    resp = await client.post(
        url,
        headers={**headers, "Content-Type": "application/json"},
        json=req_body,
    )
    ok = resp.status_code < 400
    detail = f"chat HTTP {resp.status_code}"
    resp_body = None
    resp_text = None
    try:
        resp_body = resp.json()
        if isinstance(resp_body, dict):
            choices = resp_body.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                content = (msg or {}).get("content") if isinstance(msg, dict) else None
                if content:
                    resp_text = str(content)
                    detail = f"chat ok ({resp_text[:48]})"
            if resp_text is None:
                resp_text = json.dumps(resp_body, ensure_ascii=False)[:500]
    except Exception:  # noqa: BLE001
        resp_text = (resp.text or "")[:2000]
        resp_body = {"raw": _clip(resp.text, 2000)}
    if not ok:
        detail = f"{detail}: {(resp_text or '')[:200]}"
    return ok, detail, resp.status_code, model, path, req_body, resp_body, resp_text


async def probe_one(
    proxy_id: str,
    mode: str,
    *,
    source: str = "manual_probe",
) -> dict[str, Any]:
    root = _roots(proxy_id)
    started = time.perf_counter()
    ok = False
    detail = "ok"
    status_code: int | None = None
    model: str | None = None
    request_body: dict[str, Any] | None = None
    response_body: Any = None
    response_text: str | None = None
    if mode == "chat":
        path = "/tts" if proxy_id == "azure-tts-http-api" else "/v1/chat/completions"
        model = None if proxy_id == "azure-tts-http-api" else DEFAULT_CHAT_MODELS.get(
            proxy_id, "default"
        )
    elif mode == "models":
        path = "/health" if proxy_id == "azure-tts-http-api" else "/v1/models"
    else:
        path = "/health"

    timeout = CHAT_PROBE_TIMEOUT if mode == "chat" else PROBE_TIMEOUT
    log.info(
        "probe start proxy=%s mode=%s source=%s timeout=%.0fs msg=%s",
        proxy_id,
        mode,
        source,
        timeout,
        CHAT_PROBE_MESSAGE if mode == "chat" else "-",
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if mode == "health":
                resp = await client.get(f"{root}{path}")
                status_code = resp.status_code
                ok = resp.status_code < 500
                detail = f"HTTP {resp.status_code}"
                request_body = {"method": "GET", "url": f"{root}{path}"}
                response_text = (resp.text or "")[:500]
                response_body = {"status_code": resp.status_code, "body": _clip(resp.text, 1000)}
            elif mode == "models":
                resp = await client.get(
                    f"{root}{path}",
                    headers=_auth_headers(proxy_id)
                    if proxy_id != "azure-tts-http-api"
                    else {},
                )
                status_code = resp.status_code
                ok = resp.status_code < 400
                detail = f"HTTP {resp.status_code}"
                request_body = {"method": "GET", "url": f"{root}{path}"}
                try:
                    response_body = resp.json()
                    response_text = json.dumps(response_body, ensure_ascii=False)[:500]
                except Exception:  # noqa: BLE001
                    response_text = (resp.text or "")[:500]
                    response_body = {"raw": _clip(resp.text, 1000)}
            elif mode == "chat":
                (
                    ok,
                    detail,
                    status_code,
                    model,
                    path,
                    request_body,
                    response_body,
                    response_text,
                ) = await _probe_chat(client, proxy_id, root)
            else:
                detail = f"unknown mode {mode}"
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = str(exc) or exc.__class__.__name__
        response_text = detail
        if mode == "chat" and request_body is None:
            if proxy_id == "azure-tts-http-api":
                request_body = {"text": CHAT_PROBE_MESSAGE, "return_json": True}
            else:
                request_body = {
                    "model": model or DEFAULT_CHAT_MODELS.get(proxy_id, "default"),
                    "messages": [{"role": "user", "content": CHAT_PROBE_MESSAGE}],
                    "stream": False,
                    "max_tokens": 64,
                }
        log.warning("probe failed proxy=%s mode=%s detail=%s", proxy_id, mode, detail)

    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    if mode == "chat":
        _record_request(
            proxy_id=proxy_id,
            mode="chat" if proxy_id != "azure-tts-http-api" else "tts",
            path=path,
            status_code=status_code,
            latency_ms=latency_ms,
            model=model,
            error=None if ok else detail,
            source=source,
            request_body=request_body,
            response_body=response_body,
            response_text=response_text,
        )

    result = save_connectivity(
        proxy_id, mode, ok=ok, latency_ms=latency_ms, detail=detail
    )
    log.info(
        "probe done proxy=%s mode=%s ok=%s latency_ms=%s detail=%s",
        proxy_id,
        mode,
        ok,
        latency_ms,
        detail[:120],
    )
    return result



async def probe_all(
    proxy_id: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    targets = [p[0] for p in PROXIES if proxy_id is None or p[0] == proxy_id]
    # Default manual probe: real chat only (meaningful connectivity)
    modes = [mode] if mode else ["chat"]
    if mode and mode not in MODES and mode != "chat":
        modes = [mode]
    results: list[dict[str, Any]] = []
    for pid in targets:
        for m in modes:
            results.append(await probe_one(pid, m, source="manual_probe"))
    return results


async def keepalive_tick() -> list[dict[str, Any]]:
    """Real chat probe for idle keepalive-enabled proxies (skip Cursor)."""
    results: list[dict[str, Any]] = []
    for pid, name, keepalive in PROXIES:
        if not keepalive:
            continue
        if not is_idle_since(pid, KEEPALIVE_IDLE_SEC):
            log.info(
                "keepalive skip %s (%s): traffic within %.0fh",
                name,
                pid,
                KEEPALIVE_IDLE_SEC / 3600,
            )
            continue
        log.info("keepalive chat probe %s (%s)", name, pid)
        results.append(await probe_one(pid, "chat", source="keepalive_probe"))
    return results
