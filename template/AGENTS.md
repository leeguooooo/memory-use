# Notes for AI agents

This repo is the owner's portable long-term memory: an agent's context and local memory stay behind on
one machine, this repo follows them everywhere. It is managed by the
[memory-use](https://github.com/leeguooooo/memory-use) skill.

## Before work

1. `memory_use.py sync` (autosync usually already did), then `memory_use.py brief <topic words>`.
2. Read top-down: this README → the section README (key facts, troubleshooting) → an article's key points.
3. Notes are leads, not truth: verify paths, ports and hosts on the machine, and fix the note if it was wrong.

## After work

- Write new facts, changes, reasons and rollbacks back into the right section; open items into its `todo.md`.
- Commit with `memory_use.py save <files…> -m "<section>: <what changed>"`: it commits only the files you
  name, scans for secrets, then pulls and pushes. One change per commit.
- **Never `git add -A`**: other sessions may have uncommitted work here.

## Article shape (`memory_use.py lint` checks it)

```
# Title
> Updated: 2026-09-15 · Status: active | done | deprecated
**Key points**
- 2–4 bullets: the facts and commands someone needs most
## Details / why / how to roll back
```

Section READMEs stay within ~70 lines. Superseded articles move to `archive/<section>/`. New machine, service
or nickname → one line in [glossary.md](glossary.md) so search finds it by any name.

## Secrets and personal data: pointers only

- Passwords, tokens, keys: in a password manager; the note says where (`🔑 bw:<uuid>` with bitwarden-use).
- Personal data (address, IDs, family): in profile-use; the note says `profile-use: <dotpath>`.
- The pre-commit hook blocks anything that looks like a secret.

## Rules

- Changes that can lock the owner out (firewall, ports, network) need an automatic rollback.
- Keep notes concise and written for someone coming back months later.

## Sections

<!-- `memory_use.py new` appends `- \`name/\` — description` lines here. -->
