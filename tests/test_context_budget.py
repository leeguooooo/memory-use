"""Check usable source entries, rather than just the length of a clipped string."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_memory_use import mu  # establishes isolated config/state before importing the helper


class ContextBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}
        subprocess.run(["git", "init", "-q", str(self.root)], env=env, check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "--allow-empty", "-qm", "fixture"], env=env, check=True)
        (self.root / "README.md").write_text("# Notes\n")
        (self.root / "nas").mkdir()
        (self.root / "nas/a.md").write_text("# Route\n\n**Key points**\n- route " + "x" * 3000 + "\n")
        (self.root / "nas/b.md").write_text("# Route recovery\n\n**Key points**\n- route fallback is port 2443\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_large_first_entry_does_not_hide_short_relevant_source(self):
        for budget in (512, 1500):
            text = mu.render_brief(self.root, ["route"], max_chars=budget)
            self.assertLessEqual(len(text), budget)
            self.assertEqual(mu.brief_files(text), {"nas/a.md", "nas/b.md"}, text)
            self.assertIn("route fallback is port 2443", text)
            self.assertIn("…", text)

    def test_packed_entries_preserve_deprecation_warning(self):
        path = self.root / "nas/a.md"
        path.write_text(path.read_text().replace("# Route\n", "# Route\n> Status: deprecated\n"))
        text = mu.render_brief(self.root, ["route"], max_chars=900)
        self.assertIn("WARN marked deprecated", text)
        self.assertEqual(mu.brief_files(text), {"nas/a.md", "nas/b.md"})
        self.assertLessEqual(len(text), 900)

    def test_tiny_budget_does_not_cut_a_source_header(self):
        text = mu.render_brief(self.root, ["route"], max_chars=128)
        self.assertLessEqual(len(text), 128)
        for line in text.splitlines():
            if line.startswith("- nas/"):
                self.assertIn("›", line)
        self.assertIn("truncated", text)
