import datetime
import json
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from browser_capture import parse_post_data, summarize_requests
from site_client import SiteTtsClient, SiteTtsError, daily_yzm
from tts_api import build_ssml, dry_run, resolve_provider


class SiteClientTests(unittest.TestCase):
    def test_normalize_payload_maps_language_alias(self):
        client = SiteTtsClient()
        client.bootstrap = MagicMock(return_value=MagicMock(yzm="202410170001"))
        client.list_voices = MagicMock(
            return_value={"中文（普通话，简体）": {"ShortName": ["zh-CN-XiaoxiaoNeural"]}}
        )
        payload = client.normalize_payload(
            {"text": "你好", "language": "zh-cn", "voice": "zh-CN-XiaoxiaoNeural"}
        )
        self.assertEqual(payload["language"], "中文（普通话，简体）")
        self.assertEqual(payload["yzm"], "202410170001")
        self.assertEqual(payload["voice"], "zh-CN-XiaoxiaoNeural")

    def test_normalize_payload_ssml_mode(self):
        client = SiteTtsClient()
        payload = client.normalize_payload(
            {
                "type": "SSML",
                "text": "<speak>hello</speak>",
                "kbitrate": "audio-24khz-160kbitrate-mono-mp3",
            }
        )
        self.assertEqual(payload["type"], "SSML")
        self.assertIn("hello", payload["text"])

    def test_synthesize_retries_on_401(self):
        client = SiteTtsClient()
        client.bootstrap = MagicMock(
            side_effect=[
                MagicMock(token="token-a", cookie_header="a=1"),
                MagicMock(token="token-b", cookie_header="b=2"),
                MagicMock(token="token-b", cookie_header="b=2"),
            ]
        )
        client.normalize_payload = MagicMock(return_value={"text": "你好"})
        client._request = MagicMock(
            side_effect=[
                json.dumps({"code": 401, "msg": "", "bb": datetime.date.today().isoformat()}),
                json.dumps(
                    {
                        "code": 200,
                        "msg": "ok",
                        "download": "https://www.text-to-speech.cn/mp3/test.mp3",
                    }
                ),
            ]
        )
        client._download_audio = MagicMock(return_value=b"audio")
        audio, meta = client.synthesize({"text": "你好"})
        self.assertEqual(audio, b"audio")
        self.assertEqual(meta["code"], 200)
        self.assertEqual(client.bootstrap.call_count, 3)

    def test_synthesize_retries_on_403(self):
        client = SiteTtsClient()
        client.bootstrap = MagicMock(
            side_effect=[
                MagicMock(token="token-a", cookie_header="a=1"),
                MagicMock(token="token-b", cookie_header="b=2"),
                MagicMock(token="token-b", cookie_header="b=2"),
            ]
        )
        client.normalize_payload = MagicMock(return_value={"text": "你好"})
        client._request = MagicMock(
            side_effect=[
                json.dumps({"code": 403, "msg": "invalid"}),
                json.dumps(
                    {
                        "code": 200,
                        "msg": "ok",
                        "download": "https://www.text-to-speech.cn/mp3/test.mp3",
                    }
                ),
            ]
        )
        client._download_audio = MagicMock(return_value=b"audio")
        audio, meta = client.synthesize({"text": "你好"})
        self.assertEqual(audio, b"audio")
        self.assertEqual(meta["code"], 200)
        self.assertEqual(client.bootstrap.call_count, 3)

    def test_missing_text_raises(self):
        client = SiteTtsClient()
        with self.assertRaises(SiteTtsError):
            client.normalize_payload({"voice": "zh-CN-XiaoxiaoNeural"})


class BrowserCaptureTests(unittest.TestCase):
    def test_redacts_sensitive_post_fields(self):
        params = parse_post_data("text=hello&token=secret&yzm=1234&user_id=u1")
        self.assertEqual(params["text"], "hello")
        self.assertEqual(params["token"], "<redacted>")
        self.assertEqual(params["yzm"], "<redacted>")
        self.assertEqual(params["user_id"], "<redacted>")

    def test_summarize_requests_filters_host(self):
        report = summarize_requests(
            [
                {
                    "method": "POST",
                    "url": "https://www.text-to-speech.cn/getSpeek.php",
                    "post_data": "text=hello&token=secret",
                    "status": 200,
                    "response_body": '{"code":200}',
                }
            ],
            host_filter="www.text-to-speech.cn",
        )
        self.assertEqual(report["requestCount"], 1)
        self.assertEqual(report["interestingRequests"][0]["path"], "/getSpeek.php")


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
        self.assertIn('style="cheerful"', ssml)
        self.assertIn('role="Girl"', ssml)
        self.assertIn('<break time="500ms"/>', ssml)

    def test_dry_run_site_provider(self):
        with patch("tts_api.site_client_from_env") as factory:
            factory.return_value.dry_run.return_value = {"provider": "text-to-speech.cn"}
            result = dry_run({"text": "hello", "provider": "site"})
        self.assertEqual(result["provider"], "text-to-speech.cn")

    def test_resolve_provider_defaults_to_site(self):
        with patch("tts_api.provider_from_env", return_value="site"):
            self.assertEqual(resolve_provider({}), "site")


if __name__ == "__main__":
    unittest.main()
