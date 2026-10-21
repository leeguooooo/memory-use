"""Real subprocess/Git experiments; no live notes or remote Git service."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_memory_use as base  # config/state isolation precedes all helper imports


WORKER = """
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('memory_use_worker', sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ready, release = Path(sys.argv[2]), Path(sys.argv[3])
original = m.safe_push
def paused_push(repo):
    ready.write_text('ready')
    deadline = time.monotonic() + 15
    while not release.exists():
        if time.monotonic() > deadline:
            raise TimeoutError('test did not release paused push')
        time.sleep(0.01)
    return original(repo)
m.safe_push = paused_push
sys.exit(m.main(['autosync', 'run']))
"""


class RepositoryConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.case = base.SyncTest()
        self.case.setUp()
        self.root = Path(self.case.tmp.name)
        self.ready, self.release = self.root / "ready", self.root / "release"
        self.worker = None

    def tearDown(self):
        if self.worker is not None and self.worker.poll() is None:
            self.worker.kill()
            self.worker.communicate(timeout=5)
        self.case.tearDown()

    def env(self, repo=None, state="primary-state"):
        return {**os.environ, "MEMORY_USE_DIR": str(repo or self.case.b),
                "MEMORY_USE_STATE_DIR": str(self.root / state),
                "MEMORY_USE_CONFIG_DIR": str(self.root / "worker-config"),
                "MEMORY_USE_SECRETS": str(self.root / "worker-secrets"),
                "MEMORY_USE_SECRET_HASHES": str(self.root / "worker-hashes"),
                "MEMORY_USE_NO_UPDATE_CHECK": "1"}

    def start_paused_sync(self):
        self.case.commit(self.case.b, "first.md", "first public fact\n")
        self.worker = subprocess.Popen(
            [sys.executable, "-c", WORKER, str(Path(base.mu.__file__).resolve()),
             str(self.ready), str(self.release)],
            env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 8
        while not self.ready.exists():
            if self.worker.poll() is not None:
                self.fail(str(self.worker.communicate()))
            if time.monotonic() > deadline:
                self.fail("worker did not reach the push barrier")
            time.sleep(0.01)

    def finish_sync(self):
        self.release.write_text("release")
        out, err = self.worker.communicate(timeout=8)
        return {"exit_code": self.worker.returncode, "stdout": out, "stderr": err}

    def command(self, *args, repo=None, state="contender-state"):
        return subprocess.run([sys.executable, str(Path(base.mu.__file__).resolve()), *args],
                              env=self.env(repo, state), capture_output=True, text=True, timeout=8)

    def remote_has(self, name):
        return base.mu.git("cat-file", "-e", f"HEAD:{name}", cwd=self.case.bare, check=False).returncode == 0

    def observe_save_race(self):
        self.start_paused_sync()
        (self.case.b / "later.md").write_text("local-only public fact\n")
        contender = self.command("save", "later.md", "-m", "later", "--no-push")
        before_release = base.mu.git("cat-file", "-e", "HEAD:later.md", cwd=self.case.b, check=False).returncode == 0
        primary = self.finish_sync()
        remote_contains = self.remote_has("later.md")
        retry = self.command("save", "later.md", "-m", "later", "--no-push")
        return {"contender_exit_code": contender.returncode,
                "contender_stdout": contender.stdout, "primary": primary,
                "later_committed_before_primary_finished": before_release,
                "remote_contains_local_only_note": remote_contains,
                "retry_exit_code": retry.returncode,
                "retry_remains_local": self.case.head(self.case.b) != self.case.remote_head(self.case.bare),
                "retry_created_remote_note": self.remote_has("later.md")}

    def test_no_push_save_cannot_join_another_process_pending_push(self):
        result = self.observe_save_race()
        self.assertNotEqual(result["contender_exit_code"], 0, result)
        self.assertFalse(result["later_committed_before_primary_finished"], result)
        self.assertEqual(result["primary"]["exit_code"], 0, result)
        self.assertFalse(result["remote_contains_local_only_note"], result)
        self.assertEqual(result["retry_exit_code"], 0, result)
        self.assertTrue(result["retry_remains_local"], result)
        self.assertFalse(result["retry_created_remote_note"], result)

    def test_manual_sync_cannot_enter_another_process_transaction(self):
        self.start_paused_sync()
        remote = self.case.remote_head(self.case.bare)
        result = self.command("sync")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.case.remote_head(self.case.bare), remote)
        self.assertEqual(self.finish_sync()["exit_code"], 0)

    def test_active_autosync_lock_is_not_stolen_because_of_age(self):
        self.start_paused_sync()
        # Both implementations' actual lock paths are considered, so the same
        # scenario can replay against the pre-repair snapshot.
        locks = [self.root / "primary-state/autosync.lock",
                 self.case.b / ".git/memory-use-operation.lock"]
        active = [path for path in locks if path.exists()]
        self.assertTrue(active)
        for path in active:
            os.utime(path, (1, 1))
        result = self.command("autosync", "run", state="primary-state")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.finish_sync()["exit_code"], 0)

    def test_killed_owner_releases_repository_for_next_sync(self):
        self.start_paused_sync()
        self.worker.kill()
        self.worker.communicate(timeout=5)
        result = self.command("sync")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.case.remote_head(self.case.bare), self.case.head(self.case.b))

    def test_other_checkout_and_linked_worktree_can_sync_independently(self):
        linked = self.root / "linked"
        base.sh("git", "worktree", "add", "-qb", "linked", str(linked), cwd=self.case.b)
        base.sh("git", "branch", "--set-upstream-to=origin/main", "linked", cwd=self.case.b)
        self.start_paused_sync()
        for repo in (self.case.a, linked):
            result = self.command("sync", repo=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.finish_sync()["exit_code"], 0)

    def test_save_does_not_stage_during_existing_merge(self):
        self.case.commit(self.case.a, "README.md", "remote revision\n")
        base.sh("git", "push", "-q", cwd=self.case.a)
        self.case.commit(self.case.b, "README.md", "local revision\n")
        base.sh("git", "fetch", "-q", cwd=self.case.b)
        conflict = base.mu.git("merge", "origin/main", cwd=self.case.b, check=False)
        self.assertNotEqual(conflict.returncode, 0)
        before = base.mu.git("ls-files", "--stage", cwd=self.case.b).stdout
        (self.case.b / "later.md").write_text("public fact\n")
        result = self.command("save", "later.md", "-m", "later", "--no-push")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(base.mu.git("ls-files", "--stage", cwd=self.case.b).stdout, before)
