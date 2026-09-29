# memory-use

**English** | [中文](README.zh-CN.md)

Long-term memory for AI coding agents that follows you to every session and every computer. Your notes are plain Markdown in a **private git repo you own**; this skill teaches Claude Code / Codex to read them before work, write durable facts back after work, keep them synced in the background, and bring everything to a new machine with one line.

Part of the `*-use` family: `profile-use` holds who you are, `bitwarden-use` your secrets, **memory-use how your machines and services are set up** (NAS, VPN, servers, networks, decisions, rollbacks). Notes store pointers to the other two, never values.

## Why not the agent's built-in memory?

| | Agent-local memory (`~/.claude/projects/*/memory`) | memory-use |
| --- | --- | --- |
| New computer | Gone | `curl … \| sh -s -- --repo you/notes` |
| Other agents (Codex, a second Claude session) | Can't see it | Same repo, same skill |
| You can read / edit / grep it | Hidden files | A normal repo with history |
| Secrets written by accident | Stay there | Pre-commit hook blocks them |

## Install

```sh
gh auth login   # your notes repo is private

# already have a notes repo
curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo you/notes

# starting fresh: creates a PRIVATE repo from the starter template
curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo you/notes --create
```

This links the skill into `~/.agents/skills`, `~/.claude/skills` (and `~/.codex/skills`), puts `memory-use` on your PATH (`~/.local/bin`), clones the notes, enables the secret-scan pre-commit hook, and turns on autosync. `memory-use doctor` shows the state. Any git host works: `--repo git@gitlab.com:you/notes.git`.

Also available through the family bundle: `/plugin install use-family@leeguooooo-plugins` (Claude Code) or `install-use-family.sh` from [leeguooooo/plugins](https://github.com/leeguooooo/plugins); then run `memory-use init --repo you/notes` once.

## Use

Just talk to the agent: "记一下" / "remember this", "我们之前怎么弄的 VPN" / "how did we set up the VPN", "换电脑了" / "I'm on a new computer". Under the hood:

```sh
memory-use brief vpn warp      # context pack: best matching notes + key points, todos, section summaries
memory-use search nas backup    # ranked search; glossary aliases expand each term
memory-use save vpn/servers.md -m "vpn: new exit node"   # secret-scan, commit only these files, pull --rebase, push
memory-use new photos "Photos" "where photos live and how they are backed up"
memory-use lint                # article headers, README length, stale notes, broken links
```

## Recall hook

Agents forget to look things up. The Claude Code plugin ships two hooks (`hooks/hooks.json`):

- **UserPromptSubmit**: when your message names something the notes know (any glossary alias, including colloquial ones like "windows 电脑", or a section name), the agent gets one line: `the notes know about leo-desktop — run memory-use brief leo-desktop first`. Nothing otherwise; ~50 ms, stdlib only.
- **SessionStart**: silent unless the notes repo is missing or autosync is stuck.

Installed with `install.sh` instead of the plugin? Add the same to `~/.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "memory-use hook prompt", "timeout": 5}]}],
  "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "memory-use hook session", "timeout": 5}]}]
}
```

## Sync

`memory-use autosync on` (default after `init`) runs every 15 minutes via launchd (macOS), a systemd user timer or cron (Linux), or Task Scheduler (Windows):

- fetch, then fast-forward, or rebase local commits onto the remote and push;
- **never commits for you** and never stashes: an uncommitted edit may be another agent session's half-written note, so it is left alone;
- a conflict aborts cleanly and is reported by `doctor` and `autosync status` for a human to resolve.

Agents commit with `save`, which names its files explicitly, so parallel sessions never sweep up each other's work.

## Moving to a new computer

On the old one:

```sh
memory-use migrate          # exit 1 if anything is not on GitHub yet: unpushed commits, uncommitted files, stashes
memory-use migrate --push   # push first
```

It also lists what stays behind by design (the local known-secrets list, the agent's own memory files) and prints the one line to run on the new computer. Keep a `setup-new-computer.md` in your notes for everything else (SSH keys, VPN, password manager); the template starts one.

## Secrets

The pre-commit hook (and `save`, and `check`) refuses anything that looks like a secret: private keys, GitHub / OpenAI / Anthropic / AWS / Slack tokens, JWTs, and assignments in English or Chinese (`password: …`, `密码：…`, `路由器密码是 …`). Pointers and prose pass (`🔑 bw:<uuid>`, `~/.ssh/id_ed25519`, `in secrets.env`). With [bitwarden-use](https://github.com/leeguooooo/bitwarden-use), `memory-use secret put` stores a value and prints the pointer, and `secret hashes` makes the hook catch any password from your vault's `memory` folder by hash. When [profile-use](https://github.com/leeguooooo/profile-use) is installed the hook also blocks your personal data.

## Configuration

| | |
| --- | --- |
| `~/.config/memory-use/config.json` | `{"repo": "you/notes", "dir": "~/github.com/notes"}`, written by `init` |
| `MEMORY_USE_DIR` / `MEMORY_USE_REPO` | override the checkout path / repo |
| `~/.config/memory-use/known-secrets` | literal secrets to catch, one per line, never committed |
| `MEMORY_USE_NO_UPDATE_CHECK=1` | silence the once-a-day "new version" line |

Stdlib Python 3.9+, no dependencies. `python3 -m unittest discover -s tests`.

## License

MIT
