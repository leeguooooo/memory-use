# User guide

**English** | [中文](guide.zh-CN.md)

[Project overview](../README.md) · [Changelog](../CHANGELOG.md)

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
memory-use lint                # article headers, README length, stale notes, near-duplicates, broken links
memory-use eval                # how often search ranks the right note first (evals.md, glossary 见-links, troubleshooting rows, titles)
memory-use lessons             # notes that failed commands keep running into, and whether a fix was ever saved
```

For a focused topic, `brief` keeps at most two chunks per note, drops weak matches, and includes only matching todos, summary lines, and commit subjects. A section name or its alias alone (`brief nas` / `brief pan`), or no topic, retains the section overview. `--max 1500` caps the context body including its truncation notice (minimum 128 characters). Repeating a term or its synonyms does not change its weight; `#` inside Markdown code fences is not a heading.

A glossary entry such as `- broken exit / gateway-recovery — see vpn/route.md` recalls that article even when its text uses different words; results mark the entry with `[glossary link]`. `search`, `brief`, and `show` warn when a note's update date exceeds its review interval (default 120 days; `Review: 30d` overrides it, `Review: never` disables age warnings), or its status is deprecated. Reading a note does not refresh its date.

Search and recall hooks compare Unicode compatibility forms consistently: `ＷＡＲＰ` matches `WARP`, and composed/decomposed accents match. Notes, displayed excerpts, and file paths keep their original text. This is text normalization, not semantic search.

Under a tight `brief` budget, long entries become marked excerpts so later relevant sources can still fit; source paths and review warnings stay attached. Background summaries use the remaining space. Concurrent hooks serialize local state updates; unreadable state is preserved and reported by `doctor`. See the [fault experiments and limits](deep-experiments.md).

Existing section README Troubleshooting tables also act as explicit recall links. A supported symptom query can reach the linked article even when its body uses different wording; `[symptom link]` and `trigger:` identify this route. Entity tokens and negation markers must match. The trigger is navigation metadata; check the article's actual excerpt for the answer. See the [question-to-evidence checks](recall-evaluation.md).

`eval` reports search ranking and whether the expected note has an entry and excerpt in the final `brief`. Test your own questions with `memory-use eval --cases /path/outside-notes/questions.md --max 1500 --json`, using `- question words → section/article.md` lines. External questions stay outside the searchable corpus. Results distinguish missing matches, filtered notes, and budget/excerpt losses; invalid targets are reported separately. Title and glossary checks are navigation checks, not evidence that natural questions work. See [evaluation method and measured gaps](recall-evaluation.md).

## Recall hook

Agents forget to look things up. The Claude Code plugin ships four hooks (`hooks/hooks.json`):

- **UserPromptSubmit**: when your message names something the notes know (any glossary alias, including colloquial ones like "windows 电脑", or a section name), the agent gets one line: `the notes know about leo-desktop — run memory-use brief leo-desktop first`. Nothing otherwise; ~50 ms, stdlib only.
- **PostToolUseFailure** (Bash): when a command fails and the command or its error names something the notes know, the agent gets the same pointer before it retries: `that command failed and the notes know about nas — run memory-use brief nas`. When the same command fails a third time without naming anything known, its program and error words are searched across the notes, and a note that mentions that program is pointed at. At most once per concept or note per session, so a retry loop does not repeat it.
- **Stop**: after one of those failure pointers, when the agent is about to end its turn and nothing was saved to the notes since, it is asked once whether the way past the failure belongs in the notes. Silent otherwise. (PreCompact and SessionEnd output cannot reach the model, so this is the last point where a lesson can still be written before the context is compacted away.)
- **SessionStart**: silent unless the notes repo is missing or autosync is stuck.

Installed with `install.sh` instead of the plugin? Add the same to `~/.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "memory-use hook prompt", "timeout": 5}]}],
  "PostToolUseFailure": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "memory-use hook tool", "timeout": 5}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "memory-use hook stop", "timeout": 5}]}],
  "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "memory-use hook session", "timeout": 5}]}]
}
```

## Sync

`memory-use autosync on` (default after `init`) runs every 15 minutes via launchd (macOS), a systemd user timer or cron (Linux), or Task Scheduler (Windows):

- fetch, then fast-forward, or rebase local commits onto the remote and push;
- **never commits for you** and never stashes: an uncommitted edit may be another agent session's half-written note, so it is left alone;
- a conflict aborts cleanly and is reported by `doctor` and `autosync status` for a human to resolve.

Agents commit with `save`, which names its files explicitly, so parallel sessions never sweep up each other's work.

`save` and `sync` use the same conservative sync rules as autosync. A rejected push returns nonzero and keeps the local commit; repeating `save` retries pending commits even when those files have no new changes. `--no-push` still stays local. A diverged checkout with uncommitted edits is left alone, and a conflicting rebase is aborted. See the [Letta comparison and recovery experiments](letta-sync-study.md).

All three commands share a lock for the checkout's Git operations, including staging and committing in `save`. A busy checkout returns nonzero after at most one second of waiting; retry later. Linked worktrees have separate locks. `--no-push` skips pushing in that command; later sync can still push the commit. See the [process concurrency experiments](repository-concurrency-study.md).

**Pushes go only to the bound private repo.** Before `save`, `sync` or autosync pushes anything, memory-use works out where the push would really go (`branch.pushRemote`, `remote.pushDefault`, every `pushurl`, `insteadOf` rewrites) and refuses unless each URL is the repo `init` bound this computer to. On GitHub it also asks the API that the repo is still **private** and has the same id as when it was first verified (a deleted-and-recreated or transferred repo does not count). Anything it cannot verify — `gh` logged out or on an account that can't see the repo, offline, a public repo — means no push: the commit stays local and `doctor` / `autosync status` say why. The push then names the verified remote and branch explicitly. Other hosts (GitLab, self-hosted, local paths) get the URL check only; `doctor` says visibility is not verifiable there. `save --no-push` commits without pushing.

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
Release: `scripts/release.sh <version> [notes]` — bumps, tests, tags, publishes, and syncs the plugin marketplace.
Update both changelogs together. Release notes should put English first, followed by Chinese, and link to the corresponding changelog.

## License

MIT
