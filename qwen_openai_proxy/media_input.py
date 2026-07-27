from __future__ import annotations

import base64
import mimetypes
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)


def _guess_extension(mime: str | None, fallback: str = ".png") -> str:
    if not mime:
        return fallback
    ext = mimetypes.guess_extension(mime.split(";")[0].strip(), strict=False)
    return ext or fallback


def _write_temp_file(data: bytes, *, suffix: str) -> str:
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="qwen-ref-")
    handle.write(data)
    handle.close()
    return handle.name


def materialize_reference_image_bytes(
    data: bytes,
    *,
    filename: str | None = None,
) -> str:
    suffix = Path(filename).suffix if filename else ""
    if not suffix:
        suffix = ".png"
    return _write_temp_file(data, suffix=suffix)


def materialize_reference_image_path(path: str) -> str:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"reference image does not exist: {path}")
    if not resolved.is_file():
        raise ValueError(f"reference image is not a file: {path}")
    return str(resolved)


def materialize_reference_image_data_url(data_url: str) -> str:
    match = _DATA_URL_RE.match(data_url.strip())
    if not match:
        raise ValueError("invalid data URL for reference image")
    mime = match.group("mime")
    raw = base64.b64decode(match.group("data"))
    return _write_temp_file(raw, suffix=_guess_extension(mime))


async def download_reference_image(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported image_url scheme: {parsed.scheme or 'missing'}")
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    suffix = Path(parsed.path).suffix or _guess_extension(response.headers.get("content-type"))
    return _write_temp_file(response.content, suffix=suffix)


async def resolve_reference_image_to_path(
    *,
    image_path: str | None = None,
    image_url: str | None = None,
    image_bytes: bytes | None = None,
    image_filename: str | None = None,
) -> tuple[str, bool]:
    """Return (local_path, should_cleanup)."""
    provided = [value is not None for value in (image_path, image_url, image_bytes)]
    if sum(provided) != 1:
        raise ValueError("exactly one of image_path, image_url, or image_bytes must be provided")

    if image_bytes is not None:
        return materialize_reference_image_bytes(image_bytes, filename=image_filename), True
    if image_path is not None:
        return materialize_reference_image_path(image_path), False
    assert image_url is not None
    stripped = image_url.strip()
    if stripped.startswith("data:"):
        return materialize_reference_image_data_url(stripped), True
    if stripped.startswith(("http://", "https://")):
        return await download_reference_image(stripped), True
    if os.path.exists(stripped):
        return materialize_reference_image_path(stripped), False
    raise ValueError("image_url must be http(s), data URL, or an existing local path")


@asynccontextmanager
async def resolved_reference_image(
    *,
    image_path: str | None = None,
    image_url: str | None = None,
    image_bytes: bytes | None = None,
    image_filename: str | None = None,
) -> AsyncIterator[str]:
    path, should_cleanup = await resolve_reference_image_to_path(
        image_path=image_path,
        image_url=image_url,
        image_bytes=image_bytes,
        image_filename=image_filename,
    )
    try:
        yield path
    finally:
        if should_cleanup and os.path.exists(path):
            os.unlink(path)
