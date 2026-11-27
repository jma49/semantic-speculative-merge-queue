# Semantic Speculative Merge Queues

The current anonymous IEEEtran manuscript is **Semantic Speculative Merge Queues: When to Separate Changes and When to Retest**. The evaluation uses public PR merge sequences, paired finite-burst simulations, executable Git replays, and published QuixBugs faults. Queue arrivals and interference in the public projects are modeled. The Git and Python experiments execute tests.

From the repository root, verify the current release with Python and Git:

```sh
python3 eval/verify_v6.py --verify-only
python3 -W error -m unittest discover -s tests
```

Regenerate the manuscript displays with `python3 eval/export_v6.py`. Build the PDF with `latexmk -pdf main.tex` in `paper/`. Create the companion review supplement with `python3 scripts/build_review_zip.py --output ssmq-review.zip`. The builder verifies a freshly extracted copy.

Run manifests under `runs/v6_*` identify the source and input hashes for the current paper. Earlier numbered runs and their audit scripts are retained for historical comparison.
