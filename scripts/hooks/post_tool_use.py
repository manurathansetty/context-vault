#!/usr/bin/env python3
"""Claude Code PostToolUse hook (Bash matcher): after a successful `git commit`
in a code repository, nudge the agent to record an auto-mode checkpoint.

Instruction-only — this hook never writes records. Silent unless auto mode is
active and the command was a real code-repo commit.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import auto_mode_active, load_raw_config  # noqa: E402


def is_vault_path(cwd: str) -> bool:
    config = load_raw_config()
    paths = []
    if isinstance(config.get("vault_path"), str):
        paths.append(config["vault_path"])
    vaults = config.get("vaults")
    if isinstance(vaults, dict):
        paths.extend(
            entry.get("path")
            for entry in vaults.values()
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        )
    try:
        resolved = Path(cwd).resolve()
    except OSError:
        return False
    for path in paths:
        try:
            if resolved.is_relative_to(Path(path).expanduser().resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if "git commit" not in command or "revert" in command:
        return 0
    if not (payload.get("tool_response") or {}).get("success", True):
        return 0
    if not auto_mode_active():
        return 0
    cwd = str(payload.get("cwd") or "")
    if not cwd or is_vault_path(cwd):
        return 0
    sha = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not sha:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "Auto mode: a code commit just landed "
                        f"(sha {sha[:12]}, cwd {cwd}). If it completed a milestone, "
                        "record a Context Vault checkpoint now — "
                        "record-session with --trigger git-commit "
                        f"--source-commit {sha} --workspace \"{cwd}\", reusing your "
                        "session id and superseding your previous checkpoint. Skip "
                        "if this commit was not a meaningful milestone."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 — hooks must never break a session
        raise SystemExit(0)
