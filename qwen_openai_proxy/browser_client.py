from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
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

logger = logging.getLogger("qwen_openai_proxy")

DEFAULT_INPUT_SELECTORS = [
    "textarea.message-input-textarea",
    'textarea[placeholder*="帮"]',
    'textarea[placeholder*="图像"]',
    'textarea[placeholder*="视频"]',
    "textarea",
    '[contenteditable="true"]',
]

DEFAULT_NEW_CHAT_SELECTORS = [
    '[aria-label="新建对话"]',
    ".new-chat",
    'button:has-text("新建对话")',
    'button:has-text("New chat")',
]

DEFAULT_SEND_BUTTON_SELECTORS = [
    ".message-input-right-button-send button",
    ".message-input-right-button-send",
    "div.chat-prompt-send-button button",
    'button[aria-label*="Send"]',
    'button[aria-label*="发送"]',
]

MODE_MENU_ROOT_SELECTOR = "ul.ant-dropdown-menu-root.qwen-dropdown-menu, ul.ant-dropdown-menu-root"
MODE_MENU_ITEM_SELECTOR = "li[data-menu-id]"
FILE_INPUT_SELECTORS = [
    "input#filesUpload",
    "input[type='file']",
]
MODE_TRIGGER_SELECTORS = [
    "div.mode-select-open",
    "div.mode-select",
    'button[aria-label="选择模式"]',
    'button:has-text("选择模式")',
]

MODE_SUFFIXES = {
    "chat": None,
    "image": "t2i",
    "t2i": "t2i",
    "video": "t2v",
    "t2v": "t2v",
    "deep_research": "deep_research",
    "research": "deep_research",
    "web_dev": "web_dev",
}

MODE_LABELS = {
    "t2i": "生成图像",
    "t2v": "创建视频",
    "deep_research": "深入研究",
    "web_dev": "网页开发",
}

GENERATION_ACTIVE_SELECTORS = [
    'button:has-text("停止")',
    'button:has-text("Stop")',
    '[class*="stop"]',
    '[class*="loading"]',
    '[class*="thinking"]',
    '[class*="generating"]',
]

DEFAULT_ANSWER_SELECTORS = [
    ".response-message-content.phase-answer",
    ".qwen-chat-message-assistant .response-message-content",
    ".qwen-chat-message-assistant",
    ".chat-response-message .response-message-content",
]

COMPLETION_READY_SELECTORS = [
    ".copy-response-button",
    ".qwen-chat-message-assistant .copy-response-button",
]


@dataclass
class BrowserResult:
    text: str = ""
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)


class BrowserQwenClient:
    def __init__(
        self,
        *,
        user_data_dir: str,
        headless: bool = False,
        chat_url: str = "https://chat.qwen.ai/",
        input_selector: str = "textarea.message-input-textarea",
        answer_selector: str = ".response-message-content.phase-answer, .qwen-chat-message-assistant .response-message-content",
        timeout_seconds: float = 300.0,
        start_timeout_seconds: float | None = None,
        image_timeout_seconds: float = 900.0,
        video_timeout_seconds: float = 1800.0,
        cookies_file: str | None = None,
        curl_file: str | None = None,
        storage_state_file: str | None = None,
        user_agent: str | None = None,
        new_chat_per_request: bool = True,
        new_chat_selector: str | None = None,
        default_qwen_mode: str = "chat",
        default_thinking: bool = False,
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
        self.image_timeout_seconds = image_timeout_seconds
        self.video_timeout_seconds = video_timeout_seconds
        self.cookies_file = cookies_file
        self.curl_file = curl_file
        self.storage_state_file = storage_state_file
        self.user_agent = user_agent
        self.new_chat_per_request = new_chat_per_request
        self.new_chat_selector = new_chat_selector
        self.default_qwen_mode = default_qwen_mode
        self.default_thinking = default_thinking
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._lock = asyncio.Lock()
        self._active_session_id: str | None = None
        self._active_mode: str | None = None

    @classmethod
    def from_env(cls) -> "BrowserQwenClient":
        storage_state_file = os.getenv("QWEN_STORAGE_STATE_FILE")
        if not storage_state_file:
            candidate = Path("./secrets/qwen_storage.json")
            if candidate.exists():
                storage_state_file = str(candidate)

        headless_env = os.getenv("QWEN_BROWSER_HEADLESS")
        headless = headless_env == "1" if headless_env is not None else bool(storage_state_file)

        return cls(
            user_data_dir=os.getenv("QWEN_BROWSER_PROFILE", "./qwen-browser-profile"),
            headless=headless,
            chat_url=os.getenv("QWEN_CHAT_URL", "https://chat.qwen.ai/"),
            input_selector=os.getenv("QWEN_INPUT_SELECTOR", "textarea.message-input-textarea"),
            answer_selector=os.getenv(
                "QWEN_ANSWER_SELECTOR",
                ".response-message-content.phase-answer, .qwen-chat-message-assistant .response-message-content",
            ),
            timeout_seconds=float(os.getenv("QWEN_BROWSER_TIMEOUT", "300")),
            start_timeout_seconds=(
                float(os.getenv("QWEN_BROWSER_START_TIMEOUT"))
                if os.getenv("QWEN_BROWSER_START_TIMEOUT")
                else None
            ),
            image_timeout_seconds=float(os.getenv("QWEN_IMAGE_TIMEOUT", "900")),
            video_timeout_seconds=float(os.getenv("QWEN_VIDEO_TIMEOUT", "1800")),
            cookies_file=os.getenv("QWEN_COOKIES_FILE"),
            curl_file=os.getenv("QWEN_CURL_FILE"),
            storage_state_file=storage_state_file,
            user_agent=os.getenv("QWEN_USER_AGENT"),
            new_chat_per_request=os.getenv("QWEN_NEW_CHAT_PER_REQUEST", "1") == "1",
            new_chat_selector=os.getenv("QWEN_NEW_CHAT_SELECTOR"),
            default_qwen_mode=os.getenv("QWEN_DEFAULT_MODE", "chat"),
            default_thinking=os.getenv("QWEN_THINKING", "0") == "1",
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

        if "/auth" in url or "/login" in url:
            if used_storage_state and self.storage_state_file:
                state = prepare_storage_state(load_storage_state(self.storage_state_file))
                issue = storage_state_login_issue(state)
                summary = storage_state_summary(state)
                if issue:
                    raise RuntimeError(
                        f"{issue}\n当前文件摘要: {summary}\n"
                        "请重新运行 `python3 -m save_storage_state` 并在聊天页发消息后再按 Enter。"
                    )
                raise RuntimeError(
                    "Qwen 登录态在 Docker 中未被接受（页面仍跳转登录页）。\n"
                    f"当前文件摘要: {summary}\n"
                    "常见原因：会话已过期，或导出的 cookie 与容器环境不兼容。"
                )
            raise RuntimeError(
                "Qwen 未登录：当前页面仍在登录页。"
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
            self._active_mode = None
            return

        current_url = page.url
        base_url = self.chat_url.rstrip("/")
        if "/c/" in current_url or current_url.rstrip("/") != base_url:
            logger.info("browser.new_chat fallback=goto url=%s", self.chat_url)
            await page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60_000)
            await self._wait_for_chat_ready(page)
            self._active_mode = None

    async def _open_mode_menu(self, page: Any) -> Any | None:
        for selector in MODE_TRIGGER_SELECTORS:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                target = locator.first
                if not await target.is_visible():
                    continue
                await target.click(timeout=3_000)
                menu = page.locator(MODE_MENU_ROOT_SELECTOR)
                try:
                    await menu.first.wait_for(state="visible", timeout=2_500)
                except Exception:
                    continue
                if await menu.count() > 0:
                    logger.info("browser.mode_menu opened selector=%s", selector)
                    return menu.first
            except Exception:
                continue
        return None

    async def _click_mode_item(self, page: Any, mode_suffix: str) -> bool:
        menu = await self._open_mode_menu(page)
        if menu is None:
            return False

        selectors = [
            f'li[data-menu-id$="-{mode_suffix}"]',
            f'li[data-menu-id*="-{mode_suffix}"]',
            f'[role="menuitem"]:has-text("{MODE_LABELS.get(mode_suffix, mode_suffix)}")',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                item = locator.first
                if not await item.is_visible():
                    continue
                classes = (await item.get_attribute("class")) or ""
                if "disabled" in classes:
                    raise RuntimeError(f"Qwen 模式不可用（可能未登录）: {MODE_LABELS.get(mode_suffix, mode_suffix)}")
                await item.click(timeout=3_000)
                logger.info("browser.mode selected=%s selector=%s", mode_suffix, selector)
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                self._active_mode = mode_suffix
                await asyncio.sleep(0.3)
                return True
            except RuntimeError:
                raise
            except Exception:
                continue

        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    async def _apply_qwen_mode(self, page: Any, qwen_mode: str) -> None:
        normalized = qwen_mode.strip().lower()
        suffix = MODE_SUFFIXES.get(normalized)
        if suffix is None:
            if self._active_mode:
                await self._start_new_conversation(page)
            return
        if self._active_mode == suffix:
            logger.info("browser.mode skip=%s already active", suffix)
            return
        if not await self._click_mode_item(page, suffix):
            label = MODE_LABELS.get(suffix, suffix)
            raise RuntimeError(f"未找到或无法切换到 Qwen 模式: {label}")

    async def _upload_reference_files(self, page: Any, file_paths: list[str]) -> None:
        if not file_paths:
            return

        for file_path in file_paths:
            uploaded = False
            try:
                menu = await self._open_mode_menu(page)
                if menu is not None:
                    upload_selectors = [
                        f'{MODE_MENU_ITEM_SELECTOR}[data-menu-id$="-upload"]',
                        f'{MODE_MENU_ITEM_SELECTOR}[data-menu-id*="upload"]',
                        '[role="menuitem"]:has-text("上传附件")',
                    ]
                    for selector in upload_selectors:
                        upload_item = page.locator(selector)
                        if await upload_item.count() == 0:
                            continue
                        try:
                            async with page.expect_file_chooser(timeout=6_000) as chooser_info:
                                await upload_item.first.click(timeout=3_000)
                            chooser = await chooser_info.value
                            await chooser.set_files(file_path)
                            uploaded = True
                            logger.info("browser.upload chooser path=%s selector=%s", file_path, selector)
                            break
                        except Exception:
                            continue
            except Exception:
                logger.exception("browser.upload chooser failed path=%s", file_path)

            if not uploaded:
                file_input = None
                for selector in FILE_INPUT_SELECTORS:
                    locator = page.locator(selector)
                    if await locator.count() > 0:
                        file_input = locator.first
                        break
                if file_input is None:
                    raise RuntimeError(f"未找到 Qwen 文件上传控件，无法上传参考图: {file_path}")
                await file_input.set_input_files(file_path)
                await page.evaluate(
                    """() => {
                        const inputs = Array.from(document.querySelectorAll("input[type='file']"));
                        for (const input of inputs) {
                            try { input.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
                            try { input.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) {}
                        }
                    }"""
                )
                logger.info("browser.upload input path=%s", file_path)

            await asyncio.sleep(0.8)

    async def _run_generation(
        self,
        *,
        prompt: str,
        qwen_mode: str,
        new_chat: bool | None = None,
        session_id: str | None = None,
        reference_image_paths: list[str] | None = None,
        thinking: bool | None = None,
    ) -> BrowserResult:
        should_new_chat = self.new_chat_per_request if new_chat is None else new_chat
        sid = (session_id or "").strip() or None
        mode = qwen_mode.strip().lower()
        ref_paths = [path for path in (reference_image_paths or []) if path]

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
                logger.info(
                    "browser.session_id=%s new_chat=%s mode=%s refs=%s",
                    sid,
                    should_new_chat,
                    mode,
                    len(ref_paths),
                )
            await self._apply_qwen_mode(page, mode)
            if thinking is not None and thinking != self.default_thinking:
                logger.info("browser.thinking requested=%s (UI toggle not yet automated)", thinking)
            if not should_new_chat:
                await self._wait_for_generation_idle(page)
            if ref_paths:
                await self._upload_reference_files(page, ref_paths)
            input_box = page.locator(self.input_selector).first
            answer_blocks = page.locator(self.answer_selector)
            before_count, before_last_text = await self._last_answer_snapshot(answer_blocks)
            logger.info(
                "browser.send prompt_chars=%s before_count=%s reuse=%s mode=%s refs=%s",
                len(prompt),
                before_count,
                not should_new_chat,
                mode,
                len(ref_paths),
            )
            await self._send_message(page, input_box, prompt)
            await self._wait_for_new_answer(page, answer_blocks, before_count, before_last_text)
            text = await self._wait_until_answer_stable(
                page,
                answer_blocks,
                timeout_seconds=self._timeout_for_mode(mode),
            )
            image_urls, video_urls = await self._extract_media_from_last_answer(page)
            return BrowserResult(text=text, image_urls=image_urls, video_urls=video_urls)

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
                aria = await button.get_attribute("aria-label")
                if aria and "语音" in aria:
                    continue
                await button.click(timeout=2_000)
                logger.info("browser.send selector=%s", selector)
                return True
            except Exception:
                continue
        return False

    async def _send_message(self, page: Any, input_box: Any, prompt: str) -> None:
        await self._scroll_chat_to_bottom(page)
        await input_box.click(timeout=5_000)
        await input_box.fill("")
        await input_box.type(prompt, delay=15)
        await asyncio.sleep(0.2)
        await input_box.press("Enter")
        await asyncio.sleep(0.5)
        try:
            remaining = (await input_box.input_value()).strip()
        except Exception:
            remaining = ""
        if remaining:
            logger.info("browser.send enter did not clear input, trying send button")
            if not await self._click_send_button(page):
                raise RuntimeError("消息未能发送：输入框在 Enter 后仍有内容，且未找到发送按钮")

    async def _last_answer_snapshot(self, answer_blocks: Any) -> tuple[int, str]:
        count = await answer_blocks.count()
        if count == 0:
            return 0, ""
        text = (await answer_blocks.nth(count - 1).inner_text()).strip()
        return count, text

    async def _extract_media_from_last_answer(self, page: Any) -> tuple[list[str], list[str]]:
        image_urls: list[str] = []
        video_urls: list[str] = []
        assistant_blocks = page.locator(".qwen-chat-message-assistant, .chat-response-message")
        count = await assistant_blocks.count()
        if count == 0:
            return image_urls, video_urls

        block = assistant_blocks.nth(count - 1)
        images = block.locator("img")
        image_count = await images.count()
        for index in range(image_count):
            src = await images.nth(index).get_attribute("src")
            if src and src.startswith("http") and src not in image_urls:
                image_urls.append(src)

        videos = block.locator("video")
        video_count = await videos.count()
        for index in range(video_count):
            src = await videos.nth(index).get_attribute("src")
            if src and src.startswith("http") and src not in video_urls:
                video_urls.append(src)

        links = block.locator("a[href]")
        link_count = await links.count()
        for index in range(link_count):
            href = await links.nth(index).get_attribute("href")
            if not href or not href.startswith("http"):
                continue
            lower = href.lower()
            if any(ext in lower for ext in (".mp4", ".webm", ".mov", "video")):
                if href not in video_urls:
                    video_urls.append(href)
            elif any(ext in lower for ext in (".png", ".jpg", ".jpeg", ".webp", "image")):
                if href not in image_urls:
                    image_urls.append(href)

        return image_urls, video_urls

    def _timeout_for_mode(self, qwen_mode: str) -> float:
        normalized = qwen_mode.strip().lower()
        if normalized in {"video", "t2v"}:
            return self.video_timeout_seconds
        if normalized in {"image", "t2i"}:
            return self.image_timeout_seconds
        return self.timeout_seconds

    async def chat_completion(
        self,
        payload: dict[str, Any],
        *,
        new_chat: bool | None = None,
        session_id: str | None = None,
        qwen_mode: str | None = None,
        thinking: bool | None = None,
    ) -> BrowserResult:
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")

        should_new_chat = self.new_chat_per_request if new_chat is None else new_chat
        sid = (session_id or "").strip() or None
        mode = (qwen_mode or self.default_qwen_mode or "chat").strip().lower()
        prompt = compose_browser_prompt(messages, reuse_session=not should_new_chat)
        if not prompt:
            raise ValueError("messages did not contain text content")

        return await self._run_generation(
            prompt=prompt,
            qwen_mode=mode,
            new_chat=should_new_chat,
            session_id=sid,
            thinking=thinking,
        )

    async def generate_image(
        self,
        prompt: str,
        *,
        new_chat: bool | None = None,
        reference_image_paths: list[str] | None = None,
    ) -> BrowserResult:
        return await self._run_generation(
            prompt=prompt,
            qwen_mode="image",
            new_chat=new_chat,
            reference_image_paths=reference_image_paths,
        )

    async def edit_image(
        self,
        prompt: str,
        *,
        reference_image_paths: list[str],
        new_chat: bool | None = None,
    ) -> BrowserResult:
        if not reference_image_paths:
            raise ValueError("reference image is required for image edit")
        return await self._run_generation(
            prompt=prompt,
            qwen_mode="image",
            new_chat=new_chat,
            reference_image_paths=reference_image_paths,
        )

    async def generate_video(
        self,
        prompt: str,
        *,
        new_chat: bool | None = None,
        reference_image_paths: list[str] | None = None,
    ) -> BrowserResult:
        return await self._run_generation(
            prompt=prompt,
            qwen_mode="video",
            new_chat=new_chat,
            reference_image_paths=reference_image_paths,
        )

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
            "Timed out waiting for Qwen to start answering "
            f"(before_count={before_count}, current_count={count}, "
            f"start_timeout={self.start_timeout_seconds}s)."
        )

    async def _wait_until_answer_stable(
        self,
        page: Any,
        answer_blocks: Any,
        *,
        timeout_seconds: float,
    ) -> str:
        deadline = time.monotonic() + timeout_seconds
        last_text = ""
        stable_count = 0
        while time.monotonic() < deadline:
            count = await answer_blocks.count()
            if count == 0:
                await asyncio.sleep(0.5)
                continue
            text = (await answer_blocks.nth(count - 1).inner_text()).strip()
            copy_ready = False
            for selector in COMPLETION_READY_SELECTORS:
                locator = page.locator(selector)
                try:
                    if await locator.count() > 0 and await locator.last.is_visible():
                        copy_ready = True
                        break
                except Exception:
                    continue

            image_urls, video_urls = await self._extract_media_from_last_answer(page)
            has_media = bool(image_urls or video_urls)

            if (text and text == last_text) or (has_media and not await self._is_generation_active(page)):
                stable_count += 1
                if stable_count >= 4 or (copy_ready and stable_count >= 2):
                    return text or last_text
            else:
                stable_count = 0
                last_text = text
            await asyncio.sleep(0.75)

        if last_text:
            return last_text
        image_urls, video_urls = await self._extract_media_from_last_answer(page)
        if image_urls or video_urls:
            return ""
        raise TimeoutError("Timed out waiting for Qwen answer text.")

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
