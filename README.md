# memory-use

**English** | [中文](README.zh-CN.md)

[Changelog](CHANGELOG.md) · [Releases & downloads](https://github.com/leeguooooo/memory-use/releases) · [User guide](docs/guide.md)

Long-term memory for Claude Code and Codex, stored as Markdown in **your own private Git repository**. Recall what you already know before work, then save verified changes for the next session or computer.

## Technical strengths

| Strength | What you get |
| --- | --- |
| **Portable Markdown + Git** | Read and edit your notes directly, audit their history, and bring them to another agent or computer. |
| **Traceable recall** | Ranked chunks, glossary aliases, Unicode normalization and authored troubleshooting links lead to source files and lines you can inspect. |
| **Failure-aware Claude Code hooks** | Surface relevant notes before retries, then prompt the agent to save a newly learned fix at turn end; deduplicate nudges within a session. |
| **Bounded context packs** | Fit useful excerpts, source locations and review warnings into a character budget, leaving more room for the task. |
| **Measurable retrieval** | Test your own questions with JSON reports that distinguish ranking misses from selection and context-budget losses. |
| **Pre-commit secret checks** | Flag common credentials and configured known secrets before committing; keep vault pointers in notes instead. |
| **Recoverable local writes** | Commits limited to named files, checkout locks, atomic hook state updates and sync retries help concurrent sessions preserve their work. |

## Install

Requires Git and Python 3.9+; the helper uses Python's standard library.

```sh
gh auth login  # for a private GitHub notes repo
curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo you/notes
```

Add `--create` to start a new private notes repo. The installer links the skill, enables secret checks and schedules sync; add `--no-autosync` for manual sync.

## Use

Ask your agent “How did we set up the VPN?” or “Remember this change and how to roll it back.” You can also use the CLI:

```sh
memory-use brief vpn warp --max 1500
memory-use save vpn/servers.md -m "vpn: update exit node"
```

See the [user guide](docs/guide.md) for commands and configuration, and [how memory works](docs/memory-lifecycle.md) for the feedback loop, implementation evidence and import gaps.

[MIT](LICENSE)
