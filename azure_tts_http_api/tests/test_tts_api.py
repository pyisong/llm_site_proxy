import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tts_api import azure_dry_run, build_ssml


class TtsApiTests(unittest.TestCase):
    def test_build_ssml_with_style_role_and_breaks(self):
        ssml = build_ssml(
            {
                "language": "zh-CN",
                "voice": "zh-CN-XiaoxiaoNeural",
                "text": "你好。测试！",
                "style": "cheerful",
                "role": "Girl",
                "styledegree": "1.5",
                "rate": "10",
                "pitch": "-5",
                "silence": "500ms",
            }
        )
        self.assertIn('xml:lang="zh-CN"', ssml)
        self.assertIn('voice name="zh-CN-XiaoxiaoNeural"', ssml)
        self.assertIn('style="cheerful"', ssml)
        self.assertIn('role="Girl"', ssml)
        self.assertIn('styledegree="1.5"', ssml)
        self.assertIn('rate="+10%"', ssml)
        self.assertIn('pitch="-5%"', ssml)
        self.assertIn('<break time="500ms"/>', ssml)

    def test_azure_dry_run_is_json_serializable(self):
        result = azure_dry_run({"text": "hello", "dry_run": True})
        encoded = json.dumps(result)
        self.assertIn("cognitiveservices/v1", encoded)
        self.assertIn("hello", encoded)


if __name__ == "__main__":
    unittest.main()
