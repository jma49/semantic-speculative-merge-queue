#!/usr/bin/env python3
"""Deterministic paired summary, LaTeX tables, and case-level coverage sheet for runs/v8_baselines/."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from math import comb
from pathlib import Path
from random import Random
from statistics import fmean
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.baselines_v8.engine import ALL_V8_STRATEGIES  # noqa: E402
from eval.baselines_v8.specs import (  # noqa: E402
    INCLUDED_BASELINES,
    OMITTED_BASELINES,
    PROTOCOL_INVARIANTS,
)

RUN_ID = "v8_baselines"
BOOTSTRAP_SEED = 11629
BOOTSTRAP_DRAWS = 10000

FIELDS = (
    "p95_latency_min",
    "p50_latency_min",
    "throughput_prs_per_hr",
    "merged_prs",
    "ci_minutes_per_merged_pr",
    "false_rejection_rate",
    "mean_root_batch_size",
    "invalidation_rate",
    "stale_rechecks",
    "canceled_ci_minutes",
    "runner_utilization",
    "observed_graph_edge_density",
    "realized_graph_recall",
    "realized_graph_precision",
    "escaped_defects",
)

DISPLAY_ORDER: Tuple[Tuple[str, str], ...] = (
    ("FIFOOutcomeTree", "FIFO outcome tree (v7 stacked)"),
    ("PRediBatch-RiskGroup", "PRediBatch-inspired (stacked)"),
    ("SpeculativeLinear", "Linear spec. train (v7 stacked)"),
    ("GitHubMQ-StagedGroup", "GitHub MQ staged (ALLGREEN)"),
    ("Bors-BatchBisect", "Bors batch-bisect (1-staging)"),
    ("StaticBatchBisect", "FIFO batch + bisect (W=8, v7)"),
    ("Beheshtian-BatchStop4", "Beheshtian BatchStop4 (W=8)"),
    ("FIFOBatchRetry", "FIFO batch + retry (v7)"),
    ("SSMQ-NoCone", "SSMQ-NoCone (v7 control)"),
    ("SSMQ-FilteredCone", "SSMQ-FilteredCone (diagnostic)"),
    ("SSMQ", "SSMQ (v7 full, unfiltered)"),
    ("Aviator-DirAffected", "Aviator-inspired dir (exact-tree)"),
)


def load_sealed_rows(run_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    raw = (run_dir / "metrics.json").read_bytes()
    if sha256(raw).hexdigest() != manifest["artifact_sha256"]:
        raise ValueError(f"Invalid sealed artifact hash in {run_dir}")
    rows = json.loads(raw)
    if len(rows) != manifest["record_count"]:
        raise ValueError("Record count mismatch vs manifest")
    return manifest, rows


def means(rows: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for field in FIELDS:
        values = [r[field] for r in rows if r[field] is not None]
        out[field] = fmean(values) if values else None
    return out


def paired_contrast(
    rows: List[Dict[str, Any]],
    anchor: str,
    comparator: str,
    metric: str = "p95_latency_min",
) -> Dict[str, Any]:
    """Paired seed-block bootstrap matching v7 eval/sim_displays_v7.py exactly.

    Returns difference = comparator - anchor when anchor != 'SSMQ', or
    explicitly documents `left - right` via `left_minus_right`.
    """
    groups: Dict[str, Dict[Tuple[str, int], float]] = defaultdict(dict)
    for row in rows:
        key = (row["repo"], row["seed"])
        if key in groups[row["strategy"]]:
            raise ValueError("Duplicate strategy within paired block")
        groups[row["strategy"]][key] = row[metric]
    left, right = groups[anchor], groups[comparator]
    if not left or left.keys() != right.keys():
        raise ValueError(f"Incomplete paired contrast: {anchor} vs {comparator}")
    repos = sorted({repo for repo, _ in left})
    seeds = sorted({seed for _, seed in left})
    by_repo = {repo: [left[repo, seed] - right[repo, seed] for seed in seeds] for repo in repos}
    blocks = [fmean(pair) for pair in zip(*by_repo.values())]
    rng = Random(BOOTSTRAP_SEED)
    bootstrap = sorted(fmean(rng.choices(blocks, k=len(blocks))) for _ in range(BOOTSTRAP_DRAWS))
    n = sum(x != 0 for x in blocks)
    tail = min(sum(x > 0 for x in blocks), sum(x < 0 for x in blocks))
    sign_p = min(1.0, 2 * sum(comb(n, i) for i in range(tail + 1)) / (2**n)) if n else 1.0
    return {
        "anchor": anchor,
        "comparator": comparator,
        "definition": f"{anchor} minus {comparator}",
        "difference": fmean(blocks),
        "ci95": [bootstrap[249], bootstrap[9749]],
        "sign_test_p": sign_p,
        "by_repo": {repo: fmean(values) for repo, values in by_repo.items()},
    }


def build_summary(manifest: Dict[str, Any], all_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    conditions = sorted({r["condition"] for r in all_rows})
    summary: Dict[str, Any] = {
        "schema_version": "8.0",
        "run_id": RUN_ID,
        "artifact_sha256": manifest["artifact_sha256"],
        "record_count": manifest["record_count"],
        "v7_control_parity": manifest.get("v7_control_parity"),
        "protocol_invariants": PROTOCOL_INVARIANTS,
        "conditions": {},
    }
    for cond in conditions:
        rows = [r for r in all_rows if r["condition"] == cond]
        strategies = sorted({r["strategy"] for r in rows})
        repos = sorted({r["repo"] for r in rows})
        by_strategy = {s: means([r for r in rows if r["strategy"] == s]) for s in strategies}
        by_repo_strategy = {
            repo: {
                s: means([r for r in rows if r["repo"] == repo and r["strategy"] == s])
                for s in strategies
            }
            for repo in repos
        }
        # Follow v7 convention: delta = anchor - comparator (so for anchor="SSMQ", positive => SSMQ slower).
        vs_ssmq = {
            s: paired_contrast(rows, "SSMQ", s, "p95_latency_min")
            for s in strategies
            if s != "SSMQ"
        }
        vs_nocone = {
            s: paired_contrast(rows, s, "SSMQ-NoCone", "p95_latency_min")
            for s in strategies
            if s != "SSMQ-NoCone"
        }
        vs_tree = {
            s: paired_contrast(rows, s, "FIFOOutcomeTree", "p95_latency_min")
            for s in strategies
            if s != "FIFOOutcomeTree"
        }
        vs_static = {
            s: paired_contrast(rows, s, "StaticBatchBisect", "p95_latency_min")
            for s in ("Beheshtian-BatchStop4", "Bors-BatchBisect")
        }
        vs_linear = {
            s: paired_contrast(rows, s, "SpeculativeLinear", "p95_latency_min")
            for s in ("GitHubMQ-StagedGroup", "PRediBatch-RiskGroup", "Aviator-DirAffected")
        }
        summary["conditions"][cond] = {
            "by_strategy": by_strategy,
            "by_repo_strategy": by_repo_strategy,
            "p95_ssmq_minus_policy": vs_ssmq,
            "p95_policy_minus_nocone": vs_nocone,
            "p95_policy_minus_outcome_tree": vs_tree,
            "p95_policy_minus_static_bisect": vs_static,
            "p95_policy_minus_spec_linear": vs_linear,
        }
    return summary


def build_latex_tables(summary: Dict[str, Any]) -> str:
    conds = summary["conditions"]
    lines: List[str] = []

    # Table A: Main Nominal (5x) & Saturated (15x) baseline comparison under equal exact-tree authority
    lines.extend(
        [
            r"\begin{table*}[t]\centering\scriptsize",
            r"\caption{Batch 2 exact-tree baseline comparison on frozen v7 traces ($W{=}8$, $K{\le}8$, $D{\le}4$, decoupled generic priors, zero escapes across all policies). $\Delta_{\text{NoCone}}$ is policy minus \texttt{SSMQ-NoCone} in survivor P95 minutes (negative is faster), with 95\% paired seed-block bootstrap intervals across 20 seeds $\times$ 3 repositories. Width is mean root batch size; FRR is clean false-rejection rate; Acc.\ is mean accepted PRs; CI/m is runner-minutes per merge; Stale is mean exact-tree stale rechecks per run.}\label{tab:v8baselines}",
            r"\begin{tabular}{lrrrrrrrr}\toprule",
            r"Policy & P95 (min) & $\Delta_{\text{NoCone}}$ [95\% CI] & Width & FRR (\%) & Acc. & CI/m & Inv. (\%) & Stale \\ \midrule",
        ]
    )
    for idx, (cond, header) in enumerate(
        (
            ("nominal", r"\multicolumn{9}{l}{\textit{Nominal load ($5\times$, 30 PRs/hr, plausible effect: risk corr.=1.0, graph recall=0.80)}} \\"),
            ("saturated", r"\multicolumn{9}{l}{\textit{Saturated load ($15\times$, 90 PRs/hr, plausible effect: risk corr.=1.0, graph recall=0.80)}} \\"),
        )
    ):
        if idx > 0:
            lines.append(r"\midrule")
        lines.append(header)
        entry = conds[cond]
        for key, label in DISPLAY_ORDER:
            st = entry["by_strategy"][key]
            eff = entry["p95_policy_minus_nocone"].get(key)
            if eff is None:
                delta = "---"
            else:
                lo, hi = eff["ci95"]
                delta = f"{eff['difference']:+.1f} [{lo:+.1f}, {hi:+.1f}]"
            lines.append(
                f"{label} & {st['p95_latency_min']:.1f} & {delta} & "
                f"{st['mean_root_batch_size']:.2f} & {st['false_rejection_rate']*100:.2f} & "
                f"{st['merged_prs']:.1f} & {st['ci_minutes_per_merged_pr']:.1f} & "
                f"{st['invalidation_rate']*100:.1f} & {st['stale_rechecks']:.1f}" + r" \\"
            )
    lines.append(r"\bottomrule\end{tabular}\end{table*}")

    # Table B: Non-circular Directory Proxy (effect / risk-null) and Uniform Null sensitivity
    lines.extend(
        [
            "",
            r"\begin{table*}[t]\centering\scriptsize",
            r"\caption{Non-circular directory-proxy (conflicts from evaluation directories, uniform unary risk) and structural/risk null (uniform conflict pairs, uniform unary risk) regimes at nominal load ($5\times$, $W{=}8$, exact-tree commit, zero escapes). $\Delta_{\text{NoCone}}$ is policy minus \texttt{SSMQ-NoCone} (minutes).}\label{tab:v8null}",
            r"\begin{tabular}{lrrrrrrr}\toprule",
            r"Policy & P95 (min) & $\Delta_{\text{NoCone}}$ [95\% CI] & Width & FRR (\%) & CI/m & Stale & $D$ (\%) \\ \midrule",
        ]
    )
    for idx, (cond, header) in enumerate(
        (
            ("directory_proxy", r"\multicolumn{8}{l}{\textit{Directory proxy (independent evaluation-directory conflicts, risk corr.=0.0)}} \\"),
            ("null_uniform", r"\multicolumn{8}{l}{\textit{Full null (uniform random conflict pairs, risk corr.=0.0)}} \\"),
        )
    ):
        if idx > 0:
            lines.append(r"\midrule")
        lines.append(header)
        entry = conds[cond]
        for key, label in DISPLAY_ORDER:
            st = entry["by_strategy"][key]
            eff = entry["p95_policy_minus_nocone"].get(key)
            if eff is None:
                delta = "---"
            else:
                lo, hi = eff["ci95"]
                delta = f"{eff['difference']:+.1f} [{lo:+.1f}, {hi:+.1f}]"
            lines.append(
                f"{label} & {st['p95_latency_min']:.1f} & {delta} & "
                f"{st['mean_root_batch_size']:.2f} & {st['false_rejection_rate']*100:.2f} & "
                f"{st['ci_minutes_per_merged_pr']:.1f} & {st['stale_rechecks']:.1f} & "
                f"{st['observed_graph_edge_density']*100:.1f}" + r" \\"
            )
    lines.append(r"\bottomrule\end{tabular}\end{table*}")

    # Table C: Per-repository heterogeneity & Filtered-Cone diagnostic
    lines.extend(
        [
            "",
            r"\begin{table}[t]\centering\scriptsize",
            r"\caption{Per-repository survivor P95 (min) and predictor edge density $D$ (\%) at nominal load ($5\times$, exact-tree commit).}\label{tab:v8perrepo}",
            r"\setlength{\tabcolsep}{3.0pt}",
            r"\begin{tabular}{lrrr}\toprule",
            r"Policy / Diagnostic & \texttt{k8s} & \texttt{TS} & \texttt{next.js} \\ \midrule",
        ]
    )
    nom_r = conds["nominal"]["by_repo_strategy"]
    for label, strat in (
        (r"Unfiltered cone density $D$ (\%)", "SSMQ"),
        (r"Filtered cone density $D$ (\%)", "SSMQ-FilteredCone"),
        (r"Direct 2-level dir density $D$ (\%)", "Aviator-DirAffected"),
    ):
        lines.append(
            f"{label} & "
            f"{nom_r['kubernetes'][strat]['observed_graph_edge_density']*100:.1f} & "
            f"{nom_r['typescript'][strat]['observed_graph_edge_density']*100:.1f} & "
            f"{nom_r['nextjs'][strat]['observed_graph_edge_density']*100:.1f}" + r" \\"
        )
    lines.append(r"\midrule")
    for key, label in DISPLAY_ORDER:
        lines.append(
            f"{label} & "
            f"{nom_r['kubernetes'][key]['p95_latency_min']:.1f} & "
            f"{nom_r['typescript'][key]['p95_latency_min']:.1f} & "
            f"{nom_r['nextjs'][key]['p95_latency_min']:.1f}" + r" \\"
        )
    lines.append(r"\bottomrule\end{tabular}\end{table}")
    return "\n".join(lines) + "\n"


def build_coverage_sheet_md() -> str:
    lines: List[str] = [
        "# Batch 2 Baseline Coverage Sheet (`runs/v8_baselines`)",
        "",
        "## Equal Experimental Protocol Invariants",
        "",
        f"- **Commit authority**: {PROTOCOL_INVARIANTS['commit_authority']}",
        f"- **Capacity**: `workers={PROTOCOL_INVARIANTS['capacity']['workers']}`, "
        f"`max_batch_size={PROTOCOL_INVARIANTS['capacity']['max_batch_size']}`, "
        f"`max_spec_depth={PROTOCOL_INVARIANTS['capacity']['max_spec_depth']}`, "
        f"`ci_duration_min={PROTOCOL_INVARIANTS['capacity']['ci_duration_min']}`, "
        f"`ci_duration_std={PROTOCOL_INVARIANTS['capacity']['ci_duration_std']}`.",
        f"- **Traces & arrivals**: {PROTOCOL_INVARIANTS['arrival_and_traces']}",
        f"- **Priors & anti-leakage**: {PROTOCOL_INVARIANTS['priors_and_leakage']}",
        f"- **Uncertainty**: {PROTOCOL_INVARIANTS['uncertainty_protocol']}",
        f"- **No product speed claims**: {PROTOCOL_INVARIANTS['no_product_speed_claims']}",
        "",
        "## Included Baselines & Controls (12 Policies)",
        "",
        "| Strategy | Category | Primary Source | Exact-Tree | Failure Semantics | Disclosed Deviations |",
        "| :--- | :--- | :--- | :---: | :--- | :--- |",
    ]
    for item in INCLUDED_BASELINES:
        lines.append(
            f"| `{item['strategy']}` | {item['category']} | {item['primary_source']} | "
            f"{'Yes' if item['exact_tree_authority'] else 'No'} | {item['failure_semantics']} | "
            f"{item['disclosed_deviations']} |"
        )
    lines.extend(
        [
            "",
            "## Omitted Baselines (Case-Level Coverage & Rationale)",
            "",
            "| Omitted Baseline | Primary Source | Status | Scoped Representative in v8 | Case-Level Omission Rationale | What Would Be Needed |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for item in OMITTED_BASELINES:
        lines.append(
            f"| {item['baseline']} | {item['primary_source']} | `{item['status']}` | "
            f"{item['replacement_in_v8']} | {item['reason_for_omission']} | "
            f"{item['what_would_be_needed']} |"
        )
    return "\n".join(lines) + "\n"


def export_all(run_dir: Path, output_dir: Optional[Path] = None) -> List[Path]:
    dest = output_dir if output_dir is not None else run_dir
    dest.mkdir(parents=True, exist_ok=True)
    manifest, rows = load_sealed_rows(run_dir)
    summary = build_summary(manifest, rows)
    tex = build_latex_tables(summary)
    coverage_json = {
        "schema_version": "8.0",
        "run_id": RUN_ID,
        "protocol_invariants": PROTOCOL_INVARIANTS,
        "included_baselines": INCLUDED_BASELINES,
        "omitted_baselines": OMITTED_BASELINES,
    }
    coverage_md = build_coverage_sheet_md()

    outputs = {
        dest / "summary.json": json.dumps(summary, indent=2, allow_nan=False) + "\n",
        dest / "tables_v8_baselines.tex": tex,
        dest / "baseline_coverage_sheet.json": json.dumps(coverage_json, indent=2, allow_nan=False) + "\n",
        dest / "baseline_coverage_sheet.md": coverage_md,
    }
    written: List[Path] = []
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=ROOT / "runs" / RUN_ID)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    written = export_all(args.run_dir, args.out_dir)
    print("Exported deterministic artifacts: " + ", ".join(str(p) for p in written))


if __name__ == "__main__":
    main()
