# Context Vault

Context Vault is a local Codex plugin for keeping durable, reviewable project context in an Obsidian vault. It helps Codex resume work with the current goal, decisions, facts, blockers, and source notes instead of depending on raw chat history.

## What it stores

The configured vault contains standard Markdown under `codex-context/`:

```text
codex-context/
  projects/   # workspace mapping, goal, open questions
  decisions/  # choice, alternatives, rationale, evidence
  facts/      # append-only time-aware state changes
  sessions/   # completed work, blockers, next step
  templates/
```

Obsidian can browse the notes, links, backlinks, and graph normally. Git can version the vault. Context Vault does not create a separate authoritative database.

## Install and configure

This plugin is published in the local personal Codex marketplace as `context-vault`.

```bash
codex plugin add context-vault@personal
python3 ~/plugins/context-vault/scripts/context_vault.py configure \
  --vault "/absolute/path/to/Obsidian Vault"
```

Configuration is stored at `~/.codex/context-vault/config.json`. After configuration, commands automatically use that vault. Pass `--vault /another/path` to override it for a single command.

Register a project before retrieving a brief:

```bash
python3 ~/plugins/context-vault/scripts/context_vault.py project \
  --name "Billing" --workspace "$PWD" --goal "Finish the migration" \
  --open-question "Confirm rollout" --confirm
```

## Everyday flow

1. Start Codex work with `brief --workspace "$PWD"`.
2. Work normally.
3. Use a `propose-*` command for a meaningful fact, decision, or session recap.
4. Review the proposal with the user.
5. Use `record-* --confirm` only after explicit approval.

The plugin never writes a note merely because a conversation happened.

## Facts and time

Facts are append-only. A new fact can supersede an old fact, preserving the history and its evidence.

```bash
python3 ~/plugins/context-vault/scripts/context_vault.py record-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 \
  --evidence 'PR #421' --confirm
```

- `valid_from`: when the fact became true in the world.
- `valid_to`: optional exclusive end of validity.
- `recorded_at`: automatically captured time when Context Vault learned the fact.
- `supersedes`: filename stem of the prior fact, when it replaces that state.

This supports both “what was true then?” and “what did the agent know then?” queries.

## Context commands

```bash
# Start/resume work
python3 ~/plugins/context-vault/scripts/context_vault.py brief --workspace "$PWD"

# Propose a fact without writing anything
python3 ~/plugins/context-vault/scripts/context_vault.py propose-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421'

# Persist the reviewed fact
python3 ~/plugins/context-vault/scripts/context_vault.py record-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421' --confirm

# Query history or decision provenance
python3 ~/plugins/context-vault/scripts/context_vault.py query \
  --workspace "$PWD" --mode historical --valid-at 2026-04-01

python3 ~/plugins/context-vault/scripts/context_vault.py query \
  --workspace "$PWD" --mode provenance --decision 'Use Postgres'
```

Run `python3 ~/plugins/context-vault/scripts/context_vault.py --help` for the complete command list.

## Safety and privacy

- Proposals never write to the vault.
- Every `record-*` command requires `--confirm`.
- Facts, decisions, and session recaps require evidence.
- Secret-like OpenAI keys, AWS access keys, and private-key headers are rejected before persistence.
- The plugin does not save raw Codex transcripts by default.
- An ambiguous workspace-to-project mapping fails rather than guessing.

## Tests and validation

```bash
cd ~/plugins/context-vault
python3 -m unittest discover -s tests -v
python /Users/manurathansetty/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

The validator may need `python` rather than `python3` on machines where the latter lacks PyYAML.

After installing or updating the plugin, start a new Codex task so the new skill is loaded.
