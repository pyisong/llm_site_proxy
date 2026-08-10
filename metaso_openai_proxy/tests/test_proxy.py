from fastapi.testclient import TestClient

from app import create_app


class FakeClient:
    async def ensure_ready(self):
        return None

    async def aclose(self):
        return None

    async def chat(self, q, profile, **kw):
        return {"content": f"echo:{q}", "raw_content": f"echo:{q}", "citations": [], "session_id": "s1"}

    async def chat_stream(self, q, profile, **kw):
        yield {"type": "text", "text": "hi"}
        yield {"type": "citation", "title": "T", "link": "https://t.example", "snippet": ""}
        yield {"type": "done", "session_id": "s1"}

    async def search(self, q, profile, **kw):
        return {
            "q": q,
            "mode": profile.mode,
            "scope": profile.scope,
            "webpages": [{"title": "t", "link": "https://x"}],
            "answer": "a",
        }

    async def reader(self, url, **kw):
        return {"url": url, "markdown": "# ok", "source": "fake"}


def _client() -> TestClient:
    app = create_app(web_client=FakeClient(), local_api_key="secret", skip_storage=True)
    return TestClient(app)


def test_health_and_models():
    c = _client()
    assert c.get("/health").json()["status"] == "ok"
    models = c.get("/v1/models", headers={"Authorization": "Bearer secret"}).json()
    assert any(m["id"] == "metaso-detail" for m in models["data"])


def test_chat_completions_non_stream():
    c = _client()
    r = c.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={
            "model": "metaso-detail",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False,
        },
    )
    assert r.status_code == 200
    assert "echo:你好" in r.json()["choices"][0]["message"]["content"]


def test_chat_completions_requires_auth():
    c = _client()
    r = c.post(
        "/v1/chat/completions",
        json={"model": "metaso-detail", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 401


def test_chat_completions_stream():
    c = _client()
    with c.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={
            "model": "metaso-detail",
            "messages": [{"role": "user", "content": "q"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "data:" in body
    assert "[DONE]" in body
    assert "hi" in body
    assert "参考来源" in body


def test_native_search_reader_chat():
    c = _client()
    headers = {"Authorization": "Bearer secret"}
    search = c.post(
        "/v1/metaso/search",
        headers=headers,
        json={"q": "秘塔", "scope": "webpage", "mode": "concise"},
    )
    assert search.status_code == 200
    assert search.json()["webpages"][0]["link"] == "https://x"

    reader = c.post(
        "/v1/metaso/reader",
        headers=headers,
        json={"url": "https://example.com"},
    )
    assert reader.status_code == 200
    assert reader.json()["markdown"] == "# ok"

    chat = c.post(
        "/v1/metaso/chat",
        headers=headers,
        json={"q": "你好", "mode": "detail", "stream": False},
    )
    assert chat.status_code == 200
    assert "echo:你好" in chat.json()["content"]
