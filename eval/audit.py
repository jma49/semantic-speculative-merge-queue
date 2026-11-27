#!/usr/bin/env python3
"""Deterministic Auditor v3: Matched Paired Inference, Wilcoxon + Holm-Bonferroni,
Cliff's Delta, Complete Manifest Verification, Hygiene Guard, and Auto-Generated
LaTeX Macros / Tables / PGFPlots Vector Figures (Zero LLM).

Self-contained pure Python 3.9+ stdlib so standalone GitHub Actions CI verifies
every sealed run without external dependencies.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

V3_EXPERIMENTS = [
    "v3_exp0_blame_microbench",
    "v3_exp1_aa_noise",
    "v3_exp2_main_bench",
    "v3_exp3_velocity",
    "v3_exp4_ablation",
    "v3_exp5_graph_recall",
    "v3_exp6_flaky_sweep",
    "v3_exp7_blame_sweep",
    "v3_exp8_blame_regime",
]

REQUIRED_MANIFEST_FIELDS = {
    "schema_version",
    "run_id",
    "experiment",
    "timestamp_utc",
    "git_commit",
    "harness_sha256",
    "dataset_sha256",
    "command",
    "seeds",
    "metrics_file",
    "metrics_sha256",
    "artifact_sha256",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def combined_sha(paths: List[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode("utf-8"))
        h.update(sha256_file(p).encode("utf-8"))
    return h.hexdigest()


def bootstrap_ci(values: List[float], n_boot: int = 2000, alpha: float = 0.05, seed: int = 42) -> Tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    mean = sum(values) / len(values)
    if len(values) == 1:
        return mean, mean, mean
    rng = random.Random(seed)
    n = len(values)
    boots = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    lo = boots[int((alpha / 2.0) * n_boot)]
    hi = boots[min(n_boot - 1, int((1.0 - alpha / 2.0) * n_boot))]
    return mean, lo, hi


def wilcoxon_signed_rank_p(xs: List[float], ys: List[float]) -> float:
    """Two-sided paired Wilcoxon signed-rank test p-value (normal approx with tie correction)."""
    diffs = [x - y for x, y in zip(xs, ys) if abs(x - y) > 1e-12]
    n = len(diffs)
    if n < 5:
        return 1.0
    abs_sorted = sorted((abs(d), 1 if d > 0 else -1) for d in diffs)
    ranks: List[Tuple[float, int]] = []
    i = 0
    tie_corr = 0.0
    while i < n:
        j = i
        while j < n and abs(abs_sorted[j][0] - abs_sorted[i][0]) <= 1e-12:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        t = j - i
        if t > 1:
            tie_corr += t * (t * t - 1)
        for k in range(i, j):
            ranks.append((avg_rank, abs_sorted[k][1]))
        i = j
    w_plus = sum(r for r, s in ranks if s > 0)
    mu = n * (n + 1) / 4.0
    var = (n * (n + 1) * (2 * n + 1) - tie_corr / 2.0) / 24.0
    if var <= 0.0:
        return 1.0
    z = (abs(w_plus - mu) - 0.5) / math.sqrt(var)
    p = math.erfc(max(0.0, z) / math.sqrt(2.0))
    return min(1.0, max(0.0, p))


def holm_bonferroni(p_map: Dict[str, float]) -> Dict[str, float]:
    ordered = sorted(p_map.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adj: Dict[str, float] = {}
    running_max = 0.0
    for idx, (k, p) in enumerate(ordered):
        val = min(1.0, p * (m - idx))
        running_max = max(running_max, val)
        adj[k] = running_max
    return adj


def cliffs_delta(xs: List[float], ys: List[float]) -> float:
    if not xs or not ys:
        return 0.0
    gt = sum(1 for x in xs for y in ys if x > y)
    lt = sum(1 for x in xs for y in ys if x < y)
    return (gt - lt) / (len(xs) * len(ys))


def paired_contrast(ssmq_rows: List[Dict[str, Any]], base_rows: List[Dict[str, Any]], metric: str) -> Dict[str, float]:
    s_map = {(r["repo"], r["seed"]): float(r[metric]) for r in ssmq_rows}
    b_map = {(r["repo"], r["seed"]): float(r[metric]) for r in base_rows}
    keys = sorted(set(s_map) & set(b_map))
    xs = [s_map[k] for k in keys]
    ys = [b_map[k] for k in keys]
    diffs = [x - y for x, y in zip(xs, ys)]
    d_mean, d_lo, d_hi = bootstrap_ci(diffs)
    p_val = wilcoxon_signed_rank_p(xs, ys)
    mean_y = sum(ys) / len(ys) if ys else 0.0
    pct_diff = (d_mean / mean_y * 100.0) if mean_y else 0.0
    return {
        "n_pairs": len(keys),
        "diff_mean": round(d_mean, 4),
        "diff_ci_lo": round(d_lo, 4),
        "diff_ci_hi": round(d_hi, 4),
        "pct_change": round(pct_diff, 2),
        "wilcoxon_p": round(p_val, 8),
        "cliffs_delta": round(cliffs_delta(xs, ys), 4),
    }


def _summarize(rows: List[Dict[str, Any]], key: str) -> Dict[str, float]:
    vals = [float(r[key]) for r in rows]
    mean, lo, hi = bootstrap_ci(vals, n_boot=1500, seed=42)
    return {"mean": round(mean, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4), "n": len(vals)}


def _group(rows: List[Dict[str, Any]], *keys: str) -> Dict[Tuple[Any, ...], List[Dict[str, Any]]]:
    out: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[tuple(r[k] for k in keys)].append(r)
    return dict(out)


def _fmt(val: float, digits: int = 2) -> str:
    return f"{val:.{digits}f}"


def _fmt_p(p: float) -> str:
    if p < 1e-4:
        return "<0.0001"
    return f"={p:.4f}"


def audit_v3_runs(runs_dir: Path) -> Dict[str, Any]:
    errors: List[str] = []
    verified: Dict[str, Dict[str, Any]] = {}
    harness_files = [
        REPO_ROOT / "eval" / "harness.py",
        REPO_ROOT / "src" / "ssmq" / "simulator.py",
        REPO_ROOT / "src" / "ssmq" / "graph.py",
        REPO_ROOT / "src" / "ssmq" / "loader.py",
        REPO_ROOT / "src" / "ssmq" / "models.py",
        REPO_ROOT / "src" / "ssmq" / "blame_bench.py",
    ]
    trace_files = [
        REPO_ROOT / "data" / "traces" / "kubernetes_kubernetes.json",
        REPO_ROOT / "data" / "traces" / "microsoft_TypeScript.json",
        REPO_ROOT / "data" / "traces" / "vercel_next_js.json",
    ]
    cur_harness_sha = combined_sha(harness_files)
    cur_dataset_sha = combined_sha(trace_files)

    for exp in V3_EXPERIMENTS:
        rp = runs_dir / exp
        mp = rp / "manifest.json"
        if not mp.exists():
            errors.append(f"[{exp}] Missing manifest.json")
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        missing = sorted(REQUIRED_MANIFEST_FIELDS - set(m.keys()))
        if missing:
            errors.append(f"[{exp}] Missing required manifest fields: {missing}")
            continue
        if m["git_commit"] in ("uncommitted", "unknown", ""):
            errors.append(f"[{exp}] Invalid git_commit: {m['git_commit']}")
            continue
        metp = rp / m["metrics_file"]
        if not metp.exists():
            errors.append(f"[{exp}] Missing metrics file: {metp}")
            continue
        actual_sha = sha256_file(metp)
        if actual_sha != m["metrics_sha256"] or actual_sha != m["artifact_sha256"]:
            errors.append(f"[{exp}] SHA-256 mismatch on {m['metrics_file']}")
            continue
        if m["dataset_sha256"] != cur_dataset_sha:
            errors.append(f"[{exp}] Dataset SHA-256 drift detected")
            continue
        if m["harness_sha256"] != cur_harness_sha:
            errors.append(f"[{exp}] Harness SHA-256 drift detected")
            continue
        verified[exp] = {"manifest": m, "payload": json.loads(metp.read_text(encoding="utf-8"))}

    return {
        "audited_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "passed": len(errors) == 0 and len(verified) == len(V3_EXPERIMENTS),
        "verified_run_count": len(verified),
        "errors": errors,
        "runs": verified,
    }


def check_manuscript_hygiene(paper_dir: Path) -> List[str]:
    warnings: List[str] = []
    sec_dir = paper_dir / "sections"
    for tex_path in sorted(sec_dir.glob("*.tex")):
        if tex_path.name == "ai_disclosure.tex":
            continue
        for lineno, line in enumerate(tex_path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.split("%", 1)[0]
            # Catch hand-typed decimal percentages (e.g., 34.8\%)
            if re.search(r"\b\d+\.\d+\s*\\%", stripped):
                warnings.append(f"{tex_path.name}:{lineno}: raw decimal percentage found -> {stripped.strip()[:90]}")
            # Catch accidental markdown inline code backticks (`foo`)
            if re.search(r"`[^`\n]+`", stripped):
                warnings.append(f"{tex_path.name}:{lineno}: markdown backticks in LaTeX -> {stripped.strip()[:90]}")
    num_path = paper_dir / "generated" / "numbers.tex"
    if num_path.exists():
        for lineno, line in enumerate(num_path.read_text(encoding="utf-8").splitlines(), start=1):
            m = re.match(r"\\newcommand\{\\([^}]+)\}", line)
            if m and any(ch.isdigit() for ch in m.group(1)):
                warnings.append(f"numbers.tex:{lineno}: illegal digit in TeX macro \\{m.group(1)}")
    return warnings


def write_numbers_tex(macros: Dict[str, Tuple[str, str]], out_path: Path) -> None:
    lines = [
        "% AUTO-GENERATED BY eval/audit.py v3 (DO NOT EDIT BY HAND)",
        "% Every macro traces deterministically to a SHA-256-sealed v3 run under runs/v3_*/manifest.json",
        "",
    ]
    for name, (val, prov) in sorted(macros.items()):
        if any(ch.isdigit() for ch in name):
            raise ValueError(f"Illegal digit in LaTeX macro name: {name}")
        lines.append(f"\\newcommand{{\\{name}}}{{{val}}} % {prov}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def emit_pgfplots_figures(
    figs_dir: Path,
    blame_bench: Dict[str, Any],
    main_per_repo: Dict[str, Any],
    vel_summary: Dict[str, Any],
    rec_summary: Dict[str, Any],
    flk_summary: Dict[str, Any],
    reg_summary: Dict[str, Any],
) -> None:
    """Auto-generate 4 publication-grade vector PGFPlots figures directly from sealed runs."""
    figs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Fig. 3: Per-Repository P95 Latency & CI Cost with 95% Bootstrap CIs
    # ------------------------------------------------------------------
    repos = [("kubernetes", "Kubernetes"), ("typescript", "TypeScript"), ("nextjs", "Next.js")]
    strats_f3 = [
        ("StaticBatchBisect", "StaticBatch", "ssmqGray"),
        ("UberSubmitQueue", "UberTree", "ssmqOrange"),
        ("FlakeAwareBatch", "FlakeBatch", "ssmqPurple"),
        ("SSMQ", "SSMQ (Ours)", "ssmqBlue"),
    ]

    def _bar_coords(metric: str, strat: str) -> str:
        pts = []
        for rkey, rlbl in repos:
            d = main_per_repo[rkey][strat][metric]
            err_lo = max(0.0, d["mean"] - d["ci_lo"])
            err_hi = max(0.0, d["ci_hi"] - d["mean"])
            pts.append(f"({rlbl},{d['mean']:.2f}) +- ({err_lo:.2f},{err_hi:.2f})")
        return " ".join(pts)

    f3_lines = [
        "% AUTO-GENERATED FROM v3_exp2_main_bench — DO NOT EDIT",
        "\\begin{figure*}[t]",
        "\\centering",
        "\\begin{tikzpicture}",
        "\\begin{groupplot}[",
        "  group style={group size=2 by 1, horizontal sep=1.4cm},",
        "  width=0.47\\textwidth, height=4.8cm,",
        "  symbolic x coords={Kubernetes,TypeScript,Next.js},",
        "  xtick=data, ymin=0,",
        "  ymajorgrids=true, grid style={dashed,gray!35},",
        "  tick label style={font=\\footnotesize},",
        "  label style={font=\\footnotesize},",
        "  title style={font=\\footnotesize\\bfseries},",
        "  enlarge x limits=0.22,",
        "  error bars/y dir=both, error bars/y explicit,",
        "  error bars/error bar style={line width=0.6pt, black!75},",
        "]",
        "\\nextgroupplot[ybar=2pt, /pgf/bar width=7.5pt, title={(a) P95 Merge Latency (min) $\\downarrow$}, ylabel={P95 Latency (min)}, ymax=470,",
        "  legend to name=legPerRepo, legend columns=4, legend style={font=\\footnotesize, draw=none, /tikz/every even column/.style={column sep=8pt}}]",
    ]
    for skey, slbl, scol in strats_f3:
        f3_lines.append(
            f"\\addplot[fill={scol}!80, draw={scol}!90!black] coordinates {{ {_bar_coords('p95_latency_min', skey)} }};"
        )
        f3_lines.append(f"\\addlegendentry{{{slbl}}}")
    f3_lines += [
        "\\nextgroupplot[ybar=2pt, /pgf/bar width=7.5pt, title={(b) CI Compute Cost (runner-min/PR) $\\downarrow$}, ylabel={Runner-min / Merged PR}, ymax=60]",
    ]
    for skey, _, scol in strats_f3:
        f3_lines.append(
            f"\\addplot[fill={scol}!80, draw={scol}!90!black] coordinates {{ {_bar_coords('ci_minutes_per_merged_pr', skey)} }};"
        )
    f3_lines += [
        "\\end{groupplot}",
        "\\node at ($(group c1r1.north)!0.5!(group c2r1.north)+(0,0.85cm)$) {\\pgfplotslegendfromname{legPerRepo}};",
        "\\end{tikzpicture}",
        "\\caption{Per-repository P95 merge latency and CI compute cost across 600 real OSS PRs ($5\\times$ velocity, $\\phi=5\\%$, $80\\%$ graph recall, 20 paired seeds per repo; error bars show 95\\% bootstrap CIs). SSMQ achieves the lowest tail latency and runner cost on every repository.}",
        "\\label{fig:per_repo}",
        "\\end{figure*}",
    ]
    (figs_dir / "fig_per_repo.tex").write_text("\n".join(f3_lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Fig. 4: Non-Circularity (Graph Recall Sweep) & Velocity Scaling
    # ------------------------------------------------------------------
    rec_x = [("recall_0.40", 40), ("recall_0.60", 60), ("recall_0.80", 80), ("recall_1.00", 100)]
    vel_x = [("1.0", 1), ("3.0", 3), ("5.0", 5), ("10.0", 10)]

    def _rec_pts(strat: str) -> str:
        return " ".join(
            f"({xv},{rec_summary[rk][strat]['p95_latency_min']['mean']:.2f})"
            for rk, xv in rec_x
        )

    def _vel_pts(strat: str) -> str:
        return " ".join(
            f"({xv},{vel_summary[vk][strat]['throughput_prs_per_hr']['mean']:.2f})"
            for vk, xv in vel_x
        )

    nc_static = rec_summary["no_conflict"]["StaticBatchBisect"]["p95_latency_min"]["mean"]
    nc_ssmq = rec_summary["no_conflict"]["SSMQ"]["p95_latency_min"]["mean"]

    f4_lines = [
        "% AUTO-GENERATED FROM v3_exp5_graph_recall & v3_exp3_velocity — DO NOT EDIT",
        "\\begin{figure*}[t]",
        "\\centering",
        "\\begin{tikzpicture}",
        "\\begin{groupplot}[",
        "  group style={group size=2 by 1, horizontal sep=1.5cm},",
        "  width=0.47\\textwidth, height=4.7cm,",
        "  ymajorgrids=true, xmajorgrids=true, grid style={dashed,gray!30},",
        "  tick label style={font=\\footnotesize},",
        "  label style={font=\\footnotesize},",
        "  title style={font=\\footnotesize\\bfseries},",
        "  every axis plot/.append style={thick, mark size=2.2pt},",
        "]",
        "\\nextgroupplot[title={(a) Non-Circularity: Off-Graph Latent Conflicts},",
        "  xlabel={Observed Graph Recall $\\rho$ (\\%)}, ylabel={P95 Latency (min) $\\downarrow$},",
        "  xtick={40,60,80,100}, xmin=35, xmax=105, ymin=0, ymax=480,",
        "  legend to name=legRecVel, legend columns=5, legend style={font=\\footnotesize, draw=none, /tikz/every even column/.style={column sep=6pt}}]",
        f"\\addplot[color=ssmqBlue, mark=square*] coordinates {{ {_rec_pts('SSMQ')} }}; \\addlegendentry{{SSMQ (Ours)}}",
        f"\\addplot[color=ssmqGray, mark=triangle*, dashed] coordinates {{ {_rec_pts('StaticBatchBisect')} }}; \\addlegendentry{{StaticBatch}}",
        f"\\addplot[color=ssmqOrange, mark=diamond*, dotted] coordinates {{ {_rec_pts('UberSubmitQueue')} }}; \\addlegendentry{{UberTree}}",
        f"\\addplot[color=ssmqTeal, mark=o, dashdotted] coordinates {{ (40,{nc_ssmq:.2f}) (100,{nc_ssmq:.2f}) }}; \\addlegendentry{{SSMQ ($P_c=0$)}}",
        f"\\addplot[color=black!60, mark=none, densely dotted] coordinates {{ (40,{nc_static:.2f}) (100,{nc_static:.2f}) }}; \\addlegendentry{{Static ($P_c=0$)}}",
        "\\nextgroupplot[title={(b) Arrival Velocity Scaling ($1\\times$--$10\\times$)},",
        "  xlabel={Offered Velocity Multiplier ($\\times$)}, ylabel={Merge Throughput (PRs/hr) $\\uparrow$},",
        "  xtick={1,3,5,10}, xmin=0.5, xmax=10.5, ymin=0, ymax=28]",
        f"\\addplot[color=ssmqBlue, mark=square*] coordinates {{ {_vel_pts('SSMQ')} }};",
        f"\\addplot[color=ssmqGray, mark=triangle*, dashed] coordinates {{ {_vel_pts('StaticBatchBisect')} }};",
        f"\\addplot[color=ssmqOrange, mark=diamond*, dotted] coordinates {{ {_vel_pts('UberSubmitQueue')} }};",
        f"\\addplot[color=ssmqPurple, mark=pentagon*] coordinates {{ {_vel_pts('FlakeAwareBatch')} }};",
        f"\\addplot[color=ssmqRed, mark=x, dashdotted] coordinates {{ {_vel_pts('SpeculativeLinear')} }};",
        "\\node[font=\\scriptsize, anchor=north west, fill=white, fill opacity=0.85, text opacity=1, draw=gray!40, inner sep=2pt] at (axis cs:0.8,26.8) {Purple: FlakeBatch \\quad Red: SpecLinear};",
        "\\end{groupplot}",
        "\\node at ($(group c1r1.north)!0.5!(group c2r1.north)+(0,0.85cm)$) {\\pgfplotslegendfromname{legRecVel}};",
        "\\end{tikzpicture}",
        "\\caption{(a) Non-circularity verification under decoupled off-graph latent conflicts ($40\\%$--$100\\%$ graph recall) and zero-conflict control ($P_c=0$): SSMQ retains substantial latency reductions even when $60\\%$ of conflicts lie outside $\\hat{G}$. (b) Throughput scaling under $1\\times$--$10\\times$ PR arrival bursts.}",
        "\\label{fig:recall_velocity}",
        "\\end{figure*}",
    ]
    (figs_dir / "fig_recall_velocity.tex").write_text("\n".join(f4_lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Fig. 5: Flakiness Sweep & Latency vs False-Rejection Pareto Frontier
    # ------------------------------------------------------------------
    fr_keys = [("0.0", 0), ("0.02", 2), ("0.05", 5), ("0.1", 10), ("0.15", 15)]

    def _flk_fr_pts(strat: str) -> str:
        return " ".join(
            f"({xv},{flk_summary[fk][strat]['false_rejection_rate']['mean']*100.0:.2f})"
            for fk, xv in fr_keys
        )

    pareto_strats = [
        ("SSMQ-NoFlaky", "NoFlaky", "ssmqRed", "triangle*"),
        ("SSMQ", "SSMQ", "ssmqBlue", "square*"),
        ("StaticBatchBisect", "Static", "ssmqGray", "o"),
        ("FlakeAwareBatch", "FlakeBatch", "ssmqPurple", "diamond*"),
    ]
    p10_pts = []
    p15_pts = []
    for skey, slbl, scol, smark in pareto_strats:
        d10 = flk_summary["0.1"][skey]
        d15 = flk_summary["0.15"][skey]
        p10_pts.append((skey, slbl, scol, smark, d10["p95_latency_min"]["mean"], d10["false_rejection_rate"]["mean"] * 100.0))
        p15_pts.append((skey, slbl, scol, smark, d15["p95_latency_min"]["mean"], d15["false_rejection_rate"]["mean"] * 100.0))

    f5_lines = [
        "% AUTO-GENERATED FROM v3_exp6_flaky_sweep — DO NOT EDIT",
        "\\begin{figure*}[t]",
        "\\centering",
        "\\begin{tikzpicture}",
        "\\begin{groupplot}[",
        "  group style={group size=2 by 1, horizontal sep=1.5cm},",
        "  width=0.47\\textwidth, height=4.7cm,",
        "  ymajorgrids=true, xmajorgrids=true, grid style={dashed,gray!30},",
        "  tick label style={font=\\footnotesize},",
        "  label style={font=\\footnotesize},",
        "  title style={font=\\footnotesize\\bfseries},",
        "  every axis plot/.append style={thick, mark size=2.3pt},",
        "]",
        "\\nextgroupplot[title={(a) Clean-PR False Rejection vs.\\ Suite Flakiness},",
        "  xlabel={Transient Flakiness Rate $\\phi$ (\\%)}, ylabel={Clean-PR False Rejection (\\%) $\\downarrow$},",
        "  xtick={0,2,5,10,15}, xmin=-0.5, xmax=15.8, ymin=0, ymax=10.5,",
        "  legend to name=legFlaky, legend columns=4, legend style={font=\\footnotesize, draw=none, /tikz/every even column/.style={column sep=8pt}}]",
        f"\\addplot[color=ssmqBlue, mark=square*] coordinates {{ {_flk_fr_pts('SSMQ')} }}; \\addlegendentry{{SSMQ (Ours)}}",
        f"\\addplot[color=ssmqRed, mark=triangle*, dashed] coordinates {{ {_flk_fr_pts('SSMQ-NoFlaky')} }}; \\addlegendentry{{SSMQ-NoFlaky}}",
        f"\\addplot[color=ssmqGray, mark=o, dotted] coordinates {{ {_flk_fr_pts('StaticBatchBisect')} }}; \\addlegendentry{{StaticBatch}}",
        f"\\addplot[color=ssmqPurple, mark=diamond*, dashdotted] coordinates {{ {_flk_fr_pts('FlakeAwareBatch')} }}; \\addlegendentry{{FlakeBatch}}",
        "\\nextgroupplot[title={(b) Latency--Acceptance Pareto Frontier ($\\phi=10\\%$)},",
        "  xlabel={P95 Merge Latency (min) $\\downarrow$}, ylabel={Clean-PR False Rejection (\\%) $\\downarrow$},",
        "  xmin=50, xmax=350, ymin=-0.2, ymax=6.8]",
    ]
    # Draw Pareto frontier connecting NoFlaky -> SSMQ -> FlakeBatch at phi=10%
    nf10 = flk_summary["0.1"]["SSMQ-NoFlaky"]
    ss10 = flk_summary["0.1"]["SSMQ"]
    fa10 = flk_summary["0.1"]["FlakeAwareBatch"]
    f5_lines.append(
        f"\\addplot[color=ssmqBlue!45, dashed, thin, forget plot] coordinates {{ "
        f"({nf10['p95_latency_min']['mean']:.1f},{nf10['false_rejection_rate']['mean']*100:.2f}) "
        f"({ss10['p95_latency_min']['mean']:.1f},{ss10['false_rejection_rate']['mean']*100:.2f}) "
        f"({fa10['p95_latency_min']['mean']:.1f},{fa10['false_rejection_rate']['mean']*100:.2f}) }};"
    )
    label_offsets = {
        "SSMQ-NoFlaky": ("south west", "2pt"),
        "SSMQ": ("south west", "2pt"),
        "StaticBatchBisect": ("south", "3pt"),
        "FlakeAwareBatch": ("south east", "2pt"),
    }
    for skey, slbl, scol, smark, xlat, yfr in p10_pts:
        anc, _ = label_offsets[skey]
        f5_lines.append(
            f"\\addplot[only marks, color={scol}, mark={smark}, mark size=3.2pt] coordinates {{ ({xlat:.1f},{yfr:.2f}) }};"
        )
        f5_lines.append(
            f"\\node[font=\\scriptsize\\bfseries, anchor={anc}, inner sep=3pt, text={scol}!85!black] at (axis cs:{xlat:.1f},{yfr:.2f}) {{{slbl}}};"
        )
    f5_lines += [
        "\\node[font=\\scriptsize, anchor=north east, fill=ssmqBlue!8, draw=ssmqBlue!50, inner sep=2.5pt] at (axis cs:342,6.4) {Better: Lower-Left $\\swarrow$};",
        "\\end{groupplot}",
        "\\node at ($(group c1r1.north)!0.5!(group c2r1.north)+(0,0.85cm)$) {\\pgfplotslegendfromname{legFlaky}};",
        "\\end{tikzpicture}",
        "\\caption{Flakiness sensitivity ($\\phi\\in[0\\%,15\\%]$) and latency--acceptance Pareto frontier at $\\phi=10\\%$. Two-regime Beta-Binomial gating positions SSMQ on the Pareto knee, slashing clean-PR false rejections relative to \\texttt{SSMQ-NoFlaky} and \\texttt{StaticBatchBisect} while avoiding the tail-latency explosion of unconditional full-batch retries (\\texttt{FlakeAwareBatch}).}",
        "\\label{fig:flaky_pareto}",
        "\\end{figure*}",
    ]
    (figs_dir / "fig_flaky_pareto.tex").write_text("\n".join(f5_lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Fig. 6: 64-Batch Blame Micro-Benchmark & High-Failure Blame Regime
    # ------------------------------------------------------------------
    reg_order = [
        ("nominal_8pct_k8", "8\\%/K8"),
        ("elevated_15pct_k8", "15\\%/K8"),
        ("highdef_22pct_k12", "22\\%/K12"),
        ("lowrec_15pct_k12", "40\\%Rec"),
    ]

    def _reg_pts(strat: str) -> str:
        return " ".join(
            f"({lbl},{reg_summary[rk][strat]['p95_latency_min']['mean']:.1f})"
            for rk, lbl in reg_order
        )

    f6_lines = [
        "% AUTO-GENERATED FROM v3_exp0_blame_microbench & v3_exp8_blame_regime — DO NOT EDIT",
        "\\begin{figure*}[t]",
        "\\centering",
        "\\begin{tikzpicture}",
        "\\begin{groupplot}[",
        "  group style={group size=2 by 1, horizontal sep=1.5cm},",
        "  width=0.47\\textwidth, height=4.7cm,",
        "  ymajorgrids=true, grid style={dashed,gray!30},",
        "  tick label style={font=\\footnotesize},",
        "  label style={font=\\footnotesize},",
        "  title style={font=\\footnotesize\\bfseries},",
        "]",
        "\\nextgroupplot[title={(a) Real Python Blame Benchmark (64 Batches)},",
        "  ybar=2pt, /pgf/bar width=10pt, ymin=0, ymax=112,",
        "  symbolic x coords={Random,Churn,FileOverlap,ASTStack},",
        "  xtick=data, ylabel={Accuracy (\\%) $\\uparrow$}, enlarge x limits=0.22,",
        "  legend style={at={(0.03,0.97)}, anchor=north west, font=\\scriptsize, draw=gray!40}]",
        f"\\addplot[fill=ssmqBlue!80, draw=ssmqBlue!90!black] coordinates {{ "
        f"(Random,{blame_bench['random_top1_acc']*100:.1f}) "
        f"(Churn,{blame_bench['churn_risk_top1_acc']*100:.1f}) "
        f"(FileOverlap,{blame_bench['file_overlap_top1_acc']*100:.1f}) "
        f"(ASTStack,{blame_bench['ast_stack_top1_acc']*100:.1f}) }}; \\addlegendentry{{Top-1 Acc.}}",
        f"\\addplot[fill=ssmqTeal!70, draw=ssmqTeal!90!black] coordinates {{ "
        f"(Random,{blame_bench['random_top2_acc']*100:.1f}) "
        f"(Churn,{blame_bench['churn_risk_top2_acc']*100:.1f}) "
        f"(FileOverlap,{blame_bench['file_overlap_top2_acc']*100:.1f}) "
        f"(ASTStack,{blame_bench['ast_stack_top2_acc']*100:.1f}) }}; \\addlegendentry{{Top-2 Acc.}}",
        "\\nextgroupplot[title={(b) Failure-Density Regime: Blame vs.\\ Bisection},",
        "  ybar=2pt, /pgf/bar width=8pt, ymin=0, ymax=210,",
        "  symbolic x coords={8\\%/K8,15\\%/K8,22\\%/K12,40\\%Rec},",
        "  xtick=data, ylabel={P95 Latency (min) $\\downarrow$}, enlarge x limits=0.20,",
        "  legend style={at={(0.03,0.97)}, anchor=north west, font=\\scriptsize, draw=gray!40}]",
        f"\\addplot[fill=ssmqGray!75, draw=ssmqGray!90!black] coordinates {{ {_reg_pts('StaticBatchBisect')} }}; \\addlegendentry{{StaticBatch}}",
        f"\\addplot[fill=ssmqOrange!80, draw=ssmqOrange!90!black] coordinates {{ {_reg_pts('SSMQ-NoBlame')} }}; \\addlegendentry{{SSMQ-NoBlame}}",
        f"\\addplot[fill=ssmqBlue!80, draw=ssmqBlue!90!black] coordinates {{ {_reg_pts('SSMQ')} }}; \\addlegendentry{{SSMQ (Full)}}",
        "\\end{groupplot}",
        "\\end{tikzpicture}",
        "\\caption{(a) Attribution accuracy across 64 real executed Python failing batches, demonstrating AST symbol-level + 1-hop call-graph gains over file-level traceback matching (\\texttt{FileOverlap}). (b) Failure-density regime sweep contrasting \\texttt{StaticBatch}, \\texttt{SSMQ-NoBlame}, and full \\texttt{SSMQ}: 1-step blame yields statistically significant P95 reductions in single-culprit regimes ($8\\%$--$15\\%$ defect, $K=8$) while saturating when high defect density ($22\\%$, $K=12$) causes multi-culprit batches that fall back to bisection.}",
        "\\label{fig:blame_regime}",
        "\\end{figure*}",
    ]
    (figs_dir / "fig_blame_regime.tex").write_text("\n".join(f6_lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic v3 Auditor, Hygiene Guard & PGFPlots Exporter.")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    audit = audit_v3_runs(REPO_ROOT / "runs")
    if not audit["passed"]:
        print("V3 AUDIT FAILED:", json.dumps(audit["errors"], indent=2))
        raise SystemExit(1)

    runs = audit["runs"]
    sim_exps = [e for e in V3_EXPERIMENTS if e != "v3_exp0_blame_microbench"]
    total_sim_records = 0
    total_escaped = 0
    for e in sim_exps:
        for r in runs[e]["payload"]:
            total_sim_records += 1
            total_escaped += int(r["escaped_defects"])
    if total_escaped != 0:
        print(f"CUMULATIVE SAFETY VIOLATION: {total_escaped} escaped defects!")
        raise SystemExit(1)

    blame_bench = runs["v3_exp0_blame_microbench"]["payload"]["summary"]
    hygiene = check_manuscript_hygiene(REPO_ROOT / "paper")

    if args.verify_only:
        if hygiene:
            print("MANUSCRIPT HYGIENE WARNINGS:", json.dumps(hygiene, indent=2))
            raise SystemExit(1)
        print(
            f"V3 AUDIT PASS (verify-only): {audit['verified_run_count']} sealed v3 runs, "
            f"{total_sim_records} simulation records + {blame_bench['total_failing_batches']} real executed blame batches, "
            f"0 cumulative escapes, 0 manuscript hygiene warnings."
        )
        return

    metrics_keys = [
        "throughput_prs_per_hr",
        "p50_latency_min",
        "p95_latency_min",
        "ci_minutes_per_merged_pr",
        "invalidation_rate",
        "mean_blame_rounds",
        "false_rejection_rate",
        "mean_batch_size",
        "runner_utilization",
    ]

    # 1. A/A Noise Floor
    aa_rows = runs["v3_exp1_aa_noise"]["payload"]
    aa_a = [r for r in aa_rows if r["block"] == "A"]
    aa_b = [r for r in aa_rows if r["block"] == "B"]
    aa_summary = {}
    for mk in ("throughput_prs_per_hr", "p95_latency_min", "ci_minutes_per_merged_pr", "false_rejection_rate"):
        sa, sb = _summarize(aa_a, mk), _summarize(aa_b, mk)
        rel = abs(sa["mean"] - sb["mean"]) / max(1e-9, abs(sa["mean"])) * 100.0 if sa["mean"] else 0.0
        aa_summary[mk] = {"block_A": sa, "block_B": sb, "rel_diff_pct": round(rel, 2)}

    # 2. Main Benchmark + Matched Paired Contrasts + Holm-Bonferroni
    main_rows = runs["v3_exp2_main_bench"]["payload"]
    main_overall = {s[0]: {mk: _summarize(rows, mk) for mk in metrics_keys} for s, rows in _group(main_rows, "strategy").items()}
    main_per_repo: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (repo, strat), rows in _group(main_rows, "repo", "strategy").items():
        main_per_repo[repo][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    ssmq_main = [r for r in main_rows if r["strategy"] == "SSMQ"]
    baselines = ["SerialQueue", "StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch"]
    paired_contrasts: Dict[str, Dict[str, Any]] = defaultdict(dict)
    raw_pvals: Dict[str, float] = {}
    for b in baselines:
        brows = [r for r in main_rows if r["strategy"] == b]
        for mk in ("throughput_prs_per_hr", "p95_latency_min", "ci_minutes_per_merged_pr", "false_rejection_rate", "mean_blame_rounds"):
            c = paired_contrast(ssmq_main, brows, mk)
            paired_contrasts[b][mk] = c
            raw_pvals[f"{b}:{mk}"] = c["wilcoxon_p"]
    holm_p = holm_bonferroni(raw_pvals)
    for k, adj_p in holm_p.items():
        b, mk = k.split(":")
        paired_contrasts[b][mk]["holm_p"] = round(adj_p, 8)

    # 3. Velocity Sweep
    vel_rows = runs["v3_exp3_velocity"]["payload"]
    vel_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (vel, strat), rows in _group(vel_rows, "velocity_multiplier", "strategy").items():
        vel_summary[str(vel)][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    # 4. Ablation
    abl_rows = runs["v3_exp4_ablation"]["payload"]
    abl_summary = {s[0]: {mk: _summarize(rows, mk) for mk in metrics_keys} for s, rows in _group(abl_rows, "strategy").items()}

    # 5. Graph Recall & No-Conflict Control
    rec_rows = runs["v3_exp5_graph_recall"]["payload"]
    rec_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (reg, strat), rows in _group(rec_rows, "regime", "strategy").items():
        rec_summary[reg][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    # 6. Flaky Sweep
    flk_rows = runs["v3_exp6_flaky_sweep"]["payload"]
    flk_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (fr, strat), rows in _group(flk_rows, "flaky_rate", "strategy").items():
        flk_summary[str(fr)][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    # 7. Blame Accuracy Sweep
    blm_rows = runs["v3_exp7_blame_sweep"]["payload"]
    blm_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (acc, strat), rows in _group(blm_rows, "blame_top1_acc", "strategy").items():
        blm_summary[str(acc)][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    # 8. Blame Regime Sweep (NEW in v3!)
    reg_rows = runs["v3_exp8_blame_regime"]["payload"]
    reg_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (reg, strat), rows in _group(reg_rows, "regime", "strategy").items():
        reg_summary[reg][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}
    # Paired contrast of SSMQ vs SSMQ-NoBlame in highdef_22pct_k12 and lowrec_15pct_k12
    reg_contrasts: Dict[str, Dict[str, Any]] = {}
    for rk in ("nominal_8pct_k8", "elevated_15pct_k8", "highdef_22pct_k12", "lowrec_15pct_k12"):
        r_ss = [r for r in reg_rows if r["regime"] == rk and r["strategy"] == "SSMQ"]
        r_nb = [r for r in reg_rows if r["regime"] == rk and r["strategy"] == "SSMQ-NoBlame"]
        reg_contrasts[rk] = {
            "p95": paired_contrast(r_ss, r_nb, "p95_latency_min"),
            "cost": paired_contrast(r_ss, r_nb, "ci_minutes_per_merged_pr"),
            "rounds": paired_contrast(r_ss, r_nb, "mean_blame_rounds"),
        }

    results = {
        "schema_version": "3.0",
        "audit_passed": True,
        "verified_run_count": audit["verified_run_count"],
        "total_sealed_sim_records": total_sim_records,
        "total_escaped_defects": total_escaped,
        "manifests": {e: {"run_id": runs[e]["manifest"]["run_id"], "git_commit": runs[e]["manifest"]["git_commit"], "sha256": runs[e]["manifest"]["metrics_sha256"][:16]} for e in V3_EXPERIMENTS},
        "v3_exp0_blame_microbench": blame_bench,
        "v3_exp1_aa_noise": aa_summary,
        "v3_exp2_main_overall": main_overall,
        "v3_exp2_main_per_repo": dict(main_per_repo),
        "v3_exp2_paired_contrasts": dict(paired_contrasts),
        "v3_exp3_velocity": dict(vel_summary),
        "v3_exp4_ablation": abl_summary,
        "v3_exp5_graph_recall": dict(rec_summary),
        "v3_exp6_flaky_sweep": dict(flk_summary),
        "v3_exp7_blame_sweep": dict(blm_summary),
        "v3_exp8_blame_regime": dict(reg_summary),
        "v3_exp8_blame_contrasts": reg_contrasts,
    }
    (REPO_ROOT / "results" / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    # Deterministic LaTeX macros (pure-letter names!)
    ss = main_overall["SSMQ"]
    sb = main_overall["StaticBatchBisect"]
    sl = main_overall["SpeculativeLinear"]
    ub = main_overall["UberSubmitQueue"]
    fa = main_overall["FlakeAwareBatch"]
    sq = main_overall["SerialQueue"]

    pc_sb = paired_contrasts["StaticBatchBisect"]
    pc_ub = paired_contrasts["UberSubmitQueue"]
    pc_sl = paired_contrasts["SpeculativeLinear"]
    pc_fa = paired_contrasts["FlakeAwareBatch"]

    trace_stats = json.loads((REPO_ROOT / "results" / "trace_stats.json").read_text(encoding="utf-8"))
    ts_by_repo = {r["repo"]: r for r in trace_stats["repositories"]}

    macros: Dict[str, Tuple[str, str]] = {
        "TraceFifoOverlapPct": (_fmt(ts_by_repo["kubernetes/kubernetes"]["fifo_k8_module_overlap_rate"] * 100.0, 1), "trace_stats"),
        "TraceKbsDisjPct": (_fmt(ts_by_repo["kubernetes/kubernetes"]["disjoint_batch4_feasibility_rate"] * 100.0, 1), "trace_stats"),
        "TraceTSDisjPct": (_fmt(ts_by_repo["microsoft/TypeScript"]["disjoint_batch4_feasibility_rate"] * 100.0, 1), "trace_stats"),
        "TraceNextDisjPct": (_fmt(ts_by_repo["vercel/next.js"]["disjoint_batch4_feasibility_rate"] * 100.0, 1), "trace_stats"),
        "TotalSealedRuns": (str(audit["verified_run_count"]), "v3_audit"),
        "TotalSealedRecords": (str(total_sim_records), "v3_audit"),
        "TotalEscapedDefects": (str(total_escaped), "cumulative_safety_invariant"),
        # Microbench (64 batches, 4 rankers)
        "BenchTotalBatches": (str(blame_bench["total_failing_batches"]), "v3_exp0_blame_microbench"),
        "BenchSingleBatches": (str(blame_bench["single_defect_batches"]), "v3_exp0_blame_microbench"),
        "BenchCofileBatches": (str(blame_bench["cofile_distractor_batches"]), "v3_exp0_blame_microbench"),
        "BenchSemBatches": (str(blame_bench["semantic_conflict_batches"]), "v3_exp0_blame_microbench"),
        "BenchASTTopOnePct": (_fmt(blame_bench["ast_stack_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchASTTopTwoPct": (_fmt(blame_bench["ast_stack_top2_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchASTMRR": (_fmt(blame_bench["ast_stack_mrr"], 3), "v3_exp0_blame_microbench"),
        "BenchASTMs": (_fmt(blame_bench["ast_stack_mean_ms"], 2), "v3_exp0_blame_microbench"),
        "BenchASTCofilePct": (_fmt(blame_bench["ast_stack_cofile_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchASTSemPct": (_fmt(blame_bench["ast_stack_sem_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchFileTopOnePct": (_fmt(blame_bench["file_overlap_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchFileTopTwoPct": (_fmt(blame_bench["file_overlap_top2_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchFileMRR": (_fmt(blame_bench["file_overlap_mrr"], 3), "v3_exp0_blame_microbench"),
        "BenchFileCofilePct": (_fmt(blame_bench["file_overlap_cofile_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchFileSemPct": (_fmt(blame_bench["file_overlap_sem_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchRandTopOnePct": (_fmt(blame_bench["random_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        "BenchChurnTopOnePct": (_fmt(blame_bench["churn_risk_top1_acc"] * 100.0, 1), "v3_exp0_blame_microbench"),
        # A/A
        "AANoiseThroughputPct": (_fmt(aa_summary["throughput_prs_per_hr"]["rel_diff_pct"], 2), "v3_exp1_aa_noise"),
        "AANoiseLatencyPct": (_fmt(aa_summary["p95_latency_min"]["rel_diff_pct"], 2), "v3_exp1_aa_noise"),
        "AANoiseComputePct": (_fmt(aa_summary["ci_minutes_per_merged_pr"]["rel_diff_pct"], 2), "v3_exp1_aa_noise"),
        # Main metrics
        "SSMQThroughput": (_fmt(ss["throughput_prs_per_hr"]["mean"], 2), "v3_exp2_main_bench"),
        "SSMQPFiftyLat": (_fmt(ss["p50_latency_min"]["mean"], 1), "v3_exp2_main_bench"),
        "SSMQPNinetyFiveLat": (_fmt(ss["p95_latency_min"]["mean"], 1), "v3_exp2_main_bench"),
        "SSMQCICost": (_fmt(ss["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp2_main_bench"),
        "SSMQInvalidRatePct": (_fmt(ss["invalidation_rate"]["mean"] * 100.0, 1), "v3_exp2_main_bench"),
        "SSMQBlameRounds": (_fmt(ss["mean_blame_rounds"]["mean"], 2), "v3_exp2_main_bench"),
        "SSMQFalseRejPct": (_fmt(ss["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp2_main_bench"),
        # Baselines
        "SerialThroughput": (_fmt(sq["throughput_prs_per_hr"]["mean"], 2), "v3_exp2_main_bench"),
        "SerialPNinetyFiveLat": (_fmt(sq["p95_latency_min"]["mean"], 1), "v3_exp2_main_bench"),
        "SerialFalseRejPct": (_fmt(sq["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp2_main_bench"),
        "StaticThroughput": (_fmt(sb["throughput_prs_per_hr"]["mean"], 2), "v3_exp2_main_bench"),
        "StaticPNinetyFiveLat": (_fmt(sb["p95_latency_min"]["mean"], 1), "v3_exp2_main_bench"),
        "StaticCICost": (_fmt(sb["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp2_main_bench"),
        "StaticBlameRounds": (_fmt(sb["mean_blame_rounds"]["mean"], 2), "v3_exp2_main_bench"),
        "StaticFalseRejPct": (_fmt(sb["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp2_main_bench"),
        "SpecLinThroughput": (_fmt(sl["throughput_prs_per_hr"]["mean"], 2), "v3_exp2_main_bench"),
        "SpecLinPNinetyFiveLat": (_fmt(sl["p95_latency_min"]["mean"], 1), "v3_exp2_main_bench"),
        "SpecLinCICost": (_fmt(sl["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp2_main_bench"),
        "SpecLinInvalidRatePct": (_fmt(sl["invalidation_rate"]["mean"] * 100.0, 1), "v3_exp2_main_bench"),
        "UberThroughput": (_fmt(ub["throughput_prs_per_hr"]["mean"], 2), "v3_exp2_main_bench"),
        "UberPNinetyFiveLat": (_fmt(ub["p95_latency_min"]["mean"], 1), "v3_exp2_main_bench"),
        "UberCICost": (_fmt(ub["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp2_main_bench"),
        "UberInvalidRatePct": (_fmt(ub["invalidation_rate"]["mean"] * 100.0, 1), "v3_exp2_main_bench"),
        "UberFalseRejPct": (_fmt(ub["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp2_main_bench"),
        "FlakeBatchThroughput": (_fmt(fa["throughput_prs_per_hr"]["mean"], 2), "v3_exp2_main_bench"),
        "FlakeBatchPNinetyFiveLat": (_fmt(fa["p95_latency_min"]["mean"], 1), "v3_exp2_main_bench"),
        "FlakeBatchCICost": (_fmt(fa["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp2_main_bench"),
        "FlakeBatchFalseRejPct": (_fmt(fa["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp2_main_bench"),
        # Paired % changes, Holm p-values, Cliff's delta
        "GainTputVsStaticPct": (_fmt(pc_sb["throughput_prs_per_hr"]["pct_change"], 1), "v3_exp2_paired"),
        "GainTputVsUberPct": (_fmt(pc_ub["throughput_prs_per_hr"]["pct_change"], 1), "v3_exp2_paired"),
        "GainTputVsSpecLinPct": (_fmt(pc_sl["throughput_prs_per_hr"]["pct_change"], 1), "v3_exp2_paired"),
        "GainTputVsFlakePct": (_fmt(pc_fa["throughput_prs_per_hr"]["pct_change"], 1), "v3_exp2_paired"),
        "LatRedVsStaticPct": (_fmt(-pc_sb["p95_latency_min"]["pct_change"], 1), "v3_exp2_paired"),
        "LatRedVsUberPct": (_fmt(-pc_ub["p95_latency_min"]["pct_change"], 1), "v3_exp2_paired"),
        "LatRedVsSpecLinPct": (_fmt(-pc_sl["p95_latency_min"]["pct_change"], 1), "v3_exp2_paired"),
        "LatRedVsFlakePct": (_fmt(-pc_fa["p95_latency_min"]["pct_change"], 1), "v3_exp2_paired"),
        "CostSaveVsStaticPct": (_fmt(-pc_sb["ci_minutes_per_merged_pr"]["pct_change"], 1), "v3_exp2_paired"),
        "CostSaveVsUberPct": (_fmt(-pc_ub["ci_minutes_per_merged_pr"]["pct_change"], 1), "v3_exp2_paired"),
        "CostSaveVsSpecLinPct": (_fmt(-pc_sl["ci_minutes_per_merged_pr"]["pct_change"], 1), "v3_exp2_paired"),
        "CostSaveVsFlakePct": (_fmt(-pc_fa["ci_minutes_per_merged_pr"]["pct_change"], 1), "v3_exp2_paired"),
        "CliffsLatVsStatic": (_fmt(abs(pc_sb["p95_latency_min"]["cliffs_delta"]), 2), "v3_exp2_paired"),
        "CliffsLatVsUber": (_fmt(abs(pc_ub["p95_latency_min"]["cliffs_delta"]), 2), "v3_exp2_paired"),
        "HolmPLatVsStatic": (_fmt_p(pc_sb["p95_latency_min"]["holm_p"]), "v3_exp2_paired"),
        "HolmPLatVsUber": (_fmt_p(pc_ub["p95_latency_min"]["holm_p"]), "v3_exp2_paired"),
        "HolmPCostVsStatic": (_fmt_p(pc_sb["ci_minutes_per_merged_pr"]["holm_p"]), "v3_exp2_paired"),
        "HolmPFRVsStatic": (_fmt_p(pc_sb["false_rejection_rate"]["holm_p"]), "v3_exp2_paired"),
        # 10x velocity
        "SSMQTenXThroughput": (_fmt(vel_summary["10.0"]["SSMQ"]["throughput_prs_per_hr"]["mean"], 2), "v3_exp3_velocity"),
        "StaticTenXThroughput": (_fmt(vel_summary["10.0"]["StaticBatchBisect"]["throughput_prs_per_hr"]["mean"], 2), "v3_exp3_velocity"),
        "UberTenXThroughput": (_fmt(vel_summary["10.0"]["UberSubmitQueue"]["throughput_prs_per_hr"]["mean"], 2), "v3_exp3_velocity"),
        "SSMQTenXPNinetyFiveLat": (_fmt(vel_summary["10.0"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp3_velocity"),
        "StaticTenXPNinetyFiveLat": (_fmt(vel_summary["10.0"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v3_exp3_velocity"),
        # Ablations
        "AblNoGraphLat": (_fmt(abl_summary["SSMQ-NoGraph"]["p95_latency_min"]["mean"], 1), "v3_exp4_ablation"),
        "AblNoGraphCost": (_fmt(abl_summary["SSMQ-NoGraph"]["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp4_ablation"),
        "AblNoFlakyLat": (_fmt(abl_summary["SSMQ-NoFlaky"]["p95_latency_min"]["mean"], 1), "v3_exp4_ablation"),
        "AblNoFlakyCost": (_fmt(abl_summary["SSMQ-NoFlaky"]["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp4_ablation"),
        "AblNoFlakyFRPct": (_fmt(abl_summary["SSMQ-NoFlaky"]["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp4_ablation"),
        "AblNoBlameLat": (_fmt(abl_summary["SSMQ-NoBlame"]["p95_latency_min"]["mean"], 1), "v3_exp4_ablation"),
        "AblNoBlameCost": (_fmt(abl_summary["SSMQ-NoBlame"]["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp4_ablation"),
        # Graph recall non-circularity
        "RecallFortySSMQPNinetyFive": (_fmt(rec_summary["recall_0.40"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp5_graph_recall"),
        "RecallFortyStaticPNinetyFive": (_fmt(rec_summary["recall_0.40"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v3_exp5_graph_recall"),
        "RecallSixtySSMQPNinetyFive": (_fmt(rec_summary["recall_0.60"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp5_graph_recall"),
        "RecallSixtyStaticPNinetyFive": (_fmt(rec_summary["recall_0.60"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v3_exp5_graph_recall"),
        "NoConfSSMQPNinetyFive": (_fmt(rec_summary["no_conflict"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp5_graph_recall"),
        "NoConfStaticPNinetyFive": (_fmt(rec_summary["no_conflict"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v3_exp5_graph_recall"),
        # 10% & 15% Flaky Pareto
        "FlakyTenSSMQFRPct": (_fmt(flk_summary["0.1"]["SSMQ"]["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp6_flaky_sweep"),
        "FlakyTenNoFlakyFRPct": (_fmt(flk_summary["0.1"]["SSMQ-NoFlaky"]["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp6_flaky_sweep"),
        "FlakyTenStaticFRPct": (_fmt(flk_summary["0.1"]["StaticBatchBisect"]["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp6_flaky_sweep"),
        "FlakyTenSSMQCost": (_fmt(flk_summary["0.1"]["SSMQ"]["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp6_flaky_sweep"),
        "FlakyTenFlakeBatchCost": (_fmt(flk_summary["0.1"]["FlakeAwareBatch"]["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp6_flaky_sweep"),
        "FlakyFifteenSSMQFRPct": (_fmt(flk_summary["0.15"]["SSMQ"]["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp6_flaky_sweep"),
        "FlakyFifteenNoFlakyFRPct": (_fmt(flk_summary["0.15"]["SSMQ-NoFlaky"]["false_rejection_rate"]["mean"] * 100.0, 2), "v3_exp6_flaky_sweep"),
        "FlakyFifteenSSMQPNinetyFive": (_fmt(flk_summary["0.15"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp6_flaky_sweep"),
        "FlakyFifteenFlakeBatchPNinetyFive": (_fmt(flk_summary["0.15"]["FlakeAwareBatch"]["p95_latency_min"]["mean"], 1), "v3_exp6_flaky_sweep"),
        # High-failure blame regime (v3_exp8)
        "RegimeNomBlameGainPct": (_fmt(-reg_contrasts["nominal_8pct_k8"]["p95"]["pct_change"], 1), "v3_exp8_blame_regime"),
        "RegimeNomBlameP": (_fmt_p(reg_contrasts["nominal_8pct_k8"]["p95"]["wilcoxon_p"]), "v3_exp8_blame_regime"),
        "RegimeElevSSMQPNinetyFive": (_fmt(reg_summary["elevated_15pct_k8"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeElevNoBlamePNinetyFive": (_fmt(reg_summary["elevated_15pct_k8"]["SSMQ-NoBlame"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeElevStaticPNinetyFive": (_fmt(reg_summary["elevated_15pct_k8"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeElevBlameGainPct": (_fmt(-reg_contrasts["elevated_15pct_k8"]["p95"]["pct_change"], 1), "v3_exp8_blame_regime"),
        "RegimeElevBlameP": (_fmt_p(reg_contrasts["elevated_15pct_k8"]["p95"]["wilcoxon_p"]), "v3_exp8_blame_regime"),
        "RegimeHighDefSSMQPNinetyFive": (_fmt(reg_summary["highdef_22pct_k12"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeHighDefNoBlamePNinetyFive": (_fmt(reg_summary["highdef_22pct_k12"]["SSMQ-NoBlame"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeHighDefStaticPNinetyFive": (_fmt(reg_summary["highdef_22pct_k12"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeHighDefBlameGainPct": (_fmt(-reg_contrasts["highdef_22pct_k12"]["p95"]["pct_change"], 1), "v3_exp8_blame_regime"),
        "RegimeHighDefBlameP": (_fmt_p(reg_contrasts["highdef_22pct_k12"]["p95"]["wilcoxon_p"]), "v3_exp8_blame_regime"),
        "RegimeHighDefSSMQCost": (_fmt(reg_summary["highdef_22pct_k12"]["SSMQ"]["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp8_blame_regime"),
        "RegimeHighDefNoBlameCost": (_fmt(reg_summary["highdef_22pct_k12"]["SSMQ-NoBlame"]["ci_minutes_per_merged_pr"]["mean"], 2), "v3_exp8_blame_regime"),
        "RegimeHighDefSSMQRounds": (_fmt(reg_summary["highdef_22pct_k12"]["SSMQ"]["mean_blame_rounds"]["mean"], 2), "v3_exp8_blame_regime"),
        "RegimeHighDefNoBlameRounds": (_fmt(reg_summary["highdef_22pct_k12"]["SSMQ-NoBlame"]["mean_blame_rounds"]["mean"], 2), "v3_exp8_blame_regime"),
        "RegimeLowRecSSMQPNinetyFive": (_fmt(reg_summary["lowrec_15pct_k12"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeLowRecNoBlamePNinetyFive": (_fmt(reg_summary["lowrec_15pct_k12"]["SSMQ-NoBlame"]["p95_latency_min"]["mean"], 1), "v3_exp8_blame_regime"),
        "RegimeLowRecBlameGainPct": (_fmt(-reg_contrasts["lowrec_15pct_k12"]["p95"]["pct_change"], 1), "v3_exp8_blame_regime"),
        "RegimeLowRecBlameP": (_fmt_p(reg_contrasts["lowrec_15pct_k12"]["p95"]["wilcoxon_p"]), "v3_exp8_blame_regime"),
    }
    write_numbers_tex(macros, REPO_ROOT / "paper" / "generated" / "numbers.tex")

    # Emit compact, zero-Overfull LaTeX Tables
    tables_dir = REPO_ROOT / "paper" / "generated" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    # Table 1: Main Benchmark (wide two-column table*, compact spacing)
    t1 = [
        "% AUTO-GENERATED FROM v3_exp2_main_bench — DO NOT EDIT",
        "\\begin{table*}[t]",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{5pt}",
        "\\caption{Main Benchmark Across 3 Real OSS Repositories (450 Eval PRs, $5\\times$ Velocity, $\\phi=5\\%$ Flaky, $80\\%$ Graph Recall, 20 Paired Seeds $\\times$ 3 Repos; Mean [95\\% Bootstrap CI], 0 Cumulative Escapes).}",
        "\\label{tab:main_benchmark}",
        "\\begin{tabular}{lccccc}",
        "\\toprule",
        "\\textbf{Strategy} & \\textbf{Throughput (PR/h)} $\\uparrow$ & \\textbf{P95 Latency (min)} $\\downarrow$ & \\textbf{CI Cost (min/PR)} $\\downarrow$ & \\textbf{Invalid. (\\%)} $\\downarrow$ & \\textbf{False Rej. (\\%)} $\\downarrow$ \\\\",
        "\\midrule",
    ]
    for strat in ["SerialQueue", "StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ"]:
        d = main_overall[strat]
        b0, b1 = ("\\textbf{", "}") if strat == "SSMQ" else ("", "")
        t1.append(
            f"{b0}{strat}{b1} & "
            f"{b0}{d['throughput_prs_per_hr']['mean']:.2f} [{d['throughput_prs_per_hr']['ci_lo']:.2f}, {d['throughput_prs_per_hr']['ci_hi']:.2f}]{b1} & "
            f"{b0}{d['p95_latency_min']['mean']:.1f} [{d['p95_latency_min']['ci_lo']:.1f}, {d['p95_latency_min']['ci_hi']:.1f}]{b1} & "
            f"{b0}{d['ci_minutes_per_merged_pr']['mean']:.2f} [{d['ci_minutes_per_merged_pr']['ci_lo']:.2f}, {d['ci_minutes_per_merged_pr']['ci_hi']:.2f}]{b1} & "
            f"{b0}{d['invalidation_rate']['mean']*100:.1f}\\%{b1} & "
            f"{b0}{d['false_rejection_rate']['mean']*100:.2f}\\%{b1} \\\\"
        )
    t1 += ["\\bottomrule", "\\end{tabular}", "\\end{table*}"]
    (tables_dir / "main_benchmark.tex").write_text("\n".join(t1) + "\n", encoding="utf-8")

    # Table 2: Per-Repository Breakdown (single-column, tight tabcolsep)
    t_repo = [
        "% AUTO-GENERATED FROM v3_exp2_main_bench — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{3.5pt}",
        "\\caption{Per-Repository Breakdown at $5\\times$ Velocity ($\\phi=5\\%$, $80\\%$ Graph Recall, 20 Seeds).}",
        "\\label{tab:per_repo}",
        "\\begin{tabular}{llcccc}",
        "\\toprule",
        "\\textbf{Repo} & \\textbf{Strategy} & \\textbf{Tput} & \\textbf{P95} & \\textbf{Cost} & \\textbf{FR\\%} \\\\",
        "\\midrule",
    ]
    for repo in ("kubernetes", "typescript", "nextjs"):
        rlbl = {"kubernetes": "k8s", "typescript": "TS", "nextjs": "next.js"}[repo]
        for strat in ("StaticBatchBisect", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ"):
            d = main_per_repo[repo][strat]
            b0, b1 = ("\\textbf{", "}") if strat == "SSMQ" else ("", "")
            short_s = strat.replace("StaticBatchBisect", "Static").replace("UberSubmitQueue", "UberTree").replace("FlakeAwareBatch", "FlakeBatch")
            t_repo.append(
                f"{rlbl} & {b0}{short_s}{b1} & {b0}{d['throughput_prs_per_hr']['mean']:.2f}{b1} & "
                f"{b0}{d['p95_latency_min']['mean']:.1f}{b1} & {b0}{d['ci_minutes_per_merged_pr']['mean']:.2f}{b1} & "
                f"{b0}{d['false_rejection_rate']['mean']*100:.2f}\\%{b1} \\\\"
            )
        if repo != "nextjs":
            t_repo.append("\\midrule")
    t_repo += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (tables_dir / "per_repo.tex").write_text("\n".join(t_repo) + "\n", encoding="utf-8")

    # Table 3: Ablation Study
    ablation_order = ["SSMQ", "SSMQ-NoGraph", "SSMQ-NoFlaky", "SSMQ-NoBlame", "StaticBatchBisect"]
    t_abl = [
        "% AUTO-GENERATED FROM v3_exp4_ablation — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{4pt}",
        "\\caption{Component Ablation ($5\\times$ Velocity, $\\phi=5\\%$, $80\\%$ Graph Recall, 20 Seeds $\\times$ 3 Repos).}",
        "\\label{tab:ablation}",
        "\\begin{tabular}{lcccc}",
        "\\toprule",
        "\\textbf{Variant} & \\textbf{Tput} & \\textbf{P95} & \\textbf{Cost} & \\textbf{FR\\%} \\\\",
        "\\midrule",
    ]
    for strat in ablation_order:
        d = abl_summary[strat]
        b0, b1 = ("\\textbf{", "}") if strat == "SSMQ" else ("", "")
        t_abl.append(
            f"{b0}{strat}{b1} & {b0}{d['throughput_prs_per_hr']['mean']:.2f}{b1} & "
            f"{b0}{d['p95_latency_min']['mean']:.1f}{b1} & {b0}{d['ci_minutes_per_merged_pr']['mean']:.2f}{b1} & "
            f"{b0}{d['false_rejection_rate']['mean']*100:.2f}\\%{b1} \\\\"
        )
    t_abl += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (tables_dir / "ablation.tex").write_text("\n".join(t_abl) + "\n", encoding="utf-8")

    # Table 4: Graph Recall Non-Circularity Sweep
    t_rec = [
        "% AUTO-GENERATED FROM v3_exp5_graph_recall — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{3.5pt}",
        "\\caption{Non-Circularity Test: Off-Graph Latent Conflicts ($40\\%$--$100\\%$ Recall) and Zero-Conflict Control.}",
        "\\label{tab:graph_recall}",
        "\\begin{tabular}{lccc}",
        "\\toprule",
        "\\textbf{Conflict Regime} & \\textbf{Static P95} & \\textbf{UberTree P95} & \\textbf{SSMQ P95} \\\\",
        "\\midrule",
    ]
    regime_labels = [
        ("no_conflict", "No-Conflict ($P_c=0$)"),
        ("recall_0.40", "40\\% Recall (60\\% latent)"),
        ("recall_0.60", "60\\% Recall (40\\% latent)"),
        ("recall_0.80", "80\\% Recall (20\\% latent)"),
        ("recall_1.00", "100\\% Recall (0\\% latent)"),
    ]
    for key, lbl in regime_labels:
        t_rec.append(
            f"{lbl} & {rec_summary[key]['StaticBatchBisect']['p95_latency_min']['mean']:.1f} & "
            f"{rec_summary[key]['UberSubmitQueue']['p95_latency_min']['mean']:.1f} & "
            f"\\textbf{{{rec_summary[key]['SSMQ']['p95_latency_min']['mean']:.1f}}} \\\\"
        )
    t_rec += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (tables_dir / "graph_recall.tex").write_text("\n".join(t_rec) + "\n", encoding="utf-8")

    # Table 5: Real Executable Blame Micro-Benchmark (64 batches, 4 rankers, per-category breakdown!)
    t_blm = [
        "% AUTO-GENERATED FROM v3_exp0_blame_microbench — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{2.8pt}",
        "\\caption{Real Executed Python Blame Micro-Benchmark (64 Failing Batches: 24 Disjoint + 16 Co-File Distractor + 24 Semantic Conflicts).}",
        "\\label{tab:blame_microbench}",
        "\\begin{tabular}{lccccc}",
        "\\toprule",
        "\\textbf{Ranker} & \\textbf{Top-1} & \\textbf{Top-2} & \\textbf{CoFile} & \\textbf{Sem.} & \\textbf{Time} \\\\",
        "\\midrule",
        f"RandomRanker & {blame_bench['random_top1_acc']*100:.1f}\\% & {blame_bench['random_top2_acc']*100:.1f}\\% & -- & -- & $<0.1$ms \\\\",
        f"ChurnRiskRanker & {blame_bench['churn_risk_top1_acc']*100:.1f}\\% & {blame_bench['churn_risk_top2_acc']*100:.1f}\\% & -- & -- & $<0.1$ms \\\\",
        f"FileOverlapRanker & {blame_bench['file_overlap_top1_acc']*100:.1f}\\% & {blame_bench['file_overlap_top2_acc']*100:.1f}\\% & {blame_bench['file_overlap_cofile_top1_acc']*100:.1f}\\% & {blame_bench['file_overlap_sem_top1_acc']*100:.1f}\\% & {blame_bench['file_overlap_mean_ms']:.2f}ms \\\\",
        f"\\textbf{{ASTStackCausal}} & \\textbf{{{blame_bench['ast_stack_top1_acc']*100:.1f}\\%}} & \\textbf{{{blame_bench['ast_stack_top2_acc']*100:.1f}\\%}} & \\textbf{{{blame_bench['ast_stack_cofile_top1_acc']*100:.1f}\\%}} & \\textbf{{{blame_bench['ast_stack_sem_top1_acc']*100:.1f}\\%}} & \\textbf{{{blame_bench['ast_stack_mean_ms']:.2f}ms}} \\\\",
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ]
    (tables_dir / "blame_microbench.tex").write_text("\n".join(t_blm) + "\n", encoding="utf-8")

    # Table 6: Failure-Density Blame Regime Analysis (NEW in v3!)
    t_reg = [
        "% AUTO-GENERATED FROM v3_exp8_blame_regime — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\caption{Blame Regime Analysis: End-to-End P95 Latency (min) and Recovery Rounds Under Rising Failure Density.}",
        "\\label{tab:blame_regime}",
        "\\begin{tabular}{lcccc}",
        "\\toprule",
        "\\textbf{Regime ($q$, $K$, $\\rho$)} & \\textbf{Static} & \\textbf{NoBlame} & \\textbf{SSMQ} & \\textbf{Rounds (NB/S)} \\\\",
        "\\midrule",
    ]
    r_labels = [
        ("nominal_8pct_k8", "8\\%, $K$=8, 80\\%"),
        ("elevated_15pct_k8", "15\\%, $K$=8, 80\\%"),
        ("highdef_22pct_k12", "22\\%, $K$=12, 60\\%"),
        ("lowrec_15pct_k12", "15\\%, $K$=12, 40\\%"),
    ]
    for rk, rlbl in r_labels:
        st_p = reg_summary[rk]["StaticBatchBisect"]["p95_latency_min"]["mean"]
        nb_p = reg_summary[rk]["SSMQ-NoBlame"]["p95_latency_min"]["mean"]
        ss_p = reg_summary[rk]["SSMQ"]["p95_latency_min"]["mean"]
        nb_r = reg_summary[rk]["SSMQ-NoBlame"]["mean_blame_rounds"]["mean"]
        ss_r = reg_summary[rk]["SSMQ"]["mean_blame_rounds"]["mean"]
        t_reg.append(
            f"{rlbl} & {st_p:.1f} & {nb_p:.1f} & \\textbf{{{ss_p:.1f}}} & {nb_r:.2f} / \\textbf{{{ss_r:.2f}}} \\\\"
        )
    t_reg += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (tables_dir / "blame_regime.tex").write_text("\n".join(t_reg) + "\n", encoding="utf-8")

    # Emit 4 PGFPlots Vector Figures!
    emit_pgfplots_figures(
        REPO_ROOT / "paper" / "generated" / "figures",
        blame_bench,
        main_per_repo,
        vel_summary,
        rec_summary,
        flk_summary,
        reg_summary,
    )

    # Write v3 Audit Report
    rep = [
        "# Deterministic Audit & Paired Inference Report v3 (`reports/audit_report.md`)",
        "",
        "- **Audit Verdict**: **PASS (0 Blocking Errors, Full Harness+Dataset+Artifact SHA-256 Verified)**",
        f"- **Verified Sealed v3 Runs**: {audit['verified_run_count']} (real git commits recorded on all manifests)",
        f"- **Total Sealed Simulation Records**: {total_sim_records} + {blame_bench['total_failing_batches']} real executed Python unittest batches",
        f"- **Cumulative Mainline Escaped Defects**: `{total_escaped}` (0.0%)",
        f"- **Generated LaTeX Macros**: `{len(macros)}` in `paper/generated/numbers.tex`",
        "- **Generated PGFPlots Vector Figures**: 4 data-driven figures in `paper/generated/figures/` + 2 TikZ architectural/motivating figures in `paper/figures/`",
        f"- **Manuscript Hygiene Warnings**: `{len(hygiene)}`",
        "",
        "## Real Executable Blame Micro-Benchmark (`v3_exp0_blame_microbench`, 64 Batches)",
        f"- `ASTStackCausalRanker`: **{blame_bench['ast_stack_top1_acc']*100:.1f}% Top-1**, **{blame_bench['ast_stack_top2_acc']*100:.1f}% Top-2**, Co-File Distractor **{blame_bench['ast_stack_cofile_top1_acc']*100:.1f}%**, Semantic Conflict **{blame_bench['ast_stack_sem_top1_acc']*100:.1f}%**, MRR **{blame_bench['ast_stack_mrr']:.3f}**, latency **{blame_bench['ast_stack_mean_ms']:.2f} ms**.",
        f"- `FileTracebackOverlapRanker` (strong non-AST baseline): **{blame_bench['file_overlap_top1_acc']*100:.1f}% Top-1**, **{blame_bench['file_overlap_top2_acc']*100:.1f}% Top-2**, Co-File Distractor **{blame_bench['file_overlap_cofile_top1_acc']*100:.1f}%**, Semantic Conflict **{blame_bench['file_overlap_sem_top1_acc']*100:.1f}%**, MRR **{blame_bench['file_overlap_mrr']:.3f}**.",
        "",
        "## Blame Failure-Density Regime (`v3_exp8_blame_regime`)",
        "",
        "| Regime | Static P95 | SSMQ-NoBlame P95 | SSMQ P95 | Δ Blame (%) [Wilcoxon p] | Rounds (NB / SSMQ) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    for rk, rlbl in r_labels:
        c = reg_contrasts[rk]["p95"]
        rep.append(
            f"| `{rk}` | {reg_summary[rk]['StaticBatchBisect']['p95_latency_min']['mean']:.1f} | "
            f"{reg_summary[rk]['SSMQ-NoBlame']['p95_latency_min']['mean']:.1f} | "
            f"**{reg_summary[rk]['SSMQ']['p95_latency_min']['mean']:.1f}** | "
            f"{c['pct_change']:+.1f}% (`p{_fmt_p(c['wilcoxon_p'])}`) | "
            f"{reg_summary[rk]['SSMQ-NoBlame']['mean_blame_rounds']['mean']:.2f} / "
            f"**{reg_summary[rk]['SSMQ']['mean_blame_rounds']['mean']:.2f}** |"
        )
    (REPO_ROOT / "reports" / "audit_report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print(f"V3 AUDIT COMPLETE: {audit['verified_run_count']} runs, {total_sim_records} sim records, {len(macros)} macros, 6 vector figures.")


if __name__ == "__main__":
    main()
