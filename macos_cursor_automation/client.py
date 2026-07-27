"""示例：用 OpenAI SDK 调用本地 ``serve`` 网关：Chat 与 Images（均为 Cursor 桥）。

Chat：``CURSOR_CLIENT_BASE_URL``（默认 ``http://127.0.0.1:8765/v1``）。单次请求可在 ``extra_body={"metadata": {"cursor_agent_mode": "writable"}}`` 与 ``agent_interactive`` 一样使用默认可写 Agent（不传 ``--mode ask``）。

图像：``client.images.generate`` → 本地桥 ``POST /v1/images/generations``（与 chat 同一 Base URL）。
图像编辑：``client.images.edit`` → 本地桥 ``POST /v1/images/edits``（上传参考图 + prompt；子命令 ``image-edit``）。

- **默认**：网关用 **cursor agent** 产出 **SVG**；``--save-as png`` 时在服务端栅格化为 PNG（矢量渲染）。
- **agent_interactive**：``--engine agent_interactive`` 时由 Agent 使用与 IDE 聊天类似的生图工具，将 **PNG** 写入
  workspace 并由网关读回（需本机 ``cursor agent`` 已登录且具备生图能力）。
- **扩散**：在网关配置 ``CURSOR_BRIDGE_IMAGE_ENGINE=sd_webui`` 且 ``CURSOR_BRIDGE_SDWEBUI_URL`` 指向
  Automatic1111 WebUI 后，同一接口改为 **txt2img** 出光栅 PNG；也可用 ``--engine sd_webui`` 写入
  ``metadata.image_engine`` 单次切换。

``--style vector|realistic|editorial`` 写入 ``metadata.image_style``（**agent** 为 SVG 指引；**sd_webui** / **agent_interactive** 为英文 prompt 风格前缀）。

``--preset park_flow_community`` 使用内置公园心流社区编辑插画场景（推荐 ``--engine sd_webui``）。

启动时会尝试加载与本文件同目录的 ``.env``（不覆盖已在 shell 里 export 的变量）。
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI

from image_generation import fetch_image_url

_REPO_ROOT = Path(__file__).resolve().parent


def _load_env_file() -> None:
    """从 ``_REPO_ROOT/.env`` 读入 ``KEY=value``（不覆盖 ``os.environ`` 已有键）。"""
    path = _REPO_ROOT / ".env"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def _image_url_for_request(path: str) -> str:
    if path.startswith("http"):
        return path
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{b64}"


def _resolve_model(cli_model: str | None) -> str:
    if cli_model and cli_model.strip():
        return cli_model.strip()
    env = os.environ.get("CURSOR_CLIENT_MODEL")
    if env and env.strip():
        return env.strip()
    return "cursor-agent"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenAI SDK → 本地 Cursor 桥（chat 子命令）"
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="模型 id，传给网关 body.model（默认读 CURSOR_CLIENT_MODEL，再默认 cursor-agent）",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        default="简要描述这张图",
        help="用户文本提示",
    )
    parser.add_argument(
        "-i",
        "--image",
        default=None,
        help="图像路径或 http(s) URL",
    )
    args = parser.parse_args()

    base_url = os.environ.get("CURSOR_CLIENT_BASE_URL", "http://127.0.0.1:8765/v1")
    api_key = os.environ.get("CURSOR_OPENAI_BRIDGE_API_KEY", "dummy")
    client = OpenAI(base_url=base_url, api_key=api_key)
    if args.image:
        content: list[dict[str, Any]] | str = [
            {"type": "text", "text": args.prompt},
            {"type": "image_url", "image_url": {"url": _image_url_for_request(args.image)}},
        ]
    else:
        content = args.prompt
    r = client.chat.completions.create(
        model=_resolve_model(args.model),
        messages=[{"role": "user", "content": content}],
    )
    print(r.choices[0].message.content)


def _item_field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _decode_generated_image_item(
    item: Any,
    *,
    b64_fallback_ext: str = ".svg",
) -> tuple[bytes, str] | None:
    """从 ``ImagesResponse.data[]`` 解出二进制与建议后缀（``url`` 从 data URL 推断 MIME；``b64_json`` 无 MIME 时用 ``b64_fallback_ext``）。"""
    bj = _item_field(item, "b64_json")
    if bj:
        ext = b64_fallback_ext if b64_fallback_ext.startswith(".") else f".{b64_fallback_ext}"
        return base64.b64decode(bj), ext
    url = _item_field(item, "url")
    if not url or not isinstance(url, str):
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return fetch_image_url(url)
    if url.startswith("data:") and ";base64," in url:
        _, _, b64part = url.partition(";base64,")
        mime_part = url[5 : url.index(";base64,")]
        raw = base64.b64decode(b64part)
        ml = mime_part.lower()
        if "svg" in ml:
            ext = ".svg"
        elif "png" in ml:
            ext = ".png"
        elif "webp" in ml:
            ext = ".webp"
        elif "jpeg" in ml or "jpg" in ml:
            ext = ".jpg"
        else:
            ext = ".bin"
        return raw, ext
    return None


def save_generated_images_to_files(
    image_r: Any,
    output: Path | str | None,
    *,
    auto_prefix: str = "generated",
    b64_fallback_ext: str = ".svg",
) -> list[Path]:
    """将 ``url`` / ``b64_json`` 解码写入磁盘；``output`` 为文件路径或目录；``None`` 则写入当前目录带时间戳。"""
    saved: list[Path] = []
    items = list(image_r.data)
    n = len(items)
    for i, item in enumerate(items):
        dec = _decode_generated_image_item(item, b64_fallback_ext=b64_fallback_ext)
        if not dec:
            continue
        raw, ext = dec
        if output:
            out = Path(output)
            if out.exists() and out.is_dir():
                path = out / f"{auto_prefix}_{i}{ext}"
            elif out.suffix:
                if n == 1:
                    path = out if out.suffix.lower() == ext else out.with_suffix(ext)
                else:
                    path = out.parent / f"{out.stem}_{i}{ext}"
            else:
                out.mkdir(parents=True, exist_ok=True)
                path = out / f"{auto_prefix}_{i}{ext}"
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = Path.cwd() / f"{auto_prefix}_{ts}_{i}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        saved.append(path.resolve())
    if not saved and items:
        print(
            "警告: 未能从返回项中解码 base64/data URL（可检查 SDK 返回结构是否为 url/b64_json）。",
            file=sys.stderr,
        )
    return saved


def _print_image_response_items(image_r: Any) -> None:
    full_url = os.environ.get("CURSOR_CLIENT_IMAGE_PRINT_FULL_URL") == "1"
    for i, item in enumerate(image_r.data):
        print(f"--- [{i}] ---")
        u = _item_field(item, "url")
        if u:
            if isinstance(u, str) and u.startswith("data:") and not full_url:
                print(
                    "url:",
                    u[:100]
                    + f"... [data URL 共 {len(u)} 字符，已保存到文件；完整打印设 CURSOR_CLIENT_IMAGE_PRINT_FULL_URL=1]",
                )
            else:
                print("url:", u)
        bj = _item_field(item, "b64_json")
        if bj:
            print("b64_json:", (bj[:160] + "…") if len(bj) > 160 else bj)
        rp = _item_field(item, "revised_prompt")
        if rp:
            print("revised_prompt:", rp)


def create_image(
    prompt: str,
    *,
    n: int = 1,
    size: str = "1024x1024",
    response_format: Literal["url", "b64_json"] = "url",
    model: str | None = None,
    output: Path | str | None = None,
    save_as: Literal["svg", "png"] = "svg",
    image_engine: str | None = None,
    style: str = "none",
    preset: str | None = None,
) -> Any:
    """调用本地网关 ``POST /v1/images/generations``（与 chat 相同客户端）。

    ``image_engine``：``sd_webui`` 须网关已配置 SD WebUI；``agent_interactive`` 走 IDE 同款生图 PNG；
    默认由网关 ``CURSOR_BRIDGE_IMAGE_ENGINE`` 决定。

    ``style``：``vector`` / ``realistic`` / ``editorial`` / ``none`` → ``metadata.image_style``。

    ``preset``：如 ``park_flow_community`` → ``metadata.preset``（可省略 ``prompt``）。

    ``output``：保存路径；``None`` 时当前目录 ``generated_时间戳_i.<ext>``。
    """
    st = (style or "none").strip().lower()
    base_url = os.environ.get("CURSOR_CLIENT_BASE_URL", "http://127.0.0.1:8765/v1")
    api_key = os.environ.get("CURSOR_OPENAI_BRIDGE_API_KEY", "dummy")
    client = OpenAI(base_url=base_url, api_key=api_key)
    kwargs = {
        "prompt": prompt,
        "n": n,
        "size": size,
        "response_format": response_format,
    }
    if model:
        kwargs["model"] = model
    meta = _build_image_metadata(
        style=st,
        image_engine=image_engine,
        preset=preset,
        save_as=save_as,
    )
    eng = _normalize_image_engine(image_engine)
    if meta:
        kwargs["extra_body"] = {"metadata": meta}
    if not (prompt or "").strip() and preset:
        kwargs["prompt"] = "."
    image_r = client.images.generate(**kwargs)
    _print_image_response_items(image_r)
    b64_ext = _b64_ext_for_engine(eng, save_as=save_as)
    paths = save_generated_images_to_files(image_r, output, b64_fallback_ext=b64_ext)
    for p in paths:
        print("已保存:", p)
    return image_r


def _normalize_image_engine(image_engine: str | None) -> str:
    return (image_engine or os.environ.get("CURSOR_CLIENT_IMAGE_ENGINE") or "").strip().lower()


def _build_image_metadata(
    *,
    style: str = "none",
    image_engine: str | None = None,
    preset: str | None = None,
    save_as: Literal["svg", "png"] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if save_as == "png":
        meta["image_export"] = "png"
    st = (style or "none").strip().lower()
    if st in ("vector", "realistic", "editorial"):
        meta["image_style"] = st
    if preset and preset.strip():
        meta["preset"] = preset.strip()
    eng = _normalize_image_engine(image_engine)
    if eng in ("sd_webui", "sd-webui", "sdwebui", "a1111", "webui"):
        meta["image_engine"] = "sd_webui"
    elif eng in (
        "agent_interactive",
        "agent-interactive",
        "agent_tools",
        "interactive",
    ):
        meta["image_engine"] = "agent_interactive"
    return meta


def _b64_ext_for_engine(eng: str, *, save_as: Literal["svg", "png"] = "svg") -> str:
    use_png = save_as == "png" or eng in (
        "sd_webui",
        "sd-webui",
        "sdwebui",
        "a1111",
        "webui",
        "agent_interactive",
        "agent-interactive",
        "agent_tools",
        "interactive",
    )
    return ".png" if use_png else ".svg"


def edit_image(
    image_path: str | Path,
    prompt: str,
    *,
    n: int = 1,
    size: str = "1024x1024",
    response_format: Literal["url", "b64_json"] = "url",
    model: str | None = None,
    output: Path | str | None = None,
    save_as: Literal["svg", "png"] = "png",
    image_engine: str | None = None,
    style: str = "none",
) -> Any:
    """调用本地网关 ``POST /v1/images/edits``（上传参考图 + prompt）。

    ``image_engine``：默认由网关 ``CURSOR_BRIDGE_IMAGE_ENGINE`` 决定（edits 网关默认 ``agent_interactive``）。
    ``style``：``vector`` / ``realistic`` / ``editorial`` / ``none`` → ``metadata.image_style``。
    """
    src = Path(image_path).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"参考图不存在: {src}")

    base_url = os.environ.get("CURSOR_CLIENT_BASE_URL", "http://127.0.0.1:8765/v1")
    api_key = os.environ.get("CURSOR_OPENAI_BRIDGE_API_KEY", "dummy")
    client = OpenAI(base_url=base_url, api_key=api_key)
    meta = _build_image_metadata(style=style, image_engine=image_engine, save_as=save_as)
    eng = _normalize_image_engine(image_engine)
    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "n": n,
        "size": size,
        "response_format": response_format,
    }
    if model:
        kwargs["model"] = model
    if meta:
        kwargs["extra_body"] = {"metadata": meta}

    with src.open("rb") as img_f:
        image_r = client.images.edit(image=img_f, **kwargs)
    _print_image_response_items(image_r)
    b64_ext = _b64_ext_for_engine(eng, save_as=save_as)
    paths = save_generated_images_to_files(
        image_r,
        output,
        auto_prefix="edited",
        b64_fallback_ext=b64_ext,
    )
    for p in paths:
        print("已保存:", p)
    return image_r


def main_image_cli() -> None:
    p = argparse.ArgumentParser(
        description="图像生成：走本地网关 images/generations（Agent→SVG；agent_interactive→终端同款生图 PNG；SD WebUI→扩散 PNG）"
    )
    p.add_argument(
        "prompt",
        nargs="?",
        default="",
        help="画面描述；使用 --preset 时可省略",
    )
    p.add_argument("-n", type=int, default=1, help="生成张数（1–4，由网关与 WebUI 能力决定）")
    p.add_argument("--size", default="1024x1024", help="如 1024x1024、512x512")
    p.add_argument(
        "--response-format",
        choices=("url", "b64_json"),
        default="url",
        dest="response_format",
    )
    p.add_argument("--model", default="cursor-agent", help="传给 cursor agent 的模型（仅 agent 引擎有效）")
    p.add_argument(
        "--engine",
        choices=("agent", "agent_interactive", "sd_webui"),
        default="agent",
        help="写入 metadata.image_engine；agent_interactive=IDE 同款生图工具写 PNG",
    )
    p.add_argument(
        "--style",
        choices=("none", "vector", "realistic", "editorial"),
        default="none",
        help="写入 metadata.image_style（agent=SVG 说明；sd_webui=prompt 英文前缀）",
    )
    p.add_argument(
        "--preset",
        default=None,
        help="内置场景，如 park_flow_community（公园心流社区编辑插画）",
    )
    p.add_argument(
        "-o",
        "--output",
        default=None,
        help="保存解码后的文件：可写具体路径如 ./out.svg；目录如 ./out/；省略则自动 generated_时间戳_i.<ext>",
    )
    p.add_argument(
        "--save-as",
        choices=("svg", "png"),
        default="svg",
        dest="save_as",
        help="svg=仅保存 Agent 的矢量图（默认）；png=服务端将 SVG 栅格化为 PNG（需 rsvg-convert 或 cairosvg）",
    )
    args = p.parse_args()
    if not (args.prompt or "").strip() and not args.preset:
        p.error("请提供 prompt 或 --preset")
    create_image(
        args.prompt or "",
        n=args.n,
        size=args.size,
        response_format=args.response_format,
        model=args.model,
        output=args.output,
        save_as=args.save_as,
        image_engine=args.engine,
        style=args.style,
        preset=args.preset,
    )


def main_image_edit_cli() -> None:
    p = argparse.ArgumentParser(
        description="图像编辑：走本地网关 images/edits（参考图 + prompt；agent_interactive→PNG；sd_webui→img2img）"
    )
    p.add_argument(
        "prompt",
        help="编辑/变换说明",
    )
    p.add_argument(
        "-i",
        "--image",
        required=True,
        help="参考图路径",
    )
    p.add_argument("-n", type=int, default=1, help="生成张数（1–4）")
    p.add_argument("--size", default="1024x1024", help="如 1024x1024")
    p.add_argument(
        "--response-format",
        choices=("url", "b64_json"),
        default="url",
        dest="response_format",
    )
    p.add_argument("--model", default="cursor-agent", help="传给 cursor agent 的模型")
    p.add_argument(
        "--engine",
        choices=("agent", "agent_interactive", "sd_webui"),
        default="agent_interactive",
        help="写入 metadata.image_engine（默认 agent_interactive）",
    )
    p.add_argument(
        "--style",
        choices=("none", "vector", "realistic", "editorial"),
        default="none",
        help="写入 metadata.image_style",
    )
    p.add_argument(
        "-o",
        "--output",
        default=None,
        help="保存路径或目录；省略则自动 edited_时间戳_i.<ext>",
    )
    p.add_argument(
        "--save-as",
        choices=("svg", "png"),
        default="png",
        dest="save_as",
        help="默认 png；agent 引擎产出 SVG 时可设为 svg，或 png 栅格化",
    )
    args = p.parse_args()
    if not (args.prompt or "").strip():
        p.error("请提供 prompt")
    edit_image(
        args.image,
        args.prompt,
        n=args.n,
        size=args.size,
        response_format=args.response_format,
        model=args.model,
        output=args.output,
        save_as=args.save_as,
        image_engine=args.engine,
        style=args.style,
    )


def dispatch() -> None:
    _load_env_file()
    if len(sys.argv) > 1 and sys.argv[1] == "image":
        sys.argv.pop(1)
        main_image_cli()
    elif len(sys.argv) > 1 and sys.argv[1] == "image-edit":
        sys.argv.pop(1)
        main_image_edit_cli()
    elif len(sys.argv) > 1 and sys.argv[1] == "chat":
        sys.argv.pop(1)
        main()
    else:
        main()


if __name__ == "__main__":
    """
    python3 client.py image "一只橘猫" --save-as png -o ./outputs/
    # 网关已设 CURSOR_BRIDGE_IMAGE_ENGINE=sd_webui 且 CURSOR_BRIDGE_SDWEBUI_URL 时，扩散出 PNG：
    python3 client.py image "正面胸像、年轻东亚女性、椭圆脸暖白皮、黑中长发与齐刘海、妆容与腮红、米白圆领针织衫与细项链、上浅紫到下浅蓝的竖向渐变背景、两角半透明浅圆装饰、色块清晰、深灰棕描边感、无照片质感" --style realistic --size 1024x1024 -o ./outputs/
    python3 client.py image "扁平插画" --engine sd_webui --style vector -o ./outputs/
    # 公园心流社区编辑插画（需网关 CURSOR_BRIDGE_SDWEBUI_URL + --engine sd_webui）：

    # 与 IDE 聊天类似的生图（须本机 cursor agent 具备 GenerateImage 等工具）：
    python3 client.py image "photorealistic product shot of a red mug" --engine agent_interactive --style realistic -o ./outputs/mug.png

    # 图像编辑（参考图 + prompt → images/edits）：
    python3 client.py image-edit "Turn into a clean cartoon narrator portrait, preserve identity" \
      -i ./portrait.jpg --engine agent_interactive --style editorial -o ./outputs/cartoon.png

    python3 client.py chat --prompt "## 中文简短说明（给人工修图或国产模型用） \
        - **画幅**：正面胸像，头肩为主。  \
        - **人物**：年轻东亚女性，椭圆脸、暖白皮；黑中长发 + 齐刘海；淡妆 + 明显腮红。  \
        - **服饰**：米白圆领针织衫、细项链。  \
        - **背景**：上浅紫 → 下浅蓝的**竖向渐变**；画面**两角**各一块**半透明浅圆**装饰。  \
        - **画风**：色块利落、边缘带一点**深灰棕描边感**；**不要**照片质感（避免毛孔、胶片颗粒、过写实皮肤）。"
    """
    dispatch()
