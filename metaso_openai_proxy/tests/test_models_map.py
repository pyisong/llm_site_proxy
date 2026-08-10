from models_map import MODEL_IDS, resolve_search_profile


def test_default_detail_webpage():
    p = resolve_search_profile("metaso-detail")
    assert p.mode == "detail" and p.scope == "webpage"
    assert p.engine_type == ""


def test_scholar_and_overrides():
    p = resolve_search_profile("metaso-concise-scholar")
    assert p.mode == "concise" and p.scope == "scholar"
    assert p.engine_type == "scholar"
    p2 = resolve_search_profile("metaso-detail", scope="document", mode="research")
    assert p2.mode == "research" and p2.scope == "document"


def test_model_catalog_contains_aliases():
    assert "metaso-chat-web" in MODEL_IDS
    assert "metaso-podcast" in MODEL_IDS
    assert "metaso-document" in MODEL_IDS


def test_unknown_model_falls_back_to_detail():
    p = resolve_search_profile("not-a-real-model")
    assert p.mode == "detail" and p.scope == "webpage"
