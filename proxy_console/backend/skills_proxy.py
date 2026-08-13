"""Forward Cursor Bridge skills API."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

BRIDGE_URL = os.getenv(
    "CONSOLE_CURSOR_BRIDGE_URL", "http://127.0.0.1:8765"
).rstrip("/")


def _headers() -> dict[str, str]:
    key = (os.getenv("CURSOR_OPENAI_BRIDGE_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _looks_like_github(value: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return False
    if v.startswith("git@github.com:"):
        return True
    if "github.com/" in v:
        return True
    return bool(re.match(r"^github\.com/", v))


def normalize_install_body(body: dict[str, Any]) -> dict[str, Any]:
    """把 Console 表单体映射为 Bridge ``{source, ref, name?, overwrite?, subdir?, branch?}``。

    兼容旧字段 ``path`` / ``url``；GitHub 地址自动改为 ``source=git``。
    """
    source = str(body.get("source") or "").strip().lower()
    ref = (
        body.get("ref")
        or body.get("path")
        or body.get("url")
        or ""
    )
    ref = str(ref).strip()
    if not ref:
        raise ValueError("请提供 ref / path / url")

    name = body.get("name")
    overwrite = bool(body.get("overwrite", True))
    subdir = body.get("subdir")
    branch = body.get("branch")
    proxy = body.get("proxy")

    if source in ("github",) or (source in ("", "auto") and _looks_like_github(ref)):
        source = "git"
    elif source == "url" and _looks_like_github(ref):
        # GitHub 页面/仓库地址走 git，zip 直链仍走 url
        parsed = urlparse(ref if "://" in ref else f"https://{ref}")
        path = (parsed.path or "").lower()
        if not path.endswith(".zip"):
            source = "git"
    elif not source:
        source = "path"

    out: dict[str, Any] = {
        "source": source,
        "ref": ref,
        "overwrite": overwrite,
    }
    if name is not None and str(name).strip():
        out["name"] = str(name).strip()
    if subdir is not None and str(subdir).strip():
        out["subdir"] = str(subdir).strip()
    if branch is not None and str(branch).strip():
        out["branch"] = str(branch).strip()
    if proxy is not None and str(proxy).strip():
        out["proxy"] = str(proxy).strip()
    return out


async def list_skills() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{BRIDGE_URL}/v1/skills", headers=_headers())
            if resp.status_code >= 400:
                return {
                    "skills": [],
                    "categories": [],
                    "tags": [],
                    "error": f"bridge HTTP {resp.status_code}",
                    "source": "bridge",
                }
            data = resp.json()
            data["source"] = "bridge"
            if "tags" not in data:
                data["tags"] = []
            if "categories" not in data:
                data["categories"] = []
            return data
    except Exception as exc:  # noqa: BLE001
        return {
            "skills": _fallback_skills(),
            "categories": [],
            "tags": [],
            "error": str(exc),
            "source": "fallback",
        }


def _fallback_skills() -> list[dict[str, Any]]:
    return [
        {
            "name": "hv-analysis",
            "description": "Horizontal-Vertical deep research",
            "path": "(offline fallback)",
            "valid": True,
        },
        {
            "name": "khazix-writer",
            "description": "Long-form WeChat article writer",
            "path": "(offline fallback)",
            "valid": True,
        },
        {
            "name": "neat-freak",
            "description": "Docs and memory reconciliation",
            "path": "(offline fallback)",
            "valid": True,
        },
    ]


async def install_skill(body: dict[str, Any]) -> tuple[int, Any]:
    try:
        payload = normalize_install_body(body)
    except ValueError as exc:
        return 400, {"detail": str(exc)}
    # git/url 走异步任务，避免 HTTP 被 120s/代理超时打断
    if payload.get("source") in ("git", "url", "github"):
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{BRIDGE_URL}/v1/skills/jobs",
                headers=_headers(),
                json=payload,
            )
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {"detail": resp.text}
            return resp.status_code, data
    timeout = 60.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/v1/skills/install",
            headers=_headers(),
            json=payload,
        )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"detail": resp.text}
        return resp.status_code, data


async def get_install_job(job_id: str) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{BRIDGE_URL}/v1/skills/jobs/{job_id}",
            headers=_headers(),
        )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"detail": resp.text}
        return resp.status_code, data


async def upload_skill(
    *,
    filename: str,
    content: bytes,
    name: str | None = None,
    overwrite: bool = True,
    subdir: str | None = None,
) -> tuple[int, Any]:
    if not content:
        return 400, {"detail": "空文件"}
    files = {
        "file": (filename or "skill.zip", content, "application/zip"),
    }
    data: dict[str, str] = {"overwrite": "true" if overwrite else "false"}
    if name and name.strip():
        data["name"] = name.strip()
    if subdir and subdir.strip():
        data["subdir"] = subdir.strip()
    headers = _headers()
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/v1/skills/upload",
            headers=headers,
            files=files,
            data=data,
        )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload


async def delete_skill(name: str) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(
            f"{BRIDGE_URL}/v1/skills/{name}",
            headers=_headers(),
        )
        try:
            payload = resp.json() if resp.content else {"ok": True}
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload


async def list_tags() -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{BRIDGE_URL}/v1/skills/tags", headers=_headers())
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload


async def create_tag(body: dict[str, Any]) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/v1/skills/tags",
            headers=_headers(),
            json=body,
        )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload


async def update_tag(tag_id: str, body: dict[str, Any]) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(
            f"{BRIDGE_URL}/v1/skills/tags/{tag_id}",
            headers=_headers(),
            json=body,
        )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload


async def delete_tag(tag_id: str) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"{BRIDGE_URL}/v1/skills/tags/{tag_id}",
            headers=_headers(),
        )
        try:
            payload = resp.json() if resp.content else {"ok": True}
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload


async def patch_skill_meta(name: str, body: dict[str, Any]) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(
            f"{BRIDGE_URL}/v1/skills/{name}/meta",
            headers=_headers(),
            json=body,
        )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload


async def set_skill_tags(name: str, tags: list[str]) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.put(
            f"{BRIDGE_URL}/v1/skills/{name}/tags",
            headers=_headers(),
            json={"tags": tags},
        )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"detail": resp.text}
        return resp.status_code, payload
