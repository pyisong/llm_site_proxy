from models_map import MODEL_IDS, PRIMARY_MODEL_IDS, resolve_search_profile


def test_default_chat_webpage():
    p = resolve_search_profile("metaso-chat-web")
    assert p.mode == "chat" and p.scope == "webpage"
    assert p.engine_type == ""


def test_fast_and_detail():
    assert resolve_search_profile("metaso-fast").mode == "fast"
    assert resolve_search_profile("metaso-detail").mode == "detail"


def test_scholar_and_overrides():
    p = resolve_search_profile("metaso-concise-scholar")
    assert p.mode == "concise" and p.scope == "scholar"
    assert p.engine_type == "scholar"
    p2 = resolve_search_profile("metaso-detail", scope="document", mode="research")
    assert p2.mode == "research" and p2.scope == "document"
    p3 = resolve_search_profile("metaso-detail", mode="chat")
    assert p3.mode == "chat"


def test_model_catalog_contains_aliases():
    assert "metaso-chat-web" in MODEL_IDS
    assert "metaso-chat" in MODEL_IDS
    assert "metaso-fast" in MODEL_IDS
    assert "metaso-podcast" in MODEL_IDS
    assert PRIMARY_MODEL_IDS == ["metaso-chat-web"]


def test_unknown_model_falls_back_to_chat():
    p = resolve_search_profile("not-a-real-model")
    assert p.mode == "chat" and p.scope == "webpage"
