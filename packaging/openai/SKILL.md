---
name: memory-use
description: Read and maintain the user's portable long-term memory — a private git repo of Markdown notes (servers, NAS, VPN, home/office network, infra decisions, new-computer setup) that the user owns and that follows them across AI sessions and computers. Use when the user says "remember this", "write this down", "how did we set this up", "what did we decide about X", 记一下 / 记住 / 查一下记忆 / 我们之前怎么弄的, asks about their own servers, NAS, VPN or network, or right after finishing infra or configuration work that should survive this session.
---

# memory-use

An agent's own memory stays inside one product on one machine. A private git repo of Markdown notes is memory the user owns, can read, and can take anywhere. This skill reads it before work and writes durable facts back after work.

Requirements: `git` and Python 3.9+ on the computer doing the work, plus access to the user's notes repo (for a private GitHub repo, `gh auth login` or a git credential the user set up). Nothing is sent anywhere except the user's own git remote.

The helper ships with this skill and needs only the Python standard library. Run it from this skill's directory:

```bash
M="<this skill's directory>/scripts/memory_use.py"
export MEMORY_USE_NO_UPDATE_CHECK=1 MEMORY_USE_NO_SKILL_LINKS=1   # the plugin ships updates and loads the skill
python3 "$M" doctor            # config, checkout, sync state
python3 "$M" brief vpn warp    # START HERE: best matching notes + key points, todos, section summaries
python3 "$M" search nas backup # ranked by section; glossary aliases expand terms (nas = synology = ds918 …)
python3 "$M" show vpn          # a section README (or: show vpn/todo, show nas/security.md)
python3 "$M" ls | todo [vpn] | log
python3 "$M" sync              # pull (rebase; autostash if dirty) and push local commits
python3 "$M" new photos "Photos" "Where family photos live and how they are backed up"
python3 "$M" lint              # README length, headers, stale notes, todo size, broken links
python3 "$M" check [paths]     # secret scan
python3 "$M" save vpn/servers.md vpn/todo.md -m "vpn: …"
```

## Setup

- The notes repo is `$MEMORY_USE_DIR`, else `dir` in `~/.config/memory-use/config.json`, else `~/github.com/personal-memory`. `doctor` says which.
- Existing repo: `python3 "$M" init --repo owner/notes --no-autosync` clones it and installs the pre-commit secret scan.
- No repo yet: ask the user to create an empty **private** repository on their git host, then run `init --repo owner/notes --no-autosync` and add a `README.md` listing the sections they want.
- Background sync (`autosync on`) installs a scheduled job on the user's computer. Only turn it on when the user asks.

## Before work (recall)

1. `brief <topic words>` — one compact pack is usually enough. Only then `show` a whole file or `search` for specifics.
2. Read top-down: root README → section README (key facts, troubleshooting) → an article's **Key points** → its details. Stop once you have what you need.
3. Terms expand through `glossary.md`, so any alias of a machine or service works. If a search misses because of a new nickname, add it to the glossary.
4. Treat notes as leads, not truth: paths, ports and hosts may have changed. Verify on the machine before acting, and fix the note if it was wrong.

Answer "how did we do X" from the repo first, quoting the file (`vpn/warp-switch.md`), before touching machines.

## After work (save)

Write back when the work produced something a future session needs: a new service, port, path, host, decision, workaround, gotcha, rollback, or todo. For infra work, offer to save it; for other topics, ask once ("Want me to add this to your notes?").

1. Put it in the right section (`new` if none fits). Prefer updating an existing article; keep the section README's article list current.
2. Follow the notes repo's own `AGENTS.md`. The usual rules:
   - **Never write passwords, private keys, tokens or cookies** — only where they live ("in `secrets.env` on the server").
   - Absolute dates (2026-09-15), never "yesterday".
   - Record **why** and **how to roll back**, not just what.
3. Todos go in `<section>/todo.md` as `- [ ]`; delete the line when done (git keeps history).
4. Article shape (what `lint` checks; English or Chinese headers both work):
   ```
   # Title
   > Updated: 2026-09-15 · Status: active
   **Key points**
   - 2–4 bullets: the facts and commands someone needs most
   ## Details / why / how to roll back
   ```
5. `lint` and `check`, then `save <explicit paths> -m "<section>: <what changed>"`.
   - `save` stages only the paths you name. Never `git add -A`: other sessions may be editing the same repo.
   - It refuses to commit when the scan finds a secret. Fix the note; only mark a line `memory-use: allow` for a genuine false positive.

## Secret scan

`check` flags private keys, common API tokens, JWTs, SSH key blobs, and password assignments in English or Chinese (`password=…`, `密码：…`); pointers, paths and prose pass. Values listed one per line in `~/.config/memory-use/known-secrets` (outside git, chmod 600) are caught verbatim. The pre-commit hook installed by `init` blocks any commit that fails the scan, even one made with plain git.

## Limits

- Not for personal identity data (addresses, ID numbers, bank cards). Keep those out of the notes.
- The repo cannot notify anyone on a date; put dated reminders in the user's calendar or reminders app instead.
- Works on any computer with git and Python. In a cloud environment without access to the user's notes repo, say so instead of guessing.
