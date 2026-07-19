# Context Vault v3 — Design Plan

## Status

Draft for review, 2026-07-19. Direction agreed in discussion; pillar order and
per-pillar scope need maintainer's sign-off before any implementation plan is
written. Prerequisites (v2.1 topic layer, capture hooks) are approved
separately and are not part of this document's scope.

## Framing

- **v1** made memory *durable* (personal vault, bitemporal records, consent
  gate).
- **v2** made memory *shared* (team vaults, locked git sync, attribution,
  disputes, no-loss conflicts).
- **v3** makes memory *self-sustaining and ubiquitous*: useful at volume
  (retrieval), available to any agent (MCP), and populated without depending
  on anyone's discipline (ambient capture) — all without weakening the two
  load-bearing invariants: **the vault is human-readable Markdown** and
  **nothing enters it without human approval**.

## Prerequisites (approved, pre-v3)

- **v2.1 topic layer:** topic = project; `repos` auto-stamped on records from
  the recording workspace's git remote; `--project` disambiguation when one
  workspace maps to several topics; `status: done` retires topics from
  routing; briefs group by repo.
- **Capture hooks (Tier 1/2):** SessionStart injects the brief; SessionEnd
  leaves a pending-capture marker that the next session turns into a proposal.
  Tier 3 (auto-record sessions) exists as a config flag, default off.

These ship before v3 and are assumed by it: V3B builds retrieval on topics and
repo facets; V3C upgrades the marker queue into a draft queue.

---

## V3A — MCP server: memory for any agent

**Problem.** v2 reaches agents through hand-written adapters (Codex skill,
Claude Code plugin). Every other tool — Cursor, Windsurf, custom Pydantic AI
agents, a teammate's setup — is locked out. For a product aimed at dev teams,
per-harness adapters do not scale; MCP is the standard everyone speaks.

**Design.**

- A new `scripts/context_vault_mcp.py`: an MCP server over stdio, wrapping the
  same functions the CLI uses (in-process import, not subprocess). Standard
  library only — MCP's stdio transport is JSON-RPC 2.0, small enough to
  implement without the SDK, keeping the zero-dependency property.
- Tools exposed: `vault_brief(workspace?, project?, focus?)`,
  `vault_query(...)`, `vault_propose_fact / vault_propose_decision /
  vault_propose_session`, `vault_record_fact / vault_record_decision /
  vault_record_session` (each requiring `confirm: true`), `vault_doctor()`,
  `vault_sync()`, `vault_list()`.
- **The consent gate maps onto MCP's approval model.** Propose tools are
  read-only annotated; record tools are destructive-annotated so every host
  surfaces a human approval prompt. Server instructions carry the write
  protocol (propose → show → approve → record) and the team-vault
  presentation rules (claimed attribution, dispute display, visibility
  warning), so hosts without our skill still behave correctly.
- Identity/agent stamping: same config file; the `agent` stamp comes from the
  MCP client name in the `initialize` handshake (e.g., `cursor`), overridable
  by env.
- Registration is one command per host (`claude mcp add`, Cursor/Windsurf
  config JSON); the existing skills remain the richer integration for Codex
  and Claude Code.

**Success criteria.** An agent in a host we never wrote an adapter for can:
get a correct brief for a workspace, walk the propose→approve→record flow with
the host's own approval UI, and land an attributed, synced record in a team
vault — with no Context Vault code installed beyond the MCP server entry.

## V3B — Retrieval intelligence: briefs that survive volume

**Problem.** A brief is currently *all* active records for a project. At team
× months scale it becomes a wall; unranked memory stops being read, and unread
memory is dead memory. (Bitemporality already keeps *facts* current; the
growth pressure is sessions, decisions, and fact count per topic.)

**Design.**

- **Focused briefs:** `brief --focus "<what I'm about to do>"` ranks records
  by lexical relevance (BM25-style scoring over subject/relation/value/title/
  completed/next-step fields — stdlib, no embeddings) combined with recency
  decay, type weights (active decisions and disputes always surface), and the
  v2.1 topic/repo facets.
- **Brief budgets with honest truncation:** default caps per section (e.g., 10
  facts, 5 sessions); anything omitted is counted — "12 more facts omitted;
  run `query` for the full set" — never silently dropped.
- **Ranking, never deletion:** append-only stays absolute. "Decay" means old
  records rank lower; nothing is archived out of the vault, and `query` always
  reaches everything.
- **Cross-topic entity queries:** `query --entity "[[Auth service]]"`
  searches every project and vault for records referencing an entity
  wiki-link, grouped by topic — "everything we know about the auth service"
  regardless of where it was recorded.
- **Derived local index (v1's promise, finally needed):** a rebuildable index
  in the config dir (never in the vault) keyed by file mtime, so focused
  briefs stay fast on large vaults. Canonical data remains the Markdown; the
  index can be deleted at any time. Embeddings remain out of scope until
  lexical ranking demonstrably fails.

**Success criteria.** On a vault with thousands of records, a focused brief
returns in under a second, fits in a screen, always includes active disputes
and the topic's active decisions, states exactly what it omitted — and a
cold-start entity query answers "what do we know about X" across topics.

## V3C — Ambient capture: a review queue instead of discipline

**Problem.** Even with hooks, coverage depends on someone saying yes at the
right moment, and interruptions cost flow. The vault should never depend on
memory about memory.

**Design.**

- **Draft queue, outside the vault:** the SessionEnd hook graduates from
  leaving a marker to producing a *draft* — a headless summarization pass over
  the transcript writes a proposed session recap (and any candidate facts or
  decisions it noticed) into `~/.config/context-vault/pending/`. Drafts are
  plain JSON proposals: never synced, never in the vault, auto-expired after
  30 days.
- **Batch review:** a `review` command lists pending drafts; the skill/MCP
  instructions have the agent present them in one batch ("3 drafts from
  yesterday — approve, edit, or drop each"). Approve → normal `record-*
  --confirm` with full stamping; drop → delete. The consent gate is intact —
  the *drafting* is ambient, the *writing* never is.
- **Cheap and skippable:** drafting only runs for sessions above a substance
  threshold (same heuristic as the Tier-2 marker), and a config switch turns
  ambient drafting off entirely, falling back to markers.
- Auto-record (`auto_record: ["session"]`) remains a per-user opt-in for
  recaps only; facts and decisions can never be auto-recorded, by design.

**Success criteria.** After a week of normal work with zero mid-session
interruptions, the morning review shows a complete, correct queue of what
happened; approving it takes under a minute; nothing entered any vault without
that approval.

---

## Delivery sequence

1. **V3A (MCP)** first: self-contained, no data-volume prerequisite, and it is
   distribution — it changes what Context Vault *is* (a memory layer any agent
   plugs into, not a plugin for two CLIs).
2. **V3B (retrieval)** second, once dogfooding has produced enough volume for
   ranking to be testable against real briefs.
3. **V3C (ambient)** last, after the Tier-1/2 hook experience shows where the
   friction actually is.

Each pillar gets its own spec-level review and implementation plan before
work starts; this document is direction, not an implementation contract.

## Out of scope for v3 (bench)

- Code-host adapters (live PR merge state, webhook bot recording merges).
- Verified identity (signed commits mapped to team identities).
- Consumption surfaces: Slack digests, a native Obsidian plugin UI, web
  viewer.
- Lifting the two-team-vault cap; routing registry; hosted service/API.
- Embedding-based retrieval (revisit only if lexical ranking fails).

## Risks

- **MCP without the skill's guardrails:** hosts vary in how firmly they
  enforce tool-approval; server instructions and destructive annotations
  mitigate, but a permissive host could weaken the gate. Mitigation: record
  tools hard-require `confirm: true` and echo what will be written; nothing
  defaults to writing.
- **Ranking opacity:** a focused brief that hides the wrong record is worse
  than a long one. Mitigations: always-surface classes (disputes, active
  decisions), honest omission counts, `query` as the full-fidelity escape
  hatch.
- **Ambient drafting cost/quality:** headless summarization spends tokens and
  can misread a session. Mitigations: substance threshold, drafts-not-writes,
  batch human review, easy off switch.
