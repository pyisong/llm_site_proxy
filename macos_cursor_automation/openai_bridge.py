"""OpenAI Chat Completions 风格 HTTP 网关：请求体兼容多模态，底层调用 ``cursor agent``。

``cursor agent`` 仅接受文本 prompt，因此图像/视频会先写入 ``workspace`` 下临时目录，
再在 prompt 中写明绝对路径，由 Agent 通过读文件/工具处理（取决于模型与 Agent 能力）。

``POST /v1/chat/completions`` 在 ``stream: true`` 时使用 ``cursor agent --output-format stream-json
--stream-partial-output``，将增量文本映射为 OpenAI 兼容的 SSE（``text/event-stream``）。

每次 HTTP 请求在日志侧使用独立 ``req_id``（UUID），并在调用 ``cursor agent`` 时附加 ``--force``，
避免同一 ``workspace`` 下复用上一轮 Agent 会话上下文。

默认（``CURSOR_BRIDGE_ISOLATE_WORKSPACE=1``）将 agent 工作区设为 ``<serve -w>/jobs/<req_id>``，
避免并发或多篇生成共享同一目录导致产物/上下文污染；可用 ``metadata.workspace`` 覆盖，
或 ``metadata.isolate_workspace: false`` / 环境变量 ``CURSOR_BRIDGE_ISOLATE_WORKSPACE=0`` 关闭。

``POST /v1/chat/completions`` 与 ``POST /v1/messages``：请求体根级可选 ``metadata``，其中
``cursor_agent_mode``（或 ``agent_mode``）可覆盖 ``serve --mode``；``writable`` 等值表示不向 CLI 传 ``--mode``，
与 ``agent_interactive`` 默认可写行为一致。

``POST /v1/images/generations``：

- **默认（``agent``）**：由 Cursor Agent 产出 **SVG**；可选 ``metadata.image_export: "png"`` 再栅格化为 PNG
  （需 ``rsvg-convert`` 或 ``cairosvg``）。
- **``sd_webui``**：转发到本机 **Automatic1111** ``/sdapi/v1/txt2img``（真扩散光栅图），需环境变量
  ``CURSOR_BRIDGE_SDWEBUI_URL``；由 ``CURSOR_BRIDGE_IMAGE_ENGINE=sd_webui`` 或请求体
  ``metadata.image_engine: "sd_webui"`` 启用。
- **``agent_interactive``**：与 IDE/终端中 ``cursor agent`` 一致，由模型使用内置生图能力（如 GenerateImage），
  将 PNG 保存到 ``workspace/.cursor_bridge_generated/`` 下约定路径；网关再读文件或解析回复中的 ``data:image/…``
  或 Markdown 图片 URL 作为 ``images`` API 响应（需本机 Cursor CLI 已登录且 Agent 具备生图工具）。
  该路径默认启用子进程**详细日志**（心跳、stderr 行、超时尾部），便于排查长时间无响应。

可选 ``metadata.image_style``（或 ``metadata.style``）：``vector`` / ``realistic`` / ``editorial`` —— 对 **SVG** Agent 附加指引；
对 ``sd_webui`` / ``agent_interactive`` 在英文提示前附加与扩散类似的风格前缀（非 SVG）。

可选 ``metadata.preset``（或 ``image_preset``）：内置场景，如 ``park_flow_community``（公园心流社区编辑插画，
默认横版 ``1344x768``、``editorial`` 风格，推荐 ``sd_webui`` 引擎）。

``POST /v1/images/edits``（OpenAI 兼容 ``images.edit``，``multipart/form-data``）：

- 必填表单字段：``image``（参考图文件）、``prompt``（编辑说明）。
- 可选：``model``、``n``、``size``、``response_format``（``url`` / ``b64_json``）、``metadata``（JSON 字符串，与
  ``generations`` 相同字段：``image_engine``、``image_style``、``workspace`` 等）。
- **``agent_interactive``**（推荐）：将参考图写入工作区，由 Agent 读图并生图，输出 PNG 到约定路径。
- **``sd_webui``**：转发到 **Automatic1111** ``/sdapi/v1/img2img``（``CURSOR_BRIDGE_SDWEBUI_IMG2IMG_DENOISING`` 可调强度）。
- **``agent``**：读参考图路径后产出 **SVG**（可选栅格 PNG）。

环境变量：

- ``CURSOR_OPENAI_BRIDGE_API_KEY``：若设置，则要求 ``Authorization: Bearer <key>``。
- ``CURSOR_BRIDGE_IMAGE_ENGINE``：``agent``（默认）、``agent_interactive`` 或 ``sd_webui``。
- ``CURSOR_BRIDGE_SDWEBUI_URL``：WebUI 根地址，如 ``http://127.0.0.1:7860``。
-   ``CURSOR_BRIDGE_SDWEBUI_NEGATIVE_PROMPT`` / ``CURSOR_BRIDGE_SDWEBUI_STEPS`` / ``CURSOR_BRIDGE_SDWEBUI_CFG_SCALE`` /
  ``CURSOR_BRIDGE_SDWEBUI_TIMEOUT``：可选，调节 txt2img / img2img。
- ``CURSOR_BRIDGE_SDWEBUI_IMG2IMG_DENOISING``：``images/edits`` 走 ``sd_webui`` 时的 denoising strength（默认 ``0.65``）。
- ``CURSOR_BRIDGE_MODELS_CACHE_TTL``：``/v1/models`` 缓存秒数（默认 ``60``）。
- ``CURSOR_BRIDGE_LOG_LEVEL``：详细日志级别（默认 ``INFO``）。
- ``CURSOR_BRIDGE_LOG_DIR``：桥接业务日志目录（默认 ``logs``，写入 ``cursor_openai_bridge.log``；设为空则仅控制台）。
- ``CURSOR_BRIDGE_LOG_FILE_MAX_BYTES`` / ``CURSOR_BRIDGE_LOG_FILE_BACKUP_COUNT``：业务日志轮转大小与备份个数（默认 ``10485760``、``5``）。
- ``CURSOR_BRIDGE_LOG_MAX_CHARS``：单条日志最大字符（默认 ``8192``）；``0`` 表示不截断（大 base64 慎用）。
- ``CURSOR_BRIDGE_LOG_AGENT_PROMPT``：设为 ``1`` 时额外打印发给 agent 的完整 prompt（同样受 MAX_CHARS 截断）。
- ``CURSOR_BRIDGE_LOG_AGENT_SUBPROCESS``：设为 ``1`` 时对所有 ``run_cursor_agent`` 使用 ``Popen`` 监控：心跳、超时杀进程并打 stdout/stderr 尾部。
- ``CURSOR_BRIDGE_AGENT_PROGRESS_INTERVAL_SEC``：子进程监控心跳间隔（秒，默认 ``30``，最小 ``5``）。
- ``CURSOR_BRIDGE_AGENT_SUBPROCESS_LIVE_STDERR``：设为 ``1`` 时在监控模式下逐行 ``INFO`` 打印子进程 stderr（日志量大）。
- ``CURSOR_BRIDGE_AGENT_PIPE_READ_CHUNK``：监控模式下读 stdout/stderr 的块大小（字节，默认 ``65536``）；勿过小。
- ``CURSOR_BRIDGE_ISOLATE_WORKSPACE``：设为 ``0``/``false`` 时不在 ``jobs/<req_id>`` 下隔离工作区（默认 ``1`` 开启）。
- ``CURSOR_BRIDGE_LOG_IMAGE_B64_PREVIEW``：``images/generations`` 响应日志里 ``url``/``b64_json`` 中 base64
  只保留前 N 个字符（默认 ``96``）；设 ``CURSOR_BRIDGE_LOG_IMAGE_FULL=1`` 则不打码（慎用）。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

try:
    from .cursor_automation import (
        AgentMode,
        agent_completion_text,
        agent_stream_object_text,
        agen_cursor_agent_stream_json,
        fetch_cursor_agent_models,
        normalize_agent_mode,
        resolve_cursor_cli,
        run_cursor_agent,
    )
    from .log_utils import TruncatingLogFormatter, truncate_log_text
except ImportError:
    from cursor_automation import (
        AgentMode,
        agent_completion_text,
        agent_stream_object_text,
        agen_cursor_agent_stream_json,
        fetch_cursor_agent_models,
        normalize_agent_mode,
        resolve_cursor_cli,
        run_cursor_agent,
    )
    from log_utils import TruncatingLogFormatter, truncate_log_text

try:
    from .image_generation import (
        augment_prompt_for_style,
        fetch_image_url,
        merge_negative_prompts,
        resolve_image_request,
        sd_webui_img2img_png,
        sd_webui_txt2img_png,
    )
except ImportError:
    from image_generation import (
        augment_prompt_for_style,
        fetch_image_url,
        merge_negative_prompts,
        resolve_image_request,
        sd_webui_img2img_png,
        sd_webui_txt2img_png,
    )

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

MediaKind = Literal["image", "video"]

_models_cache_lock = asyncio.Lock()
_models_cache: dict[str, Any] = {"ts": 0.0, "data": None}

_bridge_logger: logging.Logger | None = None


def _init_bridge_logging() -> logging.Logger:
    global _bridge_logger
    if _bridge_logger is not None:
        return _bridge_logger
    log = logging.getLogger("cursor_openai_bridge")
    if not log.handlers:
        level_name = os.environ.get("CURSOR_BRIDGE_LOG_LEVEL", "INFO").upper()
        log.setLevel(getattr(logging, level_name, logging.INFO))
        fmt = TruncatingLogFormatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(filename)s:%(lineno)d | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        log.addHandler(h)

        log_dir_raw = (os.environ.get("CURSOR_BRIDGE_LOG_DIR", "logs") or "").strip()
        if log_dir_raw:
            try:
                log_dir = Path(log_dir_raw).expanduser()
                log_dir.mkdir(parents=True, exist_ok=True)
                try:
                    max_b = int(os.environ.get("CURSOR_BRIDGE_LOG_FILE_MAX_BYTES", str(10 * 1024 * 1024)))
                except ValueError:
                    max_b = 10 * 1024 * 1024
                try:
                    bk = int(os.environ.get("CURSOR_BRIDGE_LOG_FILE_BACKUP_COUNT", "5"))
                except ValueError:
                    bk = 5
                fp = log_dir / "cursor_openai_bridge.log"
                fh = logging.handlers.RotatingFileHandler(
                    fp,
                    maxBytes=max(1024, max_b),
                    backupCount=max(1, bk),
                    encoding="utf-8",
                )
                fh.setFormatter(fmt)
                log.addHandler(fh)
            except OSError as e:
                sys.stderr.write(
                    f"[cursor_openai_bridge] WARNING: 无法写入日志目录 {log_dir_raw!r}: {e}\n"
                )
    log.propagate = False
    _bridge_logger = log
    return log


def _headers_for_log(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in ("authorization", "cookie", "set-cookie"):
            if lk == "authorization" and v.lower().startswith("bearer "):
                out[k] = "Bearer ***redacted***"
            else:
                out[k] = "***redacted***"
        else:
            out[k] = v
    return out


def _serialize_for_log(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except TypeError:
        return repr(obj)


def _truncate_log_text(text: str) -> str:
    return truncate_log_text(text)


def _redact_data_url_for_log(s: str, b64_preview: int) -> str:
    """将 ``data:...;base64,...`` 中的 payload 截断为日志用短串。"""
    if not s.startswith("data:") or ";base64," not in s:
        if len(s) > 512:
            return s[:256] + f"… [url 共 {len(s)} 字符]"
        return s
    prefix, _, b64part = s.partition(";base64,")
    if len(b64part) <= b64_preview:
        return s
    return (
        f"{prefix};base64,{b64part[:b64_preview]}… "
        f"[base64 共 {len(b64part)} 字符，完整日志设 CURSOR_BRIDGE_LOG_IMAGE_FULL=1]"
    )


def _redact_image_api_payload_for_log(obj: Any) -> Any:
    """递归缩短 ``images`` 响应中的 ``url`` / ``b64_json``，避免日志刷满 megabytes。"""
    if os.environ.get("CURSOR_BRIDGE_LOG_IMAGE_FULL", "").strip() == "1":
        return obj
    try:
        preview = int(os.environ.get("CURSOR_BRIDGE_LOG_IMAGE_B64_PREVIEW", "96") or "96")
    except ValueError:
        preview = 96
    preview = max(16, min(preview, 4096))

    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            out: dict[str, Any] = {}
            for k, v in o.items():
                if k == "url" and isinstance(v, str):
                    out[k] = _redact_data_url_for_log(v, preview)
                elif k == "b64_json" and isinstance(v, str) and len(v) > preview:
                    out[k] = (
                        f"{v[:preview]}… "
                        f"[共 {len(v)} 字符，完整设 CURSOR_BRIDGE_LOG_IMAGE_FULL=1]"
                    )
                else:
                    out[k] = walk(v)
            return out
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o

    return walk(obj)


def _serialize_images_api_for_log(obj: Any) -> str:
    """``/v1/images/generations`` 专用：序列化前脱敏大图字段，再套全局字符截断。"""
    return _truncate_log_text(_serialize_for_log(_redact_image_api_payload_for_log(obj)))


def _chat_completion_log_request(request: Request, req_id: str, body: Any, *, note: str = "") -> None:
    log = _init_bridge_logging()
    ip = request.client.host if request.client else "-"
    hdrs = _truncate_log_text(_serialize_for_log(_headers_for_log(request)))
    body_s = _truncate_log_text(_serialize_for_log(body)) if body is not None else "<null>"
    log.info(
        "chat/completions BEGIN id=%s client=%s %s %s\nHEADERS_JSON:\n%s\nREQUEST_BODY:\n%s\n%s",
        req_id,
        ip,
        request.method,
        request.url.path,
        hdrs,
        body_s,
        note,
    )


def _chat_completion_log_response(request: Request, req_id: str, http_status: int, payload: Any) -> None:
    log = _init_bridge_logging()
    ip = request.client.host if request.client else "-"
    ps = _truncate_log_text(_serialize_for_log(payload))
    log.info(
        "chat/completions END id=%s client=%s http_status=%s\nRESPONSE_BODY:\n%s",
        req_id,
        ip,
        http_status,
        ps,
    )


def _anthropic_messages_log_request(request: Request, req_id: str, body: Any, *, note: str = "") -> None:
    log = _init_bridge_logging()
    ip = request.client.host if request.client else "-"
    hdrs = _truncate_log_text(_serialize_for_log(_headers_for_log(request)))
    body_s = _truncate_log_text(_serialize_for_log(body)) if body is not None else "<null>"
    log.info(
        "messages BEGIN id=%s client=%s %s %s\nHEADERS_JSON:\n%s\nREQUEST_BODY:\n%s\n%s",
        req_id,
        ip,
        request.method,
        request.url.path,
        hdrs,
        body_s,
        note,
    )


def _anthropic_messages_log_response(
    request: Request, req_id: str, http_status: int, payload: Any
) -> None:
    log = _init_bridge_logging()
    ip = request.client.host if request.client else "-"
    ps = _truncate_log_text(_serialize_for_log(payload))
    log.info(
        "messages END id=%s client=%s http_status=%s\nRESPONSE_BODY:\n%s",
        req_id,
        ip,
        http_status,
        ps,
    )


def _chat_sse_chunk(
    *,
    cid: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> bytes:
    choice: dict[str, Any] = {
        "index": 0,
        "delta": delta,
        "logprobs": None,
        "finish_reason": finish_reason,
    }
    payload: dict[str, Any] = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [choice],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _openai_error(message: str, type_: str = "invalid_request_error", code: str | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"message": message, "type": type_}
    if code is not None:
        err["code"] = code
    return {"error": err}


def _anthropic_error(message: str, type_: str = "invalid_request_error") -> dict[str, Any]:
    return {"type": "error", "error": {"type": type_, "message": message}}


def _parse_data_url(url: str) -> tuple[bytes, str | None]:
    m = re.match(r"^data:([^;,]+)?(;base64)?,(.+)$", url, re.DOTALL)
    if not m:
        raise ValueError("非法 data URL")
    mime = (m.group(1) or "").strip() or None
    is_b64 = bool(m.group(2))
    payload = m.group(3) or ""
    if is_b64:
        try:
            raw = base64.b64decode(payload, validate=True)
        except binascii.Error as e:
            raise ValueError("base64 解码失败") from e
    else:
        from urllib.parse import unquote_to_bytes

        raw = unquote_to_bytes(payload)
    return raw, mime


def _suffix_for(mime: str | None, kind: MediaKind, url_hint: str) -> str:
    if mime:
        ext = mimetypes.guess_extension(mime.split(";")[0].strip())
        if ext:
            return ext
    low = url_hint.lower()
    if kind == "video":
        for ext in (".mp4", ".webm", ".mov", ".mkv", ".m4v"):
            if low.endswith(ext):
                return ext
        return ".mp4"
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        if low.endswith(ext):
            return ext
    return ".bin"


async def _materialize_url(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    r = await client.get(url, follow_redirects=True, timeout=httpx.Timeout(300.0))
    r.raise_for_status()
    dest.write_bytes(r.content)


def _materialize_data_url(url: str, dest: Path) -> str | None:
    raw, mime = _parse_data_url(url)
    dest.write_bytes(raw)
    return mime


_REQ_ID_DIR_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _workspace_isolation_enabled() -> bool:
    raw = (os.environ.get("CURSOR_BRIDGE_ISOLATE_WORKSPACE", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _job_workspace_dir(default: Path, req_id: str) -> Path:
    safe = _REQ_ID_DIR_SAFE.sub("_", (req_id or "").strip()).strip("._") or "unknown"
    return default.expanduser().resolve() / "jobs" / safe


def _normalize_workspace(
    body: dict[str, Any],
    default: Path,
    *,
    req_id: str | None = None,
) -> Path:
    meta = body.get("metadata")
    if isinstance(meta, dict):
        w = meta.get("workspace") or meta.get("workspace_path")
        if isinstance(w, str) and w.strip():
            return Path(w).expanduser().resolve()
        if meta.get("isolate_workspace") is False:
            return default.expanduser().resolve()
    if req_id and _workspace_isolation_enabled():
        return _job_workspace_dir(default, req_id)
    return default.expanduser().resolve()


def _extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n".join(texts).strip()


def _messages_to_prompt(messages: list[dict[str, Any]], media_lines: list[str]) -> str:
    blocks: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        if role not in ("system", "user", "assistant", "tool"):
            role = "user"
        content = m.get("content")
        text = _extract_text_from_content(content)
        if role == "tool":
            text = f"[tool result]\n{text}"
        blocks.append(f"### {role.upper()}\n{text}".strip())
    base = "\n\n".join(blocks)
    if media_lines:
        media_block = "[ATTACHED_MEDIA]\n" + "\n".join(media_lines)
        base = media_block + "\n\n" + base
    base += (
        "\n\n请根据以上对话与附件路径作答；若附件为图像或视频文件，请结合文件内容回答。"
    )
    return base.strip()


def _parse_image_size(size: str) -> tuple[str, str]:
    s = (size or "1024x1024").strip().lower().replace("*", "x")
    parts = re.split(r"x", s, maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return parts[0], parts[1]
    return "1024", "1024"


def _svg_generation_agent_prompt(user_prompt: str, w: str, h: str) -> str:
    return (
        "You must output ONLY a single valid SVG document. Do not use markdown code fences. "
        "Do not add any explanation before or after the XML.\n\n"
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f" ... </svg>\n\n"
        f"Depict the following (use clear shapes and colors, close all tags):\n{user_prompt.strip()}\n"
    )


def _rsvg_convert_executable() -> str | None:
    """``rsvg-convert`` 路径；``which`` 失败时尝试常见 Homebrew 路径（IDE 子进程 PATH 常不含 /opt/homebrew/bin）。"""
    w = shutil.which("rsvg-convert")
    if w:
        return w
    for p in ("/opt/homebrew/bin/rsvg-convert", "/usr/local/bin/rsvg-convert"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def export_svg_to_png(svg_bytes: bytes, width: int, height: int) -> bytes:
    """将 SVG 光栅化为 PNG（需 ``rsvg-convert`` 或 Python 包 ``cairosvg``）。"""
    if width < 1 or height < 1:
        raise ValueError("栅格化宽高须为正整数")
    rsvg = _rsvg_convert_executable()
    if rsvg:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            inp = dpath / "in.svg"
            outp = dpath / "out.png"
            inp.write_bytes(svg_bytes)
            cp = subprocess.run(
                [rsvg, "-w", str(width), "-h", str(height), "-o", str(outp), str(inp)],
                capture_output=True,
                timeout=120,
            )
            if cp.returncode != 0 or not outp.is_file():
                err = (cp.stderr or cp.stdout or b"").decode("utf-8", errors="replace")
                raise RuntimeError(f"rsvg-convert 失败: {err[:2000]}")
            return outp.read_bytes()
    try:
        import cairosvg  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError(
            "栅格化为 PNG 需要：系统 PATH 中的 rsvg-convert（推荐 macOS: brew install librsvg，"
            "并保证启动 serve 的环境 PATH 含 /opt/homebrew/bin），或 pip install cairosvg（另需 arm64 的 brew install cairo）。"
        ) from e
    except OSError as e:
        # cairosvg 已装但 cairocffi 加载 libcairo 失败（常见：/usr/local 下 x86_64 dylib 与 arm64 Python 冲突）
        raise RuntimeError(
            "cairosvg 无法加载系统 Cairo 库（多为架构不匹配或未安装 arm64 cairo）。"
            "建议：brew install cairo librsvg，确认 which rsvg-convert 指向 /opt/homebrew/bin/rsvg-convert；"
            "或移除冲突的 /usr/local 旧版 cairo。详情: "
            + str(e)[:1500]
        ) from e
    try:
        png = cairosvg.svg2png(bytestring=svg_bytes, output_width=width, output_height=height)
    except OSError as e:
        raise RuntimeError("cairosvg 渲染失败: " + str(e)[:1500]) from e
    if not png:
        raise RuntimeError("cairosvg.svg2png 未返回 PNG 字节")
    return png


def _extract_svg_from_agent_text(agent_text: str) -> str:
    t = (agent_text or "").strip()
    m = re.search(r"```(?:svg|xml)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    if m:
        t = m.group(1).strip()
    low = t.lower()
    i = low.find("<svg")
    if i < 0:
        return ""
    t = t[i:]
    j = t.lower().rfind("</svg>")
    if j < 0:
        return t.strip()
    return t[: j + len("</svg>")].strip()


def _interactive_image_agent_prompt(
    user_prompt: str,
    w: str,
    h: str,
    out_path: Path,
    *,
    aspect_mode: str = "fixed",
    content_kind: str = "general",
) -> str:
    """与 IDE 聊天类似：要求 Agent 使用内置生图能力并将 PNG 落到约定路径。"""
    p = out_path.expanduser().resolve()
    ck = (content_kind or "general").strip().lower()
    if ck == "tech":
        framing = (
            "Technical diagram: keep ALL boxes, arrows and labels fully inside the frame with at least 12% margin on every side. "
            "Scale the entire diagram down to about 75-80% of the canvas, centered. "
            "Use at most 4-6 very short labels; prefer icons and English abbreviations. "
            "No cropped or cut-off text at the edges.\n\n"
        )
    elif (aspect_mode or "").strip().lower() == "auto":
        framing = (
            "Framing: choose horizontal, vertical, or square composition that best fits the brief. "
            "Do not crop away essential subjects to force a fixed aspect ratio. "
            f"If the tool needs a canvas, prefer preserving full scene content over strict {w}x{h} cropping.\n\n"
        )
    else:
        framing = f"Target size hint: about {w} x {h} pixels (match aspect ratio when the tool allows).\n\n"
    return (
        "Task: generate exactly ONE raster image file (PNG) from the creative brief below.\n\n"
        "Use the same built-in image generation capability you have in the Cursor IDE chat "
        "(for example a GenerateImage-style tool, if available). "
        "Do not use SVG as the only deliverable; the user needs a saved PNG file on disk.\n\n"
        f"{framing}"
        f"Save the final image to this exact absolute path (create parent directories if needed):\n{p}\n\n"
        "Creative brief:\n"
        f"{user_prompt.strip()}\n"
    )


def _interactive_image_edit_agent_prompt(
    user_prompt: str,
    w: str,
    h: str,
    reference_image_path: Path,
    out_path: Path,
    *,
    aspect_mode: str = "fixed",
) -> str:
    """基于参考图的 agent_interactive 生图：要求 Agent 读参考图并输出编辑后的 PNG。"""
    ref = reference_image_path.expanduser().resolve()
    out = out_path.expanduser().resolve()
    if (aspect_mode or "").strip().lower() == "auto":
        framing = (
            "Framing: preserve essential subjects from the reference; do not crop away identity-defining features. "
            f"Prefer matching the reference composition when the tool allows.\n\n"
        )
    else:
        framing = f"Target size hint: about {w} x {h} pixels (match aspect ratio when the tool allows).\n\n"
    return (
        "Task: transform the reference image into exactly ONE new raster PNG following the creative brief below.\n\n"
        "Reference image (read this file first; preserve recognizable identity when the brief requires it):\n"
        f"{ref}\n\n"
        "Use the same built-in image generation capability you have in the Cursor IDE chat "
        "(for example GenerateImage-style tool with reference_image_paths, if available). "
        "Do not use SVG as the only deliverable; the user needs a saved PNG file on disk.\n\n"
        f"{framing}"
        f"Save the final image to this exact absolute path (create parent directories if needed):\n{out}\n\n"
        "Creative brief:\n"
        f"{user_prompt.strip()}\n"
    )


def _svg_edit_generation_agent_prompt(
    user_prompt: str,
    w: str,
    h: str,
    reference_image_path: Path,
) -> str:
    ref = reference_image_path.expanduser().resolve()
    return (
        "Task: read the reference image at this absolute path and create ONE standalone SVG "
        f"with viewBox 0 0 {w} {h} following the creative brief.\n\n"
        f"Reference image:\n{ref}\n\n"
        "Output valid SVG markup containing <svg>...</svg>.\n\n"
        "Creative brief:\n"
        f"{user_prompt.strip()}\n"
    )


def _parse_form_metadata(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _form_field_str(raw: Any, default: str = "") -> str:
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw
    return str(raw)


def _form_field_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _image_item_from_bytes(raw_bytes: bytes, *, response_format: str) -> dict[str, str]:
    mime = _mime_for_raster_bytes(raw_bytes)
    b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
    if response_format == "b64_json":
        return {"b64_json": b64}
    return {"url": f"data:{mime};base64,{b64}"}


async def _read_form_upload_bytes(upload: Any) -> tuple[bytes, str]:
    if upload is None:
        raise ValueError("image 缺失")
    if hasattr(upload, "read"):
        data = await upload.read()
        filename = getattr(upload, "filename", "") or ""
    elif isinstance(upload, (bytes, bytearray)):
        data = bytes(upload)
        filename = ""
    else:
        raise ValueError("image 须为上传文件")
    if len(data) < 16:
        raise ValueError("image 文件过小或为空")
    ext = Path(filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        mime = _mime_for_raster_bytes(data)
        ext = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }.get(mime, ".png")
    return data, ext


def _decode_data_url_to_image_bytes(data_url: str) -> bytes | None:
    url = (data_url or "").strip()
    if not url.lower().startswith("data:image/"):
        return None
    if ";base64," not in url:
        return None
    try:
        b64part = url.split(",", 1)[1].strip()
        return base64.standard_b64decode(b64part)
    except (IndexError, ValueError, binascii.Error):
        return None


def _mime_for_raster_bytes(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(raw) >= 2 and raw[:2] == b"\xff\xd8":
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _bytes_from_expected_or_reply(
    *,
    expected_png: Path,
    agent_text: str,
    raw_stdout: str,
    workspace: Path | None = None,
    search_since: float | None = None,
) -> bytes | None:
    """优先读约定 PNG；否则从正文 / stdout 解析 data URL、https 图链、绝对路径文件。"""
    ep = expected_png.expanduser().resolve()
    if ep.is_file():
        try:
            return ep.read_bytes()
        except OSError:
            pass
    combined = f"{agent_text or ''}\n{raw_stdout or ''}"
    for m in re.finditer(
        r"data:image/(?:png|jpeg|jpg|webp);base64,[A-Za-z0-9+/=\s]+",
        combined,
        re.IGNORECASE,
    ):
        b = _decode_data_url_to_image_bytes(re.sub(r"\s+", "", m.group(0)))
        if b and len(b) > 200:
            return b
    for m in re.finditer(r"https?://[^\s)<>\"']+", combined):
        url = m.group(0).rstrip(").,]")
        low = url.lower()
        if not any(low.split("?", 1)[0].endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
            continue
        try:
            raw, _ext = fetch_image_url(url)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            continue
        if raw and len(raw) > 200:
            return raw
    for m in re.finditer(r"(?:file://)?(/[^\s`'\"<>|]+\.(?:png|jpe?g|webp))\b", combined, re.IGNORECASE):
        path_s = m.group(1)
        pp = Path(path_s)
        if pp.is_file():
            try:
                return pp.read_bytes()
            except OSError:
                continue
    if workspace is not None and search_since is not None:
        root = workspace.expanduser().resolve()
        best_mtime = 0.0
        best_path: Path | None = None
        try:
            candidates = root.rglob("*")
        except OSError:
            candidates = ()
        for fp in candidates:
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_mtime < search_since or st.st_size < 200:
                continue
            if st.st_mtime > best_mtime:
                best_mtime = st.st_mtime
                best_path = fp
        if best_path is not None:
            try:
                return best_path.read_bytes()
            except OSError:
                pass
    return None


def _agent_interactive_refusal_hint(agent_text: str, raw_stdout: str) -> str | None:
    combined = f"{agent_text or ''}\n{raw_stdout or ''}"
    if ("Ask 模式" in combined or "ask mode" in combined.lower()) and (
        "生图" in combined or "GenerateImage" in combined or "不能" in combined
    ):
        return (
            "Cursor Agent 处于 Ask 只读模式，无法调用生图工具。"
            " agent_interactive 须使用默认可写 Agent（不要对 serve 传 --mode ask；"
            " 网关内部已改为不传 --mode）。"
        )
    return None


def _extract_agent_error_message(result: Any) -> str | None:
    """从 Cursor Agent 返回中提取结构化错误信息（若存在）。"""
    parsed = getattr(result, "parsed", None)
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        if isinstance(err, str) and err.strip():
            return err.strip()
        if str(parsed.get("type", "")).lower().endswith("error"):
            msg = parsed.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()

    text = agent_completion_text(result).strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict):
            err = obj.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
            if isinstance(err, str) and err.strip():
                return err.strip()
    return None


async def _save_all_media(
    workspace: Path,
    request_id: str,
    messages: list[dict[str, Any]],
    client: httpx.AsyncClient,
) -> tuple[list[str], Path]:
    """返回 (prompt 中展示的媒体说明行, 临时目录路径)。"""
    media_root = workspace / ".cursor_openai_bridge_media" / request_id
    media_root.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    idx = 0
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            url: str | None = None
            kind: MediaKind = "image"
            if ptype == "image_url":
                iu = part.get("image_url")
                if isinstance(iu, dict):
                    url = iu.get("url") if isinstance(iu.get("url"), str) else None
                kind = "image"
            elif ptype in ("input_video", "video_url", "video"):
                block = part.get("input_video") or part.get("video_url") or part.get("video")
                if isinstance(block, dict):
                    url = block.get("url") if isinstance(block.get("url"), str) else None
                kind = "video"
            if not url:
                continue
            idx += 1
            dest_name = f"{'img' if kind == 'image' else 'vid'}_{idx}"
            if url.startswith("data:"):
                mime = _materialize_data_url(url, media_root / f"{dest_name}.tmp")
                suf = _suffix_for(mime, kind, url)
                final = media_root / f"{dest_name}{suf}"
                (media_root / f"{dest_name}.tmp").rename(final)
                lines.append(f"- ({kind}) file://{final} mime={mime or 'unknown'}")
            elif url.startswith(("http://", "https://")):
                suf = _suffix_for(None, kind, url)
                final = media_root / f"{dest_name}{suf}"
                await _materialize_url(client, url, final)
                lines.append(f"- ({kind}) file://{final} source_url={url}")
            else:
                raise ValueError(f"不支持的 URL 协议: {url[:48]}")
    return lines, media_root


def _cursor_cli_mode_from_request_metadata(
    metadata: Any,
    *,
    default_mode: AgentMode,
) -> Literal["plan", "ask"] | None:
    """metadata.cursor_agent_mode / agent_mode：与 serve --mode 及 agent_interactive 对齐。"""
    if not isinstance(metadata, dict):
        return normalize_agent_mode(default_mode)
    raw = metadata.get("cursor_agent_mode")
    if raw is None:
        raw = metadata.get("agent_mode")
    if raw is None:
        return normalize_agent_mode(default_mode)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("writable", "full", "tools", "auto", "default", "none", "omit"):
            return None
        if s == "agent":
            return normalize_agent_mode("agent")
        if s in ("ask", "plan"):
            return s  # type: ignore[return-value]
    return normalize_agent_mode(default_mode)


async def _openai_models_payload() -> list[dict[str, Any]]:
    """聚合网关占位模型与 ``cursor agent models`` 输出（带短时缓存）。"""
    ttl = float(os.environ.get("CURSOR_BRIDGE_MODELS_CACHE_TTL", "60"))
    async with _models_cache_lock:
        now = time.time()
        cached = _models_cache["data"]
        if cached is not None and now - float(_models_cache["ts"]) < ttl:
            return cached

        cli_rows = await asyncio.to_thread(fetch_cursor_agent_models)
        created = int(time.time())
        gateway = {
            "id": "cursor-agent",
            "object": "model",
            "created": created,
            "owned_by": "cursor",
        }
        if not cli_rows:
            data = [gateway]
        else:
            data = [gateway] + [m for m in cli_rows if m.get("id") != "cursor-agent"]
        _models_cache["ts"] = time.time()
        _models_cache["data"] = data
        return data


def _normalize_model_for_cursor(model: Any) -> str | None:
    if not isinstance(model, str):
        return None
    m = model.strip()
    if not m or m in ("cursor-agent", "auto", "default"):
        return None
    if m.startswith("cursor/"):
        m = m.split("/", 1)[1]
    return m or None


def _anthropic_system_to_text(system: Any) -> str:
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        parts: list[str] = []
        for blk in system:
            if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                parts.append(blk["text"])
        return "\n".join([p for p in parts if p.strip()]).strip()
    return ""


def _anthropic_content_to_openai_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out: list[dict[str, Any]] = []
    for blk in content:
        if not isinstance(blk, dict):
            continue
        btype = blk.get("type")
        if btype == "text" and isinstance(blk.get("text"), str):
            out.append({"type": "text", "text": blk["text"]})
            continue
        if btype == "image":
            src = blk.get("source")
            if not isinstance(src, dict):
                continue
            st = src.get("type")
            if st == "base64":
                mt = src.get("media_type") or "image/png"
                data = src.get("data") if isinstance(src.get("data"), str) else ""
                if data:
                    out.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"}})
            elif st == "url":
                url = src.get("url") if isinstance(src.get("url"), str) else ""
                if url:
                    out.append({"type": "image_url", "image_url": {"url": url}})
    if not out:
        return ""
    return out


def create_app(
    *,
    default_workspace: Path,
    agent_mode: AgentMode = "ask",
    agent_timeout: float = 600.0,
) -> FastAPI:
    default_workspace = default_workspace.expanduser().resolve()
    expected_key = os.environ.get("CURSOR_OPENAI_BRIDGE_API_KEY")
    _init_bridge_logging()

    app = FastAPI(title="Cursor OpenAI Bridge", version="0.1.0")
    # 兼容浏览器端直连：允许 CORS 预检 OPTIONS 通过，避免 405。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if expected_key and request.url.path.startswith("/v1/"):
            auth = request.headers.get("authorization") or request.headers.get("Authorization")
            x_api_key = request.headers.get("x-api-key") or request.headers.get("X-Api-Key")
            auth_ok = auth == f"Bearer {expected_key}" or x_api_key == expected_key
            if not auth_ok:
                return JSONResponse(
                    status_code=401,
                    content=_openai_error("Invalid API key", type_="authentication_error"),
                )
        return await call_next(request)

    @app.get("/health")
    async def health():
        ok = resolve_cursor_cli() is not None
        return {"ok": ok, "cursor_cli": bool(ok)}

    @app.get("/v1/models")
    async def models():
        data = await _openai_models_payload()
        return {"object": "list", "data": data}

    @app.get("/api/v1/models")
    async def models_compat_api():
        data = await _openai_models_payload()
        return {"object": "list", "data": data}

    @app.get("/v1/models/{model_id}")
    async def model_get(model_id: str):
        mid = (model_id or "").strip() or "cursor-agent"
        if mid == "auto":
            mid = "cursor-agent"
        data = await _openai_models_payload()
        for item in data:
            if isinstance(item, dict) and item.get("id") == mid:
                return item
        return {
            "id": mid,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "cursor",
        }

    @app.get("/version")
    async def version_compat():
        return {"version": "0.1.0", "name": "cursor-openai-bridge"}

    @app.get("/props")
    async def props_compat():
        return {
            "provider": "openai_compatible_bridge",
            "supports": {
                "chat_completions": True,
                "messages": True,
                "stream": True,
            },
        }

    @app.get("/v1/props")
    async def v1_props_compat():
        return await props_compat()

    @app.get("/api/tags")
    async def tags_compat():
        data = await _openai_models_payload()
        tags: list[dict[str, Any]] = []
        for m in data:
            mid = m.get("id") if isinstance(m, dict) else None
            if isinstance(mid, str) and mid:
                tags.append({"name": mid})
        return {"models": tags}

    @app.post("/v1/images/generations")
    async def images_generations(request: Request):
        """OpenAI 兼容：由 ``cursor agent`` 产出 SVG，映射为 images 响应。"""
        req_id = str(uuid.uuid4())
        try:
            raw = await request.body()
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content=_openai_error(f"读取请求体失败: {e}", type_="api_error"),
            )
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(
                status_code=400,
                content=_openai_error("请求体须为 JSON"),
            )
        _init_bridge_logging().info(
            "images/generations BEGIN id=%s\n%s",
            req_id,
            _serialize_images_api_for_log(body),
        )

        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content=_openai_error("请求体格式错误"))

        raw_prompt = body.get("prompt")
        if raw_prompt is not None and not isinstance(raw_prompt, str):
            return JSONResponse(
                status_code=400,
                content=_openai_error("prompt 须为字符串"),
            )
        prompt_str = (raw_prompt or "").strip() if isinstance(raw_prompt, str) else ""
        meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else None
        has_preset = bool(
            meta
            and (
                (isinstance(meta.get("preset"), str) and meta.get("preset", "").strip())
                or (
                    isinstance(meta.get("image_preset"), str)
                    and meta.get("image_preset", "").strip()
                )
            )
        )
        if not prompt_str and not has_preset:
            return JSONResponse(
                status_code=400,
                content=_openai_error("prompt 必须为非空字符串（或使用 metadata.preset）"),
            )
        n = body.get("n", 1)
        if not isinstance(n, int) or n < 1 or n > 4:
            return JSONResponse(
                status_code=400,
                content=_openai_error("n 须为 1 到 4 的整数"),
            )
        response_format = body.get("response_format") or "url"
        if response_format not in ("url", "b64_json"):
            return JSONResponse(
                status_code=400,
                content=_openai_error("response_format 须为 url 或 b64_json"),
            )
        size = body.get("size") if isinstance(body.get("size"), str) else "1024x1024"
        model = body.get("model") or "cursor-agent"
        agent_model = None if model == "cursor-agent" else str(model)

        workspace = _normalize_workspace(body, default_workspace, req_id=req_id)
        workspace.mkdir(parents=True, exist_ok=True)
        if _workspace_isolation_enabled() and workspace != default_workspace.expanduser().resolve():
            _init_bridge_logging().debug(
                "images/generations workspace id=%s path=%s",
                req_id,
                workspace,
            )

        env_engine = (os.environ.get("CURSOR_BRIDGE_IMAGE_ENGINE") or "agent").strip().lower()
        try:
            resolved = resolve_image_request(
                prompt=prompt_str,
                size=size,
                metadata=meta,
                env_engine=env_engine,
            )
        except ValueError as e:
            pl = _openai_error(str(e), type_="invalid_request_error")
            _init_bridge_logging().info(
                "images/generations END id=%s status=400\n%s",
                req_id,
                _truncate_log_text(_serialize_for_log(pl)),
            )
            return JSONResponse(status_code=400, content=pl)

        prompt = resolved.prompt
        size = resolved.size
        w, h = _parse_image_size(size)
        export_png = resolved.export_png
        image_style = resolved.image_style
        engine = resolved.engine

        if engine == "sd_webui":
            base = os.environ.get("CURSOR_BRIDGE_SDWEBUI_URL", "").strip().rstrip("/")
            if not base:
                pl = _openai_error(
                    "sd_webui 引擎需要配置 CURSOR_BRIDGE_SDWEBUI_URL（Stable Diffusion WebUI 根 URL）",
                    type_="api_error",
                )
                _init_bridge_logging().info(
                    "images/generations END id=%s status=503\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log(pl)),
                )
                return JSONResponse(status_code=503, content=pl)
            neg = merge_negative_prompts(
                os.environ.get("CURSOR_BRIDGE_SDWEBUI_NEGATIVE_PROMPT", "") or "",
                resolved.negative_prompt_extra,
            )
            try:
                steps = int(float(os.environ.get("CURSOR_BRIDGE_SDWEBUI_STEPS", "28")))
            except ValueError:
                steps = 28
            try:
                cfg_scale = float(os.environ.get("CURSOR_BRIDGE_SDWEBUI_CFG_SCALE", "7"))
            except ValueError:
                cfg_scale = 7.0
            try:
                sd_timeout = float(os.environ.get("CURSOR_BRIDGE_SDWEBUI_TIMEOUT", "600"))
            except ValueError:
                sd_timeout = 600.0

            styled = augment_prompt_for_style(
                prompt,
                image_style
                if image_style in ("vector", "realistic", "editorial")
                else "none",
                target="diffusion",
            )

            def run_sd_once() -> dict[str, str]:
                png_bytes = sd_webui_txt2img_png(
                    styled,
                    base_url=base,
                    width=int(w),
                    height=int(h),
                    negative_prompt=neg,
                    steps=steps,
                    cfg_scale=cfg_scale,
                    timeout=sd_timeout,
                )
                b64 = base64.standard_b64encode(png_bytes).decode("ascii")
                if response_format == "b64_json":
                    return {"b64_json": b64}
                return {"url": f"data:image/png;base64,{b64}"}

            try:
                sd_items: list[dict[str, str]] = []
                for _ in range(n):
                    sd_items.append(await asyncio.to_thread(run_sd_once))
            except httpx.HTTPError as e:
                pl = _openai_error(f"Stable Diffusion WebUI 请求失败: {e}", type_="api_error")
                _init_bridge_logging().info(
                    "images/generations END id=%s status=502\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log(pl)),
                )
                return JSONResponse(status_code=502, content=pl)
            except RuntimeError as e:
                pl = _openai_error(str(e), type_="api_error")
                _init_bridge_logging().info(
                    "images/generations END id=%s status=502\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log(pl)),
                )
                return JSONResponse(status_code=502, content=pl)
            except Exception as e:
                log = _init_bridge_logging()
                log.exception("images/generations sd_webui id=%s", req_id)
                pl = _openai_error(f"内部错误: {e}", type_="api_error")
                return JSONResponse(status_code=500, content=pl)

            out_sd = {"created": int(time.time()), "data": sd_items}
            _init_bridge_logging().info(
                "images/generations END id=%s status=200\n%s",
                req_id,
                _serialize_images_api_for_log(out_sd),
            )
            return out_sd

        if not resolve_cursor_cli():
            return JSONResponse(
                status_code=503,
                content=_openai_error("cursor CLI 不可用", type_="api_error"),
            )

        if engine == "agent_interactive":
            gen_root = workspace / ".cursor_bridge_generated"
            gen_root.mkdir(parents=True, exist_ok=True)

            def run_one_interactive(idx: int) -> dict[str, str]:
                slot = f"{req_id}_{idx}"
                out_png = (gen_root / f"{slot}.png").resolve()
                styled = augment_prompt_for_style(
                    prompt,
                    image_style
                    if image_style in ("vector", "realistic", "editorial")
                    else "none",
                    target="diffusion",
                )
                aspect_mode = "auto" if str(meta.get("image_aspect_mode") or "").strip().lower() == "auto" else "fixed"
                content_kind = "tech" if str(meta.get("article_kind") or "").strip().lower() == "tech" else "general"
                agent_prompt = _interactive_image_agent_prompt(
                    styled, w, h, out_png, aspect_mode=aspect_mode, content_kind=content_kind
                )
                img_agent_mode: AgentMode | None = None
                raw_img_mode = os.environ.get("CURSOR_BRIDGE_IMAGE_AGENT_MODE", "").strip().lower()
                if raw_img_mode in ("plan", "ask", "agent"):
                    img_agent_mode = raw_img_mode  # type: ignore[assignment]
                t0 = time.time()
                _init_bridge_logging().info(
                    "images/agent_interactive SUBPROCESS id=%s out_png=%s timeout=%s prompt_chars=%d",
                    req_id,
                    out_png,
                    agent_timeout,
                    len(agent_prompt),
                )
                r = run_cursor_agent(
                    agent_prompt,
                    workspace=workspace,
                    output_format="json",
                    trust=True,
                    mode=img_agent_mode,
                    force=True,
                    timeout=agent_timeout,
                    model=agent_model,
                    subprocess_progress=True,
                )
                if r.returncode != 0:
                    msg = (r.stderr or r.stdout or "agent 失败").strip()
                    raise RuntimeError(msg[:8000])
                text = agent_completion_text(r)
                raw_bytes = _bytes_from_expected_or_reply(
                    expected_png=out_png,
                    agent_text=text,
                    raw_stdout=r.stdout or "",
                    workspace=workspace,
                    search_since=t0 - 2.0,
                )
                if not raw_bytes:
                    hint = _agent_interactive_refusal_hint(text, r.stdout or "")
                    if hint:
                        raise ValueError(hint)
                    raise ValueError(
                        "agent_interactive：未在约定路径找到 PNG，也未在 Agent 输出中解析到光栅图。"
                        f" 期望文件: {out_png}。"
                        " 请确认本机 cursor agent 已登录且具备生图工具（勿使用 --mode ask 启动 serve）。"
                    )
                mime = _mime_for_raster_bytes(raw_bytes)
                b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
                if response_format == "b64_json":
                    return {"b64_json": b64}
                return {"url": f"data:{mime};base64,{b64}"}

            try:
                data_interactive: list[dict[str, str]] = []
                for i in range(n):
                    data_interactive.append(await asyncio.to_thread(run_one_interactive, i))
            except ValueError as e:
                pl = _openai_error(str(e), type_="invalid_request_error")
                _init_bridge_logging().info(
                    "images/generations END id=%s status=400\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log(pl)),
                )
                return JSONResponse(status_code=400, content=pl)
            except RuntimeError as e:
                pl = _openai_error(str(e), type_="api_error")
                _init_bridge_logging().info(
                    "images/generations END id=%s status=502\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log(pl)),
                )
                return JSONResponse(status_code=502, content=pl)
            except Exception as e:
                log = _init_bridge_logging()
                log.exception("images/generations agent_interactive id=%s", req_id)
                pl = _openai_error(f"内部错误: {e}", type_="api_error")
                log.info(
                    "images/generations END id=%s status=500\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log(pl)),
                )
                return JSONResponse(status_code=500, content=pl)

            out_iv = {"created": int(time.time()), "data": data_interactive}
            _init_bridge_logging().info(
                "images/generations END id=%s status=200\n%s",
                req_id,
                _serialize_images_api_for_log(out_iv),
            )
            return out_iv

        def run_one_image() -> dict[str, str]:
            styled = augment_prompt_for_style(
                prompt,
                image_style
                if image_style in ("vector", "realistic", "editorial")
                else "none",
                target="svg_agent",
            )
            agent_prompt = _svg_generation_agent_prompt(styled, w, h)
            r = run_cursor_agent(
                agent_prompt,
                workspace=workspace,
                output_format="json",
                trust=True,
                mode=agent_mode,
                force=True,
                timeout=agent_timeout,
                model=agent_model,
            )
            if r.returncode != 0:
                msg = (r.stderr or r.stdout or "agent 失败").strip()
                raise RuntimeError(msg[:8000])
            svg = _extract_svg_from_agent_text(agent_completion_text(r))
            if not svg:
                raise ValueError("Agent 未返回可解析的 SVG（期望含 <svg>...</svg>）")
            raw_bytes = svg.encode("utf-8")
            if export_png:
                raw_bytes = export_svg_to_png(raw_bytes, int(w), int(h))
                mime = "image/png"
            else:
                mime = "image/svg+xml"
            b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
            if response_format == "b64_json":
                return {"b64_json": b64}
            return {"url": f"data:{mime};base64,{b64}"}

        try:
            data_items: list[dict[str, str]] = []
            for _ in range(n):
                data_items.append(await asyncio.to_thread(run_one_image))
        except ValueError as e:
            pl = _openai_error(str(e), type_="invalid_request_error")
            _init_bridge_logging().info(
                "images/generations END id=%s status=400\n%s",
                req_id,
                _truncate_log_text(_serialize_for_log(pl)),
            )
            return JSONResponse(status_code=400, content=pl)
        except RuntimeError as e:
            pl = _openai_error(str(e), type_="api_error")
            _init_bridge_logging().info(
                "images/generations END id=%s status=502\n%s",
                req_id,
                _truncate_log_text(_serialize_for_log(pl)),
            )
            return JSONResponse(status_code=502, content=pl)
        except Exception as e:
            log = _init_bridge_logging()
            log.exception("images/generations id=%s 未捕获异常", req_id)
            pl = _openai_error(f"内部错误: {e}", type_="api_error")
            log.info(
                "images/generations END id=%s status=500\n%s",
                req_id,
                _truncate_log_text(_serialize_for_log(pl)),
            )
            return JSONResponse(status_code=500, content=pl)

        out = {"created": int(time.time()), "data": data_items}
        _init_bridge_logging().info(
            "images/generations END id=%s status=200\n%s",
            req_id,
            _serialize_images_api_for_log(out),
        )
        return out

    @app.post("/v1/images/edits")
    async def images_edits(request: Request):
        """OpenAI 兼容 ``images.edit``：multipart 上传参考图 + prompt，映射为 images 响应。"""
        req_id = str(uuid.uuid4())
        try:
            form = await request.form()
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content=_openai_error(f"读取 multipart 表单失败: {e}", type_="api_error"),
            )

        log_body: dict[str, Any] = {
            "prompt": _form_field_str(form.get("prompt")),
            "n": form.get("n", 1),
            "size": form.get("size"),
            "model": form.get("model"),
            "response_format": form.get("response_format"),
            "metadata": _parse_form_metadata(form.get("metadata")),
            "image": getattr(form.get("image"), "filename", "<upload>"),
        }
        _init_bridge_logging().info(
            "images/edits BEGIN id=%s\n%s",
            req_id,
            _serialize_images_api_for_log(log_body),
        )

        prompt_str = _form_field_str(form.get("prompt")).strip()
        if not prompt_str:
            pl = _openai_error("prompt 必须为非空字符串", type_="invalid_request_error")
            _init_bridge_logging().info(
                "images/edits END id=%s status=400\n%s",
                req_id,
                _truncate_log_text(_serialize_for_log(pl)),
            )
            return JSONResponse(status_code=400, content=pl)

        try:
            ref_bytes, ref_ext = await _read_form_upload_bytes(form.get("image"))
        except ValueError as e:
            pl = _openai_error(str(e), type_="invalid_request_error")
            return JSONResponse(status_code=400, content=pl)

        n = _form_field_int(form.get("n"), 1)
        if n < 1 or n > 4:
            pl = _openai_error("n 须为 1 到 4 的整数", type_="invalid_request_error")
            return JSONResponse(status_code=400, content=pl)

        response_format = _form_field_str(form.get("response_format"), "url") or "url"
        if response_format not in ("url", "b64_json"):
            pl = _openai_error("response_format 须为 url 或 b64_json", type_="invalid_request_error")
            return JSONResponse(status_code=400, content=pl)

        size = _form_field_str(form.get("size"), "1024x1024") or "1024x1024"
        model = _form_field_str(form.get("model"), "cursor-agent") or "cursor-agent"
        agent_model = None if model == "cursor-agent" else str(model)
        meta = _parse_form_metadata(form.get("metadata"))

        workspace = _normalize_workspace({"metadata": meta or {}}, default_workspace, req_id=req_id)
        workspace.mkdir(parents=True, exist_ok=True)
        gen_root = workspace / ".cursor_bridge_generated"
        gen_root.mkdir(parents=True, exist_ok=True)
        ref_path = (gen_root / f"{req_id}_ref{ref_ext}").resolve()
        ref_path.write_bytes(ref_bytes)

        env_engine = (os.environ.get("CURSOR_BRIDGE_IMAGE_ENGINE") or "agent_interactive").strip().lower()
        try:
            resolved = resolve_image_request(
                prompt=prompt_str,
                size=size,
                metadata=meta,
                env_engine=env_engine,
            )
        except ValueError as e:
            pl = _openai_error(str(e), type_="invalid_request_error")
            _init_bridge_logging().info(
                "images/edits END id=%s status=400\n%s",
                req_id,
                _truncate_log_text(_serialize_for_log(pl)),
            )
            return JSONResponse(status_code=400, content=pl)

        prompt = resolved.prompt
        size = resolved.size
        w, h = _parse_image_size(size)
        export_png = resolved.export_png
        image_style = resolved.image_style
        engine = resolved.engine

        if engine == "sd_webui":
            base = os.environ.get("CURSOR_BRIDGE_SDWEBUI_URL", "").strip().rstrip("/")
            if not base:
                pl = _openai_error(
                    "sd_webui 引擎需要配置 CURSOR_BRIDGE_SDWEBUI_URL（Stable Diffusion WebUI 根 URL）",
                    type_="api_error",
                )
                return JSONResponse(status_code=503, content=pl)
            neg = merge_negative_prompts(
                os.environ.get("CURSOR_BRIDGE_SDWEBUI_NEGATIVE_PROMPT", "") or "",
                resolved.negative_prompt_extra,
            )
            try:
                steps = int(float(os.environ.get("CURSOR_BRIDGE_SDWEBUI_STEPS", "28")))
            except ValueError:
                steps = 28
            try:
                cfg_scale = float(os.environ.get("CURSOR_BRIDGE_SDWEBUI_CFG_SCALE", "7"))
            except ValueError:
                cfg_scale = 7.0
            try:
                sd_timeout = float(os.environ.get("CURSOR_BRIDGE_SDWEBUI_TIMEOUT", "600"))
            except ValueError:
                sd_timeout = 600.0
            try:
                denoising = float(os.environ.get("CURSOR_BRIDGE_SDWEBUI_IMG2IMG_DENOISING", "0.65"))
            except ValueError:
                denoising = 0.65

            styled = augment_prompt_for_style(
                prompt,
                image_style if image_style in ("vector", "realistic", "editorial") else "none",
                target="diffusion",
            )

            def run_sd_edit_once() -> dict[str, str]:
                png_bytes = sd_webui_img2img_png(
                    styled,
                    init_image_bytes=ref_bytes,
                    base_url=base,
                    width=int(w),
                    height=int(h),
                    negative_prompt=neg,
                    steps=steps,
                    cfg_scale=cfg_scale,
                    denoising_strength=denoising,
                    timeout=sd_timeout,
                )
                return _image_item_from_bytes(png_bytes, response_format=response_format)

            try:
                sd_items: list[dict[str, str]] = []
                for _ in range(n):
                    sd_items.append(await asyncio.to_thread(run_sd_edit_once))
            except httpx.HTTPError as e:
                pl = _openai_error(f"Stable Diffusion WebUI img2img 请求失败: {e}", type_="api_error")
                return JSONResponse(status_code=502, content=pl)
            except RuntimeError as e:
                pl = _openai_error(str(e), type_="api_error")
                return JSONResponse(status_code=502, content=pl)
            except Exception as e:
                log = _init_bridge_logging()
                log.exception("images/edits sd_webui id=%s", req_id)
                return JSONResponse(status_code=500, content=_openai_error(f"内部错误: {e}", type_="api_error"))

            out_sd = {"created": int(time.time()), "data": sd_items}
            _init_bridge_logging().info(
                "images/edits END id=%s status=200\n%s",
                req_id,
                _serialize_images_api_for_log(out_sd),
            )
            return out_sd

        if not resolve_cursor_cli():
            return JSONResponse(
                status_code=503,
                content=_openai_error("cursor CLI 不可用", type_="api_error"),
            )

        if engine == "agent_interactive":
            def run_one_interactive_edit(idx: int) -> dict[str, str]:
                slot = f"{req_id}_{idx}"
                out_png = (gen_root / f"{slot}.png").resolve()
                styled = augment_prompt_for_style(
                    prompt,
                    image_style if image_style in ("vector", "realistic", "editorial") else "none",
                    target="diffusion",
                )
                aspect_mode = "auto" if str((meta or {}).get("image_aspect_mode") or "").strip().lower() == "auto" else "fixed"
                agent_prompt = _interactive_image_edit_agent_prompt(
                    styled, w, h, ref_path, out_png, aspect_mode=aspect_mode
                )
                img_agent_mode: AgentMode | None = None
                raw_img_mode = os.environ.get("CURSOR_BRIDGE_IMAGE_AGENT_MODE", "").strip().lower()
                if raw_img_mode in ("plan", "ask", "agent"):
                    img_agent_mode = raw_img_mode  # type: ignore[assignment]
                t0 = time.time()
                _init_bridge_logging().info(
                    "images/edits/agent_interactive SUBPROCESS id=%s ref=%s out_png=%s timeout=%s prompt_chars=%d",
                    req_id,
                    ref_path,
                    out_png,
                    agent_timeout,
                    len(agent_prompt),
                )
                r = run_cursor_agent(
                    agent_prompt,
                    workspace=workspace,
                    output_format="json",
                    trust=True,
                    mode=img_agent_mode,
                    force=True,
                    timeout=agent_timeout,
                    model=agent_model,
                    subprocess_progress=True,
                )
                if r.returncode != 0:
                    msg = (r.stderr or r.stdout or "agent 失败").strip()
                    raise RuntimeError(msg[:8000])
                text = agent_completion_text(r)
                raw_out = _bytes_from_expected_or_reply(
                    expected_png=out_png,
                    agent_text=text,
                    raw_stdout=r.stdout or "",
                    workspace=workspace,
                    search_since=t0 - 2.0,
                )
                if not raw_out:
                    hint = _agent_interactive_refusal_hint(text, r.stdout or "")
                    if hint:
                        raise ValueError(hint)
                    raise ValueError(
                        "agent_interactive edit：未在约定路径找到 PNG，也未在 Agent 输出中解析到光栅图。"
                        f" 参考图: {ref_path}；期望输出: {out_png}。"
                        " 请确认本机 cursor agent 已登录且具备生图工具（勿使用 --mode ask 启动 serve）。"
                    )
                return _image_item_from_bytes(raw_out, response_format=response_format)

            try:
                data_interactive: list[dict[str, str]] = []
                for i in range(n):
                    data_interactive.append(await asyncio.to_thread(run_one_interactive_edit, i))
            except ValueError as e:
                pl = _openai_error(str(e), type_="invalid_request_error")
                return JSONResponse(status_code=400, content=pl)
            except RuntimeError as e:
                pl = _openai_error(str(e), type_="api_error")
                return JSONResponse(status_code=502, content=pl)
            except Exception as e:
                log = _init_bridge_logging()
                log.exception("images/edits agent_interactive id=%s", req_id)
                return JSONResponse(status_code=500, content=_openai_error(f"内部错误: {e}", type_="api_error"))

            out_iv = {"created": int(time.time()), "data": data_interactive}
            _init_bridge_logging().info(
                "images/edits END id=%s status=200\n%s",
                req_id,
                _serialize_images_api_for_log(out_iv),
            )
            return out_iv

        def run_one_svg_edit() -> dict[str, str]:
            styled = augment_prompt_for_style(
                prompt,
                image_style if image_style in ("vector", "realistic", "editorial") else "none",
                target="svg_agent",
            )
            agent_prompt = _svg_edit_generation_agent_prompt(styled, w, h, ref_path)
            r = run_cursor_agent(
                agent_prompt,
                workspace=workspace,
                output_format="json",
                trust=True,
                mode=agent_mode,
                force=True,
                timeout=agent_timeout,
                model=agent_model,
            )
            if r.returncode != 0:
                msg = (r.stderr or r.stdout or "agent 失败").strip()
                raise RuntimeError(msg[:8000])
            svg = _extract_svg_from_agent_text(agent_completion_text(r))
            if not svg:
                raise ValueError("Agent 未返回可解析的 SVG（期望含 <svg>...</svg>）")
            raw_bytes = svg.encode("utf-8")
            if export_png:
                raw_bytes = export_svg_to_png(raw_bytes, int(w), int(h))
                return _image_item_from_bytes(raw_bytes, response_format=response_format)
            b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
            if response_format == "b64_json":
                return {"b64_json": b64}
            return {"url": f"data:image/svg+xml;base64,{b64}"}

        try:
            data_items: list[dict[str, str]] = []
            for _ in range(n):
                data_items.append(await asyncio.to_thread(run_one_svg_edit))
        except ValueError as e:
            pl = _openai_error(str(e), type_="invalid_request_error")
            return JSONResponse(status_code=400, content=pl)
        except RuntimeError as e:
            pl = _openai_error(str(e), type_="api_error")
            return JSONResponse(status_code=502, content=pl)
        except Exception as e:
            log = _init_bridge_logging()
            log.exception("images/edits id=%s 未捕获异常", req_id)
            return JSONResponse(status_code=500, content=_openai_error(f"内部错误: {e}", type_="api_error"))

        out = {"created": int(time.time()), "data": data_items}
        _init_bridge_logging().info(
            "images/edits END id=%s status=200\n%s",
            req_id,
            _serialize_images_api_for_log(out),
        )
        return out

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        req_id = str(uuid.uuid4())

        def respond(payload: Any, *, http_status: int = 200) -> Any:
            _chat_completion_log_response(request, req_id, http_status, payload)
            if http_status != 200:
                return JSONResponse(status_code=http_status, content=payload)
            return payload

        try:
            raw = await request.body()
        except Exception as e:
            _chat_completion_log_request(
                request, req_id, {"_error": f"读取 body 失败: {e}"}, note="READ_BODY_ERROR"
            )
            return respond(
                _openai_error(f"读取请求体失败: {e}", type_="api_error"),
                http_status=400,
            )

        if not raw:
            body: Any = None
        else:
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                try:
                    preview = raw.decode("utf-8", errors="replace")[:8000]
                except Exception:
                    preview = f"<binary len={len(raw)}>"
                _chat_completion_log_request(
                    request,
                    req_id,
                    {"_parse_error": str(e), "_raw_preview": preview},
                    note="JSON_DECODE_ERROR",
                )
                return respond(_openai_error("请求体须为 JSON"), http_status=400)

        _chat_completion_log_request(request, req_id, body)

        if not isinstance(body, dict):
            return respond(_openai_error("请求体格式错误"), http_status=400)

        model = body.get("model") or "cursor-agent"
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return respond(
                _openai_error("messages 必须为非空数组"),
                http_status=400,
            )
        for msg in messages:
            if not isinstance(msg, dict) or "role" not in msg:
                return respond(
                    _openai_error("每条 message 须为含 role 的对象"),
                    http_status=400,
                )

        workspace = _normalize_workspace(body, default_workspace, req_id=req_id)
        workspace.mkdir(parents=True, exist_ok=True)
        if _workspace_isolation_enabled() and workspace != default_workspace.expanduser().resolve():
            _init_bridge_logging().debug(
                "chat/completions workspace id=%s path=%s",
                req_id,
                workspace,
            )
        want_stream = body.get("stream") is True
        if not resolve_cursor_cli():
            return respond(_openai_error("cursor CLI 不可用", type_="api_error"), http_status=503)
        agent_model = _normalize_model_for_cursor(model)
        chat_cli_mode = _cursor_cli_mode_from_request_metadata(
            body.get("metadata"), default_mode=agent_mode
        )

        if want_stream:
            media_root: Path | None = None
            async with httpx.AsyncClient() as client:
                try:
                    media_lines, media_root = await _save_all_media(workspace, req_id, messages, client)
                except ValueError as e:
                    return respond(_openai_error(str(e)), http_status=400)
                except httpx.HTTPError as e:
                    return respond(
                        _openai_error(f"下载媒体失败: {e!s}", type_="api_error"),
                        http_status=400,
                    )

            prompt = _messages_to_prompt(messages, media_lines)
            if os.environ.get("CURSOR_BRIDGE_LOG_AGENT_PROMPT") == "1":
                _init_bridge_logging().info(
                    "chat/completions AGENT_PROMPT id=%s chars=%s\n%s",
                    req_id,
                    len(prompt),
                    _truncate_log_text(prompt),
                )

            cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            now = int(time.time())

            async def sse_gen() -> Any:
                try:
                    yield _chat_sse_chunk(
                        cid=cid, created=now, model=str(model), delta={"role": "assistant"}
                    )
                    try:
                        last_snap = ""
                        async for obj in agen_cursor_agent_stream_json(
                            prompt,
                            workspace=workspace,
                            trust=True,
                            mode=chat_cli_mode,
                            force=True,
                            model=agent_model,
                            timeout=agent_timeout,
                        ):
                            snap = agent_stream_object_text(obj)
                            if not isinstance(snap, str):
                                snap = ""
                            if snap.startswith(last_snap):
                                delta = snap[len(last_snap) :]
                            else:
                                delta = snap
                            last_snap = snap
                            if delta:
                                yield _chat_sse_chunk(
                                    cid=cid,
                                    created=now,
                                    model=str(model),
                                    delta={"content": delta},
                                )
                    except (RuntimeError, TimeoutError, ValueError, ProcessLookupError) as e:
                        yield _chat_sse_chunk(
                            cid=cid,
                            created=now,
                            model=str(model),
                            delta={"content": f"\n\n[{type(e).__name__}] {e}"},
                        )
                    yield _chat_sse_chunk(
                        cid=cid,
                        created=now,
                        model=str(model),
                        delta={},
                        finish_reason="stop",
                    )
                    yield b"data: [DONE]\n\n"
                finally:
                    if media_root is not None and media_root.is_dir():
                        try:
                            shutil.rmtree(media_root, ignore_errors=True)
                        except OSError:
                            pass

            _init_bridge_logging().info(
                "chat/completions STREAM id=%s model=%s (SSE, 见 CURSOR_BRIDGE_LOG_AGENT_PROMPT)",
                req_id,
                model,
            )
            return StreamingResponse(
                sse_gen(),
                media_type="text/event-stream; charset=utf-8",
            )

        media_root = None
        try:
            async with httpx.AsyncClient() as client:
                try:
                    media_lines, media_root = await _save_all_media(workspace, req_id, messages, client)
                except ValueError as e:
                    return respond(_openai_error(str(e)), http_status=400)
                except httpx.HTTPError as e:
                    return respond(
                        _openai_error(f"下载媒体失败: {e!s}", type_="api_error"),
                        http_status=400,
                    )

            prompt = _messages_to_prompt(messages, media_lines)
            if os.environ.get("CURSOR_BRIDGE_LOG_AGENT_PROMPT") == "1":
                _init_bridge_logging().info(
                    "chat/completions AGENT_PROMPT id=%s chars=%s\n%s",
                    req_id,
                    len(prompt),
                    _truncate_log_text(prompt),
                )

            def _run() -> Any:
                return run_cursor_agent(
                    prompt,
                    workspace=workspace,
                    output_format="json",
                    trust=True,
                    mode=chat_cli_mode,
                    force=True,
                    timeout=agent_timeout,
                    model=agent_model,
                )

            result = await asyncio.to_thread(_run)
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or "agent 失败").strip()
                err_payload = _openai_error(msg[:8000], type_="api_error")
                extra = {
                    "_agent_debug": {
                        "returncode": result.returncode,
                        "stderr": (result.stderr or "")[:16000],
                        "stdout": (result.stdout or "")[:16000],
                    }
                }
                _init_bridge_logging().warning(
                    "chat/completions AGENT_FAILED id=%s\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log(extra)),
                )
                return respond(err_payload, http_status=502)
            inner_error = _extract_agent_error_message(result)
            if inner_error:
                err_payload = _openai_error(inner_error[:8000], type_="api_error")
                _init_bridge_logging().warning(
                    "chat/completions AGENT_ERROR_PAYLOAD id=%s\n%s",
                    req_id,
                    _truncate_log_text(_serialize_for_log({"error": inner_error})),
                )
                return respond(err_payload, http_status=502)
            text = agent_completion_text(result).strip()
        finally:
            if media_root is not None and media_root.is_dir():
                try:
                    shutil.rmtree(media_root, ignore_errors=True)
                except OSError:
                    pass

        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        now = int(time.time())
        payload = {
            "id": cid,
            "object": "chat.completion",
            "created": now,
            "model": str(model),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "note": "cursor agent 未返回分词统计，占位为 0",
            },
        }
        return respond(payload, http_status=200)

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request):
        req_id = str(uuid.uuid4())

        def respond(payload: Any, *, http_status: int = 200) -> Any:
            _anthropic_messages_log_response(request, req_id, http_status, payload)
            if http_status != 200:
                return JSONResponse(status_code=http_status, content=payload)
            return payload

        try:
            raw = await request.body()
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:
            _anthropic_messages_log_request(
                request, req_id, {"_error": f"请求体解析失败: {e}"}, note="JSON_DECODE_ERROR"
            )
            return respond(_anthropic_error("请求体须为 JSON"), http_status=400)
        _anthropic_messages_log_request(request, req_id, body)
        if not isinstance(body, dict):
            return respond(_anthropic_error("请求体格式错误"), http_status=400)

        model = body.get("model") or "auto"
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return respond(_anthropic_error("messages 必须为非空数组"), http_status=400)

        oa_messages: list[dict[str, Any]] = []
        system_text = _anthropic_system_to_text(body.get("system"))
        if system_text:
            oa_messages.append({"role": "system", "content": system_text})
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role not in ("user", "assistant"):
                role = "user"
            oa_messages.append(
                {
                    "role": role,
                    "content": _anthropic_content_to_openai_content(m.get("content")),
                }
            )
        if not oa_messages:
            return respond(_anthropic_error("messages 解析后为空"), http_status=400)

        workspace = _normalize_workspace(body, default_workspace, req_id=req_id)
        workspace.mkdir(parents=True, exist_ok=True)
        if _workspace_isolation_enabled() and workspace != default_workspace.expanduser().resolve():
            _init_bridge_logging().debug(
                "messages workspace id=%s path=%s",
                req_id,
                workspace,
            )
        want_stream = body.get("stream") is True
        if not resolve_cursor_cli():
            return respond(
                _anthropic_error("cursor CLI 不可用", type_="api_error"),
                http_status=503,
            )
        agent_model = _normalize_model_for_cursor(model)
        anthropic_cli_mode = _cursor_cli_mode_from_request_metadata(
            body.get("metadata"), default_mode=agent_mode
        )

        media_root = None
        try:
            async with httpx.AsyncClient() as client:
                media_lines, media_root = await _save_all_media(workspace, req_id, oa_messages, client)
            prompt = _messages_to_prompt(oa_messages, media_lines)

            if want_stream:
                now = int(time.time())
                msg_id = f"msg_{uuid.uuid4().hex[:24]}"
                _init_bridge_logging().info(
                    "messages STREAM id=%s model=%s (SSE, req.path=%s)",
                    req_id,
                    model,
                    request.url.path,
                )

                async def a_sse() -> Any:
                    yield (
                        f'event: message_start\ndata: {json.dumps({"type":"message_start","message":{"id":msg_id,"type":"message","role":"assistant","model":str(model),"content":[],"stop_reason":None,"stop_sequence":None,"usage":{"input_tokens":0,"output_tokens":0}}}, ensure_ascii=False)}\n\n'
                    ).encode("utf-8")
                    yield (
                        f'event: content_block_start\ndata: {json.dumps({"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}, ensure_ascii=False)}\n\n'
                    ).encode("utf-8")
                    try:
                        last_snap = ""
                        async for obj in agen_cursor_agent_stream_json(
                            prompt,
                            workspace=workspace,
                            trust=True,
                            mode=anthropic_cli_mode,
                            force=True,
                            model=agent_model,
                            timeout=agent_timeout,
                        ):
                            snap = agent_stream_object_text(obj)
                            if not isinstance(snap, str):
                                snap = ""
                            delta = snap[len(last_snap) :] if snap.startswith(last_snap) else snap
                            last_snap = snap
                            if delta:
                                ev = {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": delta},
                                }
                                yield f"event: content_block_delta\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
                    except Exception as e:
                        ev = {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": f"\n\n[BridgeError] {type(e).__name__}: {e}"},
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
                    yield b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
                    yield b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":0}}\n\n'
                    yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

                return StreamingResponse(a_sse(), media_type="text/event-stream; charset=utf-8")

            def _run() -> str:
                result = run_cursor_agent(
                    prompt,
                    workspace=workspace,
                    output_format="json",
                    trust=True,
                    mode=anthropic_cli_mode,
                    force=True,
                    timeout=agent_timeout,
                    model=agent_model,
                )
                if result.returncode == 0:
                    inner_error = _extract_agent_error_message(result)
                    if inner_error:
                        raise RuntimeError(inner_error[:8000])
                    return agent_completion_text(result).strip()
                msg = (result.stderr or result.stdout or "agent 失败").strip()
                raise RuntimeError(msg[:8000] or "cursor agent 调用失败")

            try:
                text = await asyncio.to_thread(_run)
            except RuntimeError as e:
                return respond(_anthropic_error(str(e), type_="api_error"), http_status=502)

            out = {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "model": str(model),
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
            return respond(out, http_status=200)
        finally:
            if media_root is not None and media_root.is_dir():
                try:
                    shutil.rmtree(media_root, ignore_errors=True)
                except OSError:
                    pass

    return app
