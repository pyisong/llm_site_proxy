"""Cookie 系 proxy：站点注册表（登录 URL / storage 路径 / 校验 / 热加载）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _secrets_root() -> Path:
    return Path(os.getenv("CONSOLE_SECRETS_ROOT", "/secrets")).resolve()


@dataclass(frozen=True)
class LoginSite:
    proxy_id: str
    name: str
    home_url: str
    storage_filename: str
    secrets_subdir: str
    reload_base_env: str  # docker DNS name fallback via catalog
    api_key_env: str
    kind: str  # cookies | cookies+bearer
    required_cookie_names: tuple[str, ...]
    chat_ready_selectors: tuple[str, ...] = ()
    notes: str = ""

    def storage_path(self) -> Path:
        override = (os.getenv(f"CONSOLE_STORAGE_{self.proxy_id.upper().replace('-', '_')}") or "").strip()
        if override:
            return Path(override)
        return _secrets_root() / self.secrets_subdir / self.storage_filename


LOGIN_SITES: dict[str, LoginSite] = {
    "deepseek-openai-proxy": LoginSite(
        proxy_id="deepseek-openai-proxy",
        name="DeepSeek",
        home_url="https://chat.deepseek.com/",
        storage_filename="deepseek_storage.json",
        secrets_subdir="deepseek",
        reload_base_env="deepseek-openai-proxy",
        api_key_env="DEEPSEEK_PROXY_API_KEY",
        kind="cookies+bearer",
        required_cookie_names=("ds_session_id",),
        chat_ready_selectors=("textarea",),
        notes="登录后请在聊天页发一条消息，以便捕获 Bearer。",
    ),
    "kimi-openai-proxy": LoginSite(
        proxy_id="kimi-openai-proxy",
        name="Kimi",
        home_url="https://www.kimi.com/",
        storage_filename="kimi_storage.json",
        secrets_subdir="kimi",
        reload_base_env="kimi-openai-proxy",
        api_key_env="KIMI_PROXY_API_KEY",
        kind="cookies",
        required_cookie_names=(),
        chat_ready_selectors=(
            '.chat-input-editor[contenteditable="true"]',
            '[data-lexical-editor="true"]',
            "textarea",
        ),
        notes="登录后确认能看到输入框再保存。",
    ),
    "stepfun-openai-proxy": LoginSite(
        proxy_id="stepfun-openai-proxy",
        name="StepFun",
        home_url="https://chat.stepfun.com/chats/new",
        storage_filename="stepfun_storage.json",
        secrets_subdir="stepfun",
        reload_base_env="stepfun-openai-proxy",
        api_key_env="STEPFUN_PROXY_API_KEY",
        kind="cookies",
        required_cookie_names=(),
        chat_ready_selectors=("textarea", '[contenteditable="true"]'),
        notes="关闭登录弹窗并进入聊天页后再保存。",
    ),
    "qwen-openai-proxy": LoginSite(
        proxy_id="qwen-openai-proxy",
        name="Qwen",
        home_url="https://chat.qwen.ai/",
        storage_filename="qwen_storage.json",
        secrets_subdir="qwen",
        reload_base_env="qwen-openai-proxy",
        api_key_env="QWEN_PROXY_API_KEY",
        kind="cookies+bearer",
        required_cookie_names=(),
        chat_ready_selectors=("textarea.message-input-textarea", "textarea"),
        notes="登录后请发一条消息以捕获 Bearer。",
    ),
    "metaso-openai-proxy": LoginSite(
        proxy_id="metaso-openai-proxy",
        name="Metaso",
        home_url="https://metaso.cn/",
        storage_filename="metaso_storage.json",
        secrets_subdir="metaso",
        reload_base_env="metaso-openai-proxy",
        api_key_env="METASO_PROXY_API_KEY",
        kind="cookies",
        required_cookie_names=("uid", "sid"),
        chat_ready_selectors=("textarea",),
        notes="登录后确认不在登录页即可保存。",
    ),
}


def list_login_sites() -> list[dict[str, Any]]:
    return [
        {
            "proxy_id": s.proxy_id,
            "name": s.name,
            "home_url": s.home_url,
            "kind": s.kind,
            "notes": s.notes,
            "storage_path": str(s.storage_path()),
        }
        for s in LOGIN_SITES.values()
    ]


def get_site(proxy_id: str) -> LoginSite:
    site = LOGIN_SITES.get(proxy_id)
    if site is None:
        raise KeyError(proxy_id)
    return site


def _cookie_names(state: dict[str, Any]) -> set[str]:
    return {
        str(c.get("name"))
        for c in (state.get("cookies") or [])
        if isinstance(c, dict) and c.get("name")
    }


def _inject_bearer(state: dict[str, Any], bearer: str, *, origin: str, auth_key: str) -> dict[str, Any]:
    token = bearer.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    enriched = json.loads(json.dumps(state))
    enriched[auth_key] = {**(enriched.get(auth_key) or {}), "bearer_token": token}
    origins = enriched.setdefault("origins", [])
    local = {"name": "userToken", "value": json.dumps({"value": token, "__version": "0"})}
    matched = False
    for item in origins:
        if isinstance(item, dict) and item.get("origin") == origin:
            ls = item.setdefault("localStorage", [])
            for row in ls:
                if isinstance(row, dict) and row.get("name") == "userToken":
                    row["value"] = local["value"]
                    matched = True
                    break
            if not matched:
                ls.append(local)
            matched = True
            break
    if not matched:
        origins.append({"origin": origin, "localStorage": [local]})
    return enriched


def validate_and_enrich_state(
    site: LoginSite,
    state: dict[str, Any],
    *,
    bearer_token: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """返回 (enriched_state, error_message)。"""
    names = _cookie_names(state)
    for need in site.required_cookie_names:
        if need not in names:
            return None, f"缺少 Cookie「{need}」，请确认已登录 {site.home_url}"

    if site.kind == "cookies+bearer":
        bearer = (bearer_token or "").strip() or None
        if not bearer:
            # try localStorage
            for origin in state.get("origins") or []:
                if not isinstance(origin, dict):
                    continue
                for row in origin.get("localStorage") or []:
                    if not isinstance(row, dict) or row.get("name") != "userToken":
                        continue
                    raw = row.get("value") or ""
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict) and isinstance(parsed.get("value"), str):
                            bearer = parsed["value"].strip() or None
                    except json.JSONDecodeError:
                        bearer = str(raw).strip() or None
        if not bearer:
            return None, (
                "未捕获到 Bearer。请在聊天页发送一条消息并看到回复后，再点「保存登录态」。"
            )
        if site.proxy_id.startswith("deepseek"):
            state = _inject_bearer(
                state, bearer, origin="https://chat.deepseek.com", auth_key="deepseek_auth"
            )
        elif site.proxy_id.startswith("qwen"):
            state = _inject_bearer(
                state, bearer, origin="https://chat.qwen.ai", auth_key="qwen_auth"
            )

    if not state.get("cookies"):
        return None, "storage 中没有任何 Cookie，请完成登录后再保存。"
    return state, None
