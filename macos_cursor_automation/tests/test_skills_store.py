"""skills_store 单元测试（不依赖 cursor CLI / 网络）。"""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from skills_store import (
    SkillStoreError,
    delete_skill,
    generate_skill,
    install,
    install_from_path,
    list_skills,
    validate_skill_name,
)


def _write_skill(dir_path: Path, name: str, description: str = "test skill desc") -> Path:
    skill = dir_path / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill


class SkillsStoreTests(unittest.TestCase):
    def test_validate_skill_name_ok(self) -> None:
        self.assertEqual(validate_skill_name("hv-analysis"), "hv-analysis")

    def test_parse_multiline_description_block(self) -> None:
        from skills_store import _parse_frontmatter, parse_skill_md

        fm = _parse_frontmatter(
            "---\n"
            "name: paul-graham-perspective\n"
            "description: |\n"
            "  Paul Graham的思维框架。\n"
            "  用途：创业顾问。\n"
            "---\n\n# Body\n"
        )
        self.assertEqual(fm["name"], "paul-graham-perspective")
        self.assertIn("Paul Graham的思维框架", fm["description"])
        self.assertIn("创业顾问", fm["description"])
        self.assertNotEqual(fm["description"].strip(), "|")

        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "paul-graham-perspective"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: paul-graham-perspective\n"
                "description: |\n"
                "  line one\n"
                "  line two\n"
                "---\n\n# x\n",
                encoding="utf-8",
            )
            parsed = parse_skill_md(skill / "SKILL.md")
            self.assertTrue(parsed["valid"])
            self.assertEqual(parsed["description"], "line one\nline two")

    def test_validate_skill_name_rejects_traversal(self) -> None:
        with self.assertRaises(SkillStoreError):
            validate_skill_name("../x")
        with self.assertRaises(SkillStoreError):
            validate_skill_name("Foo")

    def test_install_from_path_and_list_delete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            root = td_path / "skills"
            root.mkdir()
            src = _write_skill(td_path / "src", "demo-skill")
            meta = install_from_path(src, root=root)
            self.assertEqual(meta["name"], "demo-skill")
            self.assertTrue(meta["valid"])
            names = [s["name"] for s in list_skills(root)]
            self.assertIn("demo-skill", names)
            self.assertTrue(delete_skill("demo-skill", root=root))
            self.assertEqual(list_skills(root), [])

    def test_install_overwrite_409(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            root = td_path / "skills"
            root.mkdir()
            src = _write_skill(td_path / "src", "demo-skill")
            install_from_path(src, root=root)
            with self.assertRaises(SkillStoreError) as ctx:
                install_from_path(src, root=root, overwrite=False)
            self.assertEqual(ctx.exception.status_code, 409)

    def test_install_name_mismatch_400(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            root = td_path / "skills"
            root.mkdir()
            src = _write_skill(td_path / "src", "demo-skill")
            with self.assertRaises(SkillStoreError) as ctx:
                install_from_path(src, name="other-name", root=root)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_git_remote_disabled_403(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skills"
            root.mkdir()
            old = os.environ.pop("CURSOR_SKILLS_ALLOW_REMOTE", None)
            try:
                with self.assertRaises(SkillStoreError) as ctx:
                    install(source="git", ref="https://example.com/x.git", root=root)
                self.assertEqual(ctx.exception.status_code, 403)
            finally:
                if old is not None:
                    os.environ["CURSOR_SKILLS_ALLOW_REMOTE"] = old

    def test_parse_github_skill_ref(self) -> None:
        from skills_store import SkillStoreError, parse_github_skill_ref

        plain = parse_github_skill_ref("https://github.com/acme/skills")
        self.assertEqual(plain["clone_url"], "https://github.com/acme/skills.git")
        self.assertIsNone(plain["branch"])
        self.assertIsNone(plain["subdir"])

        tree = parse_github_skill_ref(
            "https://github.com/acme/skills/tree/main/hv-analysis"
        )
        self.assertEqual(tree["clone_url"], "https://github.com/acme/skills.git")
        self.assertEqual(tree["branch"], "main")
        self.assertEqual(tree["subdir"], "hv-analysis")

        blob = parse_github_skill_ref(
            "https://github.com/acme/skills/blob/dev/foo/SKILL.md"
        )
        self.assertEqual(blob["branch"], "dev")
        self.assertEqual(blob["subdir"], "foo")

        ssh = parse_github_skill_ref("git@github.com:acme/skills.git")
        self.assertEqual(ssh["clone_url"], "https://github.com/acme/skills.git")

        other = parse_github_skill_ref("https://gitlab.com/acme/skills.git")
        self.assertEqual(other["clone_url"], "https://gitlab.com/acme/skills.git")

        shorthand = parse_github_skill_ref("alchaincyf/zhangxuefeng-skill")
        self.assertEqual(
            shorthand["clone_url"],
            "https://github.com/alchaincyf/zhangxuefeng-skill.git",
        )

        npx = parse_github_skill_ref(
            "npx skills add alchaincyf/zhangxuefeng-skill -a cursor"
        )
        self.assertEqual(
            npx["clone_url"],
            "https://github.com/alchaincyf/zhangxuefeng-skill.git",
        )

        npx_url = parse_github_skill_ref(
            "npx skills add https://github.com/acme/skills --agent=claude-code"
        )
        self.assertEqual(npx_url["clone_url"], "https://github.com/acme/skills.git")

        npx_flags_first = parse_github_skill_ref(
            "skills add -a cursor alchaincyf/steve-jobs-skill"
        )
        self.assertEqual(
            npx_flags_first["clone_url"],
            "https://github.com/alchaincyf/steve-jobs-skill.git",
        )

        with self.assertRaises(SkillStoreError):
            parse_github_skill_ref("npx skills add")

    def test_install_from_zip_bytes_github_layout(self) -> None:
        from skills_store import install_from_zip_bytes

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            root = td_path / "skills"
            root.mkdir()
            # GitHub archive layout: repo-main/SKILL.md
            pack = td_path / "pack" / "zip-skill-main"
            pack.mkdir(parents=True)
            (pack / "SKILL.md").write_text(
                "---\nname: zip-skill\ndescription: from zip upload\n---\n\n# Zip\n",
                encoding="utf-8",
            )
            zpath = td_path / "s.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                for p in pack.rglob("*"):
                    zf.write(p, p.relative_to(td_path / "pack").as_posix())
            meta = install_from_zip_bytes(zpath.read_bytes(), overwrite=True, root=root)
            self.assertEqual(meta["name"], "zip-skill")
            self.assertTrue((root / "zip-skill" / "SKILL.md").is_file())

    def test_preferred_name_avoids_temp_repo_dir(self) -> None:
        from skills_store import _preferred_skill_name, install_from_path

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            root = td_path / "skills"
            root.mkdir()
            # Simulate git clone into .../repo with SKILL.md at root
            clone = td_path / "repo"
            clone.mkdir()
            (clone / "SKILL.md").write_text(
                "---\nname: darwin-skill\ndescription: darwin helper\n---\n\n# X\n",
                encoding="utf-8",
            )
            self.assertEqual(_preferred_skill_name(clone), "darwin-skill")
            meta = install_from_path(
                clone,
                name=_preferred_skill_name(clone),
                overwrite=True,
                root=root,
            )
            self.assertEqual(meta["name"], "darwin-skill")
            self.assertTrue((root / "darwin-skill" / "SKILL.md").is_file())

    def test_url_install_with_zip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            root = td_path / "skills"
            root.mkdir()
            pack = td_path / "pack"
            _write_skill(pack, "zip-skill")
            zpath = td_path / "s.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                for p in (pack / "zip-skill").rglob("*"):
                    zf.write(p, p.relative_to(pack).as_posix())

            os.environ["CURSOR_SKILLS_ALLOW_REMOTE"] = "1"
            try:
                data = zpath.read_bytes()

                class _Resp:
                    def read(self, n: int = -1) -> bytes:
                        return data

                    def __enter__(self) -> "_Resp":
                        return self

                    def __exit__(self, *a: object) -> None:
                        return None

                with mock.patch("skills_store.urlopen", return_value=_Resp()):
                    meta = install(
                        source="url",
                        ref="https://example.com/s.zip",
                        root=root,
                    )
                self.assertEqual(meta["name"], "zip-skill")
            finally:
                os.environ.pop("CURSOR_SKILLS_ALLOW_REMOTE", None)

    def test_generate_skill_with_mock_agent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            root = td_path / "skills"
            root.mkdir()

            def fake_agent(prompt: str, **kwargs: object) -> object:
                work = Path(str(kwargs.get("workspace")))
                _write_skill(work, "gen-skill", "generated from prompt")
                return type("R", (), {"returncode": 0, "stdout": "DONE", "stderr": ""})()

            meta = generate_skill(
                "make a helper skill",
                name="gen-skill",
                root=root,
                run_agent=fake_agent,
            )
            self.assertEqual(meta["name"], "gen-skill")
            self.assertTrue((root / "gen-skill" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
