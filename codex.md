# Context Vault — Codex Project Map

This is the repository entry point for contributors and Codex tasks.

## Start here

- [README](README.md) — installation, configuration, commands, and safety
  behavior.
- [Design](docs/superpowers/specs/2026-07-18-codex-obsidian-context-vault-design.md)
  — approved architecture and product scope.
- [Implementation plan](docs/superpowers/plans/2026-07-18-context-vault-plugin.md)
  — original task-by-task delivery plan.
- [Development transcript](.dev-transcript/2026-07-18-context-vault-build.md)
  — the decisions and milestones from the build session.

## Implementation

| Area | Location | Purpose |
| --- | --- | --- |
| Plugin manifest | [.codex-plugin/plugin.json](.codex-plugin/plugin.json) | Codex plugin metadata and version. |
| Marketplace | [.agents/plugins/marketplace.json](.agents/plugins/marketplace.json) | Public Git marketplace entry. |
| Codex workflow | [skills/context-vault/SKILL.md](skills/context-vault/SKILL.md) | Retrieval, consent, and safety instructions. |
| CLI | [scripts/context_vault.py](scripts/context_vault.py) | Markdown records, temporal queries, and guarded writes. |
| Tests | [tests/test_context_vault.py](tests/test_context_vault.py) | `unittest` coverage of the public behavior. |

## Expected product output

Context Vault does not create a separate opaque memory database. It produces:

1. a concise, source-linked startup brief for a project; and
2. durable, user-approved Markdown records in an Obsidian vault:
   `projects/`, `facts/`, `decisions/`, and `sessions/`.

Obsidian provides the human-facing link, backlink, and graph views; Git provides
version history for the source and, optionally, the vault.

## Verify locally

```bash
python3 -m unittest discover -s tests -v
python /Users/manurathansetty/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

## Install from GitHub

```bash
codex plugin marketplace add manurathansetty/context-vault
codex plugin add context-vault@context-vault
```

After installation, begin a new Codex task and ask it to resume a registered
project from Context Vault.

## Boundaries

- Obsidian Markdown is canonical; there is no hosted database in version 1.
- A native Obsidian plugin and automatic raw-transcript retention are out of
  scope.
- Every durable write is proposed first and requires explicit confirmation.
- The development demo vault is local and intentionally excluded from this
  public repository.
