"""Load the 2026 political-window calendar (DMA-level open/close + election dates).

Reads `local_data/2026_political_calendar_by_market.xlsx` and upserts rows into
a new `political_windows` table. Each row is one (state, dma, window_type,
window_open, election_date) tuple. Multi-state DMAs (e.g. "DC/MD/VA") are
preserved verbatim — the dashboard splits them when filtering by state.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH

DATA_FILE = ROOT / "local_data" / "2026_political_calendar_by_market.xlsx"
CYCLE = 2026
SOURCE = "2026 Political Calendar by Market (xlsx)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS political_windows (
    state          TEXT NOT NULL,
    region         TEXT,
    dma            TEXT NOT NULL,
    window_type    TEXT NOT NULL,
    window_open    TEXT NOT NULL,
    election_date  TEXT NOT NULL,
    cycle          INTEGER NOT NULL,
    source         TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (state, dma, window_type, election_date, window_open)
);
CREATE INDEX IF NOT EXISTS idx_pw_state    ON political_windows(state);
CREATE INDEX IF NOT EXISTS idx_pw_dma      ON political_windows(dma);
CREATE INDEX IF NOT EXISTS idx_pw_election ON political_windows(election_date);
CREATE INDEX IF NOT EXISTS idx_pw_open     ON political_windows(window_open);
"""


def _iso(d) -> str | None:
    if pd.isna(d):
        return None
    if isinstance(d, str):
        return d[:10]
    return d.strftime("%Y-%m-%d")


def _s(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def load(path: Path | str = DATA_FILE) -> dict:
    data_file = Path(path)
    if not data_file.exists():
        raise FileNotFoundError(f"Missing {data_file}")

    df = pd.read_excel(data_file)
    df.columns = [c.strip() for c in df.columns]

    market_col = "Market/DMA" if "Market/DMA" in df.columns else "Market" if "Market" in df.columns else None
    expected = {"State", "Region", "WINDOW TYPE", "WINDOW OPEN DATE", "ELECTION DATE"}
    missing = expected - set(df.columns)
    if market_col is None:
        missing.add("Market/DMA")
    if missing:
        raise ValueError(f"Missing columns in xlsx: {missing}")

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat()

    inserted = updated = skipped = 0
    for _, row in df.iterrows():
        state = _s(row["State"])
        dma = _s(row[market_col])
        wtype = _s(row["WINDOW TYPE"]).upper()
        open_d = _iso(row["WINDOW OPEN DATE"])
        elect_d = _iso(row["ELECTION DATE"])
        region = _s(row["Region"]) or None

        if not (state and dma and wtype and open_d and elect_d):
            skipped += 1
            continue

        exists = conn.execute(
            """
            SELECT 1 FROM political_windows
            WHERE state=? AND dma=? AND window_type=? AND election_date=? AND window_open=?
            """,
            (state, dma, wtype, elect_d, open_d),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO political_windows
                (state, region, dma, window_type, window_open, election_date,
                 cycle, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state, dma, window_type, election_date, window_open) DO UPDATE SET
                region=excluded.region,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (state, region, dma, wtype, open_d, elect_d, CYCLE, f"{SOURCE}: {data_file.name}", now),
        )
        if exists:
            updated += 1
        else:
            inserted += 1

    conn.commit()
    conn.close()

    summary = {
        "rows": len(df),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }
    print(summary)
    return summary


if __name__ == "__main__":
    load()
