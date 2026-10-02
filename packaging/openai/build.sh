#!/bin/sh
# Build the OpenAI plugin directory ZIP (Codex format, skills only) for upload at platform.openai.com/plugins.
#   sh packaging/openai/build.sh [out-dir]   → <out-dir>/memory-use-<version>.zip
# The directory version differs from the Claude Code skill: no hooks (the directory rejects them),
# no bundled template, no installer or Bitwarden steps. Its SKILL.md lives next to this script.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=${1:-$ROOT/dist}
VERSION=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$HERE/.codex-plugin/plugin.json")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/.codex-plugin" "$STAGE/assets" "$STAGE/skills/memory-use/scripts" "$OUT"
cp "$HERE/.codex-plugin/plugin.json" "$STAGE/.codex-plugin/"
cp "$HERE/assets/logo.png" "$STAGE/assets/"
cp "$HERE/SKILL.md" "$STAGE/skills/memory-use/SKILL.md"
cp "$ROOT/scripts/memory_use.py" "$STAGE/skills/memory-use/scripts/"
cp "$ROOT/LICENSE" "$STAGE/"

ZIP="$OUT/memory-use-$VERSION.zip"
rm -f "$ZIP"
(cd "$STAGE" && find . -type f | sort | zip -q -X "$ZIP" -@)
echo "$ZIP"
