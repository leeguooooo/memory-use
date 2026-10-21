#!/usr/bin/env python3
"""Check question -> fixed query -> article -> answer-bearing excerpt separately."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def source_excerpts(text):
    """Independent reader: routing metadata must not satisfy answer evidence."""
    import re
    result, current = {}, None
    for line in text.splitlines():
        match = re.match(r"^- (.+\.md):\d+ ‹.*›(?: \[(?:glossary|symptom) link\])?$", line)
        if match:
            current = match[1]
        elif current and line.startswith("    "):
            content = line.strip()
            if content and not content.startswith(("WARN ", "trigger: ", "要点/key points:")):
                result.setdefault(current, []).append(content)
        elif line.strip():
            current = None
    return {file: "\n".join(lines) for file, lines in result.items()}


def run(module_path, fixture_path, budget):
    spec = importlib.util.spec_from_file_location("probe_memory", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fixture = json.loads(fixture_path.read_text())
    for case in fixture["cases"]:
        if case["kind"] not in {"answerable", "unanswerable", "ambiguous"}:
            raise ValueError("unknown question kind")
        if not case.get("queries") or any(
            not isinstance(query, list) or not query or
            any(not isinstance(term, str) or not term.strip() for term in query)
            for query in case["queries"]
        ):
            raise ValueError("every question needs a fixed, nonempty query sequence")
        if case["kind"] == "answerable":
            spans = case.get("evidence")
            expected = case.get("expected")
            if expected not in fixture["notes"] or not isinstance(spans, list) or not spans or any(not isinstance(s, str) or not s for s in spans):
                raise ValueError("answerable cases need an existing target and nonempty evidence spans")
            if not all(span in fixture["notes"][expected] for span in spans):
                raise ValueError("gold evidence is absent from its target note")
        elif case.get("expected") or case.get("evidence"):
            raise ValueError("unanswerable and ambiguous cases cannot name a gold answer")
    rows = []
    with tempfile.TemporaryDirectory(prefix="symptom-probe-") as temp:
        root = Path(temp)
        for file, text in fixture["notes"].items():
            path = root / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}
        subprocess.run(["git", "-C", str(root), "commit", "--allow-empty", "-qm", "fixture"], check=True, env=env)
        for case in fixture["cases"]:
            attempts = []
            for query in case["queries"]:
                ranked = module.rank(root, query, top=None)
                files = list(dict.fromkeys(c["file"] for _, c, _ in ranked))
                text = module.render_brief(root, query, budget) or ""
                excerpts = source_excerpts(text)
                want = case.get("expected")
                routed = {c["file"] for _, c, _ in ranked if c.get("via_symptom")}
                attempts.append({"query": query, "target_rank": files.index(want) + 1 if want in files else 0,
                                 "target_excerpt": want in excerpts,
                                 "answer_evidence": want in excerpts and bool(case.get("evidence")) and all(
                                     span in excerpts.get(want, "") for span in case.get("evidence", [])),
                                 "routed_files": sorted(routed),
                                 "unexpected_routed_files": sorted(routed - {want}),
                                 "within_cap": len(text) <= budget})
            rows.append({**case, "attempts": attempts})
    summary = {}
    for split in dict.fromkeys(row["split"] for row in rows):
        group = [r for r in rows if r["split"] == split]
        summary[split] = {"cases": len(group),
                          "first_query_evidence": sum(r["attempts"][0]["answer_evidence"] for r in group),
                          "any_query_evidence": sum(any(a["answer_evidence"] for a in r["attempts"]) for r in group),
                          "strong_routing_cases": sum(any(a["routed_files"] for a in r["attempts"]) for r in group),
                          "unexpected_routing_cases": sum(any(a["unexpected_routed_files"] for a in r["attempts"]) for r in group)}
    return {"scope": fixture["scope"], "queries_provenance": "fixed authored queries, not agent-generated",
            "module_sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "budget": budget, "summary": summary, "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, default=Path(__file__).resolve().parents[1] / "memory_use.py")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures/symptom-questions.json")
    parser.add_argument("--max", type=int, default=1500)
    args = parser.parse_args()
    if args.max < 128:
        parser.error("--max must be at least 128")
    print(json.dumps(run(args.module, args.fixture, args.max), ensure_ascii=False, indent=2))
