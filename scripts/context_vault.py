from __future__ import annotations

import argparse
import json
import re
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


def configure(vault: Path, config_home: Path | None = None) -> Path:
    vault = vault.expanduser().resolve()
    notes_root = vault / "codex-context"
    for folder in ("projects", "decisions", "facts", "sessions", "templates"):
        (notes_root / folder).mkdir(parents=True, exist_ok=True)

    config_dir = (config_home or _config_dir()).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    _write_json_atomic(config_path, {"vault_path": str(vault)})
    return config_path


def _config_dir() -> Path:
    return Path.home() / ".codex" / "context-vault"


def configured_vault() -> Path:
    config_path = _config_dir() / "config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        vault_path = payload["vault_path"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ContextVaultError(
            "no configured vault; run `context_vault.py configure --vault /path/to/vault`"
        ) from exc
    if not isinstance(vault_path, str) or not vault_path.strip():
        raise ContextVaultError("configured vault path is invalid")
    return Path(vault_path).expanduser().resolve()


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


def write_note(
    root: Path,
    folder: str,
    metadata: dict[str, object],
    body: str,
) -> Path:
    if not metadata.get("id"):
        raise ValueError("note metadata requires an id")
    for key in metadata:
        if not isinstance(key, str) or ":" in key or "\n" in key:
            raise ValueError("metadata keys must be single-line keys without colons")

    destination_dir = root / folder
    destination_dir.mkdir(parents=True, exist_ok=True)
    path = destination_dir / f"{slugify(str(metadata['id']))}.md"
    lines = ["---"]
    lines.extend(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    lines.extend(["---", "", body.rstrip("\n"), ""])
    content = "\n".join(lines)

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination_dir, delete=False
    ) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)
    return path


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
    recorded_at: str | None = None,
) -> Path:
    _require_confirmation(confirm)
    if not name.strip() or not goal.strip():
        raise ValueError("project name and goal are required")
    project_id = slugify(name)
    normalized_paths = [str(Path(path).expanduser().resolve()) for path in workspace_paths]
    metadata: dict[str, object] = {
        "id": project_id,
        "type": "project",
        "name": name.strip(),
        "workspace_paths": normalized_paths,
        "goal": goal.strip(),
        "open_questions": open_questions,
        "recorded_at": recorded_at or _timestamp(),
    }
    return write_note(vault / "codex-context", "projects", metadata, f"# {name.strip()}\n")


def find_project(notes_root: Path, workspace: Path) -> dict[str, Any]:
    expected_path = str(workspace.expanduser().resolve())
    matches = [
        note
        for note in _read_folder(notes_root, "projects")
        if expected_path in note["metadata"].get("workspace_paths", [])
    ]
    if not matches:
        raise ProjectNotFoundError(f"no Context Vault project matches {expected_path}")
    if len(matches) > 1:
        names = ", ".join(str(match["metadata"].get("name")) for match in matches)
        raise AmbiguousProjectError(f"workspace matches multiple projects: {names}")
    return matches[0]


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


def build_brief(
    notes_root: Path,
    workspace: Path,
    valid_at: date | None = None,
    known_at: datetime | None = None,
) -> dict[str, Any]:
    project_note = find_project(notes_root, workspace)
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
) -> dict[str, Any]:
    project_note = find_project(notes_root, workspace)
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
    brief = build_brief(notes_root, workspace, valid_at, known_at)
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
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not all(value.strip() for value in (project, subject, relation, value, valid_from)):
        raise ValueError("project, subject, relation, value, and valid_from are required")
    if not evidence:
        raise ValueError("facts require at least one evidence item")
    valid_from_date = date.fromisoformat(valid_from)
    if valid_to is not None and date.fromisoformat(valid_to) <= valid_from_date:
        raise ValueError("valid_to must be later than valid_from")
    _assert_safe_strings(
        [project, subject, relation, value, valid_from, *( [valid_to] if valid_to else [] ), *evidence]
    )
    timestamp = recorded_at or _timestamp()
    note_id = slugify(f"fact-{project}-{subject}-{relation}-{valid_from}-{timestamp}")
    return {
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
    recorded_at: str | None = None,
) -> Path:
    _require_confirmation(confirm)
    metadata = propose_fact(
        project,
        subject,
        relation,
        value,
        valid_from,
        evidence,
        valid_to=valid_to,
        supersedes=supersedes,
        recorded_at=recorded_at,
    )
    return write_note(
        vault / "codex-context",
        "facts",
        metadata,
        f"{subject} {relation} {value}.\n",
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
        "id": slugify(f"decision-{project}-{title}-{timestamp}"),
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
) -> Path:
    _require_confirmation(confirm)
    metadata = propose_decision(
        project,
        title,
        choice,
        alternatives,
        rationale,
        evidence,
        status=status,
        supersedes=supersedes,
        recorded_at=recorded_at,
    )
    return write_note(vault / "codex-context", "decisions", metadata, f"# {title}\n\n{rationale}\n")


def propose_session(
    project: str,
    completed: list[str],
    blockers: list[str],
    next_step: str,
    evidence: list[str],
    *,
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not project.strip() or not next_step.strip():
        raise ValueError("project and next_step are required")
    if not evidence:
        raise ValueError("session recaps require at least one evidence item")
    _assert_safe_strings([project, next_step, *completed, *blockers, *evidence])
    timestamp = recorded_at or _timestamp()
    return {
        "id": slugify(f"session-{project}-{timestamp}"),
        "type": "session",
        "project": project,
        "completed": completed,
        "blockers": blockers,
        "next_step": next_step,
        "recorded_at": timestamp,
        "evidence": evidence,
    }


def record_session(
    vault: Path,
    project: str,
    completed: list[str],
    blockers: list[str],
    next_step: str,
    evidence: list[str],
    *,
    confirm: bool,
    recorded_at: str | None = None,
) -> Path:
    _require_confirmation(confirm)
    metadata = propose_session(
        project,
        completed,
        blockers,
        next_step,
        evidence,
        recorded_at=recorded_at,
    )
    lines = ["# Session recap", "", "## Completed", *completed, "", "## Blockers", *blockers]
    lines.extend(["", "## Next step", next_step, ""])
    return write_note(vault / "codex-context", "sessions", metadata, "\n".join(lines))


def _emit(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a local Context Vault")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser("configure", help="configure a canonical vault")
    configure_parser.add_argument("--vault", required=True)

    project_parser = subparsers.add_parser("project", help="register a project workspace")
    project_parser.add_argument("--vault")
    project_parser.add_argument("--name", required=True)
    project_parser.add_argument("--workspace", action="append", required=True)
    project_parser.add_argument("--goal", required=True)
    project_parser.add_argument("--open-question", action="append", default=[])
    project_parser.add_argument("--confirm", action="store_true")

    brief_parser = subparsers.add_parser("brief", help="retrieve a project startup brief")
    brief_parser.add_argument("--vault")
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

    record_fact_parser = subparsers.add_parser("record-fact", help="write an approved fact")
    record_fact_parser.add_argument("--vault")
    record_fact_parser.add_argument("--project", required=True)
    record_fact_parser.add_argument("--subject", required=True)
    record_fact_parser.add_argument("--relation", required=True)
    record_fact_parser.add_argument("--value", required=True)
    record_fact_parser.add_argument("--valid-from", required=True)
    record_fact_parser.add_argument("--valid-to")
    record_fact_parser.add_argument("--evidence", action="append", required=True)
    record_fact_parser.add_argument("--supersedes")
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

    session_parser = subparsers.add_parser("record-session", help="write an approved session recap")
    session_parser.add_argument("--vault")
    session_parser.add_argument("--project", required=True)
    session_parser.add_argument("--completed", action="append", default=[])
    session_parser.add_argument("--blocker", action="append", default=[])
    session_parser.add_argument("--next-step", required=True)
    session_parser.add_argument("--evidence", action="append", required=True)
    session_parser.add_argument("--confirm", action="store_true")

    query_parser = subparsers.add_parser("query", help="query current, historical, or decision context")
    query_parser.add_argument("--vault")
    query_parser.add_argument("--workspace", required=True)
    query_parser.add_argument("--mode", choices=("current", "historical", "provenance"), required=True)
    query_parser.add_argument("--valid-at")
    query_parser.add_argument("--known-at")
    query_parser.add_argument("--decision")
    args = parser.parse_args(argv)

    try:
        if args.command == "configure":
            config_path = configure(Path(args.vault))
            _emit({"vault": str(Path(args.vault).expanduser().resolve()), "config": str(config_path)})
            return 0
        if args.command == "project":
            note = record_project(
                _vault_from_argument(args.vault),
                args.name,
                args.workspace,
                args.goal,
                args.open_question,
                args.confirm,
            )
            _emit({"path": str(note)})
            return 0
        if args.command == "brief":
            valid_at = date.fromisoformat(args.valid_at) if args.valid_at else None
            known_at = _parse_recorded_at(args.known_at) if args.known_at else None
            _emit(
                build_brief(
                    _vault_from_argument(args.vault) / "codex-context",
                    Path(args.workspace),
                    valid_at,
                    known_at,
                )
            )
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
                )
            )
            return 0
        if args.command == "record-fact":
            note = record_fact(
                _vault_from_argument(args.vault),
                args.project,
                args.subject,
                args.relation,
                args.value,
                args.valid_from,
                args.evidence,
                args.confirm,
                valid_to=args.valid_to,
                supersedes=args.supersedes,
            )
            _emit({"path": str(note)})
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
            note = record_decision(
                _vault_from_argument(args.vault),
                args.project,
                args.title,
                args.choice,
                args.alternative,
                args.rationale,
                args.evidence,
                confirm=args.confirm,
                status=args.status,
                supersedes=args.supersedes,
            )
            _emit({"path": str(note)})
            return 0
        if args.command == "propose-session":
            _emit(
                propose_session(
                    args.project,
                    args.completed,
                    args.blocker,
                    args.next_step,
                    args.evidence,
                )
            )
            return 0
        if args.command == "record-session":
            note = record_session(
                _vault_from_argument(args.vault),
                args.project,
                args.completed,
                args.blocker,
                args.next_step,
                args.evidence,
                confirm=args.confirm,
            )
            _emit({"path": str(note)})
            return 0
        if args.command == "query":
            valid_at = date.fromisoformat(args.valid_at) if args.valid_at else None
            known_at = _parse_recorded_at(args.known_at) if args.known_at else None
            notes_root = _vault_from_argument(args.vault) / "codex-context"
            if args.mode == "historical" and valid_at is None:
                raise ValueError("historical queries require --valid-at")
            if args.mode == "provenance":
                if not args.decision:
                    raise ValueError("provenance queries require --decision")
                _emit(
                    decision_provenance(
                        notes_root,
                        Path(args.workspace),
                        args.decision,
                        valid_at,
                        known_at,
                    )
                )
            else:
                _emit(build_brief(notes_root, Path(args.workspace), valid_at, known_at))
            return 0
    except (ContextVaultError, ValueError) as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
