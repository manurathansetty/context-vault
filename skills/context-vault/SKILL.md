---
name: context-vault
description: Resume, query, and maintain durable project context in a local Obsidian vault. Use when a user asks to continue a project, preserve a decision, record a meaningful state change, or explain why a project choice was made.
---

# Context Vault

Context Vault is a durable, user-controlled project-memory layer for Codex. Its canonical data is standard Markdown beneath an Obsidian vault; the plugin never treats chat history as durable memory.

## Setup check

Before using a context command, check whether `~/.codex/context-vault/config.json` exists. If it does not, ask the user for the path to their Obsidian vault and have them run:

```bash
python3 ~/plugins/context-vault/scripts/context_vault.py configure --vault "/absolute/path/to/Obsidian Vault"
```

Do not search for, choose, or create an Obsidian vault on the user's behalf. After setup, commands use the configured vault automatically; `--vault /path` is available to override it for one command.

## Task start: retrieve only relevant context

When a task is tied to a configured project, run this before doing substantive work:

```bash
python3 ~/plugins/context-vault/scripts/context_vault.py brief --workspace "$PWD"
```

Present a short brief containing the goal, open questions, current facts, active decisions, recent sessions, and the source note paths. Treat the brief as evidence-backed context, not as unquestionable truth. State uncertainty when no relevant note exists.

If the workspace does not map to exactly one project, report the CLI error and ask the user to choose or register a project. Do not guess a project or create one silently.

## Write protocol: proposal before persistence

Never write raw transcript text. Never store credentials, private keys, or unsupported claims.

For every durable update:

1. Build a proposal with one of the `propose-*` commands.
2. Show the concise proposal and evidence to the user.
3. Persist only after the user explicitly approves the exact proposal.
4. Use the corresponding `record-* --confirm` command.

Examples:

```bash
# Changing fact; `--supersedes` is the earlier fact filename without .md.
python3 ~/plugins/context-vault/scripts/context_vault.py propose-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421'

python3 ~/plugins/context-vault/scripts/context_vault.py record-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421' --confirm

python3 ~/plugins/context-vault/scripts/context_vault.py propose-decision \
  --project billing --title 'Use Postgres' --choice Postgres \
  --alternative DynamoDB --rationale 'Need relational transactions.' --evidence 'ADR-001'

python3 ~/plugins/context-vault/scripts/context_vault.py propose-session \
  --project billing --completed 'Added migration' --blocker 'Awaiting review' \
  --next-step 'Open pull request' --evidence 'Codex task summary'
```

For a replacement fact, create a new fact with `--supersedes`. Do not edit or delete the historical note. `valid_from` is when the fact became true; `recorded_at` is when Context Vault learned it. `valid_to`, when supplied, is an exclusive end date.

## Queries

Use a context query for questions about current state, historical reality, what was known at a time, or a decision's rationale:

```bash
# Current project context
python3 ~/plugins/context-vault/scripts/context_vault.py query \
  --workspace "$PWD" --mode current

# What was true on a date; optional --known-at limits facts to what was recorded then.
python3 ~/plugins/context-vault/scripts/context_vault.py query \
  --workspace "$PWD" --mode historical --valid-at 2026-04-01 \
  --known-at 2026-04-01T18:00:00+00:00

# Decision evidence and subsequent project context
python3 ~/plugins/context-vault/scripts/context_vault.py query \
  --workspace "$PWD" --mode provenance --decision 'Use Postgres'
```

Always cite the returned source paths when explaining a state or decision. Do not claim that a historical query means the system knew something at that date unless a `--known-at` filter was applied.
