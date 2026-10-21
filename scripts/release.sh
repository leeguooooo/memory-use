#!/bin/sh
# Release memory-use: bump VERSION, test, tag, publish the GitHub Release, then sync the plugin marketplace
# so Claude Code plugin installs pick the new version up right away (no token: uses your `gh` login).
#   scripts/release.sh 0.2.2 ["release notes"]
set -eu
run_ok() {  # Poll the exact run; network/watch interruptions are not a success.
  _r=$1; shift
  _tries=0
  until [ "$(gh run view "$_r" "$@" --json status -q .status 2>/dev/null)" = completed ]; do
    _tries=$((_tries + 1))
    [ "$_tries" -lt 120 ] || { echo "error: timed out waiting for run $_r" >&2; return 1; }
    sleep 10
  done
  [ "$(gh run view "$_r" "$@" --json conclusion -q .conclusion)" = success ]
}
workflow_run() {  # workflow_run <workflow-file> <event> <commit>
  _tries=0
  while :; do
    _run=$(gh run list -w "$1" -e "$2" --commit "$3" -L 5 --json databaseId -q '.[0].databaseId // empty')
    [ -z "$_run" ] || { echo "$_run"; return; }
    _tries=$((_tries + 1))
    [ "$_tries" -lt 30 ] || { echo "error: no $1 run for $3" >&2; return 1; }
    sleep 5
  done
}
V=${1:?usage: scripts/release.sh <version> [notes]}
NOTES=${2:-}
MARKETPLACE=leeguooooo/plugins
cd "$(dirname "$0")/.."
python3 -c 'import re,sys; assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", sys.argv[1]), "invalid version"' "$V"

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "error: not on main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "error: working tree not clean" >&2; exit 1; }
git pull -q --ff-only
git rev-parse -q --verify "refs/tags/v$V" >/dev/null && { echo "error: v$V already exists" >&2; exit 1; }

sed -i.bak "s/^VERSION = \".*\"/VERSION = \"$V\"/" scripts/memory_use.py && rm scripts/memory_use.py.bak
python3 -m unittest discover -s tests -q
sh -n install.sh
if ! git diff --quiet; then git commit -qam "chore(release): 发布 v$V"; fi
SHA=$(git rev-parse HEAD)
git push -q origin main
# Tag only the candidate commit whose complete CI and security checks passed.
run_ok "$(workflow_run test.yml push "$SHA")"
run_ok "$(workflow_run plugin-security.yml push "$SHA")"
git tag "v$V" "$SHA"
git push -q origin "v$V"
if [ -n "$NOTES" ]; then
  gh release create "v$V" --title "memory-use $V" --notes "$NOTES"
else
  gh release create "v$V" --title "memory-use $V" --generate-notes
fi
run_ok "$(workflow_run release-assets.yml release "$SHA")"

# The marketplace reads the version from the latest release tag; run its sync now instead of waiting for the hourly cron.
gh workflow run auto-sync-versions.yml -R "$MARKETPLACE"
sleep 5
RUN=$(gh run list -R "$MARKETPLACE" -w auto-sync-versions.yml -e workflow_dispatch -L 1 --json databaseId -q '.[0].databaseId')
run_ok "$RUN" -R "$MARKETPLACE" || { echo "error: marketplace sync run $RUN failed" >&2; exit 1; }
gh api "repos/$MARKETPLACE/contents/.claude-plugin/marketplace.json" -q .content | base64 -d \
  | python3 -c "import json,sys; v=next(p['version'] for p in json.load(sys.stdin)['plugins'] if p['name']=='memory-use'); print('marketplace memory-use:',v); assert v == sys.argv[1], 'marketplace version mismatch'" "$V"
