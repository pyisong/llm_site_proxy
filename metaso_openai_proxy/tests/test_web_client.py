import asyncio
import json
import time
from typing import Any

import httpx
import pytest

from models_map import SearchProfile
from web_client import (
    MetasoWebClient,
    aggregate_chat_events,
    extract_meta_token,
    format_answer_with_citations,
    parse_search_sse_lines,
)


def test_parse_text_and_done():
    events = parse_search_sse_lines(
        [
            'data: {"type":"append-text","text":"你好[1]"}',
            "data: [DONE]",
        ]
    )
    assert events[0] == {"type": "text", "text": "你好"}
    assert events[-1]["type"] == "done"


def test_parse_citation_like_event():
    events = parse_search_sse_lines(
        ['data: {"type":"source","title":"T","link":"https://a.example","snippet":"s"}']
    )
    assert events[0]["type"] == "citation"
    assert events[0]["link"] == "https://a.example"


def test_format_citations_appendix():
    body = format_answer_with_citations(
        "答案", [{"title": "T", "link": "https://a.example"}]
    )
    assert "参考来源" in body and "https://a.example" in body


def test_extract_meta_token():
    html = '<html><meta id="meta-token" content="abc123"></html>'
    assert extract_meta_token(html) == "abc123"


def test_parse_chat_delta_and_rate_limit_code():
    from web_client import parse_chat_sse_lines

    events = parse_chat_sse_lines(
        [
            'data: {"type":"conversation_init","data":{"id":"c1"}}',
            'data: {"type":"response_message_init","data":{"id":"leaf-9"}}',
            'data: {"choices":[{"delta":{"content":"你好"}}]}',
            'data: {"type":"error","code":429,"msg":"Too Many Requests"}',
            "data: [DONE]",
        ]
    )
    assert events[0] == {"type": "session", "session_id": "c1"}
    assert events[1] == {"type": "response_message", "message_id": "leaf-9"}
    assert events[2] == {"type": "text", "text": "你好"}
    assert events[3]["type"] == "error" and events[3]["code"] == "TOO_MANY_REQUESTS"
    assert events[-1]["type"] == "done"


def test_strip_ai_generated_tag():
    from web_client import aggregate_chat_events

    result = aggregate_chat_events(
        [
            {"type": "text", "text": "答案<span style='font-size:12px'>[AI生成]</span>"},
            {"type": "done"},
        ]
    )
    assert result["raw_content"] == "答案"
    assert "[AI生成]" not in result["content"]


def test_aggregate_chat_events():
    result = aggregate_chat_events(
        [
            {"type": "text", "text": "A"},
            {"type": "citation", "title": "T", "link": "https://x", "snippet": ""},
            {"type": "text", "text": "B"},
            {"type": "done"},
        ]
    )
    assert result["raw_content"] == "AB"
    assert "参考来源" in result["content"]
    assert result["citations"][0]["link"] == "https://x"


@pytest.mark.anyio
async def test_chat_api_reuses_mapped_session():
    """同 client session_id：首轮建映射；次轮带 conversationId + parentId（线性追问，非同轮再生）。"""
    html = '<meta id="meta-token" content="tok">'
    seen: list[dict[str, Any]] = []
    call_n = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.rstrip("/") == "https://metaso.cn" or url.startswith("https://metaso.cn/?"):
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        if "/api/my-info" in url:
            return httpx.Response(200, json={"ok": True})
        if url.endswith("/api/search/chat"):
            body = json.loads(request.content.decode())
            msg = body["messages"][0]
            seen.append(
                {
                    "msg_conv": msg["conversationId"],
                    "body_conv": body.get("conversationId"),
                    "body_id": body.get("id"),
                    "parent_id": msg.get("parentId"),
                    "current_leaf": body.get("currentLeafId"),
                    "mode": body.get("mode"),
                    "msg_mode": msg.get("mode"),
                }
            )
            call_n["n"] += 1
            leaf = f"leaf-{call_n['n']}"
            # 续聊时上游若仍误发新 conversation_init，客户端应忽略
            init_id = "real-c1" if body.get("conversationId") is None else "SHOULD-IGNORE-NEW"
            sse = (
                f'data: {{"type":"conversation_init","data":{{"id":"{init_id}"}}}}\n\n'
                f'data: {{"type":"response_message_init","data":{{"id":"{leaf}"}}}}\n\n'
                'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(
                200,
                text=sse,
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404, text=f"unexpected {url}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        web = MetasoWebClient(cookie_header="uid=u; sid=s", client=client)
        web._min_interval = 0
        await web.ensure_ready()
        profile = SearchProfile("chat", "webpage")
        r1 = await web.chat("q1", profile, session_id="task-42", new_chat=True)
        r2 = await web.chat("q2", profile, session_id="task-42", new_chat=False)
        assert r1["session_id"] == "real-c1"
        assert r2["session_id"] == "real-c1"
        assert seen[0]["msg_conv"].startswith("temp-")
        assert seen[0]["body_conv"] is None
        assert seen[0]["parent_id"] is None
        assert seen[0]["mode"] == "chat" and seen[0]["msg_mode"] == "chat"
        assert seen[1]["msg_conv"] == "real-c1"
        assert seen[1]["body_conv"] == "real-c1"
        assert seen[1]["body_id"] == "real-c1"
        assert seen[1]["parent_id"] == "leaf-1"
        assert seen[1]["current_leaf"] == "leaf-1"
        assert seen[1]["mode"] == "chat"
        assert web._session_aliases["task-42"] == "real-c1"
        assert web._session_leaves["task-42"] == "leaf-2"


@pytest.mark.anyio
async def test_chat_with_mock_transport():
    html = '<meta id="meta-token" content="tok">'
    sse = (
        'data: {"type":"append-text","text":"hello"}\n\n'
        'data: {"type":"source","title":"Doc","link":"https://doc.example"}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.rstrip("/") == "https://metaso.cn" or url.startswith("https://metaso.cn/?"):
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        if "/api/my-info" in url:
            return httpx.Response(200, json={"ok": True})
        if url.endswith("/api/session"):
            return httpx.Response(200, json={"data": {"id": "sess-1"}})
        if "/api/searchV2" in url:
            return httpx.Response(
                200,
                text=sse,
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404, text=f"unexpected {url}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        web = MetasoWebClient(cookie_header="uid=u; sid=s", client=client)
        web._min_interval = 0
        await web.ensure_ready()
        result = await web.chat("q", SearchProfile("detail", "webpage"))
        assert "hello" in result["raw_content"]
        assert result["session_id"] == "sess-1"
        assert any(c["link"] == "https://doc.example" for c in result["citations"])


@pytest.mark.anyio
async def test_throttle_spaces_requests(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    web = MetasoWebClient(cookie_header="uid=u; sid=s", client=httpx.AsyncClient())
    web._min_interval = 8.0
    web._last_request_at = time.monotonic()
    async with web._request_slot():
        pass
    assert sleeps and sleeps[0] > 7.5
    await web.aclose()


@pytest.mark.anyio
async def test_request_slot_serializes_overlap():
    """整段占用期间第二请求必须等待，结束后再间隔。"""
    web = MetasoWebClient(cookie_header="uid=u; sid=s", client=httpx.AsyncClient())
    web._min_interval = 0.05
    order: list[str] = []

    async def job(name: str) -> None:
        async with web._request_slot():
            order.append(f"{name}:start")
            await asyncio.sleep(0.08)
            order.append(f"{name}:end")

    await asyncio.gather(job("a"), job("b"))
    # 不允许并行 start
    assert order in (
        ["a:start", "a:end", "b:start", "b:end"],
        ["b:start", "b:end", "a:start", "a:end"],
    )
    await web.aclose()


@pytest.mark.anyio
async def test_circuit_breaker_blocks_after_rate_limit():
    from web_client import MetasoRateLimitError

    web = MetasoWebClient(cookie_header="uid=u; sid=s", client=httpx.AsyncClient())
    web._min_interval = 0
    web._cooldown_sec = 120.0
    web._trip_rate_limit(reason="test")
    with pytest.raises(MetasoRateLimitError, match="冷却中"):
        async with web._request_slot():
            pass
    await web.aclose()
