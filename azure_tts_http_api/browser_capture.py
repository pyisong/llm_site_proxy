from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from site_client import SITE_BASE


SENSITIVE_NAME_RE = re.compile(
    r"(token|cookie|session|auth|authorization|key|secret|yzm|captcha|user_id|download|url)",
    re.IGNORECASE,
)

INTERESTING_PATHS = {
    "/getSpeek.php",
    "/getSpeekList.php",
    "/getStyle.php",
    "/summary.php",
    "/getshouquan.php",
    "/checkRoleStyle.php",
}


def redact_value(name: str, value: Any) -> Any:
    if SENSITIVE_NAME_RE.search(name):
        return "<redacted>"
    if isinstance(value, str) and len(value) > 120:
        return value[:80] + "...<truncated>"
    return value


def parse_post_data(post_data: str | None) -> dict[str, Any]:
    if not post_data:
        return {}
    params: dict[str, Any] = {}
    for name, value in parse_qsl(post_data, keep_blank_values=True):
        params[name] = redact_value(name, value)
    return params


def summarize_requests(requests: list[dict[str, Any]], host_filter: str | None = None) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    endpoints: dict[str, set[str]] = {}

    for item in requests:
        url = str(item.get("url") or "")
        parsed = urlparse(url)
        if host_filter and parsed.netloc != host_filter:
            continue

        method = str(item.get("method") or "GET")
        path = parsed.path
        endpoints.setdefault(path, set()).add(method)

        post_params = parse_post_data(item.get("post_data"))
        if path in INTERESTING_PATHS or post_params:
            summaries.append(
                {
                    "method": method,
                    "host": parsed.netloc,
                    "path": path,
                    "status": item.get("status"),
                    "postKeys": sorted(post_params.keys()),
                    "redactedPost": post_params,
                    "responsePreview": redact_value("body", item.get("response_body")),
                }
            )

    return {
        "hostFilter": host_filter,
        "requestCount": len(summaries),
        "endpoints": [
            {"path": path, "methods": sorted(methods)}
            for path, methods in sorted(endpoints.items())
        ],
        "interestingRequests": summaries,
    }


def capture_with_browser(
    base_url: str = SITE_BASE,
    *,
    headless: bool = True,
    sample_text: str = "你好，这是浏览器抓包测试。",
    trigger_generate: bool = True,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required. Install with: pip install playwright && playwright install chromium"
        ) from exc

    captured: list[dict[str, Any]] = []

    def on_response(response: Any) -> None:
        request = response.request
        url = request.url
        if base_url.replace("https://", "").replace("http://", "") not in url:
            return
        item = {
            "method": request.method,
            "url": url,
            "post_data": request.post_data,
            "status": response.status,
        }
        content_type = response.headers.get("content-type", "")
        if any(token in content_type for token in ("json", "text", "html")):
            try:
                body = response.text()
                if len(body) <= 5000:
                    item["response_body"] = body
            except Exception:
                pass
        captured.append(item)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        page.on("response", on_response)
        page.goto(base_url + "/", wait_until="networkidle", timeout=60000)

        if trigger_generate:
            page.locator("#language").select_option(label="中文（普通话，简体）")
            page.locator("#voice").select_option(value="zh-CN-XiaoxiaoNeural")
            page.locator("#text").fill(sample_text)
            with page.expect_response(lambda response: "/getSpeek.php" in response.url, timeout=120000):
                page.get_by_role("button", name="生成", exact=True).click()

        browser.close()

    host = urlparse(base_url).netloc
    summary = summarize_requests(captured, host_filter=host)
    summary["source"] = "playwright-browser-capture"
    summary["baseUrl"] = base_url
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture text-to-speech.cn API traffic with Playwright.")
    parser.add_argument("--base-url", default=SITE_BASE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-generate", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--text", default="你好，这是浏览器抓包测试。")
    args = parser.parse_args()

    report = capture_with_browser(
        args.base_url,
        headless=not args.headed,
        sample_text=args.text,
        trigger_generate=not args.no_generate,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
