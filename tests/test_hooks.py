from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess, run

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import context_vault


class HookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["HOME"] = self.tempdir.name
        self.env["XDG_CONFIG_HOME"] = str(self.root / "xdg")
        self.markers = self.root / "xdg" / "context-vault" / "pending-markers"

    def run_hook(self, script: str, payload: dict) -> CompletedProcess[str]:
        return run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "hooks" / script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
        )

    def write_transcript(self, user_messages: int) -> Path:
        transcript = self.root / "transcript.jsonl"
        lines = [json.dumps({"type": "user", "message": f"m{i}"}) for i in range(user_messages)]
        lines.extend(json.dumps({"type": "assistant", "message": "r"}) for _ in range(3))
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return transcript

    def end_payload(self, transcript: Path, session_id: str = "sess-1") -> dict:
        return {
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(self.root / "ws"),
        }

    def test_substantive_session_leaves_marker_once(self) -> None:
        transcript = self.write_transcript(user_messages=6)
        first = self.run_hook("session_end.py", self.end_payload(transcript))
        self.assertEqual(first.returncode, 0, first.stderr)
        markers = list(self.markers.glob("*.json"))
        self.assertEqual(len(markers), 1)
        marker = json.loads(markers[0].read_text(encoding="utf-8"))
        self.assertEqual(marker["session_id"], "sess-1")
        self.assertEqual(marker["transcript_path"], str(transcript))
        again = self.run_hook("session_end.py", self.end_payload(transcript))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(list(self.markers.glob("*.json"))), 1)

    def test_trivial_session_leaves_no_marker(self) -> None:
        transcript = self.write_transcript(user_messages=2)
        result = self.run_hook("session_end.py", self.end_payload(transcript))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.markers.glob("*.json")), [])

    def test_marker_directory_is_private(self) -> None:
        transcript = self.write_transcript(user_messages=6)
        self.run_hook("session_end.py", self.end_payload(transcript))
        mode = stat.S_IMODE(self.markers.stat().st_mode)
        self.assertEqual(mode, 0o700)

    def test_expired_markers_are_cleaned_up(self) -> None:
        transcript = self.write_transcript(user_messages=6)
        self.run_hook("session_end.py", self.end_payload(transcript, session_id="old-sess"))
        old_marker = self.markers / "old-sess.json"
        stale = time.time() - 20 * 86400
        os.utime(old_marker, (stale, stale))
        self.run_hook("session_end.py", self.end_payload(transcript, session_id="new-sess"))
        names = {path.name for path in self.markers.glob("*.json")}
        self.assertEqual(names, {"new-sess.json"})

    def test_session_start_silent_with_nothing_to_say(self) -> None:
        result = self.run_hook(
            "session_start.py",
            {"hook_event_name": "SessionStart", "cwd": str(self.root / "nowhere")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_session_start_surfaces_pending_markers(self) -> None:
        transcript = self.write_transcript(user_messages=6)
        self.run_hook("session_end.py", self.end_payload(transcript))
        result = self.run_hook(
            "session_start.py",
            {"hook_event_name": "SessionStart", "cwd": str(self.root / "nowhere")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("sess-1", context)
        self.assertIn("approval", context)
        self.assertIn(str(transcript), context)

    def test_session_start_injects_brief_for_routable_workspace(self) -> None:
        vault = self.root / "vault"
        workspace = self.root / "ws"
        workspace.mkdir()
        config_home = self.root / "xdg" / "context-vault"
        context_vault.configure(vault, config_home=config_home)
        context_vault.record_project(vault, "Hooked", [str(workspace)], "Test hooks", [], True)
        result = self.run_hook(
            "session_start.py",
            {"hook_event_name": "SessionStart", "cwd": str(workspace)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Context Vault brief", context)
        self.assertIn("hooked", context)


if __name__ == "__main__":
    unittest.main()
