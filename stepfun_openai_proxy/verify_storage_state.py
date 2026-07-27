"""检查 stepfun_storage.json 是否包含完整登录信息。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from storage_state import load_storage_state, storage_state_login_issue, storage_state_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="校验 StepFun storage state 文件")
    parser.add_argument(
        "path",
        nargs="?",
        default=os.getenv("STEPFUN_STORAGE_STATE_FILE", "secrets/stepfun_storage.json"),
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")

    state = load_storage_state(path)
    print(storage_state_summary(state))
    issue = storage_state_login_issue(state)
    if issue:
        print(f"校验失败: {issue}", file=sys.stderr)
        raise SystemExit(1)
    print("校验通过。")


if __name__ == "__main__":
    main()
