# Deterministic Audit & Paired Inference Report v2 (`reports/audit_report.md`)

- **Audit Verdict**: **PASS (0 Blocking Errors, Full Harness+Dataset+Artifact SHA-256 Verified)**
- **Verified Sealed v2 Runs**: 8 (real git commits recorded on all manifests)
- **Total Sealed Simulation Records**: 4920 + 48 real executed Python unittest batches
- **Cumulative Mainline Escaped Defects**: `0` (0.0%)
- **Generated LaTeX Macros**: `79` in `paper/generated/numbers.tex`

## Real Executable Blame Micro-Benchmark (`v2_exp0_blame_microbench`)
- `ASTStackCausalRanker`: **91.7% Top-1**, **100.0% Top-2**, MRR **0.958**, mean latency **2.29 ms** (vs Random 29.2% / Churn 2.1%).

## Main Benchmark (`v2_exp2_main_bench`, 5x Velocity, 5% Flaky, 80% Graph Recall, 3% Persistent Conflict)

| Strategy | Throughput (PR/h) | P95 Lat (min) | CI Cost (m/PR) | Invalid. (%) | False Rej. (%) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **SerialQueue** | 3.63 | 1408.8 | 16.65 | 0.0% | 4.22% |
| **StaticBatchBisect** | 16.05 | 121.9 | 19.78 | 0.9% | 1.96% |
| **SpeculativeLinear** | 5.37 | 899.8 | 30.98 | 40.3% | 2.13% |
| **UberSubmitQueue** | 10.27 | 342.7 | 43.42 | 75.2% | 1.73% |
| **FlakeAwareBatch** | 12.57 | 235.5 | 29.92 | 1.9% | 0.12% |
| **SSMQ** | 17.77 | 79.5 | 17.06 | 1.5% | 1.64% |

## Matched Paired Contrasts (SSMQ vs Baselines, Holm-Bonferroni Adjusted Wilcoxon p-values)

| Baseline | Δ Tput (%) | Δ P95 Lat (%) [Holm p] | Δ CI Cost (%) [Holm p] | Δ False Rej. (pp) [Holm p] |
| :--- | :---: | :---: | :---: | :---: |
| SerialQueue | +389.8% | -94.4% (`p=<0.0001`) | +2.5% (`p=0.7741`) | -2.58pp (`p=<0.0001`) |
| StaticBatchBisect | +10.7% | -34.8% (`p=<0.0001`) | -13.8% (`p=<0.0001`) | -0.32pp (`p=0.5627`) |
| SpeculativeLinear | +230.8% | -91.2% (`p=<0.0001`) | -44.9% (`p=<0.0001`) | -0.49pp (`p=0.1028`) |
| UberSubmitQueue | +73.2% | -76.8% (`p=<0.0001`) | -60.7% (`p=<0.0001`) | -0.08pp (`p=0.7741`) |
| FlakeAwareBatch | +41.4% | -66.2% (`p=<0.0001`) | -43.0% (`p=<0.0001`) | +1.52pp (`p=<0.0001`) |

## Non-Circularity Check: Graph Recall Sweep & No-Conflict Control (`v2_exp5_graph_recall`)

| Regime | Static P95 (m) | UberTree P95 (m) | SSMQ P95 (m) | SSMQ Tput (PR/h) |
| :--- | :---: | :---: | :---: | :---: |
| No-Conflict Control ($P_c=0$) | 43.7 | 153.3 | **49.5** | 23.86 |
| 40\% Graph Recall (60\% latent) | 148.6 | 442.3 | **111.2** | 14.62 |
| 60\% Graph Recall (40\% latent) | 135.2 | 388.0 | **93.9** | 16.18 |
| 80\% Graph Recall (20\% latent) | 121.9 | 342.7 | **79.5** | 17.77 |
| 100\% Graph Recall (0\% latent) | 112.1 | 307.3 | **69.4** | 18.90 |
