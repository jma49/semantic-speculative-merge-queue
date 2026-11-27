"""Public API for the Semantic Speculative Merge Queue simulator."""

from .graph import DependencyGraph
from .loader import load_trace
from .models import PRRecord, RunMetrics, SimConfig
from .simulator import ALL_STRATEGIES, GroundTruth, build_ground_truth, simulate_queue

__version__ = "7.0.0"

__all__ = [
    "ALL_STRATEGIES",
    "DependencyGraph",
    "GroundTruth",
    "PRRecord",
    "RunMetrics",
    "SimConfig",
    "build_ground_truth",
    "load_trace",
    "simulate_queue",
]
