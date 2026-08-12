"""Static registry of llm_site_proxy services (probe via Docker DNS, expose public host URLs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProxyEndpoint:
    chat: str | None = None
    images: str | None = None
    images_edits: str | None = None
    videos: str | None = None
    models: str | None = None
    tts: str | None = None
    search: str | None = None
    reader: str | None = None

    def as_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.chat:
            out["chat"] = self.chat
        if self.images:
            out["images"] = self.images
        if self.images_edits:
            out["images_edits"] = self.images_edits
        if self.videos:
            out["videos"] = self.videos
        if self.models:
            out["models"] = self.models
        if self.tts:
            out["tts"] = self.tts
        if self.search:
            out["search"] = self.search
        if self.reader:
            out["reader"] = self.reader
        return out


def _select_field(
    *,
    key: str,
    request_key: str,
    label: str,
    options: list[dict[str, str]],
    default: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "request_key": request_key,
        "label": label,
        "type": "select",
        "options": options,
        "default": default,
    }


def _bool_field(
    *,
    key: str,
    request_key: str,
    label: str,
    default: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "request_key": request_key,
        "label": label,
        "type": "boolean",
        "default": default,
    }


DEEPSEEK_UI_SCHEMA: dict[str, Any] = {
    "fields": [
        _select_field(
            key="deepseek_web_mode",
            request_key="deepseek_mode",
            label="网页模式",
            options=[
                {"value": "fast", "label": "快速模式"},
                {"value": "expert", "label": "专家模式"},
                {"value": "vision", "label": "识图模式"},
            ],
            default="fast",
        ),
        _bool_field(
            key="deepseek_deep_thinking",
            request_key="deep_thinking",
            label="深度思考",
            default=False,
        ),
    ]
}

KIMI_UI_SCHEMA: dict[str, Any] = {
    "fields": [
        _select_field(
            key="kimi_web_mode",
            request_key="kimi_mode",
            label="网页模式",
            options=[
                {"value": "fast", "label": "快速（标准）"},
                {"value": "thinking", "label": "快速（进阶）"},
                {"value": "k3", "label": "K3"},
                {"value": "k3_extreme", "label": "K3（极致）"},
                {"value": "k3_cluster", "label": "K3 集群"},
            ],
            default="fast",
        ),
    ]
}

STEPFUN_UI_SCHEMA: dict[str, Any] = {
    "fields": [
        _select_field(
            key="stepfun_web_mode",
            request_key="stepfun_mode",
            label="网页模式",
            options=[
                {"value": "fast", "label": "快速"},
                {"value": "search", "label": "搜索"},
                {"value": "deep_research", "label": "深入核查"},
                {"value": "knowledge", "label": "知识库问答"},
                {"value": "image", "label": "图片创作"},
            ],
            default="fast",
        ),
    ]
}

QWEN_UI_SCHEMA: dict[str, Any] = {
    "fields": [
        _select_field(
            key="qwen_web_mode",
            request_key="qwen_mode",
            label="网页模式",
            options=[
                {"value": "chat", "label": "对话"},
                {"value": "image", "label": "图片创作"},
                {"value": "video", "label": "视频创作"},
                {"value": "deep_research", "label": "深度研究"},
                {"value": "web_dev", "label": "网页开发"},
            ],
            default="chat",
        ),
    ]
}

METASO_UI_SCHEMA: dict[str, Any] = {
    "fields": [
        _select_field(
            key="metaso_mode",
            request_key="metaso_mode",
            label="检索模式",
            options=[
                {"value": "chat", "label": "对话"},
                {"value": "fast", "label": "快速"},
                {"value": "concise", "label": "简洁"},
                {"value": "detail", "label": "深入"},
                {"value": "research", "label": "研究"},
                {"value": "nosearch", "label": "无搜索"},
            ],
            default="chat",
        ),
        _select_field(
            key="metaso_scope",
            request_key="metaso_scope",
            label="检索范围",
            options=[
                {"value": "webpage", "label": "全网"},
                {"value": "scholar", "label": "学术"},
                {"value": "document", "label": "文库"},
                {"value": "podcast", "label": "播客"},
            ],
            default="webpage",
        ),
    ]
}


@dataclass(frozen=True)
class ProxySessionHint:
    """How clients should bind multi-turn web sessions for this proxy."""

    metadata_key: str = "session_id"
    supports_new_chat: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "metadata_key": self.metadata_key,
            "supports_new_chat": self.supports_new_chat,
        }


@dataclass(frozen=True)
class ProxyService:
    id: str
    name: str
    # Docker DNS host used for live probes from within llm_site_proxy_net
    probe_host: str
    probe_port: int
    # Host-published port for public base_url
    public_port: int
    capabilities: tuple[str, ...]
    endpoints: ProxyEndpoint
    # Path prefix for OpenAI-style APIs; empty for bare TTS
    api_prefix: str = "/v1"
    health_path: str = "/health"
    models_path: str | None = "/v1/models"
    # Optional Bearer for /v1/models (some proxies require it)
    auth_env: str | None = None
    route_kind: str = "web_proxy"
    ui_schema: dict[str, Any] | None = None
    session: ProxySessionHint | None = None
    # Short label for status strips / dropdowns
    short_name: str = ""

    @property
    def probe_base(self) -> str:
        return f"http://{self.probe_host}:{self.probe_port}"

    def display_name(self) -> str:
        return (self.short_name or self.name or self.id).strip()

    def internal_base_url(self) -> str:
        """Docker DNS 地址（同 llm_site_proxy_net 内可达）。"""
        if self.api_prefix:
            return f"{self.probe_base}{self.api_prefix}"
        return self.probe_base

    def public_base_url(self, public_host: str) -> str:
        root = f"http://{public_host}:{self.public_port}"
        if self.api_prefix:
            return f"{root}{self.api_prefix}"
        return root


_DEFAULT_SESSION = ProxySessionHint()

# Keep ports aligned with docker-compose.start-all.yml defaults.
SERVICES: tuple[ProxyService, ...] = (
    ProxyService(
        id="deepseek-openai-proxy",
        name="DeepSeek OpenAI Proxy",
        short_name="DeepSeek",
        probe_host="deepseek-openai-proxy",
        probe_port=8000,
        public_port=18002,
        capabilities=("llm",),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            models="/v1/models",
        ),
        auth_env="DEEPSEEK_PROXY_API_KEY",
        route_kind="web_proxy",
        ui_schema=DEEPSEEK_UI_SCHEMA,
        session=_DEFAULT_SESSION,
    ),
    ProxyService(
        id="kimi-openai-proxy",
        name="Kimi OpenAI Proxy",
        short_name="Kimi",
        probe_host="kimi-openai-proxy",
        probe_port=8000,
        public_port=18003,
        capabilities=("llm",),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            models="/v1/models",
        ),
        auth_env="KIMI_PROXY_API_KEY",
        route_kind="web_proxy",
        ui_schema=KIMI_UI_SCHEMA,
        session=_DEFAULT_SESSION,
    ),
    ProxyService(
        id="stepfun-openai-proxy",
        name="StepFun OpenAI Proxy",
        short_name="StepFun",
        probe_host="stepfun-openai-proxy",
        probe_port=8000,
        public_port=18004,
        capabilities=("llm",),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            models="/v1/models",
        ),
        auth_env="STEPFUN_PROXY_API_KEY",
        route_kind="web_proxy",
        ui_schema=STEPFUN_UI_SCHEMA,
        session=_DEFAULT_SESSION,
    ),
    ProxyService(
        id="qwen-openai-proxy",
        name="Qwen OpenAI Proxy",
        short_name="Qwen",
        probe_host="qwen-openai-proxy",
        probe_port=8000,
        public_port=18005,
        capabilities=("llm", "image", "video"),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            images="/v1/images/generations",
            images_edits="/v1/images/edits",
            videos="/v1/videos/generations",
            models="/v1/models",
        ),
        auth_env="QWEN_PROXY_API_KEY",
        route_kind="web_proxy",
        ui_schema=QWEN_UI_SCHEMA,
        session=_DEFAULT_SESSION,
    ),
    ProxyService(
        id="metaso-openai-proxy",
        name="Metaso OpenAI Proxy",
        short_name="秘塔",
        probe_host="metaso-openai-proxy",
        probe_port=8000,
        public_port=18006,
        capabilities=("llm", "search"),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            models="/v1/models",
            search="/v1/metaso/search",
            reader="/v1/metaso/reader",
        ),
        auth_env="METASO_PROXY_API_KEY",
        route_kind="web_proxy",
        ui_schema=METASO_UI_SCHEMA,
        session=_DEFAULT_SESSION,
    ),
    ProxyService(
        id="cursor-openai-bridge",
        name="Cursor OpenAI Bridge",
        short_name="Cursor",
        probe_host="cursor-openai-bridge",
        probe_port=8765,
        public_port=8765,
        capabilities=("llm", "image"),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            images="/v1/images/generations",
            images_edits="/v1/images/edits",
            models="/v1/models",
        ),
        auth_env="CURSOR_OPENAI_BRIDGE_API_KEY",
        route_kind="cursor_bridge",
    ),
    ProxyService(
        id="azure-tts-http-api",
        name="Azure TTS HTTP API",
        short_name="TTS",
        probe_host="azure-tts-http-api",
        probe_port=8787,
        public_port=8787,
        capabilities=("tts",),
        endpoints=ProxyEndpoint(tts="/tts"),
        api_prefix="",
        models_path=None,
        route_kind="tts",
    ),
)


KNOWN_CAPABILITIES = ("llm", "image", "video", "tts", "search")
ROUTE_KINDS = ("web_proxy", "cursor_bridge", "tts")


def infer_model_capabilities(model_id: str, service_capabilities: tuple[str, ...] | list[str]) -> list[str]:
    """Tag /v1/models ids so clients can split LLM vs image vs video dropdowns."""
    mid = (model_id or "").strip().lower()
    svc = {str(c).strip().lower() for c in (service_capabilities or []) if c}
    if not mid:
        return []
    caps: list[str] = []
    if "video" in mid or mid.endswith("-video-web"):
        caps.append("video")
    elif "image" in mid or mid.endswith("-image-web"):
        caps.append("image")
    else:
        if "llm" in svc:
            caps.append("llm")
        if "search" in mid and "search" in svc:
            caps.append("search")
    # Keep only caps the service actually advertises (plus llm when chat-like)
    out: list[str] = []
    for c in caps:
        if c == "llm" or c in svc:
            if c not in out:
                out.append(c)
    if not out and "llm" in svc:
        out.append("llm")
    return out
