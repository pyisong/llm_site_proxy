"""一次性本地登录，导出 Playwright storage state 供 Docker 使用。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from storage_state import (
    extract_bearer_token,
    inject_bearer_into_state,
    storage_state_login_issue,
    storage_state_summary,
)

DEFAULT_OUTPUT = "secrets/deepseek_storage.json"
DEFAULT_PROFILE = "./deepseek-browser-profile-export"
CHAT_URL = "https://chat.deepseek.com/"

INPUT_SELECTORS = [
    'textarea[placeholder*="DeepSeek"]',
    'textarea[placeholder*="发送"]',
    "textarea",
]


class AuthCapture:
    def __init__(self) -> None:
        self.bearer_token: str | None = None

    def remember(self, headers: dict[str, str]) -> None:
        authorization = headers.get("authorization") or headers.get("Authorization")
        if not authorization:
            return
        token = authorization.strip()
        if token.lower().startswith("bearer ") and len(token) > 7:
            self.bearer_token = token[7:].strip()


async def assert_logged_in(page, auth_capture: AuthCapture) -> str:
    await page.wait_for_load_state("domcontentloaded")
    if "sign_in" in page.url or "/login" in page.url:
        raise SystemExit("仍在登录页，请先在浏览器中完成 DeepSeek 登录。")

    has_input = False
    for selector in INPUT_SELECTORS:
        if await page.locator(selector).count() > 0:
            has_input = True
            break
    if not has_input:
        raise SystemExit("未找到聊天输入框，请确认已进入聊天页面而不是登录页。")

    user_token_raw = await page.evaluate("() => localStorage.getItem('userToken')")
    bearer = auth_capture.bearer_token
    if not bearer:
        try:
            parsed = json.loads(user_token_raw or "null")
            if isinstance(parsed, dict) and isinstance(parsed.get("value"), str):
                bearer = parsed["value"].strip() or None
        except json.JSONDecodeError:
            bearer = (user_token_raw or "").strip() or None

    if not bearer:
        raise SystemExit(
            "未捕获到 DeepSeek Bearer 令牌。\n"
            "请在聊天页发送一条测试消息并看到回复后，再回到终端按 Enter。\n"
            "若已发送仍失败，可在 DevTools → Network 里找到 completion 请求，"
            "确认请求头 authorization 是否存在。"
        )
    return bearer


async def run(output: Path, profile_dir: Path, headless: bool) -> None:
    from playwright.async_api import async_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    auth_capture = AuthCapture()

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        def on_request(request) -> None:
            if "/chat/completion" in request.url:
                auth_capture.remember(request.headers)

        page.on("request", on_request)

        await page.goto(CHAT_URL, wait_until="domcontentloaded")

        if headless:
            raise SystemExit("请去掉 --headless，在浏览器窗口中完成 DeepSeek 登录后再按 Enter")

        print(f"请在打开的浏览器中登录 {CHAT_URL}")
        print("登录后在聊天页发送一条测试消息，确认能正常回复，再回到终端按 Enter...")
        input()

        bearer = await assert_logged_in(page, auth_capture)
        state = await context.storage_state()
        state = inject_bearer_into_state(state, bearer)

        issue = storage_state_login_issue(state)
        if issue:
            raise SystemExit(issue)

        output.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
        await context.close()

    print(f"已保存 storage state 到 {output}")
    print(f"校验摘要: {storage_state_summary(state)}")
    print("Docker 配置: DEEPSEEK_STORAGE_STATE_FILE=/run/secrets/deepseek_storage.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 DeepSeek 登录态（cookies + localStorage + Bearer）")
    parser.add_argument("-o", "--output", default=os.getenv("DEEPSEEK_STORAGE_STATE_FILE", DEFAULT_OUTPUT))
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="临时浏览器 profile 目录")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(Path(args.output), Path(args.profile), args.headless))


if __name__ == "__main__":
    main()
