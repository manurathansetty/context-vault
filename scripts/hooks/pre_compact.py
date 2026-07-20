#!/usr/bin/env python3
"""Claude Code PreCompact hook: before context compaction, instruct the agent
to record a checkpoint — but only when the current workspace routes to an
auto-mode vault. Instruction-only; silent otherwise."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import log_hook_failure, resolve_workspace_mode  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    cwd = str(payload.get("cwd") or "")
    if not cwd or resolve_workspace_mode(cwd) != "auto":
        # Mode belongs to the routed vault; with no routable workspace, stay silent.
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreCompact",
                    "additionalContext": (
                        "Auto mode: context is about to be compacted. Record a "
                        "Context Vault checkpoint now (record-session with "
                        "--trigger precompact, reusing your session id and "
                        "superseding your previous checkpoint) so full-fidelity "
                        "state is persisted before summarization."
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
        log_hook_failure("pre_compact", repr(error))
        raise SystemExit(0)
