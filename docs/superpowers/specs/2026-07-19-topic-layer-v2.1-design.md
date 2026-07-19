# Topic Layer (v2.1) — Design

## Status

Approved in discussion 2026-07-19 (maintainer). This is the dedicated spec the v3
plan requires as a prerequisite.

## Problem

v2 projects map one-to-one with "a codebase area", but real work is organized
by *initiative* ("Fern importer") spanning several repos, with several
initiatives sharing the same repos concurrently. v2's one-workspace→one-project
routing cannot express that, and records don't say which repo they concern.

## Design

1. **Topic = project.** A project note *is* an initiative, with
   `workspace_repos` listing every repo it spans. No schema change; guidance
   plus the features below make it workable.
2. **Repo is a stamped facet on records, not a folder.** Record commands
   accept `--workspace <dir>` (agents pass `$PWD`); the CLI derives the
   workspace's normalized `origin` remote and stamps `repos: [...]` on the
   record. Explicit `--repo` flags (repeatable) override/extend. A record
   touching both repos carries both. Record bodies append a
   `Repos: [[<short-name>]]` line so each repo becomes an Obsidian graph
   node. Records with no derivable repo simply omit the field.
3. **Brief disambiguation.** Multiple topics legitimately share a repo, so
   workspace routing may become ambiguous. `brief`/`query` gain `--project
   <id>` (workspace then optional); the ambiguity error already lists
   candidates so an agent can ask the user and retry with `--project`.
4. **Topic retirement.** Project notes gain `status: active|done`
   (`--status` on the `project` command, default `active`). `done` topics are
   skipped by workspace routing and `--project` lookup; their records remain
   readable and `query` by explicit vault path still reaches them. Writes to
   a `done` topic are refused with a clear message (re-register with
   `--status active` to revive).
5. **Brief grouping.** Briefs add a compact `by_repo` index:
   `{repo: {facts: [ids], decisions: [ids], sessions: [ids]}}` so agents can
   present "in pageloop-ui: … · in text-agent: …".
6. **Onboarding polish** (rides along):
   - `init-team --identity <name>`: sets identity (creating a minimal config
     if none exists), so a teammate's entire onboarding is one command; a
     personal vault becomes optional.
   - `init-team` scaffolds `ONBOARDING.md` into the team vault repo (install
     plugin → one command → `doctor`), so onboarding is "here's the repo
     link".

## Compatibility

Existing single-repo projects keep working unchanged (they are single-topic
projects). No migration. Existing tests must pass unmodified.

## Testing

Repo derivation from workspace remote; `--repo` normalization and multi-repo
records; body repo links; `--project` brief without workspace; `done` topics
skipped in routing/lookup and write-refused; `by_repo` grouping; one-command
`init-team --identity` with no prior config; `ONBOARDING.md` scaffold;
full regression.
