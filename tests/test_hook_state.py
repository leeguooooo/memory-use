"""Cross-process hook-state invariants; never use the caller's real state."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from test_memory_use import mu  # establishes isolated config/state before importing the helper


class HookStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.previous = mu.STATE_DIR
        mu.STATE_DIR = self.root / "state"
        mu.STATE_DIR.mkdir()

    def tearDown(self):
        mu.STATE_DIR = self.previous
        self.tmp.cleanup()

    def workers(self, body, count=2):
        """Start together, widening the actual read/modify race without replacing writes."""
        code = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import memory_use as m
old_read = m.session_store
def slow_read(*args, **kwargs):
    result = old_read(*args, **kwargs)
    time.sleep(0.003)
    return result
m.session_store = slow_read
worker = sys.argv[2]
barrier = Path(sys.argv[3])
(barrier / ('ready-' + worker)).touch()
deadline = time.monotonic() + 10
while not (barrier / 'go').exists():
    if time.monotonic() > deadline:
        raise RuntimeError('start barrier timed out')
    time.sleep(0.005)
""" + body
        env = {**os.environ, "MEMORY_USE_STATE_DIR": str(mu.STATE_DIR),
               "MEMORY_USE_CONFIG_DIR": str(self.root / "config"), "MEMORY_USE_NO_UPDATE_CHECK": "1"}
        procs = [subprocess.Popen([sys.executable, "-c", code, str(Path(mu.__file__).parent), str(i), str(self.root)],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(count)]
        try:
            deadline = time.monotonic() + 10
            while len(list(self.root.glob("ready-*"))) != count:
                if time.monotonic() > deadline or any(p.poll() is not None for p in procs):
                    self.fail("worker did not reach start barrier")
                time.sleep(0.005)
            (self.root / "go").touch()
            results = []
            for p in procs:
                out, err = p.communicate(timeout=15)
                self.assertEqual(p.returncode, 0, err)
                results.append(out.strip())
            return results
        finally:
            for p in procs:
                if p.poll() is None:
                    p.kill()
                p.communicate()

    def test_concurrent_failures_account_for_commits_and_explicit_skips(self):
        outputs = self.workers("""
from contextlib import contextmanager
original_update = m.session_update
skipped = {'failure': 0, 'nudge': 0}
operation = 'failure'
@contextmanager
def observed_update():
    with original_update() as state:
        if state is None:
            skipped[operation] += 1
        yield state
m.session_update = observed_update
for i in range(30):
    operation = 'failure'
    m.failure_count('shared-session', 'probe command')
    operation = 'nudge'
    m.not_yet_nudged('worker-' + worker + '-' + str(i), ['vpn'])
print(m.json.dumps(skipped))
""")
        state = mu.session_store()
        skipped = [json.loads(output) for output in outputs]
        # The production hook intentionally skips when its bounded lock wait
        # expires. Count that separately: every admitted update must survive.
        expected_failures = 60 - sum(row["failure"] for row in skipped)
        expected_sessions = 60 - sum(row["nudge"] for row in skipped)
        self.assertGreater(expected_failures, 0)
        self.assertGreater(expected_sessions, 0)
        self.assertEqual(state["shared-session"]["fails"]["probe command"], expected_failures, skipped)
        self.assertEqual(sum(k.startswith("worker-") for k in state), expected_sessions, skipped)

    def test_concurrent_nudge_is_claimed_once(self):
        outputs = self.workers("print(int(bool(m.not_yet_nudged('same-session', ['vpn']))))\n", count=4)
        self.assertEqual(sum(map(int, outputs)), 1)

    def test_corrupt_state_is_preserved_instead_of_overwritten(self):
        path = mu.STATE_DIR / "nudged.json"
        broken = '{"valuable-session":'
        path.write_text(broken)
        self.assertEqual(mu.failure_count("other", "probe"), 1)
        mu.not_yet_nudged("other", ["vpn"])
        mu.note_lesson("other", "vpn")
        self.assertEqual(path.read_text(), broken)
        out = io.StringIO()
        with mock.patch.object(mu, "repo_dir", return_value=self.root), contextlib.redirect_stdout(out):
            self.assertEqual(mu.cmd_doctor(None), 1)
        self.assertIn("hook state unreadable", out.getvalue())
        self.assertEqual(path.read_text(), broken)

    def test_failed_replace_preserves_previous_file_and_cleans_temp(self):
        path = mu.STATE_DIR / "nudged.json"
        path.write_text('{"before": true}')
        before = set(mu.STATE_DIR.iterdir())
        with mock.patch.object(mu.os, "replace", side_effect=OSError("injected replacement failure")):
            with self.assertRaises(OSError):
                mu.atomic_write_text(path, '{"after": true}')
        self.assertEqual(path.read_text(), '{"before": true}')
        self.assertEqual(set(mu.STATE_DIR.iterdir()), before)

    def test_exception_during_update_does_not_commit(self):
        mu.failure_count("s", "probe")
        path = mu.STATE_DIR / "nudged.json"
        original = path.read_bytes()
        with self.assertRaises(RuntimeError):
            with mu.session_update() as state:
                state.clear()
                raise RuntimeError("injected update failure")
        self.assertEqual(path.read_bytes(), original)

    def test_process_death_releases_lock_without_deleting_lock_file(self):
        code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import memory_use as m
with m.local_file_lock(Path(sys.argv[2])):
    Path(sys.argv[3]).touch()
    time.sleep(30)
"""
        lock = mu.STATE_DIR / "nudged.lock"
        ready = self.root / "locked"
        mu.failure_count("existing", "probe")
        original_state = (mu.STATE_DIR / "nudged.json").read_bytes()
        proc = subprocess.Popen([sys.executable, "-c", code, str(Path(mu.__file__).parent), str(lock), str(ready)])
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if time.monotonic() > deadline or proc.poll() is not None:
                    self.fail("lock owner did not start")
                time.sleep(0.005)
            with self.assertRaises(TimeoutError):
                with mu.local_file_lock(lock, timeout=0.05):
                    self.fail("entered another process's lock")
            started = time.monotonic()
            self.assertEqual(mu.failure_count("existing", "probe"), 1)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual((mu.STATE_DIR / "nudged.json").read_bytes(), original_state)
            proc.kill()
            proc.wait(timeout=5)
            with mu.local_file_lock(lock, timeout=0.2):
                self.assertTrue(lock.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def test_concurrent_append_and_rotation_keep_new_lesson_events(self):
        path = mu.STATE_DIR / "lessons.jsonl"
        seed = {"t": 1, "event": "failed", "session": "old", "about": "x" * 200}
        path.write_text((json.dumps(seed) + "\n") * 3000)
        self.workers("""
for i in range(30):
    m.log_lesson('failed', 'worker-' + worker, 'topic-' + str(i))
""")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        actual = {(r["session"], r["about"]) for r in rows if r["session"].startswith("worker-")}
        expected = {(f"worker-{w}", f"topic-{i}") for w in range(2) for i in range(30)}
        self.assertEqual(actual, expected)

    def test_process_killed_before_replace_keeps_committed_json_readable(self):
        path = mu.STATE_DIR / "nudged.json"
        path.write_text('{"committed": true}')
        ready = self.root / "before-replace"
        code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import memory_use as m
def paused_replace(*args):
    Path(sys.argv[3]).touch()
    time.sleep(30)
m.os.replace = paused_replace
m.atomic_write_text(Path(sys.argv[2]), '{"uncommitted": true}')
"""
        proc = subprocess.Popen([sys.executable, "-c", code, str(Path(mu.__file__).parent), str(path), str(ready)])
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if time.monotonic() > deadline or proc.poll() is not None:
                    self.fail("writer did not reach replacement boundary")
                time.sleep(0.005)
            self.assertEqual(json.loads(path.read_text()), {"committed": True})
            proc.kill()
            proc.wait(timeout=5)
            self.assertEqual(json.loads(path.read_text()), {"committed": True})
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    @unittest.skipIf(os.name == "nt", "POSIX file mode assertion")
    def test_atomic_replace_keeps_private_mode_and_respects_read_only(self):
        path = mu.STATE_DIR / "private.json"
        path.write_text("old")
        path.chmod(0o600)
        mu.atomic_write_text(path, "new")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.chmod(0o400)
        try:
            with self.assertRaises(PermissionError):
                mu.atomic_write_text(path, "must not write")
            self.assertEqual(path.read_text(), "new")
        finally:
            path.chmod(0o600)

    def test_standalone_test_modules_bootstrap_outside_real_home(self):
        scratch = self.root / "import-scratch"
        scratch.mkdir()
        home = self.root / "fake-home"
        home.mkdir()
        code = """
import importlib,json,sys
sys.path.insert(0,sys.argv[1])
importlib.import_module(sys.argv[2])
import memory_use as m
print(json.dumps([str(m.CONFIG),str(m.STATE_DIR),str(m.KNOWN_SECRETS)]))
"""
        env = {k: v for k, v in os.environ.items() if not k.startswith("MEMORY_USE_")}
        env.update(HOME=str(home), TMPDIR=str(scratch), MEMORY_USE_NO_UPDATE_CHECK="1")
        for name in ("test_context_budget", "test_hook_state", "test_symptom_recall"):
            result = subprocess.run([sys.executable, "-c", code, str(Path(__file__).parent), name],
                                    env=env, text=True, capture_output=True, check=True)
            for value in json.loads(result.stdout):
                self.assertTrue(Path(value).resolve().is_relative_to(scratch.resolve()), value)
