"""The packaged prompt hook must fail open before the host discards its output."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_memory_use import mu  # isolate config/state before importing helpers
import prompt_hook


class PromptHookTest(unittest.TestCase):
    def test_timeout_discards_partial_output_and_reaps_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "finished"
            command = [sys.executable, "-c",
                       "import sys,time; from pathlib import Path; "
                       "print('partial nudge',flush=True); time.sleep(10); Path(sys.argv[1]).touch()",
                       str(marker)]
            started = time.monotonic()
            self.assertEqual(prompt_hook.run_prompt_hook(command, b"{}", timeout=0.25), b"")
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse(marker.exists())

    def test_only_successful_complete_output_is_forwarded(self):
        code = "import sys; print(sys.stdin.read()); sys.exit(int(sys.argv[1]))"
        for rc in (0, 1):
            result = prompt_hook.run_prompt_hook([sys.executable, "-c", code, str(rc)], b"payload")
            self.assertEqual(result.strip(), b"payload" if rc == 0 else b"")
        self.assertEqual(prompt_hook.run_prompt_hook(["/missing/memory-use-worker"], b"{}"), b"")

    def test_packaged_supervisor_stops_a_slow_worker_before_host_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            scripts = Path(temp)
            (scripts / "prompt_hook.py").write_bytes((mu.TOOL_ROOT / "scripts/prompt_hook.py").read_bytes())
            (scripts / "memory_use.py").write_text(
                "import time\nprint('incomplete nudge',flush=True)\ntime.sleep(20)\n")
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(scripts / "prompt_hook.py")], input=b"{}",
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            self.assertLess(time.monotonic() - started, 5)

    def test_packaged_command_preserves_nudges_and_session_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            (root / "glossary.md").write_text("- NAS / nas-box — storage\n")
            command = json.loads((mu.TOOL_ROOT / "hooks/hooks.json").read_text())["hooks"]["UserPromptSubmit"][0]["hooks"][0]
            self.assertIn("scripts/prompt_hook.py", command["command"])
            self.assertLess(prompt_hook.PROMPT_TIMEOUT, command["timeout"])
            env = {**os.environ, "MEMORY_USE_DIR": str(root), "MEMORY_USE_STATE_DIR": str(root / "state"),
                   "MEMORY_USE_CONFIG_DIR": str(root / "config"), "MEMORY_USE_NO_SKILL_LINKS": "1"}
            unmatched = json.dumps({"prompt": "unrelated question", "session_id": "unmatched"}).encode()
            result = subprocess.run([sys.executable, str(mu.TOOL_ROOT / "scripts/prompt_hook.py")],
                                    input=unmatched, env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"")
            self.assertFalse((root / "state").exists(), "unmatched prompts must not acquire the session lock")
            payload = json.dumps({"prompt": "check nas-box", "session_id": "test"}).encode()
            for expected in (True, False):
                result = subprocess.run([sys.executable, str(mu.TOOL_ROOT / "scripts/prompt_hook.py")],
                                        input=payload, env=env, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(b"memory-use brief nas" in result.stdout, expected)


if __name__ == "__main__":
    unittest.main()
