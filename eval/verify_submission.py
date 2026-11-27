"""Check the simulation, policy baselines, and observed public queue evidence offline."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from export_v8_displays import export_v8

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", required=True)
    parser.parse_args()
    checks = [
        ("eval/sensitivity_v9/verify_v9_sensitivity.py", "--verify-only"),
        ("eval/verify_v7.py", "--verify-only"),
        ("eval/baselines_v8/verify_v8_baselines.py", "--verify-only"),
        ("eval/historical_ci/verify_historical_ci.py", "--verify-only"),
        ("eval/queue_events_v8/verify_queue_events.py",),
    ]
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    for check in checks:
        subprocess.run([sys.executable, "-B", *check], cwd=ROOT, env=env, check=True)

    with tempfile.TemporaryDirectory() as temp:
        stage = Path(temp)
        export_v8(ROOT, stage)
        for artifact in stage.rglob("*"):
            if artifact.is_file() and artifact.read_bytes() != (ROOT / artifact.relative_to(stage)).read_bytes():
                raise ValueError(f"Stale manuscript display: {artifact.relative_to(stage)}")


if __name__ == "__main__":
    main()
