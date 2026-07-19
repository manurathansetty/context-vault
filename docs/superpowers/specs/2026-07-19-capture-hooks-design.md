# Capture Hooks (Tiers 1–2) — Design

## Status

Approved in discussion 2026-07-19 (maintainer). This is the dedicated spec the v3
plan requires as a prerequisite. Claude Code only in this iteration (Codex
has no equivalent hook surface; its skill protocol stands in).

## Tiers

- **Tier 1 — automate reads (SessionStart):** a hook runs
  `brief --workspace <cwd>` and injects the result as additional context, so
  every session starts pre-briefed. Zero risk: reads only. If the workspace
  maps to no project, or the CLI fails, the hook exits silently.
- **Tier 2 — automate the prompting, keep approval (SessionEnd +
  SessionStart):** SessionEnd leaves a *marker* (not a draft) for substantive
  sessions; the next SessionStart surfaces pending markers with an
  instruction to propose a session recap from the recorded transcript for
  the user's approval, then delete the marker. The consent gate is untouched
  — Tier 2 automates *remembering to ask*, nothing else.
- **Tier 3 (config only, default off):** `auto_record: ["session"]` exists
  as a documented standing-consent flag but no automation uses it in this
  iteration.

## Marker store contract (P0-3-lite from the v3 review)

Markers live in `~/.config/context-vault/pending-markers/`, directory created
`0700`. A marker is a small JSON file named `<session_id>.json` (exclusive
create = dedup across repeated SessionEnd events) containing only
*references*: session id, transcript path, cwd, ended-at timestamp, schema
version — never transcript content, so no redaction burden at this tier.
Cleanup runs at hook invocation time (markers older than 14 days deleted),
not via trust in future hooks. Best-effort by design: crashes and unsupported
hosts skip capture; the system never claims complete coverage.

## Substance threshold

SessionEnd writes a marker only when the transcript suggests real work:
at least 5 user messages or 20 KB of transcript. Below threshold, no marker.

## Hook wiring

Hook scripts ship in the plugin (`scripts/hooks/session_start.py`,
`scripts/hooks/session_end.py`, stdlib Python, JSON on stdin per Claude Code
hook protocol). `~/.claude/settings.json` registers them for SessionStart and
SessionEnd. The scripts call the CLI at its installed plugin path with a
fallback to `~/plugins/context-vault` for development.

## Testing

Marker written above threshold with exclusive-create dedup; no marker below
threshold; cleanup removes expired markers; SessionStart output shape with a
routable workspace, with pending markers, and with neither (silent);
directory permissions.
