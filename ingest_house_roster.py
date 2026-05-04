"""Load House incumbent/party roster workbooks into SQLite.

Supported layouts:

* CA House Races.xlsx / CA House Districts.xlsx with STATE_ABBR, CDFIPS,
  NAME, LAST_NAME, PARTY, and optional DMA/MARKET columns.
* House_2026_Party_District_Lookup.xlsx with Candidates, Incumbents, and
  District_Summary sheets.
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

CREATE TABLE IF NOT EXISTS house_district_markets (
    state TEXT NOT NULL,
    district TEXT NOT NULL,
    dma TEXT NOT NULL,
    market TEXT,
    cycle INTEGER NOT NULL,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (state, district, dma, cycle)
);
CREATE INDEX IF NOT EXISTS idx_house_district_markets_state_district ON house_district_markets(state, district);
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
    try:
        numeric = float(text)
        if numeric.is_integer():
            return str(int(numeric))
    except ValueError:
        pass
    return text.upper()


def _party(value: object) -> str:
    text = _s(value).lower()
    return PARTY_TO_DB.get(text, text[:1].upper() if text else "U")


def _party_from_lookup(row: pd.Series) -> str:
    short = _s(row.get("PARTY_SHORT"))
    if short:
        return short[:1].upper()
    return _party(row.get("PARTY"))


def _race_id(state: str, district: str) -> str:
    return f"us_house|{CYCLE}|{state}-{district}|general"


def _candidate_id(full_name: str, race_id: str) -> str:
    digest = hashlib.md5(f"{race_id}|{full_name.lower().strip()}".encode()).hexdigest()[:12]
    return f"cand_{digest}"


def _upsert_house_race(conn: sqlite3.Connection, state: str, district: str, now: str, source: str) -> tuple[int, int]:
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
        (race_id, CYCLE, state, district, GENERAL_DATE, f"{state}-{district} U.S. House General", source, now),
    )
    return (0, 1) if existed_race else (1, 0)


def _upsert_candidate(
    conn: sqlite3.Connection,
    state: str,
    district: str,
    name: str,
    party: str,
    incumbent: bool,
    now: str,
) -> tuple[int, int]:
    race_id = _race_id(state, district)
    cand_id = _candidate_id(name, race_id)
    existed_cand = conn.execute("SELECT 1 FROM candidates WHERE candidate_id=?", (cand_id,)).fetchone()
    conn.execute(
        """
        INSERT INTO candidates (
            candidate_id, full_name, party, race_id, incumbent,
            result, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            full_name=excluded.full_name,
            party=excluded.party,
            incumbent=MAX(candidates.incumbent, excluded.incumbent),
            updated_at=excluded.updated_at
        """,
        (cand_id, name, party, race_id, 1 if incumbent else 0, now),
    )
    return (0, 1) if existed_cand else (1, 0)


def _load_overlay_roster(data_file: Path, conn: sqlite3.Connection, now: str) -> dict:
    df = pd.read_excel(data_file)
    df.columns = [str(column).strip().upper() for column in df.columns]
    expected = {"STATE_ABBR", "CDFIPS", "NAME", "LAST_NAME", "PARTY"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in house roster xlsx: {missing}")

    roster_rows = market_rows = races_added = races_updated = cands_added = cands_updated = skipped = 0
    seen_roster: set[tuple[str, str]] = set()
    for _, row in df.iterrows():
        state = _s(row.get("STATE_ABBR")).upper()
        district = _district(row.get("CDFIPS"))
        name = _s(row.get("NAME"))
        last_name = _s(row.get("LAST_NAME"))
        party = _party(row.get("PARTY"))
        if not state or not district:
            skipped += 1
            continue

        if (state, district) not in seen_roster:
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
            added, updated = _upsert_house_race(conn, state, district, now, SOURCE)
            races_added += added
            races_updated += updated
            if name and party != "U":
                added, updated = _upsert_candidate(conn, state, district, name, party, True, now)
                cands_added += added
                cands_updated += updated
            seen_roster.add((state, district))

        dma = _s(row.get("DMA"))
        market = _s(row.get("MARKET"))
        if dma:
            conn.execute(
                """
                INSERT INTO house_district_markets (
                    state, district, dma, market, cycle, source_file, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(state, district, dma, cycle) DO UPDATE SET
                    market=excluded.market,
                    source_file=excluded.source_file,
                    updated_at=excluded.updated_at
                """,
                (state, district, dma, market, CYCLE, data_file.name, now),
            )
            market_rows += 1

    return {
        "rows": len(df),
        "layout": "overlay_roster",
        "roster_rows": roster_rows,
        "market_rows": market_rows,
        "races_added": races_added,
        "races_updated": races_updated,
        "candidates_added": cands_added,
        "candidates_updated": cands_updated,
        "skipped": skipped,
    }


def _load_party_district_lookup(data_file: Path, conn: sqlite3.Connection, now: str) -> dict:
    xl = pd.ExcelFile(data_file)
    sheet_names = {name.lower(): name for name in xl.sheet_names}
    if "candidates" not in sheet_names:
        raise ValueError("House party lookup workbook is missing a Candidates sheet")

    frames = []
    for sheet, incumbent_default in [("Candidates", False), ("Incumbents", True)]:
        actual = sheet_names.get(sheet.lower())
        if not actual:
            continue
        df = pd.read_excel(data_file, sheet_name=actual)
        df.columns = [str(column).strip().upper() for column in df.columns]
        required = {"STATE", "DISTRICT", "CANDIDATE_NAME", "PARTY"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns in {actual}: {missing}")
        df["__incumbent_default"] = incumbent_default
        frames.append(df)

    df_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    roster_rows = races_added = races_updated = cands_added = cands_updated = skipped = 0
    for (state, district), group in df_all.groupby(["STATE", "DISTRICT"], dropna=False):
        state = _s(state).upper()
        district = _district(district)
        if not state or not district:
            skipped += len(group)
            continue

        incumbents = group[group.get("INCUMB", "").fillna("").astype(str).str.upper().eq("I")].copy()
        if incumbents.empty:
            incumbents = group[group["__incumbent_default"] == True].copy()  # noqa: E712
        incumbent = incumbents.iloc[0] if not incumbents.empty else group.iloc[0]
        inc_name = _s(incumbent.get("CANDIDATE_NAME"))
        inc_party = _party_from_lookup(incumbent)
        inc_last = inc_name.split()[-1] if inc_name else None

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
            (state, district, inc_name, inc_last, inc_party, CYCLE, data_file.name, now),
        )
        roster_rows += 1
        added, updated = _upsert_house_race(conn, state, district, now, "House party district lookup")
        races_added += added
        races_updated += updated

        for _, row in group.iterrows():
            name = _s(row.get("CANDIDATE_NAME"))
            if not name:
                skipped += 1
                continue
            party = _party_from_lookup(row)
            incumbent_flag = _s(row.get("INCUMB")).upper() == "I" or bool(row.get("__incumbent_default"))
            added, updated = _upsert_candidate(conn, state, district, name, party, incumbent_flag, now)
            cands_added += added
            cands_updated += updated

    return {
        "rows": len(df_all),
        "layout": "party_district_lookup",
        "roster_rows": roster_rows,
        "market_rows": 0,
        "races_added": races_added,
        "races_updated": races_updated,
        "candidates_added": cands_added,
        "candidates_updated": cands_updated,
        "skipped": skipped,
    }


def load(path: Path | str = DATA_FILE) -> dict:
    data_file = Path(path)
    if not data_file.exists():
        raise FileNotFoundError(f"Missing {data_file}")

    Store(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    first_sheet = pd.read_excel(data_file, nrows=0)
    first_cols = {str(column).strip().upper() for column in first_sheet.columns}
    if {"STATE_ABBR", "CDFIPS", "NAME", "LAST_NAME", "PARTY"}.issubset(first_cols):
        summary = _load_overlay_roster(data_file, conn, now)
    else:
        summary = _load_party_district_lookup(data_file, conn, now)

    conn.commit()
    conn.close()
    Store(DB_PATH).set_metadata("last_house_roster_ingest", now)
    summary["file"] = data_file.name
    print(summary)
    return summary


if __name__ == "__main__":
    load(Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_FILE)
