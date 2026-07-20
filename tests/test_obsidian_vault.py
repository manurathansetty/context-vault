from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, run

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import context_vault


class ObsidianVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_home = self.root / "config"
        self.cli_env = os.environ.copy()
        self.cli_env["HOME"] = self.tempdir.name
        self.cli_env["XDG_CONFIG_HOME"] = str(self.root / "xdg")

    def run_cli(self, *args: str) -> CompletedProcess[str]:
        return run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "context_vault.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=self.cli_env,
        )

    def make_shared_folder(self) -> Path:
        shared = self.root / "shared-obsidian-vault"
        shared.mkdir()
        return shared

    def test_init_obsidian_team_registers_and_scaffolds(self) -> None:
        shared = self.make_shared_folder()
        result = context_vault.init_obsidian_team(
            shared, name="team", config_home=self.config_home, identity="alex"
        )
        self.assertEqual(result["sync"], "obsidian")
        self.assertTrue((shared / "ONBOARDING-OBSIDIAN.md").is_file())
        self.assertTrue((shared / "codex-context" / "people" / "@alex.md").is_file())
        config = context_vault.load_config(config_home=self.config_home)
        self.assertEqual(config["vaults"]["team"]["sync"], "obsidian")
        self.assertEqual(config["identity"], "alex")
        rerun = context_vault.init_obsidian_team(
            shared, name="team", config_home=self.config_home
        )
        self.assertEqual(rerun["vault"], str(shared.resolve()))

    def test_init_obsidian_team_requires_existing_folder_and_identity(self) -> None:
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.init_obsidian_team(
                self.root / "missing", config_home=self.config_home, identity="alex"
            )
        shared = self.make_shared_folder()
        context_vault.configure(self.root / "vault", config_home=self.config_home)
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.init_obsidian_team(shared, config_home=self.config_home)

    def test_obsidian_vault_counts_toward_team_cap(self) -> None:
        shared = self.make_shared_folder()
        context_vault.init_obsidian_team(
            shared, name="team-a", config_home=self.config_home, identity="alex"
        )
        config = context_vault.load_config(config_home=self.config_home)
        config["vaults"]["team-b"] = {
            "path": self.root / "gitvault", "sync": "git", "mode": None, "mode_set_at": None,
        }
        context_vault.save_config(config, config_home=self.config_home)
        third = self.root / "third-shared-vault"
        third.mkdir()
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.init_obsidian_team(
                third, name="team-c", config_home=self.config_home
            )

    def test_records_stamp_author_without_any_git(self) -> None:
        shared = self.make_shared_folder()
        joined = self.run_cli(
            "init-obsidian-team", "--path", str(shared), "--identity", "alex"
        )
        self.assertEqual(joined.returncode, 0, joined.stderr)
        registered = self.run_cli(
            "project", "--vault-name", "team", "--name", "Shared Topic",
            "--workspace", str(self.root / "ws"), "--goal", "Ship", "--confirm",
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        recorded = self.run_cli(
            "record-fact", "--project", "shared-topic", "--subject", "[[S]]",
            "--relation", "is", "--value", "v", "--valid-from", "2026-07-20",
            "--evidence", "e", "--confirm",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        payload = json.loads(recorded.stdout)
        self.assertEqual(payload["sync"], {"managed_by": "obsidian-sync"})
        note = context_vault.read_note(Path(payload["path"]))
        self.assertEqual(note["metadata"]["author"], "[[@alex]]")
        self.assertFalse((shared / ".git").exists())

    def test_brief_reports_unknown_freshness(self) -> None:
        shared = self.make_shared_folder()
        self.run_cli("init-obsidian-team", "--path", str(shared), "--identity", "alex")
        self.run_cli(
            "project", "--vault-name", "team", "--name", "Shared Topic",
            "--workspace", str(self.root / "ws"), "--goal", "Ship", "--confirm",
        )
        brief = self.run_cli("brief", "--project", "shared-topic")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        payload = json.loads(brief.stdout)
        self.assertEqual(
            payload["sync"]["team"], {"managed_by": "obsidian-sync", "freshness": "unknown"}
        )

    def test_retract_refused_withdraw_works(self) -> None:
        shared = self.make_shared_folder()
        self.run_cli("init-obsidian-team", "--path", str(shared), "--identity", "alex")
        self.run_cli(
            "project", "--vault-name", "team", "--name", "Shared Topic",
            "--workspace", str(self.root / "ws"), "--goal", "Ship", "--confirm",
        )
        recorded = self.run_cli(
            "record-fact", "--project", "shared-topic", "--subject", "[[S]]",
            "--relation", "is", "--value", "wrong", "--valid-from", "2026-07-20",
            "--evidence", "e", "--confirm",
        )
        stem = Path(json.loads(recorded.stdout)["path"]).stem
        refused = self.run_cli(
            "retract", "--record", stem, "--remove-from-current-tree", "--confirm"
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("git-synced", refused.stderr)
        withdrawn = self.run_cli(
            "withdraw", "--record", stem, "--reason", "wrong value", "--confirm"
        )
        self.assertEqual(withdrawn.returncode, 0, withdrawn.stderr)
        self.assertEqual(
            json.loads(withdrawn.stdout)["sync"], {"managed_by": "obsidian-sync"}
        )

    def test_doctor_checks_obsidian_folder(self) -> None:
        shared = self.make_shared_folder()
        self.run_cli("init-obsidian-team", "--path", str(shared), "--identity", "alex")
        report = self.run_cli("doctor")
        self.assertEqual(report.returncode, 0, report.stderr)
        checks = {c["check"]: c for c in json.loads(report.stdout)["checks"]}
        self.assertIn("team: synced folder present", checks)
        self.assertTrue(checks["team: synced folder present"]["ok"])
        self.assertIn("Obsidian Sync", checks["team: sync freshness"]["detail"])

    def test_auto_mode_ledger_marks_obsidian_sync(self) -> None:
        shared = self.make_shared_folder()
        self.run_cli("init-obsidian-team", "--path", str(shared), "--identity", "alex")
        self.run_cli(
            "project", "--vault-name", "team", "--name", "Shared Topic",
            "--workspace", str(self.root / "ws"), "--goal", "Ship", "--confirm",
        )
        self.run_cli("auto", "enable", "--vault-name", "team")
        recorded = self.run_cli(
            "record-session", "--project", "shared-topic", "--completed", "c",
            "--next-step", "n", "--evidence", "e", "--session-id", "sess-obs",
            "--trigger", "wrapup",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        note = context_vault.read_note(Path(json.loads(recorded.stdout)["path"]))
        self.assertEqual(note["metadata"]["consent"], "auto")
        ledger = self.root / "xdg" / "context-vault" / "ledger" / "sess-obs.jsonl"
        entries = [json.loads(line) for line in ledger.read_text().splitlines()]
        writes = [e for e in entries if e["event"] == "write"]
        self.assertEqual(writes[0]["push_state"], "obsidian-sync")


if __name__ == "__main__":
    unittest.main()
