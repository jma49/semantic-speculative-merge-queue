"""File dependencies learned exclusively from historical pull requests."""

from collections import defaultdict
from math import ceil, log1p
from typing import FrozenSet, Iterable, Set, Tuple

from .models import PRRecord


def _module(path: str) -> Tuple[str, ...]:
    return tuple(path.split("/")[:-1][:2])


def _repository_roots(files: Iterable[str]) -> FrozenSet[str]:
    return frozenset(path.partition("/")[0] for path in files if "/" in path)


def _scale(values: Iterable[float]) -> float:
    ordered = sorted(values)
    return max(1.0, ordered[ceil(0.9 * len(ordered)) - 1]) if ordered else 1.0


class DependencyGraph:
    """Index co-change cliques and directory cliques as sets of members."""

    def __init__(self, warmup: Iterable[PRRecord] = ()):
        history = tuple(warmup)
        cochanges = defaultdict(list)
        modules = defaultdict(set)
        for pr in history:
            files = frozenset(pr.files)
            for path in sorted(files):
                cochanges[path].append(files)
                modules[_module(path)].add(path)

        self._cochanges = dict(cochanges)
        self._modules = dict(modules)
        self._centrality = {
            path: len(self.impact_cone((path,))) - 1 for path in self._cochanges
        }
        self._centrality_scale = _scale(map(log1p, self._centrality.values()))
        self._churn_scale = _scale(log1p(pr.additions + pr.deletions) for pr in history)
        self._size_scale = _scale(log1p(self._file_count(pr)) for pr in history)

    @staticmethod
    def _file_count(pr: PRRecord) -> int:
        return max(pr.changed_files_count, len(pr.files))

    def impact_cone(self, files: Iterable[str]) -> Set[str]:
        touched = set(files)
        cone = set(touched)
        for path in touched:
            cone.update(self._modules.get(_module(path), ()))
            for cochange in self._cochanges.get(path, ()):
                cone.update(cochange)
        return cone

    def overlap_score(self, pr_a: PRRecord, pr_b: PRRecord) -> float:
        left = pr_a.impact_cone or self.impact_cone(pr_a.files)
        right = pr_b.impact_cone or self.impact_cone(pr_b.files)
        shared = len(left & right)
        return shared / (len(left) + len(right) - shared) if shared else 0.0

    def risk_score(self, pr: PRRecord) -> float:
        """Weight log churn, log file count, and the most central changed file."""
        degree = max((self._centrality.get(path, 0) for path in pr.files), default=0)
        churn = min(1.0, log1p(pr.additions + pr.deletions) / self._churn_scale)
        size = min(1.0, log1p(self._file_count(pr)) / self._size_scale)
        hotspot = min(1.0, log1p(degree) / self._centrality_scale)
        return 0.45 * churn + 0.30 * size + 0.25 * hotspot
