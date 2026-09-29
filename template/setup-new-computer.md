# Setting up a new computer

> Updated: 2026-09-29 · Status: active

**Key points**
- One line brings back the skill, this repo, the secret-scan hook and background sync.
- Before leaving the old computer: `memory_use.py migrate` lists what is not on GitHub yet.

## 1. Notes and skill

```bash
gh auth login                      # this repo is private
curl -fsSL {{INSTALL_URL}} | sh -s -- --repo {{REPO}}
```

That clones `{{REPO}}`, links the memory-use skill for Claude Code / Codex, enables the pre-commit
secret scan, and turns on autosync (pull + push every 15 minutes; it never commits for you).
Check with `memory_use.py doctor`.

## 2. Everything else

Add what your own machines need below: SSH keys and hosts, VPN clients, password manager, dotfiles.
