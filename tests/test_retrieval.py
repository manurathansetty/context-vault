from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess, run

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import context_vault


class RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_home = self.root / "config"
        self.cli_env = os.environ.copy()
        self.cli_env["HOME"] = self.tempdir.name
        self.cli_env["XDG_CONFIG_HOME"] = str(self.root / "xdg")
        self.now = datetime(2026, 7, 20, tzinfo=timezone.utc)

    def run_cli(self, *args: str) -> CompletedProcess[str]:
        return run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "context_vault.py"), *args],
            capture_output=True, text=True, check=False, env=self.cli_env,
        )

    def make_vault(self) -> tuple[Path, Path]:
        vault = self.root / "vault"
        workspace = self.root / "ws"
        workspace.mkdir()
        context_vault.record_project(vault, "App", [str(workspace)], "Ship", [], True)
        return vault, workspace

    # ------------------------------------------------------------- tokenizer

    def test_tokenize_expands_wiki_links_and_drops_stopwords(self) -> None:
        tokens = context_vault._tokenize("The [[Auth service|auth]] owner is on call")
        self.assertIn("auth", tokens)
        self.assertIn("service", tokens)
        self.assertIn("owner", tokens)
        self.assertNotIn("the", tokens)
        self.assertNotIn("is", tokens)

    # ---------------------------------------------------------------- index

    def test_index_builds_and_reuses_until_content_changes(self) -> None:
        vault, _ = self.make_vault()
        context_vault.record_fact(
            vault, "app", "[[Auth]]", "owner", "[[Platform]]", "2026-07-20", ["e"], True
        )
        first = context_vault.load_or_build_index(vault)
        self.assertIsNotNone(first)
        rev1 = first["revision"]
        second = context_vault.load_or_build_index(vault)
        self.assertEqual(second["revision"], rev1)  # unchanged → same revision
        context_vault.record_fact(
            vault, "app", "[[Cache]]", "owner", "[[Infra]]", "2026-07-20", ["e"], True
        )
        third = context_vault.load_or_build_index(vault)
        self.assertNotEqual(third["revision"], rev1)  # new record → new revision
        self.assertEqual(len(third["entries"]), len(first["entries"]) + 1)

    def test_index_dir_is_private(self) -> None:
        vault, _ = self.make_vault()
        with self.cli_env_home():
            context_vault.load_or_build_index(vault)
            mode = (Path(self.cli_env["XDG_CONFIG_HOME"]) / "context-vault" / "index").stat().st_mode
        self.assertEqual(mode & 0o777, 0o700)

    def cli_env_home(self):
        import unittest.mock as m
        return m.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.cli_env["XDG_CONFIG_HOME"]})

    def test_revision_tracks_git_head(self) -> None:
        vault = self.root / "gitvault"
        workspace = self.root / "gitws"
        workspace.mkdir()
        run(["git", "init", "-b", "main", str(vault)], capture_output=True, check=True)
        run(["git", "-C", str(vault), "config", "user.email", "t@e.com"], capture_output=True)
        run(["git", "-C", str(vault), "config", "user.name", "t"], capture_output=True)
        context_vault.record_project(vault, "G", [str(workspace)], "g", [], True)
        run(["git", "-C", str(vault), "add", "-A"], capture_output=True)
        run(["git", "-C", str(vault), "commit", "-m", "c1"], capture_output=True)
        rev_a = context_vault._vault_revision(vault)
        context_vault.record_fact(vault, "g", "[[X]]", "is", "y", "2026-07-20", ["e"], True)
        run(["git", "-C", str(vault), "add", "-A"], capture_output=True)
        run(["git", "-C", str(vault), "commit", "-m", "c2"], capture_output=True)
        self.assertNotEqual(context_vault._vault_revision(vault), rev_a)

    # -------------------------------------------------------------- ranking

    def test_focus_ranks_relevant_records_first(self) -> None:
        vault, workspace = self.make_vault()
        context_vault.record_fact(
            vault, "app", "[[Billing pipeline]]", "uses", "[[Stripe]]",
            "2026-07-20", ["e"], True,
        )
        context_vault.record_fact(
            vault, "app", "[[Auth service]]", "owner", "[[Platform]]",
            "2026-07-20", ["e"], True,
        )
        brief = context_vault.build_brief(
            vault / "codex-context", workspace, focus="billing stripe payment",
            vault_path=vault,
        )
        self.assertEqual(brief["current_facts"][0]["subject"], "[[Billing pipeline]]")
        self.assertEqual(brief["focus"], "billing stripe payment")

    def test_focus_caps_and_reports_omissions(self) -> None:
        vault, workspace = self.make_vault()
        for i in range(20):
            context_vault.record_fact(
                vault, "app", f"[[Thing{i}]]", "is", "generic",
                "2026-07-20", ["e"], True,
            )
        brief = context_vault.build_brief(
            vault / "codex-context", workspace, focus="thing", vault_path=vault,
        )
        self.assertLessEqual(len(brief["current_facts"]), context_vault.FOCUS_CAPS["current_facts"])
        self.assertGreater(brief["omitted"]["current_facts"], 0)

    def test_disputes_survive_focus_ranking(self) -> None:
        vault, workspace = self.make_vault()
        context_vault.record_fact(
            vault, "app", "[[Owner]]", "owner", "[[A-team]]", "2026-07-18", ["e"], True
        )
        context_vault.record_fact(
            vault, "app", "[[Owner]]", "owner", "[[B-team]]", "2026-07-19", ["e"], True
        )
        for i in range(20):
            context_vault.record_fact(
                vault, "app", f"[[Noise{i}]]", "is", "unrelated matching foo",
                "2026-07-20", ["e"], True,
            )
        brief = context_vault.build_brief(
            vault / "codex-context", workspace, focus="foo", vault_path=vault,
        )
        # The disputed fact must be present despite not matching the focus.
        subjects = [f["subject"] for f in brief["current_facts"]]
        self.assertIn("[[Owner]]", subjects)
        self.assertEqual(len(brief["disputes"]), 1)

    def test_brief_without_focus_is_unchanged_superset(self) -> None:
        vault, workspace = self.make_vault()
        for i in range(5):
            context_vault.record_fact(
                vault, "app", f"[[F{i}]]", "is", "v", "2026-07-20", ["e"], True
            )
        plain = context_vault.build_brief(vault / "codex-context", workspace)
        self.assertNotIn("focus", plain)
        self.assertNotIn("omitted", plain)
        self.assertEqual(len(plain["current_facts"]), 5)

    # --------------------------------------------------------------- entity

    def test_entity_query_matches_across_projects(self) -> None:
        vault = self.root / "vault"
        ws1, ws2 = self.root / "w1", self.root / "w2"
        ws1.mkdir(); ws2.mkdir()
        context_vault.record_project(vault, "Alpha", [str(ws1)], "a", [], True)
        context_vault.record_project(vault, "Beta", [str(ws2)], "b", [], True)
        context_vault.record_fact(
            vault, "alpha", "[[Auth service]]", "owner", "[[Platform]]",
            "2026-07-20", ["e"], True,
        )
        context_vault.record_decision(
            vault, "beta", "Rework [[Auth service]]", "yes", [], "needed",
            ["e"], confirm=True,
        )
        result = context_vault.entity_query(vault / "codex-context", "[[Auth service]]")
        self.assertEqual(result["matched"], 2)
        self.assertIn("alpha", result["by_project"])
        self.assertIn("beta", result["by_project"])
        self.assertFalse(result["unresolved"])

    def test_entity_query_normalizes_alias_and_reports_unresolved(self) -> None:
        vault, _ = self.make_vault()
        context_vault.record_fact(
            vault, "app", "[[Auth service|auth]]", "owner", "[[Platform]]",
            "2026-07-20", ["e"], True,
        )
        hit = context_vault.entity_query(vault / "codex-context", "Auth service")
        self.assertEqual(hit["matched"], 1)
        miss = context_vault.entity_query(vault / "codex-context", "[[Nonexistent]]")
        self.assertTrue(miss["unresolved"])

    # ------------------------------------------------------------------ cli

    def test_cli_focus_and_reindex_and_entity(self) -> None:
        self.assertEqual(
            self.run_cli("configure", "--vault", str(self.root / "vault")).returncode, 0
        )
        self.run_cli(
            "project", "--name", "App", "--workspace", str(self.root / "ws"),
            "--goal", "Ship", "--confirm",
        )
        self.run_cli(
            "record-fact", "--project", "app", "--subject", "[[Payments]]",
            "--relation", "uses", "--value", "[[Stripe]]", "--valid-from", "2026-07-20",
            "--evidence", "e", "--confirm",
        )
        focus = self.run_cli("brief", "--project", "app", "--focus", "payments stripe")
        self.assertEqual(focus.returncode, 0, focus.stderr)
        self.assertEqual(json.loads(focus.stdout)["focus"], "payments stripe")
        reindex = self.run_cli("reindex", "--vault", str(self.root / "vault"))
        self.assertEqual(reindex.returncode, 0, reindex.stderr)
        self.assertEqual(json.loads(reindex.stdout)["records"], 2)  # project + fact
        entity = self.run_cli(
            "query", "--project", "app", "--mode", "entity", "--entity", "[[Stripe]]"
        )
        self.assertEqual(entity.returncode, 0, entity.stderr)
        self.assertEqual(json.loads(entity.stdout)["matched"], 1)


if __name__ == "__main__":
    unittest.main()
