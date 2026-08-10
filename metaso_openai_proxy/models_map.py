"""OpenAI model id ↔ Metaso search strength × scope."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchProfile:
    mode: str  # concise | detail | research
    scope: str  # webpage | scholar | document | podcast

    @property
    def engine_type(self) -> str:
        """Webpage-internal ``engineType`` field."""
        if self.scope == "webpage":
            return ""
        return self.scope


_MODEL_TABLE: dict[str, SearchProfile] = {
    "metaso-concise": SearchProfile("concise", "webpage"),
    "metaso-detail": SearchProfile("detail", "webpage"),
    "metaso-research": SearchProfile("research", "webpage"),
    "metaso-concise-scholar": SearchProfile("concise", "scholar"),
    "metaso-detail-scholar": SearchProfile("detail", "scholar"),
    "metaso-research-scholar": SearchProfile("research", "scholar"),
    "metaso-document": SearchProfile("detail", "document"),
    "metaso-podcast": SearchProfile("detail", "podcast"),
    "metaso-chat-web": SearchProfile("detail", "webpage"),
}

MODEL_IDS = list(_MODEL_TABLE.keys())


def resolve_search_profile(
    model: str | None,
    *,
    scope: str | None = None,
    mode: str | None = None,
) -> SearchProfile:
    key = (model or "").strip() or "metaso-detail"
    base = _MODEL_TABLE.get(key, _MODEL_TABLE["metaso-detail"])
    out_mode = (mode or base.mode).strip().lower()
    out_scope = (scope or base.scope).strip().lower()
    if out_mode not in {"concise", "detail", "research"}:
        out_mode = base.mode
    if out_scope not in {"webpage", "scholar", "document", "podcast"}:
        out_scope = base.scope
    return SearchProfile(mode=out_mode, scope=out_scope)
