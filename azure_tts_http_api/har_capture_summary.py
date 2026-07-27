from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse


SENSITIVE_NAME_RE = re.compile(
    r"(token|cookie|session|auth|authorization|key|secret|yzm|captcha|user_id|download|url)",
    re.IGNORECASE,
)

PRIVATE_ENDPOINTS = {
    "/getSpeek.php": "private TTS generation endpoint; do not replay without explicit permission",
    "/getSpeekList.php": "voice list endpoint exposed to the web page",
    "/checkRoleStyle.php": "voice capability lookup endpoint exposed to the web page",
}


def redact_value(name: str, value: Any) -> Any:
    if SENSITIVE_NAME_RE.search(name):
        return "<redacted>"
    if isinstance(value, str) and len(value) > 120:
        return value[:80] + "...<truncated>"
    return value


def parse_post_data(post_data: dict[str, Any] | None) -> dict[str, Any]:
    if not post_data:
        return {}
    params: dict[str, Any] = {}
    for item in post_data.get("params") or []:
        name = str(item.get("name", ""))
        params[name] = redact_value(name, item.get("value", ""))
    text = post_data.get("text")
    mime = str(post_data.get("mimeType") or "")
    if text and not params:
        if "json" in mime:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return {key: redact_value(key, value) for key, value in parsed.items()}
            except json.JSONDecodeError:
                pass
        for name, value in parse_qsl(text, keep_blank_values=True):
            params[name] = redact_value(name, value)
    return params


def request_summary(entry: dict[str, Any]) -> dict[str, Any]:
    request = entry.get("request") or {}
    response = entry.get("response") or {}
    url = str(request.get("url") or "")
    parsed_url = urlparse(url)
    query = {name: redact_value(name, value) for name, value in parse_qsl(parsed_url.query, keep_blank_values=True)}
    post_params = parse_post_data(request.get("postData"))
    headers = {
        item.get("name", ""): "<redacted>"
        for item in request.get("headers") or []
        if SENSITIVE_NAME_RE.search(str(item.get("name", "")))
    }

    return {
        "method": request.get("method"),
        "host": parsed_url.netloc,
        "path": parsed_url.path,
        "status": response.get("status"),
        "mimeType": response.get("content", {}).get("mimeType"),
        "queryKeys": sorted(query.keys()),
        "postKeys": sorted(post_params.keys()),
        "redactedQuery": query,
        "redactedPost": post_params,
        "redactedHeaders": headers,
        "note": PRIVATE_ENDPOINTS.get(parsed_url.path),
    }


def summarize_har(path: Path, host_filter: str | None = None) -> dict[str, Any]:
    har = json.loads(path.read_text(encoding="utf-8"))
    entries = har.get("log", {}).get("entries", [])
    summaries = []
    status_counter: Counter[str] = Counter()
    endpoint_methods: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        summary = request_summary(entry)
        if host_filter and summary["host"] != host_filter:
            continue
        endpoint = f"{summary['method']} {summary['path']}"
        endpoint_methods[summary["path"]].add(str(summary["method"]))
        status_counter[str(summary["status"])] += 1
        if summary["path"] in PRIVATE_ENDPOINTS or summary["postKeys"]:
            summaries.append(summary)

    return {
        "source": str(path),
        "hostFilter": host_filter,
        "requestCount": len(summaries),
        "statusCounts": dict(status_counter),
        "endpoints": [
            {"path": endpoint, "methods": sorted(methods), "note": PRIVATE_ENDPOINTS.get(endpoint)}
            for endpoint, methods in sorted(endpoint_methods.items())
        ],
        "interestingRequests": summaries,
        "safety": {
            "replayGenerated": False,
            "sensitiveValuesRedacted": True,
            "message": "This report verifies interface shape only. It intentionally does not output replayable requests.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a browser HAR capture without generating replayable private API calls.")
    parser.add_argument("har", type=Path)
    parser.add_argument("--host", default="www.text-to-speech.cn")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = summarize_har(args.har, args.host)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
