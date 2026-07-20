# Roadmap — Retrieval & Ambient Capture (post-v0.4)

## Status

Direction approved; not yet implemented. Hardened across three external
review rounds, whose resolutions are folded in below — the honest approval
boundary, the summarization execution contract, the core scope policy, the
pending-store contract, and revision-safe indexing. Prerequisites (topics,
repo facets, capture hooks) shipped in
[v0.3](v0.3-topics-and-hooks.md).

## Core policy layer and the approval boundary

Capture hooks and any future non-native client are **untrusted input
devices**. The core enforces what a local CLI *can* enforce: vault scope;
payload integrity between approval and write for queued drafts;
client-asserted provenance labeling; routing/topic revalidation at write
time; and v2's append-only/no-loss behavior. Every integration (CLI, skills,
hooks) is a thin adapter over this one core.

**Honesty about approval (review rounds 2–3):** on a single-user machine, an
agent with the user's shell has the user's authority — **no mechanism
Context Vault builds locally can be a hard boundary against it**, tokens and
prompts included (a shell-capable agent can run `approve <id>` or allocate a
pseudo-TTY just as it can pass `--confirm`). The threat model is therefore
explicit: local mechanisms defend against the *realistic* adversary — a
confused or overeager agent — while *enforcement* against a genuinely
misbehaving agent belongs to the host layer, which Context Vault composes
with rather than pretends to replace. Concretely:

- The **approval token** (V3C) is an *integrity and audit* mechanism: it
  binds an approval to the exact payload bytes, expires quickly, and is
  single-use — killing content drift, replay, and stale approvals. It is a
  user/host protocol, not proof of human hands.
- The **trusted approval surfaces** are host-native and outside the agent's
  command channel: the host's own command-permission prompt (the user's
  click in Claude Code/Codex), and host sandbox/permission deny rules —
  e.g., denying the agent's shell access to the personal-vault path and the
  pending store. Docs ship the recommended deny-rule configuration. (A
  separate biometric-gated approval app is the future option if true local
  enforcement is ever required.)
- No document or output may describe `--confirm`, tokens, or scope flags as
  core-enforced against an agent.

**Scope policy is core, not an MCP concern (review rounds 2–3):** reads
default to the vault the workspace routes to; cross-vault queries require an
explicit `--all-vaults` opt-in, which works **only for vaults granted in the
local per-vault allowlist** (restored from the deferred MCP design; personal
vaults are never granted by default). Personal and team context are never
silently blended. This applies to the Claude Code and Codex adapters today.
The allowlist is itself a local file — a mistake-catcher against agent
confusion, not a wall against agent intent; the wall, where needed, is a
host deny rule on the vault path. Write, sync, and vault-enumeration scopes
are separate from read scope.

**Minimum shared core** — routing, scope enforcement, and these approval
semantics — is a prerequisite for both pillars. V3B is the first feature
delivery and does not wait for V3C's token machinery.

## Framing

- **v1** made memory *durable* (personal vault, bitemporal records, consent
  gate).
- **v2** made memory *shared* (team vaults, locked git sync, attribution,
  disputes, no-loss conflicts).
- **v3** makes memory *self-sustaining*: useful at volume (retrieval) and
  populated without depending on anyone's discipline (ambient capture) — all
  without weakening the two load-bearing invariants: **the vault is
  human-readable Markdown** and **nothing enters it without human approval**.
  (An MCP server was designed as a third pillar and is **deferred by
  decision**, 2026-07-19: the team runs entirely on Claude Code and Codex,
  whose adapters already exist. The hardened design below is kept for the day
  a different host enters the toolchain.)

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

## Deferred — MCP server: memory for any agent (NOT in v3 scope)

**Deferred 2026-07-19 (maintainer decision):** the team uses only Claude Code and Codex;
the existing adapters are sufficient. Revisit when a non-Claude/Codex agent
actually needs vault access. The design below (including the review-hardened
approval-token and vault-scope model) is preserved so that revisit starts
warm, not cold. No MCP code is built until then.

**Problem it would solve.** v2 reaches agents through hand-written adapters
(Codex skill, Claude Code plugin). Other tools — Cursor, Windsurf, custom
Pydantic AI agents — are locked out. Per-harness adapters do not scale; MCP
is the standard everyone speaks.

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
- **Approval is enforced by the core, not asserted by the client** (review
  P0-1). A `confirm: true` parameter proves nothing — a model can supply it.
  Instead: `vault_propose_*` stores the proposal locally and returns a
  `proposal_id` plus canonical payload hash; a human approves that exact
  proposal through a **local interactive step** (CLI review flow); approval
  mints a short-lived token bound to (proposal id, payload hash, vault,
  project); `vault_record_*` accepts only that token and revalidates routing,
  topic status, and identity at write time. Because no current host produces
  a trustworthy approval receipt, **the MCP server ships read-only by
  default**; write tools activate only with the token flow, and host-side
  annotations remain hints, never the boundary.
- **Vault-scope policy** (review P0-2): reads default to the vault the
  workspace routes to; the personal vault and any other team vault are
  invisible to an MCP client unless explicitly scoped in local config.
  Cross-vault entity queries require an explicit all-vault scope and never
  silently blend personal and team context. Write, sync, and vault-listing
  scopes are separate from read scope. Requested scopes are logged in runtime
  output only — never into Markdown records.
- Identity/agent stamping: same config file; the `agent` label from the MCP
  `initialize` handshake is normalized, length-bounded, and persisted as
  **client-asserted** provenance — absent/malformed values become `unknown`,
  and nothing presents it as verified identity (review P1-2).
- Compatibility is a defined subset, not an aspiration (review P1-1): the
  spec for v3A lists the exact MCP capabilities implemented (initialize/
  version negotiation, tool listing/schemas, call results, errors,
  cancellation), the server core is isolated from CLI rendering, and
  conformance tests run against Codex before any unknown-host claim.
- Registration is one command per host (`claude mcp add`, Cursor/Windsurf
  config JSON); the existing skills remain the richer integration for Codex
  and Claude Code.

**Success criteria.** An agent in a host we never wrote an adapter for gets a
correct, properly scoped brief with no Context Vault code beyond the MCP
entry; a write is impossible without a human completing the local approval
step for that exact payload; an adversarial client (fabricated confirm flags,
replayed or mismatched tokens, out-of-scope vault requests) is refused in
tests.

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
  regardless of where it was recorded. Link matching normalizes target and
  `[[target|alias]]` forms while preserving the displayed link, and reports
  unresolved or ambiguous entities explicitly instead of silently missing
  them (review P2-1). Entity queries obey the core scope policy: default to
  the active routed vault; `--all-vaults` is an explicit opt-in — for the
  Claude Code and Codex adapters as much as any future transport.
- **Deterministic ranking contract** (review P1-3): the implementation plan
  specifies tokenization, Unicode and wiki-link normalization, fields
  searched, field/type weights, the recency curve, tie-breakers, and budget
  selection order — verified against golden fixture vaults so ranking changes
  are always intentional. Non-droppable safety classes: active disputes,
  active decisions, repair chores, and sync/staleness warnings.
- **Revision-safe derived index** (review P0-4; v1's disposable-index
  promise, finally needed): a rebuildable index in the config dir (never in
  the vault) whose correctness key is the vault's current `HEAD` plus
  per-file content hashes — mtimes are not trustworthy across git checkouts
  and fast-forwards. `mtime_ns`+size serve only as a fast invalidation hint
  before hashing. Rebuilds are atomic and hold the existing per-vault lock;
  when revision identity cannot be established, the brief falls back to a
  direct scan and says so. The index directory is itself sensitive local data
  — it holds full record text — so it gets the pending-store treatment:
  created `0700`, covered by the same purge semantics, and named in the
  backup/retention note (follow-up review P1). Canonical data remains the
  Markdown; the index can be deleted at any time. Embeddings stay out of
  scope until lexical ranking demonstrably fails.

**Success criteria.** Stated at the right boundary (review P1-4): local
focused-brief queries hit a p95 under one second on a stated fixture corpus
and hardware envelope, measured after sync and separately from cold-index
build time; the brief fits in a screen, always includes the safety classes,
states exactly what it omitted; a stale index can never surface a retired
decision or hide an active dispute (revision-key tests cover checkout,
fast-forward, and concurrent-process races).

## V3C — Ambient capture: a review queue instead of discipline

**Problem.** Even with hooks, coverage depends on someone saying yes at the
right moment, and interruptions cost flow. The vault should never depend on
memory about memory.

**Design.**

- **Draft queue, outside the vault:** the SessionEnd hook graduates from
  leaving a marker to producing a *draft* — a headless summarization pass over
  the transcript writes a proposed session recap (and any candidate facts or
  decisions it noticed) into `~/.config/context-vault/pending/`. Drafts are
  plain JSON proposals: never synced, never in the vault.
- **Summarization execution contract** (follow-up review P0): drafting runs
  only on the **same provider that produced the session** — a Claude Code
  transcript is summarized headlessly by Claude, a Codex transcript by Codex;
  transcripts are never sent to a third-party model. The session already
  passed through that provider live, so drafting adds no new exposure
  surface. Known-pattern redaction plus the local deny-list run **before the
  model call** as defense-in-depth, not merely before writing the JSON
  draft. Cost rides the user's existing subscription and is bounded by the
  substance threshold; a host that cannot provide a transcript degrades to
  the marker fallback.
- **The pending store is a sensitive datastore with a contract** (review
  P0-3): directory created `0700` with exclusive file creation; secret
  redaction runs **before** anything is written (the vault's sensitive
  patterns, broadened, plus a local deny-list); every draft carries source
  session ID, host, workspace, creation timestamp, payload hash, and a draft
  schema version; repeated SessionEnd events deduplicate on session ID;
  `review purge` deletes everything on demand and expiry cleanup runs at
  CLI-invocation time (hooks alone can't be trusted to fire after crashes);
  the docs state plainly that pending drafts are unencrypted local files the
  user's backup tooling may capture.
- **Batch review:** a `review` command lists pending drafts; the skill
  instructions have the agent present them in one batch ("3 drafts from
  yesterday — approve, edit, or drop each"). **Approving a queued draft is a
  user/host protocol hardened by tokens** (review round 3): a user-typed
  `approve <draft-id>` mints a short-lived, single-use token bound to the
  draft's payload hash, and the queued-draft record path accepts only that
  token — guaranteeing the bytes written are the bytes approved, and making
  each approval a discrete auditable act. This deters and audits agent
  confusion; it is not proof of human hands (see the core policy section for
  the trusted surfaces that are). Recording
  then proceeds with full stamping, after **revalidating** routing, topic
  status, identity, and team configuration at approval time — a draft from
  Tuesday must not silently write into a project that was retired Wednesday.
  Drop → delete. The consent gate is intact — the *drafting* is ambient, the
  *writing* never is.
- **Best-effort by design** (review P1-5): session-end hooks are skipped by
  crashes, sleep, and unsupported hosts, so coverage is never claimed to be
  complete. Source-session IDs make capture idempotent, the next-session
  marker remains as fallback, and the review surface reports known gaps
  honestly rather than implying totality.
- **Auto-record is standing consent, narrowly scoped** (review P1-6): the
  `auto_record: ["session"]` opt-in is recorded as an explicit standing
  approval — records it produces are stamped with the consent policy and its
  origin, revocation is one config edit, **team vaults are excluded from
  automatic writes in v3** (personal vault only), and facts/decisions remain
  per-item human approvals permanently.
  **[Amended by v0.4]** The Auto Mode design
  ([v0.4-auto-mode.md](v0.4-auto-mode.md), supersession matrix) explicitly
  supersedes this clause's scoping for opted-in users: all record types, team
  vaults included, per-member `auto enable`, `consent: auto` stamped.
  Manual-mode users remain governed by this clause.
- **Cheap and skippable:** drafting only runs for sessions above a substance
  threshold (same heuristic as the Tier-2 marker), and a config switch turns
  ambient drafting off entirely, falling back to markers.

**Success criteria.** After a week of normal work with zero mid-session
interruptions, the morning review shows a correct queue of what was captured
— with any coverage gaps stated, not hidden; approving it takes under a
minute; nothing entered any vault without that approval or a stamped standing
consent; no **known** secret pattern survives redaction in fixture tests —
pattern scanning cannot promise detection of unknown secret shapes, and no
claim beyond the fixtures is made.

---

## Delivery sequence (revised: two pillars, review-ordered)

0. **Minimum shared core** (prerequisite, not a pillar): routing, scope
   enforcement, and the approval semantics above — small, tested, and shared
   by both pillars. V3B does not wait for V3C's token machinery.
1. **V3B (retrieval)** first, once dogfooding has produced enough volume for
   ranking to be testable against real briefs: revision-safe index and the
   deterministic ranking contract with golden fixtures, then focused briefs
   and entity queries.
2. **V3C (ambient)** second, after the Tier-1/2 hook experience shows where
   the friction actually is: the pending-store contract first, then
   best-effort drafting and batch review.

The MCP pillar re-enters this sequence only if a non-Claude/Codex host joins
the toolchain — and then read-only first, with writes gated on the core
approval-token flow per the review.

Each pillar gets its own spec-level review and implementation plan before
work starts; this document is direction, not an implementation contract.

## Out of scope for v3 (bench)

- **MCP server** (deferred by decision — design preserved above).
- Code-host adapters (live PR merge state, webhook bot recording merges).
- Verified identity (signed commits mapped to team identities).
- Consumption surfaces: Slack digests, a native Obsidian plugin UI, web
  viewer.
- Lifting the two-team-vault cap; routing registry; hosted service/API.
- Embedding-based retrieval (revisit only if lexical ranking fails).

## Risks

- **Approval is protocol, not enforcement, on interactive adapters:** a
  shell-capable agent could run `record-* --confirm` without asking.
  Mitigations: the skill's write protocol, host command-permission prompts,
  honest documentation of the boundary, and the human-hands token flow
  arriving with V3C for everything queued or automated. (MCP-specific risks
  live with the deferred design section.)
- **Ranking opacity:** a focused brief that hides the wrong record is worse
  than a long one. Mitigations: always-surface classes (disputes, active
  decisions), honest omission counts, `query` as the full-fidelity escape
  hatch.
- **Ambient drafting cost/quality:** headless summarization spends tokens and
  can misread a session. Mitigations: substance threshold, drafts-not-writes,
  batch human review, easy off switch.
