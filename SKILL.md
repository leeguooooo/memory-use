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
python3 $M sync              # pull and push local commits; never stash other sessions’ edits
python3 $M new photos "照片" "家里照片的存放和备份"   # scaffold a section, list it in README + AGENTS.md
python3 $M lint              # README length, 更新/要点 headers, stale notes, near-duplicate articles, todo size, broken links, glossary
python3 $M eval              # hit@1 / hit@3 of search over evals.md, glossary 见-links and article titles
python3 $M lessons           # topics failed commands keep hitting across sessions; "rewrite this note" = pointed at, never fixed
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
- **Autosync never commits.** It fetches, fast-forwards (or rebases local commits and pushes), and leaves anything uncommitted alone, because it may be another session's half-finished edit. `save` and `sync` use the same conservative sync rules. If it reports "left alone" or "needs a human", inspect the state and resolve with the owner of those edits; repeating `sync` will not stash them.
- **New computer:** `curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo <owner>/<name>` (after `gh auth login` for a private repo), then follow the notes repo's own `setup-new-computer.md` for everything else. Before leaving the old one, run `migrate`: exit 1 means something would be lost.
- If the user asks to set up memory and has no repo, offer `init --create` (it creates a **private** GitHub repo — ask first, it is outward-facing).

## Before work (recall)

When a hook line says `memory-use: the notes know about X` (on a user message) or `that command failed and the notes know about X` (after a failed command), run the suggested `brief` before doing anything else — and before retrying the command — the notes usually hold the gotcha you are about to rediscover. New nicknames the user uses for a machine or service ("windows 电脑") belong in `glossary.md`, so the hook and search recognise them next time.

1. `brief <topic words>` — one compact pack is usually enough (autosync keeps the checkout current; `sync` if `doctor` says behind). Only then `show` a whole file or `search` for specifics.
2. Read top-down: root README → section README (key facts, troubleshooting) → an article's **要点 / Key points** → its details. Stop as soon as you have what you need.
3. A term expands through `glossary.md`, so any alias of a machine or service works. If a search misses because of a new nickname, add it to the glossary.
4. Treat notes as leads, not truth: paths, ports and hosts may have changed. Verify on the machine before acting, and fix the note if it was wrong.

`glossary.md` can point a concept directly to an article (`- nickname / other name — see vpn/route.md`); search and brief mark these results `[glossary link]`, even if the article uses different words. A `WARN` about an overdue review or deprecated status means verify the current setup before using its instructions. Reading does not refresh the note's update date.

Answer "我们之前怎么弄的 / how did we do X" from the repo first, quoting the file (`vpn/warp-switch.md`), before touching machines. `no match` means this query found nothing; it does not prove the notes lack the answer. Retry concrete words once, or clarify the machine/service when the question is ambiguous. Do not fill the gap from general knowledge as if it were their setup.

When recall missed — `brief` did not surface a note that turned out to hold the answer — add the question to the notes repo's `evals.md` as `- <the words you searched> → <section>/<article>.md`, and fix the cause if it is obvious (a missing glossary alias, a vague title, a `见 article.md` pointer). `eval` then keeps that question answered as the notes grow.

`brief` searches keywords. If a full sentence returns no match or only unrelated notes, retry once with 2–4 concrete topic/symptom words from the question or its established context (for example `SSH 桌面 看不到`). Inspect the returned text; a filename alone is not proof that it answers the question. If the retry still misses, report the gap. For evaluation, keep original questions and rewritten keyword queries in separate sets: `eval --cases /outside-notes/questions.md --max 1500 --json` also checks whether the final budgeted brief retains a note entry and excerpt. Do not report a successful retry as first-attempt recall.

`[symptom link]` means a section README's Troubleshooting table explicitly points to that article. `trigger:` shows the routing source, not answer evidence. Read the article excerpt and check that it contains the cause/fix you need; matching a condition or a file path alone is insufficient. This is conservative literal routing, not general paraphrase understanding.

Confirm that the full trigger applies, including a machine, OS or prior action omitted from the query. A retrieved condition is not proof that the user already performed that action. If the scope is uncertain, use the note as a lead and clarify before applying its instructions.

When rewriting a query, keep the machine/service identifiers, negation and explicit constraints. Do not add a guessed service to resolve an ambiguous question, or replace a failure symptom with its affirmative form.

An explicit `> Conflicts: [other](other.md)` or `> 冲突：[other](other.md)` marks unresolved disagreement. Check both sources and their scope before acting; do not assume either wins.

## After work (save)

When a Stop hook line says `a command failed on X … write it into the matching article now`, answer it before finishing: if you learned how to get past the failure and the notes don't say it, save it (symptom, cause, fix, how you verified); otherwise just finish.

Write back whenever the work produced something a future session needs: a new service, port, path, host, decision, workaround, gotcha, rollback, or todo. Do it without being asked for infra work; for other topics, ask once ("要记到 memory 吗？").

1. Put it in the right section (`new` if none fits). Prefer updating an existing article; keep the section README's article list and troubleshooting table current.
2. Follow the notes repo's `AGENTS.md`. The usual rules:
   - **Never write passwords, private keys, tokens, cookies** — only where they live ("in `secrets.env` on the server", `🔑 bw:<uuid>`).
   - Absolute dates (2026-09-15), never "yesterday".
   - Record **why** and **how to roll back**, not just what.
   - Keep the source and verification date clear. Distinguish an observed result, a user-reported fact, and an unexecuted proposal; never rewrite a suggestion as an installed or verified setup.
   - Firewall / port / network changes that could lock the user out need an automatic rollback and approval first.
3. Todos go in `<section>/todo.md` as `- [ ]`; delete the line when done (git keeps history).
4. Article shape (what `lint` checks; Chinese or English headers both work):
   ```
   # 标题 / Title
   > 更新：2026-09-15 · 状态：在用     (or: > Updated: 2026-09-15 · Status: active)
                                      optional `· 复核：30d` / `· Review: 30d` (lint flags it after 30 days
                                      instead of 120); `复核：长期` / `Review: never` for facts that don't drift
   **要点**                            (or: **Key points**)
   - 2–4 bullets: the facts and commands someone needs most — include when it applies
     (the symptom or error that should bring this note to mind) and how to verify the fix
   ## 细节 / Details / why / how to roll back
   ```
   Section READMEs stay within ~70 lines. Superseded articles move to `archive/<section>/`. New machine / service / nickname → one line in `glossary.md` (not router addresses like 192.168.1.1: every site has one).
5. `lint` and `check`, then `save <explicit paths> -m "<section>: <what changed>" --trailer "<session attribution line, if the harness gives one>"`.
   - `save` stages only the paths you name. Never `git add -A`: other sessions work in this repo concurrently — look at `git status` and leave their files alone.
   - It pushes only to the bound private notes repo; when it says `committed locally, not synced: …` (wrong remote, repo public, `gh` can't see it), tell the user that line — don't push by hand around it. `--no-push` commits only.
   - A failed sync keeps the local commit. Repeating `save` retries pending commits even without another edit; check the exit code. Divergence with uncommitted edits is left alone, and rebase conflicts are aborted. Resolve with the owner of any other session's edits before retrying.
   - `save`, `sync`, and autosync share a checkout operation lock. Busy means failure with no entry into that transaction; retry later instead of deleting the lock file or bypassing it with Git. `--no-push` skips this command's push; later sync may push the commit.
   - It refuses to commit when the scan finds a secret. Fix the note; only mark a line `memory-use: allow` if it is a genuine false positive.

## Secrets

`check` flags private keys, GitHub/OpenAI/Anthropic/AWS/Slack tokens, JWTs, SSH public key blobs, and assignments in English or Chinese (`password=…`, `密码：…`, `密码是 …`, `口令 = …`); pointers, paths and prose (`bw:…`, `~/.ssh/…`, `secrets.env`, `不记录，问 X`) pass. Real secrets listed one per line in `~/.config/memory-use/known-secrets` (outside git, chmod 600) are caught verbatim; with bitwarden-use, `secret hashes` catches every password in the vault's `memory` folder by hash. The pre-commit hook blocks any commit, even one made with plain git.

`secret put "<name>"` accepts a single-line password on stdin, or `--generate 24`, and reports a `🔑 bw:<uuid>` pointer only after checking the new item and exact readback. Multiline secrets and private-key blocks are refused before writing; use `bwu item put` with native SSH-key or secure-note JSON. If verification fails, the item may already exist: inspect it before retrying rather than creating another copy. `secret get bw:<uuid> --copy` or `--env VAR -- cmd` uses a value without printing.

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
