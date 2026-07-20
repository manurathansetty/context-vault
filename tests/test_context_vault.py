from __future__ import annotations

import json
import sys
import tempfile
import unittest
import os
from datetime import date, datetime
from pathlib import Path
from subprocess import CompletedProcess, run


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import context_vault


class ContextVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.vault = Path(self.tempdir.name) / "vault"
        self.config_home = Path(self.tempdir.name) / "config"
        self.workspace = Path(self.tempdir.name) / "workspace"
        self.workspace.mkdir()
        self.cli_env = os.environ.copy()
        self.cli_env["HOME"] = self.tempdir.name
        # Keep configuration reads/writes inside the tempdir for both the new
        # $XDG_CONFIG_HOME location and the legacy ~/.codex fallback.
        self.cli_env["XDG_CONFIG_HOME"] = str(Path(self.tempdir.name) / "xdg")

    def run_cli(self, *args: str) -> CompletedProcess[str]:
        return run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "context_vault.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=self.cli_env,
        )

    def test_propose_fact_cli_does_not_write(self) -> None:
        result = self.run_cli(
            "propose-fact",
            "--vault",
            str(self.vault),
            "--project",
            "billing",
            "--subject",
            "[[Auth]]",
            "--relation",
            "owner",
            "--value",
            "[[Platform]]",
            "--valid-from",
            "2026-07-18",
            "--evidence",
            "PR #421",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"type": "fact"', result.stdout)
        self.assertFalse(list((self.vault / "codex-context" / "facts").glob("*.md")))

    def test_cli_configures_project_and_returns_brief(self) -> None:
        configured = self.run_cli("configure", "--vault", str(self.vault))
        self.assertEqual(configured.returncode, 0, configured.stderr)

        project = self.run_cli(
            "project",
            "--vault",
            str(self.vault),
            "--name",
            "Billing",
            "--workspace",
            str(self.workspace),
            "--goal",
            "Finish migration",
            "--confirm",
        )
        self.assertEqual(project.returncode, 0, project.stderr)

        brief = self.run_cli(
            "brief", "--vault", str(self.vault), "--workspace", str(self.workspace)
        )
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn('"goal": "Finish migration"', brief.stdout)

    def test_cli_uses_configured_vault_when_vault_flag_is_omitted(self) -> None:
        configured = self.run_cli("configure", "--vault", str(self.vault))
        self.assertEqual(configured.returncode, 0, configured.stderr)

        project = self.run_cli(
            "project",
            "--name",
            "Billing",
            "--workspace",
            str(self.workspace),
            "--goal",
            "Finish migration",
            "--confirm",
        )

        self.assertEqual(project.returncode, 0, project.stderr)

    def test_record_fact_cli_requires_confirm_before_writing(self) -> None:
        arguments = (
            "record-fact",
            "--vault",
            str(self.vault),
            "--project",
            "billing",
            "--subject",
            "[[Auth]]",
            "--relation",
            "owner",
            "--value",
            "[[Platform]]",
            "--valid-from",
            "2026-07-18",
            "--evidence",
            "PR #421",
        )
        rejected = self.run_cli(*arguments)
        self.assertNotEqual(rejected.returncode, 0)

        saved = self.run_cli(*arguments, "--confirm")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(len(list((self.vault / "codex-context" / "facts").glob("*.md"))), 1)

    def test_cli_records_decision_and_queries_provenance(self) -> None:
        self.run_cli(
            "project",
            "--vault",
            str(self.vault),
            "--name",
            "Billing",
            "--workspace",
            str(self.workspace),
            "--goal",
            "Finish migration",
            "--confirm",
        )
        recorded = self.run_cli(
            "record-decision",
            "--vault",
            str(self.vault),
            "--project",
            "billing",
            "--title",
            "Use Postgres",
            "--choice",
            "Postgres",
            "--alternative",
            "DynamoDB",
            "--rationale",
            "Need relational transactions.",
            "--evidence",
            "ADR-001",
            "--confirm",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)

        queried = self.run_cli(
            "query",
            "--vault",
            str(self.vault),
            "--workspace",
            str(self.workspace),
            "--mode",
            "provenance",
            "--decision",
            "Use Postgres",
        )
        self.assertEqual(queried.returncode, 0, queried.stderr)
        self.assertIn('"choice": "Postgres"', queried.stdout)

    def test_propose_decision_cli_does_not_write(self) -> None:
        proposed = self.run_cli(
            "propose-decision",
            "--project",
            "billing",
            "--title",
            "Use Postgres",
            "--choice",
            "Postgres",
            "--alternative",
            "DynamoDB",
            "--rationale",
            "Need relational transactions.",
            "--evidence",
            "ADR-001",
        )

        self.assertEqual(proposed.returncode, 0, proposed.stderr)
        self.assertIn('"type": "decision"', proposed.stdout)
        self.assertFalse(list((self.vault / "codex-context" / "decisions").glob("*.md")))

    def test_propose_session_cli_does_not_write(self) -> None:
        proposed = self.run_cli(
            "propose-session",
            "--project",
            "billing",
            "--completed",
            "Added context support",
            "--next-step",
            "Install plugin",
            "--evidence",
            "session-123",
        )

        self.assertEqual(proposed.returncode, 0, proposed.stderr)
        self.assertIn('"type": "session"', proposed.stdout)
        self.assertFalse(list((self.vault / "codex-context" / "sessions").glob("*.md")))

    def test_cli_records_session_recap(self) -> None:
        recorded = self.run_cli(
            "record-session",
            "--vault",
            str(self.vault),
            "--project",
            "billing",
            "--completed",
            "Added context support",
            "--blocker",
            "Need a new thread",
            "--next-step",
            "Install plugin",
            "--evidence",
            "session-123",
            "--confirm",
        )

        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(len(list((self.vault / "codex-context" / "sessions").glob("*.md"))), 1)

    def test_brief_matches_project_by_workspace_path(self) -> None:
        context_vault.record_project(
            self.vault,
            "Billing",
            [str(self.workspace)],
            "Finish migration",
            ["Confirm rollout"],
            confirm=True,
        )

        brief = context_vault.build_brief(
            self.vault / "codex-context", self.workspace
        )

        self.assertEqual(brief["project"]["id"], "billing")
        self.assertEqual(brief["goal"], "Finish migration")
        self.assertEqual(brief["open_questions"], ["Confirm rollout"])

    def test_brief_rejects_ambiguous_workspace_mapping(self) -> None:
        context_vault.record_project(
            self.vault, "One", [str(self.workspace)], "A", [], confirm=True
        )
        context_vault.record_project(
            self.vault, "Two", [str(self.workspace)], "B", [], confirm=True
        )

        with self.assertRaises(context_vault.AmbiguousProjectError):
            context_vault.build_brief(self.vault / "codex-context", self.workspace)

    def test_record_fact_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(context_vault.ConfirmationRequiredError):
            context_vault.record_fact(
                self.vault,
                "billing",
                "[[Auth service]]",
                "owner",
                "[[Platform]]",
                "2026-07-18",
                ["PR #421"],
                confirm=False,
            )

    def test_temporal_resolution_distinguishes_valid_and_recorded_time(self) -> None:
        old = context_vault.record_fact(
            self.vault,
            "billing",
            "[[User]]",
            "located_in",
            "[[New York]]",
            "2022-01-01",
            ["chat-1"],
            True,
            recorded_at="2022-01-02T00:00:00+00:00",
        )
        context_vault.record_fact(
            self.vault,
            "billing",
            "[[User]]",
            "located_in",
            "[[London]]",
            "2024-10-01",
            ["chat-2"],
            True,
            supersedes=old.stem,
            recorded_at="2025-03-01T00:00:00+00:00",
        )

        root = self.vault / "codex-context"

        current_at_december = context_vault.resolve_facts(root, date(2024, 12, 1))
        known_in_december = context_vault.resolve_facts(
            root,
            date(2024, 12, 1),
            datetime.fromisoformat("2024-12-01T23:59:00+00:00"),
        )

        self.assertEqual(current_at_december[0]["metadata"]["value"], "[[London]]")
        self.assertEqual(known_in_december[0]["metadata"]["value"], "[[New York]]")

    def test_fact_valid_to_ends_a_fact_without_rewriting_it(self) -> None:
        context_vault.record_fact(
            self.vault,
            "billing",
            "[[Feature flag]]",
            "enabled",
            "true",
            "2026-01-01",
            ["PR #100"],
            True,
            valid_to="2026-02-01",
        )

        facts = context_vault.resolve_facts(
            self.vault / "codex-context", date(2026, 2, 1)
        )

        self.assertEqual(facts, [])

    def test_propose_fact_rejects_secret_like_evidence(self) -> None:
        with self.assertRaises(context_vault.SensitiveContentError):
            context_vault.propose_fact(
                "billing",
                "[[Auth service]]",
                "owner",
                "[[Platform]]",
                "2026-07-18",
                ["sk-abcdefghijklmnopqrstuvwxyz"],
            )

    def test_brief_contains_current_facts_for_its_project(self) -> None:
        context_vault.record_project(
            self.vault, "Billing", [str(self.workspace)], "Finish migration", [], True
        )
        context_vault.record_fact(
            self.vault,
            "billing",
            "[[Auth service]]",
            "owner",
            "[[Platform]]",
            "2026-01-01",
            ["PR #421"],
            True,
        )

        brief = context_vault.build_brief(self.vault / "codex-context", self.workspace)

        self.assertEqual(len(brief["current_facts"]), 1)
        self.assertEqual(brief["current_facts"][0]["value"], "[[Platform]]")

    def test_decision_requires_evidence_and_records_reviewed_choice(self) -> None:
        with self.assertRaises(ValueError):
            context_vault.record_decision(
                self.vault,
                "billing",
                "Use Postgres",
                "Postgres",
                ["DynamoDB"],
                "Need relational transactions.",
                [],
                confirm=True,
            )

        note = context_vault.record_decision(
            self.vault,
            "billing",
            "Use Postgres",
            "Postgres",
            ["DynamoDB"],
            "Need relational transactions.",
            ["ADR-001"],
            confirm=True,
        )

        parsed = context_vault.read_note(note)
        self.assertEqual(parsed["metadata"]["type"], "decision")
        self.assertEqual(parsed["metadata"]["alternatives"], ["DynamoDB"])
        self.assertEqual(parsed["metadata"]["rationale"], "Need relational transactions.")

    def test_session_recap_requires_evidence_and_records_next_step(self) -> None:
        with self.assertRaises(ValueError):
            context_vault.record_session(
                self.vault,
                "billing",
                ["Added project note"],
                ["Need review"],
                "Open a pull request",
                [],
                confirm=True,
            )

        note = context_vault.record_session(
            self.vault,
            "billing",
            ["Added project note"],
            ["Need review"],
            "Open a pull request",
            ["session-123"],
            confirm=True,
        )

        parsed = context_vault.read_note(note)
        self.assertEqual(parsed["metadata"]["type"], "session")
        self.assertEqual(parsed["metadata"]["next_step"], "Open a pull request")

    def test_brief_contains_active_project_decisions(self) -> None:
        context_vault.record_project(
            self.vault, "Billing", [str(self.workspace)], "Finish migration", [], True
        )
        context_vault.record_decision(
            self.vault,
            "billing",
            "Use Postgres",
            "Postgres",
            ["DynamoDB"],
            "Need relational transactions.",
            ["ADR-001"],
            confirm=True,
        )

        brief = context_vault.build_brief(self.vault / "codex-context", self.workspace)

        self.assertEqual(len(brief["active_decisions"]), 1)
        self.assertEqual(brief["active_decisions"][0]["choice"], "Postgres")

    def test_brief_reads_legacy_yaml_decision_frontmatter(self) -> None:
        context_vault.record_project(
            self.vault,
            "Context Vault",
            [str(self.workspace)],
            "Make Context Vault a daily-driver",
            [],
            True,
        )
        fixture = PLUGIN_ROOT / "tests" / "fixtures" / "legacy-yaml-decision.md"
        decision_path = self.vault / "codex-context" / "decisions" / fixture.name
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

        brief = context_vault.build_brief(self.vault / "codex-context", self.workspace)

        self.assertEqual(len(brief["active_decisions"]), 1)
        decision = brief["active_decisions"][0]
        self.assertEqual(decision["title"], "Use a dedicated vault at ~/Documents/context-vault")
        self.assertEqual(decision["alternatives"], ["Keep the vault inside the text_agent repo"])
        self.assertEqual(decision["evidence"], ["Vault migration in Claude Code session, 2026-07-19"])
        self.assertIsNone(decision["supersedes"])

    def test_read_note_preserves_json_frontmatter_keys_allowed_by_writer(self) -> None:
        note = context_vault.write_note(
            self.vault / "codex-context",
            "projects",
            {"id": "custom-field", "custom field": "retained"},
            "# Custom field",
        )

        parsed = context_vault.read_note(note)

        self.assertEqual(parsed["metadata"]["custom field"], "retained")

    def test_brief_returns_three_newest_project_sessions(self) -> None:
        context_vault.record_project(
            self.vault, "Billing", [str(self.workspace)], "Finish migration", [], True
        )
        for day in range(1, 5):
            context_vault.record_session(
                self.vault,
                "billing",
                [f"Completed {day}"],
                [],
                f"Next {day}",
                [f"session-{day}"],
                confirm=True,
                recorded_at=f"2026-07-0{day}T00:00:00+00:00",
            )

        brief = context_vault.build_brief(self.vault / "codex-context", self.workspace)

        self.assertEqual(len(brief["recent_sessions"]), 3)
        self.assertEqual(brief["recent_sessions"][0]["next_step"], "Next 4")
        self.assertEqual(brief["recent_sessions"][-1]["next_step"], "Next 2")

    def test_configure_cli_writes_to_xdg_config_home(self) -> None:
        configured = self.run_cli("configure", "--vault", str(self.vault))
        self.assertEqual(configured.returncode, 0, configured.stderr)

        new_config = (
            Path(self.cli_env["XDG_CONFIG_HOME"]) / "context-vault" / "config.json"
        )
        legacy_config = (
            Path(self.tempdir.name) / ".codex" / "context-vault" / "config.json"
        )
        self.assertTrue(
            new_config.is_file(),
            "configure must write to $XDG_CONFIG_HOME/context-vault/config.json",
        )
        self.assertIn(str(self.vault.resolve()), new_config.read_text(encoding="utf-8"))
        self.assertFalse(
            legacy_config.exists(),
            "configure must not write to the legacy ~/.codex/context-vault location",
        )

    def test_cli_reads_legacy_codex_config_as_fallback(self) -> None:
        # A user who configured the Codex-only build has only the legacy config;
        # reads must fall back to ~/.codex/context-vault/config.json with no
        # re-configuration required.
        legacy_dir = Path(self.tempdir.name) / ".codex" / "context-vault"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.json").write_text(
            json.dumps({"vault_path": str(self.vault)}) + "\n", encoding="utf-8"
        )

        project = self.run_cli(
            "project",
            "--name",
            "Billing",
            "--workspace",
            str(self.workspace),
            "--goal",
            "Finish migration",
            "--confirm",
        )

        self.assertEqual(project.returncode, 0, project.stderr)
        self.assertTrue(
            (self.vault / "codex-context" / "projects" / "billing.md").is_file()
        )

    def test_configure_creates_vault_layout(self) -> None:
        config_path = context_vault.configure(self.vault, self.config_home)

        self.assertTrue(config_path.exists())
        self.assertTrue((self.vault / "codex-context" / "projects").is_dir())
        self.assertTrue((self.vault / "codex-context" / "decisions").is_dir())
        self.assertTrue((self.vault / "codex-context" / "facts").is_dir())
        self.assertTrue((self.vault / "codex-context" / "sessions").is_dir())
        self.assertTrue((self.vault / "codex-context" / "templates").is_dir())

    def test_records_link_back_to_their_project_note(self) -> None:
        fact = context_vault.record_fact(
            self.vault,
            "billing",
            "[[Auth service]]",
            "owner",
            "[[Platform]]",
            "2026-07-18",
            ["PR #421"],
            confirm=True,
        )
        decision = context_vault.record_decision(
            self.vault,
            "billing",
            "Use Postgres",
            "Postgres",
            ["DynamoDB"],
            "Need relational transactions.",
            ["ADR-001"],
            confirm=True,
        )
        session = context_vault.record_session(
            self.vault,
            "billing",
            ["Added migration"],
            [],
            "Open pull request",
            ["Codex task summary"],
            confirm=True,
        )

        for note_path in (fact, decision, session):
            self.assertIn("[[billing]]", context_vault.read_note(note_path)["body"])

    def test_brief_matches_workspace_inside_project_path(self) -> None:
        context_vault.record_project(
            self.vault,
            "Billing",
            [str(self.workspace)],
            "Finish migration",
            [],
            True,
        )
        nested = self.workspace / "services" / "api"
        nested.mkdir(parents=True)

        brief = context_vault.build_brief(self.vault / "codex-context", nested)

        self.assertEqual(brief["project"]["id"], "billing")

    def test_workspace_match_prefers_most_specific_project_path(self) -> None:
        nested = self.workspace / "services" / "api"
        nested.mkdir(parents=True)
        context_vault.record_project(
            self.vault,
            "Monorepo",
            [str(self.workspace)],
            "Own everything",
            [],
            True,
        )
        context_vault.record_project(
            self.vault,
            "API",
            [str(nested)],
            "Ship the API",
            [],
            True,
        )

        brief = context_vault.build_brief(self.vault / "codex-context", nested)

        self.assertEqual(brief["project"]["id"], "api")

    def test_write_and_read_note_round_trip(self) -> None:
        note = context_vault.write_note(
            self.vault / "codex-context",
            "facts",
            {"id": "fact-owner", "type": "fact", "evidence": ["PR #421"]},
            "Platform team owns the auth service.\n",
        )

        parsed = context_vault.read_note(note)

        self.assertEqual(parsed["metadata"]["id"], "fact-owner")
        self.assertEqual(parsed["metadata"]["evidence"], ["PR #421"])
        self.assertEqual(parsed["body"], "Platform team owns the auth service.\n")


if __name__ == "__main__":
    unittest.main()
