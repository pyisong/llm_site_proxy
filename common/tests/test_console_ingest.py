"""Tests for console_ingest helpers."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from typing import Any

from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from console_ingest import (  # noqa: E402
    build_io_meta,
    extract_model_from_body,
    extract_response_text,
    infer_mode,
    sanitize_for_ingest,
    schedule_request_ingest,
    should_ingest,
)


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("POST", "/v1/chat/completions", True),
        ("POST", "/v1/messages", True),
        ("POST", "/v1/images/generations", True),
        ("POST", "/v1/videos/generations", True),
        ("POST", "/tts", True),
        ("POST", "/v1/metaso/search", True),
        ("POST", "/v1/metaso/reader", True),
        ("POST", "/v1/metaso/chat", True),
        ("GET", "/v1/chat/completions", False),
        ("POST", "/health", False),
        ("POST", "/v1/models", False),
        ("GET", "/health", False),
    ],
)
def test_should_ingest(method: str, path: str, expected: bool) -> None:
    assert should_ingest(method, path) is expected


@pytest.mark.parametrize(
    "path,mode",
    [
        ("/v1/chat/completions", "chat"),
        ("/v1/messages", "chat"),
        ("/v1/images/generations", "image"),
        ("/v1/videos/generations", "video"),
        ("/tts", "tts"),
        ("/v1/tts", "tts"),
        ("/v1/metaso/search", "search"),
        ("/v1/metaso/reader", "reader"),
        ("/v1/metaso/chat", "chat"),
    ],
)
def test_infer_mode(path: str, mode: str) -> None:
    assert infer_mode(path) == mode


def test_extract_model_from_body() -> None:
    assert extract_model_from_body(b'{"model":"gpt-x","messages":[]}') == "gpt-x"
    assert extract_model_from_body(b"not-json") is None
    assert extract_model_from_body(None) is None


def test_sanitize_and_build_io_meta() -> None:
    req = {
        "model": "composer-2.5-fast",
        "messages": [{"role": "user", "content": "hello " * 5000}],
        "image": "a" * 1000,
    }
    resp = {
        "choices": [
            {"message": {"role": "assistant", "content": "世界和平"}},
        ]
    }
    meta = build_io_meta(
        request_obj=req,
        response_bytes=json.dumps(resp).encode("utf-8"),
        content_type="application/json",
    )
    assert meta["source"] == "proxy_live"
    assert "request" in meta
    assert "response" in meta
    assert meta["response_text"] == "世界和平"
    assert "<omitted" in str(meta["request"].get("image", ""))


def test_extract_response_text_sse() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    assert extract_response_text(sse, content_type="text/event-stream") == "你好"


def test_sanitize_for_ingest_depth() -> None:
    nested: Any = {"v": "x"}
    cur = nested
    for _ in range(20):
        cur["n"] = {"v": "x"}
        cur = cur["n"]
    out = sanitize_for_ingest(nested)
    assert out is not None


def test_schedule_request_ingest_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            received.append(json.loads(raw.decode("utf-8")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"id":"ok"}')

        def log_message(self, *_args) -> None:  # noqa: ANN002
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(
            "CONSOLE_INGEST_URL", f"http://127.0.0.1:{port}/api/ingest/request"
        )
        schedule_request_ingest(
            {
                "proxy_id": "cursor-openai-bridge",
                "mode": "chat",
                "path": "/v1/chat/completions",
                "status_code": 200,
                "latency_ms": 12.5,
                "model": "composer-2",
                "error": None,
                "meta": {"source": "proxy_live", "request": {"model": "composer-2"}},
            }
        )
        for _ in range(50):
            if received:
                break
            threading.Event().wait(0.05)
        assert received
        assert received[0]["proxy_id"] == "cursor-openai-bridge"
        assert received[0]["meta"]["request"]["model"] == "composer-2"
    finally:
        server.shutdown()
