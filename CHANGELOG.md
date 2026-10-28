# Changelog

**English** | [中文](CHANGELOG.zh-CN.md)

## 0.5.1

- Include body excerpts for title-only or filename-only matches, even without a key-points section.
- Accept evaluation targets containing spaces or parentheses, and backtick-quoted filenames containing commas.

## 0.5.0

- Surface explicitly declared conflicts in show, search, brief and lint, so both sources can be checked before use.
- Evaluate multi-file evidence and compare per-question results between versions. These metrics do not prove answer correctness.
- Avoid treating fenced and inline code examples as local links; handle percent-encoded paths.

## 0.4.2

- Reject multiline input and private-key blocks before vault calls. Verify the newly created UUID and exact password readback before reporting `secret put` success; failed or uncertain writes are not replayed and values stay hidden. `--generate` uses the same verified path.
- Match failed-command topics from diagnostic output or exact parsed operation targets. Ignore ordinary local shell, missing-path and encoding failures; quoted payloads and unrelated file operands no longer supply topics.
- Ask the Stop writeback question at most once per topic/session, including older duplicate records. Later failures on that topic do not re-arm the question.

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
