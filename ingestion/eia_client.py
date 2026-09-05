"""EIA API v2 client — pulls hourly generation mix by balancing authority (BA)
and upserts into the `eia_series` table.

Endpoint: /v2/electricity/rto/fuel-type-data/data
Docs: https://www.eia.gov/opendata/documentation.php

Usage:
    python -m ingestion.eia_client --ba PJM CISO ERCO MISO --start 2024-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime
from typing import Iterator

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

EIA_BASE_URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
PAGE_LENGTH = 5000  # EIA API max rows per request
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SEC = 2

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)


def _fetch_page(
    api_key: str, ba_code: str, start: str, end: str, offset: int
) -> dict:
    params = {
        "api_key": api_key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": ba_code,
        "start": start,
        "end": end,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "offset": offset,
        "length": PAGE_LENGTH,
    }
    last_err: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(EIA_BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"EIA API request failed after {RETRY_ATTEMPTS} attempts: {last_err}")


def fetch_fuel_type_data(
    api_key: str, ba_code: str, start: str, end: str
) -> Iterator[dict]:
    """Yields raw EIA row dicts for one BA across the full date range, paginating."""
    offset = 0
    while True:
        payload = _fetch_page(api_key, ba_code, start, end, offset)
        rows = payload.get("response", {}).get("data", [])
        if not rows:
            break
        yield from rows
        if len(rows) < PAGE_LENGTH:
            break
        offset += PAGE_LENGTH


def _parse_period_to_ts(period: str) -> datetime:
    # EIA hourly period format: "2024-01-01T05" (UTC hour, no minutes)
    return datetime.strptime(period, "%Y-%m-%dT%H")


def upsert_rows(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    records = []
    for r in rows:
        try:
            ts = _parse_period_to_ts(r["period"])
        except (KeyError, ValueError):
            continue
        value = r.get("value")
        if value is None:
            continue
        records.append(
            (
                r.get("respondent"),
                r.get("type-name") or r.get("fueltype"),
                r.get("respondent-name"),
                ts,
                float(value),
            )
        )
    if not records:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO eia_series (ba_code, fuel_type, region, ts, mwh)
            VALUES %s
            ON CONFLICT (ba_code, fuel_type, ts)
            DO UPDATE SET mwh = EXCLUDED.mwh, region = EXCLUDED.region
            """,
            records,
        )
    conn.commit()
    return len(records)


def backfill(api_key: str, ba_codes: list[str], start: str, end: str) -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        for ba in ba_codes:
            print(f"[eia_client] fetching {ba} {start}..{end}")
            total = 0
            batch: list[dict] = []
            for row in fetch_fuel_type_data(api_key, ba, start, end):
                batch.append(row)
                if len(batch) >= PAGE_LENGTH:
                    total += upsert_rows(conn, batch)
                    batch = []
            total += upsert_rows(conn, batch)
            print(f"[eia_client] {ba}: upserted {total} rows")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill EIA hourly fuel-type data.")
    parser.add_argument("--ba", nargs="+", required=True, help="BA codes, e.g. PJM CISO ERCO MISO")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        raise SystemExit("EIA_API_KEY not set (see .env.example)")

    # sanity-check date range doesn't extend past today
    if datetime.strptime(args.end, "%Y-%m-%d").date() > date.today():
        raise SystemExit("--end cannot be in the future")

    backfill(api_key, args.ba, args.start, args.end)


if __name__ == "__main__":
    main()
