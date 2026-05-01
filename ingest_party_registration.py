"""Load party-registration or partisan-lean data per geography.

State SOS voter files or L2/Catalist data aren't uniformly available via API, so
this loader accepts a CSV the user drops in with columns:

    state,district,registered_dem,registered_rep,registered_npa,total_registered,partisan_lean_d_minus_r,source,as_of

- `district` blank = statewide row.
- Any of the numeric columns can be blank; partisan_lean_d_minus_r is optional
  (e.g. Cook PVI or 2024 presidential D-R margin in percentage points, positive = D-lean).
- `as_of` is an ISO date for when the snapshot was taken.

Usage:
    python ingest_party_registration.py path/to/file.csv
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS party_registration (
    geo_id                  TEXT PRIMARY KEY,
    state                   TEXT NOT NULL,
    district                TEXT,
    registered_dem          INTEGER,
    registered_rep          INTEGER,
    registered_npa          INTEGER,
    total_registered        INTEGER,
    partisan_lean_d_minus_r REAL,
    source                  TEXT,
    as_of                   TEXT,
    loaded_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_party_state ON party_registration(state);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _int(val: str) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(float(val.replace(",", "")))
    except (ValueError, AttributeError):
        return None


def _float(val: str) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def ingest(path: Path) -> dict:
    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    inserted = updated = skipped = 0

    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                state = (row.get("state") or "").strip().upper()
                district = (row.get("district") or "").strip() or None
                if not state:
                    skipped += 1
                    continue
                geo_id = f"state-{state}" if not district else f"cd-{state}-{district.zfill(2)}"
                exists = conn.execute("SELECT 1 FROM party_registration WHERE geo_id = ?", (geo_id,)).fetchone()
                conn.execute(
                    """
                    INSERT INTO party_registration
                        (geo_id, state, district, registered_dem, registered_rep, registered_npa,
                         total_registered, partisan_lean_d_minus_r, source, as_of, loaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(geo_id) DO UPDATE SET
                        registered_dem=excluded.registered_dem,
                        registered_rep=excluded.registered_rep,
                        registered_npa=excluded.registered_npa,
                        total_registered=excluded.total_registered,
                        partisan_lean_d_minus_r=excluded.partisan_lean_d_minus_r,
                        source=excluded.source,
                        as_of=excluded.as_of,
                        loaded_at=excluded.loaded_at
                    """,
                    (
                        geo_id, state, district,
                        _int(row.get("registered_dem", "")),
                        _int(row.get("registered_rep", "")),
                        _int(row.get("registered_npa", "")),
                        _int(row.get("total_registered", "")),
                        _float(row.get("partisan_lean_d_minus_r", "")),
                        row.get("source") or None,
                        row.get("as_of") or None,
                        now,
                    ),
                )
                if exists:
                    updated += 1
                else:
                    inserted += 1
            except Exception as exc:
                skipped += 1
                print(f"skip: {exc}", file=sys.stderr)

    conn.commit()
    conn.close()
    summary = {"inserted": inserted, "updated": updated, "skipped": skipped, "file": str(path)}
    print(summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    ingest(args.path)


if __name__ == "__main__":
    main()
