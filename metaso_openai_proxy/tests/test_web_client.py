import json

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
        await web.ensure_ready()
        result = await web.chat("q", SearchProfile("detail", "webpage"))
        assert "hello" in result["raw_content"]
        assert result["session_id"] == "sess-1"
        assert any(c["link"] == "https://doc.example" for c in result["citations"])
