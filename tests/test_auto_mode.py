from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, run
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import context_vault


class AutoModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_home = self.root / "config"
        (self.root / ".gitconfig").write_text(
            "[user]\n\tname = Test User\n\temail = test@example.com\n"
            "[init]\n\tdefaultBranch = main\n",
            encoding="utf-8",
        )
        self.cli_env = os.environ.copy()
        self.cli_env["HOME"] = self.tempdir.name
        self.cli_env["XDG_CONFIG_HOME"] = str(self.root / "xdg")
        self.cli_env.pop("CONTEXT_VAULT_MANUAL", None)

    def run_cli(self, *args: str, env: dict | None = None) -> CompletedProcess[str]:
        return run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "context_vault.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env or self.cli_env,
        )

    def _git(self, cwd: Path, *args: str) -> CompletedProcess[str]:
        return run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)

    def _configure_git_user(self, clone: Path) -> None:
        self._git(clone, "config", "user.name", "Test User")
        self._git(clone, "config", "user.email", "test@example.com")

    def make_team_setup(self) -> tuple[Path, Path, Path]:
        origin = self.root / "origin.git"
        run(
            ["git", "init", "--bare", "--initial-branch=main", str(origin)],
            capture_output=True, text=True, check=True,
        )
        clone_a = self.root / "clone-a"
        run(["git", "clone", str(origin), str(clone_a)], capture_output=True, check=True)
        self._configure_git_user(clone_a)
        self._git(clone_a, "checkout", "-B", "main")
        context_vault.record_project(
            clone_a, "Shared App", [str(self.root / "ws")], "Ship it", [], True
        )
        self._git(clone_a, "add", "-A")
        self._git(clone_a, "commit", "-m", "seed")
        self._git(clone_a, "push", "-u", "origin", "main")
        clone_b = self.root / "clone-b"
        run(["git", "clone", str(origin), str(clone_b)], capture_output=True, check=True)
        self._configure_git_user(clone_b)
        return origin, clone_a, clone_b

    def cli_setup_auto_personal(self) -> Path:
        vault = self.root / "vault"
        workspace = self.root / "ws"
        workspace.mkdir(exist_ok=True)
        self.assertEqual(
            self.run_cli("configure", "--vault", str(vault), "--identity", "alex").returncode, 0
        )
        self.assertEqual(
            self.run_cli(
                "project", "--name", "Auto Topic", "--workspace", str(workspace),
                "--goal", "Test auto", "--confirm",
            ).returncode, 0,
        )
        self.assertEqual(self.run_cli("auto", "enable").returncode, 0)
        return vault

    # ----------------------------------------------------------- mode config

    def test_vault_mode_resolution_and_downgrade_only_env(self) -> None:
        config = {
            "identity": None,
            "default_mode": None,
            "vaults": {"personal": {"path": self.root, "sync": None, "mode": "auto"}},
        }
        entry = config["vaults"]["personal"]
        self.assertEqual(context_vault.vault_mode(config, entry), "auto")
        config["vaults"]["personal"]["mode"] = None
        self.assertEqual(context_vault.vault_mode(config, entry), "manual")
        config["default_mode"] = "auto"
        self.assertEqual(context_vault.vault_mode(config, entry), "auto")
        with mock.patch.dict(os.environ, {"CONTEXT_VAULT_MANUAL": "1"}):
            self.assertEqual(context_vault.vault_mode(config, entry), "manual")
        with mock.patch.dict(os.environ, {"CONTEXT_VAULT_AUTO": "1"}):
            config["default_mode"] = None
            self.assertEqual(context_vault.vault_mode(config, entry), "manual")

    def test_set_vault_mode_is_durable_and_scoped(self) -> None:
        context_vault.configure(self.root / "vault", config_home=self.config_home)
        result = context_vault.set_vault_mode("auto", config_home=self.config_home)
        self.assertEqual(result["mode"], "auto")
        reloaded = context_vault.load_config(config_home=self.config_home)
        self.assertEqual(reloaded.get("default_mode"), "auto")
        context_vault.set_vault_mode("manual", config_home=self.config_home)
        reloaded = context_vault.load_config(config_home=self.config_home)
        self.assertIsNone(reloaded.get("default_mode"))

    # -------------------------------------------------------- auto write path

    def test_auto_record_needs_no_confirm_and_is_stamped(self) -> None:
        vault = self.cli_setup_auto_personal()
        recorded = self.run_cli(
            "record-fact", "--project", "auto-topic", "--subject", "[[Schema]]",
            "--relation", "state", "--value", "generated", "--valid-from", "2026-07-20",
            "--evidence", "migration file",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        payload = json.loads(recorded.stdout)
        self.assertIn("session_id", payload)
        note = context_vault.read_note(Path(payload["path"]))
        self.assertEqual(note["metadata"]["consent"], "auto")
        self.assertEqual(note["metadata"]["trigger"], "milestone")
        self.assertEqual(note["metadata"]["basis"], "inferred")
        self.assertEqual(note["metadata"]["session"], payload["session_id"])

    def test_manual_mode_still_requires_confirm_and_stays_unstamped(self) -> None:
        vault = self.cli_setup_auto_personal()
        self.assertEqual(self.run_cli("auto", "disable").returncode, 0)
        denied = self.run_cli(
            "record-fact", "--project", "auto-topic", "--subject", "[[S]]",
            "--relation", "r", "--value", "v", "--valid-from", "2026-07-20",
            "--evidence", "e",
        )
        self.assertNotEqual(denied.returncode, 0)
        allowed = self.run_cli(
            "record-fact", "--project", "auto-topic", "--subject", "[[S]]",
            "--relation", "r", "--value", "v", "--valid-from", "2026-07-20",
            "--evidence", "e", "--confirm",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        note = context_vault.read_note(Path(json.loads(allowed.stdout)["path"]))
        self.assertNotIn("consent", note["metadata"])

    def test_checkpoint_chain_shows_one_session(self) -> None:
        vault = self.root / "vault2"
        workspace = self.root / "ws2"
        workspace.mkdir()
        context_vault.record_project(vault, "Chain", [str(workspace)], "Chain test", [], True)
        first = context_vault.record_session(
            vault, "chain", ["schema done"], [], "build page", ["notes"], confirm=True
        )
        second = context_vault.record_session(
            vault, "chain", ["schema done", "page built"], [], "wrap", ["notes"],
            confirm=True, supersedes=first.stem,
        )
        brief = context_vault.build_brief(vault / "codex-context", workspace)
        self.assertEqual(len(brief["recent_sessions"]), 1)
        self.assertEqual(brief["recent_sessions"][0]["id"], second.stem)

    def test_duplicate_deduped_trigger_is_skipped(self) -> None:
        self.cli_setup_auto_personal()
        base = [
            "record-fact", "--project", "auto-topic", "--subject", "[[A]]",
            "--relation", "is", "--value", "1", "--valid-from", "2026-07-20",
            "--evidence", "e", "--trigger", "git-commit", "--source-commit", "abc123",
            "--session-id", "sess-dup",
        ]
        first = self.run_cli(*base)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("path", json.loads(first.stdout))
        second = self.run_cli(*base)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout).get("skipped"), "duplicate")
        vault = self.root / "vault"
        self.assertEqual(len(list((vault / "codex-context" / "facts").glob("*.md"))), 1)

    def test_cross_session_supersede_refused(self) -> None:
        self.cli_setup_auto_personal()
        first = self.run_cli(
            "record-session", "--project", "auto-topic", "--completed", "c1",
            "--next-step", "n", "--evidence", "e", "--session-id", "sess-x",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        forked = self.run_cli(
            "record-session", "--project", "auto-topic", "--completed", "c2",
            "--next-step", "n", "--evidence", "e", "--session-id", "sess-x",
            "--supersedes", "some-other-session-record",
        )
        self.assertNotEqual(forked.returncode, 0)
        self.assertIn("latest checkpoint", forked.stderr)

    def test_ledger_records_write_with_state(self) -> None:
        self.cli_setup_auto_personal()
        recorded = self.run_cli(
            "record-session", "--project", "auto-topic", "--completed", "c",
            "--next-step", "n", "--evidence", "e", "--session-id", "sess-ledger",
            "--trigger", "wrapup",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        ledger = self.root / "xdg" / "context-vault" / "ledger" / "sess-ledger.jsonl"
        self.assertTrue(ledger.is_file())
        entries = [json.loads(line) for line in ledger.read_text().splitlines()]
        writes = [e for e in entries if e["event"] == "write"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["trigger"], "wrapup")
        self.assertEqual(writes[0]["push_state"], "local")

    # -------------------------------------------------------------- withdraw

    def test_withdraw_hides_current_but_preserves_known_at_past(self) -> None:
        vault = self.root / "vault3"
        workspace = self.root / "ws3"
        workspace.mkdir()
        context_vault.record_project(vault, "W", [str(workspace)], "Withdraw test", [], True)
        path = context_vault.record_fact(
            vault, "w", "[[Auth]]", "owner", "[[Platform]]", "2026-07-18", ["e"], True,
            recorded_at="2026-07-19T00:00:00+00:00",
        )
        context_vault.withdraw_record(vault, path.stem, "recorded in error", True)
        brief = context_vault.build_brief(vault / "codex-context", workspace)
        self.assertEqual(brief["current_facts"], [])
        historical = context_vault.resolve_facts(
            vault / "codex-context",
            context_vault.date.fromisoformat("2026-07-19"),
            context_vault._parse_recorded_at("2026-07-19T00:00:01+00:00"),
        )
        self.assertEqual(len(historical), 1)

    def test_withdraw_removes_dispute(self) -> None:
        vault = self.root / "vault4"
        workspace = self.root / "ws4"
        workspace.mkdir()
        context_vault.record_project(vault, "D", [str(workspace)], "Dispute test", [], True)
        keep = context_vault.record_fact(
            vault, "d", "[[Auth]]", "owner", "[[A-team]]", "2026-07-18", ["e"], True
        )
        wrong = context_vault.record_fact(
            vault, "d", "[[Auth]]", "owner", "[[B-team]]", "2026-07-19", ["e"], True
        )
        self.assertEqual(
            len(context_vault.build_brief(vault / "codex-context", workspace)["disputes"]), 1
        )
        context_vault.withdraw_record(vault, wrong.stem, "wrong owner", True)
        brief = context_vault.build_brief(vault / "codex-context", workspace)
        self.assertEqual(brief["disputes"], [])
        self.assertEqual(len(brief["current_facts"]), 1)
        self.assertEqual(brief["current_facts"][0]["id"], keep.stem)

    def test_withdraw_refuses_unknown_and_double(self) -> None:
        vault = self.root / "vault5"
        context_vault.record_project(vault, "X", [str(self.root / "x")], "g", [], True)
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.withdraw_record(vault, "missing-record", "r", True)
        path = context_vault.record_fact(
            vault, "x", "[[S]]", "r", "v", "2026-07-20", ["e"], True
        )
        context_vault.withdraw_record(vault, path.stem, "r", True)
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.withdraw_record(vault, path.stem, "r again", True)

    # --------------------------------------------------------------- retract

    def test_retract_removes_from_tree_and_propagates(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        path = context_vault.record_fact(
            clone_a, "shared-app", "[[Oops]]", "is", "wrong", "2026-07-20", ["e"], True
        )
        self.assertTrue(context_vault.sync_push(clone_a, [path], "record fact: oops")["pushed"])
        result = context_vault.retract_record(clone_a, path.stem, True)
        self.assertTrue(result["pushed"], result)
        self.assertFalse(path.exists())
        subject = self._git(clone_a, "log", "-1", "--format=%s").stdout.strip()
        self.assertTrue(subject.startswith("retract:"))
        context_vault.sync_read(clone_b)
        self.assertEqual(list((clone_b / "codex-context" / "facts").glob("*.md")), [])

    def test_retract_refuses_outside_grace_window(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        path = context_vault.record_fact(
            clone_a, "shared-app", "[[Old]]", "is", "aged", "2026-07-20", ["e"], True
        )
        with mock.patch.dict(
            os.environ,
            {
                "GIT_COMMITTER_DATE": "2026-07-01T00:00:00",
                "GIT_AUTHOR_DATE": "2026-07-01T00:00:00",
            },
        ):
            self.assertTrue(
                context_vault.sync_push(clone_a, [path], "record fact: old")["pushed"]
            )
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.retract_record(clone_a, path.stem, True)
        self.assertTrue(path.exists())

    def test_retract_refuses_non_record_only_commit(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        record = clone_a / "codex-context" / "facts" / "bundled-record.md"
        context_vault._write_markdown(record, {"id": "bundled-record", "type": "fact"}, "x\n")
        other = clone_a / "codex-context" / "facts" / "other-record.md"
        context_vault._write_markdown(other, {"id": "other-record", "type": "fact"}, "y\n")
        self._git(clone_a, "add", "-A")
        self._git(clone_a, "commit", "-m", "bundle two records")
        self._git(clone_a, "push")
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.retract_record(clone_a, "bundled-record", True)

    def test_validator_exempts_retract_and_accepts_withdrawal(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        path = context_vault.record_fact(
            clone_a, "shared-app", "[[Gone]]", "is", "temp", "2026-07-20", ["e"], True,
            author="[[@alex]]", agent="codex",
        )
        context_vault.sync_push(clone_a, [path], "record fact: gone")
        context_vault.retract_record(clone_a, path.stem, True)
        result = run(
            [
                sys.executable, str(PLUGIN_ROOT / "scripts" / "validate_vault.py"),
                "--root", str(clone_a), "--append-only-range", "HEAD~1..HEAD",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        kept = context_vault.record_fact(
            clone_a, "shared-app", "[[Keep]]", "is", "v", "2026-07-20", ["e"], True,
            author="[[@alex]]", agent="codex",
        )
        context_vault.withdraw_record(
            clone_a, kept.stem, "test tombstone", True, author="[[@alex]]", agent="codex"
        )
        result = run(
            [
                sys.executable, str(PLUGIN_ROOT / "scripts" / "validate_vault.py"),
                "--root", str(clone_a), "--require-author",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ------------------------------------------------------------ provenance

    def test_brief_consent_filter_and_vault_mode(self) -> None:
        self.cli_setup_auto_personal()
        auto_fact = self.run_cli(
            "record-fact", "--project", "auto-topic", "--subject", "[[Auto]]",
            "--relation", "is", "--value", "a", "--valid-from", "2026-07-20",
            "--evidence", "e",
        )
        self.assertEqual(auto_fact.returncode, 0, auto_fact.stderr)
        manual_env = dict(self.cli_env, CONTEXT_VAULT_MANUAL="1")
        manual_fact = self.run_cli(
            "record-fact", "--project", "auto-topic", "--subject", "[[Manual]]",
            "--relation", "is", "--value", "m", "--valid-from", "2026-07-20",
            "--evidence", "e", "--confirm", env=manual_env,
        )
        self.assertEqual(manual_fact.returncode, 0, manual_fact.stderr)
        brief = self.run_cli("brief", "--project", "auto-topic")
        payload = json.loads(brief.stdout)
        self.assertEqual(payload["vault_mode"], "auto")
        self.assertEqual(len(payload["current_facts"]), 2)
        filtered = json.loads(
            self.run_cli("brief", "--project", "auto-topic", "--consent", "auto").stdout
        )
        self.assertEqual(len(filtered["current_facts"]), 1)
        self.assertEqual(filtered["current_facts"][0]["subject"], "[[Auto]]")

    # ------------------------------------------------------------------ hooks

    def run_hook(self, script: str, payload: dict) -> CompletedProcess[str]:
        return run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "hooks" / script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env=self.cli_env,
        )

    def test_post_tool_use_nudges_on_code_commit_only_in_auto(self) -> None:
        self.cli_setup_auto_personal()
        code_repo = self.root / "code"
        code_repo.mkdir()
        run(["git", "init", str(code_repo)], capture_output=True, check=True)
        self._configure_git_user(code_repo)
        (code_repo / "f.txt").write_text("x", encoding="utf-8")
        self._git(code_repo, "add", "-A")
        self._git(code_repo, "commit", "-m", "feat: x")
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'feat: x'"},
            "tool_response": {"success": True},
            "cwd": str(code_repo),
        }
        result = self.run_hook("post_tool_use.py", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("--trigger git-commit", context)
        self.assertIn("--source-commit", context)
        self.run_cli("auto", "disable")
        silent = self.run_hook("post_tool_use.py", payload)
        self.assertEqual(silent.stdout.strip(), "")

    def test_pre_compact_instructs_checkpoint_when_auto(self) -> None:
        self.cli_setup_auto_personal()
        result = self.run_hook("pre_compact.py", {"hook_event_name": "PreCompact"})
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("precompact", context)
        self.run_cli("auto", "disable")
        self.assertEqual(self.run_hook("pre_compact.py", {}).stdout.strip(), "")

    def test_session_end_skips_marker_when_wrapup_recorded(self) -> None:
        ledger_dir = self.root / "xdg" / "context-vault" / "ledger"
        ledger_dir.mkdir(parents=True)
        (ledger_dir / "sess-done.jsonl").write_text(
            json.dumps({"event": "write", "trigger": "wrapup"}) + "\n", encoding="utf-8"
        )
        transcript = self.root / "t.jsonl"
        transcript.write_text(
            "\n".join(json.dumps({"type": "user", "message": f"m{i}"}) for i in range(8)) + "\n",
            encoding="utf-8",
        )
        result = self.run_hook(
            "session_end.py",
            {
                "hook_event_name": "SessionEnd",
                "session_id": "sess-done",
                "transcript_path": str(transcript),
                "cwd": str(self.root),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        markers = self.root / "xdg" / "context-vault" / "pending-markers"
        self.assertEqual(list(markers.glob("*.json")) if markers.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
