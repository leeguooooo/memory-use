#!/usr/bin/env python3
"""Replay a large-first-note fixture against an old or new memory_use.py."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace


def present(text):
    result, current = set(), None
    for line in text.splitlines():
        match = re.match(r"^- (.+\.md):\d+ ‹.*›", line)
        if match:
            current = match[1]
        elif line.startswith("    ") and current:
            if line.strip() and not line.strip().startswith(("WARN ", "要点/key points:")):
                result.add(current)
        elif line.strip():
            current = None
    return sorted(result)


def run(path):
    spec = importlib.util.spec_from_file_location("probe_memory", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="context-probe-") as temp:
        root = Path(temp)
        os.environ["MEMORY_USE_DIR"] = str(root)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}
        subprocess.run(["git", "-C", str(root), "commit", "--allow-empty", "-qm", "fixture"], env=env, check=True)
        (root / "README.md").write_text("# Notes\n")
        (root / "nas").mkdir()
        (root / "nas/a.md").write_text("# Route\n\n**Key points**\n- route " + "x" * 3000 + "\n")
        (root / "nas/b.md").write_text("# Route recovery\n\n**Key points**\n- route fallback is port 2443\n")
        rows = []
        for budget in (128, 512, 1500, 6000):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                module.cmd_brief(SimpleNamespace(topic=["route"], max=budget, readme_lines=25))
            text = buf.getvalue().rstrip("\n")
            rows.append({"budget": budget, "length": len(text), "usable_files": present(text),
                         "within_cap": len(text) <= budget})
    return {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, default=Path(__file__).resolve().parents[1] / "memory_use.py")
    print(json.dumps(run(parser.parse_args().module), ensure_ascii=False, indent=2))
