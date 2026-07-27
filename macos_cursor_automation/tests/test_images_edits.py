"""images/edits 辅助逻辑与 img2img 载荷测试。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from image_generation import sd_webui_img2img_png
from openai_bridge import (
    _interactive_image_edit_agent_prompt,
    _parse_form_metadata,
    create_app,
)


def test_parse_form_metadata_json_string():
    meta = _parse_form_metadata('{"image_engine": "agent_interactive", "image_style": "editorial"}')
    assert meta == {"image_engine": "agent_interactive", "image_style": "editorial"}


def test_parse_form_metadata_dict():
    assert _parse_form_metadata({"image_engine": "sd_webui"}) == {"image_engine": "sd_webui"}


def test_interactive_image_edit_prompt_includes_reference_and_output_paths(tmp_path: Path):
    ref = tmp_path / "ref.jpg"
    out = tmp_path / "out.png"
    ref.write_bytes(b"\xff\xd8\xff")
    prompt = _interactive_image_edit_agent_prompt("cartoon portrait", "1024", "1024", ref, out)
    assert str(ref.resolve()) in prompt
    assert str(out.resolve()) in prompt
    assert "cartoon portrait" in prompt
    assert "Reference image" in prompt


def test_sd_webui_img2img_posts_init_image():
    captured: dict = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"images": [base64.standard_b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

    with patch("image_generation.httpx.Client", FakeClient):
        out = sd_webui_img2img_png(
            "edit me",
            init_image_bytes=b"\xff\xd8\xff",
            base_url="http://127.0.0.1:7860",
            width=512,
            height=512,
        )
    assert captured["url"] == "http://127.0.0.1:7860/sdapi/v1/img2img"
    assert captured["json"]["prompt"] == "edit me"
    assert isinstance(captured["json"]["init_images"], list)
    assert out.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_images_edits_route_registered():
    app = create_app(default_workspace=Path("."), agent_mode="ask", agent_timeout=60.0)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/v1/images/edits" in paths
