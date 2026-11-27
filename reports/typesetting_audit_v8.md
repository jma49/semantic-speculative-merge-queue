# v8 typesetting audit (Tables II–V, algorithms, Proposition 3)

**Scope.** Compared rendered [`ssmq-paper.pdf`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/ssmq-paper.pdf) (11 pages, pre-build) against [`paper/generated/`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/paper/generated/) TeX and sealed exports via [`eval/export_v8_displays.py`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/eval/export_v8_displays.py). Raster crops under [`reports/audit_crops/`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/reports/audit_crops/). **Metrics unchanged**; only layout/readability in the generator.

## IEEE table mapping (PDF)

| Label | Source | PDF page |
|-------|--------|----------|
| Table II | `v8_obs.tex` | 2 |
| Table III | `tab:v8filtered` | 7 |
| Table IV | `tab:v8comparators` | 7 |
| Table V | `tab:v8repo_recall` | 7 |

## Alleged OCR findings

| Claim / symptom | Verdict | Evidence |
|-----------------|---------|----------|
| Table III Δ / sign-test values wrong | **Reject** | All four regime rows match `results/v8_paper.json` `filtered_cone_vs_nocone` to displayed rounding (e.g. nominal ΔP95 −15.6 [−30.5, −0.5], p 0.115; directory p 0.0026). |
| Table II TS discordant “45 (0)” vs caption “45/45” | **Reject (notation, not error)** | `descriptive_summary.json` → `typescript.merge_group_crosswalk.discordant_head_count=45`, `attribution_eligible_run_count=0`; workflow ledger `historical_ci_selected=45`, `discordant_head_cases_historical_ci=45`. Parentheses denote attrib.-eligible runs, not a second discordant count. |
| Table II Cargo 114 vs TS 45 mixed in one row | **Reject** | Per-repo crosswalk: cargo 114 (0); total 159 (0). Row is labeled merge-group discordant heads per repo. |
| Captions garble “FilteredCone”, “job.members” | **PDF text-layer artifact only** | PyMuPDF extraction inserts spaces inside `\texttt{}` strings (“FI L T E R E DCO N E”, “JOB.M E M B E R S”). Raster crops show contiguous typewriter text. |
| Table IV comparator P95/FRR cells | **Reject** | 12 policies × (nominal + saturated) = 96 numerics; automated check vs `runs/v8_baselines/metrics.json` means: **0 mismatches** (>0.1 tolerance). |
| Table V per-repo P95 / D | **Reject** | Matches `_strategy_means` on sealed baselines (e.g. k8s P95 777/631/648; D 68.5/9.9/68.5). Recall block matches `results/sim_v7.json`. |
| Proposition 3 equation / thresholds | **Reject** | PDF p.5: \(a=\lceil\log_2(K{-}1)\rceil\), \(U(\alpha_1)=\alpha_1+(1{-}\alpha_1)(1{+}a)\), threshold \(1/3\) at \(K{=}8\) aligns with [`03_methodology.tex`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/paper/sections/03_methodology.tex) `prop:complexity`. |
| Algorithms 2–3 scoring lines | **Reject** | Visual crop matches TeX (`\mathcal{F}_T`, `\mathcal{S}_T`, `allow_blame=False`); no numeric drift. |

**`03_methodology.tex`:** No genuine typos found in Prop. 3 or algorithms beyond optional notational consistency already present in source; **no edits**.

## Layout / readability changes (generator only)

[`export_v8_displays.py`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/eval/export_v8_displays.py):

- **Table II:** clearer `merge_group` discordant row label; `\addlinespace` + merged workflow footnote in one `p{\linewidth}` block; slightly wider columns.
- **Table III–V:** modest `\arraystretch`; Table IV wrapped in `\resizebox{\linewidth}{!}{...}`; Table V adds explicit lower-block header row and caption trim.

Regenerated: `paper/generated/tables/v8_obs.tex`, `v8_baselines_paper.tex` (macros/json **byte-identical** aside from TeX display files).

## Verification commands (all passed)

```sh
cd semantic-speculative-merge-queue
python3 eval/export_v8_displays.py
PYTHONPATH=src:. python3 eval/verify_submission.py --verify-only
PYTHONPATH=src:. python3 -m pytest tests/test_queue_events_v8.py eval/baselines_v8/test_baselines_v8.py -q
```

## PDF rebuild

`latexmk` / `pdflatex` not installed in this environment; audit used the checked-in PDF. Root should run `latexmk -pdf main.tex` in `paper/` to refresh the PDF after pulling regenerated TeX.

## Crops (visual)

- [`table_II_p2.png`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/reports/audit_crops/table_II_p2.png) (body text; II sits higher on page)
- [`table_III_p7.png`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/reports/audit_crops/table_III_p7.png)
- [`table_IV_p7.png`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/reports/audit_crops/table_IV_p7.png) (included in III crop)
- [`table_V_p7.png`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/reports/audit_crops/table_V_p7.png)
- [`prop3_alg_p4.png`](/Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/semantic-speculative-merge-queue/reports/audit_crops/prop3_alg_p4.png)

**Disposition:** No numeric corrections required. OCR-style reports against this PDF are false positives from the text extraction layer or misread parenthetical notation. Layout-only generator tweak applied; recompile PDF at root when TeX is available.