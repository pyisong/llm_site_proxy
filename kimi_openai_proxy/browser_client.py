from __future__ import annotations

import asyncio
import contextlib
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

logger = logging.getLogger("kimi_openai_proxy")

DEFAULT_INPUT_SELECTORS = [
    '.chat-input-editor[contenteditable="true"]',
    '[role="textbox"].chat-input-editor',
    '[data-lexical-editor="true"]',
    'textarea[placeholder*="发送"]',
    "textarea",
    '[contenteditable="true"]',
]

DEFAULT_NEW_CHAT_SELECTORS = [
    "a.new-chat-btn",
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
    ".send-button-container:not(.disabled)",
    ".send-button-container",
    'button[aria-label*="Send"]',
    'button[aria-label*="发送"]',
]

# 当前 kimi.com 模型选择器文案（帮助中心仍称「K2.6」，页面显示为「快速」）
WEB_MODEL_LABELS = {
    "fast": "快速",
    "k3": "K3",
    "k3_cluster": "K3 集群",
}

WEB_EFFORT_LABELS = {
    "standard": "标准",
    "advanced": "进阶",
    "extreme": "极致",
}

# kimi_mode 预设 → (模型 key, 思考强度 key)
WEB_MODE_PRESETS: dict[str, tuple[str, str]] = {
    "fast": ("fast", "standard"),
    "thinking": ("fast", "advanced"),
    "k3": ("k3", "standard"),
    "k3_advanced": ("k3", "advanced"),
    "k3_extreme": ("k3", "extreme"),
    "agent": ("k3", "standard"),
    "k3_cluster": ("k3_cluster", "standard"),
    "k3_cluster_advanced": ("k3_cluster", "advanced"),
    "k3_cluster_extreme": ("k3_cluster", "extreme"),
    "agent_group": ("k3_cluster", "standard"),
}

# 兼容旧常量名
WEB_MODE_LABELS = {
    mode: f"{WEB_MODEL_LABELS[model]} · {WEB_EFFORT_LABELS[effort]}"
    for mode, (model, effort) in WEB_MODE_PRESETS.items()
}

GENERATION_ACTIVE_SELECTORS = [
    'button:has-text("停止")',
    'button:has-text("Stop")',
    'button:has-text("停止生成")',
    '[class*="stop-btn"]',
    '[class*="stop-button"]',
    '[class*="StopButton"]',
    '[aria-label*="停止"]',
    '[aria-label*="Stop"]',
    # 进行中的思考/加载（避免匹配已完成的 thinking 面板）
    '[class*="thinking"][class*="loading"]',
    '[class*="thinking"][class*="stream"]',
    '[class*="thinking"][class*="running"]',
    'text=正在思考',
    'text=思考中',
]

# 像「复述任务/还在列提纲」的思考过程开头，不是最终答复
THINKING_NARRATION_PREFIXES = (
    "用户要求我作为",
    "用户要求我",
    "用户让我",
    "让我先",
    "首先，我需要",
    "首先我需要",
    "我需要先",
    "我先分析",
    "思考过程",
    "好的，我来",
    "好的，我先",
)

INCOMPLETE_THINKING_SUFFIXES = (
    "包括：",
    "包括:",
    "如下：",
    "如下:",
    "例如：",
    "例如:",
    "具体来说：",
    "具体来说:",
)

# Kimi 网页版高峰限流软提示（会作为助手消息返回，或弹窗遮挡操作）
BUSY_ANSWER_MARKERS = (
    "聊的人太多了",
    "聊天的人太多",
    "人太多啦",
    "人太多了",
    "有点累了",
    "晚点再问",
    "优先队列",
    "too many people",
    "a bit tired",
    "try again later",
)
# 忙线提示通常很短；过长文本即使含关键词也不当作限流
BUSY_ANSWER_MAX_CHARS = 200

BUSY_MODAL_SELECTORS = [
    '.modal-mask:has-text("人太多")',
    '.modal-mask:has-text("优先队列")',
    '.modal-mask:has-text("有点累了")',
    'div.body:has-text("人太多")',
    'div.body:has-text("优先队列")',
]

BUSY_MODAL_DISMISS_SELECTORS = [
    '.modal-mask button:has-text("知道了")',
    '.modal-mask button:has-text("我知道了")',
    '.modal-mask button:has-text("关闭")',
    '.modal-mask button:has-text("取消")',
    '.modal-mask button:has-text("稍后")',
    '.modal-mask [class*="close"]',
    '.modal-mask .bottom button',
]


class KimiBusyError(RuntimeError):
    """Kimi 网页持续返回繁忙/限流提示。"""


def is_kimi_busy_answer(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized or len(normalized) > BUSY_ANSWER_MAX_CHARS:
        return False
    lower = normalized.lower()
    for marker in BUSY_ANSWER_MARKERS:
        if marker.lower() in lower:
            return True
    return False


def is_kimi_busy_error_text(text: str) -> bool:
    """异常/日志文本里是否含忙线信号（不限制长度）。"""
    lower = (text or "").lower()
    if not lower:
        return False
    return any(marker.lower() in lower for marker in BUSY_ANSWER_MARKERS)


def extract_balanced_json(text: str) -> str | None:
    """从文本中提取第一个完整 JSON 对象或数组；未闭合则返回 None。"""
    if not text:
        return None
    start_candidates = [(text.find("{"), "{", "}"), (text.find("["), "[", "]")]
    start_candidates = [(idx, open_ch, close_ch) for idx, open_ch, close_ch in start_candidates if idx >= 0]
    if not start_candidates:
        return None
    start, open_ch, close_ch = min(start_candidates, key=lambda item: item[0])
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1].strip()
                try:
                    json.loads(candidate)
                except Exception:
                    return None
                return candidate
    return None


def looks_like_thinking_narration(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    if extract_balanced_json(normalized):
        return False
    return any(normalized.startswith(prefix) for prefix in THINKING_NARRATION_PREFIXES)


def looks_like_incomplete_answer(text: str) -> bool:
    """思考未完成、JSON 未闭合，或停在「包括：」这类半截提纲。"""
    normalized = (text or "").strip()
    if not normalized:
        return True
    if extract_balanced_json(normalized):
        return False
    if looks_like_thinking_narration(normalized):
        return True
    if any(normalized.endswith(suffix) for suffix in INCOMPLETE_THINKING_SUFFIXES):
        return True
    # 已出现 JSON 起始但尚未闭合
    if "{" in normalized or "[" in normalized:
        return extract_balanced_json(normalized) is None
    return False


def finalize_answer_text(text: str) -> str:
    """优先返回完整 JSON；否则返回原文。"""
    normalized = (text or "").strip()
    if not normalized:
        return ""
    extracted = extract_balanced_json(normalized)
    return extracted or normalized


def _default_storage_state_file() -> str | None:
    explicit = os.getenv("KIMI_STORAGE_STATE_FILE")
    if explicit:
        return explicit

    candidate = Path("./secrets/kimi_storage.json")
    if candidate.exists():
        return str(candidate)
    return None


def _default_headless(storage_state_file: str | None) -> bool:
    explicit = os.getenv("KIMI_BROWSER_HEADLESS")
    if explicit is not None:
        return explicit == "1"
    return bool(storage_state_file)


class BrowserKimiClient:
    def __init__(
        self,
        *,
        user_data_dir: str,
        headless: bool = False,
        chat_url: str = "https://www.kimi.com/",
        input_selector: str = '.chat-input-editor[contenteditable="true"]',
        answer_selector: str = ".chat-content-item-assistant .segment-content-box, .chat-content-item-assistant [class*='markdown'], .chat-content-item-assistant",
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
        busy_max_attempts: int = 5,
        busy_retry_wait_seconds: float = 60.0,
        busy_retry_backoff: float = 1.5,
        busy_retry_wait_max_seconds: float = 180.0,
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
        self.busy_max_attempts = max(1, int(busy_max_attempts))
        self.busy_retry_wait_seconds = max(0.0, float(busy_retry_wait_seconds))
        self.busy_retry_backoff = max(1.0, float(busy_retry_backoff))
        self.busy_retry_wait_max_seconds = max(
            self.busy_retry_wait_seconds,
            float(busy_retry_wait_max_seconds),
        )
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._lock = asyncio.Lock()
        self._active_session_id: str | None = None

    @classmethod
    def from_env(cls) -> "BrowserKimiClient":
        storage_state_file = _default_storage_state_file()
        return cls(
            user_data_dir=os.getenv("KIMI_BROWSER_PROFILE", "./kimi-browser-profile"),
            headless=_default_headless(storage_state_file),
            chat_url=os.getenv("KIMI_CHAT_URL", "https://www.kimi.com/"),
            input_selector=os.getenv("KIMI_INPUT_SELECTOR", '.chat-input-editor[contenteditable="true"]'),
            answer_selector=os.getenv(
                "KIMI_ANSWER_SELECTOR",
                ".chat-content-item-assistant .segment-content-box, .chat-content-item-assistant [class*='markdown'], .chat-content-item-assistant",
            ),
            timeout_seconds=float(os.getenv("KIMI_BROWSER_TIMEOUT", "300")),
            start_timeout_seconds=(
                float(os.getenv("KIMI_BROWSER_START_TIMEOUT"))
                if os.getenv("KIMI_BROWSER_START_TIMEOUT")
                else None
            ),
            cookies_file=os.getenv("KIMI_COOKIES_FILE"),
            curl_file=os.getenv("KIMI_CURL_FILE"),
            storage_state_file=storage_state_file,
            user_agent=os.getenv("KIMI_USER_AGENT"),
            new_chat_per_request=os.getenv("KIMI_NEW_CHAT_PER_REQUEST", "1") == "1",
            new_chat_selector=os.getenv("KIMI_NEW_CHAT_SELECTOR"),
            default_web_mode=os.getenv("KIMI_WEB_MODE", "fast"),
            default_deep_thinking=os.getenv("KIMI_DEEP_THINKING", "0") == "1",
            busy_max_attempts=int(os.getenv("KIMI_BUSY_MAX_ATTEMPTS", "5")),
            busy_retry_wait_seconds=float(os.getenv("KIMI_BUSY_RETRY_WAIT_SECONDS", "60")),
            busy_retry_backoff=float(os.getenv("KIMI_BUSY_RETRY_BACKOFF", "1.5")),
            busy_retry_wait_max_seconds=float(os.getenv("KIMI_BUSY_RETRY_WAIT_MAX_SECONDS", "180")),
        )

    def _busy_retry_delay(self, attempt: int) -> float:
        """attempt 从 1 起；第 2 次请求前用 base，之后按 backoff 递增并封顶。"""
        if attempt <= 1:
            return 0.0
        delay = self.busy_retry_wait_seconds * (self.busy_retry_backoff ** (attempt - 2))
        return min(delay, self.busy_retry_wait_max_seconds)

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
            logger.info("storage state loaded: %s", storage_state_summary(state))
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
                    "Kimi 登录态在 Docker 中未被接受（页面仍跳转登录页）。\n"
                    f"当前文件摘要: {summary}\n"
                    "常见原因：会话已过期，或 Mac 导出的指纹 cookie 与 Linux 容器不兼容。"
                    "建议在远程服务器上用 noVNC 登录并导出，或使用 official API 模式。"
                )
            raise RuntimeError(
                "Kimi 未登录：当前页面仍在登录页。"
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
        raise RuntimeError(f"未找到可用的 Kimi 页面选项控件: {option_name}")

    def _resolve_model_and_effort(
        self,
        *,
        web_mode: str | None,
        deep_thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[str, str]:
        mode = (web_mode or self.default_web_mode or "fast").strip().lower()
        preset = WEB_MODE_PRESETS.get(mode)
        if not preset:
            raise ValueError(f"unsupported Kimi web mode: {web_mode!r}")
        model_key, effort_key = preset
        if reasoning_effort:
            effort_key = reasoning_effort
        elif deep_thinking and effort_key == "standard":
            effort_key = "advanced"
        return model_key, effort_key

    async def _current_model_label(self, page: Any) -> str | None:
        try:
            current = page.locator(".current-model .name").first
            if await current.count() == 0:
                return None
            return (await current.inner_text()).strip() or None
        except Exception:
            return None

    async def _current_effort_label(self, page: Any) -> str | None:
        for selector in (".current-model .current-effort", ".current-model .effort-value", ".effort-value"):
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                text = (await locator.inner_text()).strip()
                if text:
                    return text
            except Exception:
                continue
        return None

    async def _open_model_popover(self, page: Any) -> bool:
        try:
            if await page.locator(".models-popover .model-item").count() > 0:
                return True
        except Exception:
            pass
        openers = [
            ".current-model",
            ".right-area:has(.current-model)",
            f'text="{WEB_MODEL_LABELS["fast"]}"',
        ]
        for selector in openers:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                await locator.first.click(timeout=3_000)
                await asyncio.sleep(0.35)
                if await page.locator(".models-popover .model-item, .n-popover .model-item").count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _select_web_model(self, page: Any, model_key: str) -> bool:
        label = WEB_MODEL_LABELS.get(model_key)
        if not label:
            raise ValueError(f"unsupported Kimi web model: {model_key!r}")
        current = await self._current_model_label(page)
        if current == label:
            logger.info("browser.option skip=web_model:%s state=selected", model_key)
            return False
        if not await self._open_model_popover(page):
            logger.warning(
                "browser.option skip=web_model:%s label=%s reason=mode_dropdown_not_found",
                model_key,
                label,
            )
            return False

        option_selectors = [
            f'.models-popover .model-item:has(.name:text-is("{label}"))',
            f'.models-popover .model-item:has(.header:has-text("{label}"))',
            f'.n-popover .model-item:has(.name:text-is("{label}"))',
            f'.models-popover .model-item:has-text("{label}")',
        ]
        for selector in option_selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                await locator.first.click(timeout=3_000)
                logger.info(
                    "browser.option click=web_model:%s label=%s selector=%s",
                    model_key,
                    label,
                    selector,
                )
                await asyncio.sleep(0.5)
                return True
            except Exception:
                continue
        logger.warning(
            "browser.option skip=web_model:%s label=%s reason=mode_option_not_found",
            model_key,
            label,
        )
        return False

    async def _select_reasoning_effort(self, page: Any, effort_key: str) -> bool:
        label = WEB_EFFORT_LABELS.get(effort_key)
        if not label:
            raise ValueError(f"unsupported Kimi reasoning effort: {effort_key!r}")
        current = await self._current_effort_label(page)
        if current == label:
            logger.info("browser.option skip=reasoning_effort:%s state=selected", effort_key)
            return False
        if not await self._open_model_popover(page):
            logger.warning(
                "browser.option skip=reasoning_effort:%s label=%s reason=mode_dropdown_not_found",
                effort_key,
                label,
            )
            return False

        # 打开「思考强度」二级菜单
        try:
            effort_row = page.locator(".models-popover .effort-item, .n-popover .effort-item").first
            if await effort_row.count() > 0:
                await effort_row.click(timeout=3_000)
                await asyncio.sleep(0.35)
        except Exception:
            pass

        option_selectors = [
            f'.effort-popover .effort-option:has(.effort-name:text-is("{label}"))',
            f'.effort-popover .effort-option:has-text("{label}")',
            f'.effort-container .effort-option:has(.effort-name:text-is("{label}"))',
            f'.effort-container .effort-option:has-text("{label}")',
            f'.n-popover .effort-option:has-text("{label}")',
        ]
        for selector in option_selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                await locator.first.click(timeout=3_000)
                logger.info(
                    "browser.option click=reasoning_effort:%s label=%s selector=%s",
                    effort_key,
                    label,
                    selector,
                )
                await asyncio.sleep(0.4)
                return True
            except Exception:
                continue
        logger.warning(
            "browser.option skip=reasoning_effort:%s label=%s reason=effort_option_not_found",
            effort_key,
            label,
        )
        return False

    async def _apply_chat_options(
        self,
        page: Any,
        *,
        web_mode: str | None = None,
        deep_thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        model_key, effort_key = self._resolve_model_and_effort(
            web_mode=web_mode,
            deep_thinking=deep_thinking,
            reasoning_effort=reasoning_effort,
        )
        logger.info(
            "browser.option apply model=%s effort=%s web_mode=%s",
            model_key,
            effort_key,
            web_mode,
        )
        await self._select_web_model(page, model_key)
        await self._select_reasoning_effort(page, effort_key)

    async def _busy_modal_text(self, page: Any) -> str | None:
        for selector in BUSY_MODAL_SELECTORS:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                text = (await locator.first.inner_text()).strip()
                if text and (is_kimi_busy_answer(text) or is_kimi_busy_error_text(text)):
                    return text
            except Exception:
                continue
        return None

    async def _dismiss_busy_modal(self, page: Any) -> bool:
        text = await self._busy_modal_text(page)
        if not text:
            return False
        logger.warning("browser.busy_modal dismiss content=%s", text[:160])
        for selector in BUSY_MODAL_DISMISS_SELECTORS:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                await locator.first.click(timeout=2_000)
                await asyncio.sleep(0.4)
                if not await self._busy_modal_text(page):
                    return True
            except Exception:
                continue
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.4)
        except Exception:
            pass
        return await self._busy_modal_text(page) is None

    async def _send_message(self, page: Any, input_box: Any, prompt: str) -> None:
        await self._dismiss_busy_modal(page)
        await self._scroll_chat_to_bottom(page)
        try:
            await input_box.click(timeout=5_000)
        except Exception as exc:
            modal_text = await self._busy_modal_text(page)
            if modal_text or is_kimi_busy_error_text(str(exc)):
                raise KimiBusyError(modal_text or str(exc)) from exc
            raise
        await input_box.fill("")
        await input_box.fill(prompt)
        await asyncio.sleep(0.15)
        if await self._click_send_button(page):
            return
        await input_box.press("Enter")
        await asyncio.sleep(0.4)
        remaining = await self._input_text(input_box)
        if remaining:
            raise RuntimeError("消息未能发送：未找到发送按钮，且输入框在 Enter 后仍有内容")

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
        first_index = max(0, count - 8)
        for index in range(count - 1, first_index - 1, -1):
            text = (await answer_blocks.nth(index).inner_text()).strip()
            if text:
                return count, text
        return count, ""

    async def _collect_answer_candidates(self, answer_blocks: Any) -> list[str]:
        count = await answer_blocks.count()
        if count == 0:
            return []
        first_index = max(0, count - 12)
        texts: list[str] = []
        for index in range(first_index, count):
            text = (await answer_blocks.nth(index).inner_text()).strip()
            if text:
                texts.append(text)
        return texts

    def _pick_final_answer(self, candidates: list[str]) -> str:
        if not candidates:
            return ""
        for text in reversed(candidates):
            extracted = extract_balanced_json(text)
            if extracted:
                return extracted
        for text in reversed(candidates):
            if not looks_like_incomplete_answer(text):
                return text
        return candidates[-1]

    async def _extract_final_answer(self, answer_blocks: Any) -> str:
        return self._pick_final_answer(await self._collect_answer_candidates(answer_blocks))

    async def chat_completion(
        self,
        payload: dict[str, Any],
        *,
        new_chat: bool | None = None,
        session_id: str | None = None,
        web_mode: str | None = None,
        deep_thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")

        should_new_chat = self.new_chat_per_request if new_chat is None else new_chat
        sid = (session_id or "").strip() or None
        prompt = compose_browser_prompt(messages, reuse_session=not should_new_chat)
        if not prompt:
            raise ValueError("messages did not contain text content")

        resolved_web_mode = web_mode or self.default_web_mode
        resolved_deep_thinking = self.default_deep_thinking if deep_thinking is None else deep_thinking

        async with self._lock:
            if sid and self._active_session_id and sid != self._active_session_id:
                should_new_chat = True
                logger.info(
                    "browser.session_switch from=%s to=%s force_new_chat=true",
                    self._active_session_id,
                    sid,
                )
            page = await self._ensure_page()
            last_busy = ""
            for attempt in range(1, self.busy_max_attempts + 1):
                force_new_chat = should_new_chat and attempt == 1
                if attempt > 1:
                    delay = self._busy_retry_delay(attempt)
                    logger.warning(
                        "browser.busy_retry attempt=%s/%s wait=%.1fs last=%s",
                        attempt,
                        self.busy_max_attempts,
                        delay,
                        last_busy[:120],
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    # 忙线重试时复用当前网页会话，避免同任务被拆成多个新对话
                    force_new_chat = False

                await self._dismiss_busy_modal(page)
                if force_new_chat:
                    await self._start_new_conversation(page)
                    self._active_session_id = sid
                elif sid and not self._active_session_id:
                    self._active_session_id = sid
                if sid:
                    logger.info(
                        "browser.session_id=%s new_chat=%s attempt=%s",
                        sid,
                        force_new_chat,
                        attempt,
                    )
                await self._apply_chat_options(
                    page,
                    web_mode=resolved_web_mode,
                    deep_thinking=resolved_deep_thinking,
                    reasoning_effort=reasoning_effort,
                )
                if not force_new_chat:
                    await self._wait_for_generation_idle(page)
                await self._dismiss_busy_modal(page)
                input_box = page.locator(self.input_selector).first
                answer_blocks = page.locator(self.answer_selector)
                before_count, before_last_text = await self._last_answer_snapshot(answer_blocks)
                logger.info(
                    "browser.send prompt_chars=%s before_count=%s reuse=%s attempt=%s",
                    len(prompt),
                    before_count,
                    not force_new_chat,
                    attempt,
                )
                try:
                    await self._send_message(page, input_box, prompt)
                    await self._wait_for_new_answer(page, answer_blocks, before_count, before_last_text)
                    answer = await self._wait_until_answer_stable(page, answer_blocks)
                except KimiBusyError as exc:
                    last_busy = str(exc)
                    logger.warning(
                        "browser.busy_error attempt=%s/%s content=%s",
                        attempt,
                        self.busy_max_attempts,
                        last_busy[:160],
                    )
                    await self._dismiss_busy_modal(page)
                    continue
                except Exception as exc:
                    if is_kimi_busy_error_text(str(exc)) or await self._busy_modal_text(page):
                        last_busy = str(exc)
                        logger.warning(
                            "browser.busy_exception attempt=%s/%s content=%s",
                            attempt,
                            self.busy_max_attempts,
                            last_busy[:160],
                        )
                        await self._dismiss_busy_modal(page)
                        continue
                    raise
                if not is_kimi_busy_answer(answer):
                    return answer
                last_busy = answer
                logger.warning(
                    "browser.busy_answer attempt=%s/%s content=%s",
                    attempt,
                    self.busy_max_attempts,
                    answer[:160],
                )

            raise KimiBusyError(
                f"Kimi 持续繁忙（已重试 {self.busy_max_attempts} 次）: {last_busy}"
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
            busy_text = await self._busy_modal_text(page)
            if busy_text:
                raise KimiBusyError(busy_text)
            count = await answer_blocks.count()
            if count > before_count:
                logger.info("browser.answer_started count=%s->%s", before_count, count)
                return
            if count > 0 and before_count > 0:
                _, text = await self._last_answer_snapshot(answer_blocks)
                if text and text != before_last_text:
                    logger.info("browser.answer_started last_text_changed")
                    return
            if await self._is_generation_active(page):
                logger.info("browser.answer_started generation_active")
                return
            await asyncio.sleep(0.5)
        count = await answer_blocks.count()
        raise TimeoutError(
            "Timed out waiting for Kimi to start answering "
            f"(before_count={before_count}, current_count={count}, "
            f"start_timeout={self.start_timeout_seconds}s). "
            "若使用 new_chat=false，请确认上一条已生成完毕；"
            "深度思考模式可能需增大 KIMI_BROWSER_START_TIMEOUT。"
        )

    async def _wait_until_answer_stable(self, page: Any, answer_blocks: Any) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        last_text = ""
        stable_count = 0
        saw_generation = False
        while time.monotonic() < deadline:
            generating = await self._is_generation_active(page)
            if generating:
                saw_generation = True
                stable_count = 0
                await asyncio.sleep(0.5)
                continue

            text = await self._extract_final_answer(answer_blocks)
            if not text:
                await asyncio.sleep(0.5)
                continue

            if looks_like_incomplete_answer(text):
                if text != last_text:
                    logger.info(
                        "browser.answer_waiting_final reason=incomplete_or_thinking chars=%s preview=%s",
                        len(text),
                        text[:120].replace("\n", " "),
                    )
                last_text = text
                stable_count = 0
                await asyncio.sleep(0.75)
                continue

            finalized = finalize_answer_text(text)
            if finalized == last_text:
                stable_count += 1
                # 思考结束后再多确认几次，避免把短暂停顿的推理当最终答案
                needed = 6 if saw_generation else 4
                if stable_count >= needed:
                    logger.info(
                        "browser.answer_stable chars=%s json=%s",
                        len(finalized),
                        finalized.lstrip().startswith(("{", "[")),
                    )
                    return finalized
            else:
                stable_count = 0
                last_text = finalized
            await asyncio.sleep(0.75)

        if last_text and not looks_like_incomplete_answer(last_text):
            return finalize_answer_text(last_text)
        raise TimeoutError(
            "Timed out waiting for Kimi final answer text "
            f"(last_chars={len(last_text)}, incomplete={looks_like_incomplete_answer(last_text)})."
        )

    async def aclose(self) -> None:
        if self._page is not None:
            with contextlib.suppress(Exception):
                await self._page.close()
            self._page = None
        if self._context is not None:
            with contextlib.suppress(Exception):
                await self._context.close()
            self._context = None
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None
