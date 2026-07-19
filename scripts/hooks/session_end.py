#!/usr/bin/env python3
"""Claude Code SessionEnd hook: leave a capture marker for substantive sessions.

Markers are references only (session id, transcript path, cwd) — never
transcript content. Best-effort by design: any failure exits 0 silently.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MARKER_SCHEMA = 1
MIN_USER_MESSAGES = 5
MIN_TRANSCRIPT_BYTES = 20_000
MARKER_MAX_AGE_DAYS = 14


def markers_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    directory = base / "context-vault" / "pending-markers"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def cleanup(directory: Path) -> None:
    cutoff = time.time() - MARKER_MAX_AGE_DAYS * 86400
    for path in directory.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def is_substantive(transcript_path: str) -> bool:
    path = Path(transcript_path)
    try:
        if path.stat().st_size >= MIN_TRANSCRIPT_BYTES:
            return True
        user_messages = 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    if json.loads(line).get("type") == "user":
                        user_messages += 1
                except (json.JSONDecodeError, AttributeError):
                    continue
                if user_messages >= MIN_USER_MESSAGES:
                    return True
    except OSError:
        return False
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    directory = markers_dir()
    cleanup(directory)
    session_id = str(payload.get("session_id") or "")
    transcript_path = str(payload.get("transcript_path") or "")
    if not session_id or not transcript_path:
        return 0
    if not is_substantive(transcript_path):
        return 0
    safe_id = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:80]
    marker_path = directory / f"{safe_id}.json"
    marker = {
        "schema": MARKER_SCHEMA,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": str(payload.get("cwd") or ""),
        "ended_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        # Exclusive create = idempotence across repeated SessionEnd events.
        descriptor = os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(marker, handle, indent=2)
    except FileExistsError:
        pass
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 — hooks must never break a session
        raise SystemExit(0)
