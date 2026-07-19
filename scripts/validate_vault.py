#!/usr/bin/env python3
"""Standalone Context Vault team-vault validator.

Vendored into a team vault's scripts/ directory by `context_vault.py
init-team` so the vault repo can validate itself in CI without installing the
plugin. Keep this file dependency-free and self-contained; it must not import
context_vault.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECORD_FOLDERS = ("facts", "decisions", "sessions")

REQUIRED = {
    "fact": (
        "id",
        "type",
        "project",
        "subject",
        "relation",
        "value",
        "valid_from",
        "recorded_at",
        "evidence",
    ),
    "decision": (
        "id",
        "type",
        "project",
        "title",
        "choice",
        "rationale",
        "recorded_at",
        "evidence",
    ),
    "session": ("id", "type", "project", "next_step", "recorded_at", "evidence"),
    "withdrawal": ("id", "type", "withdraws", "reason", "recorded_at"),
}


def read_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    frontmatter, separator, _ = text[4:].partition("\n---\n")
    if not separator:
        raise ValueError("unterminated frontmatter")
    metadata: dict[str, object] = {}
    for line in frontmatter.splitlines():
        key, sep, raw = line.partition(": ")
        if not sep:
            raise ValueError(f"invalid frontmatter line: {line}")
        metadata[key] = json.loads(raw)
    return metadata


def last_commit_time(root: Path, path: Path) -> datetime | None:
    result = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%cI", "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    stamp = result.stdout.strip()
    if result.returncode != 0 or not stamp:
        return None
    return datetime.fromisoformat(stamp)


def append_only_violations(root: Path, diff_range: str) -> list[str]:
    """Modified or deleted record files in a push range are protocol violations."""
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-status", diff_range],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [f"append-only check failed: git diff {diff_range}: {result.stderr.strip()}"]
    problems = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        segments = Path(path).parts
        is_record = "codex-context" in segments and any(
            folder in segments for folder in RECORD_FOLDERS
        )
        if is_record and (status.startswith("M") or status.startswith("D")):
            if status.startswith("D"):
                # A deletion by a recognized `retract:` commit is the sanctioned
                # remove-from-current-tree path, not an append-only violation.
                subjects = subprocess.run(
                    ["git", "-C", str(root), "log", "--format=%s", diff_range, "--", path],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.splitlines()
                if any(subject.startswith("retract:") for subject in subjects):
                    continue
            problems.append(
                f"{path}: record was {'modified' if status.startswith('M') else 'deleted'} "
                "— records are append-only; correct with a superseding record or "
                "`withdraw` (HIGH SEVERITY)"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Context Vault team vault")
    parser.add_argument("--root", default=".")
    parser.add_argument("--require-author", action="store_true")
    parser.add_argument("--max-mark-age-days", type=int, default=None)
    parser.add_argument("--append-only-range")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    problems: list[str] = []
    notes_root = root / "codex-context"
    for path in sorted(notes_root.rglob("*.md")):
        if path.parent.name == "conflicts":
            continue
        try:
            metadata = read_frontmatter(path)
        except (ValueError, json.JSONDecodeError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        required = REQUIRED.get(str(metadata.get("type")))
        if required:
            missing = [key for key in required if key not in metadata]
            if missing:
                problems.append(f"{path}: missing {', '.join(missing)}")
            if args.require_author and "author" not in metadata:
                problems.append(f"{path}: record has no author stamp")
        mark = metadata.get("merge_status")
        if mark and args.max_mark_age_days is not None:
            committed = last_commit_time(root, path)
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.max_mark_age_days)
            if committed is None or committed < cutoff:
                problems.append(
                    f"{path}: merge_status {mark!r} older than {args.max_mark_age_days} days"
                )
    if args.max_mark_age_days is not None and (notes_root / "conflicts").is_dir():
        for path in sorted((notes_root / "conflicts").glob("*.md")):
            committed = last_commit_time(root, path)
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.max_mark_age_days)
            if committed is None or committed < cutoff:
                problems.append(
                    f"{path}: quarantined record older than {args.max_mark_age_days} days"
                )
    if args.append_only_range:
        problems.extend(append_only_violations(root, args.append_only_range))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
