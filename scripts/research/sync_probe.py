#!/usr/bin/env python3
"""Exercise save/sync failure recovery with local bare repositories only.

Uses the test suite's isolated HOME/config fixture; never opens the user's notes
or contacts a Git service. --module can select an earlier source snapshot.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


CASES = [
    "test_sync_push_rejection_is_failure_and_can_retry",
    "test_save_retry_pushes_existing_commit_without_duplicate",
    "test_save_retry_still_rejects_unavailable_remote",
    "test_save_no_push_retry_does_not_sync_existing_commit",
    "test_save_diverged_dirty_checkout_preserves_other_sessions_index",
    "test_sync_diverged_dirty_checkout_is_left_alone",
    "test_save_rebase_conflict_aborts_and_retains_local_commit",
    "test_sync_skips_active_merge_in_linked_worktree",
    "test_autosync_and_sync_use_configured_upstream_remote",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, default=Path(__file__).resolve().parents[1] / "memory_use.py")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
    # Bootstrap the isolated config/state environment before loading any module.
    import test_memory_use as tests
    spec = importlib.util.spec_from_file_location("sync_probe_module", args.module)
    subject = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = subject
    spec.loader.exec_module(subject)
    tests.mu = subject
    rows = []
    for name in CASES:
        result = unittest.TestResult()
        tests.SyncTest(name).run(result)
        rows.append({"case": name, "passed": result.wasSuccessful(),
                     "failures": [detail for _, detail in result.failures + result.errors]})

    fixture = tests.SyncTest()
    fixture.setUp()
    try:
        fixture.commit(fixture.a, "README.md", "remote revision\n")
        tests.sh("git", "push", "-q", cwd=fixture.a)
        (fixture.b / "README.md").write_text("other session staged\n")
        tests.sh("git", "add", "README.md", cwd=fixture.b)
        (fixture.b / "README.md").write_text("other session unstaged\n")
        (fixture.b / "note.md").write_text("local fact\n")
        before_remote = fixture.remote_head(fixture.bare)
        rc, _ = fixture.save_note()
        dirty_observation = {
            "exit_code": rc,
            "other_session_worktree_preserved": (fixture.b / "README.md").read_text() == "other session unstaged\n",
            "other_session_index_preserved": subject.git("show", ":README.md", cwd=fixture.b, check=False).stdout == "other session staged\n",
            "remote_unchanged": fixture.remote_head(fixture.bare) == before_remote,
            "unmerged_paths": subject.git("diff", "--name-only", "--diff-filter=U", cwd=fixture.b).stdout.splitlines(),
            "stash_created": bool(subject.git("stash", "list", cwd=fixture.b).stdout.strip()),
        }
    finally:
        fixture.tearDown()
    print(json.dumps({
        "source_sha256": hashlib.sha256(args.module.read_bytes()).hexdigest(),
        "scope": "local bare Git, synthetic public notes; no hosted service, model, or live notes",
        "cases": rows, "passed": sum(row["passed"] for row in rows),
        "total": len(rows), "dirty_checkout_observation": dirty_observation,
    }, ensure_ascii=False, indent=2))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
