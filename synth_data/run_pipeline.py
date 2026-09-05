"""End-to-end CLI for the synthetic data foundry (PLAN.md Phase 4):

    python -m synth_data.run_pipeline --qa-n 40 --dpo-n 10 --export

Steps: QA generation -> judge audit of the QA rows -> DPO pair generation
(each DPO row is judged inline as part of generation) -> export to JSONL.
Each step prints progress; pass --skip-* to omit a step.
"""

from __future__ import annotations

import argparse

from synth_data import dpo_pairs, export, qa_generator
from synth_data.judge import audit_pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic data foundry pipeline end-to-end.")
    parser.add_argument("--qa-n", type=int, default=40, help="number of chunks for SFT QA generation")
    parser.add_argument("--dpo-n", type=int, default=10, help="number of chunks for DPO pair generation")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--flag-threshold", type=float, default=0.5, help="judge_score cutoff for audit flag / export filter")
    parser.add_argument("--skip-qa", action="store_true")
    parser.add_argument("--skip-audit", action="store_true")
    parser.add_argument("--skip-dpo", action="store_true")
    parser.add_argument("--skip-export", action="store_true")
    args = parser.parse_args()

    if not args.skip_qa:
        print(f"\n=== [1/4] QA generation (n={args.qa_n}) ===")
        qa_generator.run(n_chunks=args.qa_n, seed=args.seed)
    else:
        print("\n=== [1/4] QA generation SKIPPED ===")

    if not args.skip_audit:
        print(f"\n=== [2/4] Judge audit of SFT rows (flag threshold={args.flag_threshold}) ===")
        summary = audit_pairs(flag_threshold=args.flag_threshold)
        print(f"scored={summary['scored']} flagged={summary['flagged']}")
    else:
        print("\n=== [2/4] Judge audit SKIPPED ===")

    if not args.skip_dpo:
        print(f"\n=== [3/4] DPO pair generation (n={args.dpo_n}) ===")
        dpo_pairs.run(n_chunks=args.dpo_n, seed=args.seed)
    else:
        print("\n=== [3/4] DPO pair generation SKIPPED ===")

    if not args.skip_export:
        print(f"\n=== [4/4] Export to JSONL (threshold={args.flag_threshold}) ===")
        export.run(threshold=args.flag_threshold)
    else:
        print("\n=== [4/4] Export SKIPPED ===")

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
