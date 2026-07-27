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


def _parse_user_token_value(raw: str | None) -> str | None:
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
    auth = state.get("kimi_auth")
    if isinstance(auth, dict):
        bearer = auth.get("bearer_token")
        if isinstance(bearer, str) and bearer.strip():
            token = bearer.strip()
            return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    for origin in state.get("origins") or []:
        if not isinstance(origin, dict):
            continue
        for item in origin.get("localStorage") or []:
            if not isinstance(item, dict) or item.get("name") != "userToken":
                continue
            value = _parse_user_token_value(str(item.get("value") or ""))
            if value:
                return f"Bearer {value}"
    return None


def extract_ds_session_id(state: dict[str, Any]) -> str | None:
    auth = state.get("kimi_auth")
    if isinstance(auth, dict):
        session_id = auth.get("ds_session_id")
        if isinstance(session_id, str) and session_id.strip():
            return session_id.strip()

    for cookie in state.get("cookies") or []:
        if isinstance(cookie, dict) and cookie.get("name") == "ds_session_id":
            value = str(cookie.get("value") or "").strip()
            if value:
                return value
    return None


def storage_state_login_issue(state: dict[str, Any]) -> str | None:
    cookies = state.get("cookies") or []
    origins = state.get("origins") or []
    if cookies or origins:
        return None
    return "kimi_storage.json 未包含 cookies 或 origins，请重新运行 python3 -m save_storage_state 导出登录态。"


def inject_bearer_into_state(state: dict[str, Any], bearer_token: str) -> dict[str, Any]:
    token = bearer_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    enriched = json.loads(json.dumps(state))
    auth = dict(enriched.get("kimi_auth") or {})
    auth["bearer_token"] = token
    session_id = extract_ds_session_id(enriched)
    if session_id:
        auth["ds_session_id"] = session_id
    enriched["kimi_auth"] = auth

    user_token_json = json.dumps({"value": token, "__version": "0"}, ensure_ascii=False)
    origins = enriched.setdefault("origins", [])
    if not origins:
        origins.append({"origin": "https://www.kimi.com", "localStorage": []})

    for origin_data in origins:
        if not isinstance(origin_data, dict):
            continue
        if origin_data.get("origin") not in {None, "https://www.kimi.com", "https://chat.kimi.com"}:
            continue
        local_storage = origin_data.setdefault("localStorage", [])
        replaced = False
        for item in local_storage:
            if isinstance(item, dict) and item.get("name") == "userToken":
                item["value"] = user_token_json
                replaced = True
                break
        if not replaced:
            local_storage.append({"name": "userToken", "value": user_token_json})
        break

    return enriched


def merge_cookie_lists(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cookies in groups:
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "")
            domain = str(cookie.get("domain") or ".kimi.com")
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
