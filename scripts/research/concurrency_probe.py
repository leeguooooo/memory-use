#!/usr/bin/env python3
"""Replay process-level repository races using synthetic, local Git only."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, default=Path(__file__).resolve().parents[1] / "memory_use.py")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
    import test_memory_use as base
    import test_repository_concurrency as cases
    spec = importlib.util.spec_from_file_location("concurrency_probe_subject", args.module)
    subject = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = subject
    spec.loader.exec_module(subject)
    base.mu = subject
    rows = []
    for name in unittest.defaultTestLoader.getTestCaseNames(cases.RepositoryConcurrencyTest):
        result = unittest.TestResult()
        cases.RepositoryConcurrencyTest(name).run(result)
        rows.append({"case": name, "passed": result.wasSuccessful(),
                     "failures": [detail for _, detail in result.failures + result.errors]})
    fixture = cases.RepositoryConcurrencyTest()
    fixture.setUp()
    try:
        observation = fixture.observe_save_race()
    finally:
        fixture.tearDown()
    print(json.dumps({
        "source_sha256": hashlib.sha256(args.module.read_bytes()).hexdigest(),
        "scope": "separate Python processes, real local Git; push paused immediately before safe_push, synthetic notes only",
        "cases": rows, "passed": sum(row["passed"] for row in rows), "total": len(rows),
        "save_during_pending_push": observation,
    }, ensure_ascii=False, indent=2))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
