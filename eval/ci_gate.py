"""CI gate — runs the trajectory eval, then compares its aggregate metrics
against eval/baseline.json and fails the build on a real regression.

Bootstrap case: if eval/baseline.json doesn't exist yet, this run's results
become the baseline (written to disk) and the script exits 0 -- there's
nothing to regress against yet.

Regression thresholds: this eval calls a live LLM (Claude) with real API
non-determinism (temperature, provider-side routing, minor prompt/model
version drift) over a small ~19-item golden set, where a single flipped
item moves overall_accuracy by ~5 percentage points. A hair-trigger gate on
a set this size would be flaky rather than useful. We therefore gate only
on the metrics that most directly reflect "the agent got the wrong thing"
(overall_accuracy, tool_selection_accuracy, citation_correctness) and allow
a tolerance of 0.10 (10 percentage points) below baseline before failing --
loose enough to absorb one item's worth of noise on this sample size, tight
enough to catch a real regression (a broken prompt, a router change, a
retrieval regression) which historically has moved these numbers by 20+
points in this repo. Latency/token cost are reported for visibility but do
NOT gate the build -- they're expected to vary with API load/model updates
and aren't a correctness signal.

Usage:
    .venv/Scripts/python.exe -m eval.ci_gate
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from eval.trajectory_eval import run_eval

EVAL_DIR = Path(__file__).resolve().parent
BASELINE_PATH = EVAL_DIR / "baseline.json"

REGRESSION_TOLERANCE = 0.10  # see module docstring for reasoning

GATED_METRICS = [
    "overall_accuracy",
    "tool_selection_accuracy",
    "citation_correctness",
]


def _load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _write_baseline(summary: dict) -> None:
    BASELINE_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> int:
    report = run_eval()
    summary = report["summary"]

    baseline = _load_baseline()
    if baseline is None:
        print(f"No baseline found at {BASELINE_PATH} -- bootstrapping baseline from this run.")
        _write_baseline(summary)
        print("Baseline written. Exiting 0 (bootstrap).")
        return 0

    print("\n" + "=" * 78)
    print("CI GATE -- comparing against baseline")
    print("=" * 78)
    print(f"{'metric':32s} {'baseline':>10s} {'current':>10s} {'delta':>10s}  gated")

    failures = []
    for metric in GATED_METRICS:
        base_val = baseline.get(metric)
        cur_val = summary.get(metric)
        if base_val is None or cur_val is None:
            print(f"{metric:32s} {'n/a':>10s} {'n/a':>10s} {'--':>10s}  (skipped, not applicable)")
            continue
        delta = cur_val - base_val
        regressed = delta < -REGRESSION_TOLERANCE
        marker = "REGRESSED" if regressed else "ok"
        print(f"{metric:32s} {base_val:10.4f} {cur_val:10.4f} {delta:+10.4f}  {marker}")
        if regressed:
            failures.append((metric, base_val, cur_val, delta))

    # Informational only -- not gated.
    for metric in ("avg_tokens_per_query", "avg_latency_ms_per_query"):
        base_val = baseline.get(metric)
        cur_val = summary.get(metric)
        if base_val is not None and cur_val is not None:
            delta = cur_val - base_val
            print(f"{metric:32s} {base_val:10.1f} {cur_val:10.1f} {delta:+10.1f}  (informational, not gated)")

    print("=" * 78)

    if failures:
        print(f"\nCI GATE FAILED -- {len(failures)} metric(s) regressed beyond tolerance ({REGRESSION_TOLERANCE}):")
        for metric, base_val, cur_val, delta in failures:
            print(f"  - {metric}: baseline={base_val:.4f} current={cur_val:.4f} delta={delta:+.4f}")
        print(f"\nBaseline is unchanged at {BASELINE_PATH}. If this regression is expected/accepted, "
              f"update the baseline deliberately (delete {BASELINE_PATH.name} and re-run, or hand-edit it) "
              f"rather than letting the gate silently pass.")
        return 1

    print("\nCI GATE PASSED -- no gated metric regressed beyond tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
