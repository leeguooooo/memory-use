"""Hooks must work in any locale, and neither they nor the secret scan may fail invisibly."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_memory_use import mu  # isolate config/state before importing helpers


class HookRobustnessTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "notes/.git").mkdir(parents=True)
        (self.root / "notes/glossary.md").write_text("- 群晖 / NAS盘 — 存储\n", encoding="utf-8")
        self.env = {**os.environ, "MEMORY_USE_DIR": str(self.root / "notes"), "MEMORY_USE_STATE_DIR": str(self.root / "state"),
                    "MEMORY_USE_CONFIG_DIR": str(self.root / "config"), "MEMORY_USE_NO_UPDATE_CHECK": "1"}

    def hook(self, event, payload, **env):
        return subprocess.run([sys.executable, str(mu.TOOL_ROOT / "scripts/memory_use.py"), "hook", event],
                              input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True,
                              env={**self.env, **env}, timeout=20)

    def test_prompt_nudge_survives_non_utf8_locales(self):
        for n, env in enumerate(({"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "PYTHONIOENCODING": "ascii"},
                                 {"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})):
            result = self.hook("prompt", {"prompt": "群晖坏了", "session_id": f"s{n}"}, **env)
            self.assertIn("群晖", result.stdout.decode("utf-8"), env)

    def test_hook_failure_stays_open_and_reaches_doctor(self):
        with mock.patch.object(mu, "STATE_DIR", self.root / "state"), mock.patch.object(mu, "repo_dir", side_effect=ValueError("private text")):
            self.assertEqual(mu.cmd_hook(mock.Mock(event="prompt")), 0)
            errors = mu.recent_hook_errors()
        self.assertEqual([e["error"] for e in errors], ["ValueError"])
        self.assertNotIn("private text", json.dumps(errors))

    def test_compaction_allows_the_same_topic_again(self):
        prompt = {"prompt": "群晖坏了", "session_id": "s"}
        self.assertIn(b"memory-use", self.hook("prompt", prompt).stdout)
        self.assertEqual(self.hook("prompt", prompt).stdout, b"")
        self.hook("compact", {"session_id": "s"})
        self.assertIn(b"memory-use", self.hook("prompt", prompt).stdout)

    def test_autosync_that_stopped_running_is_reported(self):
        now = dt.datetime.now().astimezone()
        fresh = {"ok": True, "at": now.isoformat()}
        stale = {"ok": True, "at": (now - dt.timedelta(days=3)).isoformat()}
        with mock.patch.object(mu, "autosync_installed", return_value=True):
            self.assertIsNone(mu.autosync_problem(fresh))
            self.assertIn("has not run since", mu.autosync_problem(stale))
        with mock.patch.object(mu, "autosync_installed", return_value=False):
            self.assertIsNone(mu.autosync_problem(stale))
        self.assertIn("stuck", mu.autosync_problem({"ok": False, "result": "x"}))

    def test_autosync_unit_with_deleted_interpreter_is_reported(self):
        unit = self.root / "unit"
        script = self.root / "memory_use.py"
        script.touch()
        unit.write_text(f"<string>/gone/bin/python3.14</string><string>{script}</string>", encoding="utf-8")
        with mock.patch.object(mu, "autosync_units", return_value={"darwin": unit, "systemd-service": unit}):
            self.assertEqual(mu.autosync_missing_paths(), ["/gone/bin/python3.14"])


class SecretScanTest(unittest.TestCase):
    def test_credentials_in_common_shapes_are_flagged(self):
        for line in ("DB_PASSWORD=hunter2hunter2", '{"password": "hunter2hunter2"}', "https://leo:hunter2hunter@git.example.com/x",
                     "Authorization: Bearer abcdefgh12345678", "sshpass -p hunter2hunter ssh nas", "password: 12345678", "密码：66668888", "mysql -uroot -phunter2hunter"):
            self.assertTrue(mu.scan_text(line, [], set()), line)

    def test_names_limits_and_placeholders_pass(self):
        for line in ("MAX_TOKEN=4096", "TOKEN_LIMIT=8192", "postgres://admin:${DB_PASS}@db/app", "Authorization: Bearer $TOKEN",
                     "mysql -uroot -p", "https://example.com:8443/path@v2", '{"password": "required"}'):
            self.assertEqual(mu.scan_text(line, [], set()), [], line)

    def test_any_text_file_is_scanned_and_binaries_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("secrets.env", ".env", "id.pem", "notes"):
                (root / name).write_text("password=hunter2hunter2\n", encoding="utf-8")
            (root / "blob.bin").write_bytes(b"\0password=hunter2hunter2\n")
            found = mu.scan_paths(sorted(root.iterdir()))
            self.assertEqual(len(found), 4)
            self.assertFalse([x for x in found if "blob.bin" in x])


if __name__ == "__main__":
    unittest.main()
