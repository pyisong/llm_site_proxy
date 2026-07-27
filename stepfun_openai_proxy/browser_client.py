from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from .app import compose_browser_prompt
    from .curl_cookies import load_auth_from_file
    from .storage_state import (
        apply_storage_state,
        load_storage_state,
        merge_cookie_lists,
        prepare_storage_state,
        storage_state_login_issue,
        storage_state_summary,
    )
except ImportError:
    from app import compose_browser_prompt
    from curl_cookies import load_auth_from_file
    from storage_state import (
        apply_storage_state,
        load_storage_state,
        merge_cookie_lists,
        prepare_storage_state,
        storage_state_login_issue,
        storage_state_summary,
    )

logger = logging.getLogger("stepfun_openai_proxy")

DEFAULT_INPUT_SELECTORS = [
    'textarea.Publisher_textarea__pMX9t:not([disabled])',
    'textarea[placeholder*="任何问题"]:not([disabled])',
    'textarea[placeholder*="探索更多"]:not([disabled])',
    'textarea[placeholder*="发送"]',
    "textarea",
    '[contenteditable="true"]',
]

DEFAULT_NEW_CHAT_SELECTORS = [
    'button:has-text("开启新话题")',
    'a:has-text("新建会话")',
    'a:has-text("New chat")',
    'div[tabindex="0"]:has-text("新建会话")',
    'div[tabindex="0"]:has-text("新对话")',
    'div[tabindex="0"]:has-text("New chat")',
    'button:has-text("新建会话")',
    'button:has-text("新对话")',
    'button:has-text("New chat")',
]

DEFAULT_SEND_BUTTON_SELECTORS = [
    'button:has(.custom-icon-send-outline):not([disabled])',
    'button:has(svg.custom-icon-send-outline):not([disabled])',
    'button:has([class*="send"]):not([disabled])',
    '[role="button"]:has([class*="send"]):not([aria-disabled="true"])',
    'button[aria-label*="Send"]',
    'button[aria-label*="发送"]',
    '[role="button"][aria-label*="Send"]',
    '[role="button"][aria-label*="发送"]',
    'button:has-text("发送"):not([disabled])',
]

WEB_MODE_LABELS = {
    "fast": "快速",
    "search": "搜索",
    "deep_research": "深入核查",
    "knowledge": "知识库问答",
    "image": "图片创作",
}

GENERATION_ACTIVE_SELECTORS = [
    'button:has-text("停止")',
    'button:has-text("停止生成")',
    'button:has-text("Stop")',
    'button:has(.custom-icon-stop)',
    'button:has([class*="stop-outline"])',
    'button[aria-label*="停止"]',
    'button[aria-label*="Stop"]',
]

# 深入核查 / 知识库报告等场景下，最终答案未必落在默认 chat markdown 节点里。
ANSWER_FALLBACK_SELECTORS = [
    'div[data-message-id="markdown"]',
    'div[data-message-id="assistant"]',
    '[data-message-id="markdown"] [class*="markdown"]',
    'div[class*="message-markdown_markdown"]:not([class*="reason-render-ext"])',
    'div[class*="MessageMarkdown"]',
    'div[class*="markdown-body"]',
    "article",
    "pre code",
    "pre",
]

MIN_ANSWER_CHARS = 40


def _default_profile_dir(*, module_dir: Path | None = None) -> str:
    explicit = os.getenv("STEPFUN_BROWSER_PROFILE")
    if explicit:
        return explicit

    cwd_candidate = Path("stepfun-browser-profile")
    if cwd_candidate.exists():
        return str(cwd_candidate)

    base_dir = module_dir or Path(__file__).resolve().parent
    module_candidate = base_dir / "stepfun-browser-profile"
    if module_candidate.exists():
        return str(module_candidate)

    return str(cwd_candidate)


def _default_storage_state_file(*, module_dir: Path | None = None) -> str | None:
    explicit = os.getenv("STEPFUN_STORAGE_STATE_FILE")
    if explicit:
        return explicit

    cwd_candidate = Path("secrets") / "stepfun_storage.json"
    if cwd_candidate.exists():
        return str(cwd_candidate)

    base_dir = module_dir or Path(__file__).resolve().parent
    module_candidate = base_dir / "secrets" / "stepfun_storage.json"
    if module_candidate.exists():
        return str(module_candidate)

    return None


def _default_headless(storage_state_file: str | None, user_data_dir: str) -> bool:
    explicit = os.getenv("STEPFUN_BROWSER_HEADLESS")
    if explicit is not None:
        return explicit == "1"
    return bool(storage_state_file) or Path(user_data_dir).exists()


class BrowserStepFunClient:
    def __init__(
        self,
        *,
        user_data_dir: str,
        headless: bool = False,
        chat_url: str = "https://chat.stepfun.com/chats/new",
        input_selector: str = 'textarea[placeholder*="任何问题"]:not([disabled])',
        answer_selector: str = 'div[data-message-id="markdown"] > div[class*="message-markdown_markdown"]:not([class*="reason-render-ext"])',
        timeout_seconds: float = 1800.0,
        start_timeout_seconds: float | None = None,
        idle_timeout_seconds: float = 120.0,
        max_retries: int = 2,
        cookies_file: str | None = None,
        curl_file: str | None = None,
        storage_state_file: str | None = None,
        user_agent: str | None = None,
        new_chat_per_request: bool = True,
        new_chat_selector: str | None = None,
        default_web_mode: str = "fast",
        default_deep_thinking: bool = False,
    ) -> None:
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.chat_url = chat_url
        self.input_selector = input_selector
        self.answer_selector = answer_selector
        self.timeout_seconds = timeout_seconds
        self.start_timeout_seconds = (
            start_timeout_seconds if start_timeout_seconds is not None else min(timeout_seconds, 300.0)
        )
        self.idle_timeout_seconds = max(5.0, float(idle_timeout_seconds))
        self.max_retries = max(1, int(max_retries))
        self.cookies_file = cookies_file
        self.curl_file = curl_file
        self.storage_state_file = storage_state_file
        self.user_agent = user_agent
        self.new_chat_per_request = new_chat_per_request
        self.new_chat_selector = new_chat_selector
        self.default_web_mode = default_web_mode
        self.default_deep_thinking = default_deep_thinking
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._lock = asyncio.Lock()
        self._active_session_id: str | None = None

    @classmethod
    def from_env(cls) -> "BrowserStepFunClient":
        user_data_dir = _default_profile_dir()
        storage_state_file = _default_storage_state_file()
        return cls(
            user_data_dir=user_data_dir,
            headless=_default_headless(storage_state_file, user_data_dir),
            chat_url=os.getenv("STEPFUN_CHAT_URL", "https://chat.stepfun.com/chats/new"),
            input_selector=os.getenv("STEPFUN_INPUT_SELECTOR", 'textarea[placeholder*="任何问题"]:not([disabled])'),
            answer_selector=os.getenv(
                "STEPFUN_ANSWER_SELECTOR",
                'div[data-message-id="markdown"] > div[class*="message-markdown_markdown"]:not([class*="reason-render-ext"])',
            ),
            timeout_seconds=float(os.getenv("STEPFUN_BROWSER_TIMEOUT", "1800")),
            start_timeout_seconds=(
                float(os.getenv("STEPFUN_BROWSER_START_TIMEOUT"))
                if os.getenv("STEPFUN_BROWSER_START_TIMEOUT")
                else None
            ),
            idle_timeout_seconds=float(os.getenv("STEPFUN_BROWSER_IDLE_TIMEOUT", "120")),
            max_retries=int(os.getenv("STEPFUN_BROWSER_MAX_RETRIES", "2")),
            cookies_file=os.getenv("STEPFUN_COOKIES_FILE"),
            curl_file=os.getenv("STEPFUN_CURL_FILE"),
            storage_state_file=storage_state_file,
            user_agent=os.getenv("STEPFUN_USER_AGENT"),
            new_chat_per_request=os.getenv("STEPFUN_NEW_CHAT_PER_REQUEST", "1") == "1",
            new_chat_selector=os.getenv("STEPFUN_NEW_CHAT_SELECTOR"),
            default_web_mode=os.getenv("STEPFUN_WEB_MODE", "fast"),
            default_deep_thinking=os.getenv("STEPFUN_DEEP_THINKING", "0") == "1",
        )

    async def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Browser backend requires the playwright package.") from exc

        cookies, user_agent = await self._resolve_cookies()
        storage_state = await self._resolve_storage_state()
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        launch_args = ["--disable-blink-features=AutomationControlled"]

        if storage_state and self.storage_state_file:
            if cookies:
                storage_state["cookies"] = merge_cookie_lists(storage_state.get("cookies") or [], cookies)
            prepared_state = prepare_storage_state(storage_state)
            issue = storage_state_login_issue(prepared_state)
            if issue:
                raise RuntimeError(f"{issue}\n当前文件摘要: {storage_state_summary(prepared_state)}")

            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
                json.dump(prepared_state, handle, ensure_ascii=False)
                prepared_path = handle.name

            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
            )
            context_kwargs: dict[str, Any] = {
                "viewport": {"width": 1280, "height": 900},
                "locale": "zh-CN",
                "storage_state": prepared_path,
            }
            if self.user_agent:
                context_kwargs["user_agent"] = self.user_agent
            self._context = await self._browser.new_context(**context_kwargs)
            self._page = await self._context.new_page()
            await self._page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60_000)
        else:
            launch_kwargs: dict[str, Any] = {
                "headless": self.headless,
                "viewport": {"width": 1280, "height": 900},
                "args": launch_args,
                "locale": "zh-CN",
            }
            if self.user_agent:
                launch_kwargs["user_agent"] = self.user_agent
            self._context = await self._playwright.chromium.launch_persistent_context(
                self.user_data_dir,
                **launch_kwargs,
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

            if storage_state:
                await apply_storage_state(self._context, self._page, storage_state, chat_url=self.chat_url)
            elif cookies:
                await self._page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60_000)
                await self._context.add_cookies(cookies)
                await self._page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60_000)
            else:
                await self._page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60_000)

        await self._wait_for_chat_ready(self._page, used_storage_state=bool(storage_state and self.storage_state_file))
        return self._page

    async def _resolve_storage_state(self) -> dict[str, Any] | None:
        if not self.storage_state_file:
            return None
        path = Path(self.storage_state_file)
        if not path.exists():
            logger.warning("storage state file not found, skipping: %s", path)
            return None
        state = load_storage_state(path)
        issue = storage_state_login_issue(state)
        if issue:
            logger.warning("storage state validation: %s", issue)
        if state.get("cookies") or state.get("origins"):
            return state
        return None

    async def _resolve_cookies(self) -> tuple[list[dict[str, Any]], str | None]:
        auth_path = self.curl_file or self.cookies_file
        if not auth_path:
            return [], None
        cookie_path = Path(auth_path)
        if not cookie_path.exists():
            raise FileNotFoundError(f"Cookie/curl file does not exist: {cookie_path}")

        text = cookie_path.read_text(encoding="utf-8").strip()
        if text.startswith("{") and '"origins"' in text:
            return [], None

        cookies, parsed_user_agent = load_auth_from_file(cookie_path)
        if parsed_user_agent and not self.user_agent:
            self.user_agent = parsed_user_agent

        normalized: list[dict[str, Any]] = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            item = {k: v for k, v in cookie.items() if v is not None}
            if "sameSite" in item and item["sameSite"] not in {"Strict", "Lax", "None"}:
                item.pop("sameSite", None)
            normalized.append(item)
        return normalized, parsed_user_agent

    async def _wait_for_chat_ready(self, page: Any, *, used_storage_state: bool = False) -> None:
        await page.wait_for_load_state("domcontentloaded")
        url = page.url
        title = await page.title()
        logger.info("browser.page url=%s title=%s", url, title)

        if "sign_in" in url or "/login" in url or await self._has_login_modal(page):
            if used_storage_state and self.storage_state_file:
                state = prepare_storage_state(load_storage_state(self.storage_state_file))
                issue = storage_state_login_issue(state)
                summary = storage_state_summary(state)
                if issue:
                    raise RuntimeError(
                        f"{issue}\n当前文件摘要: {summary}\n"
                        "请重新运行 `python3 -m save_storage_state`，登录后确认能看到输入框再按 Enter。"
                    )
                raise RuntimeError(
                    "StepFun 登录态在 Docker 中未被接受（页面仍跳转登录页）。\n"
                    f"当前文件摘要: {summary}\n"
                    "常见原因：会话已过期，或 Mac 导出的指纹 cookie 与 Linux 容器不兼容。"
                    "建议在远程服务器上用 noVNC 登录并导出，或使用 official API 模式。"
                )
            raise RuntimeError(
                "StepFun 未登录：当前页面显示登录页或登录弹窗。"
                "请先完成登录，或运行 `python3 -m save_storage_state` 导出登录态。"
            )

        await self._find_input_box(page, timeout_seconds=60.0, page_url=url)

    async def _find_input_box(
        self,
        page: Any,
        *,
        timeout_seconds: float = 10.0,
        page_url: str | None = None,
    ) -> Any:
        selectors = [self.input_selector, *[s for s in DEFAULT_INPUT_SELECTORS if s != self.input_selector]]
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for selector in selectors:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    try:
                        await locator.first.wait_for(state="visible", timeout=2_000)
                        self.input_selector = selector
                        logger.info("browser.input selector=%s", selector)
                        return locator.first
                    except Exception:
                        continue
            await asyncio.sleep(0.5)

        raise TimeoutError(
            f"未找到聊天输入框（当前页面: {page_url or page.url}）。"
            "若 cookie 已过期，请重新导出 storage state 或更新 curl。"
        )

    async def _has_login_modal(self, page: Any) -> bool:
        selectors = [
            'text="欢迎来到阶跃AI"',
            'text="阅读并同意"',
            'button:has-text("下一步")',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _click_new_chat_button(self, page: Any) -> bool:
        selectors = []
        if self.new_chat_selector:
            selectors.append(self.new_chat_selector)
        selectors.extend(DEFAULT_NEW_CHAT_SELECTORS)

        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                button = locator.first
                if not await button.is_visible():
                    continue
                if (await button.get_attribute("aria-disabled")) == "true":
                    continue
                await button.click(timeout=3_000)
                logger.info("browser.new_chat selector=%s", selector)
                return True
            except Exception:
                continue
        return False

    async def _start_new_conversation(self, page: Any) -> None:
        if await self._click_new_chat_button(page):
            await self._wait_for_chat_ready(page)
            return

        current_url = page.url
        base_url = self.chat_url.rstrip("/")
        if "/chat/" in current_url or current_url.rstrip("/") != base_url:
            logger.info("browser.new_chat fallback=goto url=%s", self.chat_url)
            await page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60_000)
            await self._wait_for_chat_ready(page)

    async def _scroll_chat_to_bottom(self, page: Any) -> None:
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

    async def _is_generation_active(self, page: Any) -> bool:
        for selector in GENERATION_ACTIVE_SELECTORS:
            locator = page.locator(selector)
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_generation_idle(self, page: Any, *, max_wait_seconds: float = 30.0) -> None:
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            if not await self._is_generation_active(page):
                return
            await asyncio.sleep(0.5)

    async def _click_send_button(self, page: Any) -> bool:
        for selector in DEFAULT_SEND_BUTTON_SELECTORS:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                button = locator.last
                if not await button.is_visible():
                    continue
                if (await button.get_attribute("aria-disabled")) == "true":
                    continue
                await button.click(timeout=2_000)
                logger.info("browser.send selector=%s", selector)
                return True
            except Exception:
                continue
        try:
            clicked = await page.evaluate(
                """() => {
                    const selectors = [
                        '.custom-icon-send-outline',
                        'svg[class*="send"]',
                        '[class*="send"]',
                        'button[aria-label*="发送"]',
                        'button[aria-label*="Send"]',
                        '[role="button"][aria-label*="发送"]',
                        '[role="button"][aria-label*="Send"]'
                    ];
                    for (const selector of selectors) {
                        for (const node of document.querySelectorAll(selector)) {
                            const control = node.closest('button,[role="button"]') || node;
                            if (!control) continue;
                            if (control.disabled) continue;
                            if (control.getAttribute('aria-disabled') === 'true') continue;
                            const rect = control.getBoundingClientRect();
                            if (!rect || rect.width === 0 || rect.height === 0) continue;
                            control.click();
                            return true;
                        }
                    }
                    return false;
                }"""
            )
            if clicked:
                logger.info("browser.send selector=js:send-control")
                return True
        except Exception:
            pass
        return False

    async def _click_if_needed(
        self,
        page: Any,
        selectors: list[str],
        *,
        state_attribute: str,
        desired: bool,
        option_name: str,
    ) -> bool:
        desired_value = "true" if desired else "false"
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                control = locator.first
                if not await control.is_visible():
                    continue
                current = await control.get_attribute(state_attribute)
                if current == desired_value:
                    logger.info("browser.option skip=%s state=%s", option_name, desired_value)
                    return False
                if (await control.get_attribute("aria-disabled")) == "true":
                    continue
                await control.click(timeout=3_000)
                logger.info("browser.option click=%s selector=%s", option_name, selector)
                return True
            except Exception:
                continue
        raise RuntimeError(f"未找到可用的 StepFun 页面选项控件: {option_name}")

    async def _select_web_mode(self, page: Any, web_mode: str) -> bool:
        label = WEB_MODE_LABELS.get(web_mode)
        if not label:
            raise ValueError(f"unsupported StepFun web mode: {web_mode!r}")
        try:
            return await self._click_if_needed(
                page,
                [
                    f'button:has-text("{label}")',
                    f'[role="button"]:has-text("{label}")',
                    f'text="{label}"',
                ],
                state_attribute="aria-pressed",
                desired=True,
                option_name=f"web_mode:{web_mode}",
            )
        except RuntimeError:
            logger.warning("browser.option missing=web_mode:%s label=%s skip=true", web_mode, label)
            return False

    async def _set_deep_thinking(self, page: Any, enabled: bool) -> bool:
        logger.info("browser.option skip=deep_thinking:%s reason=no_stepfun_toggle", enabled)
        return False

    async def _apply_chat_options(
        self,
        page: Any,
        *,
        web_mode: str | None = None,
        deep_thinking: bool | None = None,
    ) -> None:
        if web_mode:
            await self._select_web_mode(page, web_mode)
        if deep_thinking is not None:
            await self._set_deep_thinking(page, deep_thinking)

    async def _send_message(self, page: Any, input_box: Any, prompt: str) -> None:
        await self._scroll_chat_to_bottom(page)
        try:
            await input_box.click(timeout=5_000)
        except Exception:
            logger.info("browser.input click intercepted, retrying with force")
            await input_box.click(timeout=5_000, force=True)
        await input_box.fill("")
        await input_box.fill(prompt)
        await self._dispatch_input_events(page)
        await asyncio.sleep(0.15)
        if await self._click_send_button(page):
            return
        await input_box.press("Enter")
        await asyncio.sleep(0.4)
        remaining = await self._input_text(input_box)
        if remaining:
            logger.info("browser.send enter did not clear input, trying send button")
            if not await self._click_send_button(page):
                raise RuntimeError("消息未能发送：输入框在 Enter 后仍有内容，且未找到发送按钮")

    async def _dispatch_input_events(self, page: Any) -> None:
        try:
            await page.evaluate(
                """() => {
                    const input = document.querySelector('textarea[placeholder*="任何问题"]:not([disabled])')
                        || document.querySelector('textarea:not([disabled])');
                    if (!input) return;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
        except Exception:
            pass

    async def _input_text(self, input_box: Any) -> str:
        try:
            return (await input_box.input_value()).strip()
        except Exception:
            try:
                return (await input_box.inner_text()).strip()
            except Exception:
                return ""

    async def _last_answer_snapshot(self, answer_blocks: Any) -> tuple[int, str]:
        count = await answer_blocks.count()
        if count == 0:
            return 0, ""
        text = await self._answer_text(answer_blocks)
        return count, text

    async def _answer_text(self, answer_blocks: Any, *, exclude_text: str = "") -> str:
        count = await answer_blocks.count()
        fallback = ""
        for index in range(count - 1, -1, -1):
            text = (await answer_blocks.nth(index).inner_text()).strip()
            if not text:
                continue
            if not fallback:
                fallback = text
            if not exclude_text or text != exclude_text:
                return text
        return fallback

    @staticmethod
    def _looks_like_echoed_prompt(text: str, prompt: str) -> bool:
        if not text or not prompt:
            return False
        normalized = text.strip()
        prompt_stripped = prompt.strip()
        if not normalized or not prompt_stripped:
            return False
        if normalized == prompt_stripped:
            return True
        snippet = prompt_stripped[:160]
        if snippet and snippet in normalized:
            # 用户气泡会包住整段 prompt；勿把发送内容当答案。
            if abs(len(normalized) - len(prompt_stripped)) <= max(300, int(len(prompt_stripped) * 0.15)):
                return True
            if len(normalized) >= len(prompt_stripped) * 0.85 and len(normalized) <= len(prompt_stripped) + 500:
                return True
        return False

    async def _answer_text_from_selectors(
        self,
        page: Any,
        selectors: list[str],
        *,
        exclude_text: str = "",
        prompt: str = "",
    ) -> str:
        for selector in selectors:
            try:
                blocks = page.locator(selector)
                text = await self._answer_text(blocks, exclude_text=exclude_text)
            except Exception:
                continue
            if not text or len(text) < MIN_ANSWER_CHARS:
                continue
            if self._looks_like_echoed_prompt(text, prompt):
                continue
            logger.info("browser.answer_fallback selector=%s chars=%s", selector, len(text))
            return text
        return ""

    async def _scrape_page_answer(self, page: Any, *, prompt: str = "", exclude_text: str = "") -> str:
        """Last-resort extraction when chat markdown selectors miss deep-research DOM."""
        exclude_snippet = (exclude_text or prompt or "").strip()[:160]
        try:
            scraped = await page.evaluate(
                """(excludeSnippet) => {
                    const skipRe = /(Publisher_textarea|placeholder|contenteditable|input|textarea)/i;
                    const nodes = [
                      ...document.querySelectorAll(
                        'div[data-message-id], [data-message-id="markdown"], [data-message-id="assistant"], ' +
                        'div[class*="message-markdown"], div[class*="MessageMarkdown"], div[class*="markdown-body"], ' +
                        'article, pre, code, [class*="report"], [class*="Report"], [class*="document"], [class*="Document"]'
                      )
                    ];
                    const seen = new Set();
                    const scored = [];
                    const push = (el) => {
                      if (!el || seen.has(el)) return;
                      seen.add(el);
                      const cls = (el.className || '').toString();
                      if (skipRe.test(cls) || skipRe.test(el.tagName || '')) return;
                      const text = (el.innerText || '').trim();
                      if (text.length < 40) return;
                      if (excludeSnippet && text.includes(excludeSnippet) && text.length < (excludeSnippet.length + 800)) {
                        return;
                      }
                      let score = text.length;
                      if (text.includes('{') && text.includes('}')) score += 2000;
                      if (text.includes('```')) score += 500;
                      if (/title_pattern|section_blueprint|opening_pattern/.test(text)) score += 5000;
                      if (/reason-render|thinking|推理/.test(cls)) score -= 3000;
                      scored.push({ text, score, len: text.length });
                    };
                    for (const el of nodes) push(el);
                    // Broad scan for large JSON / report blocks if specialized nodes are empty.
                    if (!scored.length) {
                      for (const el of document.querySelectorAll('div, section, main')) {
                        const text = (el.innerText || '').trim();
                        if (text.length < 200 || text.length > 200000) continue;
                        if (excludeSnippet && text.includes(excludeSnippet) &&
                            Math.abs(text.length - excludeSnippet.length) < 800) continue;
                        if (!(text.includes('{') && text.includes('}'))) continue;
                        push(el);
                      }
                    }
                    scored.sort((a, b) => b.score - a.score);
                    return scored.length ? scored[0].text : '';
                }""",
                exclude_snippet,
            )
        except Exception as exc:
            logger.debug("browser.scrape_page_answer failed: %s", exc)
            return ""
        text = (scraped or "").strip()
        if not text or len(text) < MIN_ANSWER_CHARS:
            return ""
        if self._looks_like_echoed_prompt(text, prompt):
            return ""
        logger.info("browser.answer_scrape chars=%s", len(text))
        return text

    async def _resolve_answer_text(
        self,
        page: Any,
        answer_blocks: Any,
        *,
        exclude_text: str = "",
        prompt: str = "",
    ) -> str:
        text = await self._answer_text(answer_blocks, exclude_text=exclude_text)
        if text and len(text) >= MIN_ANSWER_CHARS and not self._looks_like_echoed_prompt(text, prompt):
            return text
        fallback = await self._answer_text_from_selectors(
            page,
            ANSWER_FALLBACK_SELECTORS,
            exclude_text=exclude_text,
            prompt=prompt,
        )
        if fallback:
            return fallback
        return await self._scrape_page_answer(page, prompt=prompt, exclude_text=exclude_text)

    async def _dump_answer_miss(self, page: Any) -> None:
        try:
            info = await page.evaluate(
                """() => {
                    const ids = {};
                    for (const n of document.querySelectorAll('[data-message-id]')) {
                      const id = n.getAttribute('data-message-id') || '';
                      ids[id] = (ids[id] || 0) + 1;
                    }
                    const body = (document.body && document.body.innerText) || '';
                    return {
                      url: location.href,
                      messageIds: ids,
                      bodyLen: body.length,
                      hasTitlePattern: body.includes('title_pattern'),
                      hasJsonBrace: body.includes('{') && body.includes('}'),
                      sample: body.slice(0, 240),
                    };
                }"""
            )
            logger.warning("browser.answer_miss dump=%s", info)
        except Exception as exc:
            logger.debug("browser.answer_miss dump failed: %s", exc)

    async def chat_completion(
        self,
        payload: dict[str, Any],
        *,
        new_chat: bool | None = None,
        session_id: str | None = None,
        web_mode: str | None = None,
        deep_thinking: bool | None = None,
    ) -> str:
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")

        should_new_chat = self.new_chat_per_request if new_chat is None else new_chat
        resolved_web_mode = web_mode or self.default_web_mode
        # 深入核查网页端不支持多轮追问，必须每次新开话题
        if resolved_web_mode == "deep_research" and not should_new_chat:
            logger.info("browser.force_new_chat=true reason=deep_research_no_multiturn")
            should_new_chat = True
        sid = (session_id or "").strip() or None
        prompt = compose_browser_prompt(messages, reuse_session=not should_new_chat)
        if not prompt:
            raise ValueError("messages did not contain text content")

        async with self._lock:
            if sid and self._active_session_id and sid != self._active_session_id:
                should_new_chat = True
                logger.info(
                    "browser.session_switch from=%s to=%s force_new_chat=true",
                    self._active_session_id,
                    sid,
                )
            page = await self._ensure_page()
            if should_new_chat:
                await self._start_new_conversation(page)
                self._active_session_id = sid
            elif sid and not self._active_session_id:
                self._active_session_id = sid
            if sid:
                logger.info("browser.session_id=%s new_chat=%s", sid, should_new_chat)
            await self._apply_chat_options(
                page,
                web_mode=resolved_web_mode,
                deep_thinking=self.default_deep_thinking if deep_thinking is None else deep_thinking,
            )
            last_error: Exception | None = None
            for attempt in range(1, self.max_retries + 1):
                try:
                    if attempt > 1:
                        logger.warning(
                            "browser.retry attempt=%s/%s reason=%s",
                            attempt,
                            self.max_retries,
                            last_error,
                        )
                        await self._start_new_conversation(page)
                        self._active_session_id = sid
                        await self._apply_chat_options(
                            page,
                            web_mode=resolved_web_mode,
                            deep_thinking=self.default_deep_thinking if deep_thinking is None else deep_thinking,
                        )
                    if not should_new_chat and attempt == 1:
                        await self._wait_for_generation_idle(page)
                    input_box = await self._find_input_box(page, timeout_seconds=10.0)
                    answer_blocks = page.locator(self.answer_selector)
                    before_count, before_last_text = await self._last_answer_snapshot(answer_blocks)
                    before_resolved = await self._resolve_answer_text(
                        page,
                        answer_blocks,
                        exclude_text=before_last_text,
                        prompt=prompt,
                    )
                    logger.info(
                        "browser.send prompt_chars=%s before_count=%s reuse=%s attempt=%s",
                        len(prompt),
                        before_count,
                        not should_new_chat and attempt == 1,
                        attempt,
                    )
                    await self._send_message(page, input_box, prompt)
                    await self._wait_for_new_answer(
                        page,
                        answer_blocks,
                        before_count,
                        before_last_text,
                        prompt=prompt,
                        before_resolved=before_resolved,
                    )
                    return await self._wait_until_answer_stable(
                        page,
                        answer_blocks,
                        previous_text=before_last_text,
                        prompt=prompt,
                    )
                except TimeoutError as exc:
                    last_error = exc
                    await self._dump_answer_miss(page)
                    salvaged = await self._resolve_answer_text(
                        page,
                        page.locator(self.answer_selector),
                        prompt=prompt,
                    )
                    if salvaged:
                        logger.warning(
                            "browser.salvage_answer chars=%s after_timeout=%s",
                            len(salvaged),
                            exc,
                        )
                        return salvaged
                    if attempt >= self.max_retries:
                        raise
            assert last_error is not None
            raise last_error

    async def _wait_for_new_answer(
        self,
        page: Any,
        answer_blocks: Any,
        before_count: int,
        before_last_text: str,
        *,
        prompt: str = "",
        before_resolved: str = "",
    ) -> None:
        deadline = time.monotonic() + self.start_timeout_seconds
        while time.monotonic() < deadline:
            count = await answer_blocks.count()
            if count > before_count:
                logger.info("browser.answer_started count=%s->%s", before_count, count)
                return
            if count > 0 and before_count > 0:
                text = (await answer_blocks.nth(count - 1).inner_text()).strip()
                if text and text != before_last_text:
                    logger.info("browser.answer_started last_text_changed")
                    return
            resolved = await self._resolve_answer_text(
                page,
                answer_blocks,
                exclude_text=before_last_text,
                prompt=prompt,
            )
            if (
                resolved
                and len(resolved) >= MIN_ANSWER_CHARS
                and resolved != before_resolved
                and not self._looks_like_echoed_prompt(resolved, prompt)
            ):
                logger.info("browser.answer_started via_fallback chars=%s", len(resolved))
                return
            if await self._is_generation_active(page):
                if before_count == 0 and not before_resolved:
                    logger.info("browser.answer_started generation_active")
                    return
                logger.info("browser.answer_waiting generation_active")
            await asyncio.sleep(0.5)
        count = await answer_blocks.count()
        raise TimeoutError(
            "Timed out waiting for StepFun to start answering "
            f"(before_count={before_count}, current_count={count}, "
            f"start_timeout={self.start_timeout_seconds}s). "
            "若使用 new_chat=false，请确认上一条已生成完毕；"
            "深入核查模式可能需增大 STEPFUN_BROWSER_START_TIMEOUT。"
        )

    async def _wait_until_answer_stable(
        self,
        page: Any,
        answer_blocks: Any,
        *,
        previous_text: str = "",
        prompt: str = "",
    ) -> str:
        """Wait for final answer.

        - Absolute ceiling: ``timeout_seconds``
        - Idle budget: ``idle_timeout_seconds`` since last progress
        - Progress = answer text grew, or generation still active (streaming / researching)
        """
        max_deadline = time.monotonic() + self.timeout_seconds
        idle_deadline = time.monotonic() + self.idle_timeout_seconds
        last_text = ""
        last_len = 0
        stable_count = 0
        saw_generation = False

        while time.monotonic() < max_deadline:
            generating = await self._is_generation_active(page)
            text = await self._resolve_answer_text(
                page,
                answer_blocks,
                exclude_text=previous_text,
                prompt=prompt,
            )
            text_len = len(text or "")
            progressed = False

            if generating:
                saw_generation = True
                progressed = True
                stable_count = 0

            if text_len > last_len:
                progressed = True
                logger.info(
                    "browser.answer_progress chars=%s->%s generating=%s",
                    last_len,
                    text_len,
                    generating,
                )
                last_len = text_len
                last_text = text
                stable_count = 0
            elif text and text != last_text:
                # Same length but content replaced (rare); still count as progress.
                progressed = True
                last_text = text
                last_len = text_len
                stable_count = 0

            if progressed:
                idle_deadline = time.monotonic() + self.idle_timeout_seconds

            if time.monotonic() >= idle_deadline:
                if last_text:
                    logger.warning(
                        "browser.answer_idle_timeout returning_partial chars=%s idle=%ss",
                        len(last_text),
                        self.idle_timeout_seconds,
                    )
                    return last_text
                await self._dump_answer_miss(page)
                raise TimeoutError(
                    "Timed out waiting for StepFun answer progress "
                    f"(idle={self.idle_timeout_seconds}s, no text growth and generation inactive)."
                )

            if not generating and text:
                if text == last_text:
                    stable_count += 1
                    needed = 6 if saw_generation else 4
                    if stable_count >= needed:
                        logger.info(
                            "browser.answer_stable chars=%s saw_generation=%s",
                            len(text),
                            saw_generation,
                        )
                        return text
                else:
                    last_text = text
                    last_len = text_len
                    stable_count = 0

            await asyncio.sleep(0.75)

        if last_text:
            logger.warning(
                "browser.answer_max_timeout returning_partial chars=%s max=%ss",
                len(last_text),
                self.timeout_seconds,
            )
            return last_text
        await self._dump_answer_miss(page)
        raise TimeoutError(
            "Timed out waiting for StepFun answer text "
            f"(max={self.timeout_seconds}s, idle={self.idle_timeout_seconds}s)."
        )

    async def aclose(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception as exc:
                logger.debug("browser.context_close ignored: %s", exc)
            self._context = None
            self._page = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.debug("browser.close ignored: %s", exc)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.debug("browser.playwright_stop ignored: %s", exc)
            self._playwright = None
