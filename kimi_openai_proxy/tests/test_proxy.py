import sys
import json
import logging
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    compose_browser_prompt,
    create_app,
    extract_session_id,
    resolve_deep_thinking,
    resolve_new_chat,
    resolve_reasoning_effort,
    resolve_web_mode,
)
import app as app_module


class MockUpstream:
    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return await self.handler(method, url, **kwargs)

    async def aclose(self):
        pass


def auth_headers():
    return {"Authorization": "Bearer local-secret"}


class FakeBrowserBackend:
    def __init__(self, *, new_chat_per_request: bool = True):
        self.payloads = []
        self.new_chat_flags = []
        self.web_modes = []
        self.deep_thinking_flags = []
        self.reasoning_efforts = []
        self.new_chat_per_request = new_chat_per_request

    async def chat_completion(
        self,
        payload,
        *,
        new_chat=None,
        session_id=None,
        web_mode=None,
        deep_thinking=None,
        reasoning_effort=None,
    ):
        self.payloads.append(payload)
        self.new_chat_flags.append(new_chat)
        self.web_modes.append(web_mode)
        self.deep_thinking_flags.append(deep_thinking)
        self.reasoning_efforts.append(reasoning_effort)
        return "browser pong"

    async def aclose(self):
        pass


@pytest.mark.anyio
async def test_models_requires_local_key():
    app = create_app(local_api_key="local-secret", kimi_api_key="upstream-secret", backend_mode="official")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models")

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


@pytest.mark.anyio
async def test_models_returns_openai_shape():
    app = create_app(local_api_key="local-secret", kimi_api_key="upstream-secret", backend_mode="official")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models", headers=auth_headers())

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {"id": "kimi-k3", "object": "model", "owned_by": "moonshot"},
            {"id": "kimi-k2.7-code", "object": "model", "owned_by": "moonshot"},
            {"id": "kimi-k2.7-code-highspeed", "object": "model", "owned_by": "moonshot"},
            {"id": "kimi-k2.6", "object": "model", "owned_by": "moonshot"},
            {"id": "kimi-k2.5", "object": "model", "owned_by": "moonshot"},
            {"id": "moonshot-v1-8k", "object": "model", "owned_by": "moonshot"},
            {"id": "moonshot-v1-32k", "object": "model", "owned_by": "moonshot"},
            {"id": "moonshot-v1-128k", "object": "model", "owned_by": "moonshot"},
        ],
    }


@pytest.mark.anyio
async def test_chat_completion_forwards_openai_payload_to_kimi():
    async def handler(method, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "kimi-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    upstream = MockUpstream(handler)
    app = create_app(
        local_api_key="local-secret",
        kimi_api_key="upstream-secret",
        backend_mode="official",
        upstream_client=upstream,
    )
    transport = httpx.ASGITransport(app=app)
    body = {
        "model": "kimi-chat",
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", headers=auth_headers(), json=body)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "pong"
    method, url, kwargs = upstream.requests[0]
    assert method == "POST"
    assert url == "https://api.moonshot.cn/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer upstream-secret"
    assert kwargs["json"] == body


@pytest.mark.anyio
async def test_chat_completion_streams_sse_without_buffering_json():
    async def handler(method, url, **kwargs):
        assert kwargs["json"]["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"pong"}}]}\n\ndata: [DONE]\n\n',
        )

    upstream = MockUpstream(handler)
    app = create_app(
        local_api_key="local-secret",
        kimi_api_key="upstream-secret",
        backend_mode="official",
        upstream_client=upstream,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "kimi-chat",
                "messages": [{"role": "user", "content": "ping"}],
                "stream": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"choices"' in response.text
    assert "data: [DONE]" in response.text


@pytest.mark.anyio
async def test_browser_backend_uses_logged_in_browser_without_kimi_api_key():
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "kimi-chat-web",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "kimi-chat-web"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "browser pong"}
    assert browser_backend.payloads[0]["messages"] == [{"role": "user", "content": "ping"}]
    assert browser_backend.new_chat_flags == [True]


@pytest.mark.anyio
async def test_browser_backend_new_chat_request_body_overrides_default():
    browser_backend = FakeBrowserBackend(new_chat_per_request=True)
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "kimi-chat-web",
                "new_chat": False,
                "messages": [{"role": "user", "content": "follow up"}],
            },
        )

    assert response.status_code == 200
    assert browser_backend.new_chat_flags == [False]


@pytest.mark.anyio
async def test_browser_backend_new_chat_header_overrides_default():
    browser_backend = FakeBrowserBackend(new_chat_per_request=False)
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {**auth_headers(), "X-Kimi-New-Chat": "true"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "kimi-chat-web",
                "messages": [{"role": "user", "content": "fresh"}],
            },
        )

    assert response.status_code == 200
    assert browser_backend.new_chat_flags == [True]


def test_resolve_new_chat_priority():
    assert resolve_new_chat({"new_chat": False}, default=True) is False
    assert resolve_new_chat({"metadata": {"new_chat": True}}, default=False) is True
    assert resolve_new_chat({}, header="false", default=True) is False
    assert resolve_new_chat({}, default=True) is True


def test_resolve_web_mode_priority_and_aliases():
    assert resolve_web_mode({"kimi_mode": "fast"}, default="fast") == "fast"
    assert resolve_web_mode({"metadata": {"kimi_mode": "K2.6 思考"}}, default="fast") == "thinking"
    assert resolve_web_mode({}, header="quick", default="fast") == "fast"
    assert resolve_web_mode({}, header="agent", default="fast") == "k3"
    assert resolve_web_mode({}, header="K3 集群", default="fast") == "k3_cluster"
    assert resolve_web_mode({}, header="k3_extreme", default="fast") == "k3_extreme"
    assert resolve_web_mode({}, default="fast") == "fast"


def test_resolve_reasoning_effort_priority_and_aliases():
    assert resolve_reasoning_effort({"reasoning_effort": "max"}) == "extreme"
    assert resolve_reasoning_effort({"metadata": {"kimi_effort": "进阶"}}) == "advanced"
    assert resolve_reasoning_effort({}, header="low") == "standard"
    assert resolve_reasoning_effort({}) is None


def test_resolve_deep_thinking_priority():
    assert resolve_deep_thinking({"deep_thinking": True}, default=False) is True
    assert resolve_deep_thinking({"metadata": {"deep_thinking": False}}, default=True) is False
    assert resolve_deep_thinking({}, header="on", default=False) is True
    assert resolve_deep_thinking({}, default=False) is False


@pytest.mark.anyio
async def test_browser_backend_receives_mode_and_deep_thinking_controls():
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "kimi-chat-web",
                "kimi_mode": "k3",
                "deep_thinking": True,
                "reasoning_effort": "max",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )

    assert response.status_code == 200
    assert browser_backend.web_modes == ["k3"]
    assert browser_backend.deep_thinking_flags == [True]
    assert browser_backend.reasoning_efforts == ["extreme"]


def test_extract_session_id():
    assert extract_session_id({"session_id": "task-1"}) == "task-1"
    assert extract_session_id({"metadata": {"session_id": "task-2"}}) == "task-2"
    assert extract_session_id({}) is None


def test_compose_browser_prompt_preserves_roles_and_multimodal_text():
    prompt = compose_browser_prompt(
        [
            {"role": "system", "content": "Answer briefly."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            },
        ]
    )

    assert "System: Answer briefly." in prompt
    assert "User: hello" in prompt
    assert "[image_url omitted: https://example.com/a.png]" in prompt


def test_compose_browser_prompt_reuse_session_keeps_latest_user_only():
    prompt = compose_browser_prompt(
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ],
        reuse_session=True,
    )

    assert "System: Be concise." in prompt
    assert "User: follow up" in prompt
    assert "first question" not in prompt
    assert "first answer" not in prompt


@pytest.mark.anyio
async def test_debug_routes_lists_chat_completion_route():
    app = create_app(local_api_key="local-secret", backend_mode="browser", browser_backend=FakeBrowserBackend())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__debug/routes", headers=auth_headers())

    assert response.status_code == 200
    paths = {route["path"] for route in response.json()["routes"]}
    assert "/v1/chat/completions" in paths


@pytest.mark.anyio
async def test_chat_completion_logs_request_and_response(caplog):
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    with caplog.at_level(logging.INFO, logger="kimi_openai_proxy"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers=auth_headers(),
                json={
                    "model": "kimi-chat-web",
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )

    assert response.status_code == 200
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "request.start" in log_text
    assert "chat.request" in log_text
    assert "chat.response" in log_text
    assert "ping" in log_text


def test_truncate_shortens_long_strings(monkeypatch):
    monkeypatch.setattr(app_module, "_LOG_MAX_CHARS", 80)
    long_text = "HEAD" + ("a" * 100) + "TAIL"
    result = app_module._truncate(long_text)
    assert result.startswith("HEAD")
    assert result.endswith("TAIL")
    assert "省略中间" in result
    assert len(result) <= 80


def test_json_for_log_truncates_large_payload(monkeypatch):
    monkeypatch.setattr(app_module, "_LOG_MAX_CHARS", 80)
    payload = {"messages": [{"role": "user", "content": "x" * 200}]}
    full = app_module._json_for_log(payload)
    assert "省略中间" in full
    assert len(full) < len(json.dumps(payload))
    assert len(full) <= 80
