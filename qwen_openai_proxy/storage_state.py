from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def is_storage_state(payload: object) -> bool:
    return isinstance(payload, dict) and "origins" in payload


def load_storage_state(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("storage state 必须是 JSON 对象")
    if "cookies" not in payload and "origins" not in payload:
        raise ValueError("storage state 需包含 cookies 或 origins 字段")
    return payload


def _parse_token_value(raw: str | None) -> str | None:
    if not raw or raw == "null":
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip() or None
    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_bearer_token(state: dict[str, Any]) -> str | None:
    auth = state.get("qwen_auth")
    if isinstance(auth, dict):
        bearer = auth.get("bearer_token")
        if isinstance(bearer, str) and bearer.strip():
            token = bearer.strip()
            return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    for origin in state.get("origins") or []:
        if not isinstance(origin, dict):
            continue
        for item in origin.get("localStorage") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name not in {"token", "userToken"}:
                continue
            value = _parse_token_value(str(item.get("value") or ""))
            if value:
                return f"Bearer {value}"
    return None


def extract_qwen_session_id(state: dict[str, Any]) -> str | None:
    auth = state.get("qwen_auth")
    if isinstance(auth, dict):
        session_id = auth.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            return session_id.strip()

    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "")
        if name in {"session_id", "qwen_session_id", "token"}:
            value = str(cookie.get("value") or "").strip()
            if value:
                return value
    return None


def storage_state_login_issue(state: dict[str, Any]) -> str | None:
    bearer = extract_bearer_token(state)
    if bearer:
        return None

    return (
        "qwen_storage.json 缺少 Bearer 登录令牌（localStorage.token 为空且未捕获 authorization）。"
        "请在聊天页发送一条测试消息并收到回复后，再按 Enter 导出；"
        "或在 DevTools → Network 中查看 completions 请求的 authorization 头是否非空。"
    )


def inject_bearer_into_state(state: dict[str, Any], bearer_token: str) -> dict[str, Any]:
    token = bearer_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    enriched = json.loads(json.dumps(state))
    auth = dict(enriched.get("qwen_auth") or {})
    auth["bearer_token"] = token
    session_id = extract_qwen_session_id(enriched)
    if session_id:
        auth["session_id"] = session_id
    enriched["qwen_auth"] = auth

    token_json = json.dumps(token, ensure_ascii=False)
    origins = enriched.setdefault("origins", [])
    if not origins:
        origins.append({"origin": "https://chat.qwen.ai", "localStorage": []})

    for origin_data in origins:
        if not isinstance(origin_data, dict):
            continue
        if origin_data.get("origin") not in {None, "https://chat.qwen.ai"}:
            continue
        local_storage = origin_data.setdefault("localStorage", [])
        replaced = False
        for item in local_storage:
            if isinstance(item, dict) and item.get("name") == "token":
                item["value"] = token_json
                replaced = True
                break
        if not replaced:
            local_storage.append({"name": "token", "value": token_json})
        break

    return enriched


def merge_cookie_lists(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cookies in groups:
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "")
            domain = str(cookie.get("domain") or ".qwen.ai")
            path = str(cookie.get("path") or "/")
            if not name:
                continue
            merged[(name, domain, path)] = {k: v for k, v in cookie.items() if v is not None}
    return list(merged.values())


def prepare_storage_state(state: dict[str, Any]) -> dict[str, Any]:
    prepared = json.loads(json.dumps(state))
    bearer = extract_bearer_token(prepared)
    if bearer:
        prepared = inject_bearer_into_state(prepared, bearer)
    return prepared


def storage_state_summary(state: dict[str, Any]) -> str:
    session_id = extract_qwen_session_id(state)
    bearer = extract_bearer_token(state)
    cookie_names = [
        str(cookie.get("name"))
        for cookie in state.get("cookies") or []
        if isinstance(cookie, dict) and cookie.get("name")
    ]
    local_storage_count = sum(
        len(origin.get("localStorage") or [])
        for origin in state.get("origins") or []
        if isinstance(origin, dict)
    )
    return (
        f"session_id={'有' if session_id else '无'}, "
        f"bearer={'有' if bearer else '无'}, "
        f"cookies={len(cookie_names)} ({', '.join(cookie_names)}), "
        f"localStorage={local_storage_count}"
    )


async def apply_storage_state(context: Any, page: Any, state: dict[str, Any], *, chat_url: str) -> None:
    prepared = prepare_storage_state(state)
    cookies = merge_cookie_lists(prepared.get("cookies") or [])
    if cookies:
        normalized = []
        for cookie in cookies:
            item = {k: v for k, v in cookie.items() if v is not None}
            if "sameSite" in item and item["sameSite"] not in {"Strict", "Lax", "None"}:
                item.pop("sameSite", None)
            normalized.append(item)
        if normalized:
            await context.add_cookies(normalized)

    origins = prepared.get("origins") or []
    for origin_data in origins:
        if not isinstance(origin_data, dict):
            continue
        origin = origin_data.get("origin")
        local_storage = origin_data.get("localStorage") or []
        if not origin or not local_storage:
            continue
        await page.goto(origin, wait_until="domcontentloaded", timeout=60_000)
        for item in local_storage:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if name is None or value is None:
                continue
            await page.evaluate(
                "([name, value]) => localStorage.setItem(name, value)",
                [name, value],
            )

    await page.goto(chat_url, wait_until="domcontentloaded", timeout=60_000)
