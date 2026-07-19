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

## Team vaults (v0.2)

Context Vault can share project memory across a dev team while your personal
vault stays private. A **team vault** is a private git repository with the same
Markdown layout; the project is the unit of sharing, and at most two team
vaults may be configured.

```bash
# one-time, per teammate
python3 scripts/context_vault.py configure --vault ~/Documents/context-vault --identity yourname
python3 scripts/context_vault.py init-team --repo git@github.com:<your-org>/team-context-vault.git
```

`init-team` clones the vault, registers a git merge driver, writes
`.gitattributes`/`.gitignore`, vendors a CI validator
(`scripts/validate_vault.py` plus a GitHub Actions workflow), creates your
`people/@you.md` note, and adds the vault to config.

How it behaves:

- **Memory travels faster than code.** Session recaps, facts, and decisions
  push to the vault's `main` the moment they are approved — a teammate's brief
  shows your schema work while the code is still an unmerged PR (sessions carry
  `--branch`/`--pr` as recorded metadata; merge state is never claimed).
- **Reads never rebase.** `brief`/`query` fetch and fast-forward only; every
  git operation holds a per-vault lock, so concurrent agents serialize.
- **Attribution is client-asserted.** Records are stamped `author`/`agent`
  automatically from your configured identity — provenance, not proof.
- **Conflicts are visible, never silent.** Notes auto-merge with a
  `merge_status` mark and a repair chore; diverged records are preserved
  byte-for-byte (one in place, one quarantined under
  `codex-context/conflicts/`); contradictory facts surface as disputes in the
  brief. Records are append-only by protocol, and CI flags any edit of an
  existing record.
- `doctor` checks identity, driver, remote, lock, and pending state;
  `sync` and `vault list` round out the tooling.

See the revised design in
[docs/superpowers/specs/2026-07-19-cross-team-vault-design.md](docs/superpowers/specs/2026-07-19-cross-team-vault-design.md).

### Topics across repos (v0.3)

Projects are topics: register one project per initiative with every code repo
it spans (`--workspace-repo`, repeatable). Records are stamped with the repo
they came from (`--workspace "$PWD"`), sessions can span several repos,
briefs group activity `by_repo`, and finished topics retire with
`--status done`. Session-start/-end hooks (Claude Code) inject the brief
automatically and queue capture reminders — approval stays human.

### Auto mode (v0.4, experimental, default off)

`auto enable [--vault-name X]` turns on standing consent per vault: agents
record at milestone moments (schema landed, decision made, code committed,
pre-compaction, wrap-up) without per-record approval — every such record is
stamped `consent: auto` with its trigger and session, checkpoints supersede
into one visible record per session, and a local ledger makes triggers
idempotent. Corrections: `withdraw` (append-only tombstone, temporally
honest) or `retract --remove-from-current-tree` (10-minute receipt-gated
safe revert). `auto status` shows modes, pending syncs, and skipped
duplicates. Manual mode is unchanged and remains the default.

## Design and implementation

The approved design and the implementation plan ship with the plugin:

- [Design](docs/superpowers/specs/2026-07-18-codex-obsidian-context-vault-design.md)
- [Implementation plan](docs/superpowers/plans/2026-07-18-context-vault-plugin.md)
- [Codex project map](codex.md)
- [Development transcript](.dev-transcript/2026-07-18-context-vault-build.md)

## Use with Codex

Register the public GitHub repository as a Codex marketplace, then install the plugin:

```bash
codex plugin marketplace add manurathansetty/context-vault
codex plugin add context-vault@context-vault
```

After opening a new Codex task, ask: `Configure Context Vault with my Obsidian vault at
/absolute/path/to/Obsidian Vault`.

## Use with Claude Code

The same repository is also a Claude Code plugin and marketplace (via
`.claude-plugin/`). Add the marketplace from GitHub, then install the plugin:

```bash
claude plugin marketplace add manurathansetty/context-vault
claude plugin install context-vault@context-vault
```

The equivalent in-session slash commands are:

```text
/plugin marketplace add manurathansetty/context-vault
/plugin install context-vault@context-vault
```

The skill loads on the next session (or after `/reload-plugins`) as
`context-vault`. Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}` with the
plugin's install directory, so the CLI resolves without any hardcoded path.
Then ask Claude to `Configure Context Vault with my Obsidian vault at
/absolute/path/to/Obsidian Vault`.

## Direct script setup

Clone the repository when you want to use the command-line interface directly:

```bash
git clone https://github.com/manurathansetty/context-vault.git
cd context-vault
export CONTEXT_VAULT="$PWD"
python3 scripts/context_vault.py configure \
  --vault "/absolute/path/to/Obsidian Vault"
```

For local development, the plugin can also be installed from the personal marketplace as
`context-vault@personal`.

Configuration is stored at `${XDG_CONFIG_HOME:-~/.config}/context-vault/config.json`
(a legacy `~/.codex/context-vault/config.json` is still read as a fallback for
older installs). After configuration, commands automatically use that vault. Pass
`--vault /another/path` to override it for a single command.

Register a project before retrieving a brief:

```bash
python3 "$CONTEXT_VAULT/scripts/context_vault.py" project \
  --name "Billing" --workspace "$PWD" --goal "Finish the migration" \
  --open-question "Confirm rollout" --confirm
```

A workspace matches a project registered for that exact path or for any ancestor
directory; when several projects match, the most specific registered path wins,
and an exact tie still fails as ambiguous.

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
python3 "$CONTEXT_VAULT/scripts/context_vault.py" record-fact \
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
python3 "$CONTEXT_VAULT/scripts/context_vault.py" brief --workspace "$PWD"

# Propose a fact without writing anything
python3 "$CONTEXT_VAULT/scripts/context_vault.py" propose-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421'

# Persist the reviewed fact
python3 "$CONTEXT_VAULT/scripts/context_vault.py" record-fact \
  --project billing --subject '[[Auth service]]' --relation owner \
  --value '[[Platform team]]' --valid-from 2026-07-18 --evidence 'PR #421' --confirm

# Query history or decision provenance
python3 "$CONTEXT_VAULT/scripts/context_vault.py" query \
  --workspace "$PWD" --mode historical --valid-at 2026-04-01

python3 "$CONTEXT_VAULT/scripts/context_vault.py" query \
  --workspace "$PWD" --mode provenance --decision 'Use Postgres'
```

Run `python3 "$CONTEXT_VAULT/scripts/context_vault.py" --help` for the complete command list.

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
