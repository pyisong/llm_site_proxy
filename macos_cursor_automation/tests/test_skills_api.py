"""Skills HTTP API smoke（不调用真实 cursor agent）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from openai_bridge import create_app
from skills_store import install_from_path


def _write_skill(dir_path: Path, name: str) -> Path:
    skill = dir_path / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: api smoke skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill


class SkillsApiTests(unittest.TestCase):
    def test_list_install_get_delete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            skills_root = td_path / "skills"
            skills_root.mkdir()
            workspace = td_path / "ws"
            workspace.mkdir()
            os.environ["CURSOR_SKILLS_DIR"] = str(skills_root)
            # 清除鉴权，避免影响其它测试环境
            os.environ.pop("CURSOR_OPENAI_BRIDGE_API_KEY", None)

            app = create_app(default_workspace=workspace, agent_mode="ask", agent_timeout=30.0)
            client = TestClient(app)

            r = client.get("/v1/skills")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json().get("skills"), [])

            src = _write_skill(td_path / "src", "api-skill")
            install_from_path(src, root=skills_root)

            r = client.get("/v1/skills")
            self.assertEqual(r.status_code, 200)
            names = [s["name"] for s in r.json()["skills"]]
            self.assertIn("api-skill", names)

            r = client.get("/v1/skills/api-skill")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["name"], "api-skill")

            r = client.post(
                "/v1/skills/install",
                json={"source": "path", "ref": str(src), "overwrite": True},
            )
            self.assertEqual(r.status_code, 200)

            r = client.delete("/v1/skills/api-skill")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(client.get("/v1/skills").json()["skills"], [])

            # 既有健康检查不受影响
            self.assertIn(client.get("/health").status_code, (200,))

    def test_dockerfile_copies_app_directory(self) -> None:
        """整目录 COPY，避免新增 .py 时漏进镜像导致 serve 崩溃。"""
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
        self.assertRegex(
            dockerfile,
            r"(?m)^COPY \. \.\s*$",
            "Dockerfile should COPY . . so new modules ship automatically",
        )
        self.assertIn("tests", dockerignore)
        self.assertIn(".env", dockerignore)


if __name__ == "__main__":
    unittest.main()
