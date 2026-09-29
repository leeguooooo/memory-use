---
name: memory-use
description: Read and maintain the user's portable long-term memory — a private git repo of Markdown notes (NAS, VPN, servers, home/office network, infra decisions, new-computer setup) that follows them across AI sessions and computers. Use when the user says 记一下 / 记住 / 存到记忆 / 更新笔记 / 查一下记忆 / 我们之前怎么弄的 / 上次那个… / 换电脑 / 新电脑, "remember this", "how did we set this up", asks about their NAS, VPN, servers or network, or right after finishing infra, network, server or config work that should survive this session. Also for setting the memory up (init), background sync (autosync) and moving to a new computer (migrate). Search, brief, todo, new section, secret-safe save, Bitwarden pointers (secret find/put/get), dated reminders into Apple Reminders (remind add/ls). Not for personal data (address, IDs, bank cards, family) — that is profile-use; notes only point to it.
---

# memory-use

AI sessions lose context, and an agent's local memory (`~/.claude/projects/*/memory`) stays on one machine. A private git repo of Markdown notes is the portable, cross-session memory. This skill reads it before work, writes durable facts back after work, keeps it synced in the background, and brings it to a new computer with one line.

Helper (stdlib Python 3.9+, run from anywhere; also on PATH as `memory-use` after `install.sh`):

```bash
M=~/.agents/skills/memory-use/scripts/memory_use.py
python3 $M doctor            # config, checkout, sync state, hook, skill links, autosync
python3 $M brief vpn warp    # START HERE: context pack — best matching notes + key points, todos, section summaries
python3 $M search nas backup  # ranked by section; glossary aliases expand terms (nas = synology = ds918 …)
python3 $M show vpn          # a section README (or: show vpn/todo, show nas/security.md)
python3 $M ls | todo [vpn] | log
python3 $M sync              # pull (rebase; autostash if dirty) and push local commits
python3 $M new photos "照片" "家里照片的存放和备份"   # scaffold a section, list it in README + AGENTS.md
python3 $M lint              # README length, 更新/要点 headers, stale notes, todo size, broken links, glossary
python3 $M check [paths]     # secret scan (also the git pre-commit hook)
python3 $M save vpn/servers.md vpn/todo.md -m "vpn: …" --trailer "Claude-Session: …"
python3 $M secret find|put|get|refs|hashes …      # Bitwarden pointers via bitwarden-use (optional)
python3 $M remind add "续费 NAS 证书" --due 2026-10-01   # dated reminder → Apple Reminders (macOS, optional)
```

## Setup, sync, new computer

```bash
python3 $M init --repo owner/notes            # clone an existing notes repo (or a git URL), hook, skill links, autosync
python3 $M init --repo owner/notes --create   # no repo yet: create a private one from the starter template
python3 $M autosync on|off|status|run         # background pull + push every 15 min (launchd / systemd / cron / schtasks)
python3 $M migrate [--push]                   # on the OLD computer: what is not on GitHub yet + the line for the new one
```

- The notes repo is `$MEMORY_USE_DIR`, else `dir` in `~/.config/memory-use/config.json` (written by `init`), else `~/github.com/personal-memory`. `doctor` says which.
- **Autosync never commits.** It fetches, fast-forwards (or rebases local commits and pushes), and leaves anything uncommitted alone, because it may be another session's half-finished edit. If it reports "left alone" or "needs a human", run `sync` and resolve by hand.
- **New computer:** `curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo <owner>/<name>` (after `gh auth login` for a private repo), then follow the notes repo's own `setup-new-computer.md` for everything else. Before leaving the old one, run `migrate`: exit 1 means something would be lost.
- If the user asks to set up memory and has no repo, offer `init --create` (it creates a **private** GitHub repo — ask first, it is outward-facing).

## Before work (recall)

1. `brief <topic words>` — one compact pack is usually enough (autosync keeps the checkout current; `sync` if `doctor` says behind). Only then `show` a whole file or `search` for specifics.
2. Read top-down: root README → section README (key facts, troubleshooting) → an article's **要点 / Key points** → its details. Stop as soon as you have what you need.
3. A term expands through `glossary.md`, so any alias of a machine or service works. If a search misses because of a new nickname, add it to the glossary.
4. Treat notes as leads, not truth: paths, ports and hosts may have changed. Verify on the machine before acting, and fix the note if it was wrong.

Answer "我们之前怎么弄的 / how did we do X" from the repo first, quoting the file (`vpn/warp-switch.md`), before touching machines.

## After work (save)

Write back whenever the work produced something a future session needs: a new service, port, path, host, decision, workaround, gotcha, rollback, or todo. Do it without being asked for infra work; for other topics, ask once ("要记到 memory 吗？").

1. Put it in the right section (`new` if none fits). Prefer updating an existing article; keep the section README's article list and troubleshooting table current.
2. Follow the notes repo's `AGENTS.md`. The usual rules:
   - **Never write passwords, private keys, tokens, cookies** — only where they live ("in `secrets.env` on the server", `🔑 bw:<uuid>`).
   - Absolute dates (2026-09-15), never "yesterday".
   - Record **why** and **how to roll back**, not just what.
   - Firewall / port / network changes that could lock the user out need an automatic rollback and approval first.
3. Todos go in `<section>/todo.md` as `- [ ]`; delete the line when done (git keeps history).
4. Article shape (what `lint` checks; Chinese or English headers both work):
   ```
   # 标题 / Title
   > 更新：2026-09-15 · 状态：在用     (or: > Updated: 2026-09-15 · Status: active)
   **要点**                            (or: **Key points**)
   - 2–4 bullets: the facts and commands someone needs most
   ## 细节 / Details / why / how to roll back
   ```
   Section READMEs stay within ~70 lines. Superseded articles move to `archive/<section>/`. New machine / service / nickname → one line in `glossary.md` (not router addresses like 192.168.1.1: every site has one).
5. `lint` and `check`, then `save <explicit paths> -m "<section>: <what changed>" --trailer "<session attribution line, if the harness gives one>"`.
   - `save` stages only the paths you name. Never `git add -A`: other sessions work in this repo concurrently — look at `git status` and leave their files alone.
   - It refuses to commit when the scan finds a secret. Fix the note; only mark a line `memory-use: allow` if it is a genuine false positive.

## Secrets

`check` flags private keys, GitHub/OpenAI/Anthropic/AWS/Slack tokens, JWTs, SSH public key blobs, and assignments in English or Chinese (`password=…`, `密码：…`, `密码是 …`, `口令 = …`); pointers, paths and prose (`bw:…`, `~/.ssh/…`, `secrets.env`, `不记录，问 X`) pass. Real secrets listed one per line in `~/.config/memory-use/known-secrets` (outside git, chmod 600) are caught verbatim; with bitwarden-use, `secret hashes` catches every password in the vault's `memory` folder by hash. The pre-commit hook blocks any commit, even one made with plain git.

`secret put "<name>"` (value on stdin, or `--generate 24`) stores into Bitwarden and prints the `🔑 bw:<uuid>` pointer for the note; `secret get bw:<uuid> --copy` or `--env VAR -- cmd` uses it without printing.

## Dates, reminders, birthdays

The repo cannot notify anyone. Anything that must fire on a date → `remind add "<title>" --due YYYY-MM-DD[ HH:MM]` (Apple Reminders, one-way; keep the todo line and append the printed `⏰` pointer). Birthdays → the person's card in Contacts, never this repo. `remind` refuses text the secret scan flags; the first run asks macOS for Automation permission (error -1743 → System Settings → Privacy & Security → Automation).

## Local agent memory vs this repo

- Durable facts → the notes repo (portable, versioned, readable by the user).
- The harness's per-machine memory → short pointers only ("VPN details live in the memory repo `vpn/`").
- When the two disagree, the repo wins after verifying on the machine.

## Upgrade

When any `memory-use` command prints `memory-use X is available`, tell the user and offer to run `memory-use upgrade` (it updates this skill checkout, and the Claude Code plugin if installed). Check without changing anything: `memory-use upgrade --check`. The user may also just say "升级 memory-use" / "upgrade memory-use".

If the skill came from somewhere `upgrade` can't refresh:
- Claude Code plugin: `claude plugin update memory-use@leeguooooo-plugins`
- Whole family: `curl -fsSL https://raw.githubusercontent.com/leeguooooo/plugins/main/upgrade-use-family.sh | sh`
