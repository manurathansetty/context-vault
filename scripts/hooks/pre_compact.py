#!/usr/bin/env python3
"""Claude Code PreCompact hook: before context compaction, instruct the agent
to record an auto-mode checkpoint — the vault captures full-fidelity state at
exactly the moment agent memory is about to weaken.

Instruction-only; silent when auto mode is off.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import auto_mode_active  # noqa: E402


def main() -> int:
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not auto_mode_active():
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
    except Exception:  # noqa: BLE001 — hooks must never break a session
        raise SystemExit(0)
