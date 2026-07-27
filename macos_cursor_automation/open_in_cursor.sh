#!/usr/bin/env bash
# 用法: ./open_in_cursor.sh [路径]  ；未传路径时打开当前目录。
set -euo pipefail
TARGET="${1:-.}"
exec open -a "Cursor" -- "$TARGET"
