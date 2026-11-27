#!/usr/bin/env python3
"""Deterministic paired summary and LaTeX sensitivity table for runs/v9_sensitivity/.

Reads sealed runs/v9_sensitivity/{manifest.json, metrics.json} and emits byte-for-byte
deterministic artifacts:
  - runs/v9_sensitivity/summary.json
  - runs/v9_sensitivity/tables_v9_sensitivity.tex
  - paper/generated/tables/v9_sensitivity.tex (unless --skip-paper-table)
"""

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

from eval.sensitivity_v9.harness_v9_sensitivity import (  # noqa: E402
    CONDITION_META,
    PROTOCOL_NOTES,
    RUN_ID,
    STRATEGIES,
)

BOOTSTRAP_SEED = 11629
BOOTSTRAP_DRAWS = 10000

FIELDS: Tuple[str, ...] = (
    "p95_latency_min",
    "p50_latency_min",
    "throughput_prs_per_hr",
    "merged_prs",
    "rejected_prs",
    "rejected_defective",
    "rejected_persistent_conflict",
    "rejected_false_flaky",
    "false_rejection_rate",
    "mean_root_batch_size",
    "ci_minutes_per_merged_pr",
    "invalidation_rate",
    "stale_rechecks",
    "runner_utilization",
    "observed_graph_edge_density",
    "realized_graph_recall",
    "realized_graph_precision",
    "escaped_defects",
)

CONTRAST_METRICS: Tuple[str, ...] = (
    "p95_latency_min",
    "false_rejection_rate",
    "stale_rechecks",
    "merged_prs",
    "rejected_persistent_conflict",
    "ci_minutes_per_merged_pr",
    "mean_root_batch_size",
)


def load_sealed_rows(run_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    raw = (run_dir / "metrics.json").read_bytes()
    if sha256(raw).hexdigest() != manifest["artifact_sha256"]:
        raise ValueError(f"Invalid sealed artifact hash in {run_dir}")
    rows = json.loads(raw)
    if len(rows) != manifest["record_count"]:
        raise ValueError("Record count mismatch vs manifest")
    if any(int(r["escaped_defects"]) != 0 for r in rows):
        raise ValueError("Non-zero escaped_defects in sealed rows")
    return manifest, rows


def means(rows: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for field in FIELDS:
        values = [float(r[field]) for r in rows if r.get(field) is not None]
        out[field] = fmean(values) if values else None
    return out


def _bootstrap_blocks(by_repo: Dict[str, List[float]]) -> Dict[str, Any]:
    repos = sorted(by_repo)
    blocks = [fmean(pair) for pair in zip(*(by_repo[r] for r in repos))]
    rng = Random(BOOTSTRAP_SEED)
    draws = sorted(fmean(rng.choices(blocks, k=len(blocks))) for _ in range(BOOTSTRAP_DRAWS))
    n = sum(x != 0 for x in blocks)
    tail = min(sum(x > 0 for x in blocks), sum(x < 0 for x in blocks))
    sign_p = min(1.0, 2 * sum(comb(n, i) for i in range(tail + 1)) / (2**n)) if n else 1.0
    return {
        "difference": fmean(blocks),
        "ci95": [draws[249], draws[9749]],
        "sign_test_p": sign_p,
        "by_repo": {repo: fmean(by_repo[repo]) for repo in repos},
        "n_blocks": len(blocks),
        "n_positive_blocks": sum(x > 0 for x in blocks),
        "n_negative_blocks": sum(x < 0 for x in blocks),
    }


def paired_contrast(
    rows: List[Dict[str, Any]],
    left_strategy: str,
    right_strategy: str,
    metric: str,
) -> Dict[str, Any]:
    """Paired seed-block bootstrap matching eval/baselines_v8/export_v8_baselines.py."""
    groups: Dict[str, Dict[Tuple[str, int], float]] = defaultdict(dict)
    for row in rows:
        key = (row["repo"], int(row["seed"]))
        if key in groups[row["strategy"]]:
            raise ValueError(f"Duplicate strategy within paired block: {key}")
        groups[row["strategy"]][key] = float(row[metric])
    left, right = groups[left_strategy], groups[right_strategy]
    if not left or left.keys() != right.keys():
        raise ValueError(f"Incomplete paired contrast: {left_strategy} vs {right_strategy}")
    repos = sorted({repo for repo, _ in left})
    seeds = sorted({seed for _, seed in left})
    by_repo = {repo: [left[repo, seed] - right[repo, seed] for seed in seeds] for repo in repos}
    stats = _bootstrap_blocks(by_repo)
    return {
        "left": left_strategy,
        "right": right_strategy,
        "metric": metric,
        "definition": f"{left_strategy} minus {right_strategy}",
        **stats,
    }


def paired_shift_of_gap(
    rows_target: List[Dict[str, Any]],
    rows_anchor: List[Dict[str, Any]],
    left_strategy: str,
    right_strategy: str,
    metric: str,
) -> Dict[str, Any]:
    """Paired difference-in-differences: (left - right)_target minus (left - right)_anchor."""
    def _index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, int], float]:
        idx: Dict[Tuple[str, str, int], float] = {}
        for r in rows:
            idx[(r["strategy"], r["repo"], int(r["seed"]))] = float(r[metric])
        return idx

    t_idx = _index(rows_target)
    a_idx = _index(rows_anchor)
    repos = sorted({r["repo"] for r in rows_target})
    seeds = sorted({int(r["seed"]) for r in rows_target})
    by_repo: Dict[str, List[float]] = {}
    for repo in repos:
        diffs: List[float] = []
        for seed in seeds:
            gap_t = t_idx[(left_strategy, repo, seed)] - t_idx[(right_strategy, repo, seed)]
            gap_a = a_idx[(left_strategy, repo, seed)] - a_idx[(right_strategy, repo, seed)]
            diffs.append(gap_t - gap_a)
        by_repo[repo] = diffs
    stats = _bootstrap_blocks(by_repo)
    return {
        "left": left_strategy,
        "right": right_strategy,
        "metric": metric,
        "definition": f"({left_strategy} - {right_strategy})_target minus ({left_strategy} - {right_strategy})_default",
        **stats,
    }


def _fmt_sign_p(p: float) -> str:
    if p < 0.001:
        return "<0.001"
    if p < 0.01:
        return f"{p:.4f}"
    return f"{p:.3f}"


def build_summary(manifest: Dict[str, Any], all_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_cond_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        by_cond_rows[row["condition"]].append(row)

    summary: Dict[str, Any] = {
        "schema_version": "9.0",
        "run_id": RUN_ID,
        "artifact_sha256": manifest["artifact_sha256"],
        "record_count": manifest["record_count"],
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "default_parity": manifest.get("default_parity"),
        "protocol_notes": PROTOCOL_NOTES,
        "conditions": {},
        "gap_shifts_vs_default": {},
    }

    for cond in CONDITION_META:
        rows = by_cond_rows[cond]
        repos = sorted({r["repo"] for r in rows})
        by_strategy = {s: means([r for r in rows if r["strategy"] == s]) for s in STRATEGIES}
        by_repo_strategy = {
            repo: {
                s: means([r for r in rows if r["repo"] == repo and r["strategy"] == s])
                for s in STRATEGIES
            }
            for repo in repos
        }
        fc_minus_nc = {
            metric: paired_contrast(rows, "SSMQ-FilteredCone", "SSMQ-NoCone", metric)
            for metric in CONTRAST_METRICS
        }
        summary["conditions"][cond] = {
            "meta": CONDITION_META[cond],
            "by_strategy": by_strategy,
            "by_repo_strategy": by_repo_strategy,
            "fc_minus_nc": fc_minus_nc,
        }

    # Difference-in-differences relative to each load's default anchor (12m, 3% persistent)
    shift_pairs = (
        ("nominal_ci16", "nominal_default"),
        ("nominal_ci20", "nominal_default"),
        ("nominal_persist10", "nominal_default"),
        ("nominal_persist25", "nominal_default"),
        ("saturated_ci16", "saturated_default"),
        ("saturated_ci20", "saturated_default"),
        ("saturated_persist10", "saturated_default"),
        ("saturated_persist25", "saturated_default"),
    )
    for target_cond, anchor_cond in shift_pairs:
        summary["gap_shifts_vs_default"][target_cond] = {
            "anchor": anchor_cond,
            "p95_latency_min": paired_shift_of_gap(
                by_cond_rows[target_cond],
                by_cond_rows[anchor_cond],
                "SSMQ-FilteredCone",
                "SSMQ-NoCone",
                "p95_latency_min",
            ),
            "false_rejection_rate": paired_shift_of_gap(
                by_cond_rows[target_cond],
                by_cond_rows[anchor_cond],
                "SSMQ-FilteredCone",
                "SSMQ-NoCone",
                "false_rejection_rate",
            ),
            "stale_rechecks": paired_shift_of_gap(
                by_cond_rows[target_cond],
                by_cond_rows[anchor_cond],
                "SSMQ-FilteredCone",
                "SSMQ-NoCone",
                "stale_rechecks",
            ),
        }

    return summary


def build_latex_table(summary: Dict[str, Any]) -> str:
    conds = summary["conditions"]
    lines: List[str] = [
        "% AUTO-GENERATED BY eval/sensitivity_v9/export_v9_sensitivity.py -- DO NOT EDIT",
        r"\begin{table*}[t]\centering\scriptsize",
        (
            r"\caption{Paired same-family sensitivity of \texttt{SSMQ-FilteredCone} (FC) vs.\ \texttt{SSMQ-NoCone} (NC) "
            r"under frozen v8 unstacked exact-tree semantics ($W{=}8$, $K{\le}8$, fixed decoupled priors, zero escapes; "
            r"20 paired seeds $\times$ 3 repositories). "
            r"\textbf{Panel~A} varies mean CI job duration $\mu_{\text{CI}}\in\{12,16,20\}$~min ($\sigma_{\text{CI}}{=}2$~min, "
            r"persistent share $3\%$), bracketing observed TypeScript \texttt{merge\_group} workflow-run times "
            r"(P50/mean/P95 $= 16.0/16.1/18.8$~min, $n{=}45$; distinct from GraphQL queue sojourn, which includes queueing wait: "
            r"TS P50 $17.1$~min, Cargo P50 $34.2$~min). "
            r"\textbf{Panel~B} varies persistent-conflict share $p_{\text{pers}}\in\{3\%,10\%,25\%\}$ ($\mu_{\text{CI}}{=}12$~min) "
            r"with identical defects, total conflict pairs, and fixed priors per seed; higher $p_{\text{pers}}$ increases "
            r"persistent rejections and reduces merged survivors (Acc.), so survivor P95 should be read alongside Acc. "
            r"All $\Delta$ columns report FC minus NC with 95\% paired seed-block bootstrap intervals and exact two-sided "
            r"sign-test $p$; default rows ($12$~min, $3\%$) match sealed v8 baselines identically.}"
            r"\label{tab:v9sensitivity}"
        ),
        r"\setlength{\tabcolsep}{3.1pt}",
        r"\resizebox{\linewidth}{!}{\begin{tabular}{lrrrrrrrr}\toprule",
        (
            r"Condition (Load) & P95 (FC/NC) & $\Delta$P95 [95\% CI] & $p_{\text{sign}}$ & "
            r"FRR (FC/NC, \%) & $\Delta$FRR [95\% CI] (pp) & Stale (FC/NC) & $\Delta$Stale [95\% CI] & Acc.\ (FC/NC) \\ \midrule"
        ),
    ]

    def _row(cond_key: str, label: str) -> str:
        entry = conds[cond_key]
        fc = entry["by_strategy"]["SSMQ-FilteredCone"]
        nc = entry["by_strategy"]["SSMQ-NoCone"]
        dp = entry["fc_minus_nc"]["p95_latency_min"]
        df = entry["fc_minus_nc"]["false_rejection_rate"]
        ds = entry["fc_minus_nc"]["stale_rechecks"]
        return (
            f"{label} & "
            f"{fc['p95_latency_min']:.1f}/{nc['p95_latency_min']:.1f} & "
            f"{dp['difference']:+.1f} [{dp['ci95'][0]:+.1f}, {dp['ci95'][1]:+.1f}] & "
            f"{_fmt_sign_p(dp['sign_test_p'])} & "
            f"{fc['false_rejection_rate']*100:.2f}/{nc['false_rejection_rate']*100:.2f} & "
            f"{df['difference']*100:+.2f} [{df['ci95'][0]*100:+.2f}, {df['ci95'][1]*100:+.2f}] & "
            f"{fc['stale_rechecks']:.0f}/{nc['stale_rechecks']:.0f} & "
            f"{ds['difference']:+.1f} [{ds['ci95'][0]:+.1f}, {ds['ci95'][1]:+.1f}] & "
            f"{fc['merged_prs']:.1f}/{nc['merged_prs']:.1f} \\\\"
        )

    lines.append(
        r"\multicolumn{9}{l}{\textit{Panel A: Longer CI duration ($\sigma_{\text{CI}}{=}2$~min, persistent share $3\%$; 12~min is v8 default)}} \\"
    )
    for cond_key, label in (
        ("nominal_default", r"$\mu_{\text{CI}}{=}12$~min (Nom.\ $5\times$, default)"),
        ("nominal_ci16", r"$\mu_{\text{CI}}{=}16$~min (Nom.\ $5\times$)"),
        ("nominal_ci20", r"$\mu_{\text{CI}}{=}20$~min (Nom.\ $5\times$)"),
        ("saturated_default", r"$\mu_{\text{CI}}{=}12$~min (Sat.\ $15\times$, default)"),
        ("saturated_ci16", r"$\mu_{\text{CI}}{=}16$~min (Sat.\ $15\times$)"),
        ("saturated_ci20", r"$\mu_{\text{CI}}{=}20$~min (Sat.\ $15\times$)"),
    ):
        lines.append(_row(cond_key, label))

    lines.append(r"\midrule")
    lines.append(
        r"\multicolumn{9}{l}{\textit{Panel B: Higher persistent-conflict share ($\mu_{\text{CI}}{=}12$~min, fixed conflicts/priors; 3\% is v8 default)}} \\"
    )
    for cond_key, label in (
        ("nominal_default", r"$p_{\text{pers}}{=}3\%$ (Nom.\ $5\times$, default)"),
        ("nominal_persist10", r"$p_{\text{pers}}{=}10\%$ (Nom.\ $5\times$)"),
        ("nominal_persist25", r"$p_{\text{pers}}{=}25\%$ (Nom.\ $5\times$)"),
        ("saturated_default", r"$p_{\text{pers}}{=}3\%$ (Sat.\ $15\times$, default)"),
        ("saturated_persist10", r"$p_{\text{pers}}{=}10\%$ (Sat.\ $15\times$)"),
        ("saturated_persist25", r"$p_{\text{pers}}{=}25\%$ (Sat.\ $15\times$)"),
    ):
        lines.append(_row(cond_key, label))

    lines.append(r"\bottomrule\end{tabular}}\end{table*}")
    return "\n".join(lines) + "\n"


def export_all(
    run_dir: Path,
    output_dir: Optional[Path] = None,
    paper_table_path: Optional[Path] = None,
) -> List[Path]:
    dest = output_dir if output_dir is not None else run_dir
    dest.mkdir(parents=True, exist_ok=True)
    manifest, rows = load_sealed_rows(run_dir)
    summary = build_summary(manifest, rows)
    tex = build_latex_table(summary)

    outputs: Dict[Path, str] = {
        dest / "summary.json": json.dumps(summary, indent=2, allow_nan=False) + "\n",
        dest / "tables_v9_sensitivity.tex": tex,
    }
    if paper_table_path is not None:
        paper_table_path.parent.mkdir(parents=True, exist_ok=True)
        outputs[paper_table_path] = tex

    written: List[Path] = []
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=ROOT / "runs" / RUN_ID)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--paper-table",
        type=Path,
        default=ROOT / "paper" / "generated" / "tables" / "v9_sensitivity.tex",
    )
    parser.add_argument("--skip-paper-table", action="store_true")
    args = parser.parse_args()
    paper_path = None if args.skip_paper_table else args.paper_table
    written = export_all(args.run_dir, args.out_dir, paper_table_path=paper_path)
    print("Exported deterministic artifacts: " + ", ".join(str(p) for p in written))


if __name__ == "__main__":
    main()
