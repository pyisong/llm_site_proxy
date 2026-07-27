"""文生图共用逻辑：风格前缀、场景预设（SVG Agent / 扩散模型 prompt）及 HTTP 工具。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Literal

ImageEngine = Literal["agent", "agent_interactive", "sd_webui"]

import httpx

_VECTOR_SVG_AGENT = (
    "Mandatory visual style: flat vector graphic inside the SVG — solid fills, clear shapes, "
    "optional strokes in dark gray-brown (~2px equivalent), closed paths, no embedded raster images, "
    "no photo textures.\n\n"
)

_REALISTIC_SVG_AGENT = (
    "Visual style: semi-realistic illustrated portrait in pure SVG — use smooth linear/radial "
    "gradients for skin and hair, soft highlights; avoid flat clip-art unless the subject demands it; "
    "still no embedded raster images.\n\n"
)

_VECTOR_DIFFUSION = (
    "flat vector illustration, clean graphic art, solid color shapes, bold outlines, "
)

_REALISTIC_DIFFUSION = (
    "photorealistic, natural soft lighting, highly detailed, sharp focus, "
)

_EDITORIAL_DIFFUSION = (
    "soft editorial illustration, gentle warm golden-hour light, soft green grass, "
    "pastel sky with wispy clouds, harmonious inclusive atmosphere, shallow depth of field, "
    "watercolor-like textures, muted warm palette, magazine editorial quality, "
)

_EDITORIAL_SVG_AGENT = (
    "Visual style: soft editorial illustration — warm gentle light, pastel sky, soft greens, "
    "readable simplified figures, harmonious inclusive mood; reserve the bottom third as empty "
    "negative space for call-to-action text overlay; no embedded raster images.\n\n"
)

# 场景预设：复杂编辑插画/光栅图建议 ``engine=sd_webui`` + ``image_style=editorial``
PARK_FLOW_COMMUNITY_PROMPT = (
    "A warm and inviting scene of diverse people each engaged in their own flow activity "
    "in a shared sunlit park: one person sketching on a bench, another doing tai chi, "
    "a child building sand in a small pit, a woman reading under a tree, "
    "soft editorial illustration style with gentle warm light, soft green grass, "
    "pastel sky with wispy clouds, harmonious and inclusive atmosphere, shallow depth of field, "
    "conveying community, everyday joy, and the universal accessibility of flow experiences, "
    "composition leaves space at the bottom for call-to-action text"
)

PARK_FLOW_COMMUNITY_NEGATIVE = (
    "text, watermark, logo, signature, blurry, low quality, distorted anatomy, extra limbs, "
    "harsh neon, cluttered background, readable signage"
)


@dataclass(frozen=True)
class ImagePreset:
    """内置场景：默认尺寸、引擎与风格。"""

    prompt: str
    default_size: str = "1344x768"
    engine: Literal["agent", "sd_webui"] = "sd_webui"
    image_style: str = "editorial"
    negative_prompt_suffix: str = ""
    description_zh: str = ""


IMAGE_PRESETS: dict[str, ImagePreset] = {
    "park_flow_community": ImagePreset(
        prompt=PARK_FLOW_COMMUNITY_PROMPT,
        default_size="1344x768",
        engine="sd_webui",
        image_style="editorial",
        negative_prompt_suffix=PARK_FLOW_COMMUNITY_NEGATIVE,
        description_zh="阳光公园心流社区编辑插画（底部留白 CTA）",
    ),
}


def list_image_preset_ids() -> list[str]:
    return sorted(IMAGE_PRESETS.keys())


def get_image_preset(preset_id: str) -> ImagePreset | None:
    key = (preset_id or "").strip().lower().replace("-", "_")
    return IMAGE_PRESETS.get(key)


@dataclass
class ResolvedImageRequest:
    """``/v1/images/generations`` 解析后的有效参数。"""

    prompt: str
    size: str
    engine: ImageEngine
    image_style: str
    export_png: bool
    negative_prompt_extra: str = ""


def resolve_image_request(
    *,
    prompt: str,
    size: str,
    metadata: dict[str, Any] | None,
    env_engine: str = "agent",
) -> ResolvedImageRequest:
    """合并 ``metadata.preset``、风格与引擎默认值。"""
    meta = metadata if isinstance(metadata, dict) else {}
    preset_obj: ImagePreset | None = None
    raw_preset = meta.get("preset") or meta.get("image_preset")
    if isinstance(raw_preset, str) and raw_preset.strip():
        preset_obj = get_image_preset(raw_preset)
        if preset_obj is None:
            raise ValueError(
                f"未知 preset: {raw_preset!r}，可用: {', '.join(list_image_preset_ids())}"
            )

    user_prompt = (prompt or "").strip()
    if user_prompt in (".", "…"):
        user_prompt = ""
    if preset_obj:
        if not user_prompt:
            merged_prompt = preset_obj.prompt
        else:
            merged_prompt = f"{preset_obj.prompt.strip()}\n\nAdditional details: {user_prompt}"
    else:
        merged_prompt = user_prompt

    if not merged_prompt:
        raise ValueError("prompt 必须为非空字符串（或使用 metadata.preset）")

    resolved_size = (size or "1024x1024").strip()
    if preset_obj and (not size or size.strip().lower() in ("1024x1024", "default")):
        resolved_size = preset_obj.default_size

    image_style = "none"
    raw_style = meta.get("image_style") or meta.get("style")
    if isinstance(raw_style, str) and raw_style.strip():
        image_style = raw_style.strip().lower()
    if preset_obj and image_style == "none":
        image_style = preset_obj.image_style

    engine_raw = (env_engine or "agent").strip().lower()
    meta_eng = meta.get("image_engine")
    if isinstance(meta_eng, str) and meta_eng.strip():
        engine_raw = meta_eng.strip().lower()
    if preset_obj and not (isinstance(meta_eng, str) and meta_eng.strip()):
        engine_raw = preset_obj.engine

    if engine_raw in (
        "sd_webui",
        "sd-webui",
        "sdwebui",
        "a1111",
        "webui",
        "stable-diffusion",
        "stable_diffusion",
    ):
        engine: ImageEngine = "sd_webui"
    elif engine_raw in (
        "agent_interactive",
        "agent-interactive",
        "agent_tools",
        "agent-tools",
        "interactive",
        "ide_agent",
        "ide-agent",
    ):
        engine = "agent_interactive"
    else:
        engine = "agent"

    export_png = False
    ie = str(meta.get("image_export", "")).lower()
    if ie == "png" or meta.get("image_export_png") is True:
        export_png = True
    if engine in ("sd_webui", "agent_interactive"):
        export_png = True

    neg_extra = ""
    if preset_obj and preset_obj.negative_prompt_suffix:
        neg_extra = preset_obj.negative_prompt_suffix.strip()

    return ResolvedImageRequest(
        prompt=merged_prompt,
        size=resolved_size,
        engine=engine,
        image_style=image_style,
        export_png=export_png,
        negative_prompt_extra=neg_extra,
    )


def augment_prompt_for_style(
    prompt: str,
    style: str,
    *,
    target: Literal["svg_agent", "diffusion"],
) -> str:
    """在用户提示前附加风格说明。``style`` 为 ``vector`` / ``realistic`` / 其它（不附加）。"""
    p = prompt.strip()
    s = (style or "none").strip().lower()
    if s not in ("vector", "realistic", "editorial") or not p:
        return p
    if target == "diffusion":
        if s == "vector":
            prefix = _VECTOR_DIFFUSION
        elif s == "realistic":
            prefix = _REALISTIC_DIFFUSION
        else:
            prefix = _EDITORIAL_DIFFUSION
    else:
        if s == "vector":
            prefix = _VECTOR_SVG_AGENT
        elif s == "realistic":
            prefix = _REALISTIC_SVG_AGENT
        else:
            prefix = _EDITORIAL_SVG_AGENT
    return prefix + p


def merge_negative_prompts(*parts: str) -> str:
    """合并负向 prompt 片段（去空、逗号拼接）。"""
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for chunk in (part or "").replace(";", ",").split(","):
            t = chunk.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                tokens.append(t)
    return ", ".join(tokens)


def fetch_image_url(url: str, *, timeout: float = 120.0) -> tuple[bytes, str]:
    """下载 ``http(s)`` 图像，返回 ``(bytes, 建议后缀)``。"""
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        r = client.get(url)
        r.raise_for_status()
        raw = r.content
        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if "svg" in ct:
        ext = ".svg"
    elif "webp" in ct:
        ext = ".webp"
    elif "jpeg" in ct or "jpg" in ct:
        ext = ".jpg"
    elif "png" in ct:
        ext = ".png"
    else:
        ext = ".png"
    return raw, ext

def sd_webui_txt2img_png(
    prompt: str,
    *,
    base_url: str,
    width: int,
    height: int,
    negative_prompt: str = "",
    steps: int = 28,
    cfg_scale: float = 7.0,
    timeout: float = 600.0,
) -> bytes:
    """调用 Automatic1111 Stable Diffusion WebUI 的 ``POST /sdapi/v1/txt2img``，返回单张 PNG 字节。"""
    root = base_url.strip().rstrip("/")
    url = f"{root}/sdapi/v1/txt2img"
    payload: dict[str, object] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "steps": max(1, int(steps)),
        "width": max(64, min(2048, int(width))),
        "height": max(64, min(2048, int(height))),
        "cfg_scale": float(cfg_scale),
        "batch_size": 1,
        "n_iter": 1,
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    images = data.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError("sdapi/v1/txt2img 响应中缺少 images 数组")
    b64 = images[0]
    if not isinstance(b64, str):
        raise RuntimeError("sdapi/v1/txt2img images[0] 非 base64 字符串")
    return base64.standard_b64decode(b64)


def sd_webui_img2img_png(
    prompt: str,
    *,
    init_image_bytes: bytes,
    base_url: str,
    width: int,
    height: int,
    negative_prompt: str = "",
    steps: int = 28,
    cfg_scale: float = 7.0,
    denoising_strength: float = 0.65,
    timeout: float = 600.0,
) -> bytes:
    """调用 Automatic1111 ``POST /sdapi/v1/img2img``，基于参考图生成单张 PNG。"""
    root = base_url.strip().rstrip("/")
    url = f"{root}/sdapi/v1/img2img"
    init_b64 = base64.standard_b64encode(init_image_bytes).decode("ascii")
    payload: dict[str, object] = {
        "init_images": [init_b64],
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "steps": max(1, int(steps)),
        "width": max(64, min(2048, int(width))),
        "height": max(64, min(2048, int(height))),
        "cfg_scale": float(cfg_scale),
        "denoising_strength": max(0.0, min(1.0, float(denoising_strength))),
        "batch_size": 1,
        "n_iter": 1,
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    images = data.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError("sdapi/v1/img2img 响应中缺少 images 数组")
    b64 = images[0]
    if not isinstance(b64, str):
        raise RuntimeError("sdapi/v1/img2img images[0] 非 base64 字符串")
    return base64.standard_b64decode(b64)

