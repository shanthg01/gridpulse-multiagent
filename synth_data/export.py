"""Exports `synthetic_pairs` to JSONL under data/synthetic/, in both:
  - SFT format: {"prompt":..., "completion":...}   (rows with rejected IS NULL)
  - DPO format: {"prompt":..., "chosen":..., "rejected":...}  (rows with both)

Rows with a judge_score below `threshold` are excluded from export (flagged,
not deleted -- still in the DB for inspection/re-audit). Rows never audited
(judge_score IS NULL, e.g. freshly generated SFT rows before judge.py has
run) are included by default; pass --require-audit to exclude those too.
"""

from __future__ import annotations

import json
from pathlib import Path

from synth_data.db import get_conn

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"


def export_sft(threshold: float = 0.5, require_audit: bool = False) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prompt, chosen, judge_score
                FROM synthetic_pairs
                WHERE rejected IS NULL
                ORDER BY id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    out = []
    for prompt, chosen, judge_score in rows:
        if judge_score is None:
            if require_audit:
                continue
        elif judge_score < threshold:
            continue
        out.append({"prompt": prompt, "completion": chosen})
    return out


def export_dpo(threshold: float = 0.5, require_audit: bool = False) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prompt, chosen, rejected, judge_score
                FROM synthetic_pairs
                WHERE rejected IS NOT NULL
                ORDER BY id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    out = []
    for prompt, chosen, rejected, judge_score in rows:
        if judge_score is None:
            if require_audit:
                continue
        elif judge_score < threshold:
            continue
        out.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
    return out


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(threshold: float = 0.5, require_audit: bool = False, output_dir: Path = OUTPUT_DIR) -> dict:
    sft_rows = export_sft(threshold=threshold, require_audit=require_audit)
    dpo_rows = export_dpo(threshold=threshold, require_audit=require_audit)

    sft_path = output_dir / "sft.jsonl"
    dpo_path = output_dir / "dpo.jsonl"
    write_jsonl(sft_rows, sft_path)
    write_jsonl(dpo_rows, dpo_path)

    print(f"Wrote {len(sft_rows)} SFT rows -> {sft_path}")
    print(f"Wrote {len(dpo_rows)} DPO rows -> {dpo_path}")
    return {"sft_count": len(sft_rows), "dpo_count": len(dpo_rows), "sft_path": str(sft_path), "dpo_path": str(dpo_path)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export synthetic_pairs to SFT/DPO JSONL.")
    parser.add_argument("--threshold", type=float, default=0.5, help="min judge_score to include (flagged rows excluded)")
    parser.add_argument("--require-audit", action="store_true", help="also exclude never-audited rows (judge_score IS NULL)")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    run(threshold=args.threshold, require_audit=args.require_audit, output_dir=out_dir)
