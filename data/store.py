from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from data.schema import Candidate, Race, SpendRecord


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS races (
    race_id TEXT PRIMARY KEY,
    cycle INTEGER NOT NULL,
    office TEXT NOT NULL,
    state TEXT NOT NULL,
    district TEXT,
    general_date TEXT,
    election_name TEXT,
    election_scope TEXT,
    election_type TEXT,
    source TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    party TEXT,
    race_id TEXT NOT NULL,
    incumbent INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL,
    votes_received INTEGER,
    vote_share REAL,
    margin REAL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);

CREATE TABLE IF NOT EXISTS spend_records (
    spend_id TEXT PRIMARY KEY,
    source_batch_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_data_type TEXT,
    source_race_type TEXT,
    source_state TEXT,
    source_district TEXT,
    source_party_affiliation TEXT,
    advertiser_type TEXT,
    advertiser_name TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    race_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    gross_amount REAL NOT NULL,
    match_source TEXT NOT NULL,
    match_reason TEXT,
    match_confidence REAL NOT NULL DEFAULT 0,
    likely_state TEXT,
    likely_office TEXT,
    likely_surname TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_race ON candidates(race_id);
CREATE INDEX IF NOT EXISTS idx_spend_candidate ON spend_records(candidate_id);
CREATE INDEX IF NOT EXISTS idx_spend_race ON spend_records(race_id);
CREATE INDEX IF NOT EXISTS idx_spend_batch ON spend_records(source_batch_id);
CREATE INDEX IF NOT EXISTS idx_spend_advertiser ON spend_records(advertiser_name);

CREATE TABLE IF NOT EXISTS spend_aggregates (
    aggregate_id TEXT PRIMARY KEY,
    source_batch_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    label TEXT NOT NULL,
    share_of_total REAL,
    gross_spending REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spend_aggregates_type ON spend_aggregates(aggregate_type);
CREATE INDEX IF NOT EXISTS idx_spend_aggregates_batch ON spend_aggregates(source_batch_id);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Lightweight migration: ensure newer optional columns exist
            existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(spend_records)").fetchall()}
            optional_columns = {
                "agency": "TEXT",
                "is_national": "INTEGER DEFAULT 0",
                "source_data_type": "TEXT",
                "source_race_type": "TEXT",
                "source_state": "TEXT",
                "source_district": "TEXT",
                "source_party_affiliation": "TEXT",
                "advertiser_type": "TEXT",
            }
            for column, definition in optional_columns.items():
                if column not in existing_cols:
                    conn.execute(f"ALTER TABLE spend_records ADD COLUMN {column} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spend_source_data_type ON spend_records(source_data_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spend_source_race_type ON spend_records(source_race_type)")

    def upsert_races(self, races: Iterable[Race]) -> int:
        rows = [
            (
                race.race_id,
                race.cycle,
                race.office.value,
                race.state,
                race.district,
                race.general_date.isoformat() if race.general_date else None,
                race.election_name,
                race.election_scope,
                race.election_type,
                race.source,
                utc_now(),
            )
            for race in races
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO races (
                    race_id, cycle, office, state, district, general_date,
                    election_name, election_scope, election_type, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(race_id) DO UPDATE SET
                    cycle=excluded.cycle,
                    office=excluded.office,
                    state=excluded.state,
                    district=excluded.district,
                    general_date=excluded.general_date,
                    election_name=excluded.election_name,
                    election_scope=excluded.election_scope,
                    election_type=excluded.election_type,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        self.set_metadata("last_civicapi_refresh", utc_now())
        return len(rows)

    def upsert_candidates(self, candidates: Iterable[Candidate]) -> int:
        rows = [
            (
                candidate.candidate_id,
                candidate.full_name,
                candidate.party,
                candidate.race_id,
                1 if candidate.incumbent else 0,
                candidate.result.value,
                candidate.votes_received,
                candidate.vote_share,
                candidate.margin,
                utc_now(),
            )
            for candidate in candidates
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO candidates (
                    candidate_id, full_name, party, race_id, incumbent, result,
                    votes_received, vote_share, margin, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    party=excluded.party,
                    race_id=excluded.race_id,
                    incumbent=excluded.incumbent,
                    result=excluded.result,
                    votes_received=excluded.votes_received,
                    vote_share=excluded.vote_share,
                    margin=excluded.margin,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def upsert_spend(self, records: Iterable[SpendRecord]) -> int:
        rows = [
            (
                record.spend_id,
                record.source_batch_id,
                record.source_file,
                getattr(record, "source_data_type", None),
                getattr(record, "source_race_type", None),
                getattr(record, "source_state", None),
                getattr(record, "source_district", None),
                getattr(record, "source_party_affiliation", None),
                getattr(record, "advertiser_type", None),
                record.advertiser_name,
                record.candidate_id,
                record.race_id,
                record.media_type.value,
                record.gross_amount,
                record.match_source,
                record.match_reason,
                record.match_confidence,
                record.likely_state,
                record.likely_office,
                record.likely_surname,
                getattr(record, "agency", None),
                int(getattr(record, "is_national", 0) or 0),
                utc_now(),
            )
            for record in records
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO spend_records (
                    spend_id, source_batch_id, source_file, source_data_type,
                    source_race_type, source_state, source_district,
                    source_party_affiliation, advertiser_type, advertiser_name,
                    candidate_id, race_id, media_type, gross_amount, match_source,
                    match_reason, match_confidence, likely_state, likely_office,
                    likely_surname, agency, is_national, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spend_id) DO UPDATE SET
                    source_batch_id=excluded.source_batch_id,
                    source_file=excluded.source_file,
                    source_data_type=excluded.source_data_type,
                    source_race_type=excluded.source_race_type,
                    source_state=excluded.source_state,
                    source_district=excluded.source_district,
                    source_party_affiliation=excluded.source_party_affiliation,
                    advertiser_type=excluded.advertiser_type,
                    advertiser_name=excluded.advertiser_name,
                    candidate_id=excluded.candidate_id,
                    race_id=excluded.race_id,
                    media_type=excluded.media_type,
                    gross_amount=excluded.gross_amount,
                    match_source=excluded.match_source,
                    match_reason=excluded.match_reason,
                    match_confidence=excluded.match_confidence,
                    likely_state=excluded.likely_state,
                    likely_office=excluded.likely_office,
                    likely_surname=excluded.likely_surname,
                    agency=excluded.agency,
                    is_national=excluded.is_national,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        self.set_metadata("last_advertiser_ingest", utc_now())
        return len(rows)

    def upsert_spend_aggregates(self, aggregates: Iterable[dict]) -> int:
        rows = [row for row in aggregates]
        if not rows:
            return 0
        with self._connect() as conn:
            for row in rows:
                timestamp = utc_now()
                updated = conn.execute(
                    """
                    UPDATE spend_aggregates
                    SET aggregate_id = ?,
                        source_batch_id = ?,
                        source_file = ?,
                        aggregate_type = ?,
                        label = ?,
                        share_of_total = ?,
                        gross_spending = ?,
                        updated_at = ?
                    WHERE aggregate_id = ?
                       OR (
                            source_batch_id = ?
                            AND source_file = ?
                            AND aggregate_type = ?
                            AND ABS(gross_spending - ?) < 0.005
                            AND (
                                (share_of_total IS NULL AND ? IS NULL)
                                OR ABS(COALESCE(share_of_total, -999999.0) - COALESCE(?, -999999.0)) < 0.0000001
                            )
                       )
                    """,
                    (
                        row["aggregate_id"],
                        row["source_batch_id"],
                        row["source_file"],
                        row["aggregate_type"],
                        row["label"],
                        row.get("share_of_total"),
                        row["gross_spending"],
                        timestamp,
                        row["aggregate_id"],
                        row["source_batch_id"],
                        row["source_file"],
                        row["aggregate_type"],
                        row["gross_spending"],
                        row.get("share_of_total"),
                        row.get("share_of_total"),
                    ),
                )
                if updated.rowcount:
                    continue
                conn.execute(
                    """
                    INSERT INTO spend_aggregates (
                        aggregate_id, source_batch_id, source_file, aggregate_type,
                        label, share_of_total, gross_spending, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["aggregate_id"],
                        row["source_batch_id"],
                        row["source_file"],
                        row["aggregate_type"],
                        row["label"],
                        row.get("share_of_total"),
                        row["gross_spending"],
                        timestamp,
                    ),
                )
        self.set_metadata("last_spend_aggregate_ingest", utc_now())
        return len(rows)

    def set_metadata(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def get_metadata(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def log_pipeline_run(self, job_name: str, status: str, details: str = "") -> None:
        timestamp = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pipeline_runs (job_name, status, details, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_name, status, details, timestamp, timestamp),
            )

    def table_counts(self) -> dict[str, int]:
        tables = [
            "races",
            "candidates",
            "spend_records",
            "spend_aggregates",
            "fec_ie_by_committee",
            "fec_candidates",
            "fec_candidate_committee_links",
            "fec_committee_summary",
            "fec_leadership_pacs",
            "pac_candidate_support",
            "analysis_candidate_view",
            "analysis_advertiser_view",
        ]
        counts: dict[str, int] = {}
        with self._connect() as conn:
            for table in tables:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
                    (table,),
                ).fetchone()
                if not exists:
                    counts[table] = 0
                    continue
                counts[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        return counts

    def query_df(self, sql: str, params: tuple | list | None = None) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(sql, conn, params=params or ())
