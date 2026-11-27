# Deterministic Audit & Paired Inference Report v3 (`reports/audit_report.md`)

- **Audit Verdict**: **PASS (0 Blocking Errors, Full Harness+Dataset+Artifact SHA-256 Verified)**
- **Verified Sealed v3 Runs**: 9 (real git commits recorded on all manifests)
- **Total Sealed Simulation Records**: 6000 + 64 real executed Python unittest batches
- **Cumulative Mainline Escaped Defects**: `0` (0.0%)
- **Generated LaTeX Macros**: `120` in `paper/generated/numbers.tex`
- **Generated PGFPlots Vector Figures**: 4 data-driven figures in `paper/generated/figures/` + 2 TikZ architectural/motivating figures in `paper/figures/`
- **Manuscript Hygiene Warnings**: `0`

## Real Executable Blame Micro-Benchmark (`v3_exp0_blame_microbench`, 64 Batches)
- `ASTStackCausalRanker`: **93.8% Top-1**, **100.0% Top-2**, Co-File Distractor **100.0%**, Semantic Conflict **100.0%**, MRR **0.969**, latency **2.25 ms**.
- `FileTracebackOverlapRanker` (strong non-AST baseline): **68.8% Top-1**, **100.0% Top-2**, Co-File Distractor **0.0%**, Semantic Conflict **100.0%**, MRR **0.844**.

## Blame Failure-Density Regime (`v3_exp8_blame_regime`)

| Regime | Static P95 | SSMQ-NoBlame P95 | SSMQ P95 | Δ Blame (%) [Wilcoxon p] | Rounds (NB / SSMQ) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `nominal_8pct_k8` | 121.9 | 80.6 | **79.5** | -1.4% (`p=0.0352`) | 0.85 / **0.84** |
| `elevated_15pct_k8` | 132.3 | 75.7 | **73.8** | -2.5% (`p=0.0012`) | 0.55 / **0.54** |
| `highdef_22pct_k12` | 159.7 | 86.0 | **85.6** | -0.5% (`p=0.7488`) | 0.54 / **0.55** |
| `lowrec_15pct_k12` | 166.6 | 95.2 | **94.0** | -1.3% (`p=0.5100`) | 0.63 / **0.63** |
