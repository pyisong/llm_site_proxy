"""一次性本地登录，导出 Playwright storage state 供 Docker 使用。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from storage_state import storage_state_login_issue, storage_state_summary

DEFAULT_OUTPUT = "secrets/stepfun_storage.json"
DEFAULT_PROFILE = "./stepfun-browser-profile-export"
CHAT_URL = "https://chat.stepfun.com/chats/new"

INPUT_SELECTORS = [
    'textarea.Publisher_textarea__pMX9t:not([disabled])',
    'textarea[placeholder*="任何问题"]:not([disabled])',
    'textarea[placeholder*="探索更多"]:not([disabled])',
    'textarea[placeholder*="发送"]',
    "textarea",
    '[contenteditable="true"]',
]


async def assert_chat_ready(page) -> None:
    await page.wait_for_load_state("domcontentloaded")
    if "sign_in" in page.url or "/login" in page.url:
        raise SystemExit("仍在登录页，请先在浏览器中完成 StepFun 登录。")

    for selector in ['text="欢迎来到阶跃AI"', 'text="阅读并同意"', 'button:has-text("下一步")']:
        locator = page.locator(selector)
        if await locator.count() > 0 and await locator.first.is_visible():
            raise SystemExit("仍显示 StepFun 登录弹窗，请先完成登录并关闭弹窗。")

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
            raise SystemExit("请去掉 --headless，在浏览器窗口中完成 StepFun 登录后再按 Enter")

        print(f"请在打开的浏览器中登录 {CHAT_URL}")
        print("登录后确认能看到 StepFun 输入框，再回到终端按 Enter...")
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
    print("Docker 配置: STEPFUN_STORAGE_STATE_FILE=/run/secrets/stepfun_storage.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 StepFun 登录态（cookies + localStorage）")
    parser.add_argument("-o", "--output", default=os.getenv("STEPFUN_STORAGE_STATE_FILE", DEFAULT_OUTPUT))
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="临时浏览器 profile 目录")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(Path(args.output), Path(args.profile), args.headless))


if __name__ == "__main__":
    main()
