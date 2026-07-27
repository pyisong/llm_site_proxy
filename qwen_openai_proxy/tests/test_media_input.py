import base64
import tempfile
from pathlib import Path

import httpx
import pytest

from media_input import (
    materialize_reference_image_bytes,
    materialize_reference_image_data_url,
    materialize_reference_image_path,
    resolve_reference_image_to_path,
)


def test_materialize_reference_image_bytes(tmp_path):
    path = materialize_reference_image_bytes(b"abc", filename="demo.png")
    try:
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"abc"
        assert path.endswith(".png")
    finally:
        Path(path).unlink(missing_ok=True)


def test_materialize_reference_image_data_url():
    data_url = "data:image/png;base64," + base64.b64encode(b"hello").decode("ascii")
    path = materialize_reference_image_data_url(data_url)
    try:
        assert Path(path).read_bytes() == b"hello"
    finally:
        Path(path).unlink(missing_ok=True)


def test_materialize_reference_image_path(tmp_path):
    image = tmp_path / "ref.jpg"
    image.write_bytes(b"jpg")
    assert materialize_reference_image_path(str(image)) == str(image.resolve())


@pytest.mark.anyio
async def test_resolve_reference_image_to_path_from_url(monkeypatch):
    async def fake_download(url: str) -> str:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".png", prefix="qwen-ref-")
        handle.write(b"remote")
        handle.close()
        return handle.name

    monkeypatch.setattr("media_input.download_reference_image", fake_download)
    path, should_cleanup = await resolve_reference_image_to_path(
        image_url="https://example.com/a.png",
    )
    try:
        assert should_cleanup is True
        assert Path(path).read_bytes() == b"remote"
    finally:
        Path(path).unlink(missing_ok=True)
