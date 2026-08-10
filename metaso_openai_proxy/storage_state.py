"""Load Playwright storage state and extract Metaso cookies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_storage_state(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("storage state 必须是 JSON 对象")
    if "cookies" not in payload and "origins" not in payload:
        raise ValueError("storage state 需包含 cookies 或 origins 字段")
    return payload


def extract_uid_sid(state: dict[str, Any]) -> tuple[str | None, str | None]:
    uid = sid = None
    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = str(cookie.get("value") or "").strip()
        if not value:
            continue
        if name == "uid":
            uid = value
        elif name == "sid":
            sid = value
    return uid, sid


def extract_cookie_header(state: dict[str, Any]) -> str:
    parts: list[str] = []
    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        if not name or cookie.get("value") is None:
            continue
        parts.append(f"{name}={cookie['value']}")
    return "; ".join(parts)


def storage_state_login_issue(state: dict[str, Any]) -> str | None:
    uid, sid = extract_uid_sid(state)
    if uid and sid:
        return None
    return "metaso_storage.json 缺少 uid/sid Cookie，请重新运行 python3 -m save_storage_state 导出登录态。"


def storage_state_summary(state: dict[str, Any]) -> str:
    uid, sid = extract_uid_sid(state)
    cookie_names = [
        str(c.get("name"))
        for c in state.get("cookies") or []
        if isinstance(c, dict) and c.get("name")
    ]
    return (
        f"cookies={len(cookie_names)} names={cookie_names[:12]} "
        f"uid={'yes' if uid else 'no'} sid={'yes' if sid else 'no'}"
    )
