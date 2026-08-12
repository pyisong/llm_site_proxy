from fastapi.testclient import TestClient

from app import (
    completion_text_from_result,
    compose_browser_prompt,
    create_app,
    last_user_query,
)


class FakeClient:
    last_q: str = ""

    async def ensure_ready(self):
        return None

    async def aclose(self):
        return None

    async def chat(self, q, profile, **kw):
        FakeClient.last_q = q
        return {
            "content": f"echo:{q}\n\n参考来源:\n1. T — https://t.example",
            "raw_content": f"echo:{q}",
            "citations": [{"title": "T", "link": "https://t.example"}],
            "session_id": "s1",
        }

    async def chat_stream(self, q, profile, **kw):
        FakeClient.last_q = q
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
    FakeClient.last_q = ""
    app = create_app(web_client=FakeClient(), local_api_key="secret", skip_storage=True)
    return TestClient(app)


def test_health_and_models():
    c = _client()
    assert c.get("/health").json()["status"] == "ok"
    models = c.get("/v1/models", headers={"Authorization": "Bearer secret"}).json()
    ids = {m["id"] for m in models["data"]}
    assert ids == {"metaso-chat-web"}


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
    assert "echo:User: 你好" in r.json()["choices"][0]["message"]["content"]
    assert "参考来源" not in r.json()["choices"][0]["message"]["content"]


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


def test_chat_completions_includes_system_prompt():
    c = _client()
    r = c.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={
            "model": "metaso-chat-web",
            "new_chat": True,
            "messages": [
                {"role": "system", "content": "只输出 JSON"},
                {"role": "user", "content": "生成配图"},
            ],
            "stream": False,
        },
    )
    assert r.status_code == 200
    assert "System: 只输出 JSON" in FakeClient.last_q
    assert "User: 生成配图" in FakeClient.last_q
    # 结构化场景应返回 raw_content，避免「参考来源」污染
    content = r.json()["choices"][0]["message"]["content"]
    assert content.startswith("echo:")
    assert "参考来源" not in content


def test_compose_browser_prompt_and_completion_text():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    assert last_user_query(messages) == "u2"
    full = compose_browser_prompt(messages, reuse_session=False)
    assert "System: sys" in full and "User: u2" in full and "Assistant: a1" in full
    reuse = compose_browser_prompt(messages, reuse_session=True)
    assert reuse == "System: sys\n\nUser: u2"
    raw_pref = completion_text_from_result(
        {"raw_content": '{"prompts":[]}', "content": '{"prompts":[]}\n\n参考来源:\n1. x'},
        response_format={"type": "json_object"},
    )
    assert raw_pref == '{"prompts":[]}'


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
