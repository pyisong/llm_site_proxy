import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_client import (
    BrowserStepFunClient,
    _default_headless,
    _default_profile_dir,
    _default_storage_state_file,
)


class FakeLocator:
    def __init__(self, count: int = 1, visible: bool = True, disabled: bool = False):
        self._count = count
        self._visible = visible
        self._disabled = disabled
        self.clicked = False
        self.click_calls = []

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

    async def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        if state == "visible" and not self._visible:
            raise TimeoutError("not visible")

    async def get_attribute(self, name: str):
        if name == "aria-disabled":
            return "true" if self._disabled else "false"
        return None

    async def click(self, timeout: int = 0, force: bool = False) -> None:
        self.click_calls.append({"timeout": timeout, "force": force})
        self.clicked = True


class FakePage:
    def __init__(self, *, url: str = "https://chat.stepfun.com/chats/old-session"):
        self.url = url
        self.goto = AsyncMock()
        self._locators: dict[str, FakeLocator] = {}

    def locator(self, selector: str):
        return self._locators.get(selector, FakeLocator(count=0))


class FakeAnswerNode:
    def __init__(self, text: str):
        self._text = text

    async def inner_text(self) -> str:
        return self._text


class FakeAnswerBlocks:
    def __init__(self, texts: list[str]):
        self._texts = texts

    async def count(self) -> int:
        return len(self._texts)

    def nth(self, index: int):
        return FakeAnswerNode(self._texts[index])


class FakeClosable:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.closed = False

    async def close(self):
        self.closed = True
        if self.exc:
            raise self.exc


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


def test_default_profile_dir_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("STEPFUN_BROWSER_PROFILE", "/tmp/custom-stepfun-profile")

    assert _default_profile_dir() == "/tmp/custom-stepfun-profile"


def test_default_profile_dir_prefers_cwd_profile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STEPFUN_BROWSER_PROFILE", raising=False)
    profile = tmp_path / "stepfun-browser-profile"
    profile.mkdir()

    assert _default_profile_dir(module_dir=tmp_path / "module") == "stepfun-browser-profile"


def test_default_profile_dir_falls_back_to_module_profile(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.delenv("STEPFUN_BROWSER_PROFILE", raising=False)
    module_dir = tmp_path / "stepfun_openai_proxy"
    profile = module_dir / "stepfun-browser-profile"
    profile.mkdir(parents=True)

    assert _default_profile_dir(module_dir=module_dir) == str(profile)


def test_default_storage_state_file_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("STEPFUN_STORAGE_STATE_FILE", "/tmp/custom-stepfun-storage.json")

    assert _default_storage_state_file() == "/tmp/custom-stepfun-storage.json"


def test_default_storage_state_file_discovers_cwd_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STEPFUN_STORAGE_STATE_FILE", raising=False)
    secret = tmp_path / "secrets" / "stepfun_storage.json"
    secret.parent.mkdir()
    secret.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    assert _default_storage_state_file(module_dir=tmp_path / "module") == "secrets/stepfun_storage.json"


def test_default_storage_state_file_discovers_module_secret(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.delenv("STEPFUN_STORAGE_STATE_FILE", raising=False)
    module_dir = tmp_path / "stepfun_openai_proxy"
    secret = module_dir / "secrets" / "stepfun_storage.json"
    secret.parent.mkdir(parents=True)
    secret.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    assert _default_storage_state_file(module_dir=module_dir) == str(secret)


def test_default_headless_uses_profile_or_storage_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("STEPFUN_BROWSER_HEADLESS", raising=False)
    profile = tmp_path / "stepfun-browser-profile"
    profile.mkdir()

    assert _default_headless(None, str(profile)) is True
    assert _default_headless("secrets/stepfun_storage.json", str(tmp_path / "missing")) is True
    assert _default_headless(None, str(tmp_path / "missing")) is False


def test_default_headless_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("STEPFUN_BROWSER_HEADLESS", "0")
    assert _default_headless("secrets/stepfun_storage.json", "./stepfun-browser-profile") is False

    monkeypatch.setenv("STEPFUN_BROWSER_HEADLESS", "1")
    assert _default_headless(None, "missing") is True


@pytest.mark.anyio
async def test_aclose_ignores_already_closed_browser_transport():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    browser = FakeClosable(RuntimeError("Browser.close: transport closed"))
    playwright = FakePlaywright()
    client._browser = browser
    client._playwright = playwright

    await client.aclose()

    assert browser.closed is True
    assert playwright.stopped is True
    assert client._browser is None
    assert client._playwright is None


@pytest.mark.anyio
async def test_start_new_conversation_clicks_new_chat_button():
    client = BrowserStepFunClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=True,
    )
    page = FakePage()
    page._locators['button:has-text("开启新话题")'] = FakeLocator()

    client._click_new_chat_button = AsyncMock(return_value=True)
    client._wait_for_chat_ready = AsyncMock()

    await client._start_new_conversation(page)

    client._click_new_chat_button.assert_awaited_once_with(page)
    client._wait_for_chat_ready.assert_awaited_once_with(page)
    page.goto.assert_not_awaited()


@pytest.mark.anyio
async def test_start_new_conversation_falls_back_to_goto():
    client = BrowserStepFunClient(
        user_data_dir="/tmp/test-profile",
        chat_url="https://chat.stepfun.com/chats/new",
    )
    page = FakePage(url="https://chat.stepfun.com/chats/old-session")

    client._click_new_chat_button = AsyncMock(return_value=False)
    client._wait_for_chat_ready = AsyncMock()

    await client._start_new_conversation(page)

    page.goto.assert_awaited_once_with(
        "https://chat.stepfun.com/chats/new",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    client._wait_for_chat_ready.assert_awaited_once_with(page)


@pytest.mark.anyio
async def test_find_input_box_falls_back_when_current_selector_disappears():
    client = BrowserStepFunClient(
        user_data_dir="/tmp/test-profile",
        input_selector='textarea[placeholder*="任何问题"]:not([disabled])',
    )
    page = FakePage()
    fallback = FakeLocator()
    page._locators['textarea.Publisher_textarea__pMX9t:not([disabled])'] = fallback

    input_box = await client._find_input_box(page, timeout_seconds=0.2)

    assert input_box is fallback
    assert client.input_selector == 'textarea.Publisher_textarea__pMX9t:not([disabled])'


@pytest.mark.anyio
async def test_answer_text_prefers_text_different_from_previous_answer():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    blocks = FakeAnswerBlocks(["B2", "A1"])

    text = await client._answer_text(blocks, exclude_text="A1")

    assert text == "B2"


@pytest.mark.anyio
async def test_chat_completion_starts_new_conversation_when_enabled():
    client = BrowserStepFunClient(
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
    client._resolve_answer_text = AsyncMock(return_value="")
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._apply_chat_options = AsyncMock()
    client._find_input_box = AsyncMock(return_value=input_box)

    result = await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

    assert result == "hello"
    client._start_new_conversation.assert_awaited_once_with(page)


@pytest.mark.anyio
async def test_chat_completion_skips_new_conversation_when_disabled():
    client = BrowserStepFunClient(
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
    client._resolve_answer_text = AsyncMock(return_value="")
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._apply_chat_options = AsyncMock()
    client._find_input_box = AsyncMock(return_value=input_box)

    await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

    client._start_new_conversation.assert_not_awaited()


@pytest.mark.anyio
async def test_chat_completion_session_switch_forces_new_chat():
    client = BrowserStepFunClient(
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
    client._resolve_answer_text = AsyncMock(return_value="")
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._active_session_id = "task-a"
    client._apply_chat_options = AsyncMock()
    client._find_input_box = AsyncMock(return_value=input_box)

    await client.chat_completion(
        {"messages": [{"role": "user", "content": "hi"}]},
        new_chat=False,
        session_id="task-b",
    )

    client._start_new_conversation.assert_awaited_once()
    assert client._active_session_id == "task-b"


@pytest.mark.anyio
async def test_chat_completion_deep_research_forces_new_chat_even_when_false():
    client = BrowserStepFunClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=False,
    )
    page = MagicMock()
    input_box = MagicMock()
    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._wait_for_generation_idle = AsyncMock()
    client._send_message = AsyncMock()
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._resolve_answer_text = AsyncMock(return_value="")
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._apply_chat_options = AsyncMock()
    client._find_input_box = AsyncMock(return_value=input_box)

    await client.chat_completion(
        {"messages": [{"role": "user", "content": "hi"}]},
        new_chat=False,
        web_mode="deep_research",
    )

    client._start_new_conversation.assert_awaited()
    client._wait_for_generation_idle.assert_not_awaited()


@pytest.mark.anyio
async def test_chat_completion_payload_new_chat_overrides_env_default():
    client = BrowserStepFunClient(
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
    client._resolve_answer_text = AsyncMock(return_value="")
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="hello")
    client._apply_chat_options = AsyncMock()
    client._find_input_box = AsyncMock(return_value=input_box)

    await client.chat_completion(
        {"messages": [{"role": "user", "content": "hi"}], "new_chat": False},
        new_chat=False,
    )

    client._start_new_conversation.assert_not_awaited()


@pytest.mark.anyio
async def test_apply_chat_options_selects_requested_stepfun_mode():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://chat.stepfun.com/chats/new")
    fast = FakeLocator()
    deep_thinking = FakeLocator(disabled=False)
    page._locators['button:has-text("快速")'] = fast

    await client._apply_chat_options(page, web_mode="fast", deep_thinking=True)

    assert fast.clicked is True
    assert deep_thinking.clicked is False


@pytest.mark.anyio
async def test_apply_chat_options_skips_already_selected_controls():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://chat.stepfun.com/chats/new")
    fast = FakeLocator()
    fast.get_attribute = AsyncMock(side_effect=lambda name: "true" if name == "aria-pressed" else None)
    deep_thinking = FakeLocator()
    deep_thinking.get_attribute = AsyncMock(side_effect=lambda name: "true" if name == "aria-pressed" else None)
    page._locators['button:has-text("快速")'] = fast
    page._locators['[tabindex="0"]:has-text("深度思考")'] = deep_thinking

    await client._apply_chat_options(page, web_mode="fast", deep_thinking=True)

    assert fast.clicked is False
    assert deep_thinking.clicked is False


@pytest.mark.anyio
async def test_apply_chat_options_does_not_fail_when_mode_button_missing():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://chat.stepfun.com/chats/new")

    await client._apply_chat_options(page, web_mode="fast", deep_thinking=False)


def test_default_answer_selector_targets_final_markdown_not_user_message():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")

    assert 'data-message-id="markdown"' in client.answer_selector
    assert "message-markdown_markdown" in client.answer_selector
    assert "reason-render-ext" in client.answer_selector


@pytest.mark.anyio
async def test_send_message_retries_click_with_force_when_intercepted():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = MagicMock()
    page.evaluate = AsyncMock()
    input_box = FakeLocator()
    input_box.click = AsyncMock(side_effect=[TimeoutError("intercepted"), None])
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()
    client._input_text = AsyncMock(return_value="")

    await client._send_message(page, input_box, "hello")

    assert input_box.click.await_args_list[0].kwargs == {"timeout": 5_000}
    assert input_box.click.await_args_list[1].kwargs == {"timeout": 5_000, "force": True}
    input_box.fill.assert_any_await("")
    input_box.fill.assert_any_await("hello")
    page.evaluate.assert_awaited()


@pytest.mark.anyio
async def test_send_message_clicks_send_button_before_enter():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = FakePage()
    page.evaluate = AsyncMock()
    send_button = FakeLocator()
    page._locators["button:has(.custom-icon-send-outline):not([disabled])"] = send_button
    input_box = FakeLocator()
    input_box.click = AsyncMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()

    await client._send_message(page, input_box, "hello")

    assert send_button.clicked is True
    input_box.press.assert_not_awaited()


@pytest.mark.anyio
async def test_click_send_button_accepts_generic_send_icon_class():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = FakePage()
    send_button = FakeLocator()
    page._locators['button:has([class*="send"]):not([disabled])'] = send_button

    assert await client._click_send_button(page) is True
    assert send_button.clicked is True


@pytest.mark.anyio
async def test_click_send_button_falls_back_to_dom_click():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = FakePage()
    page.evaluate = AsyncMock(return_value=True)

    assert await client._click_send_button(page) is True
    page.evaluate.assert_awaited_once()


@pytest.mark.anyio
async def test_wait_until_answer_stable_refreshes_idle_when_text_grows():
    client = BrowserStepFunClient(
        user_data_dir="/tmp/test-profile",
        timeout_seconds=30,
        idle_timeout_seconds=0.4,
    )
    page = FakePage()
    client._is_generation_active = AsyncMock(return_value=False)
    texts = ["a" * 50, "a" * 60, "a" * 70, "a" * 70, "a" * 70, "a" * 70, "a" * 70, "a" * 70]
    client._resolve_answer_text = AsyncMock(side_effect=texts)

    answer = await client._wait_until_answer_stable(page, MagicMock())
    assert answer == "a" * 70


@pytest.mark.anyio
async def test_wait_until_answer_stable_idle_timeout_without_progress():
    client = BrowserStepFunClient(
        user_data_dir="/tmp/test-profile",
        timeout_seconds=5,
        idle_timeout_seconds=0.3,
    )
    page = FakePage()
    client._is_generation_active = AsyncMock(return_value=False)
    client._resolve_answer_text = AsyncMock(return_value="")
    client._dump_answer_miss = AsyncMock()

    with pytest.raises(TimeoutError, match="idle="):
        await client._wait_until_answer_stable(page, MagicMock())


@pytest.mark.anyio
async def test_wait_until_answer_stable_treats_generation_as_progress():
    client = BrowserStepFunClient(
        user_data_dir="/tmp/test-profile",
        timeout_seconds=5,
        idle_timeout_seconds=0.35,
    )
    page = FakePage()
    # Stay generating for a bit (refreshes idle), then emit stable text.
    gen_flags = [True, True, True, True, False, False, False, False, False, False, False]
    texts = ["", "", "", "", "hello" * 10, "hello" * 10, "hello" * 10, "hello" * 10, "hello" * 10, "hello" * 10, "hello" * 10]
    client._is_generation_active = AsyncMock(side_effect=gen_flags)
    client._resolve_answer_text = AsyncMock(side_effect=texts)

    answer = await client._wait_until_answer_stable(page, MagicMock())
    assert answer == "hello" * 10


def test_looks_like_echoed_prompt_detects_user_bubble():
    prompt = "【分析对象】\n主题「幸福的勇气」" + ("x" * 200)
    assert BrowserStepFunClient._looks_like_echoed_prompt(prompt, prompt) is True
    assert BrowserStepFunClient._looks_like_echoed_prompt('{"title_pattern":"q"}', prompt) is False


@pytest.mark.anyio
async def test_resolve_answer_text_falls_back_when_primary_empty():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile")
    page = FakePage()
    primary = FakeAnswerBlocks([])
    fallback_text = '{"title_pattern":"问句","section_blueprint":[]}'
    client._answer_text_from_selectors = AsyncMock(return_value=fallback_text)
    client._scrape_page_answer = AsyncMock(return_value="")

    text = await client._resolve_answer_text(page, primary, prompt="user prompt here")
    assert text == fallback_text
    client._answer_text_from_selectors.assert_awaited()


@pytest.mark.anyio
async def test_chat_completion_salvages_answer_before_retry():
    client = BrowserStepFunClient(user_data_dir="/tmp/test-profile", max_retries=2)
    page = FakePage()
    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._apply_chat_options = AsyncMock()
    client._find_input_box = AsyncMock(return_value=FakeLocator())
    client._send_message = AsyncMock()
    client._wait_for_new_answer = AsyncMock(side_effect=TimeoutError("start timeout"))
    client._dump_answer_miss = AsyncMock()
    salvaged = '{"title_pattern":"问句型","opening_pattern":"痛点"}'
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._resolve_answer_text = AsyncMock(side_effect=["", salvaged])

    result = await client.chat_completion(
        {"messages": [{"role": "user", "content": "hello"}]},
        new_chat=True,
        web_mode="deep_research",
    )
    assert result == salvaged
    client._start_new_conversation.assert_awaited()  # initial new chat
    # Should not start a second conversation for retry after salvage
    assert client._start_new_conversation.await_count == 1


def test_from_env_idle_timeout_defaults(monkeypatch):
    monkeypatch.delenv("STEPFUN_BROWSER_IDLE_TIMEOUT", raising=False)
    monkeypatch.delenv("STEPFUN_BROWSER_TIMEOUT", raising=False)
    monkeypatch.setenv("STEPFUN_BROWSER_HEADLESS", "1")
    client = BrowserStepFunClient.from_env()
    assert client.idle_timeout_seconds == 120.0
    assert client.timeout_seconds == 1800.0
    assert client.max_retries == 2
