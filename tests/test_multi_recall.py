"""Multi-source eval exercises real retrieval and the budgeted brief."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import memory_use as mu


class MultiRecallTest(unittest.TestCase):
    def test_eval_accepts_paths_with_spaces_parentheses_and_quoted_commas(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "notes"
            (root / "ops").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "--allow-empty", "-qm", "init"], check=True)
            targets = ["ops/recovery notes.md", "ops/恢复(备用).md", "ops/restore, verify.md"]
            for target in targets:
                (root / target).write_text("# needle\n\nneedle recovery evidence\n")
            cases = Path(temp) / "questions.md"
            cases.write_text("- needle → " + ", ".join(f"`{target}`" for target in targets) + "\n"
                             "- needle → ops/recovery notes.md\n")
            with patch.dict(os.environ, {"MEMORY_USE_DIR": str(root), "MEMORY_USE_NO_UPDATE_CHECK": "1"}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = mu.main(["eval", "--cases", str(cases), "--json"])
                self.assertEqual(rc, 0)
                report = json.loads(out.getvalue())
                self.assertEqual(report["summary"]["all"]["cases"], 2)
                self.assertEqual([e["expected"] for e in report["cases"][0]["evidence"]], targets)
                self.assertEqual(report["summary"]["all"]["brief_present"], 1)
                (root / "evals.md").write_text(cases.read_text())
                stored = [c for c in mu.eval_cases(root) if c[0] == "evals.md"]
                self.assertEqual(len(stored), 2)
                automatic = mu.evaluate_recall(root, mu.eval_cases(root))
                self.assertEqual(automatic["summary"]["all"]["invalid"], 0)

    def test_partial_evidence_and_invalid_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "notes"
            (root / "ops").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "ops/a.md").write_text("# needle\n\nneedle evidence\n")
            (root / "ops/b.md").write_text("# unrelated\n\nother evidence\n")
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "--allow-empty", "-qm", "init"], check=True)
            cases = Path(temp) / "questions.md"
            cases.write_text("- needle → `ops/a.md`, `ops/b.md`\n")
            with patch.dict(os.environ, {"MEMORY_USE_DIR": str(root), "MEMORY_USE_NO_UPDATE_CHECK": "1"}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = mu.main(["eval", "--cases", str(cases), "--json"])
                self.assertEqual(rc, 0)
                report = json.loads(out.getvalue())
                self.assertEqual(report["summary"]["all"]["cases"], 1)
                row = report["cases"][0]
                self.assertEqual([e["brief_status"] for e in row["evidence"]], ["present", "not-retrieved"])
                self.assertEqual(row["recall_at_3"], 0.5)
                self.assertEqual(row["brief_evidence_recall"], 0.5)
                self.assertEqual(report["multi_evidence"]["recall_at_3"], 0.5)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(mu.main(["eval", "--cases", str(cases)]), 0)
                self.assertIn("multi-evidence (1 valid questions): recall@3 50%", out.getvalue())
                cases.write_text("- needle → ops/a.md, ops/./a.md, ops/b.md\n")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    mu.main(["eval", "--cases", str(cases), "--json"])
                self.assertEqual(len(json.loads(out.getvalue())["cases"][0]["evidence"]), 2)
                cases.write_text("- needle → ops/a.md, ops/missing.md\n")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = mu.main(["eval", "--cases", str(cases), "--json"])
                self.assertEqual(rc, 1)
                report = json.loads(out.getvalue())
                self.assertEqual(report["summary"]["all"]["invalid"], 1)
                self.assertEqual(report["cases"][0]["evidence"][1]["brief_status"], "invalid-target")


if __name__ == "__main__":
    unittest.main()
