#!/usr/bin/env python3
"""Claude Code SessionStart hook: inject the Context Vault brief and surface
pending capture markers.

Reads only. Any failure exits 0 silently; a workspace with no project simply
produces no brief.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BRIEF_TIMEOUT_SECONDS = 90

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import cli_path, log_hook_failure, resolve_workspace_mode  # noqa: E402
from session_end import cleanup, markers_dir  # noqa: E402


def run_auto_status() -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, str(cli_path()), "auto", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def run_brief(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, str(cli_path()), "brief", "--workspace", cwd],
            capture_output=True,
            text=True,
            check=False,
            timeout=BRIEF_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def pending_marker_text(directory: Path) -> str | None:
    markers = []
    for path in sorted(directory.glob("*.json")):
        try:
            markers.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    if not markers:
        return None
    lines = [
        "Pending Context Vault capture(s) from previous session(s) — as a small",
        "startup chore, for each marker below: read the transcript, build a",
        "session-recap proposal (propose-session, include --workspace/--branch/--pr",
        "where known), show it to the user, record it only after their explicit",
        "approval, then delete the marker file.",
    ]
    for path, marker in markers:
        lines.append(
            f"- marker: {path} | session: {marker.get('session_id')} | "
            f"cwd: {marker.get('cwd')} | transcript: {marker.get('transcript_path')} | "
            f"ended: {marker.get('ended_at')}"
        )
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    directory = markers_dir()
    cleanup(directory)
    sections = []
    cwd = str(payload.get("cwd") or "")
    if cwd:
        brief = run_brief(cwd)
        if brief:
            sections.append(
                "Context Vault brief for this workspace (evidence-backed context, "
                "not unquestionable truth; cite source paths when using it):\n" + brief
            )
    marker_text = pending_marker_text(directory)
    if marker_text:
        sections.append(marker_text)
    # Auto instructions apply only when THIS workspace routes to an auto vault;
    # another vault being auto elsewhere must not change behavior here.
    if cwd and resolve_workspace_mode(cwd) == "auto":
        digest = run_auto_status()
        if digest:
            sections.append(
                "Context Vault auto mode is ON for this workspace's vault "
                "(standing consent; record at milestones without pausing for "
                "approval; wrap up before ending). Status digest:\n" + digest
            )
    if not sections:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "\n\n".join(sections),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 — hooks must never break a session
        log_hook_failure("session_start", repr(error))
        raise SystemExit(0)
