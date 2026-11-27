# Gate 2 Deterministic Audit & Reconciliation Report (`reports/audit_report.md`)

- **Audit Verdict**: **PASS (0 Blocking Errors)**
- **Verified Sealed Runs**: 6
- **Total Sealed Evaluation Records**: 3480
- **Mainline Safety Invariant (Escaped Defects)**: `0` (0.0%)
- **Generated LaTeX Macros**: `61` in `paper/generated/numbers.tex`

## A/A Noise Floor Calibration (`exp0_aa_noise_v1`)

- Throughput Block A vs B relative diff: **0.10%** (Cliff's $\delta = -0.016$)
- P95 Latency Block A vs B relative diff: **2.63%** (Cliff's $\delta = -0.088$)
- CI Compute Cost Block A vs B relative diff: **1.16%** (Cliff's $\delta = -0.056$)

## Main Benchmark Summary (`exp1_main_bench_v1`, 5x Velocity, 5% Flaky, 20 Seeds x 3 Repos)

| Strategy | Throughput (PR/h) | P50 Lat (m) | P95 Lat (m) | CI Cost (m/PR) | Invalid. (%) | Blame Rounds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SerialQueue** | 4.37 | 680.5 | 1426.4 | 13.74 | 0.0% | 0.00 |
| **StaticBatchBisect** | 22.08 | 34.3 | 78.4 | 12.56 | 0.0% | 1.80 |
| **SpeculativeLinear** | 7.57 | 343.1 | 746.5 | 21.36 | 31.6% | 0.89 |
| **UberSubmitQueue** | 6.27 | 474.2 | 984.0 | 12.59 | 0.1% | 0.72 |
| **FlakeAwareBatch** | 18.55 | 53.5 | 137.8 | 16.90 | 2.3% | 4.24 |
| **SSMQ** | 23.81 | 22.0 | 49.7 | 11.35 | 1.0% | 0.72 |

## Ablation Study (`exp3_ablation_v1`)

| Variant | Throughput (PR/h) | P95 Lat (m) | CI Cost (m/PR) | Blame Rounds |
| :--- | :---: | :---: | :---: | :---: |
| **SSMQ** | 23.81 | 49.7 | 11.35 | 0.72 |
| **SSMQ-NoGraph** | 21.74 | 85.9 | 12.77 | 2.17 |
| **SSMQ-NoFlaky** | 23.86 | 46.7 | 11.06 | 0.42 |
| **SSMQ-NoBlame** | 23.74 | 50.6 | 11.44 | 0.79 |
| **StaticBatchBisect** | 22.08 | 78.4 | 12.56 | 1.80 |
