#!/usr/bin/env bash
# 把工作区官方 remotion-* 同步到 bridge 的 cursor_skills，并盖上工厂安全桥。
# 未选这些 skill 时工厂生产路径不变；选中后 Cursor 才能 /remotion-markup 触发。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${REMOTION_SKILLS_SRC:-$ROOT/../.cursor/skills/remotion/skills}"
DEST="$ROOT/cursor_skills"
OVERLAY="$ROOT/factory_skill_overlays/remotion-create.md"

if [[ ! -d "$SRC" ]]; then
  echo "official remotion skills not found: $SRC" >&2
  exit 1
fi
if [[ ! -f "$OVERLAY" ]]; then
  echo "factory overlay missing: $OVERLAY" >&2
  exit 1
fi

mkdir -p "$DEST"
for src_dir in "$SRC"/remotion-*; do
  [[ -d "$src_dir" ]] || continue
  name="$(basename "$src_dir")"
  rm -rf "$DEST/$name"
  cp -a "$src_dir" "$DEST/$name"
  rm -rf "$DEST/$name/.git"
done

install_create_overlay() {
  local dir="$1"
  mkdir -p "$dir"
  cp "$OVERLAY" "$dir/SKILL.md"
  cp "$OVERLAY" "$dir/REFERENCE.md"
}

install_create_overlay "$DEST/remotion-create"
if [[ -d "$DEST/remotion-best-practices/remotion-create" ]]; then
  install_create_overlay "$DEST/remotion-best-practices/remotion-create"
fi

echo "synced remotion-* -> $DEST"
ls -1d "$DEST"/remotion-* 2>/dev/null | xargs -n1 basename
