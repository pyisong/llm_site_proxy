"""skill_usage 推断单测。"""

from __future__ import annotations

import unittest

from skill_usage import format_skill_usage_log, infer_skill_usage


class SkillUsageTests(unittest.TestCase):
    def test_none(self) -> None:
        u = infer_skill_usage("hello world", installed_names=["hv-analysis"])
        self.assertEqual(u.label, "none")
        self.assertEqual(u.requested, [])
        self.assertEqual(u.evidenced, [])
        self.assertEqual(u.installed_count, 1)

    def test_requested_slash(self) -> None:
        u = infer_skill_usage(
            "/hv-analysis 研究一下 XXX",
            installed_names=["hv-analysis", "other"],
        )
        self.assertEqual(u.requested, ["hv-analysis"])
        self.assertEqual(u.label, "requested")

    def test_evidenced_path(self) -> None:
        u = infer_skill_usage(
            "do work",
            agent_stdout="Reading /root/.cursor/skills/hv-analysis/SKILL.md",
            installed_names=["hv-analysis"],
        )
        self.assertEqual(u.evidenced, ["hv-analysis"])
        self.assertEqual(u.label, "evidenced")

    def test_requested_and_evidenced(self) -> None:
        u = infer_skill_usage(
            "please /hv-analysis now",
            parsed={"tool": {"path": "/root/.cursor/skills/hv-analysis/SKILL.md"}},
            installed_names=["hv-analysis"],
        )
        self.assertEqual(u.label, "requested+evidenced")
        self.assertIn("hv-analysis", u.requested)
        self.assertIn("hv-analysis", u.evidenced)

    def test_unknown_slash_ignored(self) -> None:
        u = infer_skill_usage("/not-installed hi", installed_names=["hv-analysis"])
        self.assertEqual(u.requested, [])

    def test_format_log(self) -> None:
        u = infer_skill_usage("/hv-analysis x", installed_names=["hv-analysis"])
        line = format_skill_usage_log("abc", u)
        self.assertIn("id=abc", line)
        self.assertIn("skill_usage=requested", line)
        self.assertIn("hv-analysis", line)

    def test_image_skill_trigger_line_is_requested(self) -> None:
        prompt = (
            "Task: generate exactly ONE raster image file (PNG)\n\n"
            "Creative brief:\n"
            "【最高优先级 · Cursor 生图 Skills】本张图必须启用下列 Skill：\n"
            "/ip-diagram-creator\n\n"
            "画一张知识卡"
        )
        u = infer_skill_usage(prompt, installed_names=["ip-diagram-creator"])
        self.assertEqual(u.requested, ["ip-diagram-creator"])
        self.assertEqual(u.label, "requested")


if __name__ == "__main__":
    unittest.main()
