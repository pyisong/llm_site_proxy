"""Agent 把交付物写到磁盘、聊天只回摘要时，从 workspace 回捞正文。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from response_hoist import (  # noqa: E402
    extract_written_artifact_paths,
    maybe_hoist_written_file_content,
)


class ExtractWrittenPathsTests(unittest.TestCase):
    def test_chinese_written_backticks(self) -> None:
        text = (
            "完整 JSON 已写入 `/workspace/jobs/abc/deliberate_practice_video_script.json`"
            "（178 秒）。因篇幅限制，此处不重复粘贴全文；请直接读取该文件使用。"
        )
        paths = extract_written_artifact_paths(text)
        self.assertEqual(
            paths,
            ["/workspace/jobs/abc/deliberate_practice_video_script.json"],
        )

    def test_saved_to_relative(self) -> None:
        text = "文章已保存至 `gtd-article.md`。全文如下简述。"
        self.assertEqual(extract_written_artifact_paths(text), ["gtd-article.md"])

    def test_no_path(self) -> None:
        self.assertEqual(extract_written_artifact_paths('{"a":1}'), [])


class HoistWrittenContentTests(unittest.TestCase):
    def test_hoists_json_when_reply_is_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = '{"title":"ok","shots":[{"voiceover":"hello"}]}'
            target = root / "script.json"
            target.write_text(payload, encoding="utf-8")
            reply = (
                f"完整 JSON 已写入 `{target}`（可直接解析）。"
                "因篇幅限制，此处不重复粘贴全文；请直接读取该文件使用。"
            )
            out, path = maybe_hoist_written_file_content(reply, root)
            self.assertEqual(out, payload)
            self.assertEqual(path, str(target.resolve()))

    def test_keeps_inline_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inline = '{"title":"inline","shots":[]}'
            out, path = maybe_hoist_written_file_content(inline, root)
            self.assertEqual(out, inline)
            self.assertIsNone(path)

    def test_rejects_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outside = Path(td).parent / "outside_hoist.json"
            try:
                outside.write_text('{"x":1}', encoding="utf-8")
                reply = f"已写入 `{outside}`，请读取。"
                out, path = maybe_hoist_written_file_content(reply, root)
                self.assertEqual(out, reply)
                self.assertIsNone(path)
            finally:
                outside.unlink(missing_ok=True)

    def test_relative_path_under_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "out.json").write_text('{"ok":true}', encoding="utf-8")
            reply = "结果已写入 `out.json`，请直接读取该文件。"
            out, path = maybe_hoist_written_file_content(reply, root)
            self.assertEqual(out, '{"ok":true}')
            self.assertTrue(str(path).endswith("out.json"))


if __name__ == "__main__":
    unittest.main()
