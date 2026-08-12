"""当 Cursor Agent 把交付物写到 workspace、聊天只回「请读文件」时，回捞文件正文。"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 已写入 / 已保存至 / written to / saved to + 可选反引号路径
_WRITE_PATH_RE = re.compile(
    r"(?:"
    r"已写入|已保存至|已保存到|写入了|保存到|"
    r"written\s+to|saved\s+to|wrote\s+(?:to\s+)?"
    r")"
    r"\s*"
    r"[`\"']?"
    r"("
    r"(?:/(?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]+)"  # absolute
    r"|(?:[\w.-]+(?:/[\w.-]+)*\.[A-Za-z0-9]+)"  # relative
    r")"
    r"[`\"']?",
    re.IGNORECASE,
)

_READ_FILE_HINT_RE = re.compile(
    r"(请直接读取该文件|不重复粘贴全文|因篇幅限制|read\s+(?:the\s+)?(?:file|that\s+file))",
    re.IGNORECASE,
)


def extract_written_artifact_paths(text: str) -> list[str]:
    """从 Agent 回复中提取「已写入/已保存」类路径（去重、保序）。"""
    raw = text or ""
    out: list[str] = []
    seen: set[str] = set()
    for m in _WRITE_PATH_RE.finditer(raw):
        p = (m.group(1) or "").strip().strip("`\"'")
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _resolve_under_workspace(path_str: str, workspace: Path) -> Path | None:
    root = workspace.expanduser().resolve()
    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _looks_like_standalone_json(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    s = s.strip()
    if not (s.startswith("{") or s.startswith("[")):
        return False
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        # 允许接近完整但未严格 loads 的大 JSON；有明显结构则不当作 pointer
        return len(s) >= 200 and ("{" in s[1:] or "[" in s[1:])


def _should_attempt_hoist(text: str, paths: list[str]) -> bool:
    if not paths:
        return False
    if _looks_like_standalone_json(text):
        return False
    if _READ_FILE_HINT_RE.search(text or ""):
        return True
    # 短摘要 + 写盘路径：典型「只回指针」
    return len((text or "").strip()) < 1200


def maybe_hoist_written_file_content(
    text: str,
    workspace: Path,
    *,
    max_bytes: int = 2_000_000,
) -> tuple[str, str | None]:
    """
    若回复像「文件指针」且目标文件在 workspace 内，返回文件正文。

    Returns:
        (content, hoisted_abs_path_or_None)
    """
    original = text or ""
    paths = extract_written_artifact_paths(original)
    if not _should_attempt_hoist(original, paths):
        return original, None

    for path_str in paths:
        resolved = _resolve_under_workspace(path_str, workspace)
        if resolved is None or not resolved.is_file():
            continue
        try:
            size = resolved.stat().st_size
        except OSError:
            continue
        if size <= 0 or size > max_bytes:
            continue
        try:
            body = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        body = body.strip()
        if not body:
            continue
        # JSON 优先；否则正文明显长于摘要也采纳（如 md）
        if path_str.lower().endswith((".json", ".md", ".txt", ".yaml", ".yml")) or len(
            body
        ) > len(original.strip()):
            return body, str(resolved)
    return original, None
