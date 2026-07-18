from __future__ import annotations

import argparse
import contextlib
import difflib
import fcntl
import json
import os
import re
import secrets
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class ContextVaultError(Exception):
    """Base error for Context Vault operations."""


class ConfirmationRequiredError(ContextVaultError):
    """Raised when a write has not been explicitly confirmed."""


class ProjectNotFoundError(ContextVaultError):
    """Raised when a workspace has no registered project."""


class AmbiguousProjectError(ContextVaultError):
    """Raised when more than one project claims a workspace."""


class SensitiveContentError(ContextVaultError):
    """Raised when a proposed memory appears to contain a credential."""


class DecisionNotFoundError(ContextVaultError):
    """Raised when a provenance query cannot identify one decision."""


SENSITIVE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _require_confirmation(confirm: bool) -> None:
    if confirm is not True:
        raise ConfirmationRequiredError("write requires explicit confirmation")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assert_safe_strings(values: list[str]) -> None:
    for value in values:
        if any(pattern.search(value) for pattern in SENSITIVE_PATTERNS):
            raise SensitiveContentError("secret-like content cannot be stored in Context Vault")


VAULT_FOLDERS = ("projects", "decisions", "facts", "sessions", "templates", "people", "conflicts")


def configure(vault: Path, config_home: Path | None = None, identity: str | None = None) -> Path:
    vault = vault.expanduser().resolve()
    notes_root = vault / "codex-context"
    for folder in VAULT_FOLDERS:
        (notes_root / folder).mkdir(parents=True, exist_ok=True)
    try:
        config = load_config(config_home)
    except ContextVaultError:
        config = {"identity": None, "vaults": {}}
    config["vaults"]["personal"] = {"path": vault, "sync": None}
    if identity:
        config["identity"] = identity
    return save_config(config, config_home)


def _config_dir() -> Path:
    """Return the canonical write location for the config file.

    Writes go to ``$XDG_CONFIG_HOME/context-vault`` (defaulting to
    ``~/.config/context-vault`` when ``XDG_CONFIG_HOME`` is unset), keeping the
    tool decoupled from any single agent host.
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "context-vault"


def _legacy_config_dir() -> Path:
    """Return the pre-portability location used by Codex-only installs."""
    return Path.home() / ".codex" / "context-vault"


def _parse_config(payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload.get("identity")
    if identity is not None and (not isinstance(identity, str) or not identity.strip()):
        raise ContextVaultError("configured identity is invalid")
    if "vaults" in payload:
        raw_vaults = payload["vaults"]
        if not isinstance(raw_vaults, dict) or not raw_vaults:
            raise ContextVaultError("config vaults table must be a non-empty object")
        vaults: dict[str, dict[str, Any]] = {}
        for name, entry in raw_vaults.items():
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not path.strip():
                raise ContextVaultError(f"vault {name!r} has an invalid path")
            vaults[name] = {"path": Path(path).expanduser().resolve(), "sync": entry.get("sync")}
        synced = [name for name, entry in vaults.items() if entry.get("sync") == "git"]
        if len(synced) > 2:
            raise ContextVaultError(
                "at most two team vaults are supported; remove one of: " + ", ".join(synced)
            )
        return {"identity": identity, "vaults": vaults}
    vault_path = payload.get("vault_path")
    if not isinstance(vault_path, str) or not vault_path.strip():
        raise ContextVaultError("configured vault path is invalid")
    return {
        "identity": identity,
        "vaults": {"personal": {"path": Path(vault_path).expanduser().resolve(), "sync": None}},
    }


def load_config(config_home: Path | None = None) -> dict[str, Any]:
    # Prefer the portable location, then fall back to the legacy Codex path so
    # existing users keep working without re-running `configure`.
    search_paths = (
        (config_home or _config_dir()) / "config.json",
        _legacy_config_dir() / "config.json",
    )
    for config_path in search_paths:
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            raise ContextVaultError("config must be a JSON object")
        return _parse_config(payload)
    raise ContextVaultError(
        "no configured vault; run `context_vault.py configure --vault /path/to/vault`"
    )


def save_config(config: dict[str, Any], config_home: Path | None = None) -> Path:
    config_dir = (config_home or _config_dir()).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    vaults = config["vaults"]
    only_personal = list(vaults) == ["personal"] and not vaults["personal"].get("sync")
    if only_personal:
        # Legacy shape keeps previously installed plugin versions working.
        if config.get("identity"):
            payload["identity"] = config["identity"]
        payload["vault_path"] = str(vaults["personal"]["path"])
    else:
        payload["schema_version"] = 2
        if config.get("identity"):
            payload["identity"] = config["identity"]
        payload["vaults"] = {
            name: {
                "path": str(entry["path"]),
                **({"sync": entry["sync"]} if entry.get("sync") else {}),
            }
            for name, entry in vaults.items()
        }
    config_path = config_dir / "config.json"
    _write_json_atomic(config_path, payload)
    return config_path


def configured_vault() -> Path:
    config = load_config()
    vaults = config["vaults"]
    if len(vaults) == 1:
        return next(iter(vaults.values()))["path"]
    raise ContextVaultError(
        "multiple vaults configured; pass --vault or use a workspace-routed command"
    )


def _vault_from_argument(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else configured_vault()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("note id must contain at least one letter or digit")
    return slug


def _record_suffix() -> str:
    """Random suffix keeping simultaneous record filenames collision-free across machines."""
    return secrets.token_hex(3)


def _write_markdown(
    path: Path, metadata: dict[str, object], body: str, *, exclusive: bool = False
) -> Path:
    lines = ["---"]
    lines.extend(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    lines.extend(["---", "", body.rstrip("\n"), ""])
    content = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)
    return path


def write_note(
    root: Path,
    folder: str,
    metadata: dict[str, object],
    body: str,
    *,
    exclusive: bool = False,
) -> Path:
    if not metadata.get("id"):
        raise ValueError("note metadata requires an id")
    for key in metadata:
        if not isinstance(key, str) or ":" in key or "\n" in key:
            raise ValueError("metadata keys must be single-line keys without colons")
    path = root / folder / f"{slugify(str(metadata['id']))}.md"
    return _write_markdown(path, metadata, body, exclusive=exclusive)


def read_note(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} is missing frontmatter")
    try:
        frontmatter, body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"{path} has unterminated frontmatter") from exc

    metadata: dict[str, Any] = {}
    for line in frontmatter.splitlines():
        key, separator, raw_value = line.partition(": ")
        if not separator:
            raise ValueError(f"{path} has invalid frontmatter line: {line}")
        metadata[key] = json.loads(raw_value)
    if body.startswith("\n"):
        body = body[1:]
    return {"path": path, "metadata": metadata, "body": body}


def _read_folder(notes_root: Path, folder: str) -> list[dict[str, Any]]:
    directory = notes_root / folder
    if not directory.is_dir():
        return []
    return [read_note(path) for path in sorted(directory.glob("*.md"))]


def record_project(
    vault: Path,
    name: str,
    workspace_paths: list[str],
    goal: str,
    open_questions: list[str],
    confirm: bool,
    *,
    workspace_repos: list[str] | None = None,
    recorded_at: str | None = None,
) -> Path:
    _require_confirmation(confirm)
    if not name.strip() or not goal.strip():
        raise ValueError("project name and goal are required")
    project_id = slugify(name)
    normalized_paths = [str(Path(path).expanduser().resolve()) for path in workspace_paths]
    normalized_repos = [normalize_remote_url(repo) for repo in (workspace_repos or [])]
    metadata: dict[str, object] = {
        "id": project_id,
        "type": "project",
        "name": name.strip(),
        "workspace_paths": normalized_paths,
        "workspace_repos": normalized_repos,
        "goal": goal.strip(),
        "open_questions": open_questions,
        "recorded_at": recorded_at or _timestamp(),
    }
    return write_note(vault / "codex-context", "projects", metadata, f"# {name.strip()}\n")


def find_project(notes_root: Path, workspace: Path) -> dict[str, Any]:
    workspace_path = workspace.expanduser().resolve()
    ancestor_depths = {
        str(path): len(path.parts) for path in (workspace_path, *workspace_path.parents)
    }
    scored: list[tuple[int, dict[str, Any]]] = []
    for note in _read_folder(notes_root, "projects"):
        depths = [
            ancestor_depths[registered]
            for registered in note["metadata"].get("workspace_paths", [])
            if registered in ancestor_depths
        ]
        if depths:
            scored.append((max(depths), note))
    if not scored:
        raise ProjectNotFoundError(f"no Context Vault project matches {workspace_path}")
    best_depth = max(depth for depth, _ in scored)
    matches = [note for depth, note in scored if depth == best_depth]
    if len(matches) > 1:
        names = ", ".join(str(match["metadata"].get("name")) for match in matches)
        raise AmbiguousProjectError(f"workspace matches multiple projects: {names}")
    return matches[0]


def normalize_remote_url(url: str) -> str:
    """Normalize a git remote URL to host/org/repo for machine-independent matching.

    Handles https://, ssh://, and scp-style (git@host:org/repo) forms. Host
    aliases and renamed repositories are documented limitations.
    """
    stripped = re.sub(r"^[a-z+]+://", "", url.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"^[^@/]+@", "", stripped)
    host, separator, rest = stripped.partition(":")
    if separator and "/" not in host:
        stripped = f"{host}/{rest}"
    if stripped.endswith(".git"):
        stripped = stripped[:-4]
    return stripped.rstrip("/").lower()


def workspace_remote(workspace: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(workspace), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    url = result.stdout.strip()
    if result.returncode != 0 or not url:
        return None
    return normalize_remote_url(url)


def find_project_across_vaults(
    config: dict[str, Any], workspace: Path
) -> tuple[str, dict[str, Any]]:
    remote = workspace_remote(workspace)
    if remote:
        remote_matches: list[tuple[str, dict[str, Any]]] = []
        for name, entry in config["vaults"].items():
            for note in _read_folder(entry["path"] / "codex-context", "projects"):
                repos = [
                    normalize_remote_url(str(repo))
                    for repo in note["metadata"].get("workspace_repos", [])
                ]
                if remote in repos:
                    remote_matches.append((name, note))
        if len(remote_matches) > 1:
            names = ", ".join(
                f"{note['metadata'].get('name')} ({vault})" for vault, note in remote_matches
            )
            raise AmbiguousProjectError(f"workspace matches multiple projects: {names}")
        if remote_matches:
            return remote_matches[0]
    path_matches: list[tuple[str, dict[str, Any]]] = []
    for name, entry in config["vaults"].items():
        try:
            note = find_project(entry["path"] / "codex-context", workspace)
        except ProjectNotFoundError:
            continue
        path_matches.append((name, note))
    if not path_matches:
        raise ProjectNotFoundError(f"no Context Vault project matches {workspace}")
    if len(path_matches) > 1:
        names = ", ".join(
            f"{note['metadata'].get('name')} ({vault})" for vault, note in path_matches
        )
        raise AmbiguousProjectError(f"workspace matches projects in multiple vaults: {names}")
    name, note = path_matches[0]
    declared = [
        normalize_remote_url(str(repo)) for repo in note["metadata"].get("workspace_repos", [])
    ]
    if remote and declared and remote not in declared:
        # Guardrail: never silently route repo-mapped work into the wrong project.
        raise ContextVaultError(
            f"workspace remote {remote} does not match project "
            f"{note['metadata'].get('name')!r} ({', '.join(declared)}); "
            "pass --vault-name or register the project for this repository"
        )
    return name, note


def _vault_by_name(config: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return config["vaults"][name]
    except KeyError:
        raise ContextVaultError(f"no vault named {name!r}; run `vault list`") from None


def resolve_write_vault(
    config: dict[str, Any], project: str, explicit: str | None
) -> tuple[dict[str, Any], Path]:
    if explicit:
        explicit_path = Path(explicit).expanduser().resolve()
        for entry in config["vaults"].values():
            if entry["path"] == explicit_path:
                return entry, explicit_path
        return {"path": explicit_path, "sync": None}, explicit_path
    project_id = slugify(project)
    matches: list[tuple[str, dict[str, Any]]] = []
    for name, entry in config["vaults"].items():
        for note in _read_folder(entry["path"] / "codex-context", "projects"):
            if str(note["metadata"].get("id")) == project_id:
                matches.append((name, entry))
    if len(matches) > 1:
        names = ", ".join(name for name, _ in matches)
        raise AmbiguousProjectError(f"project {project_id!r} exists in multiple vaults: {names}")
    if matches:
        entry = matches[0][1]
        return entry, entry["path"]
    if len(config["vaults"]) == 1:
        entry = next(iter(config["vaults"].values()))
        return entry, entry["path"]
    raise ProjectNotFoundError(f"no vault contains project {project_id!r}; pass --vault")


def _write_target(
    vault_arg: str | None, vault_name_arg: str | None, project: str
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if vault_arg and vault_name_arg:
        raise ValueError("pass --vault or --vault-name, not both")
    try:
        config = load_config()
    except ContextVaultError:
        if vault_arg:
            path = Path(vault_arg).expanduser().resolve()
            return {"path": path, "sync": None}, path, {"identity": None, "vaults": {}}
        raise
    if vault_name_arg:
        entry = _vault_by_name(config, vault_name_arg)
        return entry, entry["path"], config
    entry, path = resolve_write_vault(config, project, vault_arg)
    return entry, path, config


def _attribution(
    config: dict[str, Any], vault_entry: dict[str, Any], agent: str | None
) -> dict[str, str]:
    """Client-asserted attribution for synced vaults; never proof of identity."""
    if not vault_entry.get("sync"):
        return {}
    identity = config.get("identity")
    if not identity:
        raise ContextVaultError(
            "synced vaults require an identity; run "
            "`context_vault.py configure --vault <path> --identity <name>`"
        )
    return {
        "author": f"[[@{slugify(identity)}]]",
        "agent": agent or os.environ.get("CONTEXT_VAULT_AGENT", "unknown"),
    }


def ensure_person_note(vault: Path, identity: str, role: str | None = None) -> Path:
    slug = slugify(identity)
    path = vault / "codex-context" / "people" / f"@{slug}.md"
    if path.exists():
        return path
    metadata: dict[str, object] = {
        "id": f"@{slug}",
        "type": "person",
        "name": identity,
        "role": role,
        "recorded_at": _timestamp(),
    }
    return _write_markdown(path, metadata, f"# @{slug}\n")


GIT_TIMEOUT_SECONDS = 15


def _run_git(vault_path: Path, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(vault_path), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=list(args), returncode=124, stdout="", stderr=f"timed out after {timeout}s"
        )


@contextlib.contextmanager
def _vault_lock(vault_path: Path):
    """Serialize every git-touching operation on one clone across processes."""
    lock_path = vault_path / ".git" / "context-vault.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def sync_status(vault_path: Path) -> dict[str, Any]:
    unpushed = 0
    result = _run_git(vault_path, "rev-list", "--count", "@{u}..HEAD")
    if result.returncode == 0:
        unpushed = int(result.stdout.strip() or "0")
    fetch_head = vault_path / ".git" / "FETCH_HEAD"
    last_synced = None
    if fetch_head.exists():
        last_synced = datetime.fromtimestamp(fetch_head.stat().st_mtime, timezone.utc).isoformat()
    return {"unpushed": unpushed, "last_synced": last_synced}


def sync_read(vault_path: Path) -> dict[str, Any]:
    """Fetch and fast-forward only. Reads never rebase and never mutate local commits."""
    with _vault_lock(vault_path):
        fetch = _run_git(vault_path, "fetch", "origin", timeout=GIT_TIMEOUT_SECONDS)
        online = fetch.returncode == 0
        if online:
            dirty = _run_git(vault_path, "status", "--porcelain").stdout.strip()
            if not dirty and sync_status(vault_path)["unpushed"] == 0:
                _run_git(vault_path, "merge", "--ff-only", "@{u}")
        status = sync_status(vault_path)
        status["online"] = online
        return status


def _commit_quarantine(vault_path: Path) -> bool:
    status = _run_git(vault_path, "status", "--porcelain", "codex-context/conflicts")
    if not status.stdout.strip():
        return False
    _run_git(vault_path, "add", "codex-context/conflicts")
    _run_git(vault_path, "commit", "-m", "quarantine: preserve diverged record versions")
    _run_git(vault_path, "push", timeout=GIT_TIMEOUT_SECONDS)
    return True


def sync_push(
    vault_path: Path, paths: list[Path], message: str, retries: int = 3
) -> dict[str, Any]:
    with _vault_lock(vault_path):
        for path in paths:
            _run_git(vault_path, "add", str(path))
        _run_git(vault_path, "commit", "-m", message)
        pushed = False
        for _ in range(retries):
            if _run_git(vault_path, "push", timeout=GIT_TIMEOUT_SECONDS).returncode == 0:
                pushed = True
                break
            pull = _run_git(vault_path, "pull", "--rebase", timeout=GIT_TIMEOUT_SECONDS)
            if pull.returncode != 0:
                # Never leave the vault mid-rebase; local commits stay safe.
                _run_git(vault_path, "rebase", "--abort")
                break
        _commit_quarantine(vault_path)
        return {"pushed": pushed, **sync_status(vault_path)}


RECORD_FOLDERS = ("facts", "decisions", "sessions")


def _hunks(base: list[str], other: list[str]) -> list[tuple[int, int, list[str]]]:
    """Non-equal opcodes as (base_start, base_end, replacement_lines)."""
    matcher = difflib.SequenceMatcher(a=base, b=other, autojunk=False)
    return [
        (i1, i2, other[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def three_way_merge(base: str, ours: str, theirs: str) -> tuple[str, bool, int | None]:
    """Line-based three-way merge in the diff-match-patch spirit.

    Non-overlapping edits combine cleanly; identical edits deduplicate.
    Overlapping edits keep both sides' lines (never silently drop either) and
    report clean=False plus the first conflicted base line index.
    """
    base_lines = base.splitlines(keepends=True)
    ours_hunks = _hunks(base_lines, ours.splitlines(keepends=True))
    theirs_hunks = _hunks(base_lines, theirs.splitlines(keepends=True))
    merged: list[str] = []
    clean = True
    first_conflict: int | None = None
    cursor = 0
    while ours_hunks or theirs_hunks:
        if ours_hunks and (not theirs_hunks or ours_hunks[0][0] <= theirs_hunks[0][0]):
            start, end, lines = ours_hunks.pop(0)
        else:
            start, end, lines = theirs_hunks.pop(0)
        group = [(start, end, lines)]
        changed = True
        while changed:
            changed = False
            for hunks in (ours_hunks, theirs_hunks):
                while hunks and hunks[0][0] < end:
                    overlapping = hunks.pop(0)
                    end = max(end, overlapping[1])
                    group.append(overlapping)
                    changed = True
        merged.extend(base_lines[cursor:start])
        if len(group) == 1 or all(hunk == group[0] for hunk in group[1:]):
            merged.extend(group[0][2])
        else:
            clean = False
            if first_conflict is None:
                first_conflict = start
            for _, _, hunk_lines in group:
                merged.extend(hunk_lines)
        cursor = end
    merged.extend(base_lines[cursor:])
    return "".join(merged), clean, first_conflict


def _frontmatter_end_line(text: str) -> int | None:
    if not text.startswith("---\n"):
        return None
    for index, line in enumerate(text.splitlines()[1:], start=1):
        if line == "---":
            return index
    return None


def stamp_merge_status(text: str, status: str) -> str:
    stamp = f"merge_status: {json.dumps(status)}"
    if not text.startswith("---\n"):
        return f"---\n{stamp}\n---\n\n{text}"
    frontmatter, separator, rest = text[4:].partition("\n---\n")
    if not separator:
        return text
    kept = [line for line in frontmatter.splitlines() if not line.startswith("merge_status:")]
    return "---\n" + "\n".join([*kept, stamp]) + "\n---\n" + rest


def run_merge_driver(base: str, ours: str, theirs: str, repo_relative_path: str) -> int:
    """Git merge driver. Runs with cwd at the vault repo root; writes result to `ours`."""
    base_text = Path(base).read_text(encoding="utf-8")
    ours_text = Path(ours).read_text(encoding="utf-8")
    theirs_text = Path(theirs).read_text(encoding="utf-8")
    relative = Path(repo_relative_path)
    if any(folder in relative.parts for folder in RECORD_FOLDERS):
        # Records are immutable: keep ours byte-for-byte, quarantine theirs byte-for-byte.
        quarantine = Path("codex-context") / "conflicts" / f"{relative.stem}.theirs.md"
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        quarantine.write_text(theirs_text, encoding="utf-8")
        Path(ours).write_text(ours_text, encoding="utf-8")
        return 0
    merged, clean, first_conflict = three_way_merge(base_text, ours_text, theirs_text)
    if clean:
        Path(ours).write_text(merged, encoding="utf-8")
        return 0
    frontmatter_end = _frontmatter_end_line(base_text)
    if frontmatter_end is not None and first_conflict is not None and first_conflict < frontmatter_end:
        # Conflicting metadata is never blended; keep ours and ask a human.
        Path(ours).write_text(stamp_merge_status(ours_text, "needs-human"), encoding="utf-8")
        return 0
    Path(ours).write_text(stamp_merge_status(merged, "auto-merged"), encoding="utf-8")
    return 0


def _note_summary(note: dict[str, Any]) -> dict[str, Any]:
    summary = dict(note["metadata"])
    summary["source"] = str(note["path"])
    return summary


def _active_decisions(notes_root: Path, project_id: str) -> list[dict[str, Any]]:
    decisions = [
        note
        for note in _read_folder(notes_root, "decisions")
        if note["metadata"].get("type") == "decision"
        and note["metadata"].get("project") == project_id
        and note["metadata"].get("status") == "active"
    ]
    superseded_ids = {
        str(note["metadata"]["supersedes"])
        for note in decisions
        if note["metadata"].get("supersedes")
    }
    return sorted(
        [note for note in decisions if note["path"].stem not in superseded_ids],
        key=lambda note: str(note["metadata"]["recorded_at"]),
        reverse=True,
    )


def _recent_sessions(notes_root: Path, project_id: str) -> list[dict[str, Any]]:
    sessions = [
        note
        for note in _read_folder(notes_root, "sessions")
        if note["metadata"].get("type") == "session"
        and note["metadata"].get("project") == project_id
    ]
    ordered = sorted(
        sessions,
        key=lambda note: str(note["metadata"]["recorded_at"]),
        reverse=True,
    )
    return ordered[:3]


def detect_disputes(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contradictory active facts: same subject and exclusive relation, >1 value."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fact in facts:
        if fact.get("cardinality") == "multi":
            continue
        key = (str(fact.get("subject")), str(fact.get("relation")))
        grouped.setdefault(key, []).append(fact)
    disputes = []
    for (subject, relation), group in sorted(grouped.items()):
        if len({str(fact.get("value")) for fact in group}) > 1:
            disputes.append({"subject": subject, "relation": relation, "facts": group})
    return disputes


def repair_chores(notes_root: Path) -> list[dict[str, Any]]:
    chores: list[dict[str, Any]] = []
    for folder in ("projects", "people", "facts", "decisions", "sessions"):
        for note in _read_folder(notes_root, folder):
            status = note["metadata"].get("merge_status")
            if status:
                chores.append({"path": str(note["path"]), "merge_status": status})
    conflicts_dir = notes_root / "conflicts"
    if conflicts_dir.is_dir():
        for path in sorted(conflicts_dir.glob("*.md")):
            chores.append({"path": str(path), "merge_status": "quarantined"})
    return chores


def build_brief(
    notes_root: Path,
    workspace: Path,
    valid_at: date | None = None,
    known_at: datetime | None = None,
    project_note: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_note = project_note or find_project(notes_root, workspace)
    project = project_note["metadata"]
    facts = [
        _note_summary(note)
        for note in resolve_facts(notes_root, valid_at or date.today(), known_at)
        if note["metadata"].get("project") == project["id"]
    ]
    decisions = [_note_summary(note) for note in _active_decisions(notes_root, project["id"])]
    sessions = [_note_summary(note) for note in _recent_sessions(notes_root, project["id"])]
    return {
        "project": project,
        "goal": project["goal"],
        "open_questions": project.get("open_questions", []),
        "current_facts": facts,
        "active_decisions": decisions,
        "recent_sessions": sessions,
        "disputes": detect_disputes(facts),
        "repair_chores": repair_chores(notes_root),
        "sources": [
            str(project_note["path"]),
            *(fact["source"] for fact in facts),
            *(decision["source"] for decision in decisions),
            *(session["source"] for session in sessions),
        ],
    }


def decision_provenance(
    notes_root: Path,
    workspace: Path,
    decision_selector: str,
    valid_at: date | None = None,
    known_at: datetime | None = None,
    project_note: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_note = project_note or find_project(notes_root, workspace)
    project_id = str(project_note["metadata"]["id"])
    matches = [
        note
        for note in _read_folder(notes_root, "decisions")
        if note["metadata"].get("project") == project_id
        and decision_selector
        in {str(note["metadata"].get("id")), str(note["metadata"].get("title"))}
    ]
    if len(matches) != 1:
        raise DecisionNotFoundError(f"expected one decision matching {decision_selector!r}")
    brief = build_brief(notes_root, workspace, valid_at, known_at, project_note)
    decision = _note_summary(matches[0])
    return {
        "project": brief["project"],
        "decision": decision,
        "current_facts": brief["current_facts"],
        "recent_sessions": brief["recent_sessions"],
        "sources": [decision["source"], *brief["sources"]],
    }


def propose_fact(
    project: str,
    subject: str,
    relation: str,
    value: str,
    valid_from: str,
    evidence: list[str],
    *,
    valid_to: str | None = None,
    supersedes: str | None = None,
    cardinality: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not all(value.strip() for value in (project, subject, relation, value, valid_from)):
        raise ValueError("project, subject, relation, value, and valid_from are required")
    if not evidence:
        raise ValueError("facts require at least one evidence item")
    if cardinality not in (None, "exclusive", "multi"):
        raise ValueError("cardinality must be 'exclusive' or 'multi'")
    valid_from_date = date.fromisoformat(valid_from)
    if valid_to is not None and date.fromisoformat(valid_to) <= valid_from_date:
        raise ValueError("valid_to must be later than valid_from")
    _assert_safe_strings(
        [project, subject, relation, value, valid_from, *( [valid_to] if valid_to else [] ), *evidence]
    )
    timestamp = recorded_at or _timestamp()
    note_id = slugify(
        f"fact-{project}-{subject}-{relation}-{valid_from}-{timestamp}-{_record_suffix()}"
    )
    payload: dict[str, object] = {
        "id": note_id,
        "type": "fact",
        "project": project,
        "subject": subject,
        "relation": relation,
        "value": value,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "recorded_at": timestamp,
        "status": "active",
        "evidence": evidence,
        "supersedes": supersedes,
    }
    if cardinality == "multi":
        payload["cardinality"] = "multi"
    return payload


def _write_record(
    vault: Path, folder: str, build_metadata, body: str, author: str | None, agent: str | None
) -> Path:
    """Exclusively create a record, regenerating the random suffix on collision."""
    for _ in range(3):
        metadata = build_metadata()
        if author:
            metadata["author"] = author
            metadata["agent"] = agent or "unknown"
        try:
            return write_note(vault / "codex-context", folder, metadata, body, exclusive=True)
        except FileExistsError:
            continue
    raise ContextVaultError(f"could not create a unique {folder} record file")


def record_fact(
    vault: Path,
    project: str,
    subject: str,
    relation: str,
    value: str,
    valid_from: str,
    evidence: list[str],
    confirm: bool,
    *,
    valid_to: str | None = None,
    supersedes: str | None = None,
    cardinality: str | None = None,
    recorded_at: str | None = None,
    author: str | None = None,
    agent: str | None = None,
) -> Path:
    _require_confirmation(confirm)
    return _write_record(
        vault,
        "facts",
        lambda: propose_fact(
            project,
            subject,
            relation,
            value,
            valid_from,
            evidence,
            valid_to=valid_to,
            supersedes=supersedes,
            cardinality=cardinality,
            recorded_at=recorded_at,
        ),
        f"{subject} {relation} {value}.\n\nProject: [[{slugify(project)}]]\n",
        author,
        agent,
    )


def _parse_recorded_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def resolve_facts(
    notes_root: Path,
    valid_at: date,
    known_at: datetime | None = None,
) -> list[dict[str, Any]]:
    candidates = []
    for note in _read_folder(notes_root, "facts"):
        metadata = note["metadata"]
        if metadata.get("type") != "fact":
            continue
        if date.fromisoformat(str(metadata["valid_from"])) > valid_at:
            continue
        valid_to = metadata.get("valid_to")
        if valid_to and date.fromisoformat(str(valid_to)) <= valid_at:
            continue
        if known_at is not None and _parse_recorded_at(str(metadata["recorded_at"])) > known_at:
            continue
        candidates.append(note)

    active: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = candidate["path"].stem
        is_superseded = any(
            successor["metadata"].get("supersedes") == candidate_id
            and date.fromisoformat(str(successor["metadata"]["valid_from"])) <= valid_at
            and (
                known_at is None
                or _parse_recorded_at(str(successor["metadata"]["recorded_at"])) <= known_at
            )
            for successor in candidates
        )
        if not is_superseded:
            active.append(candidate)
    return sorted(
        active,
        key=lambda note: (
            str(note["metadata"]["valid_from"]),
            str(note["metadata"]["recorded_at"]),
        ),
        reverse=True,
    )


def propose_decision(
    project: str,
    title: str,
    choice: str,
    alternatives: list[str],
    rationale: str,
    evidence: list[str],
    *,
    status: str = "active",
    supersedes: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not all(value.strip() for value in (project, title, choice, rationale)):
        raise ValueError("project, title, choice, and rationale are required")
    if not evidence:
        raise ValueError("decisions require at least one evidence item")
    _assert_safe_strings(
        [project, title, choice, rationale, status, *(alternatives or []), *evidence]
    )
    timestamp = recorded_at or _timestamp()
    return {
        "id": slugify(f"decision-{project}-{title}-{timestamp}-{_record_suffix()}"),
        "type": "decision",
        "project": project,
        "title": title,
        "choice": choice,
        "alternatives": alternatives,
        "rationale": rationale,
        "status": status,
        "recorded_at": timestamp,
        "evidence": evidence,
        "supersedes": supersedes,
    }


def record_decision(
    vault: Path,
    project: str,
    title: str,
    choice: str,
    alternatives: list[str],
    rationale: str,
    evidence: list[str],
    *,
    confirm: bool,
    status: str = "active",
    supersedes: str | None = None,
    recorded_at: str | None = None,
    author: str | None = None,
    agent: str | None = None,
) -> Path:
    _require_confirmation(confirm)
    return _write_record(
        vault,
        "decisions",
        lambda: propose_decision(
            project,
            title,
            choice,
            alternatives,
            rationale,
            evidence,
            status=status,
            supersedes=supersedes,
            recorded_at=recorded_at,
        ),
        f"# {title}\n\n{rationale}\n\nProject: [[{slugify(project)}]]\n",
        author,
        agent,
    )


def propose_session(
    project: str,
    completed: list[str],
    blockers: list[str],
    next_step: str,
    evidence: list[str],
    *,
    branch: str | None = None,
    pr: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not project.strip() or not next_step.strip():
        raise ValueError("project and next_step are required")
    if not evidence:
        raise ValueError("session recaps require at least one evidence item")
    _assert_safe_strings(
        [
            project,
            next_step,
            *completed,
            *blockers,
            *evidence,
            *([branch] if branch else []),
            *([pr] if pr else []),
        ]
    )
    timestamp = recorded_at or _timestamp()
    payload: dict[str, object] = {
        "id": slugify(f"session-{project}-{timestamp}-{_record_suffix()}"),
        "type": "session",
        "project": project,
        "completed": completed,
        "blockers": blockers,
        "next_step": next_step,
        "recorded_at": timestamp,
        "evidence": evidence,
    }
    # branch/pr are recorded metadata only; merge state is never determined here.
    if branch:
        payload["branch"] = branch
    if pr:
        payload["pr"] = pr
    return payload


def record_session(
    vault: Path,
    project: str,
    completed: list[str],
    blockers: list[str],
    next_step: str,
    evidence: list[str],
    *,
    confirm: bool,
    branch: str | None = None,
    pr: str | None = None,
    recorded_at: str | None = None,
    author: str | None = None,
    agent: str | None = None,
) -> Path:
    _require_confirmation(confirm)
    lines = ["# Session recap", "", "## Completed", *completed, "", "## Blockers", *blockers]
    lines.extend(["", "## Next step", next_step, "", f"Project: [[{slugify(project)}]]", ""])
    return _write_record(
        vault,
        "sessions",
        lambda: propose_session(
            project,
            completed,
            blockers,
            next_step,
            evidence,
            branch=branch,
            pr=pr,
            recorded_at=recorded_at,
        ),
        "\n".join(lines),
        author,
        agent,
    )


VALIDATE_WORKFLOW = """\
name: Validate Context Vault
on:
  push:
    branches: [main]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Validate vault
        run: |
          RANGE=""
          if [ "${{ github.event.before }}" != "0000000000000000000000000000000000000000" ]; then
            RANGE="--append-only-range ${{ github.event.before }}..${{ github.sha }}"
          fi
          python3 scripts/validate_vault.py --require-author --max-mark-age-days 3 $RANGE
"""

GITATTRIBUTES_LINE = "*.md merge=context-vault"

GITIGNORE_CONTENT = """\
.obsidian/workspace*
.obsidian/cache/
.DS_Store
"""


def register_merge_driver(vault_path: Path) -> None:
    script = str(Path(__file__).resolve())
    _run_git(vault_path, "config", "merge.context-vault.name", "Context Vault note merge")
    _run_git(
        vault_path,
        "config",
        "merge.context-vault.driver",
        f'python3 "{script}" merge-driver %O %A %B %P',
    )


def init_team(
    repo: str,
    name: str = "team",
    path: Path | None = None,
    config_home: Path | None = None,
) -> dict[str, Any]:
    config = load_config(config_home)
    identity = config.get("identity")
    if not identity:
        raise ContextVaultError(
            "init-team requires an identity; run "
            "`context_vault.py configure --vault <path> --identity <name>` first"
        )
    synced = [
        vault_name
        for vault_name, entry in config["vaults"].items()
        if entry.get("sync") == "git" and vault_name != name
    ]
    if len(synced) >= 2:
        raise ContextVaultError(
            "at most two team vaults are supported; already configured: " + ", ".join(synced)
        )
    target = (path or Path.home() / "Documents" / f"{name}-context").expanduser().resolve()
    if not target.exists():
        clone = subprocess.run(
            ["git", "clone", repo, str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if clone.returncode != 0:
            raise ContextVaultError(f"git clone failed: {clone.stderr.strip()}")
    notes_root = target / "codex-context"
    for folder in VAULT_FOLDERS:
        (notes_root / folder).mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    gitattributes = target / ".gitattributes"
    if not gitattributes.exists():
        gitattributes.write_text(GITATTRIBUTES_LINE + "\n", encoding="utf-8")
        created.append(gitattributes)
    gitignore = target / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_CONTENT, encoding="utf-8")
        created.append(gitignore)
    validator = target / "scripts" / "validate_vault.py"
    if not validator.exists():
        validator.parent.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).resolve().parent / "validate_vault.py"
        validator.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(validator)
    workflow = target / ".github" / "workflows" / "context-vault-validate.yml"
    if not workflow.exists():
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(VALIDATE_WORKFLOW, encoding="utf-8")
        created.append(workflow)
    person = target / "codex-context" / "people" / f"@{slugify(identity)}.md"
    if not person.exists():
        created.append(ensure_person_note(target, identity))
    register_merge_driver(target)
    push_info = sync_push(target, created, f"init-team: bootstrap by @{slugify(identity)}")
    config["vaults"][name] = {"path": target, "sync": "git"}
    save_config(config, config_home)
    return {
        "vault": str(target),
        "name": name,
        "created": [str(item) for item in created],
        "sync": push_info,
    }


def doctor(config_home: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        config = load_config(config_home)
    except ContextVaultError as exc:
        return {"ok": False, "checks": [{"check": "config", "ok": False, "detail": str(exc)}]}
    checks.append(
        {
            "check": "identity",
            "ok": bool(config.get("identity")),
            "detail": config.get("identity") or "not set",
        }
    )
    for name, entry in config["vaults"].items():
        if entry.get("sync") != "git":
            continue
        path = entry["path"]
        driver = _run_git(path, "config", "--get", "merge.context-vault.driver")
        checks.append(
            {
                "check": f"{name}: merge driver",
                "ok": driver.returncode == 0,
                "detail": driver.stdout.strip() or "not registered",
            }
        )
        remote = _run_git(path, "ls-remote", "--heads", "origin", timeout=GIT_TIMEOUT_SECONDS)
        checks.append(
            {
                "check": f"{name}: remote reachable",
                "ok": remote.returncode == 0,
                "detail": "ok" if remote.returncode == 0 else (remote.stderr.strip()[:200] or "unreachable"),
            }
        )
        rebase_residue = (path / ".git" / "rebase-merge").exists() or (
            path / ".git" / "rebase-apply"
        ).exists()
        checks.append(
            {
                "check": f"{name}: no rebase in progress",
                "ok": not rebase_residue,
                "detail": "clean" if not rebase_residue else "residual rebase state; run `git -C <vault> rebase --abort`",
            }
        )
        status = sync_status(path)
        checks.append(
            {
                "check": f"{name}: unpushed records",
                "ok": status["unpushed"] == 0,
                "detail": str(status["unpushed"]),
            }
        )
        marks = repair_chores(path / "codex-context")
        checks.append(
            {"check": f"{name}: merge_status marks", "ok": not marks, "detail": str(len(marks))}
        )
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def sync_vault(path: Path) -> dict[str, Any]:
    info = sync_read(path)
    if info.get("unpushed"):
        # Reuse the locked push path: bounded rebase-retry, quarantine commit.
        info.update(sync_push(path, [], "sync: push pending records"))
    return info


def _emit(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a local Context Vault")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser("configure", help="configure a canonical vault")
    configure_parser.add_argument("--vault", required=True)
    configure_parser.add_argument("--identity")

    project_parser = subparsers.add_parser("project", help="register a project workspace")
    project_parser.add_argument("--vault")
    project_parser.add_argument("--vault-name")
    project_parser.add_argument("--name", required=True)
    project_parser.add_argument("--workspace", action="append", required=True)
    project_parser.add_argument("--workspace-repo", action="append", default=[])
    project_parser.add_argument("--goal", required=True)
    project_parser.add_argument("--open-question", action="append", default=[])
    project_parser.add_argument("--confirm", action="store_true")

    brief_parser = subparsers.add_parser("brief", help="retrieve a project startup brief")
    brief_parser.add_argument("--vault")
    brief_parser.add_argument("--vault-name")
    brief_parser.add_argument("--workspace", required=True)
    brief_parser.add_argument("--valid-at")
    brief_parser.add_argument("--known-at")

    proposal = subparsers.add_parser("propose-fact", help="create a fact proposal without writing")
    proposal.add_argument("--vault")
    proposal.add_argument("--project", required=True)
    proposal.add_argument("--subject", required=True)
    proposal.add_argument("--relation", required=True)
    proposal.add_argument("--value", required=True)
    proposal.add_argument("--valid-from", required=True)
    proposal.add_argument("--valid-to")
    proposal.add_argument("--evidence", action="append", required=True)
    proposal.add_argument("--supersedes")

    proposal.add_argument("--cardinality", choices=("exclusive", "multi"))

    record_fact_parser = subparsers.add_parser("record-fact", help="write an approved fact")
    record_fact_parser.add_argument("--vault")
    record_fact_parser.add_argument("--vault-name")
    record_fact_parser.add_argument("--agent")
    record_fact_parser.add_argument("--project", required=True)
    record_fact_parser.add_argument("--subject", required=True)
    record_fact_parser.add_argument("--relation", required=True)
    record_fact_parser.add_argument("--value", required=True)
    record_fact_parser.add_argument("--valid-from", required=True)
    record_fact_parser.add_argument("--valid-to")
    record_fact_parser.add_argument("--evidence", action="append", required=True)
    record_fact_parser.add_argument("--supersedes")
    record_fact_parser.add_argument("--cardinality", choices=("exclusive", "multi"))
    record_fact_parser.add_argument("--confirm", action="store_true")

    decision_proposal_parser = subparsers.add_parser(
        "propose-decision", help="create a decision proposal without writing"
    )
    decision_proposal_parser.add_argument("--project", required=True)
    decision_proposal_parser.add_argument("--title", required=True)
    decision_proposal_parser.add_argument("--choice", required=True)
    decision_proposal_parser.add_argument("--alternative", action="append", default=[])
    decision_proposal_parser.add_argument("--rationale", required=True)
    decision_proposal_parser.add_argument("--evidence", action="append", required=True)
    decision_proposal_parser.add_argument("--status", default="active")
    decision_proposal_parser.add_argument("--supersedes")

    decision_parser = subparsers.add_parser("record-decision", help="write an approved decision")
    decision_parser.add_argument("--vault")
    decision_parser.add_argument("--vault-name")
    decision_parser.add_argument("--agent")
    decision_parser.add_argument("--project", required=True)
    decision_parser.add_argument("--title", required=True)
    decision_parser.add_argument("--choice", required=True)
    decision_parser.add_argument("--alternative", action="append", default=[])
    decision_parser.add_argument("--rationale", required=True)
    decision_parser.add_argument("--evidence", action="append", required=True)
    decision_parser.add_argument("--status", default="active")
    decision_parser.add_argument("--supersedes")
    decision_parser.add_argument("--confirm", action="store_true")

    session_proposal_parser = subparsers.add_parser(
        "propose-session", help="create a session proposal without writing"
    )
    session_proposal_parser.add_argument("--project", required=True)
    session_proposal_parser.add_argument("--completed", action="append", default=[])
    session_proposal_parser.add_argument("--blocker", action="append", default=[])
    session_proposal_parser.add_argument("--next-step", required=True)
    session_proposal_parser.add_argument("--evidence", action="append", required=True)
    session_proposal_parser.add_argument("--branch")
    session_proposal_parser.add_argument("--pr")

    session_parser = subparsers.add_parser("record-session", help="write an approved session recap")
    session_parser.add_argument("--vault")
    session_parser.add_argument("--vault-name")
    session_parser.add_argument("--agent")
    session_parser.add_argument("--project", required=True)
    session_parser.add_argument("--completed", action="append", default=[])
    session_parser.add_argument("--blocker", action="append", default=[])
    session_parser.add_argument("--next-step", required=True)
    session_parser.add_argument("--evidence", action="append", required=True)
    session_parser.add_argument("--branch")
    session_parser.add_argument("--pr")
    session_parser.add_argument("--confirm", action="store_true")

    query_parser = subparsers.add_parser("query", help="query current, historical, or decision context")
    query_parser.add_argument("--vault")
    query_parser.add_argument("--vault-name")
    query_parser.add_argument("--workspace", required=True)
    query_parser.add_argument("--mode", choices=("current", "historical", "provenance"), required=True)
    query_parser.add_argument("--valid-at")
    query_parser.add_argument("--known-at")
    query_parser.add_argument("--decision")

    merge_parser = subparsers.add_parser(
        "merge-driver", help="git merge driver for vault notes (%%O %%A %%B %%P)"
    )
    merge_parser.add_argument("base")
    merge_parser.add_argument("ours")
    merge_parser.add_argument("theirs")
    merge_parser.add_argument("path")

    init_team_parser = subparsers.add_parser("init-team", help="join a git-hosted team vault")
    init_team_parser.add_argument("--repo", required=True)
    init_team_parser.add_argument("--vault-name", default="team")
    init_team_parser.add_argument("--path")

    subparsers.add_parser("doctor", help="check team-vault health")

    sync_parser = subparsers.add_parser("sync", help="pull and push synced vaults")
    sync_parser.add_argument("--vault-name")

    vault_parser = subparsers.add_parser("vault", help="manage configured vaults")
    vault_parser.add_argument("action", choices=("list",))
    args = parser.parse_args(argv)

    try:
        if args.command == "configure":
            config_path = configure(Path(args.vault), identity=args.identity)
            _emit({"vault": str(Path(args.vault).expanduser().resolve()), "config": str(config_path)})
            return 0
        if args.command == "project":
            entry, vault_path, _config = _write_target(args.vault, args.vault_name, args.name)
            note = record_project(
                vault_path,
                args.name,
                args.workspace,
                args.goal,
                args.open_question,
                args.confirm,
                workspace_repos=args.workspace_repo,
            )
            result: dict[str, Any] = {"path": str(note)}
            if entry.get("sync") == "git":
                result["sync"] = sync_push(vault_path, [note], f"record project: {args.name}")
            _emit(result)
            return 0
        if args.command in ("brief", "query"):
            valid_at = date.fromisoformat(args.valid_at) if args.valid_at else None
            known_at = _parse_recorded_at(args.known_at) if args.known_at else None
            if args.command == "query":
                if args.mode == "historical" and valid_at is None:
                    raise ValueError("historical queries require --valid-at")
                if args.mode == "provenance" and not args.decision:
                    raise ValueError("provenance queries require --decision")
            if args.vault and args.vault_name:
                raise ValueError("pass --vault or --vault-name, not both")
            sync_map: dict[str, Any] = {}
            project_note: dict[str, Any] | None = None
            if args.vault:
                notes_root = Path(args.vault).expanduser().resolve() / "codex-context"
            else:
                config = load_config()
                if args.vault_name:
                    entry = _vault_by_name(config, args.vault_name)
                    if entry.get("sync") == "git":
                        sync_map[args.vault_name] = sync_read(entry["path"])
                    notes_root = entry["path"] / "codex-context"
                else:
                    for name, entry in config["vaults"].items():
                        if entry.get("sync") == "git":
                            # Failure-isolated: an offline vault serves local notes.
                            sync_map[name] = sync_read(entry["path"])
                    vault_name, project_note = find_project_across_vaults(
                        config, Path(args.workspace)
                    )
                    notes_root = config["vaults"][vault_name]["path"] / "codex-context"
            if args.command == "query" and args.mode == "provenance":
                payload = decision_provenance(
                    notes_root,
                    Path(args.workspace),
                    args.decision,
                    valid_at,
                    known_at,
                    project_note,
                )
            else:
                payload = build_brief(
                    notes_root, Path(args.workspace), valid_at, known_at, project_note
                )
            if sync_map:
                payload["sync"] = sync_map
            _emit(payload)
            return 0
        if args.command == "propose-fact":
            _emit(
                propose_fact(
                    args.project,
                    args.subject,
                    args.relation,
                    args.value,
                    args.valid_from,
                    args.evidence,
                    valid_to=args.valid_to,
                    supersedes=args.supersedes,
                    cardinality=args.cardinality,
                )
            )
            return 0
        if args.command == "record-fact":
            entry, vault_path, config = _write_target(args.vault, args.vault_name, args.project)
            stamp = _attribution(config, entry, args.agent)
            note = record_fact(
                vault_path,
                args.project,
                args.subject,
                args.relation,
                args.value,
                args.valid_from,
                args.evidence,
                args.confirm,
                valid_to=args.valid_to,
                supersedes=args.supersedes,
                cardinality=args.cardinality,
                author=stamp.get("author"),
                agent=stamp.get("agent"),
            )
            result = {"path": str(note)}
            if entry.get("sync") == "git":
                result["sync"] = sync_push(
                    vault_path, [note], f"record fact: {args.subject} {args.relation}"
                )
            _emit(result)
            return 0
        if args.command == "propose-decision":
            _emit(
                propose_decision(
                    args.project,
                    args.title,
                    args.choice,
                    args.alternative,
                    args.rationale,
                    args.evidence,
                    status=args.status,
                    supersedes=args.supersedes,
                )
            )
            return 0
        if args.command == "record-decision":
            entry, vault_path, config = _write_target(args.vault, args.vault_name, args.project)
            stamp = _attribution(config, entry, args.agent)
            note = record_decision(
                vault_path,
                args.project,
                args.title,
                args.choice,
                args.alternative,
                args.rationale,
                args.evidence,
                confirm=args.confirm,
                status=args.status,
                supersedes=args.supersedes,
                author=stamp.get("author"),
                agent=stamp.get("agent"),
            )
            result = {"path": str(note)}
            if entry.get("sync") == "git":
                result["sync"] = sync_push(vault_path, [note], f"record decision: {args.title}")
            _emit(result)
            return 0
        if args.command == "propose-session":
            _emit(
                propose_session(
                    args.project,
                    args.completed,
                    args.blocker,
                    args.next_step,
                    args.evidence,
                    branch=args.branch,
                    pr=args.pr,
                )
            )
            return 0
        if args.command == "record-session":
            entry, vault_path, config = _write_target(args.vault, args.vault_name, args.project)
            stamp = _attribution(config, entry, args.agent)
            note = record_session(
                vault_path,
                args.project,
                args.completed,
                args.blocker,
                args.next_step,
                args.evidence,
                confirm=args.confirm,
                branch=args.branch,
                pr=args.pr,
                author=stamp.get("author"),
                agent=stamp.get("agent"),
            )
            result = {"path": str(note)}
            if entry.get("sync") == "git":
                result["sync"] = sync_push(vault_path, [note], f"record session: {args.project}")
            _emit(result)
            return 0
        if args.command == "merge-driver":
            return run_merge_driver(args.base, args.ours, args.theirs, args.path)
        if args.command == "init-team":
            _emit(
                init_team(
                    args.repo,
                    name=args.vault_name,
                    path=Path(args.path) if args.path else None,
                )
            )
            return 0
        if args.command == "doctor":
            report = doctor()
            _emit(report)
            return 0 if report["ok"] else 1
        if args.command == "sync":
            config = load_config()
            results = {}
            for name, entry in config["vaults"].items():
                if entry.get("sync") != "git":
                    continue
                if args.vault_name and name != args.vault_name:
                    continue
                results[name] = sync_vault(entry["path"])
            _emit(results)
            return 0
        if args.command == "vault":
            config = load_config()
            _emit(
                {
                    "identity": config.get("identity"),
                    "vaults": {
                        name: {"path": str(entry["path"]), "sync": entry.get("sync")}
                        for name, entry in config["vaults"].items()
                    },
                }
            )
            return 0
    except (ContextVaultError, ValueError) as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
