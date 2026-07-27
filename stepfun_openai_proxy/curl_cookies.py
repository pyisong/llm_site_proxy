from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote


def normalize_curl_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    return re.sub(r"\\\s*\n", " ", text.strip())


def extract_cookie_header(curl_text: str) -> str | None:
    text = normalize_curl_text(curl_text)
    patterns = [
        r"""-b\s+(['"])(.*?)\1""",
        r"""--cookie\s+(['"])(.*?)\1""",
        r"-b\s+(\S+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)
    return None


def extract_user_agent(curl_text: str) -> str | None:
    text = normalize_curl_text(curl_text)
    match = re.search(
        r"""-H\s+(['"])user-agent:\s*(.*?)\1""",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(2).strip() if match else None


def cookie_header_to_playwright(
    cookie_header: str,
    *,
    domain: str = ".stepfun.com",
    path: str = "/",
) -> list[dict[str, object]]:
    cookies: list[dict[str, object]] = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = unquote(value.strip())
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": True,
            }
        )
    return cookies


def parse_curl_cookies(curl_text: str, *, domain: str = ".stepfun.com") -> tuple[list[dict[str, object]], str | None]:
    cookie_header = extract_cookie_header(curl_text)
    if not cookie_header:
        raise ValueError("curl 文本中未找到 -b / --cookie 参数")
    cookies = cookie_header_to_playwright(cookie_header, domain=domain)
    if not cookies:
        raise ValueError("cookie 字符串解析结果为空")
    return cookies, extract_user_agent(curl_text)


def load_auth_from_text(text: str, *, domain: str = ".stepfun.com") -> tuple[list[dict[str, object]], str | None]:
    stripped = text.strip()
    if stripped.startswith("curl") or "-b " in stripped or "--cookie" in stripped:
        return parse_curl_cookies(stripped, domain=domain)

    payload = json.loads(stripped)
    if isinstance(payload, dict):
        cookies = payload.get("cookies", [])
        user_agent = payload.get("user_agent")
    elif isinstance(payload, list):
        cookies = payload
        user_agent = None
    else:
        raise ValueError("JSON cookie 文件必须是数组，或包含 cookies 字段的对象")

    if not isinstance(cookies, list):
        raise ValueError("cookies 必须是数组")
    return cookies, user_agent if isinstance(user_agent, str) else None


def load_auth_from_file(path: str | Path, *, domain: str = ".stepfun.com") -> tuple[list[dict[str, object]], str | None]:
    return load_auth_from_text(Path(path).read_text(encoding="utf-8"), domain=domain)


def write_cookies_json(
    cookies: list[dict[str, object]],
    output_path: str | Path,
    *,
    user_agent: str | None = None,
) -> None:
    payload: dict[str, object] = {"cookies": cookies}
    if user_agent:
        payload["user_agent"] = user_agent
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="从 curl 命令解析 StepFun cookie")
    parser.add_argument("input", nargs="?", help="curl 文件路径；省略则从 stdin 读取")
    parser.add_argument("-o", "--output", default="secrets/stepfun_cookies.json", help="输出 JSON 路径")
    parser.add_argument("--domain", default=".stepfun.com", help="cookie domain")
    args = parser.parse_args()

    if args.input:
        text = Path(args.input).read_text(encoding="utf-8")
    else:
        import sys

        text = sys.stdin.read()

    cookies, user_agent = load_auth_from_text(text, domain=args.domain)
    write_cookies_json(cookies, args.output, user_agent=user_agent)
    print(f"已写入 {len(cookies)} 个 cookie 到 {args.output}")
    if user_agent:
        print(f"user-agent: {user_agent}")


if __name__ == "__main__":
    main()
