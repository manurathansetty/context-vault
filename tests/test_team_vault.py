from __future__ import annotations

import fcntl
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


class TeamVaultTests(unittest.TestCase):
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

    def run_cli(self, *args: str) -> CompletedProcess[str]:
        return run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "context_vault.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=self.cli_env,
        )

    def _git(self, cwd: Path, *args: str) -> CompletedProcess[str]:
        return run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)

    def _configure_git_user(self, clone: Path) -> None:
        self._git(clone, "config", "user.name", "Test User")
        self._git(clone, "config", "user.email", "test@example.com")

    def make_team_setup(self) -> tuple[Path, Path, Path]:
        """Create a bare origin plus two clones seeded with a team project note."""
        origin = self.root / "origin.git"
        run(
            ["git", "init", "--bare", "--initial-branch=main", str(origin)],
            capture_output=True,
            text=True,
            check=True,
        )
        clone_a = self.root / "clone-a"
        run(["git", "clone", str(origin), str(clone_a)], capture_output=True, text=True, check=True)
        self._configure_git_user(clone_a)
        self._git(clone_a, "checkout", "-B", "main")
        context_vault.record_project(
            clone_a,
            "Shared App",
            [str(self.root / "workspace-a")],
            "Ship the shared app",
            [],
            True,
        )
        (clone_a / ".gitattributes").write_text("*.md merge=context-vault\n", encoding="utf-8")
        self._git(clone_a, "add", "-A")
        self._git(clone_a, "commit", "-m", "seed team vault")
        self._git(clone_a, "push", "-u", "origin", "main")
        clone_b = self.root / "clone-b"
        run(["git", "clone", str(origin), str(clone_b)], capture_output=True, text=True, check=True)
        self._configure_git_user(clone_b)
        return origin, clone_a, clone_b

    def register_driver(self, clone: Path) -> None:
        driver = (
            f'"{sys.executable}" "{PLUGIN_ROOT / "scripts" / "context_vault.py"}" '
            "merge-driver %O %A %B %P"
        )
        self._git(clone, "config", "merge.context-vault.name", "Context Vault note merge")
        self._git(clone, "config", "merge.context-vault.driver", driver)

    # ------------------------------------------------------------------ config

    def test_load_config_accepts_legacy_shape(self) -> None:
        self.config_home.mkdir(parents=True)
        (self.config_home / "config.json").write_text(
            json.dumps({"vault_path": str(self.root / "vault")}), encoding="utf-8"
        )
        config = context_vault.load_config(config_home=self.config_home)
        self.assertIsNone(config["identity"])
        self.assertEqual(list(config["vaults"]), ["personal"])
        self.assertIsNone(config["vaults"]["personal"]["sync"])

    def test_load_config_accepts_multi_vault_shape(self) -> None:
        self.config_home.mkdir(parents=True)
        (self.config_home / "config.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "identity": "alex",
                    "vaults": {
                        "personal": {"path": str(self.root / "vault")},
                        "team": {"path": str(self.root / "team"), "sync": "git"},
                    },
                }
            ),
            encoding="utf-8",
        )
        config = context_vault.load_config(config_home=self.config_home)
        self.assertEqual(config["identity"], "alex")
        self.assertEqual(config["vaults"]["team"]["sync"], "git")

    def test_load_config_rejects_three_synced_vaults(self) -> None:
        self.config_home.mkdir(parents=True)
        (self.config_home / "config.json").write_text(
            json.dumps(
                {
                    "vaults": {
                        name: {"path": str(self.root / name), "sync": "git"}
                        for name in ("team-a", "team-b", "team-c")
                    }
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.load_config(config_home=self.config_home)

    def test_configure_preserves_identity_and_team_vaults(self) -> None:
        context_vault.configure(self.root / "vault", config_home=self.config_home, identity="alex")
        config = context_vault.load_config(config_home=self.config_home)
        config["vaults"]["team"] = {"path": self.root / "team", "sync": "git"}
        context_vault.save_config(config, config_home=self.config_home)
        context_vault.configure(self.root / "vault2", config_home=self.config_home)
        reloaded = context_vault.load_config(config_home=self.config_home)
        self.assertEqual(reloaded["identity"], "alex")
        self.assertIn("team", reloaded["vaults"])
        self.assertEqual(reloaded["vaults"]["personal"]["path"], (self.root / "vault2").resolve())

    def test_save_config_keeps_legacy_shape_for_single_personal_vault(self) -> None:
        context_vault.configure(self.root / "vault", config_home=self.config_home)
        payload = json.loads((self.config_home / "config.json").read_text(encoding="utf-8"))
        self.assertIn("vault_path", payload)
        self.assertNotIn("vaults", payload)

    # ----------------------------------------------------------------- routing

    def test_normalize_remote_url_handles_common_forms(self) -> None:
        for url in (
            "git@github.com:Your-Org/App.git",
            "https://github.com/your-org/app.git",
            "ssh://git@github.com/your-org/app",
            "https://github.com/your-org/app",
        ):
            self.assertEqual(context_vault.normalize_remote_url(url), "github.com/your-org/app")

    def _workspace_with_remote(self, name: str, remote: str) -> Path:
        workspace = self.root / name
        workspace.mkdir()
        run(["git", "init", str(workspace)], capture_output=True, check=True)
        run(
            ["git", "-C", str(workspace), "remote", "add", "origin", remote],
            capture_output=True,
            check=True,
        )
        return workspace

    def test_find_project_across_vaults_matches_by_remote(self) -> None:
        team_vault = self.root / "team"
        context_vault.record_project(
            team_vault,
            "Shared App",
            [str(self.root / "someone-elses-path")],
            "Ship it",
            [],
            True,
            workspace_repos=["git@github.com:your-org/app.git"],
        )
        workspace = self._workspace_with_remote("my-clone", "https://github.com/your-org/app.git")
        config = {"identity": None, "vaults": {"team": {"path": team_vault, "sync": "git"}}}
        vault_name, note = context_vault.find_project_across_vaults(config, workspace)
        self.assertEqual(vault_name, "team")
        self.assertEqual(note["metadata"]["id"], "shared-app")

    def test_find_project_across_vaults_falls_back_to_paths(self) -> None:
        personal_vault = self.root / "vault"
        workspace = self._workspace_with_remote(
            "personal-workspace", "https://github.com/me/personal.git"
        )
        context_vault.record_project(personal_vault, "Solo", [str(workspace)], "My thing", [], True)
        config = {
            "identity": None,
            "vaults": {
                "personal": {"path": personal_vault, "sync": None},
                "team": {"path": self.root / "team", "sync": "git"},
            },
        }
        vault_name, note = context_vault.find_project_across_vaults(config, workspace)
        self.assertEqual(vault_name, "personal")
        self.assertEqual(note["metadata"]["id"], "solo")

    def test_guardrail_blocks_repo_mapped_mismatch(self) -> None:
        vault = self.root / "vault"
        workspace = self._workspace_with_remote(
            "other-repo", "https://github.com/your-org/other.git"
        )
        context_vault.record_project(
            vault,
            "Mapped",
            [str(workspace)],
            "Repo-mapped project",
            [],
            True,
            workspace_repos=["github.com/your-org/app"],
        )
        config = {"identity": None, "vaults": {"personal": {"path": vault, "sync": None}}}
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.find_project_across_vaults(config, workspace)

    # ------------------------------------------------------------- attribution

    def test_record_fact_stamps_author_on_synced_vault(self) -> None:
        vault = self.root / "team"
        path = context_vault.record_fact(
            vault,
            "shared-app",
            "[[Orders schema]]",
            "defined-in",
            "PR #123",
            "2026-07-19",
            ["design session"],
            True,
            author="[[@alex]]",
            agent="claude-code",
        )
        note = context_vault.read_note(path)
        self.assertEqual(note["metadata"]["author"], "[[@alex]]")
        self.assertEqual(note["metadata"]["agent"], "claude-code")

    def test_record_fact_has_no_author_by_default(self) -> None:
        vault = self.root / "vault"
        path = context_vault.record_fact(
            vault, "solo", "[[A]]", "is", "B", "2026-07-19", ["note"], True
        )
        self.assertNotIn("author", context_vault.read_note(path)["metadata"])

    def test_attribution_requires_identity_for_synced_vault(self) -> None:
        entry = {"path": self.root / "team", "sync": "git"}
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault._attribution({"identity": None, "vaults": {}}, entry, None)
        self.assertEqual(
            context_vault._attribution({"identity": "alex", "vaults": {}}, entry, "codex"),
            {"author": "[[@alex]]", "agent": "codex"},
        )
        self.assertEqual(
            context_vault._attribution(
                {"identity": "alex", "vaults": {}}, {"path": self.root, "sync": None}, "codex"
            ),
            {},
        )

    def test_ensure_person_note_creates_stub_once(self) -> None:
        vault = self.root / "team"
        path = context_vault.ensure_person_note(vault, "alex", role="developer")
        self.assertEqual(path.name, "@alex.md")
        note = context_vault.read_note(path)
        self.assertEqual(note["metadata"]["type"], "person")
        self.assertEqual(context_vault.ensure_person_note(vault, "alex"), path)

    def test_record_ids_get_random_suffix(self) -> None:
        with mock.patch.object(context_vault, "_record_suffix", side_effect=["aaa111", "bbb222"]):
            first = context_vault.propose_fact(
                "p", "[[S]]", "r", "v", "2026-07-19", ["e"], recorded_at="2026-07-19T00:00:00+00:00"
            )
            second = context_vault.propose_fact(
                "p", "[[S]]", "r", "v", "2026-07-19", ["e"], recorded_at="2026-07-19T00:00:00+00:00"
            )
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(str(first["id"]).endswith("aaa111"))

    def test_exclusive_create_retries_on_collision(self) -> None:
        vault = self.root / "vault"
        with mock.patch.object(
            context_vault, "_record_suffix", side_effect=["aaa111", "aaa111", "bbb222"]
        ):
            first = context_vault.record_fact(
                vault, "p", "[[S]]", "r", "v", "2026-07-19", ["e"], True,
                recorded_at="2026-07-19T00:00:00+00:00",
            )
            second = context_vault.record_fact(
                vault, "p", "[[S]]", "r", "v", "2026-07-19", ["e"], True,
                recorded_at="2026-07-19T00:00:00+00:00",
            )
        self.assertNotEqual(first, second)
        self.assertTrue(second.stem.endswith("bbb222"))

    # ---------------------------------------------------------------- sessions

    def test_session_carries_branch_and_pr(self) -> None:
        payload = context_vault.propose_session(
            "shared-app",
            ["Designed orders schema"],
            [],
            "Build the listing page",
            ["session summary"],
            branch="feat/orders-schema",
            pr="#123",
        )
        self.assertEqual(payload["branch"], "feat/orders-schema")
        self.assertEqual(payload["pr"], "#123")
        plain = context_vault.propose_session("shared-app", [], [], "Continue", ["summary"])
        self.assertNotIn("branch", plain)
        self.assertNotIn("pr", plain)

    # -------------------------------------------------------- disputes, chores

    def test_brief_surfaces_disputed_facts(self) -> None:
        vault = self.root / "team"
        workspace = self.root / "shared-ws"
        workspace.mkdir()
        context_vault.record_project(vault, "Shared App", [str(workspace)], "Ship it", [], True)
        context_vault.record_fact(
            vault, "shared-app", "[[Auth service]]", "owner", "[[Platform team]]",
            "2026-07-18", ["standup"], True, author="[[@alex]]", agent="claude-code",
        )
        context_vault.record_fact(
            vault, "shared-app", "[[Auth service]]", "owner", "[[Infra team]]",
            "2026-07-19", ["other standup"], True, author="[[@alice]]", agent="codex",
        )
        brief = context_vault.build_brief(vault / "codex-context", workspace)
        self.assertEqual(len(brief["disputes"]), 1)
        self.assertEqual(brief["disputes"][0]["subject"], "[[Auth service]]")
        self.assertEqual(len(brief["disputes"][0]["facts"]), 2)

    def test_multi_valued_facts_are_not_disputes(self) -> None:
        vault = self.root / "team"
        workspace = self.root / "shared-ws-multi"
        workspace.mkdir()
        context_vault.record_project(vault, "Shared App", [str(workspace)], "Ship it", [], True)
        for person in ("[[@alex]]", "[[@alice]]"):
            context_vault.record_fact(
                vault, "shared-app", "[[Auth service]]", "contributor", person,
                "2026-07-19", ["git log"], True, cardinality="multi",
            )
        brief = context_vault.build_brief(vault / "codex-context", workspace)
        self.assertEqual(brief["disputes"], [])

    def test_brief_lists_repair_chores(self) -> None:
        vault = self.root / "team"
        workspace = self.root / "shared-ws2"
        workspace.mkdir()
        context_vault.record_project(vault, "Shared App", [str(workspace)], "Ship it", [], True)
        note = vault / "codex-context" / "projects" / "shared-app.md"
        note.write_text(
            context_vault.stamp_merge_status(note.read_text(encoding="utf-8"), "auto-merged"),
            encoding="utf-8",
        )
        quarantine = vault / "codex-context" / "conflicts" / "old-fact.theirs.md"
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        quarantine.write_text("---\nid: \"old-fact\"\n---\n\nremote version\n", encoding="utf-8")
        brief = context_vault.build_brief(vault / "codex-context", workspace)
        statuses = {chore["merge_status"] for chore in brief["repair_chores"]}
        self.assertEqual(statuses, {"auto-merged", "quarantined"})

    # -------------------------------------------------------------------- sync

    def test_sync_push_delivers_and_sync_read_receives(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        path = context_vault.record_fact(
            clone_a, "shared-app", "[[Orders schema]]", "defined-in", "PR #123",
            "2026-07-19", ["design session"], True,
        )
        info = context_vault.sync_push(clone_a, [path], "record fact: orders schema")
        self.assertTrue(info["pushed"], info)
        self.assertEqual(info["unpushed"], 0)
        pulled = context_vault.sync_read(clone_b)
        self.assertTrue(pulled["online"])
        self.assertEqual(len(list((clone_b / "codex-context" / "facts").glob("*.md"))), 1)

    def test_sync_push_retries_after_concurrent_push(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        path_a = context_vault.record_fact(
            clone_a, "shared-app", "[[A]]", "is", "1", "2026-07-19", ["e"], True
        )
        path_b = context_vault.record_fact(
            clone_b, "shared-app", "[[B]]", "is", "2", "2026-07-19", ["e"], True
        )
        self.assertTrue(context_vault.sync_push(clone_a, [path_a], "record fact: A")["pushed"])
        info = context_vault.sync_push(clone_b, [path_b], "record fact: B")
        self.assertTrue(info["pushed"], info)
        context_vault.sync_read(clone_a)
        self.assertEqual(len(list((clone_a / "codex-context" / "facts").glob("*.md"))), 2)

    def test_sync_read_offline_reports_stale_not_error(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        self._git(clone_b, "remote", "set-url", "origin", str(self.root / "gone.git"))
        info = context_vault.sync_read(clone_b)
        self.assertFalse(info["online"])
        self.assertEqual(self._git(clone_b, "status", "--porcelain").stdout.strip(), "")

    def test_sync_read_never_rebases_local_commits(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        path_b = context_vault.record_fact(
            clone_b, "shared-app", "[[Local]]", "is", "unpushed", "2026-07-19", ["e"], True
        )
        self._git(clone_b, "add", str(path_b))
        self._git(clone_b, "commit", "-m", "local unpushed record")
        head_before = self._git(clone_b, "rev-parse", "HEAD").stdout.strip()
        path_a = context_vault.record_fact(
            clone_a, "shared-app", "[[Remote]]", "is", "pushed", "2026-07-19", ["e"], True
        )
        context_vault.sync_push(clone_a, [path_a], "record fact: remote")
        info = context_vault.sync_read(clone_b)
        self.assertTrue(info["online"])
        self.assertEqual(info["unpushed"], 1)
        self.assertEqual(self._git(clone_b, "rev-parse", "HEAD").stdout.strip(), head_before)
        self.assertFalse((clone_b / ".git" / "rebase-merge").exists())
        self.assertFalse((clone_b / ".git" / "rebase-apply").exists())

    def test_vault_lock_serializes(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        with context_vault._vault_lock(clone_a):
            with open(clone_a / ".git" / "context-vault.lock", "w", encoding="utf-8") as handle:
                with self.assertRaises(OSError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # ------------------------------------------------------------ merge driver

    def test_three_way_merge_combines_non_overlapping_edits(self) -> None:
        base = "alpha\nbravo\ncharlie\n"
        ours = "alpha CHANGED\nbravo\ncharlie\n"
        theirs = "alpha\nbravo\ncharlie CHANGED\n"
        merged, clean, first_conflict = context_vault.three_way_merge(base, ours, theirs)
        self.assertTrue(clean)
        self.assertIsNone(first_conflict)
        self.assertEqual(merged, "alpha CHANGED\nbravo\ncharlie CHANGED\n")

    def test_three_way_merge_keeps_both_sides_on_overlap(self) -> None:
        base = "alpha\nbravo\ncharlie\n"
        ours = "alpha\nbravo OURS\ncharlie\n"
        theirs = "alpha\nbravo THEIRS\ncharlie\n"
        merged, clean, first_conflict = context_vault.three_way_merge(base, ours, theirs)
        self.assertFalse(clean)
        self.assertEqual(first_conflict, 1)
        self.assertIn("bravo OURS", merged)
        self.assertIn("bravo THEIRS", merged)

    def test_three_way_merge_identical_edits_stay_clean(self) -> None:
        base = "alpha\nbravo\n"
        edited = "alpha\nbravo SAME\n"
        merged, clean, _ = context_vault.three_way_merge(base, edited, edited)
        self.assertTrue(clean)
        self.assertEqual(merged, edited)

    def test_stamp_merge_status_updates_frontmatter(self) -> None:
        text = '---\nid: "p"\n---\n\nbody\n'
        stamped = context_vault.stamp_merge_status(text, "auto-merged")
        self.assertIn('merge_status: "auto-merged"', stamped)
        restamped = context_vault.stamp_merge_status(stamped, "needs-human")
        self.assertEqual(restamped.count("merge_status"), 1)
        self.assertIn('merge_status: "needs-human"', restamped)

    def test_merge_driver_quarantines_record_files(self) -> None:
        workdir = self.root / "driver-cwd"
        workdir.mkdir()
        base = self.root / "base.md"
        ours = self.root / "ours.md"
        theirs = self.root / "theirs.md"
        base.write_text('---\nid: "f"\n---\n\noriginal\n', encoding="utf-8")
        ours.write_text('---\nid: "f"\n---\n\nlocal version\n', encoding="utf-8")
        theirs.write_text('---\nid: "f"\n---\n\nremote version\n', encoding="utf-8")
        previous = os.getcwd()
        os.chdir(workdir)
        self.addCleanup(os.chdir, previous)
        code = context_vault.run_merge_driver(
            str(base), str(ours), str(theirs), "codex-context/facts/f.md"
        )
        self.assertEqual(code, 0)
        self.assertEqual(ours.read_text(encoding="utf-8"), '---\nid: "f"\n---\n\nlocal version\n')
        quarantined = workdir / "codex-context" / "conflicts" / "f.theirs.md"
        self.assertEqual(
            quarantined.read_text(encoding="utf-8"), '---\nid: "f"\n---\n\nremote version\n'
        )

    def test_merge_driver_frontmatter_conflict_needs_human(self) -> None:
        base = self.root / "b.md"
        ours = self.root / "o.md"
        theirs = self.root / "t.md"
        base.write_text('---\ngoal: "x"\n---\n\nbody\n', encoding="utf-8")
        ours.write_text('---\ngoal: "y"\n---\n\nbody\n', encoding="utf-8")
        theirs.write_text('---\ngoal: "z"\n---\n\nbody\n', encoding="utf-8")
        code = context_vault.run_merge_driver(
            str(base), str(ours), str(theirs), "codex-context/projects/p.md"
        )
        self.assertEqual(code, 0)
        result = ours.read_text(encoding="utf-8")
        self.assertIn('goal: "y"', result)
        self.assertNotIn('goal: "z"', result)
        self.assertIn('merge_status: "needs-human"', result)

    def test_merge_driver_resolves_project_note_double_edit_in_git(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        self.register_driver(clone_a)
        self.register_driver(clone_b)
        note_a = clone_a / "codex-context" / "projects" / "shared-app.md"
        note_b = clone_b / "codex-context" / "projects" / "shared-app.md"
        note_a.write_text(
            note_a.read_text(encoding="utf-8").replace(
                '"Ship the shared app"', '"Ship the shared app fast"'
            ),
            encoding="utf-8",
        )
        self.assertTrue(context_vault.sync_push(clone_a, [note_a], "edit goal")["pushed"])
        note_b.write_text(
            note_b.read_text(encoding="utf-8") + "\nExtra planning paragraph.\n", encoding="utf-8"
        )
        info = context_vault.sync_push(clone_b, [note_b], "add paragraph")
        self.assertTrue(info["pushed"], info)
        merged = note_b.read_text(encoding="utf-8")
        self.assertIn("fast", merged)
        self.assertIn("Extra planning paragraph.", merged)

    def test_record_divergence_preserves_both_versions(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        self.register_driver(clone_a)
        self.register_driver(clone_b)
        record_a = clone_a / "codex-context" / "facts" / "same-record.md"
        record_b = clone_b / "codex-context" / "facts" / "same-record.md"
        context_vault._write_markdown(
            record_a, {"id": "same-record", "type": "fact"}, "version from a\n"
        )
        context_vault._write_markdown(
            record_b, {"id": "same-record", "type": "fact"}, "version from b\n"
        )
        self.assertTrue(context_vault.sync_push(clone_a, [record_a], "record a")["pushed"])
        info = context_vault.sync_push(clone_b, [record_b], "record b")
        self.assertTrue(info["pushed"], info)
        surviving = record_b.read_text(encoding="utf-8")
        quarantine_dir = clone_b / "codex-context" / "conflicts"
        quarantined_files = list(quarantine_dir.glob("*.md"))
        self.assertEqual(len(quarantined_files), 1)
        quarantined = quarantined_files[0].read_text(encoding="utf-8")
        self.assertEqual(
            {surviving.strip()[-len("version from a"):], quarantined.strip()[-len("version from b"):]},
            {"version from a", "version from b"},
        )

    # --------------------------------------------------------------- validator

    def _run_validator(self, root: Path, *flags: str) -> CompletedProcess[str]:
        return run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "scripts" / "validate_vault.py"),
                "--root",
                str(root),
                *flags,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_validator_passes_a_clean_vault(self) -> None:
        vault = self.root / "team"
        context_vault.record_fact(
            vault, "shared-app", "[[A]]", "is", "B", "2026-07-19", ["e"], True,
            author="[[@alex]]", agent="codex",
        )
        result = self._run_validator(vault, "--require-author")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validator_flags_missing_author_and_bad_frontmatter(self) -> None:
        vault = self.root / "team"
        context_vault.record_fact(
            vault, "shared-app", "[[A]]", "is", "B", "2026-07-19", ["e"], True
        )
        broken = vault / "codex-context" / "facts" / "broken.md"
        broken.write_text("no frontmatter at all\n", encoding="utf-8")
        result = self._run_validator(vault, "--require-author")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no author stamp", result.stdout)
        self.assertIn("broken.md", result.stdout)

    def test_validator_flags_append_only_violation(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        record = context_vault.record_fact(
            clone_a, "shared-app", "[[A]]", "is", "B", "2026-07-19", ["e"], True
        )
        self._git(clone_a, "add", "-A")
        self._git(clone_a, "commit", "-m", "add record")
        record.write_text(record.read_text(encoding="utf-8").replace('"B"', '"EDITED"'), encoding="utf-8")
        self._git(clone_a, "add", "-A")
        self._git(clone_a, "commit", "-m", "edit record")
        result = self._run_validator(clone_a, "--append-only-range", "HEAD~1..HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertIn("append-only", result.stdout)

    # ---------------------------------------------------------------- commands

    def test_init_team_bootstraps_clone_config_and_driver(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        context_vault.configure(self.root / "vault", config_home=self.config_home, identity="alex")
        target = self.root / "team-context"
        run(["git", "clone", str(origin), str(target)], capture_output=True, check=True)
        self._configure_git_user(target)
        result = context_vault.init_team(
            str(origin), name="team", path=target, config_home=self.config_home
        )
        self.assertTrue((target / "codex-context" / "people" / "@alex.md").is_file())
        self.assertTrue((target / ".gitattributes").is_file())
        self.assertTrue((target / ".gitignore").is_file())
        self.assertTrue((target / "scripts" / "validate_vault.py").is_file())
        self.assertTrue(
            (target / ".github" / "workflows" / "context-vault-validate.yml").is_file()
        )
        driver = self._git(target, "config", "--get", "merge.context-vault.driver")
        self.assertEqual(driver.returncode, 0)
        config = context_vault.load_config(config_home=self.config_home)
        self.assertEqual(config["vaults"]["team"]["sync"], "git")
        self.assertTrue(result["sync"]["pushed"], result)
        rerun = context_vault.init_team(
            str(origin), name="team", path=target, config_home=self.config_home
        )
        self.assertEqual(rerun["vault"], str(target.resolve()))

    def test_init_team_requires_identity(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        context_vault.configure(self.root / "vault", config_home=self.config_home)
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.init_team(
                str(origin), path=self.root / "team-context", config_home=self.config_home
            )

    def test_init_team_enforces_two_team_cap(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        context_vault.configure(self.root / "vault", config_home=self.config_home, identity="alex")
        config = context_vault.load_config(config_home=self.config_home)
        config["vaults"]["team-a"] = {"path": self.root / "ta", "sync": "git"}
        config["vaults"]["team-b"] = {"path": self.root / "tb", "sync": "git"}
        context_vault.save_config(config, config_home=self.config_home)
        with self.assertRaises(context_vault.ContextVaultError):
            context_vault.init_team(
                str(origin), name="team-c", path=self.root / "tc", config_home=self.config_home
            )

    def test_doctor_reports_identity_driver_and_unpushed(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        context_vault.configure(self.root / "vault", config_home=self.config_home, identity="alex")
        target = self.root / "team-context"
        run(["git", "clone", str(origin), str(target)], capture_output=True, check=True)
        self._configure_git_user(target)
        context_vault.init_team(str(origin), name="team", path=target, config_home=self.config_home)
        report = context_vault.doctor(config_home=self.config_home)
        self.assertTrue(report["ok"], report)
        checks = {check["check"] for check in report["checks"]}
        self.assertIn("identity", checks)
        self.assertIn("team: merge driver", checks)
        self.assertIn("team: no rebase in progress", checks)

    # ------------------------------------------------------------ CLI end2end

    def test_cli_team_flow_records_with_attribution_and_sync(self) -> None:
        origin, clone_a, clone_b = self.make_team_setup()
        configured = self.run_cli(
            "configure", "--vault", str(self.root / "vault"), "--identity", "alex"
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        target = self.root / "team-context"
        joined = self.run_cli(
            "init-team", "--repo", str(origin), "--vault-name", "team", "--path", str(target)
        )
        self.assertEqual(joined.returncode, 0, joined.stderr)
        recorded = self.run_cli(
            "record-fact",
            "--project", "shared-app",
            "--subject", "[[Orders schema]]",
            "--relation", "defined-in",
            "--value", "PR #123",
            "--valid-from", "2026-07-19",
            "--evidence", "design session",
            "--agent", "claude-code",
            "--confirm",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        payload = json.loads(recorded.stdout)
        self.assertTrue(payload["sync"]["pushed"], recorded.stdout)
        note = context_vault.read_note(Path(payload["path"]))
        self.assertEqual(note["metadata"]["author"], "[[@alex]]")
        self.assertEqual(note["metadata"]["agent"], "claude-code")
        brief = self.run_cli("brief", "--workspace", str(self.root / "workspace-a"))
        self.assertEqual(brief.returncode, 0, brief.stderr)
        brief_payload = json.loads(brief.stdout)
        self.assertIn("team", brief_payload["sync"])
        self.assertEqual(len(brief_payload["current_facts"]), 1)


if __name__ == "__main__":
    unittest.main()
