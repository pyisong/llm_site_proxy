from __future__ import annotations

import datetime
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

SITE_BASE = "https://www.text-to-speech.cn"
DEFAULT_YZM = "202410170001"
TOKEN_RE = re.compile(r"const token = '([^']+)'")
YZM_RE = re.compile(r"var yzm='([^']+)'")


def daily_yzm() -> str:
    """站点按日校验码兜底，格式 YYYYMMDD0001。"""
    return datetime.date.today().strftime("%Y%m%d") + "0001"

LANGUAGE_ALIASES = {
    "zh-cn": "中文（普通话，简体）",
    "zh_cn": "中文（普通话，简体）",
    "mandarin": "中文（普通话，简体）",
    "普通话": "中文（普通话，简体）",
    "中文": "中文（普通话，简体）",
    "en-us": "英语（美国）",
    "en_us": "英语（美国）",
    "english": "英语（美国）",
    "ja-jp": "日语（日本）",
    "ja_jp": "日语（日本）",
    "japanese": "日语（日本）",
}

SUPPORTED_FORMATS = [
    "audio-16khz-32kbitrate-mono-mp3",
    "audio-16khz-128kbitrate-mono-mp3",
    "audio-24khz-160kbitrate-mono-mp3",
    "audio-48khz-192kbitrate-mono-mp3",
    "riff-16khz-16bit-mono-pcm",
    "riff-24khz-16bit-mono-pcm",
    "riff-48khz-16bit-mono-pcm",
]

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_LANGUAGE = "中文（普通话，简体）"
DEFAULT_FORMAT = "audio-24khz-160kbitrate-mono-mp3"


class SiteTtsError(RuntimeError):
    pass


@dataclass
class SiteSession:
    token: str
    cookie_header: str
    yzm: str = DEFAULT_YZM
    created_at: float = field(default_factory=time.time)
    voices: dict[str, Any] | None = None

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class SiteTtsClient:
    def __init__(
        self,
        base_url: str = SITE_BASE,
        headless: bool = True,
        session_ttl: int = 1800,
        user_agent: str = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.session_ttl = session_ttl
        self.user_agent = user_agent
        self._session: SiteSession | None = None
        self._lock = threading.Lock()

    def bootstrap(self, force: bool = False) -> SiteSession:
        with self._lock:
            if (
                not force
                and self._session is not None
                and self._session.age_seconds < self.session_ttl
            ):
                return self._session
            self._session = self._bootstrap_with_browser()
            return self._session

    def _bootstrap_with_browser(self) -> SiteSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SiteTtsError(
                "Playwright is required for site mode. Install with: pip install playwright && playwright install chromium"
            ) from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(user_agent=self.user_agent)
            page = context.new_page()
            try:
                page.goto(self.base_url + "/", wait_until="networkidle", timeout=60000)
                token = page.evaluate(
                    """() => {
                        for (const script of document.querySelectorAll('script')) {
                            const match = (script.textContent || '').match(/const token = '([^']+)'/);
                            if (match) return match[1];
                        }
                        return null;
                    }"""
                )
                yzm = page.evaluate(
                    """() => {
                        for (const script of document.querySelectorAll('script')) {
                            const match = (script.textContent || '').match(/var yzm='([^']+)'/);
                            if (match) return match[1];
                        }
                        return null;
                    }"""
                )
                if not token:
                    raise SiteTtsError("Failed to extract site token from page")
                cookies = context.cookies()
            finally:
                browser.close()

        cookie_header = "; ".join(f"{item['name']}={item['value']}" for item in cookies)
        return SiteSession(token=token, cookie_header=cookie_header, yzm=str(yzm or daily_yzm()))

    def _request(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        retry_on_auth: bool = True,
    ) -> str:
        session = self.bootstrap()
        encoded = urllib.parse.urlencode(data or {}, doseq=True).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/",
                "Cookie": session.cookie_header,
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise SiteTtsError(f"Site request failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise SiteTtsError(f"Site request failed: {exc.reason}") from exc

    def _parse_json(self, body: str, *, endpoint: str) -> dict[str, Any]:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SiteTtsError(f"{endpoint} returned non-JSON: {body[:200]}") from exc
        if not isinstance(parsed, dict):
            raise SiteTtsError(f"{endpoint} returned unexpected payload")
        return parsed

    def list_voices(self, refresh: bool = False) -> dict[str, Any]:
        session = self.bootstrap(force=refresh)
        if session.voices is not None and not refresh:
            return session.voices
        body = self._request("/getSpeekList.php", {})
        voices = self._parse_json(body, endpoint="getSpeekList.php")
        session.voices = voices
        return voices

    def get_voice_style(self, voice: str) -> dict[str, Any]:
        return self._parse_json(
            self._request("/getStyle.php", {"voice": voice}),
            endpoint="getStyle.php",
        )

    def resolve_language(self, language: str | None, voice: str | None = None) -> str:
        if not language:
            if voice:
                voices = self.list_voices()
                for language_name, info in voices.items():
                    short_names = info.get("ShortName") or []
                    if voice in short_names:
                        return language_name
            return DEFAULT_LANGUAGE

        normalized = language.strip()
        alias = LANGUAGE_ALIASES.get(normalized.lower())
        if alias:
            return alias

        voices = self.list_voices()
        if normalized in voices:
            return normalized

        for language_name in voices:
            if normalized.lower() in language_name.lower():
                return language_name

        raise SiteTtsError(f"Unknown language: {language!r}")

    def _resolve_yzm(self, payload: dict[str, Any]) -> str:
        if payload.get("yzm"):
            return str(payload["yzm"])
        session = self.bootstrap()
        return str(session.yzm or daily_yzm())

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise SiteTtsError("text is required")

        voice = str(payload.get("voice") or DEFAULT_VOICE)
        language = self.resolve_language(payload.get("language"), voice)
        output_format = str(payload.get("kbitrate") or payload.get("output_format") or DEFAULT_FORMAT)
        if output_format not in SUPPORTED_FORMATS:
            raise SiteTtsError(f"Unsupported kbitrate/output format: {output_format}")

        if str(payload.get("type") or "").upper() == "SSML" or payload.get("ssml"):
            ssml = str(payload.get("ssml") or text)
            return {
                "type": "SSML",
                "text": ssml,
                "kbitrate": output_format,
                "user_id": str(payload.get("user_id") or ""),
                "yzm": self._resolve_yzm(payload),
            }

        return {
            "language": language,
            "voice": voice,
            "text": text,
            "role": str(payload.get("role") or "0"),
            "style": str(payload.get("style") or "0"),
            "rate": str(payload.get("rate", payload.get("speed", 0))),
            "pitch": str(payload.get("pitch") or "0"),
            "kbitrate": output_format,
            "silence": str(payload.get("silence") or ""),
            "styledegree": str(payload.get("styledegree") or "1"),
            "volume": str(payload.get("volume") or "75"),
            "predict": str(payload.get("predict") or "0"),
            "user_id": str(payload.get("user_id") or ""),
            "yzm": self._resolve_yzm(payload),
            "replice": str(payload.get("replice") or "1"),
        }

    def synthesize(self, payload: dict[str, Any], *, refresh_session: bool = False) -> tuple[bytes, dict[str, Any]]:
        if refresh_session:
            self.bootstrap(force=True)

        session = self.bootstrap()
        request_data = self.normalize_payload(payload)
        request_data["token"] = session.token

        body = self._request("/getSpeek.php", request_data)
        result = self._parse_json(body, endpoint="getSpeek.php")

        code = result.get("code")
        if code in (401, 403) and not refresh_session:
            return self.synthesize(payload, refresh_session=True)

        if code == 401:
            raise SiteTtsError(
                "text-to-speech.cn 需要验证码或今日免费额度已用尽（401）。"
                f" 响应: {result}"
            )

        if code != 200:
            raise SiteTtsError(str(result.get("msg") or result))

        download_url = str(result.get("download") or "")
        if not download_url:
            raise SiteTtsError(f"Site response missing download URL: {result}")

        audio = self._download_audio(download_url)
        meta = {
            "code": code,
            "msg": result.get("msg"),
            "download": download_url,
            "request": request_data,
        }
        return audio, meta

    def _download_audio(self, download_url: str) -> bytes:
        if download_url.startswith("/"):
            download_url = self.base_url + download_url
        request = urllib.request.Request(
            download_url,
            headers={"User-Agent": self.user_agent, "Referer": f"{self.base_url}/"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise SiteTtsError(f"Audio download failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise SiteTtsError(f"Audio download failed: {exc.reason}") from exc

    def dry_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.bootstrap()
        request_data = self.normalize_payload(payload)
        request_data["token"] = "<redacted>"
        return {
            "provider": "text-to-speech.cn",
            "method": "POST",
            "url": f"{self.base_url}/getSpeek.php",
            "headers": {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/",
                "Cookie": "<redacted>",
            },
            "form": request_data,
            "sessionAgeSeconds": round(session.age_seconds, 2),
        }

    def capture_summary(self) -> dict[str, Any]:
        session = self.bootstrap(force=True)
        voices = self.list_voices(refresh=True)
        return {
            "provider": "text-to-speech.cn",
            "baseUrl": self.base_url,
            "tokenPresent": bool(session.token),
            "sessionAgeSeconds": round(session.age_seconds, 2),
            "endpoints": [
                {"path": "/getSpeek.php", "method": "POST", "purpose": "TTS generation"},
                {"path": "/getSpeekList.php", "method": "POST", "purpose": "Voice list"},
                {"path": "/getStyle.php", "method": "POST", "purpose": "Voice style metadata"},
                {"path": "/summary.php", "method": "POST", "purpose": "Usage summary"},
            ],
            "voiceLanguageCount": len(voices),
            "sampleLanguages": list(voices.keys())[:5],
        }
