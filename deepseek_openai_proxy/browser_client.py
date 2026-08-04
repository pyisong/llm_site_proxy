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
    from .answer_markdown import ANSWER_MARKDOWN_JS, COPY_BUTTON_JS, choose_answer_text
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
    from answer_markdown import ANSWER_MARKDOWN_JS, COPY_BUTTON_JS, choose_answer_text
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

logger = logging.getLogger("deepseek_openai_proxy")

DEFAULT_INPUT_SELECTORS = [
    'textarea[placeholder*="DeepSeek"]',
    'textarea[placeholder*="发送"]',
    "textarea",
    '[contenteditable="true"]',
]

DEFAULT_NEW_CHAT_SELECTORS = [
    'div[tabindex="0"]:has-text("新对话")',
    'div[tabindex="0"]:has-text("New chat")',
    'button:has-text("新对话")',
    'button:has-text("New chat")',
]

DEFAULT_SEND_BUTTON_SELECTORS = [
    # DeepSeek 2026 UI: circular primary send button (class-disabled, often no aria-disabled).
    'div.ds-button.ds-button--primary.ds-button--circle[role="button"]:not(.ds-button--disabled)',
    'div.ds-button.ds-button--primary[role="button"]:not(.ds-button--disabled)',
    # Legacy selectors kept as fallback.
    'div.ds-icon-button[role="button"]:not([aria-disabled="true"])',
    'div[role="button"].ds-icon-button:not([aria-disabled="true"])',
    'button[aria-label*="Send"]',
    'button[aria-label*="发送"]',
    "#chat-input ~ div.ds-icon-button",
    'div.ds-icon-button:not([aria-disabled="true"])',
]

# Walk up from the chat input to find the send button in the same composer.
_SEND_BUTTON_NEAR_INPUT_JS = """
(input) => {
    let el = input;
    const selectors = [
        'div.ds-button.ds-button--primary[role="button"]',
        'div.ds-icon-button[role="button"]',
        'div[role="button"].ds-icon-button',
    ].join(', ');
    for (let depth = 0; depth < 15 && el; depth++) {
        el = el.parentElement;
        if (!el) break;
        const buttons = Array.from(el.querySelectorAll(selectors));
        if (buttons.length === 0) continue;
        for (let i = buttons.length - 1; i >= 0; i--) {
            const btn = buttons[i];
            if (btn.getAttribute("aria-disabled") === "true") continue;
            if (btn.classList.contains("ds-button--disabled")) continue;
            const rect = btn.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) return btn;
        }
    }
    return null;
}
"""

WEB_MODE_LABELS = {
    "fast": "快速模式",
    "expert": "专家模式",
    "vision": "识图模式",
}

GENERATION_ACTIVE_SELECTORS = [
    'button:has-text("停止")',
    'button:has-text("Stop")',
    '[class*="stop"]',
    '[class*="loading"]',
    '[class*="thinking"]',
]

# React-controlled textarea: assign via native setter + input event.
_SET_TEXTAREA_VALUE_JS = """
(el, text) => {
    el.focus();
    const desc = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        "value",
    );
    if (desc && desc.set) desc.set.call(el, text);
    else el.value = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return (el.value || "").length;
}
"""


class BrowserDeepSeekClient:
    def __init__(
        self,
        *,
        user_data_dir: str,
        headless: bool = False,
        chat_url: str = "https://chat.deepseek.com/",
        input_selector: str = 'textarea[placeholder*="DeepSeek"]',
        answer_selector: str = ".ds-assistant-message-main-content",
        timeout_seconds: float = 300.0,
        start_timeout_seconds: float | None = None,
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
            start_timeout_seconds if start_timeout_seconds is not None else timeout_seconds
        )
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
        # Avoid re-clicking mode toggles every request (bare text= clicks can hide the composer).
        self._applied_web_mode: str | None = None
        self._applied_deep_thinking: bool | None = None

    @classmethod
    def from_env(cls) -> "BrowserDeepSeekClient":
        return cls(
            user_data_dir=os.getenv("DEEPSEEK_BROWSER_PROFILE", "./deepseek-browser-profile"),
            headless=os.getenv("DEEPSEEK_BROWSER_HEADLESS", "0") == "1",
            chat_url=os.getenv("DEEPSEEK_CHAT_URL", "https://chat.deepseek.com/"),
            input_selector=os.getenv("DEEPSEEK_INPUT_SELECTOR", 'textarea[placeholder*="DeepSeek"]'),
            answer_selector=os.getenv("DEEPSEEK_ANSWER_SELECTOR", ".ds-assistant-message-main-content"),
            timeout_seconds=float(os.getenv("DEEPSEEK_BROWSER_TIMEOUT", "300")),
            start_timeout_seconds=(
                float(os.getenv("DEEPSEEK_BROWSER_START_TIMEOUT"))
                if os.getenv("DEEPSEEK_BROWSER_START_TIMEOUT")
                else None
            ),
            cookies_file=os.getenv("DEEPSEEK_COOKIES_FILE"),
            curl_file=os.getenv("DEEPSEEK_CURL_FILE"),
            storage_state_file=os.getenv("DEEPSEEK_STORAGE_STATE_FILE"),
            user_agent=os.getenv("DEEPSEEK_USER_AGENT"),
            new_chat_per_request=os.getenv("DEEPSEEK_NEW_CHAT_PER_REQUEST", "1") == "1",
            new_chat_selector=os.getenv("DEEPSEEK_NEW_CHAT_SELECTOR"),
            default_web_mode=os.getenv("DEEPSEEK_WEB_MODE", "fast"),
            default_deep_thinking=os.getenv("DEEPSEEK_DEEP_THINKING", "0") == "1",
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

        await self._grant_clipboard_permissions(self._context)
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
        if state.get("origins"):
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

        if "sign_in" in url or "/login" in url:
            if used_storage_state and self.storage_state_file:
                state = prepare_storage_state(load_storage_state(self.storage_state_file))
                issue = storage_state_login_issue(state)
                summary = storage_state_summary(state)
                if issue:
                    raise RuntimeError(
                        f"{issue}\n当前文件摘要: {summary}\n"
                        "若 bearer=无，说明导出时未捕获 authorization；请重新运行 "
                        "`python3 -m save_storage_state` 并在聊天页发消息后再按 Enter。"
                    )
                raise RuntimeError(
                    "DeepSeek 登录态在 Docker 中未被接受（页面仍跳转登录页）。\n"
                    f"当前文件摘要: {summary}\n"
                    "常见原因：会话已过期，或 Mac 导出的指纹 cookie 与 Linux 容器不兼容。"
                    "建议在远程服务器上用 noVNC 登录并导出，或使用 official API 模式。"
                )
            raise RuntimeError(
                "DeepSeek 未登录：当前页面仍在登录页。"
                "请重新运行 `python3 -m save_storage_state`，登录后在聊天页发一条消息再导出。"
            )

        selectors = [self.input_selector, *[s for s in DEFAULT_INPUT_SELECTORS if s != self.input_selector]]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for selector in selectors:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    try:
                        await locator.first.wait_for(state="visible", timeout=2_000)
                        self.input_selector = selector
                        logger.info("browser.input selector=%s", selector)
                        return
                    except Exception:
                        continue
            await asyncio.sleep(0.5)

        raise TimeoutError(
            f"未找到聊天输入框（当前页面: {url}）。"
            "若 cookie 已过期，请重新导出 storage state 或更新 curl。"
        )

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
        if "/a/chat/s/" in current_url or current_url.rstrip("/") != base_url:
            logger.info("browser.new_chat fallback=goto url=%s", self.chat_url)
            await page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60_000)
            await self._wait_for_chat_ready(page)

    async def _scroll_chat_to_bottom(self, page: Any) -> None:
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

    async def _locator_is_visible(self, locator: Any) -> bool:
        try:
            return bool(await locator.is_visible())
        except Exception:
            return False

    async def _dismiss_overlays(self, page: Any) -> None:
        """Close mode popovers / modals that can hide the chat composer."""
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.15)
            await page.keyboard.press("Escape")
        except Exception:
            pass

    async def _resolve_input_box(self, page: Any, *, timeout_ms: float = 10_000) -> Any:
        """Prefer a visible chat textarea; DeepSeek may keep hidden duplicates in the DOM."""
        locator = page.locator(self.input_selector)
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                count = await locator.count()
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.2)
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_visible():
                        if index > 0:
                            logger.info(
                                "browser.input resolved visible_index=%s count=%s",
                                index,
                                count,
                            )
                        return candidate
                except Exception as exc:
                    last_error = exc
                    continue
            await asyncio.sleep(0.2)
        # Fall back to first match so JS fill path can still try.
        logger.warning(
            "browser.input no_visible_match selector=%s last_error=%s; using .first",
            self.input_selector,
            last_error,
        )
        return locator.first

    async def _focus_input(self, input_box: Any) -> None:
        visible = await self._locator_is_visible(input_box)
        if visible:
            try:
                await input_box.scroll_into_view_if_needed(timeout=3_000)
            except Exception:
                pass
            try:
                await input_box.click(timeout=3_000)
                return
            except Exception as exc:
                logger.warning("browser.input click failed (%s); retrying force/focus", exc)
            try:
                await input_box.click(timeout=2_000, force=True)
                return
            except Exception as exc:
                logger.warning("browser.input force click failed (%s); using JS focus", exc)
        await input_box.evaluate(
            """(el) => {
                el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                el.focus();
                try { el.click(); } catch (e) {}
            }"""
        )

    async def _fill_input_via_js(self, input_box: Any, prompt: str) -> None:
        written = await input_box.evaluate(_SET_TEXTAREA_VALUE_JS, prompt)
        logger.info("browser.input js_fill chars=%s written=%s", len(prompt), written)

    async def _fill_input(self, input_box: Any, prompt: str) -> None:
        visible = await self._locator_is_visible(input_box)
        # Large prompts + hidden composers: Playwright fill can stall for minutes.
        if (not visible) or len(prompt) >= 4_000:
            await self._fill_input_via_js(input_box, prompt)
            return
        try:
            await input_box.fill(prompt, timeout=5_000)
            return
        except Exception as exc:
            logger.warning("browser.input fill failed (%s); using JS fill", exc)
        await self._fill_input_via_js(input_box, prompt)

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

    async def _input_remaining_text(self, input_box: Any) -> str:
        try:
            remaining = (await input_box.input_value()).strip()
            if remaining:
                return remaining
        except Exception:
            pass
        try:
            return (await input_box.inner_text()).strip()
        except Exception:
            return ""

    @staticmethod
    def _is_send_control_disabled(aria_disabled: str | None, class_name: str | None) -> bool:
        if aria_disabled == "true":
            return True
        return "ds-button--disabled" in (class_name or "")

    async def _click_send_button(self, page: Any, input_box: Any | None = None) -> bool:
        if input_box is not None:
            try:
                handle = await input_box.evaluate_handle(_SEND_BUTTON_NEAR_INPUT_JS)
                element = handle.as_element()
                if element is not None:
                    await element.click(timeout=2_000)
                    logger.info("browser.send method=near_input")
                    return True
            except Exception:
                pass

        for selector in DEFAULT_SEND_BUTTON_SELECTORS:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                button = locator.last
                if not await button.is_visible():
                    continue
                if self._is_send_control_disabled(
                    await button.get_attribute("aria-disabled"),
                    await button.get_attribute("class"),
                ):
                    continue
                await button.click(timeout=2_000)
                logger.info("browser.send method=selector selector=%s", selector)
                return True
            except Exception:
                continue
        return False

    async def _click_send_button_with_retry(
        self,
        page: Any,
        input_box: Any,
        *,
        max_wait_seconds: float = 3.0,
    ) -> bool:
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            if await self._click_send_button(page, input_box):
                return True
            await asyncio.sleep(0.2)
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
                # Bare text= matches often lack ARIA; repeated blind clicks open panels that hide input.
                if current is None and selector.startswith("text="):
                    logger.info(
                        "browser.option skip=%s selector=%s reason=no_aria_on_text_match",
                        option_name,
                        selector,
                    )
                    continue
                await control.click(timeout=3_000)
                logger.info("browser.option click=%s selector=%s", option_name, selector)
                return True
            except Exception:
                continue
        raise RuntimeError(f"未找到可用的 DeepSeek 页面选项控件: {option_name}")

    async def _select_web_mode(self, page: Any, web_mode: str) -> bool:
        label = WEB_MODE_LABELS.get(web_mode)
        if not label:
            raise ValueError(f"unsupported DeepSeek web mode: {web_mode!r}")
        short_label = label.replace("模式", "")
        selectors = [
            f'[role="radio"]:has-text("{label}")',
            f'[tabindex="0"]:has-text("{label}")',
            f'[role="button"]:has-text("{label}")',
            f'div.ds-segmented-button:has-text("{label}")',
        ]
        if short_label and short_label != label:
            selectors.extend(
                [
                    f'[role="radio"]:has-text("{short_label}")',
                    f'[tabindex="0"]:has-text("{short_label}")',
                ]
            )
        # Last resort only — skipped automatically when aria state is missing.
        selectors.append(f'text="{label}"')
        return await self._click_if_needed(
            page,
            selectors,
            state_attribute="aria-checked",
            desired=True,
            option_name=f"web_mode:{web_mode}",
        )

    async def _set_deep_thinking(self, page: Any, enabled: bool) -> bool:
        return await self._click_if_needed(
            page,
            [
                '[tabindex="0"]:has-text("深度思考")',
                'div.ds-toggle-button:has-text("深度思考")',
                'text="深度思考"',
            ],
            state_attribute="aria-pressed",
            desired=enabled,
            option_name=f"deep_thinking:{enabled}",
        )

    async def _apply_chat_options(
        self,
        page: Any,
        *,
        web_mode: str | None = None,
        deep_thinking: bool | None = None,
    ) -> None:
        if web_mode:
            if web_mode == self._applied_web_mode:
                logger.info("browser.option skip=web_mode:%s reason=session_cached", web_mode)
            else:
                try:
                    await self._select_web_mode(page, web_mode)
                    self._applied_web_mode = web_mode
                except RuntimeError as exc:
                    # Keep going with whatever mode the page already shows.
                    logger.warning("browser.option web_mode failed: %s", exc)
                    self._applied_web_mode = web_mode
        if deep_thinking is not None:
            if deep_thinking == self._applied_deep_thinking:
                logger.info(
                    "browser.option skip=deep_thinking:%s reason=session_cached",
                    deep_thinking,
                )
            else:
                try:
                    await self._set_deep_thinking(page, deep_thinking)
                    self._applied_deep_thinking = deep_thinking
                except RuntimeError as exc:
                    logger.warning("browser.option deep_thinking failed: %s", exc)
                    self._applied_deep_thinking = deep_thinking
        await self._dismiss_overlays(page)

    async def _send_message(self, page: Any, input_box: Any, prompt: str) -> None:
        await self._scroll_chat_to_bottom(page)
        await self._focus_input(input_box)
        await self._fill_input(input_box, prompt)
        await asyncio.sleep(0.2)

        # Long or multiline prompts need the send button; Enter often inserts newline only.
        if await self._click_send_button_with_retry(page, input_box):
            await asyncio.sleep(0.4)
            if not await self._input_remaining_text(input_box):
                return

        for key in ("Enter", "Control+Enter", "Meta+Enter"):
            remaining = await self._input_remaining_text(input_box)
            if not remaining:
                return
            logger.info("browser.send fallback key=%s remaining_chars=%s", key, len(remaining))
            await input_box.press(key)
            await asyncio.sleep(0.4)
            if not await self._input_remaining_text(input_box):
                return

        remaining = await self._input_remaining_text(input_box)
        if remaining:
            raise RuntimeError(
                "消息未能发送：输入框在 Enter 后仍有内容，且未找到发送按钮 "
                f"(remaining_chars={len(remaining)})"
            )

    async def _last_answer_snapshot(self, answer_blocks: Any) -> tuple[int, str]:
        count = await answer_blocks.count()
        if count == 0:
            return 0, ""
        text = (await answer_blocks.nth(count - 1).inner_text()).strip()
        return count, text

    async def _grant_clipboard_permissions(self, context: Any) -> None:
        """读剪贴板需要显式授权；失败不致命，后面会退到 DOM 还原。"""
        try:
            await context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=self.chat_url,
            )
        except Exception as exc:
            logger.info("browser.clipboard permission unavailable: %s", exc)

    async def _clipboard_markdown(self, page: Any, block: Any) -> str | None:
        """点消息自带的「复制」按钮，读回 Markdown 源码。"""
        try:
            try:
                await block.hover(timeout=2_000)
            except Exception:
                pass
            button = None
            # 工具栏按钮出现/可点击有时有延迟：重试多轮，避免偶发 copy_button=not_found。
            for delay_s in (0.15, 0.45, 0.9, 1.2):
                await asyncio.sleep(delay_s)
                try:
                    await block.hover(timeout=1_000)
                except Exception:
                    pass
                handle = await block.evaluate_handle(COPY_BUTTON_JS)
                button = handle.as_element()
                if button is not None:
                    break
            if button is None:
                logger.info("browser.answer copy_button=not_found")
                return None

            # 第 1 轮：点 JS 选择的按钮
            for _ in range(2):
                await button.click(timeout=2_000)
                for _ in range(6):
                    await asyncio.sleep(0.25)
                    text = await page.evaluate("() => navigator.clipboard.readText()")
                    if isinstance(text, str) and "```" in text:
                        return text

            # 第 2 轮：兜底尝试同一条消息附近的其它图标按钮
            # 目的：避免 COPY_BUTTON_JS 误点其它按钮，导致剪贴板复制到提示词而非 Markdown。
            candidates = await block.evaluate(
                """
                (el) => {
                  const elRect = el.getBoundingClientRect();
                  const cx = elRect.left + elRect.width / 2;
                  const cy = elRect.top + elRect.height / 2;
                  const out = [];
                  const btns = Array.from(document.querySelectorAll('[role=\"button\"], button'))
                    .filter((n) => !n.closest('[class*=\"code-block\"]'));
                  for (const n of btns) {
                    const r = n.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    const d = Math.hypot((r.left + r.width/2) - cx, (r.top + r.height/2) - cy);
                    // 只在合理距离内找，避免点到其它消息的工具栏
                    if (d > 650) continue;
                    out.push({ x: r.left + r.width/2, y: r.top + r.height/2, d });
                  }
                  out.sort((a,b)=>a.d-b.d);
                  return out.slice(0, 8);
                }
                """
            )
            for c in candidates:
                await page.mouse.click(c["x"], c["y"])
                for _ in range(6):
                    await asyncio.sleep(0.25)
                    text = await page.evaluate("() => navigator.clipboard.readText()")
                    if isinstance(text, str) and "```" in text:
                        return text
            return None
        except Exception as exc:
            logger.info("browser.answer clipboard unavailable: %s", exc)
            return None

    async def _dom_markdown(self, block: Any) -> str | None:
        """页面内把渲染后的 DOM 反序列化回 Markdown。"""
        try:
            # DeepSeek 的 mermaid 渲染区有一个“代码”按钮；有时源码在切换到代码视图后才会落到 DOM。
            # 先尝试切换，再做 DOM→Markdown，避免 mermaid 退化成“图表/代码/下载/全屏”纯文案。
            try:
                await block.evaluate(
                    """
                    (el) => {
                      const btns = Array.from(el.querySelectorAll('[role="button"], button'))
                        .filter((b) => !b.closest('[class*="code-block"]'));
                      for (const b of btns) {
                        const t = (b.innerText || "").trim();
                        if (t === "代码" || t.includes("代码")) {
                          b.click();
                          return true;
                        }
                      }
                      return false;
                    }
                    """
                )
                await asyncio.sleep(0.8)
            except Exception:
                pass
            text = await block.evaluate(ANSWER_MARKDOWN_JS)
            if isinstance(text, str) and text.strip():
                return text
            return None
        except Exception as exc:
            logger.warning("browser.answer dom markdown failed: %s", exc)
            return None

    async def _extract_answer_markdown(
        self,
        page: Any,
        answer_blocks: Any,
        rendered: str,
    ) -> str:
        """取回原始 Markdown：剪贴板优先，其次 DOM 还原，最后退回渲染文本。"""
        count = await answer_blocks.count()
        if count == 0:
            return rendered
        block = answer_blocks.nth(count - 1)
        clipboard = await self._clipboard_markdown(page, block)
        dom_markdown = await self._dom_markdown(block)
        chosen = choose_answer_text(
            clipboard=clipboard,
            dom_markdown=dom_markdown,
            rendered=rendered,
        )
        if clipboard and chosen == clipboard.strip():
            source = "clipboard"
        elif dom_markdown and chosen == dom_markdown.strip():
            source = "dom_markdown"
        else:
            source = "rendered_text"
        logger.info(
            "browser.answer source=%s chars=%s rendered_chars=%s",
            source,
            len(chosen),
            len(rendered or ""),
        )
        if source == "rendered_text":
            logger.warning(
                "browser.answer 未取到 Markdown 源码，正文可能丢失标题/表格/代码围栏"
                " (clipboard=%s dom=%s)",
                "empty" if not clipboard else f"{len(clipboard)}chars",
                "empty" if not dom_markdown else f"{len(dom_markdown)}chars",
            )
        return chosen

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
                self._applied_web_mode = None
                self._applied_deep_thinking = None
            elif sid and not self._active_session_id:
                self._active_session_id = sid
            if sid:
                logger.info("browser.session_id=%s new_chat=%s", sid, should_new_chat)
            await self._apply_chat_options(
                page,
                web_mode=web_mode or self.default_web_mode,
                deep_thinking=self.default_deep_thinking if deep_thinking is None else deep_thinking,
            )
            await asyncio.sleep(0.2)
            if not should_new_chat:
                await self._wait_for_generation_idle(page)
            input_box = await self._resolve_input_box(page, timeout_ms=3_000)
            if not await self._locator_is_visible(input_box):
                await self._dismiss_overlays(page)
                input_box = await self._resolve_input_box(page, timeout_ms=3_000)
            if not await self._locator_is_visible(input_box) and not should_new_chat:
                logger.warning("browser.input still_hidden; recovering with new_chat")
                await self._start_new_conversation(page)
                self._applied_web_mode = None
                self._applied_deep_thinking = None
                await self._apply_chat_options(
                    page,
                    web_mode=web_mode or self.default_web_mode,
                    deep_thinking=(
                        self.default_deep_thinking if deep_thinking is None else deep_thinking
                    ),
                )
                input_box = await self._resolve_input_box(page, timeout_ms=10_000)
            answer_blocks = page.locator(self.answer_selector)
            before_count, before_last_text = await self._last_answer_snapshot(answer_blocks)
            logger.info(
                "browser.send prompt_chars=%s before_count=%s reuse=%s",
                len(prompt),
                before_count,
                not should_new_chat,
            )
            await self._send_message(page, input_box, prompt)
            await self._wait_for_new_answer(page, answer_blocks, before_count, before_last_text)
            rendered = await self._wait_until_answer_stable(answer_blocks)
            return await self._extract_answer_markdown(page, answer_blocks, rendered)

    async def _wait_for_new_answer(
        self,
        page: Any,
        answer_blocks: Any,
        before_count: int,
        before_last_text: str,
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
            if await self._is_generation_active(page):
                logger.info("browser.answer_started generation_active")
                return
            await asyncio.sleep(0.5)
        count = await answer_blocks.count()
        raise TimeoutError(
            "Timed out waiting for DeepSeek to start answering "
            f"(before_count={before_count}, current_count={count}, "
            f"start_timeout={self.start_timeout_seconds}s). "
            "若使用 new_chat=false，请确认上一条已生成完毕；"
            "深度思考模式可能需增大 DEEPSEEK_BROWSER_START_TIMEOUT。"
        )

    async def _wait_until_answer_stable(self, answer_blocks: Any) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        last_text = ""
        stable_count = 0
        while time.monotonic() < deadline:
            count = await answer_blocks.count()
            if count == 0:
                await asyncio.sleep(0.5)
                continue
            text = (await answer_blocks.nth(count - 1).inner_text()).strip()
            if text and text == last_text:
                stable_count += 1
                if stable_count >= 4:
                    return text
            else:
                stable_count = 0
                last_text = text
            await asyncio.sleep(0.75)
        if last_text:
            return last_text
        raise TimeoutError("Timed out waiting for DeepSeek answer text.")

    async def aclose(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
            self._page = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
