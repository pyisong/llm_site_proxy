from __future__ import annotations

import respx
from httpx import Response
from fastapi.testclient import TestClient

from app import create_app
from registry import SERVICES


def test_health():
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}


@respx.mock
def test_services_live_probe_and_by_capability(monkeypatch):
    monkeypatch.setenv("CATALOG_PUBLIC_HOST", "10.1.10.113")
    for svc in SERVICES:
        respx.get(f"{svc.probe_base}{svc.health_path}").mock(
            return_value=Response(200, json={"status": "ok"})
        )
        if svc.models_path:
            respx.get(f"{svc.probe_base}{svc.models_path}").mock(
                return_value=Response(
                    200,
                    json={"data": [{"id": f"{svc.id}-model", "object": "model"}]},
                )
            )

    client = TestClient(create_app())
    payload = client.get("/v1/services").json()

    assert payload["public_host"] == "10.1.10.113"
    assert len(payload["services"]) == len(SERVICES)
    assert all(s["status"] == "online" for s in payload["services"])

    qwen = next(s for s in payload["services"] if s["id"] == "qwen-openai-proxy")
    assert qwen["base_url"] == "http://10.1.10.113:18005/v1"
    assert qwen["internal_base_url"] == "http://qwen-openai-proxy:8000/v1"
    assert set(qwen["capabilities"]) == {"llm", "image", "video"}
    assert qwen["models"][0]["id"] == "qwen-openai-proxy-model"
    assert qwen["route_kind"] == "web_proxy"
    assert qwen["short_name"] == "Qwen"
    assert qwen["ui_schema"]["fields"][0]["key"] == "qwen_web_mode"
    assert qwen["session"]["supports_new_chat"] is True
    assert "llm" in qwen["models"][0]["capabilities"]

    cursor = next(s for s in payload["services"] if s["id"] == "cursor-openai-bridge")
    assert cursor["route_kind"] == "cursor_bridge"
    assert cursor["ui_schema"] is None

    tts = next(s for s in payload["services"] if s["id"] == "azure-tts-http-api")
    assert tts["base_url"] == "http://10.1.10.113:8787"
    assert tts["endpoints"]["tts"] == "/tts"
    assert tts["models"] == []

    assert "llm" in payload["by_capability"]
    assert "image" in payload["by_capability"]
    assert "video" in payload["by_capability"]
    assert "tts" in payload["by_capability"]
    assert "search" in payload["by_capability"]
    assert any(x["id"] == "qwen-openai-proxy" for x in payload["by_capability"]["image"])
    assert any(x["id"] == "azure-tts-http-api" for x in payload["by_capability"]["tts"])
    metaso = next(s for s in payload["services"] if s["id"] == "metaso-openai-proxy")
    assert metaso["base_url"] == "http://10.1.10.113:18006/v1"
    assert set(metaso["capabilities"]) == {"llm", "search"}
    assert metaso["endpoints"]["search"] == "/v1/metaso/search"
    assert metaso["ui_schema"]["fields"][0]["key"] == "metaso_mode"
    assert metaso["ui_schema"]["fields"][1]["key"] == "metaso_scope"
    assert any(x["id"] == "metaso-openai-proxy" for x in payload["by_capability"]["search"])


@respx.mock
def test_offline_service(monkeypatch):
    monkeypatch.setenv("CATALOG_PUBLIC_HOST", "127.0.0.1")
    for svc in SERVICES:
        if svc.id == "deepseek-openai-proxy":
            respx.get(f"{svc.probe_base}{svc.health_path}").mock(side_effect=ConnectionError("down"))
        else:
            respx.get(f"{svc.probe_base}{svc.health_path}").mock(
                return_value=Response(200, json={"ok": True})
            )
            if svc.models_path:
                respx.get(f"{svc.probe_base}{svc.models_path}").mock(
                    return_value=Response(200, json={"data": []})
                )

    client = TestClient(create_app())
    payload = client.get("/v1/services").json()
    deepseek = next(s for s in payload["services"] if s["id"] == "deepseek-openai-proxy")
    assert deepseek["status"] == "offline"
    assert "error" in deepseek
    assert not any(x["id"] == "deepseek-openai-proxy" for x in payload["by_capability"]["llm"])


@respx.mock
def test_capability_filter(monkeypatch):
    monkeypatch.setenv("CATALOG_PUBLIC_HOST", "127.0.0.1")
    for svc in SERVICES:
        respx.get(f"{svc.probe_base}{svc.health_path}").mock(
            return_value=Response(200, json={"status": "ok"})
        )
        if svc.models_path:
            respx.get(f"{svc.probe_base}{svc.models_path}").mock(
                return_value=Response(200, json={"data": []})
            )

    client = TestClient(create_app())
    payload = client.get("/v1/services", params={"capability": "video"}).json()
    assert all("video" in s["capabilities"] for s in payload["services"])
    assert list(payload["by_capability"].keys()) == ["video"]
    assert payload["by_capability"]["video"]
