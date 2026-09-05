"""Curated regulatory/ISO document corpus — downloads PDFs and registers them
in the `documents` table (chunking happens separately, see rag/chunking.py).

Small hand-picked corpus (side-project scope), weighted toward interconnection
queue rules (PJM vs CAISO) + CAISO curtailment reports + FERC Order 2023,
matching the kind of hybrid policy/grid queries this project targets.

Usage:
    python -m ingestion.doc_corpus
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
REQUEST_TIMEOUT = 60
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SEC = 3
USER_AGENT = "gridpulse-multiagent-rag (side project; contact via github)"


@dataclass
class DocSpec:
    source_type: str  # ferc | iso_manual | decarb_report
    iso: str | None
    title: str
    url: str
    filename: str


DOC_MANIFEST: list[DocSpec] = [
    # --- FERC orders ---
    DocSpec(
        "ferc", None,
        "FERC Order No. 2023 — Improvements to Generator Interconnection Procedures and Agreements",
        "https://www.wrightlaw.com/wp-content/uploads/2024/01/Order-No-2023-Improvements-to-Generator-Interconnection-Procedures-and-Agreements.pdf",
        "ferc_order_2023.pdf",
    ),
    DocSpec(
        "ferc", None,
        "Summary of FERC Order No. 2023-A on Generator Interconnections (Troutman Pepper)",
        "https://www.troutman.com/wp-content/uploads/2024/03/Troutman_Pepper_Summary_of_FERC_Order_No._2023-A_on_Generator_Interconnections_-_March_2024.pdf",
        "ferc_order_2023a_summary.pdf",
    ),
    # --- PJM ---
    DocSpec(
        "iso_manual", "PJM",
        "PJM Manual 14G: Generation Interconnection Requests",
        "https://www.pjm.com/-/media/DotCom/documents/manuals/m14g.pdf",
        "pjm_manual_14g.pdf",
    ),
    DocSpec(
        "iso_manual", "PJM",
        "PJM Manual 14C: Interconnection Facilities and Network Upgrade Construction",
        "https://www.pjm.com/-/media/DotCom/documents/manuals/m14c.pdf",
        "pjm_manual_14c.pdf",
    ),
    # --- CAISO ---
    DocSpec(
        "iso_manual", "CAISO",
        "CAISO BPM for Generator Interconnection and Deliverability Allocation Procedures (GIDAP) v33",
        "https://bpmcm.caiso.com/BPM%20Document%20Library/Generator%20Interconnection%20and%20Deliverability%20Allocation%20Procedures/BPM_for_GIDAP_V33_redline.pdf",
        "caiso_bpm_gidap_v33.pdf",
    ),
    DocSpec(
        "iso_manual", "CAISO",
        "CAISO BPM for Generator Management v35",
        "https://bpmcm.caiso.com/BPM%20Document%20Library/Generator%20Management/BPM_for_GeneratorManagement_V35_redline.pdf",
        "caiso_bpm_generator_management_v35.pdf",
    ),
    DocSpec(
        "decarb_report", "CAISO",
        "CAISO Wind and Solar Curtailment Report — July 15, 2024",
        "https://www.caiso.com/documents/wind-solar-real-time-dispatch-curtailment-report-jul-15-2024.pdf",
        "caiso_curtailment_2024-07-15.pdf",
    ),
    DocSpec(
        "decarb_report", "CAISO",
        "CAISO Wind and Solar Curtailment Report — July 16, 2024",
        "https://www.caiso.com/documents/wind-solar-real-time-dispatch-curtailment-report-jul-16-2024.pdf",
        "caiso_curtailment_2024-07-16.pdf",
    ),
    DocSpec(
        "decarb_report", "CAISO",
        "CAISO Wind and Solar Curtailment Report — July 30, 2024",
        "https://www.caiso.com/documents/wind-solar-real-time-dispatch-curtailment-report-jul-30-2024.pdf",
        "caiso_curtailment_2024-07-30.pdf",
    ),
    # --- MISO ---
    DocSpec(
        "iso_manual", "MISO",
        "MISO BPM-015: Generator Interconnection Queue Reform Redlines",
        "https://cdn.misoenergy.org/20230918%20PAC%20Item%2002c%20BPM-015%20Generator%20Interconnection%20Queue%20Reform%20Redlines630228.pdf",
        "miso_bpm_015_queue_reform.pdf",
    ),
    # --- ERCOT ---
    DocSpec(
        "iso_manual", "ERCOT",
        "ERCOT Planning Guide — February 1, 2025 (incl. Section 3, Generation Interconnection)",
        "https://www.ercot.com/files/docs/2025/01/31/February%201,%202025%20Planning%20Guide.pdf",
        "ercot_planning_guide_2025-02-01.pdf",
    ),
    # --- Decarbonization / queue trend report ---
    DocSpec(
        "decarb_report", None,
        "LBNL Queued Up: 2024 Edition — Characteristics of Power Plants Seeking Transmission Interconnection",
        "https://emp.lbl.gov/sites/default/files/2024-04/Queued%20Up%202024%20Edition_1.pdf",
        "lbnl_queued_up_2024.pdf",
    ),
]


def _download(spec: DocSpec) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / spec.filename
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[doc_corpus] already have {spec.filename}, skipping download")
        return dest

    last_err: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(
                spec.url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            print(f"[doc_corpus] downloaded {spec.filename} ({len(resp.content)} bytes)")
            return dest
        except requests.RequestException as e:
            last_err = e
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"failed to download {spec.url}: {last_err}")


def _upsert_document(conn, spec: DocSpec) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (source_type, iso, title, url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE
                SET source_type = EXCLUDED.source_type,
                    iso = EXCLUDED.iso,
                    title = EXCLUDED.title
            RETURNING id
            """,
            (spec.source_type, spec.iso, spec.title, spec.url),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]


def fetch_all() -> list[tuple[int, Path]]:
    conn = psycopg2.connect(DATABASE_URL)
    results = []
    try:
        for spec in DOC_MANIFEST:
            try:
                path = _download(spec)
            except RuntimeError as e:
                print(f"[doc_corpus] SKIP {spec.title}: {e}")
                continue
            doc_id = _upsert_document(conn, spec)
            results.append((doc_id, path))
            print(f"[doc_corpus] registered doc_id={doc_id} ({spec.title})")
    finally:
        conn.close()
    return results


if __name__ == "__main__":
    fetched = fetch_all()
    print(f"[doc_corpus] done: {len(fetched)}/{len(DOC_MANIFEST)} docs registered")
