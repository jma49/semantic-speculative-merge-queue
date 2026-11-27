"""Read chronological GitHub merge traces and construct the evaluation stream."""

import json
from dataclasses import fields, replace
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import List, Tuple, Union

from .graph import DependencyGraph
from .models import PRRecord, SimConfig

_PR_FIELDS = {f.name for f in fields(PRRecord)}


def load_trace(
    path: Union[str, Path],
    warmup_fraction: float = SimConfig.warmup_fraction,
    baseline_arrivals_per_hr: float = 6.0,
) -> Tuple[DependencyGraph, List[PRRecord]]:
    """Use merge times as arrival proxies and linearly rescale their gaps.

    Evaluation starts at minute zero. Its mean inter-arrival gap is
    60 / baseline_arrivals_per_hr minutes. Velocity is applied by the simulator.
    """
    if not 0.0 <= warmup_fraction <= 1.0:
        raise ValueError("warmup_fraction must be in [0, 1]")
    if not isfinite(baseline_arrivals_per_hr) or baseline_arrivals_per_hr <= 0:
        raise ValueError("baseline_arrivals_per_hr must be finite and positive")

    with Path(path).open(encoding="utf-8") as source:
        rows = json.load(source)
    dated_records = [
        (
            datetime.fromisoformat(row["merged_at"].replace("Z", "+00:00")),
            PRRecord(**{**{k: v for k, v in row.items() if k in _PR_FIELDS}, "files": tuple(row["files"])}),
        )
        for row in rows
    ]
    dated_records.sort(key=lambda item: (item[0], item[1].number))
    split = int(len(dated_records) * warmup_fraction)
    graph = DependencyGraph(pr for _, pr in dated_records[:split])
    evaluation = dated_records[split:]
    if not evaluation:
        return graph, []

    start = evaluation[0][0]
    span_seconds = (evaluation[-1][0] - start).total_seconds()
    target_span = (len(evaluation) - 1) * 60.0 / baseline_arrivals_per_hr
    records = []
    for merged, pr in evaluation:
        if span_seconds:
            relative_time = (merged - start).total_seconds() / span_seconds
        else:
            # Preserve simultaneous arrivals when the trace has no spread.
            relative_time = 0.0
        records.append(
            replace(
                pr,
                arrival_min=relative_time * target_span,
                impact_cone=frozenset(graph.impact_cone(pr.files)),
                risk_score=graph.risk_score(pr),
            )
        )
    return graph, records
