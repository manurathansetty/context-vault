<div align="center">

# ◈ Context Vault

**Your agents forget everything. Your team forgets everything. This one refuses.**

Durable, user-controlled memory for coding agents — plain Markdown in your Obsidian
vault, git-backed team sharing, consent-first.

![Python](https://img.shields.io/badge/Python-stdlib_only-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-140_passing-2EA44F)
![Claude Code](https://img.shields.io/badge/Claude_Code-plugin-D97757)
![Codex](https://img.shields.io/badge/Codex-plugin-10A37F)
![Obsidian](https://img.shields.io/badge/Obsidian-native_Markdown-7C3AED?logo=obsidian&logoColor=white)
![Zero deps](https://img.shields.io/badge/dependencies-zero-040308)
![License](https://img.shields.io/badge/license-MIT-blue)

</div>

Context Vault gives Claude Code and Codex a project memory that survives sessions,
machines, and teammates. Agents start every session with an evidence-backed brief of
your project's goals, decisions, facts, and recent work — and record new memory back,
either with your per-record approval (manual mode) or under standing consent at
milestone moments (auto mode).

No database. No server. The canonical store is a folder of Markdown files that
Obsidian renders as a browsable knowledge graph, and git versions like any repo.

```text
your workspace ──brief──▶ agent knows the project ──work──▶ approved records
                                                              │
     Obsidian graph ◀── Markdown vault ◀──── git sync ◀───────┘
     (you browse it)    (canonical, yours)  (team shares it)
```

## 🧠 Why

Chat history is neither a reliable state store nor a human-readable record. Context
Vault makes memory:

- ⏳ **Durable** — append-only, time-aware records (`valid_from`, `recorded_at`,
  `supersedes`) answer *what is true now*, *what was true in April*, and *why we
  chose this* — including "what did we know at the time?" via `--known-at`.
- 🤝 **Shared** — a team vault is a private git repo; a teammate's session recap
  reaches your next brief minutes after their session ends, even while their code is
  still an unmerged PR. **Memory travels faster than code.**
- 🔑 **Yours** — everything is Markdown you can read, link, and graph in Obsidian.
  Nothing enters a vault without your approval or your explicit standing consent,
  and every record says who recorded it and how.

## 📦 Install

**Claude Code**

```bash
claude plugin marketplace add manurathansetty/context-vault
claude plugin install context-vault@context-vault
```

**Codex**

```bash
codex plugin marketplace add manurathansetty/context-vault
codex plugin add context-vault@context-vault
```

Start a new session after installing so the skill loads, then just ask your agent:
*"Configure Context Vault with my Obsidian vault at /path/to/vault"* — or drive the
CLI yourself. All commands below use the bundled script; set a shorthand once (or
clone this repo and use `CV="./scripts/context_vault.py"`):

```bash
CV="$HOME/.claude/plugins/cache/context-vault/context-vault/<version>/scripts/context_vault.py"
```

Configuration lives at `${XDG_CONFIG_HOME:-~/.config}/context-vault/config.json`.

## 🚀 Quick start

```bash
# 1. Point at your Obsidian vault (folders are created) and pick an identity
python3 "$CV" configure --vault ~/Documents/context-vault --identity yourname

# 2. Register a project (a topic — it may span several repos)
python3 "$CV" project --name "My App" \
  --workspace ~/code/my-app \
  --workspace-repo github.com/you/my-app \
  --goal "Ship the app" --confirm

# 3. Ask your agent to "Resume this project from my Context Vault"
#    (or run the brief yourself)
python3 "$CV" brief --workspace ~/code/my-app
```

From then on the loop is: **brief at task start → work → propose → you approve →
record**. Records are facts (time-aware claims), decisions (choice + alternatives +
rationale + evidence), and session recaps (completed / blockers / next step, with
`--branch`/`--pr` so teammates can pick up unmerged work).

```bash
python3 "$CV" propose-fact --project my-app --subject '[[Auth service]]' \
  --relation owner --value '[[Platform team]]' --valid-from 2026-07-20 \
  --evidence 'PR #421'                       # writes nothing — shows the proposal
python3 "$CV" record-fact  ... --confirm     # persists after your approval
```

Queries answer current state, historical state, and provenance:

```bash
python3 "$CV" query --workspace "$PWD" --mode current
python3 "$CV" query --workspace "$PWD" --mode historical --valid-at 2026-04-01
python3 "$CV" query --workspace "$PWD" --mode provenance --decision 'Use Postgres'
```

## 🗂️ What the vault looks like

```text
codex-context/
  projects/     # topic notes: goal, open questions, workspaces + repos, status
  facts/        # append-only, time-aware claims with evidence
  decisions/    # choice, alternatives, rationale, status
  sessions/     # recaps and checkpoints (branch/PR for unmerged work)
  people/       # @identity notes — every author is a graph node
  withdrawals/  # append-only correction tombstones
  conflicts/    # byte-for-byte quarantine of diverged records (rare)
  templates/
```

Open it in Obsidian and the graph shows topics as hubs, records as spokes, and
people and repos as cross-cutting nodes. Context Vault never maintains a separate
authoritative database — **the Markdown *is* the truth.**

## 👥 Team vaults

A team vault is a **private git repository** of shared memory. Your personal vault
stays private and separate — the *project* is the unit of sharing, never your whole
vault, so a teammate's personal graph never bleeds into yours. Access control is
just repo access.

**One-time, per teammate** (or just send the repo link — `init-team` scaffolds an
`ONBOARDING.md` into the vault that explains exactly this):

```bash
python3 "$CV" init-team --repo git@github.com:<your-org>/team-context-vault.git \
  --identity yourname
```

That clones the vault, registers the merge driver, writes
`.gitattributes`/`.gitignore`, vendors the CI validator and GitHub Actions
workflow, creates your `people/@yourname.md` note, and adds the vault to your
config. Bootstrap a brand-new team vault by creating an empty private repo first.

| | How it behaves |
|---|---|
| ⚡ | **Memory travels faster than code.** Records push to the vault's `main` the moment they're approved — a teammate's brief shows your schema work while your code sits in an unmerged PR. No PRs on the vault repo, ever. |
| 🔒 | **Reads never rebase.** Briefs fetch + fast-forward only; every git operation holds a per-vault lock so concurrent agents serialize. Offline you get local state with a staleness note; unpushed records deliver on the next sync. |
| ✍️ | **Attribution is client-asserted.** Records are stamped `author: [[@yourname]]` plus the agent that wrote them — provenance, not proof of identity. |
| ⚔️ | **Conflicts are visible, never silent.** Records are append-only new files (structurally conflict-free); mutable notes auto-merge with a visible `merge_status` mark; diverged records are preserved byte-for-byte with one copy quarantined; contradictory facts surface as **disputes** in every brief (`--cardinality multi` opts out relations where many values are normal). |
| 🛡️ | **CI backstop.** The vendored workflow validates schema and author stamps, flags stale marks, and reports any edit of a historical record as an append-only violation. |

## 🧵 Topics across repos

Projects are topics ("Payments revamp"), and one topic may span several code
repositories — register them all with repeatable `--workspace-repo` flags.

- 🧭 **Machine-independent routing** — any clone of a registered repo, on any
  machine, resolves to its topic via the git remote. Ambiguity asks instead of
  guessing; `--project <id>` disambiguates (and works with no workspace at all).
- 🏷️ **Repo facets** — agents pass `--workspace "$PWD"` when recording, so each
  record carries the repo(s) it touched (`repos:` plus `[[repo-name]]` graph links)
  and briefs group activity `by_repo`. A record spanning two repos is one record
  with two facets — never a filing dilemma.
- 🪦 **Retirement** — re-register with `--status done` to remove a finished topic
  from routing; its records stay readable forever.

## 🪝 Hooks (Claude Code)

Four optional hooks automate the loop (`scripts/hooks/`):

| Hook | Does |
|---|---|
| 🌅 SessionStart | Injects the brief (and auto-mode status) — sessions start pre-briefed |
| 🌙 SessionEnd | Leaves a capture marker for substantive sessions with no wrap-up |
| 📌 PostToolUse | After a real code commit, nudges an auto-mode checkpoint with the sha |
| 🗜️ PreCompact | Requests a checkpoint before the host compresses its context |

Hooks are instruction-only — they never write records; the live agent does, under
whatever consent mode the target vault is in. Register them in
`~/.claude/settings.json`:

```json
{ "hooks": { "SessionStart": [ { "hooks": [ { "type": "command",
  "command": "python3 \"$HOME/<plugin-path>/scripts/hooks/session_start.py\"" } ] } ] } }
```

(Codex has no hook surface; the skill protocol covers the same moments.)

## ⚡ Auto mode (v0.4 — experimental, default off)

Manual mode approves every record. Auto mode is **standing consent**: for vaults
you explicitly enable, agents record at milestone moments without pausing — schema
generated, tests green, decision settled, code committed, before compaction, and at
session wrap-up.

```bash
python3 "$CV" auto enable                 # all vaults — or scope it: --vault-name team
python3 "$CV" auto status                 # modes, pending syncs, skipped duplicates
CONTEXT_VAULT_MANUAL=1 ...                # per-session downgrade; nothing can force auto ON
```

What keeps it honest:

- 🏷️ Every auto record is stamped `consent: auto` with its trigger, session id,
  source commit, and a `basis` (observed / inferred / user-stated) — visible in
  every brief and filterable (`brief --consent auto`).
- 🔗 Mid-session checkpoints supersede each other, so one working session shows as
  one record; a local idempotency ledger (with per-record commit receipts) prevents
  duplicate or forked checkpoints.
- 🩹 Corrections are cheap and truthful:
  - `withdraw --record <stem> --reason "..."` — append-only tombstone; hides the
    record from current state while historical queries still see what was known at
    the time.
  - `retract --record <stem> --remove-from-current-tree` — a safe revert of a
    record-only commit within a 10-minute grace window. History and already-pulled
    clones retain the content, and the command says so.
  - 🚨 Leaked a credential? **Rotate it first** — treat vault presence as
    compromise. Then withdraw; history cleanup is a coordinated team operation, not
    a command.

Honest scope note: consent is a protocol enforced by your agent's instructions and
your host's own permission prompts — the design docs carry the full threat model
and never claim more than the code delivers.

## 🛟 Safety and privacy

- Proposals never write; every manual write requires `--confirm`; every record
  requires evidence.
- Secret-like strings (API keys, AWS keys, private-key headers) are rejected before
  persistence — a narrow net by design, not a guarantee.
- Raw transcripts are never stored; capture markers hold references only.
- Ambiguous routing fails loudly instead of guessing, and a repo-mapped workspace
  can never silently route into the wrong vault.

## 🧰 Command reference

| Command | Purpose |
|---|---|
| `configure --vault <path> [--identity <name>]` | Personal vault + identity |
| `project --name ... --workspace ... [--workspace-repo ...] [--status done]` | Register / retire a topic |
| `brief --workspace <dir> \| --project <id> [--consent auto\|manual]` | Task-start brief |
| `query --mode current\|historical\|provenance ...` | Time-aware queries |
| `propose-fact / propose-decision / propose-session` | Build a proposal (writes nothing) |
| `record-fact / record-decision / record-session ... --confirm` | Persist (confirm implied in auto mode) |
| `init-team --repo <url> [--identity <name>]` | Join or bootstrap a team vault |
| `sync` / `doctor` / `vault list` | Manual sync, health checks, config view |
| `auto enable\|disable\|status [--vault-name X]` | Standing consent per vault |
| `withdraw --record <stem> --reason ...` | Append-only correction tombstone |
| `retract --record <stem> --remove-from-current-tree` | Grace-window safe revert |

Run `python3 "$CV" --help` for every flag.

## 🏗️ How it's built

- 🐍 **Standard-library-only Python CLI** (`scripts/context_vault.py`) — no
  dependencies, no daemon. A standalone validator (`scripts/validate_vault.py`) is
  vendored into team vaults for CI.
- ✅ **140 unit and integration tests**, including two-clone git race simulations:
  concurrent writes, merge-driver conflicts, record-divergence quarantine,
  retraction propagation.

  ```bash
  python3 -m unittest discover -s tests -v
  ```

- 📐 **Spec-first** — every version was designed, independently reviewed, and
  revised before implementation. The specs, implementation plans, and verbatim
  review rounds — including the decision logs where trade-offs like auto mode's
  consent model were argued and settled — live in
  [`docs/superpowers/`](docs/superpowers/).

| Version | Adds |
|---|---|
| 🌱 v0.1 | Personal vault: bitemporal records, propose→approve consent gate |
| 🤝 v0.2 | Team vaults: locked git sync, merge driver + quarantine, attribution, disputes, CI |
| 🧵 v0.3 | Topics across repos, repo facets, one-command onboarding, capture hooks |
| ⚡ v0.4 | Auto mode: standing consent, milestone checkpoints, idempotency ledger, withdraw/retract |

## 📜 License

[MIT](LICENSE)
