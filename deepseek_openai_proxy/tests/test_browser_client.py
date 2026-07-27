import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_client import (
    DEFAULT_SEND_BUTTON_SELECTORS,
    BrowserDeepSeekClient,
    _SEND_BUTTON_NEAR_INPUT_JS,
)


class FakeLocator:
    def __init__(
        self,
        count: int = 1,
        visible: bool = True,
        disabled: bool = False,
        *,
        class_name: str = "",
        aria_disabled: str | None = None,
    ):
        self._count = count
        self._visible = visible
        self._disabled = disabled
        self._class_name = class_name
        self._aria_disabled = aria_disabled
        self.clicked = False

    async def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    async def is_visible(self) -> bool:
        return self._visible

    async def get_attribute(self, name: str):
        if name == "aria-disabled":
            if self._aria_disabled is not None:
                return self._aria_disabled
            return "true" if self._disabled else "false"
        if name == "class":
            return self._class_name
        return None

    async def click(self, timeout: int = 0) -> None:
        self.clicked = True


class FakePage:
    def __init__(self, *, url: str = "https://chat.deepseek.com/a/chat/s/old-session"):
        self.url = url
        self.goto = AsyncMock()
        self._locators: dict[str, FakeLocator] = {}

    def locator(self, selector: str):
        return self._locators.get(selector, FakeLocator(count=0))


@pytest.mark.anyio
async def test_start_new_conversation_clicks_new_chat_button():
    client = BrowserDeepSeekClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=True,
    )
    page = FakePage()
    page._locators['div[tabindex="0"]:has-text("新对话")'] = FakeLocator()

    client._click_new_chat_button = AsyncMock(return_value=True)
    client._wait_for_chat_ready = AsyncMock()

    await client._start_new_conversation(page)

    client._click_new_chat_button.assert_awaited_once_with(page)
    client._wait_for_chat_ready.assert_awaited_once_with(page)
    page.goto.assert_not_awaited()


@pytest.mark.anyio
async def test_start_new_conversation_falls_back_to_goto():
    client = BrowserDeepSeekClient(
        user_data_dir="/tmp/test-profile",
        chat_url="https://chat.deepseek.com/",
    )
    page = FakePage(url="https://chat.deepseek.com/a/chat/s/old-session")

    client._click_new_chat_button = AsyncMock(return_value=False)
    client._wait_for_chat_ready = AsyncMock()

    await client._start_new_conversation(page)

    page.goto.assert_awaited_once_with(
        "https://chat.deepseek.com/",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    client._wait_for_chat_ready.assert_awaited_once_with(page)


@pytest.mark.anyio
async def test_chat_completion_starts_new_conversation_when_enabled():
    client = BrowserDeepSeekClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=True,
    )
    page = MagicMock()
    input_box = MagicMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()
    page.locator = MagicMock(return_value=MagicMock(first=input_box))
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)
    page.locator.side_effect = lambda selector: (
        answer_blocks if "assistant" in selector else MagicMock(first=input_box)
    )

    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._wait_for_generation_idle = AsyncMock()
    client._send_message = AsyncMock()
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._apply_chat_options = AsyncMock()

    result = await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

    assert result == "hello"
    client._start_new_conversation.assert_awaited_once_with(page)


@pytest.mark.anyio
async def test_chat_completion_skips_new_conversation_when_disabled():
    client = BrowserDeepSeekClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=False,
    )
    page = MagicMock()
    input_box = MagicMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)
    page.locator = MagicMock(
        side_effect=lambda selector: (
            answer_blocks if "assistant" in selector else MagicMock(first=input_box)
        )
    )

    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._wait_for_generation_idle = AsyncMock()
    client._send_message = AsyncMock()
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._apply_chat_options = AsyncMock()

    await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

    client._start_new_conversation.assert_not_awaited()


@pytest.mark.anyio
async def test_chat_completion_session_switch_forces_new_chat():
    client = BrowserDeepSeekClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=False,
    )
    page = MagicMock()
    input_box = MagicMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(side_effect=[0, 1, 1, 1, 1])
    page.locator = MagicMock(
        side_effect=lambda selector: (
            answer_blocks if "assistant" in selector else MagicMock(first=input_box)
        )
    )

    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._wait_for_generation_idle = AsyncMock()
    client._send_message = AsyncMock()
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._active_session_id = "task-a"
    client._apply_chat_options = AsyncMock()

    await client.chat_completion(
        {"messages": [{"role": "user", "content": "hi"}]},
        new_chat=False,
        session_id="task-b",
    )

    client._start_new_conversation.assert_awaited_once()
    assert client._active_session_id == "task-b"


@pytest.mark.anyio
async def test_chat_completion_payload_new_chat_overrides_env_default():
    client = BrowserDeepSeekClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=True,
    )
    page = MagicMock()
    input_box = MagicMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)
    page.locator = MagicMock(
        side_effect=lambda selector: (
            answer_blocks if "assistant" in selector else MagicMock(first=input_box)
        )
    )

    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._wait_for_generation_idle = AsyncMock()
    client._send_message = AsyncMock()
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._apply_chat_options = AsyncMock()

    await client.chat_completion(
        {"messages": [{"role": "user", "content": "hi"}], "new_chat": False},
        new_chat=False,
    )

    client._start_new_conversation.assert_not_awaited()


@pytest.mark.anyio
async def test_apply_chat_options_selects_requested_mode_and_deep_thinking():
    client = BrowserDeepSeekClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://chat.deepseek.com/")
    expert = FakeLocator()
    deep_thinking = FakeLocator(disabled=False)
    page._locators['[role="radio"]:has-text("专家模式")'] = expert
    page._locators['[tabindex="0"]:has-text("深度思考")'] = deep_thinking

    await client._apply_chat_options(page, web_mode="expert", deep_thinking=True)

    assert expert.clicked is True
    assert deep_thinking.clicked is True


@pytest.mark.anyio
async def test_apply_chat_options_skips_already_selected_controls():
    client = BrowserDeepSeekClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://chat.deepseek.com/")
    fast = FakeLocator()
    fast.get_attribute = AsyncMock(side_effect=lambda name: "true" if name == "aria-checked" else None)
    deep_thinking = FakeLocator()
    deep_thinking.get_attribute = AsyncMock(side_effect=lambda name: "true" if name == "aria-pressed" else None)
    page._locators['[role="radio"]:has-text("快速模式")'] = fast
    page._locators['[tabindex="0"]:has-text("深度思考")'] = deep_thinking

    await client._apply_chat_options(page, web_mode="fast", deep_thinking=True)

    assert fast.clicked is False
    assert deep_thinking.clicked is False


def test_send_button_selectors_include_primary_circle_and_skip_disabled():
    assert any("ds-button--primary" in selector for selector in DEFAULT_SEND_BUTTON_SELECTORS)
    assert any(
        "ds-button--circle" in selector and "ds-button--disabled" in selector
        for selector in DEFAULT_SEND_BUTTON_SELECTORS
    )


def test_near_input_js_targets_primary_button_and_skips_disabled_class():
    assert "ds-button--primary" in _SEND_BUTTON_NEAR_INPUT_JS
    assert "ds-button--disabled" in _SEND_BUTTON_NEAR_INPUT_JS


@pytest.mark.anyio
async def test_click_send_button_clicks_primary_selector():
    client = BrowserDeepSeekClient(user_data_dir="/tmp/test-profile")
    page = FakePage()
    button = FakeLocator(class_name="ds-button ds-button--primary ds-button--circle")
    page._locators[
        'div.ds-button.ds-button--primary.ds-button--circle[role="button"]:not(.ds-button--disabled)'
    ] = button
    input_box = MagicMock()
    input_box.evaluate_handle = AsyncMock(side_effect=RuntimeError("no near button"))

    ok = await client._click_send_button(page, input_box)

    assert ok is True
    assert button.clicked is True


@pytest.mark.anyio
async def test_click_send_button_skips_disabled_class_without_aria():
    client = BrowserDeepSeekClient(user_data_dir="/tmp/test-profile")
    page = FakePage()
    # Match a legacy/broad selector that does not filter by class.
    button = FakeLocator(
        class_name="ds-button ds-button--primary ds-button--disabled",
        aria_disabled=None,
    )
    page._locators['div.ds-button.ds-button--primary[role="button"]'] = button
    input_box = MagicMock()
    input_box.evaluate_handle = AsyncMock(side_effect=RuntimeError("no near button"))

    # Temporarily force only the broad selector so class-disabled logic is exercised.
    import browser_client as bc

    original = bc.DEFAULT_SEND_BUTTON_SELECTORS[:]
    bc.DEFAULT_SEND_BUTTON_SELECTORS[:] = [
        'div.ds-button.ds-button--primary[role="button"]',
    ]
    try:
        ok = await client._click_send_button(page, input_box)
    finally:
        bc.DEFAULT_SEND_BUTTON_SELECTORS[:] = original

    assert ok is False
    assert button.clicked is False


@pytest.mark.anyio
async def test_send_message_prefers_send_button_for_long_prompt():
    client = BrowserDeepSeekClient(user_data_dir="/tmp/test-profile")
    page = MagicMock()
    input_box = MagicMock()
    input_box.click = AsyncMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()
    input_box.input_value = AsyncMock(side_effect=["x" * 100, ""])
    input_box.inner_text = AsyncMock(return_value="")
    input_box.evaluate_handle = AsyncMock()

    client._scroll_chat_to_bottom = AsyncMock()
    client._click_send_button_with_retry = AsyncMock(return_value=True)
    client._input_remaining_text = AsyncMock(side_effect=["x" * 100, ""])

    await client._send_message(page, input_box, "x" * 100)

    client._click_send_button_with_retry.assert_awaited_once_with(page, input_box)
    input_box.press.assert_not_awaited()


@pytest.mark.anyio
async def test_send_message_falls_back_to_enter_when_send_button_fails():
    client = BrowserDeepSeekClient(user_data_dir="/tmp/test-profile")
    page = MagicMock()
    input_box = MagicMock()
    input_box.click = AsyncMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()

    client._scroll_chat_to_bottom = AsyncMock()
    client._click_send_button_with_retry = AsyncMock(return_value=False)
    client._input_remaining_text = AsyncMock(side_effect=["hello", ""])

    await client._send_message(page, input_box, "hello")

    input_box.press.assert_awaited_once_with("Enter")


@pytest.mark.anyio
async def test_send_message_raises_when_all_send_attempts_fail():
    client = BrowserDeepSeekClient(user_data_dir="/tmp/test-profile")
    page = MagicMock()
    input_box = MagicMock()
    input_box.click = AsyncMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()

    client._scroll_chat_to_bottom = AsyncMock()
    client._click_send_button_with_retry = AsyncMock(return_value=False)
    client._input_remaining_text = AsyncMock(return_value="still here")

    with pytest.raises(RuntimeError, match="remaining_chars=10"):
        await client._send_message(page, input_box, "still here")
