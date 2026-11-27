#!/usr/bin/env python3
"""Deterministic manuscript display export for sealed v8 evidence (no external deps).

Reads sealed observations without modifying any sealed input or historical output:
  - runs/v8_baselines/{manifest.json, metrics.json}
  - runs/v8_queue_events/{manifest.json, metrics.json}
  - data/queue_events_v8/{descriptive_summary.json, historical_ci_selection_ledger.json}
  - runs/v8_historical_ci/{manifest.json, metrics.json}
  - data/historical_ci/descriptive_summary.json
  - results/sim_v7.json (for v7 recall-budget reference rows)

Emits byte-for-byte deterministic manuscript displays:
  - results/v8_paper.json
  - paper/generated/v8.tex
  - paper/generated/tables/v8_obs.tex
  - paper/generated/tables/v8_baselines_paper.tex
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
import json
from math import comb
from pathlib import Path
from random import Random
from statistics import fmean
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SEED = 11629
BOOTSTRAP_DRAWS = 10000


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo]))


def _paired_contrast(
    rows: List[Dict[str, Any]],
    left_strategy: str,
    right_strategy: str,
    metric: str,
) -> Dict[str, Any]:
    """Paired seed-block bootstrap matching eval/sim_displays_v7.py and export_v8_baselines.py."""
    groups: Dict[str, Dict[Tuple[str, int], float]] = defaultdict(dict)
    for row in rows:
        key = (row["repo"], int(row["seed"]))
        if key in groups[row["strategy"]]:
            raise ValueError(f"Duplicate block {key} for {row['strategy']}")
        groups[row["strategy"]][key] = float(row[metric])
    left = groups[left_strategy]
    right = groups[right_strategy]
    if not left or left.keys() != right.keys():
        raise ValueError(f"Incomplete paired blocks: {left_strategy} vs {right_strategy}")
    repos = sorted({repo for repo, _ in left})
    seeds = sorted({seed for _, seed in left})
    by_repo = {repo: [left[repo, seed] - right[repo, seed] for seed in seeds] for repo in repos}
    blocks = [fmean(pair) for pair in zip(*by_repo.values())]
    rng = Random(BOOTSTRAP_SEED)
    draws = sorted(fmean(rng.choices(blocks, k=len(blocks))) for _ in range(BOOTSTRAP_DRAWS))
    n = sum(x != 0 for x in blocks)
    tail = min(sum(x > 0 for x in blocks), sum(x < 0 for x in blocks))
    sign_p = min(1.0, 2 * sum(comb(n, i) for i in range(tail + 1)) / (2**n)) if n else 1.0
    return {
        "left": left_strategy,
        "right": right_strategy,
        "metric": metric,
        "difference": fmean(blocks),
        "ci95": [draws[249], draws[9749]],
        "sign_test_p": sign_p,
        "by_repo": {repo: fmean(vals) for repo, vals in by_repo.items()},
    }


def _strategy_means(rows: List[Dict[str, Any]], strategy: str) -> Dict[str, float]:
    sub = [r for r in rows if r["strategy"] == strategy]
    if not sub:
        raise ValueError(f"Missing rows for strategy {strategy}")
    fields = (
        "p95_latency_min",
        "mean_root_batch_size",
        "false_rejection_rate",
        "merged_prs",
        "ci_minutes_per_merged_pr",
        "invalidation_rate",
        "stale_rechecks",
        "observed_graph_edge_density",
        "realized_graph_recall",
        "realized_graph_precision",
        "escaped_defects",
    )
    out: Dict[str, float] = {}
    for f in fields:
        vals = [float(r[f]) for r in sub if r.get(f) is not None]
        out[f] = fmean(vals) if vals else 0.0
    return out


def _verify_and_load(root: Path) -> Dict[str, Any]:
    # 1. Sealed v8_baselines
    b_manifest = _read_json(root / "runs/v8_baselines/manifest.json")
    b_metrics_path = root / "runs/v8_baselines/metrics.json"
    if _digest(b_metrics_path) != b_manifest["artifact_sha256"]:
        raise ValueError("runs/v8_baselines/metrics.json SHA-256 mismatch")
    b_rows = _read_json(b_metrics_path)
    if len(b_rows) != b_manifest["record_count"]:
        raise ValueError("runs/v8_baselines record count mismatch")
    if any(int(r["escaped_defects"]) != 0 for r in b_rows):
        raise ValueError("Non-zero escaped_defects in runs/v8_baselines")

    # 2. Sealed v8_queue_events (schema 8.2 / descriptive summary 1.2)
    q_manifest = _read_json(root / "runs/v8_queue_events/manifest.json")
    q_metrics_path = root / "runs/v8_queue_events/metrics.json"
    if _digest(q_metrics_path) != q_manifest["metrics_sha256"]:
        raise ValueError("runs/v8_queue_events/metrics.json SHA-256 mismatch")
    q_metrics = _read_json(q_metrics_path)
    if q_metrics.get("schema_version") != "8.2":
        raise ValueError(f"Expected queue events schema 8.2, got {q_metrics.get('schema_version')}")
    q_summary = _read_json(root / "data/queue_events_v8/descriptive_summary.json")
    if q_summary.get("schema_version") != "1.2":
        raise ValueError(f"Expected descriptive_summary schema 1.2, got {q_summary.get('schema_version')}")
    q_ledger = _read_json(root / "data/queue_events_v8/historical_ci_selection_ledger.json")

    # 3. Sealed v8_historical_ci
    h_manifest = _read_json(root / "runs/v8_historical_ci/manifest.json")
    h_metrics_path = root / "runs/v8_historical_ci/metrics.json"
    if _digest(h_metrics_path) != h_manifest["metrics_sha256"]:
        raise ValueError("runs/v8_historical_ci/metrics.json SHA-256 mismatch")
    h_metrics = _read_json(h_metrics_path)
    h_summary = _read_json(root / "data/historical_ci/descriptive_summary.json")

    # 4. Sealed v7 recall summary (read-only reference)
    v7_sim = _read_json(root / "results/sim_v7.json")

    return {
        "baselines_rows": b_rows,
        "queue_metrics": q_metrics,
        "queue_summary": q_summary,
        "queue_ledger": q_ledger,
        "hist_metrics": h_metrics,
        "hist_summary": h_summary,
        "v7_sim": v7_sim,
    }


def _extract_queue_obs(
    q_metrics: Dict[str, Any],
    q_summary: Dict[str, Any],
    h_summary: Dict[str, Any],
) -> Dict[str, Any]:
    repos_out: Dict[str, Any] = {}
    for key in ("typescript", "cargo"):
        repo = q_summary["repositories"][key]
        raw_c = repo["raw_timeline_event_counts"]
        reasons = repo["removal_reason_histogram"]
        durs: List[float] = []
        multi_prs = 0
        max_ep = 0
        censored = 0
        for pull in repo["per_pull"]:
            eps = pull.get("episodes_in_observation_week") or []
            if len(eps) > 1:
                multi_prs += 1
            if len(eps) > max_ep:
                max_ep = len(eps)
            for ep in eps:
                flags = ep.get("observation_boundary_flags") or []
                if any(f != "fully_inside_window" for f in flags):
                    censored += 1
                if ep.get("added_at") and ep.get("removed_at"):
                    t0 = datetime.fromisoformat(ep["added_at"].replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(ep["removed_at"].replace("Z", "+00:00"))
                    durs.append((t1 - t0).total_seconds() / 60.0)
        durs.sort()
        non_merge = sum(v for k, v in reasons.items() if k != "merged")
        repos_out[key] = {
            "enumerated_ids": int(repo["enumerated_pr_count"]),
            "not_found": int(repo["pr_fetch_completeness"]["pr_not_found_count"]),
            "draft_excluded": int(repo["draft_excluded"]),
            "transport_failures": int(repo["pr_fetch_completeness"]["transport_failure_count"]),
            "prs_fetched": int(repo["pull_requests_fetched"]),
            "distinct_prs_enqueued": int(raw_c["distinct_prs_with_enqueue_in_week"]),
            "admissions": int(raw_c["added_to_merge_queue_in_week"]),
            "merged_removals": int(reasons.get("merged", 0)),
            "failed_checks": int(reasons.get("failed_checks", 0)),
            "merge_conflict": int(reasons.get("merge_conflict", 0)),
            "checks_timed_out": int(reasons.get("checks_timed_out", 0)),
            "non_merge_evictions": int(non_merge),
            "multi_enqueue_prs": int(multi_prs),
            "max_admissions_per_pr": int(max_ep),
            "censored_episodes": int(censored),
            "sojourn_n": len(durs),
            "sojourn_p50_min": _percentile(durs, 0.50),
            "sojourn_mean_min": fmean(durs) if durs else 0.0,
            "sojourn_p95_min": _percentile(durs, 0.95),
            "discordant_heads": int(repo["merge_group_crosswalk"]["discordant_head_count"]),
            "attribution_eligible": int(repo["merge_group_crosswalk"]["attribution_eligible_run_count"]),
        }

    wf = h_summary["merge_group_duration_minutes"]
    return {
        "schema_version": q_metrics["schema_version"],
        "summary_schema_version": q_summary["schema_version"],
        "calendar_days_inclusive": int(q_metrics["observation_calendar_days_inclusive"]),
        "window_start": q_summary["observation_week"]["start"],
        "window_end": q_summary["observation_week"]["end"],
        "combined_distinct_prs": int(q_metrics["combined_distinct_prs_enqueue_in_week"]),
        "combined_admissions": int(q_metrics["combined_queue_admissions_in_week"]),
        "combined_non_merge_evictions": (
            repos_out["typescript"]["non_merge_evictions"]
            + repos_out["cargo"]["non_merge_evictions"]
        ),
        "combined_multi_enqueue_prs": (
            repos_out["typescript"]["multi_enqueue_prs"]
            + repos_out["cargo"]["multi_enqueue_prs"]
        ),
        "repositories": repos_out,
        "ts_workflow_runs": {
            "api_scanned": int(q_metrics["historical_ci_scanned"]),
            "selected": int(q_metrics["historical_ci_selected"]),
            "excluded_pr_outside_window": int(q_metrics["historical_ci_excluded_pr_number_window"]),
            "discordant_heads": int(q_metrics["discordant_head_cases_historical_ci"]),
            "success": int(h_summary["merge_group_conclusions"].get("success", 0)),
            "failure": int(h_summary["merge_group_conclusions"].get("failure", 0)),
            "duration_n": int(wf["n"]),
            "duration_p50_min": float(wf["p50"]),
            "duration_mean_min": float(wf["mean"]),
            "duration_p95_min": float(wf["p95"]),
        },
    }


def _fmt_sign_p(p: float) -> str:
    if p < 0.001:
        return "<0.001"
    if p < 0.01:
        return f"{p:.4f}"
    return f"{p:.3f}"


def _build_obs_table(obs: Dict[str, Any]) -> str:
    ts = obs["repositories"]["typescript"]
    cg = obs["repositories"]["cargo"]
    wf = obs["ts_workflow_runs"]
    comb_fetched = ts["prs_fetched"] + cg["prs_fetched"]
    comb_evict = obs["combined_non_merge_evictions"]
    comb_multi = obs["combined_multi_enqueue_prs"]
    lines = [
        "% AUTO-GENERATED BY eval/export_v8_displays.py -- DO NOT EDIT",
        r"\begin{table}[t]\centering\scriptsize",
        r"\caption{Observed native GitHub Merge Queue events across 8 inclusive UTC dates (2026-11-10 to 2026-11-17, schema~8.2). Distinct enqueued PRs differ from total queue admissions when PRs re-enqueue after eviction. Top: GraphQL queue-timeline episodes (sojourn = enqueue to removal). Bottom: TypeScript \texttt{merge\_group} workflow-run duration sample (45 selected runs $\neq$ all 246 non-draft applicants; enqueue-time PR heads unavailable).}\label{tab:v8obs}",
        r"\setlength{\tabcolsep}{2.8pt}",
        r"\renewcommand{\arraystretch}{1.05}",
        r"\begin{tabular}{lrrr}\toprule",
        r"Observed Quantity & \texttt{TS} & \texttt{cargo} & Total \\ \midrule",
        f"IDs (non-draft PRs) & {ts['enumerated_ids']} ({ts['prs_fetched']}) & {cg['enumerated_ids']} ({cg['prs_fetched']}) & {ts['enumerated_ids']+cg['enumerated_ids']} ({comb_fetched}) \\\\",
        f"Unavailable \\texttt{{NOT\\_FOUND}} / drafts & {ts['not_found']}/{ts['draft_excluded']} & {cg['not_found']}/{cg['draft_excluded']} & {ts['not_found']+cg['not_found']}/{ts['draft_excluded']+cg['draft_excluded']} \\\\",
        f"Distinct PRs enqueued in window & {ts['distinct_prs_enqueued']} & {cg['distinct_prs_enqueued']} & {obs['combined_distinct_prs']} \\\\",
        f"Queue admissions (\\texttt{{ADDED}}) & {ts['admissions']} & {cg['admissions']} & {obs['combined_admissions']} \\\\",
        f"Non-merge evictions (re-enq.\\ PRs) & {ts['non_merge_evictions']} ({ts['multi_enqueue_prs']}) & {cg['non_merge_evictions']} ({cg['multi_enqueue_prs']}) & {comb_evict} ({comb_multi}) \\\\",
        f"Episode sojourn P50/P95 (min) & {ts['sojourn_p50_min']:.1f}/{ts['sojourn_p95_min']:.1f} & {cg['sojourn_p50_min']:.1f}/{cg['sojourn_p95_min']:.1f} & --- \\\\",
        f"Discordant heads (eligible runs) & {ts['discordant_heads']} ({ts['attribution_eligible']}) & {cg['discordant_heads']} ({cg['attribution_eligible']}) & {ts['discordant_heads']+cg['discordant_heads']} (0) \\\\",
        r"\addlinespace[2pt]",
        r"\midrule",
        f"\\multicolumn{{4}}{{@{{}}p{{\\linewidth}}@{{}}}}{{\\textit{{TS \\texttt{{merge\\_group}} runs (workflow ledger):}} {wf['selected']} selected ({wf['success']} pass, {wf['failure']} fail); {wf['api_scanned']} scanned, {wf['excluded_pr_outside_window']} PR\\# caveats; P50/mean/P95 = {wf['duration_p50_min']:.1f}/{wf['duration_mean_min']:.1f}/{wf['duration_p95_min']:.1f}~min.}} \\\\",
        r"\bottomrule\end{tabular}\end{table}",
    ]
    return "\n".join(lines) + "\n"


def _build_baselines_tables(
    by_cond: Dict[str, Any],
    fc_contrasts: Dict[str, Any],
    bs4_contrasts: Dict[str, Any],
    v7_sim: Dict[str, Any],
) -> str:
    lines: List[str] = ["% AUTO-GENERATED BY eval/export_v8_displays.py -- DO NOT EDIT"]

    # Table A: Primary NEW controlled contrast (SSMQ-FilteredCone vs SSMQ-NoCone) across all 4 regimes
    lines.extend(
        [
            r"\begin{table*}[t]\centering\scriptsize",
            r"\caption{Primary controlled cone-filtering contrast under identical unstacked exact-tree semantics ($W{=}8$, $K{\le}8$, decoupled generic priors, zero escapes; 20 paired seeds $\times$ 3 repos). \texttt{FilteredCone} drops warm-up mega-PRs ($>20$ files) and 2-level directory cliques, cutting edge density $D$ from 73.3\% to 10.8\%. All $\Delta$ columns report \texttt{SSMQ-FilteredCone} minus \texttt{SSMQ-NoCone} with 95\% paired seed-block bootstrap intervals and exact two-sided sign-test $p$; Unf.\ $\Delta$P95 shows unfiltered \texttt{SSMQ} minus \texttt{SSMQ-NoCone} for reference.}\label{tab:v8filtered}",
            r"\setlength{\tabcolsep}{3.4pt}",
            r"\renewcommand{\arraystretch}{1.08}",
            r"\resizebox{\linewidth}{!}{\begin{tabular}{@{}lrrrrrrrr@{}}\toprule",
            r"Regime & Width (FC/NC) & P95 (FC/NC) & $\Delta$P95 [95\% CI] & $p_{\text{sign}}$ & FRR (FC/NC, \%) & $\Delta$FRR [95\% CI] (pp) & Stale (FC/NC) & Unf.\ $\Delta$P95 \\ \midrule",
        ]
    )
    regime_labels = (
        ("nominal", r"Nominal ($5\times$, plausible)"),
        ("saturated", r"Saturated ($15\times$, plausible)"),
        ("directory_proxy", r"Directory proxy (non-circ.)"),
        ("null_uniform", r"Uniform null (risk/struct.)"),
    )
    for cond, label in regime_labels:
        fc = by_cond[cond]["by_strategy"]["SSMQ-FilteredCone"]
        nc = by_cond[cond]["by_strategy"]["SSMQ-NoCone"]
        dp = fc_contrasts[cond]["p95"]
        df = fc_contrasts[cond]["frr"]
        du = fc_contrasts[cond]["unfiltered_p95"]
        lines.append(
            f"{label} & {fc['mean_root_batch_size']:.2f}/{nc['mean_root_batch_size']:.2f} & "
            f"{fc['p95_latency_min']:.1f}/{nc['p95_latency_min']:.1f} & "
            f"{dp['difference']:+.1f} [{dp['ci95'][0]:+.1f}, {dp['ci95'][1]:+.1f}] & "
            f"{_fmt_sign_p(dp['sign_test_p'])} & "
            f"{fc['false_rejection_rate']*100:.2f}/{nc['false_rejection_rate']*100:.2f} & "
            f"{df['difference']*100:+.2f} [{df['ci95'][0]*100:+.2f}, {df['ci95'][1]*100:+.2f}] & "
            f"{fc['stale_rechecks']:.0f}/{nc['stale_rechecks']:.0f} & "
            f"{du['difference']:+.1f} \\\\"
        )
    lines.append(r"\bottomrule\end{tabular}}\end{table*}")

    # Table B: Grounded exact-tree comparators grouped by membership-partition class (NO cross-architecture causal deltas!)
    lines.extend(
        [
            "",
            r"\begin{table*}[t]\centering\scriptsize",
            r"\caption{Grounded exact-tree comparators on frozen traces ($W{=}8$, $K{\le}8$, $D{\le}4$, decoupled generic priors, zero escapes; 20 seeds $\times$ 3 repos). Policies are grouped by membership-partition class because transient pair faults (97\% of sampled pairs) trigger only among PRs co-placed in \texttt{job.members}, whereas persistent pairs (3\%) also trigger against \texttt{mainline} and speculative \texttt{prefix}; cross-family differences are therefore descriptive protocol profiles, not causal architecture rankings.}\label{tab:v8comparators}",
            r"\setlength{\tabcolsep}{3.2pt}",
            r"\renewcommand{\arraystretch}{1.04}",
            r"\resizebox{\linewidth}{!}{\begin{tabular}{@{}llrrrrrrrr@{}}\toprule",
            r"Policy & \texttt{members} Partition & \multicolumn{4}{c}{Nominal Load ($5\times$, 30~PRs/hr)} & \multicolumn{4}{c}{Saturated Load ($15\times$, 90~PRs/hr)} \\ \cmidrule(lr){3-6}\cmidrule(lr){7-10}",
            r" & & P95 & FRR (\%) & CI/m & Stale & P95 & FRR (\%) & CI/m & Stale \\ \midrule",
        ]
    )
    groups = (
        (
            r"\multicolumn{10}{l}{\textit{Family A: Unstacked multi-worker ($W{=}8$; co-batched \texttt{members}, exact-tree stale rechecks)}} \\",
            (
                ("StaticBatchBisect", "FIFO batch + bisect (v7)", "Multi-PR ($K{\\le}8$)"),
                ("Beheshtian-BatchStop4", "Beheshtian BatchStop4", "Multi-PR ($|B|{\\le}4\\to 1$)"),
                ("FIFOBatchRetry", "FIFO batch + retry (v7)", "Multi-PR ($K{\\le}8$)"),
                ("SSMQ-NoCone", "SSMQ-NoCone (v7 control)", "Multi-PR (risk lanes)"),
                ("SSMQ-FilteredCone", "SSMQ-FilteredCone (v8)", "Multi-PR (sparse cone)"),
                ("SSMQ", "SSMQ (v7 unfiltered)", "Multi-PR (full cone)"),
            ),
        ),
        (
            r"\multicolumn{10}{l}{\textit{Family B: Unstacked single-staging ($W{=}1$ active staging commit; zero stale rechecks)}} \\",
            (
                ("Bors-BatchBisect", "Historical Bors batch-bisect", "Multi-PR (1-staging)"),
            ),
        ),
        (
            r"\multicolumn{10}{l}{\textit{Family C: Single-timeline stacked (zero stale rechecks; \texttt{members} vs.\ \texttt{prefix} varies by policy)}} \\",
            (
                ("FIFOOutcomeTree", "FIFO outcome tree (v7)", "Tree node + prefix"),
                ("GitHubMQ-StagedGroup", "GitHub MQ staged (ALLGREEN)", "1-PR + prefix"),
                ("PRediBatch-RiskGroup", "PRediBatch-inspired (risk)", "Risk batch + prefix"),
                ("SpeculativeLinear", "Linear spec.\\ train (v7)", "Cumul.\\ prefix"),
            ),
        ),
        (
            r"\multicolumn{10}{l}{\textit{Family D: Multi-lane exact-tree (2-level directory lanes; cross-lane stale rechecks)}} \\",
            (
                ("Aviator-DirAffected", "Aviator-inspired dir (exact)", "1-PR + lane prefix"),
            ),
        ),
    )
    nom = by_cond["nominal"]["by_strategy"]
    sat = by_cond["saturated"]["by_strategy"]
    for idx, (hdr, items) in enumerate(groups):
        if idx > 0:
            lines.append(r"\midrule")
        lines.append(hdr)
        for key, label, part in items:
            n_s = nom[key]
            s_s = sat[key]
            lines.append(
                f"{label} & {part} & "
                f"{n_s['p95_latency_min']:.1f} & {n_s['false_rejection_rate']*100:.2f} & "
                f"{n_s['ci_minutes_per_merged_pr']:.1f} & {n_s['stale_rechecks']:.1f} & "
                f"{s_s['p95_latency_min']:.1f} & {s_s['false_rejection_rate']*100:.2f} & "
                f"{s_s['ci_minutes_per_merged_pr']:.1f} & {s_s['stale_rechecks']:.1f} \\\\"
            )
    lines.append(r"\bottomrule\end{tabular}}\end{table*}")

    # Table C: Compact single-column table combining per-repository cone density / filtered contrast + v7 recall sweep
    nom_r = by_cond["nominal"]["by_repo_strategy"]
    rec = v7_sim["recall"]
    lines.extend(
        [
            "",
            r"\begin{table}[t]\centering\scriptsize",
            r"\caption{Per-repository cone density $D$ (\%) and nominal P95 (min) for unstacked cone controls; lower block lists the v7 fixed-budget recall sweep.}\label{tab:v8repo_recall}",
            r"\setlength{\tabcolsep}{2.4pt}",
            r"\renewcommand{\arraystretch}{1.06}",
            r"\resizebox{\linewidth}{!}{\begin{tabular}{@{}lrrr@{}}\toprule",
            r"Per-Repo Metric (Nom.\ $5\times$) & \texttt{k8s} & \texttt{TS} & \texttt{next.js} \\ \midrule",
            f"Cone $D$: Unf./FC (\\%) & {nom_r['kubernetes']['SSMQ']['observed_graph_edge_density']*100:.1f}/{nom_r['kubernetes']['SSMQ-FilteredCone']['observed_graph_edge_density']*100:.1f} & {nom_r['typescript']['SSMQ']['observed_graph_edge_density']*100:.1f}/{nom_r['typescript']['SSMQ-FilteredCone']['observed_graph_edge_density']*100:.1f} & {nom_r['nextjs']['SSMQ']['observed_graph_edge_density']*100:.1f}/{nom_r['nextjs']['SSMQ-FilteredCone']['observed_graph_edge_density']*100:.1f} \\\\",
            f"P95: Unf./FC/NC (m) & {nom_r['kubernetes']['SSMQ']['p95_latency_min']:.0f}/{nom_r['kubernetes']['SSMQ-FilteredCone']['p95_latency_min']:.0f}/{nom_r['kubernetes']['SSMQ-NoCone']['p95_latency_min']:.0f} & {nom_r['typescript']['SSMQ']['p95_latency_min']:.0f}/{nom_r['typescript']['SSMQ-FilteredCone']['p95_latency_min']:.0f}/{nom_r['typescript']['SSMQ-NoCone']['p95_latency_min']:.0f} & {nom_r['nextjs']['SSMQ']['p95_latency_min']:.0f}/{nom_r['nextjs']['SSMQ-FilteredCone']['p95_latency_min']:.0f}/{nom_r['nextjs']['SSMQ-NoCone']['p95_latency_min']:.0f} \\\\",
            r"\addlinespace[2pt]",
            r"\midrule",
            r"\multicolumn{4}{l}{\textit{Bottom: v7 fixed-budget recall sweep (unfiltered \texttt{SSMQ} minus \texttt{SSMQ-NoCone}; positive is slower).}} \\",
            r"Recall budget (Unf.$-$NC) & Rec./Prec. & $\Delta$P95 [95\% CI] & FRR (\%) \\ \midrule",
        ]
    )
    for key, label in (
        ("recall_0.0", r"Budget 0\%"),
        ("recall_0.4", r"Budget 40\%"),
        ("recall_0.8", r"Budget 80\%"),
        ("recall_1.0", r"Budget 100\%"),
    ):
        entry = rec[key]
        ss = entry["by_strategy"]["SSMQ"]
        nc = entry["by_strategy"]["SSMQ-NoCone"]
        d = entry["p95_difference"]["SSMQ-NoCone"]
        lines.append(
            f"{label} & {ss['realized_graph_recall']:.2f}/{ss['realized_graph_precision']:.2f} & "
            f"{d['difference']:+.1f} [{d['ci95'][0]:+.1f}, {d['ci95'][1]:+.1f}] & "
            f"{ss['false_rejection_rate']*100:.1f}/{nc['false_rejection_rate']*100:.1f} \\\\"
        )
    lines.append(r"\bottomrule\end{tabular}}\end{table}")

    return "\n".join(lines) + "\n"


def export_v8(root: Path, output=None) -> Dict[str, Any]:
    loaded = _verify_and_load(root)
    b_rows = loaded["baselines_rows"]
    obs = _extract_queue_obs(loaded["queue_metrics"], loaded["queue_summary"], loaded["hist_summary"])

    conditions = ("nominal", "saturated", "directory_proxy", "null_uniform")
    by_cond: Dict[str, Any] = {}
    fc_contrasts: Dict[str, Any] = {}
    bs4_contrasts: Dict[str, Any] = {}

    for cond in conditions:
        c_rows = [r for r in b_rows if r["condition"] == cond]
        strats = sorted({r["strategy"] for r in c_rows})
        repos = sorted({r["repo"] for r in c_rows})
        by_cond[cond] = {
            "by_strategy": {s: _strategy_means(c_rows, s) for s in strats},
            "by_repo_strategy": {
                repo: {s: _strategy_means([r for r in c_rows if r["repo"] == repo], s) for s in strats}
                for repo in repos
            },
        }
        fc_contrasts[cond] = {
            "p95": _paired_contrast(c_rows, "SSMQ-FilteredCone", "SSMQ-NoCone", "p95_latency_min"),
            "frr": _paired_contrast(c_rows, "SSMQ-FilteredCone", "SSMQ-NoCone", "false_rejection_rate"),
            "acc": _paired_contrast(c_rows, "SSMQ-FilteredCone", "SSMQ-NoCone", "merged_prs"),
            "ci": _paired_contrast(c_rows, "SSMQ-FilteredCone", "SSMQ-NoCone", "ci_minutes_per_merged_pr"),
            "stale": _paired_contrast(c_rows, "SSMQ-FilteredCone", "SSMQ-NoCone", "stale_rechecks"),
            "unfiltered_p95": _paired_contrast(c_rows, "SSMQ", "SSMQ-NoCone", "p95_latency_min"),
            "ssmq_minus_fc_p95": _paired_contrast(c_rows, "SSMQ", "SSMQ-FilteredCone", "p95_latency_min"),
        }
        bs4_contrasts[cond] = {
            "p95": _paired_contrast(c_rows, "Beheshtian-BatchStop4", "StaticBatchBisect", "p95_latency_min"),
            "frr": _paired_contrast(c_rows, "Beheshtian-BatchStop4", "StaticBatchBisect", "false_rejection_rate"),
            "ci": _paired_contrast(c_rows, "Beheshtian-BatchStop4", "StaticBatchBisect", "ci_minutes_per_merged_pr"),
        }

    ts = obs["repositories"]["typescript"]
    cg = obs["repositories"]["cargo"]
    wf = obs["ts_workflow_runs"]

    nom_fc = by_cond["nominal"]["by_strategy"]["SSMQ-FilteredCone"]
    nom_nc = by_cond["nominal"]["by_strategy"]["SSMQ-NoCone"]
    sat_fc = by_cond["saturated"]["by_strategy"]["SSMQ-FilteredCone"]
    dir_fc = by_cond["directory_proxy"]["by_strategy"]["SSMQ-FilteredCone"]
    nul_fc = by_cond["null_uniform"]["by_strategy"]["SSMQ-FilteredCone"]

    macros: Dict[str, str] = {
        # Observed queue events (schema 8.2)
        "ObsDays": str(obs["calendar_days_inclusive"]),
        "ObsDistinctPrs": str(obs["combined_distinct_prs"]),
        "ObsAdmissions": str(obs["combined_admissions"]),
        "ObsEvictions": str(obs["combined_non_merge_evictions"]),
        "ObsMultiPrs": str(obs["combined_multi_enqueue_prs"]),
        "ObsTsEnum": str(ts["enumerated_ids"]),
        "ObsTsFetched": str(ts["prs_fetched"]),
        "ObsTsNotFound": str(ts["not_found"]),
        "ObsTsDrafts": str(ts["draft_excluded"]),
        "ObsTsPrs": str(ts["distinct_prs_enqueued"]),
        "ObsTsAdmissions": str(ts["admissions"]),
        "ObsTsFailedChecks": str(ts["failed_checks"]),
        "ObsTsSojournP": f"{ts['sojourn_p50_min']:.1f}",
        "ObsTsSojournPnf": f"{ts['sojourn_p95_min']:.1f}",
        "ObsCargoEnum": str(cg["enumerated_ids"]),
        "ObsCargoFetched": str(cg["prs_fetched"]),
        "ObsCargoNotFound": str(cg["not_found"]),
        "ObsCargoDrafts": str(cg["draft_excluded"]),
        "ObsCargoPrs": str(cg["distinct_prs_enqueued"]),
        "ObsCargoAdmissions": str(cg["admissions"]),
        "ObsCargoEvictions": str(cg["non_merge_evictions"]),
        "ObsCargoFailedChecks": str(cg["failed_checks"]),
        "ObsCargoConflicts": str(cg["merge_conflict"]),
        "ObsCargoTimeouts": str(cg["checks_timed_out"]),
        "ObsCargoMultiPrs": str(cg["multi_enqueue_prs"]),
        "ObsCargoSojournP": f"{cg['sojourn_p50_min']:.1f}",
        "ObsCargoSojournPnf": f"{cg['sojourn_p95_min']:.1f}",
        "ObsCargoDiscordant": str(cg["discordant_heads"]),
        # Historical CI TS workflow-run duration sample
        "ObsTsWfScanned": str(wf["api_scanned"]),
        "ObsTsWfSelected": str(wf["selected"]),
        "ObsTsWfCaveats": str(wf["excluded_pr_outside_window"]),
        "ObsTsWfDiscordant": str(wf["discordant_heads"]),
        "ObsTsWfP": f"{wf['duration_p50_min']:.1f}",
        "ObsTsWfMean": f"{wf['duration_mean_min']:.1f}",
        "ObsTsWfPnf": f"{wf['duration_p95_min']:.1f}",
        # FilteredCone primary NEW contrast
        "UnfDensity": f"{by_cond['nominal']['by_strategy']['SSMQ']['observed_graph_edge_density']*100:.1f}",
        "FcDensity": f"{nom_fc['observed_graph_edge_density']*100:.1f}",
        "DirTargetDensity": f"{by_cond['nominal']['by_strategy']['Aviator-DirAffected']['observed_graph_edge_density']*100:.1f}",
        "FcNomWidth": f"{nom_fc['mean_root_batch_size']:.2f}",
        "FcNomP": f"{nom_fc['p95_latency_min']:.1f}",
        "FcNomFrr": f"{nom_fc['false_rejection_rate']*100:.2f}",
        "FcNomAcc": f"{nom_fc['merged_prs']:.1f}",
        "FcNomCi": f"{nom_fc['ci_minutes_per_merged_pr']:.1f}",
        "FcNomStale": f"{nom_fc['stale_rechecks']:.1f}",
        "FcNomRec": f"{nom_fc['realized_graph_recall']:.2f}",
        "FcDirRec": f"{dir_fc['realized_graph_recall']:.2f}",
        "FcNomDeltaP": f"{fc_contrasts['nominal']['p95']['difference']:+.1f}",
        "FcNomDeltaPLo": f"{fc_contrasts['nominal']['p95']['ci95'][0]:+.1f}",
        "FcNomDeltaPHi": f"{fc_contrasts['nominal']['p95']['ci95'][1]:+.1f}",
        "FcNomSignP": _fmt_sign_p(fc_contrasts["nominal"]["p95"]["sign_test_p"]),
        "FcNomDeltaFrr": f"{fc_contrasts['nominal']['frr']['difference']*100:+.2f}",
        "FcNomDeltaFrrLo": f"{fc_contrasts['nominal']['frr']['ci95'][0]*100:+.2f}",
        "FcNomDeltaFrrHi": f"{fc_contrasts['nominal']['frr']['ci95'][1]*100:+.2f}",
        "FcNomDeltaAcc": f"{fc_contrasts['nominal']['acc']['difference']:+.2f}",
        "FcNomGainVsUnf": f"{fc_contrasts['nominal']['ssmq_minus_fc_p95']['difference']:.1f}",
        "FcSatP": f"{sat_fc['p95_latency_min']:.1f}",
        "FcSatFrr": f"{sat_fc['false_rejection_rate']*100:.2f}",
        "FcSatDeltaP": f"{fc_contrasts['saturated']['p95']['difference']:+.1f}",
        "FcSatDeltaPLo": f"{fc_contrasts['saturated']['p95']['ci95'][0]:+.1f}",
        "FcSatDeltaPHi": f"{fc_contrasts['saturated']['p95']['ci95'][1]:+.1f}",
        "FcSatSignP": _fmt_sign_p(fc_contrasts["saturated"]["p95"]["sign_test_p"]),
        "FcSatDeltaFrr": f"{fc_contrasts['saturated']['frr']['difference']*100:+.2f}",
        "FcDirP": f"{dir_fc['p95_latency_min']:.1f}",
        "FcDirDeltaP": f"{fc_contrasts['directory_proxy']['p95']['difference']:+.1f}",
        "FcDirDeltaPLo": f"{fc_contrasts['directory_proxy']['p95']['ci95'][0]:+.1f}",
        "FcDirDeltaPHi": f"{fc_contrasts['directory_proxy']['p95']['ci95'][1]:+.1f}",
        "FcDirSignP": _fmt_sign_p(fc_contrasts["directory_proxy"]["p95"]["sign_test_p"]),
        "FcDirDeltaFrr": f"{fc_contrasts['directory_proxy']['frr']['difference']*100:+.2f}",
        "FcNullP": f"{nul_fc['p95_latency_min']:.1f}",
        "FcNullDeltaP": f"{fc_contrasts['null_uniform']['p95']['difference']:+.1f}",
        "FcNullDeltaPLo": f"{fc_contrasts['null_uniform']['p95']['ci95'][0]:+.1f}",
        "FcNullDeltaPHi": f"{fc_contrasts['null_uniform']['p95']['ci95'][1]:+.1f}",
        "FcNullSignP": _fmt_sign_p(fc_contrasts["null_uniform"]["p95"]["sign_test_p"]),
        # Within-family Beheshtian BatchStop4 vs StaticBatchBisect
        "BsNomP": f"{by_cond['nominal']['by_strategy']['Beheshtian-BatchStop4']['p95_latency_min']:.1f}",
        "BsNomFrr": f"{by_cond['nominal']['by_strategy']['Beheshtian-BatchStop4']['false_rejection_rate']*100:.2f}",
        "BsNomStale": f"{by_cond['nominal']['by_strategy']['Beheshtian-BatchStop4']['stale_rechecks']:.1f}",
        "StaticNomStale": f"{by_cond['nominal']['by_strategy']['StaticBatchBisect']['stale_rechecks']:.1f}",
        "BsNomDeltaP": f"{bs4_contrasts['nominal']['p95']['difference']:+.1f}",
        "BsNomDeltaPLo": f"{bs4_contrasts['nominal']['p95']['ci95'][0]:+.1f}",
        "BsNomDeltaPHi": f"{bs4_contrasts['nominal']['p95']['ci95'][1]:+.1f}",
        "BsNomDeltaFrr": f"{bs4_contrasts['nominal']['frr']['difference']*100:+.2f}",
        "BsSatDeltaP": f"{bs4_contrasts['saturated']['p95']['difference']:+.1f}",
        "BsSatDeltaFrr": f"{bs4_contrasts['saturated']['frr']['difference']*100:+.2f}",
        # Selected descriptive comparator highlights
        "BorsNomP": f"{by_cond['nominal']['by_strategy']['Bors-BatchBisect']['p95_latency_min']:.1f}",
        "BorsNomFrr": f"{by_cond['nominal']['by_strategy']['Bors-BatchBisect']['false_rejection_rate']*100:.2f}",
        "BorsNomCi": f"{by_cond['nominal']['by_strategy']['Bors-BatchBisect']['ci_minutes_per_merged_pr']:.1f}",
        "BorsNomWidth": f"{by_cond['nominal']['by_strategy']['Bors-BatchBisect']['mean_root_batch_size']:.2f}",
        "GhmqNomP": f"{by_cond['nominal']['by_strategy']['GitHubMQ-StagedGroup']['p95_latency_min']:.1f}",
        "GhmqNomFrr": f"{by_cond['nominal']['by_strategy']['GitHubMQ-StagedGroup']['false_rejection_rate']*100:.2f}",
        "GhmqNomCi": f"{by_cond['nominal']['by_strategy']['GitHubMQ-StagedGroup']['ci_minutes_per_merged_pr']:.1f}",
        "PbNomP": f"{by_cond['nominal']['by_strategy']['PRediBatch-RiskGroup']['p95_latency_min']:.1f}",
        "PbNomFrr": f"{by_cond['nominal']['by_strategy']['PRediBatch-RiskGroup']['false_rejection_rate']*100:.2f}",
        "PbNomWidth": f"{by_cond['nominal']['by_strategy']['PRediBatch-RiskGroup']['mean_root_batch_size']:.2f}",
        "LinNomP": f"{by_cond['nominal']['by_strategy']['SpeculativeLinear']['p95_latency_min']:.1f}",
        "AviNomP": f"{by_cond['nominal']['by_strategy']['Aviator-DirAffected']['p95_latency_min']:.1f}",
        "AviNomFrr": f"{by_cond['nominal']['by_strategy']['Aviator-DirAffected']['false_rejection_rate']*100:.2f}",
        "AviNomStale": f"{by_cond['nominal']['by_strategy']['Aviator-DirAffected']['stale_rechecks']:.1f}",
        "NoConeNomStale": f"{nom_nc['stale_rechecks']:.1f}",
        "SsmqNomStale": f"{by_cond['nominal']['by_strategy']['SSMQ']['stale_rechecks']:.1f}",
    }

    payload = {
        "schema_version": "8.2",
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "observed_queue": obs,
        "filtered_cone_vs_nocone": fc_contrasts,
        "batchstop4_vs_static_bisect": bs4_contrasts,
        "macros": macros,
    }

    obs_tex = _build_obs_table(obs)
    base_tex = _build_baselines_tables(by_cond, fc_contrasts, bs4_contrasts, loaded["v7_sim"])
    macro_tex = (
        "% AUTO-GENERATED BY eval/export_v8_displays.py -- DO NOT EDIT\n"
        + "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in macros.items())
    )

    root = output if output is not None else root
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "paper/generated/tables").mkdir(parents=True, exist_ok=True)
    (root / "results/v8_paper.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    (root / "paper/generated/v8.tex").write_text(macro_tex, encoding="utf-8")
    (root / "paper/generated/tables/v8_obs.tex").write_text(obs_tex, encoding="utf-8")
    (root / "paper/generated/tables/v8_baselines_paper.tex").write_text(base_tex, encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    export_v8(args.root)
    print(
        "Exported v8 manuscript displays: results/v8_paper.json, paper/generated/v8.tex, "
        "paper/generated/tables/v8_obs.tex, paper/generated/tables/v8_baselines_paper.tex"
    )


if __name__ == "__main__":
    main()
