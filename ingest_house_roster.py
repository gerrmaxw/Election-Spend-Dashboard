"""Load a House incumbent/party roster workbook into SQLite.

The user-supplied file is named `CA House Races.xlsx`, but its sheet contains
many states. Expected columns are STATE_ABBR, CDFIPS, NAME, LAST_NAME, PARTY.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH
from data.store import Store


DATA_FILE = ROOT / "local_data" / "CA House Races.xlsx"
CYCLE = 2026
GENERAL_DATE = "2026-11-03"
SOURCE = "House roster workbook"

SCHEMA = """
CREATE TABLE IF NOT EXISTS house_roster (
    state TEXT NOT NULL,
    district TEXT NOT NULL,
    incumbent_name TEXT,
    incumbent_last_name TEXT,
    incumbent_party TEXT,
    cycle INTEGER NOT NULL,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (state, district, cycle)
);
CREATE INDEX IF NOT EXISTS idx_house_roster_state_district ON house_roster(state, district);
"""

PARTY_TO_DB = {
    "democrat": "D",
    "democratic": "D",
    "democratic-farmer-labor": "D",
    "republican": "R",
    "independent": "I",
    "libertarian": "L",
    "green": "G",
    "vacant": "U",
}


def _s(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("\u200b", "").strip()


def _district(value: object) -> str:
    text = _s(value)
    if not text:
        return ""
    if text.isdigit():
        return str(int(text))
    return text.upper()


def _party(value: object) -> str:
    text = _s(value).lower()
    return PARTY_TO_DB.get(text, text[:1].upper() if text else "U")


def _race_id(state: str, district: str) -> str:
    return f"us_house|{CYCLE}|{state}-{district}|general"


def _candidate_id(full_name: str, race_id: str) -> str:
    digest = hashlib.md5(f"{race_id}|{full_name.lower().strip()}".encode()).hexdigest()[:12]
    return f"cand_{digest}"


def load(path: Path | str = DATA_FILE) -> dict:
    data_file = Path(path)
    if not data_file.exists():
        raise FileNotFoundError(f"Missing {data_file}")

    df = pd.read_excel(data_file)
    df.columns = [str(column).strip().upper() for column in df.columns]
    expected = {"STATE_ABBR", "CDFIPS", "NAME", "LAST_NAME", "PARTY"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in house roster xlsx: {missing}")

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat()

    roster_rows = races_added = races_updated = cands_added = cands_updated = skipped = 0
    for _, row in df.iterrows():
        state = _s(row.get("STATE_ABBR")).upper()
        district = _district(row.get("CDFIPS"))
        name = _s(row.get("NAME"))
        last_name = _s(row.get("LAST_NAME"))
        party = _party(row.get("PARTY"))
        if not state or not district:
            skipped += 1
            continue

        conn.execute(
            """
            INSERT INTO house_roster (
                state, district, incumbent_name, incumbent_last_name,
                incumbent_party, cycle, source_file, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state, district, cycle) DO UPDATE SET
                incumbent_name=excluded.incumbent_name,
                incumbent_last_name=excluded.incumbent_last_name,
                incumbent_party=excluded.incumbent_party,
                source_file=excluded.source_file,
                updated_at=excluded.updated_at
            """,
            (state, district, name or None, last_name or None, party, CYCLE, data_file.name, now),
        )
        roster_rows += 1

        race_id = _race_id(state, district)
        existed_race = conn.execute("SELECT 1 FROM races WHERE race_id=?", (race_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO races (
                race_id, cycle, office, state, district, general_date,
                election_name, election_scope, election_type, source, updated_at
            ) VALUES (?, ?, 'us_house', ?, ?, ?, ?, 'district', 'general', ?, ?)
            ON CONFLICT(race_id) DO UPDATE SET
                election_name=COALESCE(races.election_name, excluded.election_name),
                source=COALESCE(races.source, excluded.source),
                updated_at=excluded.updated_at
            """,
            (race_id, CYCLE, state, district, GENERAL_DATE, f"{state}-{district} U.S. House General", SOURCE, now),
        )
        if existed_race:
            races_updated += 1
        else:
            races_added += 1

        if name and party != "U":
            cand_id = _candidate_id(name, race_id)
            existed_cand = conn.execute("SELECT 1 FROM candidates WHERE candidate_id=?", (cand_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO candidates (
                    candidate_id, full_name, party, race_id, incumbent,
                    result, updated_at
                ) VALUES (?, ?, ?, ?, 1, 'pending', ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    party=excluded.party,
                    incumbent=1,
                    updated_at=excluded.updated_at
                """,
                (cand_id, name, party, race_id, now),
            )
            if existed_cand:
                cands_updated += 1
            else:
                cands_added += 1

    conn.commit()
    conn.close()

    Store(DB_PATH).set_metadata("last_house_roster_ingest", now)
    summary = {
        "rows": len(df),
        "roster_rows": roster_rows,
        "races_added": races_added,
        "races_updated": races_updated,
        "candidates_added": cands_added,
        "candidates_updated": cands_updated,
        "skipped": skipped,
    }
    print(summary)
    return summary


if __name__ == "__main__":
    load(Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_FILE)
