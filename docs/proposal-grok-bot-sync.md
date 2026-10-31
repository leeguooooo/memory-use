# Proposal: fast two-way sync between Grok Bot agent memory and memory-use

Status: proposal only, nothing implemented.

## Gap

- memory-use keeps notes as Markdown sections in a private git repo (e.g. `leeguooooo/personal-memory`), synced by `sync` / `autosync`, written via `save` (secret scan → commit named paths → pull --rebase → push), read via `search` / `brief` / `show`.
- Grok Bot keeps its agent memory in a hosted store (profile facts + dated journal), reachable only through the bot's own memory tools, not as files on the box.
- No bridge exists. Facts learned in one are invisible to the other, and `docs/memory-lifecycle.md` already lists "agent-decided import" as a missing workflow.

## Proposed approach

1. **Exchange format: one section per agent**, e.g. `agents/grok-bot/`:
   - `facts.md` — one fact per line: `- <fact> <!-- id:<hash> learned:<date> source:grok-bot -->`.
   - `journal.md` — dated entries, append-only.
   - Front matter: `exported_at`, `agent_id`, `schema: 1`.
2. **`memory-use agent export --agent grok-bot --from <file|->`**: takes a JSON list of facts (what the bot dumps from its memory tool), normalizes to the format above, dedupes by id, runs `check`, then `save` with a fixed trailer (`Memory-Source: grok-bot`).
3. **`memory-use agent import --agent grok-bot --since <rev> --json`**: emits a bounded JSON list of *changed* facts since the last import revision (diff-based, not whole-repo), so the bot ingests only deltas into its own memory, with provenance. Scope controlled by `--sections a,b` allowlist; default is the agent's own section only.
4. **Trigger**: the bot runs export after it writes memory and import at session start (or a scheduled routine every N minutes). Reuses `sync` locking; no new daemon. Works without crontab (see #14).
5. **State**: last imported/exported revision stored in `~/.config/memory-use/agents/<agent>.json`.

## Open questions

- **Conflict resolution**: last-writer-wins by `learned` date, or hold conflicts in `agents/<agent>/conflicts.md` for the agent to decide? (Lifecycle doc suggests the agent decides.)
- **Scope / privacy**: which sections may an agent import by default? Cards, finance, visa-type sections should likely be opt-in only.
- **Deletes**: how does a "forget" in one store propagate — tombstone line, or removal + commit trailer?
- **Format**: Markdown lines vs. JSONL file for machine round-trips; per-fact ids stable across rewording?
- **Secrets**: rely on existing `check` + pre-commit scan, or also block categories (addresses, phone numbers) at export?
- **Multiple agents**: one section per agent vs. shared `agents/shared.md` with source tags?
