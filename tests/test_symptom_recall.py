"""Existing troubleshooting links can recall an article without restating its body."""
import contextlib
import io
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from test_memory_use import mu  # establishes isolated config/state before importing the helper


class SymptomRecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fixture = Path(__file__).resolve().parents[1] / "scripts/research/fixtures/symptom-questions.json"
        self.fixture = json.loads(fixture.read_text())
        for file, text in self.fixture["notes"].items():
            path = self.root / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "test",
               "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, env=env)
        subprocess.run(["git", "-C", str(self.root), "commit", "--allow-empty", "-qm", "fixture"], check=True, env=env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_question_recalls_body_and_answer_evidence(self):
        query = ["SSH", "启动程序桌面上看不到"]
        results = mu.rank(self.root, query)
        self.assertEqual(results[0][1]["file"], "ops/gui.md")
        self.assertEqual(results[0][1]["via_symptom"]["source"], "ops/README.md")
        text = mu.render_brief(self.root, query)
        self.assertIn("[symptom link]", text)
        self.assertIn("trigger: ops/README.md:", text)
        body = "\n".join(mu.brief_sources(text)["ops/gui.md"])
        self.assertIn("Interactive", body)
        self.assertIn("计划任务", body)
        out = io.StringIO()
        with mock.patch.object(mu, "need_repo", return_value=self.root), contextlib.redirect_stdout(out):
            self.assertEqual(mu.cmd_search(SimpleNamespace(terms=query, all=False, limit=8)), 0)
        self.assertIn("ops/gui.md", out.getvalue().splitlines()[0])
        self.assertIn("[symptom link]", out.getvalue().splitlines()[0])
        self.assertIn("trigger: ops/README.md:", out.getvalue())
        self.assertIn("Interactive", out.getvalue())

    def test_wrong_entities_negation_unknown_constraints_and_scope_do_not_route(self):
        for case in self.fixture["cases"]:
            if case["kind"] == "answerable":
                continue
            with self.subTest(question=case["question"]):
                self.assertFalse([c for _, c, _ in mu.rank(self.root, case["queries"][0], top=None)
                                  if c.get("via_symptom")])

    def test_keyword_and_numeric_queries_preserve_existing_ranking(self):
        for query in (["SSH"], ["windows-box"], ["267011"], ["sslVerify"]):
            def rows():
                return [(s, c["file"], c["line"]) for s, c, _ in mu.rank(self.root, query, top=None)]
            with mock.patch.object(mu, "trouble_routes", return_value=[]):
                before = rows()
            self.assertEqual(before, rows())

    def test_repeated_entity_aliases_do_not_add_weight(self):
        def rows(query):
            return [(s, c["file"], c["line"]) for s, c, _ in mu.rank(self.root, query, top=None)]
        first = ["SSH", "台式机", "启动程序桌面上看不到"]
        self.assertEqual(rows(first), rows(first + ["windows", "电脑", "SSH"]))

    def test_source_condition_alone_is_not_answer_evidence(self):
        (self.root / "ops/gui.md").write_text("# Empty guide\n")
        text = mu.render_brief(self.root, ["SSH", "启动程序桌面上看不到"])
        self.assertIn("ops/gui.md", text)
        self.assertIn("trigger:", text)
        self.assertNotIn("ops/gui.md", mu.brief_sources(text))

    def test_only_real_troubleshooting_rows_with_safe_targets_are_routes(self):
        (self.root / "archive").mkdir()
        (self.root / "archive/old.md").write_text("# Old guide\n")
        (self.root / "evals.md").write_text("- question → ops/gui.md\n")
        external = Path(self.tmp.name).parent / (self.root.name + "-external.md")
        external.write_text("# Outside\n")
        self.addCleanup(external.unlink)
        (self.root / "ops/external.md").symlink_to(external)
        readme = self.root / "ops/README.md"
        readme.write_text(readme.read_text() + r"""
## Troubleshooting
| Symptom | Fix |
|---|---|
```markdown
| fake fenced condition | [gui.md](gui.md) |
```
| archived condition | [old](../archive/old.md) |
| test question condition | [cases](../evals.md) |
| outside condition | [outside](external.md) |
| missing condition | [missing](missing.md) |
| Windows path condition | see C:\folder\gui.md |
| drive path condition | see C:gui.md |
## Other
| fake other section | [gui.md](gui.md) |
""")
        routes = mu.trouble_routes(self.root)
        self.assertEqual(len(routes), 7)
        self.assertFalse(any("fake" in r["condition"] for r in routes))
        self.assertEqual({r["target"] for r in routes}, {"ops/gui.md", "ops/client.md", "ops/camera.md", "ops/disk.md", "ops/git.md", "ops/other.md", "ops/https.md"})

    def test_condition_requires_complete_numeric_entity_and_preserves_negation(self):
        condition = "SSH 190 程序在桌面上看不到"
        glossary = [["190", "office-mac"]]
        self.assertTrue(mu.symptom_matches(["SSH", "190", "程序桌面上看不到"], condition, glossary))
        self.assertFalse(mu.symptom_matches(["SSH", "1900", "程序桌面上看不到"], condition, glossary))
        self.assertFalse(mu.symptom_matches(["SSH", "190", "程序桌面上看到"], condition, glossary))
        for terms in (["相机", "传输权限拒绝"], ["相机传输权限拒绝"]):
            self.assertFalse(mu.symptom_matches(terms, "相关机器传输权限拒绝", [["相机"]]))
            self.assertFalse(mu.symptom_matches(terms, "相关机器传输权限拒绝", []))
        self.assertFalse(mu.symptom_matches(["启动相机传输权限拒绝"], "启动相关机器传输权限拒绝", [["相机"]]))
        routed = {c["file"] for _, c, _ in mu.rank(self.root, ["相机", "FTP", "传输权限拒绝"], top=None) if c.get("via_symptom")}
        self.assertEqual(routed, {"ops/camera.md"})
        self.assertFalse(mu.symptom_matches(["相机", "HTTP", "传输权限拒绝"], "相机 HTTPS 传输权限拒绝", []))
        self.assertFalse(mu.symptom_matches(["相机http传输权限拒绝"], "相机 HTTPS 传输权限拒绝", []))
        self.assertFalse(mu.symptom_matches(["访问https://box/a报错"], "访问 https://box/b 时 /a 报错", []))

    def test_tight_budget_preserves_actual_excerpt_and_warning(self):
        path = self.root / "ops/gui.md"
        path.write_text(path.read_text().replace("# 交互式会话规则", "# 交互式会话规则\n> Status: deprecated") + "x" * 3000)
        text = mu.render_brief(self.root, ["SSH", "启动程序桌面上看不到"], 512)
        self.assertLessEqual(len(text), 512)
        self.assertIn("WARN marked deprecated", text)
        self.assertIn("Interactive", "\n".join(mu.brief_sources(text).get("ops/gui.md", [])))

    def test_evidence_fixture_cannot_pass_with_an_empty_or_missing_answer(self):
        script = Path(__file__).resolve().parents[1] / "scripts/research/symptom_probe.py"
        spec = importlib.util.spec_from_file_location("symptom_probe_test", script)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        for spans in ([], ["not in this note"], "Interactive"):
            self.fixture["cases"][0]["evidence"] = spans
            fixture = self.root / "bad-fixture.json"
            fixture.write_text(json.dumps(self.fixture))
            with self.assertRaises(ValueError):
                probe.run(Path(mu.__file__), fixture, 1500)
        self.fixture["cases"][0]["evidence"] = ["Interactive"]
        for queries in ([[""]], ["SSH"]):
            self.fixture["cases"][0]["queries"] = queries
            fixture.write_text(json.dumps(self.fixture))
            with self.assertRaises(ValueError):
                probe.run(Path(mu.__file__), fixture, 1500)
