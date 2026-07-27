import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from har_capture_summary import request_summary


class HarCaptureSummaryTests(unittest.TestCase):
    def test_redacts_private_tts_fields(self):
        entry = {
            "request": {
                "method": "POST",
                "url": "https://www.text-to-speech.cn/getSpeek.php",
                "headers": [{"name": "Cookie", "value": "abc"}],
                "postData": {
                    "mimeType": "application/x-www-form-urlencoded",
                    "text": "text=hello&voice=zh-CN-XiaoxiaoNeural&token=secret&yzm=1234&user_id=u1",
                },
            },
            "response": {"status": 200, "content": {"mimeType": "application/json"}},
        }
        summary = request_summary(entry)
        self.assertEqual(summary["path"], "/getSpeek.php")
        self.assertEqual(summary["redactedPost"]["text"], "hello")
        self.assertEqual(summary["redactedPost"]["voice"], "zh-CN-XiaoxiaoNeural")
        self.assertEqual(summary["redactedPost"]["token"], "<redacted>")
        self.assertEqual(summary["redactedPost"]["yzm"], "<redacted>")
        self.assertEqual(summary["redactedPost"]["user_id"], "<redacted>")
        self.assertIn("private TTS generation endpoint", summary["note"])


if __name__ == "__main__":
    unittest.main()
