from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from xml.sax.saxutils import escape

from site_client import (
    DEFAULT_FORMAT,
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
    SUPPORTED_FORMATS as SITE_FORMATS,
    SiteTtsClient,
    SiteTtsError,
)


AZURE_NS = "http://www.w3.org/2001/10/synthesis"
MSTTS_NS = "https://www.w3.org/2001/mstts"

COMMON_STYLES = [
    "affectionate",
    "angry",
    "assistant",
    "calm",
    "chat",
    "cheerful",
    "customerservice",
    "depressed",
    "disgruntled",
    "embarrassed",
    "empathetic",
    "envious",
    "fearful",
    "friendly",
    "gentle",
    "hopeful",
    "lyrical",
    "narration-professional",
    "narration-relaxed",
    "newscast",
    "newscast-casual",
    "newscast-formal",
    "poetry-reading",
    "sad",
    "serious",
    "shouting",
    "sports-commentary",
    "sports-commentary-excited",
    "terrified",
    "unfriendly",
    "whispering",
]

COMMON_ROLES = [
    "Girl",
    "Boy",
    "YoungAdultFemale",
    "YoungAdultMale",
    "OlderAdultFemale",
    "OlderAdultMale",
    "SeniorFemale",
    "SeniorMale",
]


class TtsError(RuntimeError):
    pass


@dataclass(frozen=True)
class AzureConfig:
    key: str
    region: str
    endpoint: str | None = None

    @property
    def tts_url(self) -> str:
        if self.endpoint:
            return self.endpoint.rstrip("/") + "/cognitiveservices/v1"
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"

    @property
    def voices_url(self) -> str:
        if self.endpoint:
            return self.endpoint.rstrip("/") + "/cognitiveservices/voices/list"
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/voices/list"


def provider_from_env() -> str:
    return os.getenv("TTS_PROVIDER", "site").strip().lower()


def config_from_env() -> AzureConfig:
    key = os.getenv("AZURE_SPEECH_KEY", "")
    region = os.getenv("AZURE_SPEECH_REGION", "")
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")
    if not key or not region:
        raise TtsError("Missing AZURE_SPEECH_KEY or AZURE_SPEECH_REGION")
    return AzureConfig(key=key, region=region, endpoint=endpoint)


def site_client_from_env() -> SiteTtsClient:
    headless = os.getenv("TTS_BROWSER_HEADLESS", "1") not in ("0", "false", "False")
    return SiteTtsClient(headless=headless)


def normalize_percent(value: Any, default: int = 0) -> str:
    if value is None or value == "":
        value = default
    try:
        number = int(float(str(value).rstrip("%")))
    except ValueError as exc:
        raise TtsError(f"Invalid percentage value: {value!r}") from exc
    sign = "+" if number >= 0 else ""
    return f"{sign}{number}%"


def normalize_styledegree(value: Any) -> str:
    if value in (None, "", "0"):
        return "1"
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TtsError(f"Invalid styledegree: {value!r}") from exc
    if number <= 0 or number > 2:
        raise TtsError("styledegree must be > 0 and <= 2")
    return str(value)


def validate_format(output_format: str) -> str:
    if output_format not in SITE_FORMATS:
        raise TtsError(f"Unsupported kbitrate/output format: {output_format}")
    return output_format


def content_type_for_format(output_format: str) -> str:
    if "mp3" in output_format:
        return "audio/mpeg"
    if output_format.startswith("riff-"):
        return "audio/wav"
    return "application/octet-stream"


def with_sentence_breaks(text: str, silence: str | None) -> str:
    import re

    escaped = escape(text)
    if not silence:
        return escaped
    if not re.fullmatch(r"\d+ms", silence):
        raise TtsError("silence must look like '500ms'")
    return re.sub(r"([。！？.!?])", rf'\1<break time="{silence}"/>', escaped)


def build_ssml(payload: dict[str, Any]) -> str:
    text = str(payload.get("text", "")).strip()
    if not text:
        raise TtsError("text is required")

    language = str(payload.get("language") or DEFAULT_LANGUAGE)
    voice = str(payload.get("voice") or DEFAULT_VOICE)
    rate = normalize_percent(payload.get("rate", payload.get("speed", 0)))
    pitch = normalize_percent(payload.get("pitch", 0))
    volume = str(payload.get("volume") or "default")
    style = str(payload.get("style") or "0")
    role = str(payload.get("role") or "0")
    styledegree = normalize_styledegree(payload.get("styledegree", "1"))
    silence = str(payload.get("silence") or "")

    body = with_sentence_breaks(text, silence)
    prosody_attrs = [f'rate="{rate}"', f'pitch="{pitch}"']
    if volume not in ("", "0", "75", "default"):
        prosody_attrs.append(f'volume="{escape(volume)}"')

    inner = f"<prosody {' '.join(prosody_attrs)}>{body}</prosody>"
    express_attrs: list[str] = []
    if style not in ("", "0", "default"):
        express_attrs.append(f'style="{escape(style)}"')
        express_attrs.append(f'styledegree="{styledegree}"')
    if role not in ("", "0", "default"):
        express_attrs.append(f'role="{escape(role)}"')
    if express_attrs:
        inner = f"<mstts:express-as {' '.join(express_attrs)}>{inner}</mstts:express-as>"

    return (
        f'<speak version="1.0" xmlns="{AZURE_NS}" xmlns:mstts="{MSTTS_NS}" '
        f'xml:lang="{escape(language)}">'
        f'<voice name="{escape(voice)}">{inner}</voice>'
        "</speak>"
    )


def ssml_from_payload(payload: dict[str, Any]) -> str:
    if payload.get("type") == "SSML" or payload.get("ssml"):
        ssml = str(payload.get("ssml") or payload.get("text") or "").strip()
        if not ssml:
            raise TtsError("ssml or text is required for SSML mode")
        return ssml
    if str(payload.get("predict", "0")) == "1":
        raise TtsError("predict is a site-specific feature and is not available in Azure REST")
    return build_ssml(payload)


def azure_tts(payload: dict[str, Any], config: AzureConfig) -> tuple[bytes, str]:
    output_format = validate_format(str(payload.get("kbitrate") or payload.get("output_format") or DEFAULT_FORMAT))
    ssml = ssml_from_payload(payload)
    headers = {
        "Ocp-Apim-Subscription-Key": config.key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": output_format,
        "User-Agent": "local-tts-http-api/2.0",
    }
    request = urllib.request.Request(config.tts_url, data=ssml.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(), content_type_for_format(output_format)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise TtsError(f"Azure TTS failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise TtsError(f"Azure TTS failed: {exc.reason}") from exc


def azure_voices(config: AzureConfig) -> list[dict[str, Any]]:
    request = urllib.request.Request(config.voices_url, headers={"Ocp-Apim-Subscription-Key": config.key})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise TtsError(f"Azure voices failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise TtsError(f"Azure voices failed: {exc.reason}") from exc


def azure_dry_run(payload: dict[str, Any], config: AzureConfig | None = None) -> dict[str, Any]:
    output_format = validate_format(str(payload.get("kbitrate") or payload.get("output_format") or DEFAULT_FORMAT))
    ssml = ssml_from_payload(payload)
    region = config.region if config else os.getenv("AZURE_SPEECH_REGION", "<region>")
    endpoint = config.tts_url if config else f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    return {
        "provider": "azure",
        "method": "POST",
        "url": endpoint,
        "headers": {
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": output_format,
            "Ocp-Apim-Subscription-Key": "<redacted>",
        },
        "ssml": ssml,
    }


def resolve_provider(payload: dict[str, Any]) -> str:
    provider = str(payload.get("provider") or provider_from_env()).lower()
    if provider not in ("site", "azure"):
        raise TtsError("provider must be 'site' or 'azure'")
    return provider


def synthesize(payload: dict[str, Any], provider: str | None = None) -> tuple[bytes, str, dict[str, Any] | None]:
    selected = provider or resolve_provider(payload)
    if selected == "azure":
        audio, content_type = azure_tts(payload, config_from_env())
        return audio, content_type, None

    client = site_client_from_env()
    try:
        audio, meta = client.synthesize(payload)
    except SiteTtsError as exc:
        raise TtsError(str(exc)) from exc
    output_format = str(payload.get("kbitrate") or payload.get("output_format") or DEFAULT_FORMAT)
    return audio, content_type_for_format(output_format), meta


def dry_run(payload: dict[str, Any], provider: str | None = None) -> dict[str, Any]:
    selected = provider or resolve_provider(payload)
    if selected == "azure":
        try:
            config = config_from_env()
        except TtsError:
            config = None
        return azure_dry_run(payload, config)
    return site_client_from_env().dry_run(payload)


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalTtsHttpApi/2.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            self.write_json({"ok": True, "provider": provider_from_env()})
        elif self.path == "/formats":
            self.write_json({"formats": SITE_FORMATS, "styles": COMMON_STYLES, "roles": COMMON_ROLES})
        elif self.path == "/voices":
            provider = provider_from_env()
            try:
                if provider == "azure":
                    self.write_json({"provider": "azure", "voices": azure_voices(config_from_env())})
                else:
                    client = site_client_from_env()
                    self.write_json({"provider": "site", "voices": client.list_voices()})
            except (TtsError, SiteTtsError) as exc:
                self.write_json({"error": str(exc)}, status=400)
        elif self.path == "/capture":
            try:
                client = site_client_from_env()
                self.write_json(client.capture_summary())
            except SiteTtsError as exc:
                self.write_json({"error": str(exc)}, status=400)
        else:
            self.write_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path == "/tts":
            self.handle_tts()
            return
        if self.path == "/capture":
            self.handle_capture()
            return
        self.write_json({"error": "not found"}, status=404)

    def handle_tts(self) -> None:
        start = time.perf_counter()
        status_code: int | None = None
        error: str | None = None
        request_obj: dict[str, Any] | None = None
        response_obj: Any = None
        response_text: str | None = None
        try:
            payload = self.read_json()
            request_obj = payload
            if payload.get("dry_run"):
                dry = dry_run(payload)
                self.write_json(dry)
                response_obj = dry
                status_code = 200
                return
            audio, content_type, meta = synthesize(payload)
            if payload.get("return_json"):
                out = {
                    "provider": resolve_provider(payload),
                    "contentType": content_type,
                    "audioBase64": __import__("base64").b64encode(audio).decode("ascii"),
                    "meta": meta,
                }
                self.write_json(out)
                response_obj = {
                    "provider": out["provider"],
                    "contentType": content_type,
                    "audioBase64": f"<omitted {len(audio)} bytes>",
                    "meta": meta,
                }
                status_code = 200
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if meta and meta.get("download"):
                self.send_header("X-TTS-Download-Url", str(meta["download"]))
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            response_obj = {
                "content_type": content_type,
                "audio_bytes": len(audio),
                "meta": meta,
            }
            response_text = f"audio {len(audio)} bytes ({content_type})"
            status_code = 200
        except (TtsError, json.JSONDecodeError) as exc:
            error = str(exc)
            status_code = 400
            self.write_json({"error": str(exc)}, status=400)
            response_obj = {"error": str(exc)}
        finally:
            try:
                from console_ingest import build_io_meta, report_request

                meta = build_io_meta(request_obj=request_obj, extra={"source": "proxy_live"})
                if response_obj is not None:
                    meta["response"] = response_obj
                if response_text:
                    meta["response_text"] = response_text
                report_request(
                    proxy_id="azure-tts-http-api",
                    path="/tts",
                    status_code=status_code,
                    latency_ms=round((time.perf_counter() - start) * 1000, 2),
                    mode="tts",
                    error=error,
                    meta=meta,
                )
            except Exception:  # noqa: BLE001
                pass

    def handle_capture(self) -> None:
        try:
            payload = self.read_json()
            from browser_capture import capture_with_browser

            report = capture_with_browser(
                sample_text=str(payload.get("text") or "你好，这是浏览器抓包测试。"),
                trigger_generate=not payload.get("skip_generate"),
                headless=os.getenv("TTS_BROWSER_HEADLESS", "1") not in ("0", "false", "False"),
            )
            self.write_json(report)
        except (RuntimeError, SiteTtsError) as exc:
            self.write_json({"error": str(exc)}, status=400)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise TtsError("JSON body must be an object")
        return parsed

    def write_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def serve(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving on http://{host}:{port} (provider={provider_from_env()})", flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local HTTP TTS API for text-to-speech.cn or Azure Speech REST.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--provider", choices=["site", "azure"], default=provider_from_env())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--text")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--rate", default="0")
    parser.add_argument("--pitch", default="0")
    parser.add_argument("--style", default="0")
    parser.add_argument("--role", default="0")
    parser.add_argument("--styledegree", default="1")
    parser.add_argument("--volume", default="75")
    parser.add_argument("--silence", default="")
    parser.add_argument("--kbitrate", default=DEFAULT_FORMAT)
    parser.add_argument("--output", default="output.mp3")
    args = parser.parse_args()

    if args.text is None:
        serve(args.host, args.port)
        return

    payload = {
        "provider": args.provider,
        "text": args.text,
        "voice": args.voice,
        "language": args.language,
        "rate": args.rate,
        "pitch": args.pitch,
        "style": args.style,
        "role": args.role,
        "styledegree": args.styledegree,
        "volume": args.volume,
        "silence": args.silence,
        "kbitrate": args.kbitrate,
    }
    if args.dry_run:
        print(json.dumps(dry_run(payload, args.provider), ensure_ascii=False, indent=2))
        return
    audio, _, _ = synthesize(payload, args.provider)
    with open(args.output, "wb") as file:
        file.write(audio)
    print(args.output)


if __name__ == "__main__":
    main()
