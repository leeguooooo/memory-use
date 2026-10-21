#!/usr/bin/env python3
"""Replay pinned upstream primitives against a public, synthetic corpus.

Download script_ngrams.py and fact_budget.py from the revisions recorded in
docs/deep-experiments.md into --upstream as basic.py and hindsight.py.
This does not benchmark either complete product or call any model/service.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


# These notes and questions are authored fixtures, not a user's private notes.
CORPUS = {
    "network/device.md": "# 新电脑接入\n新电脑接入私人网络，需要登记公钥、安装网络客户端。\n",
    "machines/desktop.md": "# 远程启动桌面程序\n终端启动的程序不在交互式桌面。通过计划任务启动桌面程序。\n",
    "machines/disk.md": "# 磁盘空间清理\n磁盘空间不足，先找缓存和临时目录，再清理磁盘空间。\n",
    "nas/camera.md": "# 相机上传失败\n相机上传照片失败，检查文件服务端口与上传目录权限。\n",
    "nas/security.md": "# 网络安全\n网络安全要求限制外网管理入口，保护登录密钥。\n",
}
CASES = [
    ("control", "新电脑接入", "network/device.md"),
    ("control", "启动桌面程序", "machines/desktop.md"),
    ("control", "磁盘空间", "machines/disk.md"),
    ("control", "相机上传", "nas/camera.md"),
    ("control", "网络安全", "nas/security.md"),
    ("paraphrase", "换了电脑怎么接入私人网络", "network/device.md"),
    ("paraphrase", "为什么终端启动的程序没有出现在桌面", "machines/desktop.md"),
    ("paraphrase", "磁盘快满了应该先清理什么", "machines/disk.md"),
    ("paraphrase", "为什么相机传不了照片", "nas/camera.md"),
    ("paraphrase", "外网管理入口应该怎么限制", "nas/security.md"),
    ("unanswerable", "新电脑接入的发票怎么报销", None),
    ("unanswerable", "桌面程序开发的合同怎么续签", None),
    ("unanswerable", "磁盘空间的采购预算怎么审批", None),
    ("unanswerable", "相机上传照片的版权怎么登记", None),
    ("unanswerable", "网络安全培训的费用怎么报销", None),
]


def run(upstream):
    basic = module("upstream_script_ngrams", upstream / "basic.py")
    hindsight = module("upstream_fact_budget", upstream / "hindsight.py")
    own = module("memory_use_probe", Path(__file__).resolve().parents[1] / "memory_use.py")
    with tempfile.TemporaryDirectory(prefix="recall-probe-") as temp:
        root = Path(temp)
        for name, text in CORPUS.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        records = []
        for kind, query, expected in CASES:
            # Run Basic Memory's real analyzer/index builder; check its ordered
            # gram phrase channel only. This is not its complete SQL search.
            analysis = basic.analyze_script_query(query)
            ordered = [name for name, text in CORPUS.items()
                       if analysis.gram_phrases and all(
                           " ".join(phrase) in basic.build_script_ngrams(text)
                           for phrase in analysis.gram_phrases)]
            # A deliberately simple candidate: unordered bigram overlap >=25%.
            # It is our ablation, NOT Basic Memory's implementation.
            grams = {g for phrase in analysis.gram_phrases for g in phrase}
            overlap = []
            for name, text in CORPUS.items():
                matched = sum(g in text for g in grams)
                score = matched / len(grams) if grams else 0
                if score >= 0.25:
                    overlap.append((score, name))
            overlap.sort(key=lambda row: (-row[0], row[1]))
            literal = list(dict.fromkeys(c["file"] for _, c, _ in own.rank(root, query.split(), top=None)))
            records.append({"kind": kind, "query": query, "expected": expected,
                            "literal": literal, "ordered_gram_channel": ordered,
                            "naive_overlap": [name for _, name in overlap]})
        metrics = {}
        for strategy in ("literal", "ordered_gram_channel", "naive_overlap"):
            metrics[strategy] = {}
            for kind in ("control", "paraphrase", "unanswerable"):
                rows = [r for r in records if r["kind"] == kind]
                metrics[strategy][kind] = {
                    "cases": len(rows),
                    "target_present": sum(r["expected"] in r[strategy] for r in rows if r["expected"]),
                    "nonempty_results": sum(bool(r[strategy]) for r in rows),
                }
    budget_cases = {}
    for name, texts, budget in (
        ("skip_oversize", {"long": "x" * 1000, "short1": "x" * 40, "short2": "x" * 40}, 120),
        ("all_oversize", {"long": "x" * 1000}, 120),
        ("zero_budget", {"long": "x" * 1000}, 0),
    ):
        result = hindsight.select_facts_within_budget(
            fact_ids_ordered=list(texts), text_by_id=texts, max_tokens=budget, count_tokens=len)
        budget_cases[name] = {"selected": result.ids, "measured_units": result.total_tokens,
                              "budget_units": budget, "truncated": result.truncated}
    return {"scope": "isolated primitives; synthetic cases; not end-to-end product quality",
            "memory_use_sha256": hashlib.sha256(Path(own.__file__).read_bytes()).hexdigest(),
            "sources_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (upstream / "basic.py", upstream / "hindsight.py")},
            "metrics": metrics, "cases": records, "hindsight_budget": budget_cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.upstream), ensure_ascii=False, indent=2))
