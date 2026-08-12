"""OpenAI model id ↔ Metaso search / chat mode × scope."""

from __future__ import annotations

from dataclasses import dataclass

# 网页内部：
# - metaso-chat* → POST /api/search/chat（官网对话；searchV2+mode=chat 仍偏检索百科）
# - 其余 model → GET /api/searchV2，mode=fast/concise/detail/research…
KNOWN_MODES = frozenset(
    {"chat", "fast", "concise", "detail", "research", "nosearch"}
)
KNOWN_SCOPES = frozenset({"webpage", "scholar", "document", "podcast"})


@dataclass(frozen=True)
class SearchProfile:
    mode: str
    scope: str  # webpage | scholar | document | podcast

    @property
    def engine_type(self) -> str:
        """Webpage-internal ``engineType`` field."""
        if self.scope == "webpage":
            return ""
        return self.scope


_MODEL_TABLE: dict[str, SearchProfile] = {
    # 对话向（对齐其它 proxy 的 *-chat-web；官网 mode=chat）
    "metaso-chat-web": SearchProfile("chat", "webpage"),
    "metaso-chat": SearchProfile("chat", "webpage"),
    "metaso-fast": SearchProfile("fast", "webpage"),
    # 检索强度
    "metaso-concise": SearchProfile("concise", "webpage"),
    "metaso-detail": SearchProfile("detail", "webpage"),
    "metaso-research": SearchProfile("research", "webpage"),
    "metaso-concise-scholar": SearchProfile("concise", "scholar"),
    "metaso-detail-scholar": SearchProfile("detail", "scholar"),
    "metaso-research-scholar": SearchProfile("research", "scholar"),
    "metaso-document": SearchProfile("detail", "document"),
    "metaso-podcast": SearchProfile("detail", "podcast"),
}

MODEL_IDS = list(_MODEL_TABLE.keys())
# 下拉 / 发现只暴露单一入口；mode×scope 走请求体 metaso_mode / metaso_scope（与 DeepSeek 一致）
PRIMARY_MODEL_IDS = ["metaso-chat-web"]


def resolve_search_profile(
    model: str | None,
    *,
    scope: str | None = None,
    mode: str | None = None,
) -> SearchProfile:
    key = (model or "").strip() or "metaso-chat-web"
    base = _MODEL_TABLE.get(key, _MODEL_TABLE["metaso-chat-web"])
    out_mode = (mode or base.mode).strip().lower()
    out_scope = (scope or base.scope).strip().lower()
    if out_mode not in KNOWN_MODES:
        out_mode = base.mode
    if out_scope not in KNOWN_SCOPES:
        out_scope = base.scope
    return SearchProfile(mode=out_mode, scope=out_scope)
