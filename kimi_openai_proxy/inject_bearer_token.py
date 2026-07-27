"""向已有 kimi_storage.json 写入 Bearer 令牌。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path

from storage_state import inject_bearer_into_state, load_storage_state, storage_state_login_issue, storage_state_summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="向 storage state 注入 Bearer 令牌（可从 DevTools → Network → completion 请求头复制）"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=os.getenv("KIMI_STORAGE_STATE_FILE", "secrets/kimi_storage.json"),
    )
    parser.add_argument("token", nargs="?", help="Bearer 令牌；可带或不带 Bearer 前缀")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取令牌")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")

    token = args.token
    if args.stdin:
        token = sys.stdin.read().strip()
    if not token:
        token = getpass("粘贴 authorization（Bearer ...）: ").strip()
    if not token:
        raise SystemExit("未提供令牌")

    state = inject_bearer_into_state(load_storage_state(path), token)
    issue = storage_state_login_issue(state)
    if issue:
        raise SystemExit(issue)

    path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"已更新 {path}")
    print(storage_state_summary(state))


if __name__ == "__main__":
    main()
