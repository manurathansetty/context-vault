# Cross-Team Context Vault — Design

## Status

Approved for implementation on 2026-07-19.

## Problem

Context Vault v1 is single-user: one developer, one vault, one machine. On a dev team, the context that matters most is shared — decisions a teammate made, facts they discovered, and sessions they finished minutes ago on code that has not merged yet. Today none of that reaches anyone else's brief.

Team memory raises problems personal memory never has: who recorded a claim, what happens when two people record contradictory claims, how memory syncs between machines without conflict pain, and how one developer's personal notes stay out of a teammate's graph.

## Product outcome

Extend the plugin so any dev team can share project memory through a git-hosted team vault while every developer keeps a fully private personal vault. A teammate's session recap, decisions, and facts appear in your brief as soon as their session ends — even when their code is still an unmerged pull request. The design is general-purpose: any team adopts it by creating a private repo and running one setup command.

## Guiding principles

1. **The project is the unit of sharing, not the vault.** Personal projects never leave the personal vault; shared projects live wholly in a team vault.
2. **Memory travels faster than code.** Records reach the team the moment a session ends; code merges on its own schedule.
3. **Attribution is a property of shared memory, not of memory itself.** Team-vault records are stamped automatically; personal-vault records are never stamped.
4. **Provenance, not authority.** The system records who said what and surfaces disputes; humans resolve them by superseding. No automatic ranking of authors.
5. **A clean git merge is not proof of consistent memory.** Semantic conflicts (contradictory active facts) are detected and surfaced at read time.
6. **Never blend two versions of a record.** Auto-merge applies only to mutable notes; records are immutable and correction happens only via `supersedes`.

## Scope

### In scope

- Multi-vault configuration with per-vault git sync and a user identity.
- A team vault: a private git repository with the standard `codex-context/` layout plus `people/`.
- Machine-independent workspace-to-project matching via git remote URLs.
- Automatic author/agent stamping on team-vault records; `people/@name.md` person notes.
- Session records carrying branch and PR state for unmerged-work handoffs.
- Git sync engine: pull before reads, commit-and-push after confirmed writes, bounded retry on push races, offline queueing.
- Conflict layer: custom merge driver for mutable notes, `merge_status` marks, agent repair chore, CI validation workflow.
- Brief upgrades: attribution, disputed-fact warnings, team sessions with merge state, repair chores, staleness.
- New commands: `init-team`, `doctor`, `sync`.

### Out of scope for this version

- A hosted service or API; real-time sync.
- Access control beyond git repository permissions.
- Authority ranking between authors; automated dispute resolution.
- Encryption at rest.
- Migrating (promoting) existing personal-vault records into a team vault.
- Changes to any code repository's workflow. The design touches only the vault repository.

## Chosen architecture

```text
Personal vault (~/Documents/context-vault)      Team vault clone (~/Documents/team-context)
  private, unstamped, no sync                     git repo: <your-org>/team-context-vault
        \                                              |  pull --rebase before reads
         \                                             |  commit + push after confirmed writes
          +----------- Context Vault CLI --------------+
                       - vault routing by project
                       - identity stamping (team only)
                       - sync engine + merge driver
                       - brief with attribution/disputes
```

Alternatives considered and rejected: Obsidian Sync shared vaults (no attribution, silent character-level auto-merge, no headless-agent access, per-user subscription); shared drives (conflicted-copy files, no history); a hosted service (largest build, loses plain-files-in-Obsidian; revisit only if org-wide scale demands it).

The team vault repository uses **direct commits to `main`** — no pull requests and no manual merges. Client-side `pull --rebase` plus the merge driver absorb concurrent-write races. The only branch protection is a force-push ban, so history remains an append-only audit log.

## Configuration and identity

`config.json` grows from a single path to named vaults plus an identity. The legacy shape (`{"vault_path": ...}`) keeps working unchanged as single-vault personal mode.

```json
{
  "identity": "alex",
  "vaults": {
    "personal": {"path": "~/Documents/context-vault"},
    "team":     {"path": "~/Documents/team-context", "sync": "git"}
  }
}
```

- Vault names are user-chosen labels; multiple team vaults are allowed.
- `sync: "git"` marks a vault as a git clone the sync engine manages. Vaults without `sync` behave exactly as v1.
- `identity` is required before writing to any synced vault; the CLI refuses stamped writes without it.

## Workspace resolution across machines

Team-vault project notes add `workspace_repos` — normalized git remote URLs (for example `github.com/<your-org>/<app>`) — alongside the existing `workspace_paths`. Resolution order for a workspace:

1. Read `git remote get-url origin` in the workspace; match against `workspace_repos` across all configured vaults.
2. Fall back to `workspace_paths` prefix matching (v1 behavior, still used by personal vaults).

A project found in a team vault routes both reads and writes to that vault. Ambiguity (a workspace matching projects in two vaults) is reported as an error listing the candidates; the CLI never guesses.

## Attribution and people notes

- Every record written to a synced vault is stamped by the CLI from config: `author: "[[@alex]]"` and `agent: <adapter>` (for example `claude-code`, `codex`). Values are never typed by hand and never applied to personal vaults.
- `people/@<identity>.md` holds a stub person note (name, role). `init-team` creates the caller's stub if missing. Obsidian renders each person as a graph node linked to everything they recorded.
- Git commit authorship provides a second, transport-level record of the same information.

## Schema additions

- **Sessions** gain optional `branch` and `pr` frontmatter fields so a brief can state where unmerged work lives (for example `branch: feat/orders-schema`, `pr: "#123"`).
- **Mutable notes** (project and person notes) may carry `merge_status: auto-merged | needs-human`, written only by the merge driver and removed only by the repair chore.
- Records (facts, decisions, sessions) remain append-only and immutable; `supersedes` remains the only correction mechanism. Record filenames gain a short random suffix so identical-microsecond writes on two machines cannot collide.

## Sync engine

- **Before any read** (`brief`, `query`): `git pull --rebase` on the target vault. If the remote is unreachable, serve the local copy and include "last synced <age> ago" in the output.
- **After `record-* --confirm`**: write the file, `git add`, `git commit` with a self-describing message (`record fact: [[Auth service]] owner`), `git push`. A rejected push triggers `pull --rebase` and one bounded retry loop.
- **Offline writes** commit locally and push on the next successful sync; the brief reports "N records not yet pushed".
- The engine never leaves the vault mid-rebase: any unresolvable state aborts the rebase, keeps the local commit, and reports plainly.

## Conflict layer

Three tiers, from structurally impossible to human-resolved:

1. **Records — no merges by construction.** Every record is a new file with a unique name. If the same record path ever diverges (corruption, manual meddling), the driver keeps the local version and stamps `merge_status: needs-human`; it never blends two versions of a record.
2. **Mutable notes — auto-merge with a visible mark.** A custom git merge driver (Python standard library only: a diff-match-patch-style three-way merge built on `difflib`) is declared in the repo's `.gitattributes` and registered into each clone's git config by `init-team` (git does not auto-install drivers from cloned repos). On overlapping edits it merges, stamps `merge_status: auto-merged`, and lets the rebase complete. GitHub's server-side merge cannot run custom drivers, which is irrelevant here because nothing merges server-side in a no-PR flow.
3. **Semantic conflicts — surfaced at read time.** Two active facts with the same subject and relation but different values are a dispute. The brief presents both values with authors and dates; resolution is a human recording a superseding fact.

**Repair chore:** the brief lists files carrying `merge_status`; the next agent working in that project fixes formatting, validates frontmatter, removes the mark, and commits an attributed `repair:` commit.

**CI backstop:** a GitHub Actions workflow in the vault repo validates frontmatter schema on every push (including required author stamps) and flags `merge_status` marks older than a configurable number of days.

## Brief upgrades (team vaults)

In addition to v1 content, a team-vault brief includes:

- Author and agent on every fact, decision, and session shown.
- Disputed-fact warnings, showing all conflicting active values side by side — never silently picking one.
- Recent sessions from all teammates, with branch/PR and whether that code has merged.
- Pending repair chores (`merge_status` marks).
- Sync status: last-synced age and any unpushed local records.

## Write protocol

Unchanged in spirit from v1: propose → show the user → explicit approval → `record-* --confirm`. The approval gate stays per-person and in-session. There is no team review step for memory writes; git history is the audit mechanism and `git revert`/supersession the correction mechanisms.

## New commands

| Command | Behavior |
| --- | --- |
| `init-team --repo <url> [--name <label>] [--path <dir>]` | Clone the team vault, register the merge driver in the clone, add the vault to config, create the caller's person stub. Idempotent. |
| `doctor` | Check identity set, remotes reachable, merge driver registered, unpushed commits, stale `merge_status` marks. |
| `sync [--vault <label>]` | Manual pull-and-push for humans or cron. |

## Error handling

- Push fails after retries: the record is safe in a local commit; the CLI says so and `doctor`/next sync completes delivery.
- Unresolvable rebase state: abort, keep local commits, report; the vault is never left conflicted.
- Merge driver missing in a clone: a conflicting pull aborts safely (local commits kept, vault never left mid-rebase) and `doctor` reports the unregistered driver.
- Missing identity on a synced-vault write: refused with the exact config fix.
- Ambiguous workspace: error listing candidate projects and vaults.

## Testing

- **Unit:** config parsing (legacy and multi-vault), vault routing, remote-URL normalization and matching, identity stamping, merge driver (clean merge, overlapping edit, record-file divergence), disputed-fact detection, session branch/PR fields.
- **Integration:** two clones plus a bare origin repository simulating the real races — concurrent record writes, project-note double-edit resolved by the driver with a `merge_status` stamp, offline queueing and delayed push, push-rejection retry.
- **Regression:** the entire existing single-vault test suite passes unmodified.

## Success criteria

- A teammate's session recorded after their session ends appears in another machine's next `brief` with author, branch, and PR state, before the related code PR merges.
- Two simultaneous record writes from two clones both land on `main` with no human intervention and no conflict artifacts.
- A simultaneous double-edit of a project note lands merged, stamped `merge_status: auto-merged`, and is listed as a repair chore in the next brief.
- Contradictory active facts appear in the brief as an explicit dispute with both authors.
- A v1 user upgrading the plugin notices no behavior change until they run `init-team`.
