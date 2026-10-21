# Changelog

## 0.4.1

- A successful `init --no-autosync` now returns zero when autosync is intentionally disabled. Other diagnostic failures still return nonzero.

## 0.4.0

- Recall glossary links and authored troubleshooting links even when the target note uses different words. Results identify the link and its source; conditions still need verification before use.
- Normalize compatibility-equivalent Unicode during search and hook matching, preserving original note text and paths.
- Use forward slashes for repository-relative note identifiers on every OS, keeping Windows recall links and evaluation output consistent.
- Keep usable source excerpts and review warnings within tight `brief` budgets. Show overdue/deprecated metadata in search, brief and show.
- Evaluate external Markdown question sets with `eval --cases --max --json`, separating ranking, final excerpt presence and invalid targets. These metrics do not claim answer correctness.
- Serialize hook-state updates and replace files atomically. Preserve corrupt hook state and report it through `doctor`.
- Share a checkout lock across save, manual sync and autosync. Contention returns nonzero after at most one second of lock waiting; retry later. Direct Git/editor writes and other computers are outside this lock.
- Return failure on rejected pushes, retry pending commits when `save` has no new changes, and abort conflicting rebases. Sync no longer autostashes other sessions' edits; divergence with uncommitted edits is left alone.
- Publish a portable source archive and SHA-256 checksum on GitHub Releases. Requires Git and Python 3.9+; no model or database dependency is added.
