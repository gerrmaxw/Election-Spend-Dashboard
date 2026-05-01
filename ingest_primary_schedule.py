"""Load the 2026 state primary / runoff calendar from the FVAP PDF.

Creates / upserts rows in `state_primary_schedule`.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH

GENERAL_DATE = "2026-11-03"

# (state_code, primary_date, runoff_date, has_us_senate, us_house_seats_or_note)
SCHEDULE = [
    ("AL", "2026-05-19", "2026-06-16", True, 7),
    ("AK", "2026-08-18", None, True, 1),
    ("AS", None, None, None, "1 Delegate"),
    ("AZ", "2026-07-21", None, False, 9),
    ("AR", "2026-03-03", "2026-03-31", True, 4),
    ("CA", "2026-06-02", None, False, 52),
    ("CO", "2026-06-30", None, True, 8),
    ("CT", "2026-08-11", None, False, 5),
    ("DE", "2026-09-15", None, True, 1),
    ("DC", "2026-06-16", None, None, "1 Delegate"),
    ("FL", "2026-08-18", None, True, 28),
    ("GA", "2026-05-19", "2026-06-16", True, 14),
    ("GU", "2026-08-01", None, None, "1 Delegate"),
    ("HI", "2026-08-08", None, False, 2),
    ("ID", "2026-05-19", None, True, 2),
    ("IL", "2026-03-17", None, True, 17),
    ("IN", "2026-05-05", None, False, 9),
    ("IA", "2026-06-02", None, True, 4),
    ("KS", "2026-08-04", None, True, 4),
    ("KY", "2026-05-19", None, True, 6),
    ("LA", "2026-05-16", "2026-06-27", True, 6),
    ("ME", "2026-06-09", None, True, 2),
    ("MD", "2026-06-23", None, False, 8),
    ("MA", "2026-09-01", None, True, 9),
    ("MI", "2026-08-04", None, True, 13),
    ("MN", "2026-08-11", None, True, 8),
    ("MS", "2026-03-10", "2026-04-07", True, 4),
    ("MO", "2026-08-04", None, False, 8),
    ("MT", "2026-06-02", None, True, 2),
    ("NE", "2026-05-12", None, True, 3),
    ("NV", "2026-06-09", None, False, 4),
    ("NH", "2026-09-08", None, True, 2),
    ("NJ", "2026-06-02", None, True, 12),
    ("NM", "2026-06-02", None, True, 3),
    ("NY", "2026-06-23", None, False, 26),
    ("NC", "2026-03-03", "2026-05-12", True, 14),
    ("ND", "2026-06-09", None, False, 1),
    ("OH", "2026-05-05", None, True, 15),
    ("OK", "2026-06-16", "2026-08-25", True, 5),
    ("OR", "2026-05-19", None, True, 6),
    ("PA", "2026-05-19", None, True, 17),
    ("PR", None, None, None, "1 Resident"),
    ("RI", "2026-09-08", None, True, 2),
    ("SC", "2026-06-09", "2026-06-23", True, 7),
    ("SD", "2026-06-02", None, True, 1),
    ("TN", "2026-08-06", None, True, 9),
    ("TX", "2026-03-03", "2026-05-26", True, 38),
    ("UT", "2026-06-23", None, False, 4),
    ("VT", "2026-08-11", None, False, 1),
    ("VI", "2026-08-01", None, None, "1 Delegate"),
    ("VA", "2026-08-04", None, True, 11),
    ("WA", "2026-08-04", None, False, 10),
    ("WV", "2026-05-12", None, True, 2),
    ("WI", "2026-08-11", None, False, 8),
    ("WY", "2026-08-18", None, True, 1),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS state_primary_schedule (
    state             TEXT PRIMARY KEY,
    primary_date      TEXT,
    runoff_date       TEXT,
    has_us_senate     INTEGER,
    us_house_seats    INTEGER,
    us_house_note     TEXT,
    general_date      TEXT,
    source            TEXT,
    updated_at        TEXT NOT NULL
);
"""


def load() -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    inserted = updated = 0
    for state, primary, runoff, has_sen, seats in SCHEDULE:
        exists = conn.execute("SELECT 1 FROM state_primary_schedule WHERE state=?", (state,)).fetchone()
        seat_int = seats if isinstance(seats, int) else None
        seat_note = seats if isinstance(seats, str) else None
        has_sen_int = None if has_sen is None else int(has_sen)
        conn.execute(
            """
            INSERT INTO state_primary_schedule
                (state, primary_date, runoff_date, has_us_senate,
                 us_house_seats, us_house_note, general_date, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state) DO UPDATE SET
                primary_date=excluded.primary_date,
                runoff_date=excluded.runoff_date,
                has_us_senate=excluded.has_us_senate,
                us_house_seats=excluded.us_house_seats,
                us_house_note=excluded.us_house_note,
                general_date=excluded.general_date,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (state, primary, runoff, has_sen_int, seat_int, seat_note,
             GENERAL_DATE, "FVAP 2026 Primary Elections (Oct 2025)", now),
        )
        if exists:
            updated += 1
        else:
            inserted += 1
    conn.commit()
    conn.close()
    summary = {"rows": len(SCHEDULE), "inserted": inserted, "updated": updated}
    print(summary)
    return summary


if __name__ == "__main__":
    load()
