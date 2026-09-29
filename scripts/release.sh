#!/bin/sh
# Release memory-use: bump VERSION, test, tag, publish the GitHub Release, then sync the plugin marketplace
# so Claude Code plugin installs pick the new version up right away (no token: uses your `gh` login).
#   scripts/release.sh 0.2.2 ["release notes"]
set -eu
run_ok() {  # run_ok <run-id> [-R owner/repo]: wait until the run completes (gh run watch can drop on a network error), then require success
  _r=$1; shift
  until [ "$(gh run view "$_r" "$@" --json status -q .status 2>/dev/null)" = completed ]; do gh run watch "$_r" "$@" >/dev/null 2>&1 || sleep 15; done
  [ "$(gh run view "$_r" "$@" --json conclusion -q .conclusion)" = success ]
}
V=${1:?usage: scripts/release.sh <version> [notes]}
NOTES=${2:-}
MARKETPLACE=leeguooooo/plugins
cd "$(dirname "$0")/.."

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "error: not on main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "error: working tree not clean" >&2; exit 1; }
git pull -q --ff-only
git rev-parse -q --verify "refs/tags/v$V" >/dev/null && { echo "error: v$V already exists" >&2; exit 1; }

sed -i.bak "s/^VERSION = \".*\"/VERSION = \"$V\"/" scripts/memory_use.py && rm scripts/memory_use.py.bak
python3 -m unittest discover -s tests -q
sh -n install.sh
if ! git diff --quiet; then git commit -qam "$V"; fi
git tag "v$V"
git push -q origin main "v$V"
if [ -n "$NOTES" ]; then
  gh release create "v$V" --title "memory-use $V" --notes "$NOTES"
else
  gh release create "v$V" --title "memory-use $V" --generate-notes
fi

# The marketplace reads the version from the latest release tag; run its sync now instead of waiting for the hourly cron.
gh workflow run auto-sync-versions.yml -R "$MARKETPLACE"
sleep 5
RUN=$(gh run list -R "$MARKETPLACE" -w auto-sync-versions.yml -e workflow_dispatch -L 1 --json databaseId -q '.[0].databaseId')
run_ok "$RUN" -R "$MARKETPLACE" && echo "marketplace synced" || echo "warn: marketplace sync run $RUN failed; the hourly run will retry"
gh api "repos/$MARKETPLACE/contents/.claude-plugin/marketplace.json" -q .content | base64 -d \
  | python3 -c "import json,sys; print('marketplace memory-use:', next(p['version'] for p in json.load(sys.stdin)['plugins'] if p['name']=='memory-use'))"
