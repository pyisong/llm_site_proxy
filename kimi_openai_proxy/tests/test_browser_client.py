import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_client import (
    BrowserKimiClient,
    KimiBusyError,
    _default_headless,
    _default_storage_state_file,
    extract_balanced_json,
    finalize_answer_text,
    is_kimi_busy_answer,
    looks_like_incomplete_answer,
    looks_like_thinking_narration,
)


class FakeLocator:
    def __init__(self, count: int = 1, visible: bool = True, disabled: bool = False, text: str = ""):
        self._count = count
        self._visible = visible
        self._disabled = disabled
        self._text = text
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
            return "true" if self._disabled else "false"
        return None

    async def inner_text(self) -> str:
        return self._text

    async def click(self, timeout: int = 0) -> None:
        self.clicked = True


class FakePage:
    def __init__(self, *, url: str = "https://www.kimi.com/a/chat/s/old-session"):
        self.url = url
        self.goto = AsyncMock()
        self._locators: dict[str, FakeLocator] = {}
        self._popover_open = False
        self._popover_locators: dict[str, FakeLocator] = {}

    def locator(self, selector: str):
        if "," in selector:
            parts = [part.strip() for part in selector.split(",") if part.strip()]

            class _AnyLocator:
                def __init__(self, outer, parts):
                    self._outer = outer
                    self._parts = parts

                def _first_hit(self):
                    for part in self._parts:
                        loc = self._outer.locator(part)
                        # unwrap nested FakeLocator-like
                        return loc
                    return FakeLocator(count=0)

                @property
                def first(self):
                    return self

                async def count(self):
                    total = 0
                    for part in self._parts:
                        total += await self._outer.locator(part).count()
                    return total

                async def click(self, timeout: int = 0):
                    for part in self._parts:
                        loc = self._outer.locator(part)
                        if await loc.count() > 0:
                            await loc.first.click(timeout=timeout)
                            return

            return _AnyLocator(self, parts)

        if selector == ".current-model":
            base = self._locators.get(selector, FakeLocator(count=0))

            class _Opener:
                def __init__(self, outer, inner):
                    self._outer = outer
                    self._inner = inner

                @property
                def first(self):
                    return self

                @property
                def clicked(self):
                    return self._inner.clicked

                async def count(self):
                    return await self._inner.count()

                async def click(self, timeout: int = 0):
                    await self._inner.click(timeout=timeout)
                    self._outer._popover_open = True

            return _Opener(self, base)

        if any(
            key in selector
            for key in (
                ".models-popover",
                ".n-popover",
                ".effort-popover",
                ".effort-container",
                ".effort-item",
                ".effort-option",
            )
        ):
            if not self._popover_open:
                return FakeLocator(count=0)
            return self._popover_locators.get(selector) or self._locators.get(selector, FakeLocator(count=0))
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


def test_default_storage_state_file_auto_discovers_project_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KIMI_STORAGE_STATE_FILE", raising=False)
    secret = tmp_path / "secrets" / "kimi_storage.json"
    secret.parent.mkdir()
    secret.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    assert _default_storage_state_file() == "secrets/kimi_storage.json"


def test_default_storage_state_file_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("KIMI_STORAGE_STATE_FILE", "/tmp/custom-kimi-storage.json")

    assert _default_storage_state_file() == "/tmp/custom-kimi-storage.json"


def test_default_headless_uses_storage_state_when_env_missing(monkeypatch):
    monkeypatch.delenv("KIMI_BROWSER_HEADLESS", raising=False)

    assert _default_headless("secrets/kimi_storage.json") is True
    assert _default_headless(None) is False


def test_default_headless_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("KIMI_BROWSER_HEADLESS", "0")
    assert _default_headless("secrets/kimi_storage.json") is False

    monkeypatch.setenv("KIMI_BROWSER_HEADLESS", "1")
    assert _default_headless(None) is True


@pytest.mark.anyio
async def test_resolve_storage_state_accepts_cookie_only_state(tmp_path):
    path = tmp_path / "kimi_storage.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "kimi-auth",
                        "value": "token",
                        "domain": ".kimi.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    client = BrowserKimiClient(
        user_data_dir="/tmp/test-profile",
        storage_state_file=str(path),
    )

    state = await client._resolve_storage_state()

    assert state is not None
    assert state["cookies"][0]["name"] == "kimi-auth"


@pytest.mark.anyio
async def test_last_answer_snapshot_uses_last_non_empty_candidate():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    blocks = FakeAnswerBlocks(["", "old", "OK", ""])

    count, text = await client._last_answer_snapshot(blocks)

    assert count == 4
    assert text == "OK"


def test_extract_balanced_json_and_thinking_detection():
    thinking = (
        '用户要求我作为"公众号内容结构分析师"，基于提供的《幸福的勇气》研究底稿，'
        "分析其写作结构并输出一个JSON模板。但用户没有提供具体的\"范文正文\"，只有研究底稿。\n"
        "不过，用户提供了非常详细的研究底稿，包括："
    )
    assert looks_like_thinking_narration(thinking)
    assert looks_like_incomplete_answer(thinking)
    assert extract_balanced_json(thinking) is None

    mixed = thinking + '\n{"title_hook":"x","sections":[]}'
    assert extract_balanced_json(mixed) == '{"title_hook":"x","sections":[]}'
    assert looks_like_incomplete_answer(mixed) is False
    assert finalize_answer_text(mixed) == '{"title_hook":"x","sections":[]}'

    incomplete_json = '{"a": 1'
    assert looks_like_incomplete_answer(incomplete_json)


@pytest.mark.anyio
async def test_pick_final_answer_prefers_json_over_thinking():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    thinking = '用户要求我作为"分析师"。研究底稿包括：'
    final = '{"ok": true, "items": [1]}'
    blocks = FakeAnswerBlocks([thinking, final])

    text = await client._extract_final_answer(blocks)

    assert text == final


@pytest.mark.anyio
async def test_wait_until_answer_stable_waits_out_thinking_then_returns_json(monkeypatch):
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile", timeout_seconds=30)
    thinking = '用户要求我作为"分析师"。研究底稿包括：'
    final = '{"ok": true}'
    blocks = FakeAnswerBlocks([thinking])
    page = MagicMock()
    gen_calls = {"n": 0}
    extract_calls = {"n": 0}

    async def fake_generation(_page=None):
        gen_calls["n"] += 1
        return gen_calls["n"] <= 2

    async def fake_extract(_blocks=None):
        extract_calls["n"] += 1
        return thinking if extract_calls["n"] <= 2 else final

    client._is_generation_active = fake_generation
    client._extract_final_answer = fake_extract
    monkeypatch.setattr("browser_client.asyncio.sleep", AsyncMock())

    result = await client._wait_until_answer_stable(page, blocks)

    assert result == final
    assert extract_calls["n"] >= 5


@pytest.mark.anyio
async def test_wait_until_answer_stable_times_out_on_thinking_only(monkeypatch):
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile", timeout_seconds=0.01)
    thinking = '用户要求我作为"分析师"。研究底稿包括：'
    page = MagicMock()
    client._is_generation_active = AsyncMock(return_value=False)
    client._extract_final_answer = AsyncMock(return_value=thinking)
    monkeypatch.setattr("browser_client.asyncio.sleep", AsyncMock())

    with pytest.raises(TimeoutError, match="final answer"):
        await client._wait_until_answer_stable(page, FakeAnswerBlocks([thinking]))


@pytest.mark.anyio
async def test_send_message_clicks_send_button_before_enter():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://www.kimi.com/")
    send_button = FakeLocator()
    page._locators[".send-button-container:not(.disabled)"] = send_button
    input_box = MagicMock()
    input_box.click = AsyncMock()
    input_box.fill = AsyncMock()
    input_box.press = AsyncMock()

    await client._send_message(page, input_box, "hi")

    assert send_button.clicked is True
    input_box.press.assert_not_awaited()


@pytest.mark.anyio
async def test_start_new_conversation_clicks_new_chat_button():
    client = BrowserKimiClient(
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
    client = BrowserKimiClient(
        user_data_dir="/tmp/test-profile",
        chat_url="https://www.kimi.com/",
    )
    page = FakePage(url="https://www.kimi.com/a/chat/s/old-session")

    client._click_new_chat_button = AsyncMock(return_value=False)
    client._wait_for_chat_ready = AsyncMock()

    await client._start_new_conversation(page)

    page.goto.assert_awaited_once_with(
        "https://www.kimi.com/",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    client._wait_for_chat_ready.assert_awaited_once_with(page)


@pytest.mark.anyio
async def test_chat_completion_starts_new_conversation_when_enabled():
    client = BrowserKimiClient(
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
    client = BrowserKimiClient(
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
    client = BrowserKimiClient(
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
    client = BrowserKimiClient(
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
async def test_apply_chat_options_selects_requested_kimi_mode():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://www.kimi.com/")
    current_name = FakeLocator(text="快速")
    current_effort = FakeLocator(text="标准")
    opener = FakeLocator()
    k3 = FakeLocator()
    page._locators[".current-model .name"] = current_name
    page._locators[".current-model .current-effort"] = current_effort
    page._locators[".current-model"] = opener
    page._popover_locators[".models-popover .model-item"] = FakeLocator(count=3)
    page._popover_locators['.models-popover .model-item:has(.name:text-is("K3"))'] = k3
    page._popover_locators[".models-popover .effort-item"] = FakeLocator()
    page._popover_locators['.effort-popover .effort-option:has(.effort-name:text-is("标准"))'] = FakeLocator()

    await client._apply_chat_options(page, web_mode="k3", deep_thinking=False)

    assert opener.clicked is True
    assert k3.clicked is True


@pytest.mark.anyio
async def test_apply_chat_options_skips_already_selected_controls():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://www.kimi.com/")
    current_name = FakeLocator(text="快速")
    current_effort = FakeLocator(text="标准")
    opener = FakeLocator()
    page._locators[".current-model .name"] = current_name
    page._locators[".current-model .current-effort"] = current_effort
    page._locators[".current-model"] = opener

    await client._apply_chat_options(page, web_mode="fast", deep_thinking=False)

    assert opener.clicked is False


@pytest.mark.anyio
async def test_apply_chat_options_maps_deep_thinking_to_advanced_effort():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://www.kimi.com/")
    current_name = FakeLocator(text="快速")
    current_effort = FakeLocator(text="标准")
    opener = FakeLocator()
    advanced = FakeLocator()
    page._locators[".current-model .name"] = current_name
    page._locators[".current-model .current-effort"] = current_effort
    page._locators[".current-model"] = opener
    page._popover_locators[".models-popover .model-item"] = FakeLocator(count=3)
    page._popover_locators[".models-popover .effort-item"] = FakeLocator()
    page._popover_locators['.effort-popover .effort-option:has(.effort-name:text-is("进阶"))'] = advanced

    await client._apply_chat_options(page, web_mode="fast", deep_thinking=True)

    assert opener.clicked is True
    assert advanced.clicked is True


@pytest.mark.anyio
async def test_apply_chat_options_skips_missing_kimi_mode_controls():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://www.kimi.com/")

    await client._apply_chat_options(page, web_mode="thinking", deep_thinking=False)


def test_is_kimi_busy_answer_detects_soft_rate_limit():
    assert is_kimi_busy_answer(
        "不好意思，刚刚和Kimi聊的人太多了。Kimi有点累了，可以晚点再问我一遍。"
    )
    assert is_kimi_busy_answer("和Kimi聊天的人太多啦，订阅会员可进入独立的优先队列～")
    assert is_kimi_busy_answer("Sorry, too many people. Please try again later.")
    assert not is_kimi_busy_answer("hello")
    assert not is_kimi_busy_answer("有点累了" + ("x" * 250))


@pytest.mark.anyio
async def test_send_message_raises_busy_when_modal_blocks_click():
    client = BrowserKimiClient(user_data_dir="/tmp/test-profile")
    page = FakePage(url="https://www.kimi.com/")
    input_box = MagicMock()
    input_box.click = AsyncMock(
        side_effect=TimeoutError(
            'Locator.click: Timeout 5000ms exceeded.\n'
            '<div class="body">和Kimi聊天的人太多啦，订阅会员可进入独立的优先队列～</div>'
        )
    )
    client._dismiss_busy_modal = AsyncMock(return_value=False)
    client._scroll_chat_to_bottom = AsyncMock()
    client._busy_modal_text = AsyncMock(
        return_value="和Kimi聊天的人太多啦，订阅会员可进入独立的优先队列～"
    )

    with pytest.raises(KimiBusyError):
        await client._send_message(page, input_box, "hi")


@pytest.mark.anyio
async def test_chat_completion_retries_on_busy_modal_error(monkeypatch):
    client = BrowserKimiClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=True,
        busy_max_attempts=3,
        busy_retry_wait_seconds=2.0,
    )
    page = MagicMock()
    input_box = MagicMock()
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
    client._dismiss_busy_modal = AsyncMock(return_value=True)
    client._send_message = AsyncMock(
        side_effect=[
            KimiBusyError("和Kimi聊天的人太多啦，订阅会员可进入独立的优先队列～"),
            None,
        ]
    )
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="ok")
    client._apply_chat_options = AsyncMock()
    monkeypatch.setattr("browser_client.asyncio.sleep", AsyncMock())

    result = await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

    assert result == "ok"
    assert client._send_message.await_count == 2
    assert client._start_new_conversation.await_count == 1



@pytest.mark.anyio
async def test_chat_completion_retries_on_busy_answer(monkeypatch):
    client = BrowserKimiClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=True,
        busy_max_attempts=3,
        busy_retry_wait_seconds=1.0,
        busy_retry_backoff=2.0,
    )
    page = MagicMock()
    input_box = MagicMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)
    page.locator = MagicMock(
        side_effect=lambda selector: (
            answer_blocks if "assistant" in selector else MagicMock(first=input_box)
        )
    )
    busy = "不好意思，刚刚和Kimi聊的人太多了。Kimi有点累了，可以晚点再问我一遍。"

    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._wait_for_generation_idle = AsyncMock()
    client._send_message = AsyncMock()
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(side_effect=[busy, "ok"])
    client._apply_chat_options = AsyncMock()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("browser_client.asyncio.sleep", fake_sleep)

    result = await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

    assert result == "ok"
    assert sleeps == [1.0]
    assert client._start_new_conversation.await_count == 1
    assert client._send_message.await_count == 2


@pytest.mark.anyio
async def test_chat_completion_raises_after_busy_retries_exhausted(monkeypatch):
    client = BrowserKimiClient(
        user_data_dir="/tmp/test-profile",
        new_chat_per_request=True,
        busy_max_attempts=2,
        busy_retry_wait_seconds=5.0,
    )
    page = MagicMock()
    input_box = MagicMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)
    page.locator = MagicMock(
        side_effect=lambda selector: (
            answer_blocks if "assistant" in selector else MagicMock(first=input_box)
        )
    )
    busy = "不好意思，刚刚和Kimi聊的人太多了。Kimi有点累了，可以晚点再问我一遍。"

    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._send_message = AsyncMock()
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value=busy)
    client._apply_chat_options = AsyncMock()
    monkeypatch.setattr("browser_client.asyncio.sleep", AsyncMock())

    with pytest.raises(KimiBusyError):
        await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

    assert client._send_message.await_count == 2

