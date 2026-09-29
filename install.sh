#!/bin/sh
# Install memory-use: the skill for Claude Code / Codex, a `memory-use` command, and optionally your notes repo.
#   curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo owner/notes
# Any arguments are passed to `memory_use.py init` (--repo, --dir, --create, --no-autosync, --every N).
# Re-run to update. Needs git and python3; a private notes repo also needs `gh auth login` first.
set -eu
command -v git >/dev/null 2>&1 || { echo "error: git is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "error: python3 is required" >&2; exit 1; }

# Run from a checkout → use it; piped from curl → keep a checkout under ~/.agents/use-family like the rest of the family.
SELF_DIR=""
case "$0" in */install.sh) SELF_DIR=$(cd "$(dirname "$0")" && pwd) ;; esac
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/SKILL.md" ] && [ -f "$SELF_DIR/scripts/memory_use.py" ]; then
  ROOT=$SELF_DIR
else
  ROOT="${MEMORY_USE_HOME:-${USE_FAMILY_DIR:-$HOME/.agents/use-family}/memory-use}"
  if [ -d "$ROOT/.git" ]; then
    git -C "$ROOT" pull -q --ff-only || echo "warn: $ROOT not updated (local changes?)"
  else
    mkdir -p "$(dirname "$ROOT")"
    git clone -q --depth 1 https://github.com/leeguooooo/memory-use.git "$ROOT"
  fi
fi

link() {  # link <target> <link-path>; never replaces a real directory
  if [ -e "$2" ] && [ ! -L "$2" ]; then
    echo "skip: $2 exists and is not a symlink (move it aside, then re-run)"
  else
    ln -sfn "$1" "$2" && echo "linked: $2 -> $1"
  fi
}

mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills" "$HOME/.local/bin"
link "$ROOT" "$HOME/.agents/skills/memory-use"
# Claude Code plugin installed → it already provides the skill; a second copy would load twice.
if grep -q '"memory-use@' "$HOME/.claude/plugins/installed_plugins.json" 2>/dev/null; then
  echo "skip: $HOME/.claude/skills/memory-use (Claude Code plugin memory-use already provides the skill)"
else
  link "../../.agents/skills/memory-use" "$HOME/.claude/skills/memory-use"
fi
[ -d "$HOME/.codex/skills" ] && link "$ROOT" "$HOME/.codex/skills/memory-use"
chmod +x "$ROOT/scripts/memory_use.py"
link "$ROOT/scripts/memory_use.py" "$HOME/.local/bin/memory-use"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to PATH to run \`memory-use\` directly" ;; esac

if [ $# -gt 0 ]; then
  python3 "$ROOT/scripts/memory_use.py" init "$@"
elif python3 "$ROOT/scripts/memory_use.py" path >/dev/null 2>&1 && [ -d "$(python3 "$ROOT/scripts/memory_use.py" path)/.git" ]; then
  python3 "$ROOT/scripts/memory_use.py" doctor || true
else
  echo
  echo "Skill installed. Now connect your notes repo:"
  echo "  memory-use init --repo <owner>/<name>            # an existing repo (clone + hook + autosync)"
  echo "  memory-use init --repo <owner>/<name> --create   # a new private repo from the starter template"
fi
