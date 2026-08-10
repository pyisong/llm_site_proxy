"""一次性本地登录，导出 Playwright storage state 供 Docker 使用。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from storage_state import storage_state_login_issue, storage_state_summary

DEFAULT_OUTPUT = "secrets/metaso_storage.json"
DEFAULT_PROFILE = "./metaso-browser-profile-export"
HOME_URL = "https://metaso.cn/"


async def assert_ready(page) -> None:
    await page.wait_for_load_state("domcontentloaded")
    if "/login" in page.url or "sign" in page.url.lower():
        raise SystemExit("仍在登录页，请先在浏览器中完成秘塔登录。")
    cookies = await page.context.cookies()
    names = {c.get("name") for c in cookies}
    if "uid" not in names or "sid" not in names:
        raise SystemExit("未检测到 uid/sid Cookie，请确认已登录 metaso.cn。")


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
        await page.goto(HOME_URL, wait_until="domcontentloaded")

        if headless:
            raise SystemExit("请去掉 --headless，在浏览器窗口中完成秘塔登录后再按 Enter")

        print(f"请在打开的浏览器中登录 {HOME_URL}")
        print("登录后确认页面可用，再回到终端按 Enter...")
        input()

        await assert_ready(page)
        state = await context.storage_state()
        issue = storage_state_login_issue(state)
        if issue:
            raise SystemExit(issue)

        output.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
        await context.close()

    print(f"已保存 storage state 到 {output}")
    print(f"校验摘要: {storage_state_summary(state)}")
    print("Docker 配置: METASO_STORAGE_STATE_FILE=/run/secrets/metaso_storage.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="导出秘塔登录态（cookies）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(Path(args.output), Path(args.profile), args.headless))


if __name__ == "__main__":
    main()
