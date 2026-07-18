# Cross-Team Context Vault — Design

## Status

Approved for implementation on 2026-07-19. Revised same day after external
design review (`2026-07-19-cross-team-vault-review.md`): reads never rebase,
per-vault locking, no-loss record-conflict quarantine, client-asserted
attribution language, append-only-by-protocol wording, dispute cardinality,
routing guardrail, and a two-team-vault cap.

## Problem

Context Vault v1 is single-user: one developer, one vault, one machine. On a
dev team, the context that matters most is shared — decisions a teammate made,
facts they discovered, and sessions they finished minutes ago on code that has
not merged yet. Today none of that reaches anyone else's brief.

Team memory raises problems personal memory never has: who recorded a claim,
what happens when two people record contradictory claims, how memory syncs
between machines without conflict pain, and how one developer's personal notes
stay out of a teammate's graph.

## Product outcome

Extend the plugin so any dev team can share project memory through a git-hosted
team vault while every developer keeps a fully private personal vault. A
teammate's session recap, decisions, and facts appear in your brief as soon as
their session ends — even when their code is still an unmerged pull request.
The design is general-purpose: any team adopts it by creating a private repo
and running one setup command.

V2 is opt-in. V1 behavior is unchanged until a user runs `init-team`; every
existing personal-vault command keeps its exact current behavior.

## Guiding principles

1. **The project is the unit of sharing, not the vault.** Personal projects
   never leave the personal vault; shared projects live wholly in a team vault.
2. **Memory travels faster than code.** Records reach the team the moment a
   session ends; code merges on its own schedule.
3. **Attribution is a property of shared memory, not of memory itself.**
   Team-vault records are stamped automatically; personal-vault records are
   never stamped. Stamps are **client-asserted attribution** — useful
   provenance, not proof of identity.
4. **Provenance, not authority.** The system records who said what and surfaces
   disputes; humans resolve them by superseding. No automatic ranking of
   authors.
5. **A clean git merge is not proof of consistent memory.** Semantic conflicts
   (contradictory active facts) are detected and surfaced at read time.
6. **Never blend or lose a record.** Records are immutable; correction happens
   only via `supersedes`. A divergent record preserves both versions
   byte-for-byte — the local one in place, the remote one in quarantine.
7. **Reads never mutate.** A read may fetch and fast-forward a clean vault; it
   never rebases, never touches local commits, and never leaves the vault in an
   intermediate git state.

## Scope

### In scope

- Multi-vault configuration (schema version 2) with a user identity and at most
  **two** team vaults.
- A team vault: a private git repository with the standard `codex-context/`
  layout plus `people/` and `conflicts/`.
- Machine-independent workspace-to-project matching via git remote URLs, with a
  guardrail against silent personal-vault fallback.
- Automatic author/agent stamping on team-vault records; `people/@name.md`
  person notes.
- Session records carrying branch and PR **as recorded metadata**.
- Git sync engine: per-vault OS lock, fetch + fast-forward-only reads,
  commit-and-push after confirmed writes with bounded rebase-retry, offline
  queueing, per-vault failure isolation.
- Conflict layer: custom merge driver for mutable notes, `merge_status` marks,
  record-divergence quarantine, agent repair chore, CI validation workflow with
  append-only violation detection.
- Brief upgrades: attribution, disputed-fact warnings with relation
  cardinality, team sessions, repair chores, per-vault sync status.
- New commands: `init-team`, `doctor`, `sync`; `--vault-name` label flags
  alongside the existing `--vault` path flag.

### Out of scope for this version

- A hosted service or API; real-time sync.
- Access control beyond git repository permissions.
- Authority ranking between authors; automated dispute resolution.
- Verified authorship (signed commits) — attribution is client-asserted.
- Determining whether a branch or PR has merged. Branch/PR are recorded
  metadata only; V2 makes no merge-state claim.
- Encryption at rest.
- Migrating (promoting) existing personal-vault records into a team vault.
- More than two team vaults.
- Changes to any code repository's workflow. The design touches only the vault
  repository; workspaces are read (`git remote get-url origin`) and never
  written.

### Considered and deferred (from design review)

- **Routing registry** (remote → vault map in config): redundant with the
  sync-all-then-route policy at ≤2 team vaults, and a second source of truth
  that can go stale. Revisit if the vault cap is lifted.
- **Project UUIDs**: fight the human-first `[[wiki-link]]` property; slug
  ambiguity already errors instead of guessing. Revisit if renames become a
  real problem.
- **Signed commits / verified identity**: future hardening; V2 documents
  attribution as client-asserted.
- **Code-host adapters for PR merge state**: V3 candidate; V2 records metadata
  and claims nothing.
- **Structural field-level merge rules for mutable notes**: the
  frontmatter-conflict → `needs-human` rule plus visible marks covers the harm;
  full field-merge machinery deferred.
- **Sync transaction journal**: the per-vault lock plus never-mid-rebase
  invariant covers recoverability at this scale.

## Chosen architecture

```text
Personal vault (~/Documents/context-vault)      Team vault clone (~/Documents/team-context)
  private, unstamped, no sync                     git repo: <your-org>/team-context-vault
        \                                              |  per-vault lock around every git op
         \                                             |  reads: fetch + ff-only when clean
          \                                            |  writes: commit + push (+ locked rebase retry)
           +----------- Context Vault CLI -------------+
                       - sync all team vaults, then route by remote/paths
                       - identity stamping (team only, client-asserted)
                       - merge driver + record quarantine
                       - brief with attribution/disputes/chores/sync map
```

Alternatives considered and rejected: Obsidian Sync shared vaults (no
attribution, silent character-level auto-merge, no headless-agent access,
per-user subscription); shared drives (conflicted-copy files, no history); a
hosted service (largest build, loses plain-files-in-Obsidian; revisit only if
org-wide scale demands it).

The team vault repository uses **direct commits to `main`** — no pull requests
and no manual merges. Client-side locked sync absorbs concurrent-write races.
The only branch protection is a force-push ban. History is an audit log and
records are append-only **by protocol**: the host cannot reject a bad direct
push, so CI detects and flags any modification of an existing record as a
high-severity violation rather than claiming immutability the host does not
enforce.

## Configuration and identity

`config.json` grows from a single path to named vaults plus an identity. The
legacy shape (`{"vault_path": ...}`) keeps working unchanged as single-vault
personal mode; the CLI reads it natively, so there is no migration step and the
rollback path is simply downgrading the plugin. Multi-vault configs carry
`"schema_version": 2`.

```json
{
  "schema_version": 2,
  "identity": "alex",
  "vaults": {
    "personal": {"path": "~/Documents/context-vault"},
    "team":     {"path": "~/Documents/team-context", "sync": "git"}
  }
}
```

- Vault names are user-chosen labels; **at most two vaults may have
  `sync: "git"`** — a deliberate V2 bound so sync-all-then-route stays fast and
  predictable. A third synced vault is a configuration error.
- Vaults without `sync` behave exactly as v1.
- `identity` is required before writing to any synced vault; the CLI refuses
  stamped writes without it.
- Flags: `--vault` always takes a **path** (v1-compatible); `--vault-name`
  takes a config **label**. Commands accept either, never both.

## Workspace resolution across machines

Team-vault project notes add `workspace_repos` — normalized git remote URLs
(for example `github.com/<your-org>/<app>`) — alongside the existing
`workspace_paths`.

Normalization handles HTTPS, SSH (`ssh://`), and scp-style (`git@host:org/repo`)
URLs, strips credentials and `.git`, and lowercases. Only the `origin` remote is
consulted; a workspace with no `origin` falls back to path matching. Host
aliases and renamed repositories are out of scope and documented as
limitations.

Resolution order for a workspace, run **after** the sync step (below) so notes
are fresh:

1. Read `git remote get-url origin`; match against `workspace_repos` across all
   configured vaults. A unique match routes there. Multiple matches error with
   candidates.
2. Fall back to `workspace_paths` prefix matching (v1 behavior).
3. **Guardrail:** a remote-recognized workspace never falls through to path
   matching (step 1 wins outright). And if path fallback selects a project
   whose note declares `workspace_repos` that do **not** include this
   workspace's remote, the CLI stops with an explanatory error
   (`pass --vault-name or register the project for this repository`) instead
   of silently routing repo-mapped work into the wrong project. Personal
   projects without `workspace_repos` keep matching by path exactly as in v1,
   whether or not the workspace has a remote.

## Sync policy (explicit and bounded)

Before any read, the CLI syncs **all** synced vaults — at most two by
configuration — under these rules:

- **Ordering:** config declaration order, deterministic.
- **Isolation:** each vault syncs independently; one vault's failure never
  blocks syncing the other or routing. A failed or unreachable vault serves its
  local notes, marked stale.
- **Bounding:** network git operations (`fetch`, `push`, `ls-remote`) run with
  a 15-second timeout; a timeout is treated as offline, not an error.
- **Output:** the brief's `sync` key is a per-vault map:
  `{<name>: {"online": bool, "last_synced": iso|null, "unpushed": int}}`.
  Agents must mention stale or unpushed state when presenting a brief.

## Locking and git-state invariants

- **Per-vault OS lock:** every git-touching operation (read-sync, write-push,
  init, doctor, manual sync) holds an exclusive `flock` on
  `<vault>/.git/context-vault.lock`. Two CLI processes — or Codex and Claude
  Code side by side — serialize instead of interleaving.
- **Reads never rebase.** A read-sync is `git fetch` plus fast-forward merge,
  attempted only when the checkout is clean and the local branch is not ahead.
  Otherwise the read serves local state with staleness/pending metadata.
- **Rebase only inside locked writes.** `sync_push` commits, pushes, and on
  rejection runs `pull --rebase` with bounded retries.
- **Never mid-rebase.** Any failed rebase is aborted immediately; the vault is
  always left on a normal checkout with local commits intact. Interrupted
  operations are recoverable by rerunning; `doctor` reports any residual
  abnormal state.

## Attribution and people notes

- Every record written to a synced vault is stamped by the CLI from config:
  `author: "[[@alex]]"` and `agent: <adapter>` (for example `claude-code`,
  `codex`, via `--agent` or `CONTEXT_VAULT_AGENT`). Values are never typed by
  hand and never applied to personal vaults.
- Stamps are **client-asserted**: they record who the writing client claimed to
  be. Git commit authorship is a second client-asserted trail. Neither is
  proof of identity, and no output may describe them as verified.
- `people/@<identity>.md` holds a stub person note (name, role). `init-team`
  creates the caller's stub if missing. Offboarding is recorded by setting
  `role: "inactive"` — person notes are never deleted, preserving historic
  attribution.

## Schema additions

- **Sessions** gain optional `branch` and `pr` frontmatter fields — recorded
  metadata describing where unmerged work lives (for example
  `branch: feat/orders-schema`, `pr: "#123"`). V2 does not determine or claim
  whether they have merged.
- **Facts** gain an optional `cardinality` field: `"exclusive"` (default,
  omitted) or `"multi"`. Only exclusive relations participate in dispute
  detection — two `owner` values conflict; two `contributor` values are normal.
- **Mutable notes** (project and person notes) may carry
  `merge_status: auto-merged | needs-human`, written only by the merge driver
  and removed only by the repair chore. Records never carry `merge_status`.
- Records remain append-only and immutable; `supersedes` remains the only
  correction mechanism. Record filenames gain a short random suffix, and record
  files are created with exclusive-create semantics (collision → regenerate
  suffix and retry), so identical-timestamp writes on two machines cannot
  collide or overwrite.

## Conflict layer

Three tiers, from structurally impossible to human-resolved:

1. **Records — no-loss quarantine, never merge, never mutate.** Every record is
   a new exclusively-created file, so divergence is structurally ~impossible.
   If the same record path ever does diverge, the merge driver keeps the local
   version **byte-for-byte unmodified** in place and writes the remote version
   **byte-for-byte** to `codex-context/conflicts/<name>.theirs.md`. Neither
   original is altered; nothing is lost. The quarantine entry is committed by
   the next CLI operation and surfaced as a high-priority repair chore;
   resolution is a human recording a superseding record, never editing either
   original.
2. **Mutable notes — auto-merge with a visible mark.** A custom git merge
   driver (Python standard library only: a diff-match-patch-style three-way
   merge built on `difflib`) is declared in the repo's `.gitattributes` and
   registered into each clone's git config by `init-team`. On overlapping body
   edits it merges, stamps `merge_status: auto-merged`, and lets the operation
   complete. Overlapping **frontmatter** edits are not blended: the driver
   keeps the local version and stamps `merge_status: needs-human`. GitHub's
   server-side merge cannot run custom drivers, which is irrelevant in a no-PR
   flow.
3. **Semantic conflicts — surfaced at read time.** Two active facts with the
   same subject and exclusive relation but different values are a dispute. The
   brief presents all values with authors and dates; resolution is a human
   recording a superseding fact.

**Repair chore:** the brief lists `merge_status`-marked files and quarantine
entries; the next agent working in that project fixes formatting, validates
frontmatter, removes the mark or resolves the quarantine, and commits an
attributed `repair:` commit.

**CI backstop:** a GitHub Actions workflow in the vault repo validates
frontmatter schema on every push (including required author stamps), flags
`merge_status` marks and quarantine entries older than a configurable number of
days, and — because append-only is protocol, not enforcement — diffs each push
and reports any modification or deletion of an existing record file as a
high-severity violation.

## Brief upgrades (team vaults)

In addition to v1 content, a team-vault brief includes:

- Author and agent on every fact, decision, and session shown (presented as
  claimed attribution).
- Disputed-fact warnings for exclusive relations, showing all conflicting
  active values side by side — never silently picking one.
- Recent sessions from all teammates, with `branch`/`pr` as recorded; merge
  state is not claimed.
- Pending repair chores (`merge_status` marks and quarantined records).
- The per-vault sync map (online/stale, last synced, unpushed count).

## Write protocol

Unchanged in spirit from v1: propose → show the user → explicit approval →
`record-* --confirm`. The approval gate stays per-person and in-session. When
the target is a synced vault, the proposal shown to the user must carry a
visibility warning ("this will be pushed to the team vault and visible to your
team") — the existing secret patterns are a narrow net, not a guarantee, and
the human approval step is the real gate. There is no team review step for
memory writes; git history is the audit mechanism and `git revert`/supersession
the correction mechanisms.

## New commands

| Command | Behavior |
| --- | --- |
| `init-team --repo <url> [--vault-name <label>] [--path <dir>]` | Clone (join) or adopt an existing clone of the team vault, register the merge driver, write `.gitattributes`/`.gitignore` (per-user `.obsidian/` state ignored), vendor `scripts/validate_vault.py` and the CI workflow, add the vault to config, create the caller's person stub. Idempotent. Bootstrap of a brand-new team vault = create an empty private repo on the host, then `init-team`. Enforces the two-team-vault cap. |
| `doctor` | Check identity set, per-vault: lock acquirable, merge driver registered, remote reachable (bounded), clean checkout / no residual rebase state, unpushed commits, stale marks and quarantine entries. Output is bounded and never embeds credentials. |
| `sync [--vault-name <label>]` | Manual locked pull-and-push for humans or cron. |
| `vault list` | Print configured vaults with path, sync mode, and identity. |

## Error handling

- Push fails after retries: the record is safe in a local commit; the CLI says
  so; `doctor`/next sync completes delivery.
- Unresolvable rebase during a write: abort, keep local commits, report; the
  vault is never left mid-rebase. Reads never enter this path.
- Lock held by another process: wait briefly, then report which operation to
  retry; never proceed unlocked.
- Merge driver missing in a clone: a conflicting locked write aborts safely
  (local commits kept) and `doctor` reports the unregistered driver.
- Missing identity on a synced-vault write: refused with the exact config fix.
- Third synced vault in config: refused with the cap explained.
- Ambiguous workspace, or the recognized-remote guardrail: error listing
  candidates / instructing `--vault-name`, never a silent guess.
- Dirty checkout, missing upstream, detached HEAD: reported by `doctor`; reads
  serve local state with staleness rather than failing.

## Testing

- **Unit:** config parsing (legacy, v2, cap violation), vault routing and the
  fallback guardrail, remote-URL normalization, identity stamping,
  exclusive-create retry, merge driver (clean merge, overlapping body,
  overlapping frontmatter, record divergence quarantine), dispute detection
  with cardinality, session branch/PR fields, lock serialization.
- **Integration:** two clones plus a bare origin repository simulating the real
  races — concurrent record writes, project-note double-edit, record-path
  divergence preserving both versions, offline queueing and delayed push,
  push-rejection retry, read-sync on a dirty/ahead checkout serving local
  state.
- **Regression:** the entire existing single-vault test suite passes
  unmodified.

## Success criteria

- A teammate's session recorded after their session ends appears in another
  machine's next `brief` with author and with branch/PR **as recorded**
  (merge state not claimed), before the related code PR merges.
- Two simultaneous record writes from two clones both land on `main` with no
  human intervention and no loss.
- A forced record-path divergence ends with both versions intact — local in
  place, remote quarantined — and a repair chore in the next brief.
- A simultaneous double-edit of a project note lands merged and stamped, and is
  listed as a repair chore.
- Contradictory active exclusive facts appear as a dispute with both authors;
  multi-valued facts do not.
- A recognized git remote never silently routes to a personal vault.
- Two CLI processes on one clone serialize; neither ever observes or leaves a
  mid-rebase state.
- A v1 user upgrading the plugin notices no behavior change until `init-team`.
