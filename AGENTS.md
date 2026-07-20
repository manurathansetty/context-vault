# AGENTS.md — working in this repository

Instructions for coding agents (Claude Code, Codex, or anything else) tracing
this repo for context or making changes.

## What this is

Context Vault: durable, consent-first memory for coding agents. One
standard-library-only Python CLI backed by plain Markdown in an Obsidian
vault, with git-synced team vaults. Read [README.md](README.md) first — it is
the product truth.

## Getting context, in order

1. **README.md** — what the product does and every user-facing command.
2. **`docs/superpowers/specs/`** — the design contracts, spec-first and
   review-hardened. Read the one for the area you're touching:
   - `2026-07-18-codex-obsidian-context-vault-design.md` — v0.1 core: vault
     layout, bitemporal records, the propose→approve consent gate.
   - `2026-07-19-cross-team-vault-design.md` — v0.2 team vaults: locked git
     sync, merge driver, quarantine, attribution, disputes.
   - `2026-07-19-topic-layer-v2.1-design.md` — topics spanning repos, repo
     facets, routing.
   - `2026-07-19-capture-hooks-design.md` — the hook tiers and marker store.
   - `2026-07-19-context-vault-v3-design.md` — future direction (retrieval,
     ambient capture) and the honest threat model for approval/consent.
   - `2026-07-19-auto-mode-v0.4-design.md` — auto mode: standing consent,
     triggers, ledger, withdraw/retract, plus the full decision log.
3. **Tests** (`tests/`) — executable spec. `test_context_vault.py` covers
   v0.1 behavior; `test_team_vault.py` team/topic behavior with real
   two-clone git fixtures; `test_hooks.py` the hook scripts;
   `test_auto_mode.py` auto mode end to end.

The spec's *status* sections may reference implementation plans and review
documents — those are intentionally **local-only** (gitignored), not lost.

## Layout

| Path | What |
| --- | --- |
| `scripts/context_vault.py` | The entire CLI — config, routing, sync engine, merge driver, records, auto mode. Single file by design. |
| `scripts/validate_vault.py` | Standalone CI validator vendored into team vaults. Must never import `context_vault`. |
| `scripts/hooks/` | Claude Code hook scripts. Instruction-only: hooks never write records. |
| `skills/context-vault/SKILL.md` | The agent-facing protocol (both Claude Code and Codex load this). |
| `.claude-plugin/`, `.codex-plugin/` | Plugin manifests + marketplace. Version lives in **three** files — bump all or none. |
| `tests/` | `unittest`, no fixtures framework, real git repos in tempdirs. |

## Hard rules

- **Stdlib only.** No pip dependencies, ever — including transitive
  conveniences. `difflib` not diff-match-patch, `fcntl` not filelock.
- **Records are append-only and immutable.** Correction is `supersedes` or
  `withdraw` (tombstone). Never write code that edits or deletes a record
  file in place; `retract --remove-from-current-tree` is the only sanctioned
  tree removal and must stay receipt-gated.
- **Manual mode is sacred.** Any change must leave manual-mode behavior
  byte-identical — the pre-existing test suites must pass unmodified. New
  behavior goes behind modes/flags.
- **Honesty in output and docs.** Never describe client-asserted things
  (attribution, `--confirm`, consent stamps) as enforced or verified. This
  project's docs deliberately never claim more than the code delivers.
- **No personal names anywhere** — code, tests, docs, examples, commit
  messages. Use generic identities (`alex`, `blake`, `alice`) and the GitHub
  handle `manurathansetty` only. History was rewritten once to enforce this;
  don't make it need a second time.
- **Never commit internal process docs** — `docs/superpowers/plans/`,
  `docs/superpowers/specs/*review*.md`, `.dev-transcript/`, `codex.md` are
  gitignored on purpose. Design specs are the only published docs.
- Reads never rebase; writes hold the per-vault lock; hooks never compose
  record content. Keep these invariants when touching the sync engine.

## Workflow

```bash
python3 -m unittest discover -s tests -v     # full suite — must be green before any push
```

- TDD: add the failing test in the matching `tests/test_*.py` first.
- Feature work on a branch; `main` is the released plugin (the marketplace
  serves it), so `main` must always be installable and green.
- Release = bump the version in all three manifests + tests green + push
  `main`, then `claude plugin update context-vault@context-vault`.
- Commit style: `feat:` / `fix:` / `docs:` / `chore:`, imperative, no
  attribution trailers.

## Dogfood note

This repo is its own memory system. If the Context Vault plugin is installed,
`brief --workspace <this repo>` returns the project's recorded goals,
decisions, and session history — prefer that over reconstructing context from
git archaeology alone, and record meaningful sessions/decisions back through
the normal consent flow.
