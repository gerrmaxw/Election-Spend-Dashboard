"""Fetch The Downballot's 2024 presidential-by-CD calculations and load as partisan lean.

Source: https://docs.google.com/spreadsheets/d/1ng1i_Dm_RMDnEvauH44pgE6JCUsapcuu8F2pCfeLWFo

Populates party_registration with:
    partisan_lean_d_minus_r = 2024 Harris-Trump margin (pp)
    source = 'The Downballot 2024 Pres-by-CD'
    as_of = '2024-11-05'
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import TARGET_STATES
from ingest_party_registration import ensure_schema
import sqlite3
from config.settings import DB_PATH

SHEET_ID = "1ng1i_Dm_RMDnEvauH44pgE6JCUsapcuu8F2pCfeLWFo"
GID = "620838163"
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

AS_OF = "2024-11-05"
SOURCE_LABEL = "The Downballot 2024 Pres-by-CD"


def parse_rows(raw: str) -> list[dict]:
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    out = []
    for row in rows:
        if not row or not row[0] or "-" not in row[0]:
            continue
        district = row[0].strip()
        if len(district) < 4 or district[2] != "-":
            continue
        state = district[:2]
        dist = district[3:]
        # Margin is column index 5 (0-based)
        try:
            harris = float(row[3])
            trump = float(row[4])
            margin_2024 = float(row[5])
        except (IndexError, ValueError):
            continue
        out.append(
            {
                "state": state,
                "district": "00" if dist in ("AL", "0") else dist.zfill(2),
                "harris_pct": harris,
                "trump_pct": trump,
                "margin_2024": margin_2024,
            }
        )
    return out


def load(only_targets: bool = False) -> dict:
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()
    parsed = parse_rows(resp.text)
    if only_targets:
        parsed = [r for r in parsed if r["state"] in TARGET_STATES]

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    inserted = updated = 0
    for r in parsed:
        geo_id = f"cd-{r['state']}-{r['district']}"
        exists = conn.execute("SELECT 1 FROM party_registration WHERE geo_id = ?", (geo_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO party_registration
                (geo_id, state, district, registered_dem, registered_rep, registered_npa,
                 total_registered, partisan_lean_d_minus_r, source, as_of, loaded_at)
            VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT(geo_id) DO UPDATE SET
                partisan_lean_d_minus_r=excluded.partisan_lean_d_minus_r,
                source=excluded.source,
                as_of=excluded.as_of,
                loaded_at=excluded.loaded_at
            """,
            (geo_id, r["state"], r["district"], r["margin_2024"], SOURCE_LABEL, AS_OF, now),
        )
        if exists:
            updated += 1
        else:
            inserted += 1
    conn.commit()
    conn.close()
    return {"rows": len(parsed), "inserted": inserted, "updated": updated}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-states", action="store_true", help="Load all 435 CDs (default: only target states)")
    args = parser.parse_args()
    summary = load(only_targets=not args.all_states)
    print(summary)


if __name__ == "__main__":
    main()
