"""Transfer the frozen rankers to six public QuixBugs faults and tests."""
import argparse
import ast
import difflib
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ssmq import blame_bench as bench

DATA = ROOT / "data/quixbugs"
NAMES = ("bucketsort", "gcd", "knapsack", "lis", "max_sublist_sum", "possible_change")
MODULE = "pkg/algorithms.py"
RUN_ID = "v6_exp12_quixbugs"


def function_source(family, name):
    text = (DATA / family / (name + ".py")).read_text()
    fn, = [node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)]
    return ast.get_source_segment(text, fn)


def make_adapter():
    bodies, tests, edits, counts = [], [], {}, {}
    for name in NAMES:
        good = function_source("correct_python_programs", name)
        bad = function_source("python_programs", name)
        bodies.append(good)
        lines = good.splitlines()
        newer = bad.splitlines()
        ops = [(i, j, k, l) for op, i, j, k, l in difflib.SequenceMatcher(None, lines, newer).get_opcodes() if op != "equal"]
        (i, j, k, l), = ops
        edits[name] = ("\n".join(lines[i:j]), "\n".join(newer[k:l]))
        cases = [json.loads(line) for line in (DATA / "json_testcases" / (name + ".json")).read_text().splitlines() if line.strip()]
        # Bound tabulation memory independent of outcomes. The largest upstream capacity exceeds six million.
        eligible = [(args, value) for args, value in cases if name != "knapsack" or args[0] <= 1000]
        counts[name] = {"eligible": len(eligible), "upstream": len(cases)}
        tests.append(f"    def test_{name}(self):\n        for args, expected in {eligible!r}:\n            with self.subTest(args=args):\n                self.assertEqual({name}(*args), expected)")
    test_source = "import unittest\nfrom pkg.algorithms import " + ", ".join(NAMES) + "\nclass Vectors(unittest.TestCase):\n" + "\n".join(tests)
    source = {"pkg/__init__.py": "", MODULE: "\n\n".join(bodies) + "\n", "tests/test_integration.py": test_source}
    return source, edits, counts


def evaluate():
    source, faults, counts = make_adapter()
    rows = []
    cache = {}

    def execute(changes):
        # Labels have no effect on execution. This memoizes identical case/complement trees.
        key = tuple(sorted(p.files[MODULE] for p in changes))
        if key not in cache:
            cache[key] = bench.execute_batch(changes)
        return cache[key]

    with patch.object(bench, "BASE_REPO", source):
        if not execute([])[0]:
            raise RuntimeError("Correct upstream functions failed their test vectors")
        for idx, name in enumerate(NAMES):
            sibling = NAMES[(idx + 1) % len(NAMES)]
            third = NAMES[(idx + 2) % len(NAMES)]
            for kind in ("sibling", "same_function", "two_faults"):
                names = [name] if kind != "two_faults" else [name, sibling]
                neutral = {"sibling": sibling, "same_function": name, "two_faults": third}[kind]
                old = function_source("correct_python_programs", neutral).splitlines()[0]
                neutral_edit = (old, old + "\n    pass")
                for reverse_ids in (False, True):
                    identifiers = ["a", "z"] if not reverse_ids else ["z", "a"]
                    candidates = [bench.PatchPR(identifiers[0], {MODULE: faults[name]}, name)]
                    if kind == "two_faults":
                        candidates.append(bench.PatchPR("m", {MODULE: faults[sibling]}, sibling))
                    candidates.append(bench.PatchPR(identifiers[1], {MODULE: neutral_edit}, neutral))
                    passed, trace = execute(candidates)
                    if passed:
                        raise RuntimeError(f"Failed to expose {name} ({kind})")
                    results = {}
                    for signal, text in (("full", trace), ("absent", "")):
                        for ranker, rank in (("ast", bench.rank_ast_stack_causal), ("file", bench.rank_file_trivia_filtered)):
                            ids = rank(candidates, text)
                            remaining = [pr for pr in candidates if pr.pr_id != ids[0]]
                            recovered = execute(remaining)[0]
                            results[signal + "_" + ranker] = {"ranking": ids, "top1_fault": ids[0] in [p.pr_id for p in candidates[:-1]], "complement_pass": recovered}
                    rows.append({"faults": names, "kind": kind, "neutral_function": neutral,
                                 "fault_ids": [p.pr_id for p in candidates[:-1]], "neutral_id": identifiers[1],
                                 "failing_tests": sorted(set(re.findall(r"(?:FAIL|ERROR): (test_\w+)", trace))),
                                 "outcomes": results})
    return {"vectors": counts, "distinct_executed_trees": len(cache), "records": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=ROOT/"runs")
    args = parser.parse_args()
    destination = args.runs_dir / RUN_ID
    if destination.exists():
        parser.error("Run already exists; choose a fresh --runs-dir")
    paths = [Path(__file__), ROOT/"src/ssmq/blame_bench.py"] + sorted(p for p in DATA.rglob("*") if p.is_file())
    hashes = {p.resolve().relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    payload = evaluate()
    data = (json.dumps(payload, indent=2) + "\n").encode()
    artifact = hashlib.sha256(data).hexdigest()
    groups = lambda group: hashlib.sha256(json.dumps(group, sort_keys=True).encode()).hexdigest()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    manifest = dict(schema_version="6.0", run_id=RUN_ID, experiment=RUN_ID, command="python3 eval/quixbugs_v6.py",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(), git_commit=commit.stdout.strip() or "anonymous-source",
                    git_version=subprocess.check_output(["git","--version"],text=True).strip(), python_version=platform.python_version(),
                    platform=platform.platform(), seeds=[], input_sha256=hashes, record_count=len(payload["records"]),
                    harness_sha256=groups(hashes), dataset_sha256=groups({k:v for k,v in hashes.items() if k.startswith("data/")}),
                    metrics_file="metrics.json", metrics_sha256=artifact, artifact_sha256=artifact)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        staging = Path(temp)/RUN_ID
        staging.mkdir()
        (staging/"metrics.json").write_bytes(data)
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staging.rename(destination)
    print(f"Sealed {RUN_ID}: {len(payload['records'])} cases")


if __name__ == "__main__":
    main()
