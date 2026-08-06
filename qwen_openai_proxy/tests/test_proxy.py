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
    resolve_new_chat,
    resolve_qwen_mode,
    resolve_response_mode,
    resolve_thinking,
)
import app as app_module
from browser_client import BrowserResult


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
        self.qwen_modes = []
        self.response_modes = []
        self.thinking_flags = []
        self.reference_paths = []
        self.new_chat_per_request = new_chat_per_request
        self.default_qwen_mode = "chat"
        self.default_response_mode = "auto"
        self.default_thinking = False

    async def chat_completion(
        self,
        payload,
        *,
        new_chat=None,
        session_id=None,
        qwen_mode=None,
        response_mode=None,
        thinking=None,
    ):
        self.payloads.append(payload)
        self.new_chat_flags.append(new_chat)
        self.qwen_modes.append(qwen_mode)
        self.response_modes.append(response_mode)
        self.thinking_flags.append(thinking)
        return BrowserResult(text="browser pong")

    async def generate_image(self, prompt, *, new_chat=None, reference_image_paths=None):
        self.new_chat_flags.append(new_chat)
        self.reference_paths.append(reference_image_paths or [])
        return BrowserResult(text="", image_urls=["https://example.com/image.png"])

    async def edit_image(self, prompt, *, reference_image_paths, new_chat=None):
        self.new_chat_flags.append(new_chat)
        self.reference_paths.append(reference_image_paths)
        return BrowserResult(text="", image_urls=["https://example.com/edited.png"])

    async def generate_video(self, prompt, *, new_chat=None, reference_image_paths=None):
        self.new_chat_flags.append(new_chat)
        self.reference_paths.append(reference_image_paths or [])
        return BrowserResult(text="", video_urls=["https://example.com/video.mp4"])

    async def aclose(self):
        pass


@pytest.mark.anyio
async def test_models_requires_local_key():
    app = create_app(local_api_key="local-secret", qwen_api_key="upstream-secret", backend_mode="official")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models")

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


@pytest.mark.anyio
async def test_models_returns_openai_shape():
    app = create_app(local_api_key="local-secret", qwen_api_key="upstream-secret", backend_mode="browser")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models", headers=auth_headers())

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]}
    assert "qwen-chat-web" in ids
    assert "qwen-image-web" in ids
    assert "qwen-video-web" in ids


@pytest.mark.anyio
async def test_chat_completion_browser_backend():
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
                "model": "qwen-chat-web",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "browser pong"
    assert browser_backend.new_chat_flags == [True]
    assert browser_backend.response_modes == ["auto"]


@pytest.mark.anyio
async def test_chat_completion_passes_response_mode_to_browser():
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
                "model": "qwen-chat-web",
                "response_mode": "thinking",
                "messages": [{"role": "user", "content": "analyze this"}],
            },
        )

    assert response.status_code == 200
    assert browser_backend.response_modes == ["thinking"]


@pytest.mark.anyio
async def test_chat_completion_with_image_mode():
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
                "model": "qwen-image-web",
                "qwen_mode": "image",
                "messages": [{"role": "user", "content": "画一只猫"}],
            },
        )

    assert response.status_code == 200
    assert browser_backend.qwen_modes == ["image"]


@pytest.mark.anyio
async def test_image_generations_endpoint():
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/images/generations",
            headers=auth_headers(),
            json={"prompt": "a cat"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["url"] == "https://example.com/image.png"
    assert body["data"][0]["revised_prompt"] == "a cat"


@pytest.mark.anyio
async def test_image_generations_with_reference_image_url(monkeypatch):
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async def fake_resolve(payload):
        return ["/tmp/ref.png"], ["/tmp/ref.png"]

    monkeypatch.setattr(app_module, "_resolve_reference_image_paths", fake_resolve)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/images/generations",
            headers=auth_headers(),
            json={
                "prompt": "make it watercolor",
                "image_url": "https://example.com/input.png",
            },
        )

    assert response.status_code == 200
    assert browser_backend.reference_paths == [["/tmp/ref.png"]]


@pytest.mark.anyio
async def test_image_edits_endpoint_multipart():
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/images/edits",
            headers=auth_headers(),
            data={"prompt": "turn the cat into watercolor"},
            files={"image": ("cat.png", b"fake-image-bytes", "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["url"] == "https://example.com/edited.png"
    assert browser_backend.reference_paths
    assert browser_backend.reference_paths[0][0].endswith(".png")


@pytest.mark.anyio
async def test_video_generations_with_reference_image_url(monkeypatch):
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async def fake_resolve(payload):
        return ["/tmp/ref.png"], ["/tmp/ref.png"]

    monkeypatch.setattr(app_module, "_resolve_reference_image_paths", fake_resolve)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/videos/generations",
            headers=auth_headers(),
            json={
                "prompt": "animate this scene",
                "image_url": "https://example.com/frame.png",
            },
        )

    assert response.status_code == 200
    assert browser_backend.reference_paths == [["/tmp/ref.png"]]


@pytest.mark.anyio
async def test_video_generations_endpoint():
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/videos/generations",
            headers=auth_headers(),
            json={"prompt": "a cat running"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["url"] == "https://example.com/video.mp4"


def test_resolve_qwen_mode_aliases():
    assert resolve_qwen_mode({"qwen_mode": "t2i"}, default="chat") == "image"
    assert resolve_qwen_mode({"qwen_mode": "创建视频"}, default="chat") == "video"
    assert resolve_qwen_mode({}, header="deep_research", default="chat") == "deep_research"


def test_resolve_new_chat_priority():
    assert resolve_new_chat({"new_chat": False}, default=True) is False
    assert resolve_new_chat({"metadata": {"new_chat": True}}, default=False) is True
    assert resolve_new_chat({}, header="false", default=True) is False


def test_resolve_response_mode_priority_and_aliases():
    assert resolve_response_mode({"response_mode": "thinking"}, default="auto") == "thinking"
    assert resolve_response_mode({"metadata": {"qwen_response_mode": "快速"}}, default="auto") == "fast"
    assert resolve_response_mode({}, header="auto", default="fast") == "auto"
    assert resolve_response_mode({"thinking": True}, default="auto") == "thinking"
    assert resolve_response_mode({"thinking": False}, default="fast") == "fast"
    assert resolve_response_mode({}, default="auto") == "auto"


def test_resolve_thinking_priority():
    assert resolve_thinking({"thinking": True}, default=False) is True
    assert resolve_thinking({"metadata": {"enable_thinking": False}}, default=True) is False


def test_extract_session_id():
    assert extract_session_id({"session_id": "task-1"}) == "task-1"
    assert extract_session_id({"metadata": {"session_id": "task-2"}}) == "task-2"


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

    assert "User: follow up" in prompt
    assert "first question" not in prompt


@pytest.mark.anyio
async def test_debug_routes_lists_generation_routes():
    app = create_app(local_api_key="local-secret", backend_mode="browser", browser_backend=FakeBrowserBackend())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__debug/routes", headers=auth_headers())

    assert response.status_code == 200
    paths = {route["path"] for route in response.json()["routes"]}
    assert "/v1/chat/completions" in paths
    assert "/v1/images/generations" in paths
    assert "/v1/images/edits" in paths
    assert "/v1/videos/generations" in paths


@pytest.mark.anyio
async def test_chat_completion_logs_request_and_response(caplog):
    browser_backend = FakeBrowserBackend()
    app = create_app(
        local_api_key="local-secret",
        backend_mode="browser",
        browser_backend=browser_backend,
    )
    transport = httpx.ASGITransport(app=app)

    with caplog.at_level(logging.INFO, logger="qwen_openai_proxy"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers=auth_headers(),
                json={
                    "model": "qwen-chat-web",
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )

    assert response.status_code == 200
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "request.start" in log_text
    assert "chat.request" in log_text
    assert "chat.response" in log_text


def test_truncate_shortens_long_strings(monkeypatch):
    monkeypatch.setattr(app_module, "_LOG_MAX_CHARS", 80)
    long_text = "HEAD" + ("a" * 100) + "TAIL"
    result = app_module._truncate(long_text)
    assert result.startswith("HEAD")
    assert result.endswith("TAIL")
    assert "省略中间" in result
    assert len(result) <= 80
