"""argv 文本消毒与超长 prompt 外置。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cursor_automation import (
    _argv_prompt_max_bytes,
    _cleanup_prompt_file,
    _prepare_agent_prompt_for_argv,
    _sanitize_subprocess_argv_text,
)


class ArgvSanitizeTests(unittest.TestCase):
    def test_strips_nul(self) -> None:
        self.assertEqual(_sanitize_subprocess_argv_text("a\x00b"), "ab")

    def test_multiple_nul(self) -> None:
        self.assertEqual(_sanitize_subprocess_argv_text("\x00\x00x"), "x")

    def test_unchanged_without_nul(self) -> None:
        self.assertEqual(_sanitize_subprocess_argv_text("中文\nurl"), "中文\nurl")


class PromptOffloadTests(unittest.TestCase):
    def test_short_prompt_stays_inline(self) -> None:
        argv, path = _prepare_agent_prompt_for_argv("hello", None)
        self.assertEqual(argv, "hello")
        self.assertIsNone(path)

    def test_long_prompt_goes_to_file(self) -> None:
        budget = _argv_prompt_max_bytes()
        big = "中" * ((budget // 3) + 500)
        with tempfile.TemporaryDirectory() as d:
            argv, path = _prepare_agent_prompt_for_argv(big, d)
            try:
                self.assertIsNotNone(path)
                assert path is not None
                self.assertTrue(path.is_file())
                self.assertIn(str(path.resolve()), argv)
                self.assertLess(len(argv.encode("utf-8")), budget)
                self.assertEqual(path.read_text(encoding="utf-8"), big)
            finally:
                _cleanup_prompt_file(path)
            self.assertFalse(path.exists())

    def test_budget_env_override(self) -> None:
        with mock.patch.dict("os.environ", {"CURSOR_BRIDGE_ARGV_PROMPT_MAX_BYTES": "2048"}):
            self.assertEqual(_argv_prompt_max_bytes(), 4096)  # clamp min 4096


if __name__ == "__main__":
    unittest.main()
