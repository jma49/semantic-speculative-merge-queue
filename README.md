# Semantic Speculative Merge Queues

The current anonymous IEEEtran manuscript is **Semantic Speculative Merge Queues: When to Separate Changes and When to Retest** (v9 sensitivity revision). The evaluation uses 600 public merged PRs, native queue timeline events in TypeScript and Cargo, paired exact-tree discrete-event simulations with decoupled scheduler priors (nominal and saturated loads), counterbalanced and weight-matched executable Git replays, and published QuixBugs faults. Queue arrivals and interference in the public projects are modeled; the Git and Python experiments execute real tests.

From the repository root, verify the current release offline with Python and Git:

```sh
python3 eval/verify_submission.py --verify-only
python3 -W error -m unittest discover -s tests
```

Regenerate the manuscript displays with `python3 eval/export_v7.py` and `python3 eval/export_v8_displays.py`. Build the PDF with `latexmk -pdf main.tex` in `paper/`. Create the companion anonymous review supplement with `python3 scripts/build_review_zip.py --output ../ssmq-review.zip`. The builder verifies a freshly extracted copy.

Run manifests under `runs/v7_*` , `runs/v8_*` and `runs/v9_sensitivity` identify the source and input hashes for the current paper. Earlier numbered runs (`runs/v2_*` through `runs/v6_*`) and their harnesses are retained unmodified for historical comparison.
