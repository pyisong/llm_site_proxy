"""Static registry of llm_site_proxy services (probe via Docker DNS, expose public host URLs)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProxyEndpoint:
    chat: str | None = None
    images: str | None = None
    images_edits: str | None = None
    videos: str | None = None
    models: str | None = None
    tts: str | None = None

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
        return out


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

    @property
    def probe_base(self) -> str:
        return f"http://{self.probe_host}:{self.probe_port}"

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


# Keep ports aligned with docker-compose.start-all.yml defaults.
SERVICES: tuple[ProxyService, ...] = (
    ProxyService(
        id="deepseek-openai-proxy",
        name="DeepSeek OpenAI Proxy",
        probe_host="deepseek-openai-proxy",
        probe_port=8000,
        public_port=18002,
        capabilities=("llm",),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            models="/v1/models",
        ),
        auth_env="DEEPSEEK_PROXY_API_KEY",
    ),
    ProxyService(
        id="kimi-openai-proxy",
        name="Kimi OpenAI Proxy",
        probe_host="kimi-openai-proxy",
        probe_port=8000,
        public_port=18003,
        capabilities=("llm",),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            models="/v1/models",
        ),
        auth_env="KIMI_PROXY_API_KEY",
    ),
    ProxyService(
        id="stepfun-openai-proxy",
        name="StepFun OpenAI Proxy",
        probe_host="stepfun-openai-proxy",
        probe_port=8000,
        public_port=18004,
        capabilities=("llm",),
        endpoints=ProxyEndpoint(
            chat="/v1/chat/completions",
            models="/v1/models",
        ),
        auth_env="STEPFUN_PROXY_API_KEY",
    ),
    ProxyService(
        id="qwen-openai-proxy",
        name="Qwen OpenAI Proxy",
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
    ),
    ProxyService(
        id="cursor-openai-bridge",
        name="Cursor OpenAI Bridge",
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
    ),
    ProxyService(
        id="azure-tts-http-api",
        name="Azure TTS HTTP API",
        probe_host="azure-tts-http-api",
        probe_port=8787,
        public_port=8787,
        capabilities=("tts",),
        endpoints=ProxyEndpoint(tts="/tts"),
        api_prefix="",
        models_path=None,
    ),
)


KNOWN_CAPABILITIES = ("llm", "image", "video", "tts")
