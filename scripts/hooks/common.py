"""Shared helpers for Context Vault hook scripts. Best-effort: never raise."""
from __future__ import annotations

import json
import os
from pathlib import Path


def config_base() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "context-vault"


def load_raw_config() -> dict:
    for candidate in (
        config_base() / "config.json",
        Path.home() / ".codex" / "context-vault" / "config.json",
    ):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def auto_mode_active() -> bool:
    """True when any configured vault is in auto mode (downgrade override wins)."""
    if os.environ.get("CONTEXT_VAULT_MANUAL") == "1":
        return False
    config = load_raw_config()
    if config.get("default_mode") == "auto":
        return True
    vaults = config.get("vaults")
    if isinstance(vaults, dict):
        return any(
            isinstance(entry, dict) and entry.get("mode") == "auto"
            for entry in vaults.values()
        )
    return False


def ledger_has_wrapup(session_id: str) -> bool:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:80] or "unnamed"
    path = config_base() / "ledger" / f"{safe}.jsonl"
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("event") == "write" and entry.get("trigger") == "wrapup":
                return True
    except OSError:
        return False
    return False
