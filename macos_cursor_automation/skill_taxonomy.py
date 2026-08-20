"""Skills 分类：服务端单一来源，供 ``GET /v1/skills`` 与下游 UI 使用。

优先级（高 → 低）：
1. ``CURSOR_SKILLS_TAXONOMY`` / ``CURSOR_SKILLS_TAXONOMY_PATH`` 覆盖（JSON）
2. SKILL.md frontmatter 的 ``category`` / ``type``
3. 命名约定回退（``*-perspective``、``baoyu-post-to-*`` 等）
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("cursor_openai_bridge.skill_taxonomy")

# 默认分类目录（可被 JSON 覆盖增删改）
_DEFAULT_CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "id": "perspective",
        "label": "思维视角",
        "hint": "名人/专家框架，写稿与决策时套用视角",
        "accent": "#8b9cff",
        "purposes": ["text"],
    },
    {
        "id": "content",
        "label": "内容创作",
        "hint": "配图、漫画、封面、信息图、幻灯片",
        "accent": "#2bb8c8",
        "purposes": ["image", "text"],
    },
    {
        "id": "publish",
        "label": "发布分发",
        "hint": "微信 / 微博 / X 等平台发布",
        "accent": "#3dba7e",
        "purposes": ["text"],
    },
    {
        "id": "docs",
        "label": "文档处理",
        "hint": "格式化、转写、翻译、摘要、压缩",
        "accent": "#d4a017",
        "purposes": ["text"],
    },
    {
        "id": "generation",
        "label": "生成后端",
        "hint": "图像/文本生成通道（含实验性）",
        "accent": "#e05d5d",
        "purposes": ["image"],
    },
    {
        "id": "utility",
        "label": "工具箱",
        "hint": "抓取、转换与通用辅助",
        "accent": "#8b97a5",
        "purposes": ["text"],
    },
    {
        "id": "platform",
        "label": "平台/运维",
        "hint": "本机与控制台相关 skill",
        "accent": "#1a6f79",
        "purposes": ["text"],
    },
    {
        "id": "motion",
        "label": "成片运动",
        "hint": "Remotion 镜头节奏、转场与成片结构；与生文/生图叠加",
        "accent": "#c45c4a",
        "purposes": ["motion"],
    },
    {
        "id": "other",
        "label": "其它",
        "hint": "尚未归类",
        "accent": "#5a6570",
        "purposes": ["text", "image"],
    },
)

# frontmatter type/category 别名 → 规范 id
_TYPE_ALIASES: dict[str, str] = {
    "perspective": "perspective",
    "perspectives": "perspective",
    "content": "content",
    "creative": "content",
    "design": "content",
    "illustration": "content",
    "image": "content",
    "publish": "publish",
    "posting": "publish",
    "docs": "docs",
    "document": "docs",
    "documentation": "docs",
    "utility": "utility",
    "utilities": "utility",
    "tool": "utility",
    "tools": "utility",
    "generation": "generation",
    "generator": "generation",
    "backend": "generation",
    "platform": "platform",
    "ops": "platform",
    "motion": "motion",
    "remotion": "motion",
    "video": "motion",
    "other": "other",
}

# 已知 skill 名 → 分类（无 frontmatter 时的回退；也可被 JSON 覆盖）
_DEFAULT_BY_NAME: dict[str, str] = {
    "baoyu-article-illustrator": "content",
    "baoyu-comic": "content",
    "baoyu-cover-image": "content",
    "baoyu-diagram": "content",
    "baoyu-infographic": "content",
    "baoyu-slide-deck": "content",
    "baoyu-xhs-images": "content",
    "baoyu-post-to-wechat": "publish",
    "baoyu-post-to-weibo": "publish",
    "baoyu-post-to-x": "publish",
    "baoyu-format-markdown": "docs",
    "baoyu-markdown-to-html": "docs",
    "baoyu-translate": "docs",
    "baoyu-url-to-markdown": "docs",
    "baoyu-wechat-summary": "docs",
    "baoyu-youtube-transcript": "docs",
    "baoyu-compress-image": "docs",
    "baoyu-danger-x-to-markdown": "docs",
    "baoyu-image-gen": "generation",
    "baoyu-danger-gemini-web": "generation",
    "baoyu-electron-extract": "utility",
    "huashu-nuwa": "utility",
    "darwin-skill": "platform",
    "booktok-remotion": "motion",
    "remotion-best-practices": "motion",
    "remotion-markup": "motion",
    "remotion-render": "motion",
    "remotion-captions": "motion",
    "remotion-multimedia": "motion",
    "remotion-maps": "motion",
    "remotion-interactivity": "motion",
    "remotion-studio": "motion",
    "remotion-docs": "motion",
    "remotion-create": "motion",
}

_CAT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

_override_cache: dict[str, Any] | None = None
_override_mtime: float | None = None


def _load_override() -> dict[str, Any]:
    """从环境变量加载分类覆盖；失败则空 dict。"""
    global _override_cache, _override_mtime
    raw_json = (os.environ.get("CURSOR_SKILLS_TAXONOMY") or "").strip()
    path_raw = (os.environ.get("CURSOR_SKILLS_TAXONOMY_PATH") or "").strip()
    if raw_json:
        try:
            data = json.loads(raw_json)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as e:
            log.warning("CURSOR_SKILLS_TAXONOMY JSON 无效: %s", e)
            return {}
    if not path_raw:
        return {}
    path = Path(path_raw).expanduser()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _override_cache is not None and _override_mtime == mtime:
        return _override_cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _override_cache = data if isinstance(data, dict) else {}
        _override_mtime = mtime
        return _override_cache
    except (OSError, json.JSONDecodeError) as e:
        log.warning("读取 CURSOR_SKILLS_TAXONOMY_PATH 失败: %s", e)
        return {}


def list_categories() -> list[dict[str, Any]]:
    """返回分类目录（含 label/hint/accent/purposes）。"""
    ov = _load_override()
    cats = ov.get("categories")
    if isinstance(cats, list) and cats:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in cats:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("id") or "").strip().lower()
            if not cid or not _CAT_ID_RE.fullmatch(cid) or cid in seen:
                continue
            seen.add(cid)
            purposes = row.get("purposes")
            if not isinstance(purposes, list) or not purposes:
                purposes = ["text", "image"]
            out.append(
                {
                    "id": cid,
                    "label": str(row.get("label") or cid),
                    "hint": str(row.get("hint") or ""),
                    "accent": str(row.get("accent") or "#5a6570"),
                    "purposes": [str(p).strip() for p in purposes if str(p).strip()],
                }
            )
        if out and not any(c["id"] == "other" for c in out):
            out.append(
                {
                    "id": "other",
                    "label": "其它",
                    "hint": "尚未归类",
                    "accent": "#5a6570",
                    "purposes": ["text", "image"],
                }
            )
        if out:
            return out
    return [dict(c) for c in _DEFAULT_CATEGORIES]


def _by_name_map() -> dict[str, str]:
    ov = _load_override()
    merged = dict(_DEFAULT_BY_NAME)
    extra = ov.get("by_name") or ov.get("skills")
    if isinstance(extra, dict):
        for k, v in extra.items():
            name = str(k).strip().lower()
            cid = str(v).strip().lower()
            if name and cid and _CAT_ID_RE.fullmatch(cid):
                merged[name] = cid
    # 可写 meta 覆盖（console 改分类）
    try:
        from skill_meta_store import by_name_overrides
    except ImportError:
        try:
            from .skill_meta_store import by_name_overrides  # type: ignore
        except ImportError:
            by_name_overrides = None  # type: ignore
    if by_name_overrides is not None:
        for k, v in by_name_overrides().items():
            name = str(k).strip().lower()
            cid = str(v).strip().lower()
            if name and cid and _CAT_ID_RE.fullmatch(cid):
                merged[name] = cid
    return merged


def _normalize_category_id(raw: str | None, *, known: set[str]) -> str | None:
    if not raw:
        return None
    cid = str(raw).strip().lower().replace(" ", "-")
    if not cid:
        return None
    aliased = _TYPE_ALIASES.get(cid, cid)
    if aliased in known:
        return aliased
    if _CAT_ID_RE.fullmatch(aliased):
        # 未知但合法 id：归入 other，避免污染目录；仍可用 by_name 覆盖
        return None
    return None


def categorize_skill(
    name: str,
    *,
    frontmatter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据名称 + frontmatter 归类，返回分类元数据字段。"""
    cats = list_categories()
    known = {c["id"] for c in cats}
    by_name = _by_name_map()
    n = (name or "").strip().lower()
    fm = frontmatter or {}

    source = "fallback"
    cid: str | None = None

    # 0) 可写 meta 覆盖（Console 修改分类优先）
    try:
        from skill_meta_store import get_category_override
    except ImportError:
        try:
            from .skill_meta_store import get_category_override  # type: ignore
        except ImportError:
            get_category_override = None  # type: ignore
    if get_category_override is not None:
        ov = get_category_override(n)
        if ov and ov in known:
            cid = ov
            source = "meta:by_name"

    # 1) frontmatter category / type
    if cid is None:
        for key in ("category", "type"):
            raw = fm.get(key)
            if isinstance(raw, str) and raw.strip():
                hit = _normalize_category_id(raw, known=known)
                if hit:
                    cid = hit
                    source = f"frontmatter:{key}"
                    break

    # 2) 覆盖表 / 默认 by_name
    if cid is None and n in by_name:
        cand = by_name[n]
        if cand in known:
            cid = cand
            source = "by_name"

    # 3) 命名约定
    if cid is None:
        if n.startswith("remotion-") or n == "booktok-remotion":
            cid = "motion" if "motion" in known else None
            source = "rule:remotion"
        elif n.endswith("-perspective") or "-perspective" in n:
            cid = "perspective" if "perspective" in known else None
            source = "rule:perspective-suffix"
        elif n.startswith("baoyu-post-to-"):
            cid = "publish" if "publish" in known else None
            source = "rule:baoyu-post"
        elif n.startswith("baoyu-danger-"):
            cid = "generation" if "generation" in known else None
            source = "rule:baoyu-danger"
        elif n.startswith("baoyu-"):
            cid = "utility" if "utility" in known else None
            source = "rule:baoyu-default"

    if cid is None or cid not in known:
        cid = "other" if "other" in known else cats[-1]["id"]
        if source == "fallback":
            source = "default:other"

    meta = next((c for c in cats if c["id"] == cid), cats[-1])
    display = _display_name(n or name)
    family = _family_label(n or name)
    purposes = list(meta.get("purposes") or ["text", "image"])
    return {
        "category": meta["id"],
        "category_label": meta["label"],
        "category_hint": meta.get("hint") or "",
        "category_accent": meta.get("accent") or "#5a6570",
        "category_source": source,
        "purposes": purposes,
        "display_name": display,
        "family": family,
    }


def _display_name(name: str) -> str:
    n = (name or "").strip()
    if n.startswith("baoyu-") and len(n) > 6:
        return n[6:]
    if n.endswith("-perspective") and len(n) > len("-perspective"):
        return n[: -len("-perspective")]
    return n


def _family_label(name: str) -> str | None:
    n = (name or "").strip()
    if n.startswith("baoyu-"):
        return "baoyu"
    if n.endswith("-perspective"):
        return "视角"
    return None


def enrich_skill_item(
    item: dict[str, Any],
    *,
    frontmatter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """就地写入分类字段与标签后返回。"""
    tax = categorize_skill(str(item.get("name") or ""), frontmatter=frontmatter)
    item.update(tax)
    try:
        from skill_meta_store import resolve_skill_tags
    except ImportError:
        try:
            from .skill_meta_store import resolve_skill_tags  # type: ignore
        except ImportError:
            resolve_skill_tags = None  # type: ignore
    if resolve_skill_tags is not None:
        tags = resolve_skill_tags(str(item.get("name") or ""))
        item["tags"] = tags
        item["tag_ids"] = [t["id"] for t in tags]
    else:
        item.setdefault("tags", [])
        item.setdefault("tag_ids", [])
    return item


def skills_list_payload(skills: list[dict[str, Any]]) -> dict[str, Any]:
    """``GET /v1/skills`` 响应体：skills + categories + tags。"""
    try:
        from skill_meta_store import list_tags
    except ImportError:
        try:
            from .skill_meta_store import list_tags  # type: ignore
        except ImportError:
            list_tags = lambda: []  # type: ignore
    return {
        "skills": skills,
        "categories": list_categories(),
        "tags": list_tags(),
    }
