# Independent Codex methods review of the v8 evidence

**Disposition.** The observational counts and same-family filtered-cone contrast support a scoped submission. The available cross-family simulation cannot establish an architecture-level speed ranking for real systems. This review was performed by a separate Codex instance. Neither attempted Opus review returned scientific findings.

## Candidate-state audit

I invoked the actual v8 job starter on an empty base with flakiness disabled, one pair edge and no unary defects. Both representations produce the candidate set {1,2}. With prefix checking enabled, a transient edge makes a joint batch fail and a staged prefix pass. Marking that edge persistent makes both fail. The exact-state merge guard constrains authority to commit; it does not guarantee that the model assigned equal verdicts, duration or noise to alternative representations of a candidate. Duration and transient test noise are keyed to member IDs and attempt. Common seeds define paired workloads, not identical realized CI noise across different jobs.

From the repository root the following reproduces the four verdicts.

```sh
PYTHONPATH=src:. python3 - <<'PY'
from ssmq import DependencyGraph, GroundTruth, SimConfig
from eval.baselines_v8.engine import _V8Simulation, _Job, _Recovery
edges = frozenset({(1, 2)})
for persistent in (False, True):
    for members, prefix in (((1, 2), ()), ((2,), (1,))):
        sim = _V8Simulation(DependencyGraph(), [], "GitHubMQ-StagedGroup", SimConfig(flaky_rate=0), 1001)
        sim.truth = GroundTruth(frozenset(), edges, edges if persistent else frozenset())
        run = sim._start(_Job(members, _Recovery(), prefix=prefix), check_prefix_fault=True)
        print(persistent, members, prefix, run.passes, run.culprits)
PY
```

To estimate an effect attributable solely to prefix scheduling, define a policy-independent outcome function of the full tested candidate, the committed base and a reproducible noise draw, then rerun both arms. Until then, report differences only as outcomes of the specified models.

## Filtered-cone evidence

The diagnostic changes two things together: it drops warm-up changes touching more than twenty files and drops directory cliques. Its ground truth is sampled before the filtered scheduler cone is assigned. Main-condition labels still depend on the original graph. As a targeted parity check I instantiated the v8 filtered engine, replaced its cones with its original v7 cones, and ran seeds 1001 and 1002 for all three repositories. Each of the six runs matched the sealed full-SSMQ P95 and total CI count exactly.

The raw metrics have 2,880 unique repository/seed/condition/policy keys. I recomputed filtered minus NoCone mean survivor-P95 differences of −15.58, +4.12, +38.49 and +10.00 minutes for nominal, saturated, directory proxy and uniform pairs, respectively. Nominal false rejection increased by approximately 0.82 percentage points. Report the bundled intervention and the acceptance trade-off. The nominal pointwise bootstrap interval narrowly excludes zero. Multiple exploratory policy contrasts and the post-review choice of filter preclude treating it as confirmatory. The resampling units are 20 seed blocks retaining three fixed repositories; none of the intervals estimates between-project generalization. Survivor latency is conditional on policy-specific acceptance.

## Historical evidence

I recomputed counts from timestamped timeline nodes in raw_records.json, filtering directly on the eight inclusive UTC dates (Nov 10–17). TypeScript contributed 43 distinct enqueued PRs and 44 adds among 246 fetched PRs, with 43 merged removals and one failed-check removal. Cargo contributed 40 distinct enqueued PRs and 56 adds among 161 fetched PRs, with 40 merged, 13 failed-check, two merge-conflict and one timeout removal. Thus 83 distinct enqueued PRs account for 100 admissions, within the two specified PR-number bands. Failure/removal events may recur on one PR and do not measure permanent rejection or uniquely identifiable faulty changes. The 45 TypeScript workflow runs form a separate denominator. A synthetic queue SHA differing from a fetch-time PR head is expected even without a PR update. The stable enqueue-time head required to attribute a failure, quantify same-code retries or label flakiness is unavailable.

This assessment covers the sealed evidence. I did not observe a live deployment, replay the counterfactual histories of real repositories or certify the final typeset manuscript.
