"""Load local FEC/PAC reference files into the dashboard SQLite database.

Supported files:

* cn.txt candidate master
* ccl.txt candidate-committee links
* weball26.txt / webl26.txt candidate financial summaries
* webk26.txt committee financial summaries
* committee_summary_2026.csv committee summary export
* leadership2026.csv leadership PAC export
* PAC_to_Candidates_2026*.csv manual PAC support lookup
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH
from data.store import Store


CYCLE = 2026

CN_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "party",
    "election_year",
    "state",
    "office",
    "district",
    "incumbent_challenge_status",
    "candidate_status",
    "principal_committee_id",
    "street_1",
    "street_2",
    "city",
    "mailing_state",
    "zip_code",
]

CCL_COLUMNS = [
    "candidate_id",
    "candidate_election_year",
    "fec_election_year",
    "committee_id",
    "committee_type",
    "committee_designation",
    "linkage_id",
]

CANDIDATE_SUMMARY_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "incumbent_challenge_status",
    "party_code",
    "party",
    "total_receipts",
    "transfers_from_authorized",
    "total_disbursements",
    "transfers_to_authorized",
    "cash_on_hand_beginning",
    "cash_on_hand_close",
    "candidate_contributions",
    "candidate_loans",
    "other_loans",
    "candidate_loan_repayments",
    "other_loan_repayments",
    "debts_owed_by_committee",
    "debts_owed_to_committee",
    "state",
    "district",
    "special_election",
    "primary_election",
    "runoff_election",
    "general_election",
    "general_election_percent",
    "other_committee_contributions",
    "party_committee_contributions",
    "coverage_end_date",
    "individual_refunds",
    "committee_refunds",
]

COMMITTEE_SUMMARY_COLUMNS = [
    "committee_id",
    "committee_name",
    "committee_type",
    "committee_designation",
    "filing_frequency",
    "total_receipts",
    "transfers_from_authorized",
    "total_disbursements",
    "transfers_to_authorized",
    "cash_on_hand_beginning",
    "cash_on_hand_close",
    "candidate_contributions",
    "candidate_loans",
    "total_loans_received",
    "contribution_refunds",
    "candidate_loan_repayments",
    "loan_repayments",
    "debts_owed_by_committee",
    "debts_owed_to_committee",
    "individual_contributions",
    "other_committee_contributions",
    "party_committee_contributions",
    "coverage_end_date",
    "individual_refunds",
    "committee_refunds",
    "other_refunds",
    "file_coverage_end_date",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS fec_candidates (
    candidate_id TEXT PRIMARY KEY,
    candidate_name TEXT,
    party TEXT,
    election_year INTEGER,
    state TEXT,
    office TEXT,
    district TEXT,
    incumbent_challenge_status TEXT,
    candidate_status TEXT,
    principal_committee_id TEXT,
    city TEXT,
    mailing_state TEXT,
    zip_code TEXT,
    source_file TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fec_candidates_state_office ON fec_candidates(state, office);
CREATE INDEX IF NOT EXISTS idx_fec_candidates_pcc ON fec_candidates(principal_committee_id);

CREATE TABLE IF NOT EXISTS fec_candidate_committee_links (
    candidate_id TEXT NOT NULL,
    candidate_election_year INTEGER,
    fec_election_year INTEGER,
    committee_id TEXT NOT NULL,
    committee_type TEXT,
    committee_designation TEXT,
    linkage_id TEXT,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (candidate_id, fec_election_year, committee_id, linkage_id)
);
CREATE INDEX IF NOT EXISTS idx_fec_ccl_committee ON fec_candidate_committee_links(committee_id);

CREATE TABLE IF NOT EXISTS fec_candidate_financial_summaries (
    file_family TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    candidate_name TEXT,
    incumbent_challenge_status TEXT,
    party_code TEXT,
    party TEXT,
    total_receipts REAL,
    total_disbursements REAL,
    cash_on_hand_close REAL,
    debts_owed_by_committee REAL,
    debts_owed_to_committee REAL,
    state TEXT,
    district TEXT,
    coverage_end_date TEXT,
    raw_json TEXT,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (file_family, candidate_id, source_file)
);

CREATE TABLE IF NOT EXISTS fec_committee_financial_summaries (
    committee_id TEXT NOT NULL,
    committee_name TEXT,
    committee_type TEXT,
    committee_designation TEXT,
    filing_frequency TEXT,
    total_receipts REAL,
    total_disbursements REAL,
    cash_on_hand_close REAL,
    debts_owed_by_committee REAL,
    debts_owed_to_committee REAL,
    individual_contributions REAL,
    other_committee_contributions REAL,
    party_committee_contributions REAL,
    coverage_end_date TEXT,
    raw_json TEXT,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (committee_id, source_file)
);
CREATE INDEX IF NOT EXISTS idx_fec_committee_fin_name ON fec_committee_financial_summaries(committee_name);

CREATE TABLE IF NOT EXISTS fec_committee_summary (
    committee_id TEXT NOT NULL,
    committee_name TEXT,
    committee_type TEXT,
    committee_designation TEXT,
    filing_frequency TEXT,
    committee_state TEXT,
    committee_city TEXT,
    treasurer_name TEXT,
    candidate_id TEXT,
    election_year INTEGER,
    individual_contributions REAL,
    total_contributions REAL,
    total_receipts REAL,
    total_disbursements REAL,
    independent_expenditures REAL,
    cash_on_hand_close REAL,
    coverage_start_date TEXT,
    coverage_end_date TEXT,
    debts_owed_by_committee REAL,
    debts_owed_to_committee REAL,
    raw_json TEXT,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (committee_id, election_year, source_file)
);
CREATE INDEX IF NOT EXISTS idx_fec_committee_summary_name ON fec_committee_summary(committee_name);
CREATE INDEX IF NOT EXISTS idx_fec_committee_summary_candidate ON fec_committee_summary(candidate_id);

CREATE TABLE IF NOT EXISTS fec_leadership_pacs (
    committee_id TEXT PRIMARY KEY,
    committee_name TEXT,
    link_image TEXT,
    sponsor_name TEXT,
    cash_on_hand REAL,
    coverage_end_date TEXT,
    total_disbursement REAL,
    total_receipt REAL,
    source_file TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pac_candidate_support (
    support_id TEXT PRIMARY KEY,
    advertiser TEXT NOT NULL,
    committee_id TEXT,
    supported_candidates TEXT,
    support_method TEXT,
    grand_total REAL,
    race_type TEXT,
    source_file TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pac_support_advertiser ON pac_candidate_support(advertiser);
CREATE INDEX IF NOT EXISTS idx_pac_support_committee ON pac_candidate_support(committee_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _s(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).replace("\u200b", "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def _int(value: object) -> int | None:
    text = _s(value)
    if text is None:
        return None
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return None


def _float(value: object) -> float | None:
    text = _s(value)
    if text is None:
        return None
    try:
        return float(re.sub(r"[^0-9.\-]", "", text))
    except ValueError:
        return None


def _norm_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _pick(row: dict, *names: str) -> object:
    lookup = {_norm_header(key): value for key, value in row.items()}
    for name in names:
        hit = lookup.get(_norm_header(name))
        if hit is not None:
            return hit
    return None


def _pipe_frame(path: Path, columns: list[str]) -> pd.DataFrame:
    return pd.read_csv(path, sep="|", header=None, names=columns, dtype=str, keep_default_na=False)


def _jsonable(row: dict) -> str:
    return json.dumps({key: (_s(value) if not isinstance(value, (int, float)) else value) for key, value in row.items()})


def _display_name(fec_name: object) -> str | None:
    text = _s(fec_name)
    if not text:
        return None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 2:
        last = parts[0]
        first = parts[1]
        suffix = " ".join(parts[2:])
        return " ".join(part for part in [first, last, suffix] if part).title()
    return text.title()


def _party_short(value: object) -> str | None:
    text = (_s(value) or "").upper()
    return {"DEM": "D", "REP": "R", "IND": "I", "LIB": "L", "GRE": "G"}.get(text, text[:1] or None)


def _office_value(value: object) -> str | None:
    text = (_s(value) or "").upper()
    return {"H": "us_house", "S": "us_senate", "P": "president"}.get(text)


def _race_id(row: pd.Series) -> str | None:
    office = _office_value(row.get("office"))
    state = (_s(row.get("state")) or "").upper()
    district = _s(row.get("district"))
    if not office or not state:
        return None
    if office == "us_house":
        district = str(int(float(district))) if district and district.replace(".", "", 1).isdigit() else district
        return f"us_house|{CYCLE}|{state}-{district or '0'}|general"
    if office == "us_senate":
        return f"us_senate|{CYCLE}|{state}|general"
    return None


def _seed_dashboard_from_fec_candidates(conn: sqlite3.Connection, frame: pd.DataFrame, source_file: str) -> int:
    now = utc_now()
    inserted = 0
    for _, row in frame.iterrows():
        race_id = _race_id(row)
        if not race_id:
            continue
        office = _office_value(row.get("office"))
        state = (_s(row.get("state")) or "").upper()
        district = _s(row.get("district"))
        if office == "us_house" and district:
            district = str(int(float(district))) if district.replace(".", "", 1).isdigit() else district
        else:
            district = None
        name = _display_name(row.get("candidate_name"))
        fec_candidate_id = _s(row.get("candidate_id"))
        if not name or not fec_candidate_id:
            continue
        existed = conn.execute("SELECT 1 FROM candidates WHERE candidate_id = ?", (f"fec_{fec_candidate_id}",)).fetchone()
        conn.execute(
            """
            INSERT INTO races (
                race_id, cycle, office, state, district, general_date,
                election_name, election_scope, election_type, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, '2026-11-03', ?, ?, 'general', ?, ?)
            ON CONFLICT(race_id) DO UPDATE SET
                source=COALESCE(races.source, excluded.source),
                updated_at=excluded.updated_at
            """,
            (
                race_id,
                CYCLE,
                office,
                state,
                district,
                f"{state}{('-' + district) if district else ''} {office.replace('_', ' ').title()} General",
                "district" if office == "us_house" else "state",
                f"FEC {source_file}",
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO candidates (
                candidate_id, full_name, party, race_id, incumbent,
                result, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                full_name=excluded.full_name,
                party=excluded.party,
                race_id=excluded.race_id,
                incumbent=MAX(candidates.incumbent, excluded.incumbent),
                updated_at=excluded.updated_at
            """,
            (
                f"fec_{fec_candidate_id}",
                name,
                _party_short(row.get("party")),
                race_id,
                1 if (_s(row.get("incumbent_challenge_status")) or "").upper() == "I" else 0,
                now,
            ),
        )
        if not existed:
            inserted += 1
    return inserted


def ingest_cn(path: Path, conn: sqlite3.Connection) -> dict:
    frame = _pipe_frame(path, CN_COLUMNS)
    now = utc_now()
    rows = []
    for _, row in frame.iterrows():
        candidate_id = _s(row["candidate_id"])
        if not candidate_id:
            continue
        rows.append(
            (
                candidate_id,
                _s(row["candidate_name"]),
                _s(row["party"]),
                _int(row["election_year"]),
                _s(row["state"]),
                _s(row["office"]),
                _s(row["district"]),
                _s(row["incumbent_challenge_status"]),
                _s(row["candidate_status"]),
                _s(row["principal_committee_id"]),
                _s(row["city"]),
                _s(row["mailing_state"]),
                _s(row["zip_code"]),
                path.name,
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO fec_candidates (
            candidate_id, candidate_name, party, election_year, state, office,
            district, incumbent_challenge_status, candidate_status,
            principal_committee_id, city, mailing_state, zip_code, source_file, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            candidate_name=excluded.candidate_name,
            party=excluded.party,
            election_year=excluded.election_year,
            state=excluded.state,
            office=excluded.office,
            district=excluded.district,
            incumbent_challenge_status=excluded.incumbent_challenge_status,
            candidate_status=excluded.candidate_status,
            principal_committee_id=excluded.principal_committee_id,
            city=excluded.city,
            mailing_state=excluded.mailing_state,
            zip_code=excluded.zip_code,
            source_file=excluded.source_file,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    seeded = _seed_dashboard_from_fec_candidates(conn, frame, path.name)
    return {"kind": "fec_candidates", "rows": len(rows), "dashboard_candidates_seeded": seeded, "file": path.name}


def ingest_ccl(path: Path, conn: sqlite3.Connection) -> dict:
    frame = _pipe_frame(path, CCL_COLUMNS)
    now = utc_now()
    rows = []
    for _, row in frame.iterrows():
        candidate_id = _s(row["candidate_id"])
        committee_id = _s(row["committee_id"])
        if not candidate_id or not committee_id:
            continue
        rows.append(
            (
                candidate_id,
                _int(row["candidate_election_year"]),
                _int(row["fec_election_year"]),
                committee_id,
                _s(row["committee_type"]),
                _s(row["committee_designation"]),
                _s(row["linkage_id"]) or "",
                path.name,
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO fec_candidate_committee_links (
            candidate_id, candidate_election_year, fec_election_year,
            committee_id, committee_type, committee_designation,
            linkage_id, source_file, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id, fec_election_year, committee_id, linkage_id) DO UPDATE SET
            candidate_election_year=excluded.candidate_election_year,
            committee_type=excluded.committee_type,
            committee_designation=excluded.committee_designation,
            source_file=excluded.source_file,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    return {"kind": "fec_candidate_committee_links", "rows": len(rows), "file": path.name}


def ingest_candidate_summary(path: Path, conn: sqlite3.Connection) -> dict:
    frame = _pipe_frame(path, CANDIDATE_SUMMARY_COLUMNS)
    now = utc_now()
    family = "webl" if path.name.lower().startswith("webl") else "weball"
    rows = []
    for _, row in frame.iterrows():
        candidate_id = _s(row["candidate_id"])
        if not candidate_id:
            continue
        raw = row.to_dict()
        rows.append(
            (
                family,
                candidate_id,
                _s(row["candidate_name"]),
                _s(row["incumbent_challenge_status"]),
                _s(row["party_code"]),
                _s(row["party"]),
                _float(row["total_receipts"]),
                _float(row["total_disbursements"]),
                _float(row["cash_on_hand_close"]),
                _float(row["debts_owed_by_committee"]),
                _float(row["debts_owed_to_committee"]),
                _s(row["state"]),
                _s(row["district"]),
                _s(row["coverage_end_date"]),
                _jsonable(raw),
                path.name,
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO fec_candidate_financial_summaries (
            file_family, candidate_id, candidate_name, incumbent_challenge_status,
            party_code, party, total_receipts, total_disbursements,
            cash_on_hand_close, debts_owed_by_committee, debts_owed_to_committee,
            state, district, coverage_end_date, raw_json, source_file, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_family, candidate_id, source_file) DO UPDATE SET
            candidate_name=excluded.candidate_name,
            incumbent_challenge_status=excluded.incumbent_challenge_status,
            party_code=excluded.party_code,
            party=excluded.party,
            total_receipts=excluded.total_receipts,
            total_disbursements=excluded.total_disbursements,
            cash_on_hand_close=excluded.cash_on_hand_close,
            debts_owed_by_committee=excluded.debts_owed_by_committee,
            debts_owed_to_committee=excluded.debts_owed_to_committee,
            state=excluded.state,
            district=excluded.district,
            coverage_end_date=excluded.coverage_end_date,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    return {"kind": "fec_candidate_financial_summaries", "rows": len(rows), "file": path.name}


def ingest_committee_financial_summary(path: Path, conn: sqlite3.Connection) -> dict:
    frame = _pipe_frame(path, COMMITTEE_SUMMARY_COLUMNS)
    now = utc_now()
    rows = []
    for _, row in frame.iterrows():
        committee_id = _s(row["committee_id"])
        if not committee_id:
            continue
        raw = row.to_dict()
        rows.append(
            (
                committee_id,
                _s(row["committee_name"]),
                _s(row["committee_type"]),
                _s(row["committee_designation"]),
                _s(row["filing_frequency"]),
                _float(row["total_receipts"]),
                _float(row["total_disbursements"]),
                _float(row["cash_on_hand_close"]),
                _float(row["debts_owed_by_committee"]),
                _float(row["debts_owed_to_committee"]),
                _float(row["individual_contributions"]),
                _float(row["other_committee_contributions"]),
                _float(row["party_committee_contributions"]),
                _s(row["coverage_end_date"]) or _s(row["file_coverage_end_date"]),
                _jsonable(raw),
                path.name,
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO fec_committee_financial_summaries (
            committee_id, committee_name, committee_type, committee_designation,
            filing_frequency, total_receipts, total_disbursements,
            cash_on_hand_close, debts_owed_by_committee, debts_owed_to_committee,
            individual_contributions, other_committee_contributions,
            party_committee_contributions, coverage_end_date, raw_json, source_file, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(committee_id, source_file) DO UPDATE SET
            committee_name=excluded.committee_name,
            committee_type=excluded.committee_type,
            committee_designation=excluded.committee_designation,
            filing_frequency=excluded.filing_frequency,
            total_receipts=excluded.total_receipts,
            total_disbursements=excluded.total_disbursements,
            cash_on_hand_close=excluded.cash_on_hand_close,
            debts_owed_by_committee=excluded.debts_owed_by_committee,
            debts_owed_to_committee=excluded.debts_owed_to_committee,
            individual_contributions=excluded.individual_contributions,
            other_committee_contributions=excluded.other_committee_contributions,
            party_committee_contributions=excluded.party_committee_contributions,
            coverage_end_date=excluded.coverage_end_date,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    return {"kind": "fec_committee_financial_summaries", "rows": len(rows), "file": path.name}


def ingest_committee_summary_csv(path: Path, conn: sqlite3.Connection) -> dict:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    now = utc_now()
    rows = []
    for raw in frame.to_dict("records"):
        committee_id = _s(_pick(raw, "CMTE_ID"))
        if not committee_id:
            continue
        rows.append(
            (
                committee_id,
                _s(_pick(raw, "CMTE_NM")),
                _s(_pick(raw, "CMTE_TP")),
                _s(_pick(raw, "CMTE_DSGN")),
                _s(_pick(raw, "CMTE_FILING_FREQ")),
                _s(_pick(raw, "CMTE_ST")),
                _s(_pick(raw, "CMTE_CITY")),
                _s(_pick(raw, "TRES_NM")),
                _s(_pick(raw, "CAND_ID")),
                _int(_pick(raw, "FEC_ELECTION_YR")),
                _float(_pick(raw, "INDV_CONTB")),
                _float(_pick(raw, "TTL_CONTB")),
                _float(_pick(raw, "TTL_RECEIPTS")),
                _float(_pick(raw, "TTL_DISB")),
                _float(_pick(raw, "INDT_EXP")),
                _float(_pick(raw, "COH_COP")),
                _s(_pick(raw, "CVG_START_DT")),
                _s(_pick(raw, "CVG_END_DT")),
                _float(_pick(raw, "DEBTS_OWED_BY_CMTE")),
                _float(_pick(raw, "DEBTS_OWED_TO_CMTE")),
                _jsonable(raw),
                path.name,
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO fec_committee_summary (
            committee_id, committee_name, committee_type, committee_designation,
            filing_frequency, committee_state, committee_city, treasurer_name,
            candidate_id, election_year, individual_contributions,
            total_contributions, total_receipts, total_disbursements,
            independent_expenditures, cash_on_hand_close, coverage_start_date,
            coverage_end_date, debts_owed_by_committee, debts_owed_to_committee,
            raw_json, source_file, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(committee_id, election_year, source_file) DO UPDATE SET
            committee_name=excluded.committee_name,
            committee_type=excluded.committee_type,
            committee_designation=excluded.committee_designation,
            filing_frequency=excluded.filing_frequency,
            committee_state=excluded.committee_state,
            committee_city=excluded.committee_city,
            treasurer_name=excluded.treasurer_name,
            candidate_id=excluded.candidate_id,
            individual_contributions=excluded.individual_contributions,
            total_contributions=excluded.total_contributions,
            total_receipts=excluded.total_receipts,
            total_disbursements=excluded.total_disbursements,
            independent_expenditures=excluded.independent_expenditures,
            cash_on_hand_close=excluded.cash_on_hand_close,
            coverage_start_date=excluded.coverage_start_date,
            coverage_end_date=excluded.coverage_end_date,
            debts_owed_by_committee=excluded.debts_owed_by_committee,
            debts_owed_to_committee=excluded.debts_owed_to_committee,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    return {"kind": "fec_committee_summary", "rows": len(rows), "file": path.name}


def ingest_leadership_csv(path: Path, conn: sqlite3.Connection) -> dict:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    now = utc_now()
    rows = []
    for raw in frame.to_dict("records"):
        committee_id = _s(_pick(raw, "Committee_Id"))
        if not committee_id:
            continue
        rows.append(
            (
                committee_id,
                _s(_pick(raw, "Committee_Name")),
                _s(_pick(raw, "Link_Image")),
                _s(_pick(raw, "Sponsor_Name")),
                _float(_pick(raw, "Cash_on_Hand")),
                _s(_pick(raw, "Coverage_End_Date")),
                _float(_pick(raw, "Total_Disbursement")),
                _float(_pick(raw, "Total_Receipt")),
                path.name,
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO fec_leadership_pacs (
            committee_id, committee_name, link_image, sponsor_name,
            cash_on_hand, coverage_end_date, total_disbursement,
            total_receipt, source_file, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(committee_id) DO UPDATE SET
            committee_name=excluded.committee_name,
            link_image=excluded.link_image,
            sponsor_name=excluded.sponsor_name,
            cash_on_hand=excluded.cash_on_hand,
            coverage_end_date=excluded.coverage_end_date,
            total_disbursement=excluded.total_disbursement,
            total_receipt=excluded.total_receipt,
            source_file=excluded.source_file,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    return {"kind": "fec_leadership_pacs", "rows": len(rows), "file": path.name}


def ingest_pac_support_csv(path: Path, conn: sqlite3.Connection) -> dict:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    now = utc_now()
    rows = []
    for raw in frame.to_dict("records"):
        advertiser = _s(_pick(raw, "Advertiser"))
        if not advertiser:
            continue
        rows.append(
            (
                hashlib.md5(
                    "|".join(
                        [
                            advertiser,
                            _s(_pick(raw, "Committee ID")) or "",
                            _s(_pick(raw, "Race Type")) or "",
                            path.name,
                        ]
                    ).encode()
                ).hexdigest()[:24],
                advertiser,
                _s(_pick(raw, "Committee ID")),
                _s(_pick(raw, "Supported Candidates")),
                _s(_pick(raw, "Support Method")),
                _float(_pick(raw, "Grand Total")),
                _s(_pick(raw, "Race Type")),
                path.name,
                now,
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO pac_candidate_support (
            support_id, advertiser, committee_id, supported_candidates, support_method,
            grand_total, race_type, source_file, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return {"kind": "pac_candidate_support", "rows": len(rows), "file": path.name}


def detect_kind(path: Path) -> str | None:
    name = path.name.lower()
    if name == "cn.txt":
        return "cn"
    if name == "ccl.txt":
        return "ccl"
    if name.startswith("weball") or name.startswith("webl"):
        return "candidate_summary"
    if name.startswith("webk"):
        return "committee_financial_summary"
    if name == "committee_summary_2026.csv":
        return "committee_summary_csv"
    if name == "leadership2026.csv":
        return "leadership_csv"
    if name.startswith("pac_to_candidates_2026"):
        return "pac_support_csv"
    return None


def ingest(path: Path | str, db_path: Path = DB_PATH) -> dict:
    data_file = Path(path)
    if not data_file.exists():
        raise FileNotFoundError(data_file)
    kind = detect_kind(data_file)
    if kind is None:
        raise ValueError(f"Unsupported FEC/PAC file: {data_file.name}")

    Store(db_path)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        if kind == "cn":
            summary = ingest_cn(data_file, conn)
        elif kind == "ccl":
            summary = ingest_ccl(data_file, conn)
        elif kind == "candidate_summary":
            summary = ingest_candidate_summary(data_file, conn)
        elif kind == "committee_financial_summary":
            summary = ingest_committee_financial_summary(data_file, conn)
        elif kind == "committee_summary_csv":
            summary = ingest_committee_summary_csv(data_file, conn)
        elif kind == "leadership_csv":
            summary = ingest_leadership_csv(data_file, conn)
        elif kind == "pac_support_csv":
            summary = ingest_pac_support_csv(data_file, conn)
        else:
            raise AssertionError(kind)
        conn.commit()
    finally:
        conn.close()
    Store(db_path).set_metadata("last_fec_local_ingest", utc_now())
    return summary


def ingest_many(paths: Iterable[Path | str], db_path: Path = DB_PATH) -> list[dict]:
    results = []
    for path in paths:
        data_file = Path(path)
        if detect_kind(data_file) is None:
            continue
        results.append(ingest(data_file, db_path=db_path))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path, help="FEC/PAC files to ingest")
    args = parser.parse_args()
    print(json.dumps(ingest_many(args.paths), indent=2))


if __name__ == "__main__":
    main()
