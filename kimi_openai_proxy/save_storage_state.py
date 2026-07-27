"""一次性本地登录，导出 Playwright storage state 供 Docker 使用。"""

from __future__ import annotations

import argparse
import asyncio
import os
import json
from pathlib import Path

from storage_state import storage_state_login_issue, storage_state_summary

DEFAULT_OUTPUT = "secrets/kimi_storage.json"
DEFAULT_PROFILE = "./kimi-browser-profile-export"
CHAT_URL = "https://www.kimi.com/"

INPUT_SELECTORS = [
    '.chat-input-editor[contenteditable="true"]',
    '[role="textbox"].chat-input-editor',
    '[data-lexical-editor="true"]',
    'textarea[placeholder*="发送"]',
    "textarea",
    '[contenteditable="true"]',
]


async def assert_chat_ready(page) -> None:
    await page.wait_for_load_state("domcontentloaded")
    if "sign_in" in page.url or "/login" in page.url:
        raise SystemExit("仍在登录页，请先在浏览器中完成 Kimi 登录。")

    has_input = False
    for selector in INPUT_SELECTORS:
        if await page.locator(selector).count() > 0:
            has_input = True
            break
    if not has_input:
        raise SystemExit("未找到聊天输入框，请确认已进入聊天页面而不是登录页。")


async def run(output: Path, profile_dir: Path, headless: bool) -> None:
    from playwright.async_api import async_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto(CHAT_URL, wait_until="domcontentloaded")

        if headless:
            raise SystemExit("请去掉 --headless，在浏览器窗口中完成 Kimi 登录后再按 Enter")

        print(f"请在打开的浏览器中登录 {CHAT_URL}")
        print("登录后确认能看到 Kimi 输入框，再回到终端按 Enter...")
        input()

        await assert_chat_ready(page)
        state = await context.storage_state()

        issue = storage_state_login_issue(state)
        if issue:
            raise SystemExit(issue)

        output.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
        await context.close()

    print(f"已保存 storage state 到 {output}")
    print(f"校验摘要: {storage_state_summary(state)}")
    print("Docker 配置: KIMI_STORAGE_STATE_FILE=/run/secrets/kimi_storage.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 Kimi 登录态（cookies + localStorage + Bearer）")
    parser.add_argument("-o", "--output", default=os.getenv("KIMI_STORAGE_STATE_FILE", DEFAULT_OUTPUT))
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="临时浏览器 profile 目录")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(Path(args.output), Path(args.profile), args.headless))


if __name__ == "__main__":
    main()
