#!/usr/bin/env python3
"""Claude Code PostToolUse hook (Bash matcher): after a successful `git commit`
in a code repository whose workspace routes to an auto-mode vault, nudge the
agent to record a checkpoint.

Instruction-only — this hook never writes records. Silent unless the commit is
real, in a code repo, and that repo's routed vault is in auto mode.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import is_vault_path_check, log_hook_failure, resolve_workspace_mode  # noqa: E402

# A git *subcommand* named commit — not the word "commit" anywhere in the text.
# Handles `git commit`, `git -C <dir> commit`, and chained `... && git commit`.
_COMMIT_RE = re.compile(
    r"(?:^|[;&|]\s*)git\s+(?:-C\s+(?P<cdir>\S+)\s+)?(?:-{1,2}[\w=-]+\s+)*commit\b"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    match = _COMMIT_RE.search(command)
    if not match:
        return 0
    if not (payload.get("tool_response") or {}).get("success", True):
        return 0
    cwd = str(payload.get("cwd") or "")
    # Attribute the commit to the repo git actually operated on (-C wins).
    repo_dir = match.group("cdir") or cwd
    if not repo_dir or is_vault_path_check(repo_dir):
        return 0
    if resolve_workspace_mode(repo_dir) != "auto":
        return 0
    sha = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "HEAD"],
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
                        f"(sha {sha[:12]}, repo {repo_dir}). If it completed a "
                        "milestone, record a Context Vault checkpoint now — "
                        "record-session with --trigger git-commit "
                        f"--source-commit {sha} --workspace \"{repo_dir}\", reusing "
                        "your session id and superseding your previous checkpoint. "
                        "Skip if this commit was not a meaningful milestone."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 — hooks must never break a session
        log_hook_failure("post_tool_use", repr(error))
        raise SystemExit(0)
