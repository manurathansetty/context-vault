"""Shared helpers for Context Vault hook scripts. Best-effort: never raise."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def cli_path() -> Path:
    override = os.environ.get("CONTEXT_VAULT_CLI")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "context_vault.py"


def resolve_workspace_mode(cwd: str) -> str | None:
    """Effective consent mode of the vault this workspace routes to.

    Mode is a property of the routed vault — never of the config as a whole —
    so hooks must not treat 'some vault somewhere is auto' as auto. Returns
    None when routing is unavailable (hooks then stay silent)."""
    if os.environ.get("CONTEXT_VAULT_MANUAL") == "1":
        return "manual"
    try:
        result = subprocess.run(
            [sys.executable, str(cli_path()), "resolve-mode", "--workspace", cwd],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout).get("mode")
    except json.JSONDecodeError:
        return None


def is_vault_path_check(cwd: str) -> bool:
    """True when cwd lies inside any configured vault (records repo, not code)."""
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


def log_hook_failure(hook: str, error: str) -> None:
    try:
        path = config_base()
        path.mkdir(parents=True, exist_ok=True)
        log = path / "hook-failures.log"
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"{hook}: {error}"[:500] + "\n")
        os.chmod(log, 0o600)
    except OSError:
        pass


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
