---
name: context-vault
description: Resume, query, and maintain durable project context in a local Obsidian vault. Use when a user asks to continue a project, preserve a decision, record a meaningful state change, or explain why a project choice was made.
---

# Context Vault

Context Vault is a durable, user-controlled project-memory layer. Its canonical
data is standard Markdown beneath an Obsidian vault; the plugin never treats chat
history as durable memory.

## Locate the CLI

Every command below runs the Context Vault CLI (`context_vault.py`). Resolve its
path once at the start of a session, then reuse it — do not hardcode a
machine-specific path.

- In Claude Code, `${CLAUDE_PLUGIN_ROOT}` is substituted into this skill with the
  plugin's install directory, so set:

  ```bash
  CONTEXT_VAULT="${CLAUDE_PLUGIN_ROOT}/scripts/context_vault.py"
  ```

- In Codex there is no plugin-root variable. Point `CONTEXT_VAULT` at the
  `scripts/context_vault.py` bundled with this plugin — the local clone or the
  installed plugin directory — for example:

  ```bash
  CONTEXT_VAULT="$HOME/plugins/context-vault/scripts/context_vault.py"
  ```

Every command in this skill then runs as `python3 "$CONTEXT_VAULT" ...`.

## Setup check

Before using a context command, check whether the config file exists at
`${XDG_CONFIG_HOME:-$HOME/.config}/context-vault/config.json` (the tool also still
reads a legacy `~/.codex/context-vault/config.json` for older installs). If no
config exists, ask the user for the path to their Obsidian vault and have them
run:

```bash
python3 "$CONTEXT_VAULT" configure --vault "/absolute/path/to/Obsidian Vault"
```

Do not search for, choose, or create an Obsidian vault on the user's behalf.
After setup, commands use the configured vault automatically; `--vault /path` is
available to override it for one command.

## Task start: retrieve only relevant context

When a task is tied to a configured project, run this before doing substantive
work:

```bash
python3 "$CONTEXT_VAULT" brief --workspace "$PWD"
```

Present a short brief containing the goal, open questions, current facts, active
decisions, recent sessions, and the source note paths. Treat the brief as
evidence-backed context, not as unquestionable truth. State uncertainty when no
relevant note exists.

If the workspace does not map to exactly one project, report the CLI error and
ask the user to choose or register a project. Do not guess a project or create
one silently.

## Write protocol: proposal before persistence

Never write raw transcript text. Never store credentials, private keys, or
unsupported claims.

For every durable update:

1. Build a proposal with one of the `propose-*` commands.
2. Show the concise proposal and evidence to the user.
3. Persist only after the user explicitly approves the exact proposal.
4. Use the corresponding `record-* --confirm` command.

Examples:

```bash
# Changing fact; `--supersedes` is the earlier fact filename without .md.
python3 "$CONTEXT_VAULT" propose-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421'

python3 "$CONTEXT_VAULT" record-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421' --confirm

python3 "$CONTEXT_VAULT" propose-decision \
  --project billing --title 'Use Postgres' --choice Postgres \
  --alternative DynamoDB --rationale 'Need relational transactions.' --evidence 'ADR-001'

python3 "$CONTEXT_VAULT" propose-session \
  --project billing --completed 'Added migration' --blocker 'Awaiting review' \
  --next-step 'Open pull request' --evidence 'Codex task summary'
```

For a replacement fact, create a new fact with `--supersedes`. Do not edit or
delete the historical note. `valid_from` is when the fact became true;
`recorded_at` is when Context Vault learned it. `valid_to`, when supplied, is an
exclusive end date.

## Queries

Use a context query for questions about current state, historical reality, what
was known at a time, or a decision's rationale:

```bash
# Current project context
python3 "$CONTEXT_VAULT" query \
  --workspace "$PWD" --mode current

# What was true on a date; optional --known-at limits facts to what was recorded then.
python3 "$CONTEXT_VAULT" query \
  --workspace "$PWD" --mode historical --valid-at 2026-04-01 \
  --known-at 2026-04-01T18:00:00+00:00

# Decision evidence and subsequent project context
python3 "$CONTEXT_VAULT" query \
  --workspace "$PWD" --mode provenance --decision 'Use Postgres'
```

Always cite the returned source paths when explaining a state or decision. Do not
claim that a historical query means the system knew something at that date unless
a `--known-at` filter was applied.

## Team vaults

A team vault is a private git repository of shared project memory; the personal
vault stays private and unstamped. The project is the unit of sharing, and at
most two team vaults may be configured.

One-time setup per teammate (identity first, then join):

```bash
python3 "$CONTEXT_VAULT" configure --vault "/absolute/path/to/personal-vault" --identity yourname
python3 "$CONTEXT_VAULT" init-team --repo git@github.com:<your-org>/team-context-vault.git
```

To bootstrap a brand-new team vault, create an empty private repository on the
host first, then run `init-team` against it.

Behavior differences for projects that live in a team vault:

- Reads (`brief`, `query`) fetch and fast-forward the team vault first; writes
  commit and push to `main` immediately after `--confirm`. Never open a pull
  request against the vault repository.
- Records are stamped with the configured identity and agent automatically.
  Pass `--agent claude-code` (or set `CONTEXT_VAULT_AGENT`) when recording.
  Present stamps as claimed attribution — they record who the writing client
  said it was, not verified identity.
- Before asking the user to approve any proposal that will be persisted to a
  team vault, state plainly: "this will be pushed to the team vault and visible
  to your team." The secret patterns are a narrow net; the user's approval is
  the real gate.
- Record sessions with `--branch` and `--pr` whenever work is unmerged so
  teammates can link up to in-flight code. These are recorded metadata only —
  never claim to know whether a branch or PR has merged.
- The brief may include `disputes` (contradictory active facts for an exclusive
  relation): present every value with its author; never pick one silently.
  Resolution is a new superseding fact recorded with user approval. Use
  `--cardinality multi` for relations where several values are normal
  (contributors, tags).
- The brief may include `repair_chores`. As a small startup chore: for an
  `auto-merged` note, fix duplicated or garbled text, remove the
  `merge_status` frontmatter line, and commit `repair: clean up auto-merge`.
  For a `needs-human` note or a `quarantined` record in
  `codex-context/conflicts/`, show the user both versions and record their
  resolution as a superseding note — never edit or delete either original.
- The brief's `sync` map reports each team vault's state. If a vault is
  offline, stale, or has unpushed records, say so when presenting the brief.
- Run `python3 "$CONTEXT_VAULT" doctor` when sync misbehaves and report the
  failing checks; `vault list` shows the configured vaults.

## Topics and repos (v2.1)

Projects are **topics** (initiatives like "Fern importer"), and one topic may
span several code repositories via repeated `--workspace-repo` flags. Repos
are a facet on records, not a folder:

- When recording any fact, decision, or session, pass `--workspace "$PWD"` so
  the record is stamped with the repo it came from (`repos:` frontmatter plus
  a `Repos: [[name]]` graph link). Add explicit `--repo <url>` flags when the
  work touched other repos too.
- If a workspace maps to several topics, routing reports the candidates
  instead of guessing: ask the user which topic applies, then rerun with
  `--project <id>` (which also works without any `--workspace`).
- Briefs include `by_repo` — use it to present team activity grouped by
  repository ("in app: … · in text-agent: …").
- Retire a finished topic by re-registering it with `--status done`; it drops
  out of routing and refuses new records until revived with
  `--status active`.

## Capture hooks

When installed (Claude Code), a SessionStart hook injects the brief
automatically and may include **pending capture markers** from earlier
sessions. Treat each marker as a startup chore: read the transcript it points
to, build a `propose-session` recap, show it to the user, record only after
their explicit approval, then delete the marker file. Never record a marker's
recap without approval.

## Auto mode (v0.4, experimental, default off)

Auto mode is standing consent: for vaults where the user has run
`auto enable`, record WITHOUT pausing for approval — but always show what was
recorded. Check state with `auto status` (also injected at session start).
Manual-mode vaults keep the full propose → approve protocol.

Recording protocol in auto mode:

- Pass `--workspace "$PWD"` as usual. On your first record the CLI returns a
  `session_id` — reuse it (`--session-id`) on every later record this session
  so checkpoints chain and dedup works.
- Record at these moments (`--trigger`): an artifact milestone lands
  (`milestone` — schema generated, endpoint working, tests green), a decision
  settles (`decision`), a code commit you're nudged about (`git-commit` with
  `--source-commit <sha>`), before compaction (`precompact`), and when work
  concludes (`wrapup` — REQUIRED before the session ends; hooks only detect a
  missing wrap-up, they cannot write it for you).
- Chain checkpoints: each session record passes `--supersedes
  <previous-checkpoint-stem>` so briefs show one record per session.
- Facts/decisions: pass `--basis observed|inferred|user-stated` honestly
  (default is `inferred`, the lowest-trust label).
- `--confirm` is unnecessary in auto mode; never claim a human reviewed an
  auto record — `consent: auto` is visible to every reader by design.

Corrections (any mode):

- Wrong record → `withdraw --record <stem> --reason "..."`: append-only
  tombstone; hides it from current state, preserves temporal history.
- Just-written mistake (≤10 min, record-only commit) →
  `retract --record <stem> --remove-from-current-tree`: safe revert; history
  and pulled clones still retain it — say so if asked.
- Leaked credential → tell the user to ROTATE IT FIRST (vault presence =
  compromised), then withdraw; history cleanup is a coordinated team
  operation, not a command.
