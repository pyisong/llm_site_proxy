"""校验 metaso_storage.json 是否包含可用登录 Cookie。"""

from __future__ import annotations

import argparse
from pathlib import Path

from storage_state import load_storage_state, storage_state_login_issue, storage_state_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="校验秘塔 storage state")
    parser.add_argument("path", nargs="?", default="secrets/metaso_storage.json")
    args = parser.parse_args()
    path = Path(args.path)
    if not path.is_file():
        raise SystemExit(f"文件不存在: {path}")
    state = load_storage_state(path)
    issue = storage_state_login_issue(state)
    print(storage_state_summary(state))
    if issue:
        raise SystemExit(issue)
    print("OK")


if __name__ == "__main__":
    main()
