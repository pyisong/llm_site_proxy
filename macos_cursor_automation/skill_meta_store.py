"""Skills 可写元数据：标签库 + skill→tags / category 覆盖。

持久化文件默认 ``{CURSOR_SKILLS_DIR}/.skills-meta.json``，
可用 ``CURSOR_SKILLS_META_PATH`` 覆盖。与 skills 目录同卷，重启不丢。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("cursor_openai_bridge.skill_meta")

_TAG_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_lock = threading.RLock()

_DEFAULT_TAGS: tuple[dict[str, str], ...] = (
    {"id": "text", "label": "生文", "color": "#8b9cff"},
    {"id": "image", "label": "生图", "color": "#2bb8c8"},
    {"id": "wechat", "label": "微信", "color": "#07c160"},
    {"id": "research", "label": "研究", "color": "#d4a017"},
)


def _skills_root() -> Path:
    raw = (os.environ.get("CURSOR_SKILLS_DIR") or "/root/.cursor/skills").strip()
    return Path(raw).expanduser().resolve()


def meta_path() -> Path:
    override = (os.environ.get("CURSOR_SKILLS_META_PATH") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _skills_root() / ".skills-meta.json"


def _empty_doc() -> dict[str, Any]:
    return {
        "version": 1,
        "tags": [dict(t) for t in _DEFAULT_TAGS],
        "skill_tags": {},
        "by_name": {},
    }


def _normalize_tag(row: dict[str, Any]) -> dict[str, str] | None:
    tid = str(row.get("id") or "").strip().lower()
    if not tid or not _TAG_ID_RE.fullmatch(tid):
        return None
    label = str(row.get("label") or tid).strip() or tid
    color = str(row.get("color") or "#5a6570").strip() or "#5a6570"
    return {"id": tid, "label": label, "color": color}


def _load_unlocked() -> dict[str, Any]:
    path = meta_path()
    if not path.is_file():
        return _empty_doc()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("读取 skills meta 失败 path=%s err=%s", path, e)
        return _empty_doc()
    if not isinstance(data, dict):
        return _empty_doc()
    tags_raw = data.get("tags")
    tags: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(tags_raw, list):
        for row in tags_raw:
            if not isinstance(row, dict):
                continue
            norm = _normalize_tag(row)
            if not norm or norm["id"] in seen:
                continue
            seen.add(norm["id"])
            tags.append(norm)
    if not tags:
        tags = [dict(t) for t in _DEFAULT_TAGS]
    skill_tags: dict[str, list[str]] = {}
    st = data.get("skill_tags")
    if isinstance(st, dict):
        for k, v in st.items():
            name = str(k).strip()
            if not name or not isinstance(v, list):
                continue
            ids = []
            for x in v:
                tid = str(x).strip().lower()
                if tid and tid not in ids:
                    ids.append(tid)
            skill_tags[name] = ids
    by_name: dict[str, str] = {}
    bn = data.get("by_name")
    if isinstance(bn, dict):
        for k, v in bn.items():
            name = str(k).strip().lower()
            cid = str(v).strip().lower()
            if name and cid:
                by_name[name] = cid
    return {
        "version": int(data.get("version") or 1),
        "tags": tags,
        "skill_tags": skill_tags,
        "by_name": by_name,
    }


def _save_unlocked(doc: dict[str, Any]) -> None:
    path = meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": int(doc.get("version") or 1),
        "tags": doc.get("tags") or [],
        "skill_tags": doc.get("skill_tags") or {},
        "by_name": doc.get("by_name") or {},
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def load_meta() -> dict[str, Any]:
    with _lock:
        return _load_unlocked()


def list_tags() -> list[dict[str, str]]:
    return list(load_meta().get("tags") or [])


def get_skill_tag_ids(name: str) -> list[str]:
    n = (name or "").strip()
    if not n:
        return []
    doc = load_meta()
    known = {t["id"] for t in (doc.get("tags") or [])}
    raw = (doc.get("skill_tags") or {}).get(n) or []
    return [t for t in raw if t in known]


def get_category_override(name: str) -> str | None:
    n = (name or "").strip().lower()
    if not n:
        return None
    return (load_meta().get("by_name") or {}).get(n)


def by_name_overrides() -> dict[str, str]:
    return dict(load_meta().get("by_name") or {})


def resolve_skill_tags(name: str) -> list[dict[str, str]]:
    """返回 skill 上挂的完整 tag 对象列表。"""
    catalog = {t["id"]: t for t in list_tags()}
    out: list[dict[str, str]] = []
    for tid in get_skill_tag_ids(name):
        if tid in catalog:
            out.append(dict(catalog[tid]))
    return out


class SkillMetaError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def create_tag(
    *,
    tag_id: str,
    label: str | None = None,
    color: str | None = None,
) -> dict[str, str]:
    with _lock:
        doc = _load_unlocked()
        tid = (tag_id or "").strip().lower()
        if not tid or not _TAG_ID_RE.fullmatch(tid):
            raise SkillMetaError(
                "tag id 须匹配 ^[a-z][a-z0-9_-]{0,63}$",
                status_code=400,
            )
        tags: list[dict[str, str]] = list(doc.get("tags") or [])
        if any(t["id"] == tid for t in tags):
            raise SkillMetaError(f"标签已存在: {tid}", status_code=409)
        row = {
            "id": tid,
            "label": (label or tid).strip() or tid,
            "color": (color or "#5a6570").strip() or "#5a6570",
        }
        tags.append(row)
        doc["tags"] = tags
        _save_unlocked(doc)
        return dict(row)


def update_tag(
    tag_id: str,
    *,
    label: str | None = None,
    color: str | None = None,
    new_id: str | None = None,
) -> dict[str, str]:
    with _lock:
        doc = _load_unlocked()
        tid = (tag_id or "").strip().lower()
        tags: list[dict[str, str]] = list(doc.get("tags") or [])
        idx = next((i for i, t in enumerate(tags) if t["id"] == tid), -1)
        if idx < 0:
            raise SkillMetaError(f"标签不存在: {tid}", status_code=404)
        row = dict(tags[idx])
        target_id = tid
        if new_id is not None and str(new_id).strip():
            nid = str(new_id).strip().lower()
            if not _TAG_ID_RE.fullmatch(nid):
                raise SkillMetaError(
                    "tag id 须匹配 ^[a-z][a-z0-9_-]{0,63}$",
                    status_code=400,
                )
            if nid != tid and any(t["id"] == nid for t in tags):
                raise SkillMetaError(f"标签已存在: {nid}", status_code=409)
            target_id = nid
        if label is not None:
            row["label"] = str(label).strip() or target_id
        if color is not None:
            row["color"] = str(color).strip() or "#5a6570"
        row["id"] = target_id
        tags[idx] = row
        doc["tags"] = tags
        if target_id != tid:
            skill_tags = dict(doc.get("skill_tags") or {})
            for skill, ids in list(skill_tags.items()):
                skill_tags[skill] = [target_id if x == tid else x for x in ids]
            doc["skill_tags"] = skill_tags
        _save_unlocked(doc)
        return dict(row)


def delete_tag(tag_id: str) -> dict[str, Any]:
    with _lock:
        doc = _load_unlocked()
        tid = (tag_id or "").strip().lower()
        tags: list[dict[str, str]] = list(doc.get("tags") or [])
        if not any(t["id"] == tid for t in tags):
            raise SkillMetaError(f"标签不存在: {tid}", status_code=404)
        doc["tags"] = [t for t in tags if t["id"] != tid]
        skill_tags = dict(doc.get("skill_tags") or {})
        for skill, ids in list(skill_tags.items()):
            skill_tags[skill] = [x for x in ids if x != tid]
        doc["skill_tags"] = skill_tags
        _save_unlocked(doc)
        return {"ok": True, "id": tid}


def set_skill_tags(name: str, tag_ids: list[str]) -> list[dict[str, str]]:
    with _lock:
        doc = _load_unlocked()
        n = (name or "").strip()
        if not n:
            raise SkillMetaError("skill name 不能为空", status_code=400)
        known = {t["id"]: t for t in (doc.get("tags") or [])}
        cleaned: list[str] = []
        missing: list[str] = []
        for raw in tag_ids or []:
            tid = str(raw).strip().lower()
            if not tid:
                continue
            if tid not in known:
                missing.append(tid)
                continue
            if tid not in cleaned:
                cleaned.append(tid)
        if missing:
            raise SkillMetaError(
                f"未知标签: {', '.join(missing)}",
                status_code=400,
            )
        skill_tags = dict(doc.get("skill_tags") or {})
        if cleaned:
            skill_tags[n] = cleaned
        else:
            skill_tags.pop(n, None)
        doc["skill_tags"] = skill_tags
        _save_unlocked(doc)
        return [dict(known[t]) for t in cleaned]


def set_skill_category(name: str, category_id: str | None) -> str | None:
    """设置 skill 分类覆盖；传空则清除覆盖。"""
    with _lock:
        doc = _load_unlocked()
        n = (name or "").strip().lower()
        if not n:
            raise SkillMetaError("skill name 不能为空", status_code=400)
        by_name = dict(doc.get("by_name") or {})
        if category_id is None or not str(category_id).strip():
            by_name.pop(n, None)
            doc["by_name"] = by_name
            _save_unlocked(doc)
            return None
        cid = str(category_id).strip().lower()
        by_name[n] = cid
        doc["by_name"] = by_name
        _save_unlocked(doc)
        return cid


def patch_skill_meta(
    name: str,
    *,
    tags: list[str] | None = None,
    category: str | None = None,
    clear_category: bool = False,
) -> dict[str, Any]:
    """一次更新 tags 和/或 category 覆盖。"""
    result: dict[str, Any] = {"name": name}
    if tags is not None:
        result["tags"] = set_skill_tags(name, tags)
    if clear_category:
        set_skill_category(name, None)
        result["category_override"] = None
    elif category is not None:
        result["category_override"] = set_skill_category(name, category)
    return result
