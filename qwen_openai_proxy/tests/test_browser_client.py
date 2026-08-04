import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import json

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_client import (
    GENERATION_ACTIVE_SELECTORS,
    BrowserQwenClient,
    extract_balanced_json,
    finalize_answer_text,
    strip_code_gutter_line_numbers,
    strip_markdown_code_fence,
)


@pytest.mark.anyio
async def test_send_message_fills_long_prompt_without_type():
    """Long prompts must use fill(), not type(delay=…), to avoid Playwright 30s timeout."""
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()
    input_box = MagicMock()
    input_box.click = AsyncMock()
    input_box.fill = AsyncMock()
    input_box.type = AsyncMock()
    input_box.press = AsyncMock()
    input_box.input_value = AsyncMock(return_value="")

    client._scroll_chat_to_bottom = AsyncMock()
    client._click_send_button = AsyncMock(return_value=True)

    long_prompt = "正文\n" * 2000
    await client._send_message(page, input_box, long_prompt)

    assert input_box.fill.await_count >= 1
    fill_calls = [call.args[0] for call in input_box.fill.await_args_list if call.args]
    assert long_prompt in fill_calls
    input_box.type.assert_not_awaited()


@pytest.mark.anyio
async def test_send_message_falls_back_to_send_button_when_enter_leaves_text():
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()
    input_box = MagicMock()
    input_box.click = AsyncMock()
    input_box.fill = AsyncMock()
    input_box.type = AsyncMock()
    input_box.press = AsyncMock()
    input_box.input_value = AsyncMock(return_value="still here")

    client._scroll_chat_to_bottom = AsyncMock()
    client._click_send_button = AsyncMock(return_value=True)

    await client._send_message(page, input_box, "hello")

    input_box.press.assert_awaited_once_with("Enter")
    client._click_send_button.assert_awaited_once_with(page)


@pytest.mark.anyio
async def test_select_response_mode_clicks_dropdown_option():
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()

    option = MagicMock()
    option.count = AsyncMock(return_value=1)
    option.is_visible = AsyncMock(return_value=True)
    option.click = AsyncMock()

    page.locator = MagicMock(return_value=MagicMock(first=option, count=AsyncMock(return_value=1)))
    client._current_response_mode_label = AsyncMock(side_effect=["自动", "思考"])
    client._open_response_mode_dropdown = AsyncMock(return_value=True)

    changed = await client._select_response_mode(page, "thinking")

    assert changed is True
    option.click.assert_awaited()


@pytest.mark.anyio
async def test_select_response_mode_skips_when_already_selected():
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()

    client._current_response_mode_label = AsyncMock(return_value="快速")
    client._open_response_mode_dropdown = AsyncMock()

    changed = await client._select_response_mode(page, "fast")

    assert changed is False
    client._open_response_mode_dropdown.assert_not_awaited()


@pytest.mark.anyio
async def test_apply_chat_options_skips_response_mode_for_image_mode():
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()

    client._apply_qwen_mode = AsyncMock()
    client._select_response_mode = AsyncMock()

    await client._apply_chat_options(page, qwen_mode="image", response_mode="thinking")

    client._apply_qwen_mode.assert_awaited_once_with(page, "image")
    client._select_response_mode.assert_not_awaited()


def test_strip_markdown_code_fence_and_bare_lang_tag():
    fenced = '```json\n{"ok": true}\n```'
    assert strip_markdown_code_fence(fenced) == '{"ok": true}'
    bare = 'json\n{"ok": true}'
    assert strip_markdown_code_fence(bare) == '{"ok": true}'


def test_strip_code_gutter_line_numbers():
    polluted = "\n".join(
        ["json", "48", "49", "50", "51", '  "output_format": "png"', "}", ""]
    )
    cleaned = strip_code_gutter_line_numbers(polluted)
    assert "48" not in cleaned.splitlines()
    assert '"output_format": "png"' in cleaned


def test_finalize_answer_text_recovers_json_from_gutter_pollution():
    payload = {"prompts": [{"id": 1, "label": "封面", "prompt_zh": "画面"}]}
    body = json.dumps(payload, ensure_ascii=False)
    polluted = "json\n" + "\n".join(str(i) for i in range(48, 69)) + "\n" + body
    assert finalize_answer_text(polluted) == body
    assert extract_balanced_json(finalize_answer_text(polluted)) == body


def test_finalize_answer_text_keeps_plain_markdown():
    text = "摘要：这是一篇关于终身成长的文章。\n\n## 标题"
    assert finalize_answer_text(text) == text


@pytest.mark.anyio
async def test_read_block_text_prefers_evaluate_result():
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    block = MagicMock()
    block.evaluate = AsyncMock(return_value='{"ok": true}')
    block.inner_text = AsyncMock(return_value="json\n1\n2\nfallback")

    text = await client._read_block_text(block)

    assert text == '{"ok": true}'
    block.inner_text.assert_not_awaited()


def test_generation_active_selectors_exclude_permanent_ui_chrome():
    """Broad class*=thinking/loading matches 自动 selector and page-loading forever."""
    banned = {'[class*="thinking"]', '[class*="loading"]', '[class*="stop"]'}
    assert banned.isdisjoint(set(GENERATION_ACTIVE_SELECTORS))


@pytest.mark.anyio
async def test_wait_until_answer_stable_returns_early_for_image_without_answer_text():
    """t2i replies use .qwen-image, not .response-message-content — must not spin to timeout."""
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)

    client._extract_media_from_last_answer = AsyncMock(
        return_value=(["https://cdn.qwenlm.ai/output/x/t2i/y.png?key=z"], [])
    )
    client._is_generation_active = AsyncMock(return_value=False)

    started = time.monotonic()
    text = await client._wait_until_answer_stable(page, answer_blocks, timeout_seconds=5.0)
    elapsed = time.monotonic() - started

    assert text == ""
    assert elapsed < 3.0


@pytest.mark.anyio
async def test_wait_for_new_answer_ignores_stale_generation_active():
    """残留「停止」按钮不能当成新回答已开始，否则会空等满 image timeout。"""
    client = BrowserQwenClient(
        user_data_dir="/tmp/test-qwen-profile",
        start_timeout_seconds=0.8,
    )
    page = MagicMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)
    client._extract_media_from_last_answer = AsyncMock(return_value=([], []))
    client._is_generation_active = AsyncMock(return_value=True)

    with pytest.raises(TimeoutError, match="start_timeout"):
        await client._wait_for_new_answer(page, answer_blocks, before_count=0, before_last_text="")


@pytest.mark.anyio
async def test_wait_for_new_answer_accepts_idle_to_active_edge():
    client = BrowserQwenClient(
        user_data_dir="/tmp/test-qwen-profile",
        start_timeout_seconds=3.0,
    )
    page = MagicMock()
    answer_blocks = MagicMock()
    answer_blocks.count = AsyncMock(return_value=0)
    client._extract_media_from_last_answer = AsyncMock(return_value=([], []))
    client._is_generation_active = AsyncMock(side_effect=[False, False, True])

    await client._wait_for_new_answer(page, answer_blocks, before_count=0, before_last_text="")


@pytest.mark.anyio
async def test_run_generation_stops_and_waits_idle_before_mode_on_new_chat():
    import asyncio

    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()
    input_loc = MagicMock()
    input_loc.first = input_loc
    answer_blocks = MagicMock()

    def locator(selector: str):
        if "message-input" in selector or selector == client.input_selector:
            return input_loc
        return answer_blocks

    page.locator = MagicMock(side_effect=locator)
    sequence: list[str] = []

    client._lock = asyncio.Lock()
    client._ensure_page = AsyncMock(return_value=page)
    client._start_new_conversation = AsyncMock()
    client._stop_generation_if_active = AsyncMock(side_effect=lambda *_a, **_k: sequence.append("stop") or False)
    client._wait_for_generation_idle = AsyncMock(side_effect=lambda *_a, **_k: sequence.append("idle"))
    client._apply_chat_options = AsyncMock(side_effect=lambda *_a, **_k: sequence.append("apply"))
    client._send_message = AsyncMock(side_effect=lambda *_a, **_k: sequence.append("send"))
    client._wait_for_new_answer = AsyncMock()
    client._wait_until_answer_stable = AsyncMock(return_value="")
    client._extract_media_from_last_answer = AsyncMock(return_value=(["https://cdn.example/a.png"], []))
    client._last_answer_snapshot = AsyncMock(return_value=(0, ""))

    await client._run_generation(prompt="画一只猫", qwen_mode="image", new_chat=True)

    assert "stop" in sequence and "idle" in sequence and "apply" in sequence
    assert sequence.index("stop") < sequence.index("apply")
    assert sequence.index("idle") < sequence.index("apply")


@pytest.mark.anyio
async def test_extract_media_prefers_qwen_image_widgets():
    client = BrowserQwenClient(user_data_dir="/tmp/test-qwen-profile")
    page = MagicMock()

    img = MagicMock()
    img.get_attribute = AsyncMock(
        return_value="https://cdn.qwenlm.ai/output/a/t2i/b.png?key=c"
    )
    images = MagicMock()
    images.count = AsyncMock(return_value=1)
    images.nth = MagicMock(return_value=img)

    def locator(selector: str):
        loc = MagicMock()
        if "qwen-image" in selector or "qwen-markdown-image" in selector:
            loc.count = AsyncMock(return_value=1)
            # chain: page.locator(sel) -> images locator used as block.locator("img") or direct img
            loc.locator = MagicMock(return_value=images)
            # when selector already targets img
            if selector.strip().startswith("img") or selector.endswith(" img"):
                return images
            return loc
        # assistant fallback unused
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=locator)

    image_urls, video_urls = await client._extract_media_from_last_answer(page)

    assert image_urls == ["https://cdn.qwenlm.ai/output/a/t2i/b.png?key=c"]
    assert video_urls == []
