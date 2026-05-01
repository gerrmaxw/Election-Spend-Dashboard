"""Load polling data from RealClearPolling Excel/CSV exports or JSON into SQLite.

Input JSON format (array of objects):
[
  {
    "race_id": "...",                 optional — matched by state/office/district if absent
    "state": "VA",
    "office": "governor",             one of the OfficeType values
    "district": null,
    "pollster": "Roanoke College",
    "start_date": "2026-03-15",
    "end_date":   "2026-03-20",
    "sample_size": 812,
    "population": "LV",               optional: LV/RV/A
    "candidates": [
      {"name": "Jane Doe", "party": "Democratic", "pct": 48.2},
      {"name": "John Roe", "party": "Republican", "pct": 44.1}
    ],
    "sponsors": "",
    "url": "https://...",
    "notes": ""
  }
]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    poll_id       TEXT PRIMARY KEY,
    race_id       TEXT,
    state         TEXT,
    office        TEXT,
    district      TEXT,
    pollster      TEXT,
    race_name     TEXT,
    race_type     TEXT,
    start_date    TEXT,
    end_date      TEXT,
    sample_size   INTEGER,
    population    TEXT,
    sponsors      TEXT,
    source_url    TEXT,
    notes         TEXT,
    winner        TEXT,
    spread        REAL,
    source_file   TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_answers (
    poll_id        TEXT NOT NULL,
    candidate_name TEXT NOT NULL,
    party          TEXT,
    pct            REAL NOT NULL,
    PRIMARY KEY (poll_id, candidate_name),
    FOREIGN KEY (poll_id) REFERENCES polls(poll_id)
);

CREATE INDEX IF NOT EXISTS idx_polls_race ON polls(race_id);
CREATE INDEX IF NOT EXISTS idx_polls_state ON polls(state);
CREATE INDEX IF NOT EXISTS idx_polls_end_date ON polls(end_date);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(polls)").fetchall()}
    optional_columns = {
        "race_name": "TEXT",
        "race_type": "TEXT",
        "winner": "TEXT",
        "spread": "REAL",
        "source_file": "TEXT",
    }
    for column, definition in optional_columns.items():
        if column not in existing_cols:
            conn.execute(f"ALTER TABLE polls ADD COLUMN {column} {definition}")
    conn.commit()


STATE_CODES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


def _s(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).replace("\u200b", "").strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _pct(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if not text or text in {"-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_int(value: object) -> int | None:
    numeric = _pct(value)
    return int(numeric) if numeric is not None else None


def _optional_float(value: object) -> float | None:
    return _pct(value)


def _date(value: object) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _state(value: object) -> str | None:
    text = _s(value)
    if not text:
        return None
    if text.lower() == "national":
        return "National"
    upper = text.upper()
    if len(upper) == 2:
        return upper
    return STATE_CODES.get(text.lower(), text)


def _office(value: object, race_name: str = "") -> str | None:
    text = f"{_s(value)} {race_name}".lower()
    if "generic congressional" in text:
        return "generic_ballot"
    if "job approval" in text or "direction of country" in text or "military action" in text:
        return "national_issue"
    if "presidential" in text or "president" in text:
        return "president"
    if "house" in text or "district" in text:
        return "us_house"
    if "senate" in text:
        return "us_senate"
    if "governor" in text:
        return "governor"
    if "sotu" in text:
        return "national_issue"
    return None


def _district(race_name: str) -> str | None:
    match = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+District\b", race_name, flags=re.I)
    if match:
        return str(int(match.group(1)))
    match = re.search(r"\b[A-Z]{2}[-\s]*(?:CD)?[-\s]*(\d{1,3})\b", race_name, flags=re.I)
    if match:
        return str(int(match.group(1)))
    return None


def _party_for_answer(answer_name: str, race_name: str) -> str | None:
    lower = f"{answer_name} {race_name}".lower()
    if answer_name.lower() in {"democrat", "democrats"} or "democratic primary" in lower:
        return "Democratic"
    if answer_name.lower() in {"republican", "republicans"} or "republican primary" in lower:
        return "Republican"
    return None


def _winner_and_spread(row: pd.Series, answers: list[dict]) -> tuple[str | None, float | None]:
    winner = _s(row.get("Winner"))
    spread = _pct(row.get("Spread"))
    result5 = _s(row.get("Result 5"))
    candidate5 = _s(row.get("Candidate/Option 5"))
    winner_value = _pct(row.get("Winner"))
    if result5 and not candidate5 and winner_value is not None and spread is None:
        winner = result5
        spread = winner_value
    elif spread is None and winner_value is not None and not winner:
        spread = winner_value
    if not winner and answers:
        ordered = sorted(answers, key=lambda item: item["pct"], reverse=True)
        winner = ordered[0]["name"]
        if len(ordered) > 1:
            spread = ordered[0]["pct"] - ordered[1]["pct"]
    return winner or None, spread


def parse_realclearpolling_frame(frame: pd.DataFrame, source_file: str = "") -> list[dict]:
    required = {
        "Date",
        "Race",
        "State",
        "Race Type",
        "Pollster",
        "Candidate/Option 1",
        "Result 1",
        "Candidate/Option 2",
        "Result 2",
    }
    if not required.issubset({str(column).strip() for column in frame.columns}):
        raise ValueError("Not a supported RealClearPolling wide workbook")
    frame = frame.rename(columns={column: str(column).strip() for column in frame.columns})
    polls: list[dict] = []
    for _, row in frame.iterrows():
        race_name = _s(row.get("Race"))
        pollster = _s(row.get("Pollster"))
        end_date = _date(row.get("Date"))
        if not race_name or not pollster or not end_date:
            continue
        state = _state(row.get("State"))
        race_type = _s(row.get("Race Type"))
        answers: list[dict] = []
        for idx in range(1, 6):
            name = _s(row.get(f"Candidate/Option {idx}"))
            pct = _pct(row.get(f"Result {idx}"))
            if not name or pct is None:
                continue
            answers.append(
                {
                    "name": name,
                    "party": _party_for_answer(name, race_name),
                    "pct": pct,
                }
            )
        if len(answers) < 2:
            continue
        winner, spread = _winner_and_spread(row, answers)
        polls.append(
            {
                "state": state,
                "office": _office(race_type, race_name),
                "district": _district(race_name),
                "race_name": race_name,
                "race_type": race_type,
                "pollster": pollster,
                "start_date": end_date,
                "end_date": end_date,
                "population": "",
                "candidates": answers,
                "winner": winner,
                "spread": spread,
                "sponsors": "",
                "url": "",
                "source_file": source_file,
                "notes": "RealClearPolling workbook import",
            }
        )
    return polls


def load_payload(path: Path) -> list[dict]:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        frame = pd.read_excel(path)
        return parse_realclearpolling_frame(frame, path.name)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
        columns = {str(column).strip() for column in frame.columns}
        if {"Race", "Race Type", "Candidate/Option 1", "Result 1"}.issubset(columns):
            return parse_realclearpolling_frame(frame, path.name)
        return json.loads(frame.to_json(orient="records"))
    raw = path.read_text()
    payload = json.loads(raw)
    if isinstance(payload, dict) and "polls" in payload:
        payload = payload["polls"]
    if not isinstance(payload, list):
        raise ValueError("Expected a JSON array of poll objects")
    return payload


def poll_fingerprint(row: dict) -> str:
    key = "|".join(
        [
            str(row.get("state", "")).upper(),
            str(row.get("office", "")).lower(),
            str(row.get("district", "") or ""),
            str(row.get("race_name") or row.get("race") or "").strip().lower(),
            str(row.get("pollster", "")).strip().lower(),
            str(row.get("start_date", "")),
            str(row.get("end_date", "")),
        ]
    )
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def resolve_race_id(conn: sqlite3.Connection, row: dict) -> str | None:
    if row.get("race_id"):
        return str(row["race_id"])
    state = (row.get("state") or "").upper()
    office = (row.get("office") or "").lower()
    district = row.get("district")
    district = str(district) if district not in (None, "") else None
    cur = conn.execute(
        "SELECT race_id FROM races WHERE state = ? AND office = ? AND (district IS ? OR district = ?) LIMIT 1",
        (state, office, district, district),
    )
    result = cur.fetchone()
    return result[0] if result else None


def ingest(path: Path, verbose: bool = True) -> dict:
    payload = load_payload(path)

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    updated = 0
    skipped = 0

    for row in payload:
        try:
            poll_id = row.get("poll_id") or poll_fingerprint(row)
            race_id = resolve_race_id(conn, row)
            exists = conn.execute("SELECT 1 FROM polls WHERE poll_id = ?", (poll_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO polls (poll_id, race_id, state, office, district, pollster,
                                   race_name, race_type,
                                   start_date, end_date, sample_size, population, sponsors,
                                   source_url, notes, winner, spread, source_file, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(poll_id) DO UPDATE SET
                    race_id=excluded.race_id,
                    state=excluded.state,
                    office=excluded.office,
                    district=excluded.district,
                    pollster=excluded.pollster,
                    race_name=excluded.race_name,
                    race_type=excluded.race_type,
                    start_date=excluded.start_date,
                    end_date=excluded.end_date,
                    sample_size=excluded.sample_size,
                    population=excluded.population,
                    sponsors=excluded.sponsors,
                    source_url=excluded.source_url,
                    notes=excluded.notes,
                    winner=excluded.winner,
                    spread=excluded.spread,
                    source_file=excluded.source_file,
                    fetched_at=excluded.fetched_at
                """,
                (
                    poll_id,
                    race_id,
                    _state(row.get("state")) if row.get("state") else None,
                    (row.get("office") or "").lower() or None,
                    str(row["district"]) if row.get("district") not in (None, "") else None,
                    row.get("pollster"),
                    row.get("race_name") or row.get("race"),
                    row.get("race_type"),
                    row.get("start_date"),
                    row.get("end_date"),
                    _optional_int(row.get("sample_size")),
                    row.get("population"),
                    row.get("sponsors"),
                    row.get("url") or row.get("source_url"),
                    row.get("notes"),
                    row.get("winner"),
                    _optional_float(row.get("spread")),
                    row.get("source_file") or path.name,
                    now,
                ),
            )
            conn.execute("DELETE FROM poll_answers WHERE poll_id = ?", (poll_id,))
            for answer in row.get("candidates", []) or []:
                conn.execute(
                    "INSERT INTO poll_answers (poll_id, candidate_name, party, pct) VALUES (?, ?, ?, ?)",
                    (
                        poll_id,
                        str(answer.get("name", "")).strip(),
                        answer.get("party"),
                        float(answer.get("pct", 0)),
                    ),
                )
            if exists:
                updated += 1
            else:
                inserted += 1
        except Exception as exc:
            skipped += 1
            if verbose:
                print(f"skip poll: {exc}", file=sys.stderr)

    conn.commit()
    conn.execute(
        """
        INSERT INTO metadata (key, value, updated_at)
        VALUES ('last_polling_ingest', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO metadata (key, value, updated_at)
        VALUES ('last_uploaded_file', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (path.name, now),
    )
    conn.commit()
    conn.close()
    summary = {"inserted": inserted, "updated": updated, "skipped": skipped, "rows": len(payload), "file": str(path)}
    if verbose:
        print(json.dumps(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="Path to a polling workbook, CSV, or JSON file")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    ingest(args.path, verbose=not args.quiet)


if __name__ == "__main__":
    main()
