"""Load the Comcast Advertising footprint US House district dataset.

848 (district × DMA) crossings covering 289 districts and 58 DMAs across
the Comcast footprint, with the current (119th Congress) representative
attached. Three side effects:

  1. Adds `comcast_footprint` flag to `dma_district_map` and upserts
     every (DMA, state, district) crossing from this file with the flag set.
  2. Upserts an "incumbent" candidate per district into `candidates`
     (creating the race row if missing) so the spend-roster matcher has
     more incumbents to fuzzy-match against.
  3. Tags `races` rows with the incumbent name in `election_name` for
     downstream display.

Run after `ingest_dma_races.py` so that file's race ids stay primary.
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

DATA_FILE = ROOT / "local_data" / "comcast_footprint_house_districts.xlsx"
CYCLE = 2026
GENERAL_DATE = "2026-11-03"
SOURCE = "Comcast footprint House districts (xlsx)"

PARTY_TO_DB = {
    "republican": "R",
    "democrat":   "D",
    "democratic": "D",
    "independent": "I",
    "libertarian": "L",
    "green":      "G",
    "vacant":     "U",
}


def _race_id(state: str, district: str) -> str:
    return f"us_house|{CYCLE}|{state}-{district}|general"


def _candidate_id(full_name: str, race_id: str) -> str:
    h = hashlib.md5(f"{race_id}|{full_name.lower().strip()}".encode()).hexdigest()[:12]
    return f"cand_{h}"


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(dma_district_map)").fetchall()}
    if "comcast_footprint" not in cols:
        conn.execute("ALTER TABLE dma_district_map ADD COLUMN comcast_footprint INTEGER DEFAULT 0")
    if "market_short" not in cols:
        conn.execute("ALTER TABLE dma_district_map ADD COLUMN market_short TEXT")


def load() -> dict:
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Missing {DATA_FILE}")

    df = pd.read_excel(DATA_FILE)
    df.columns = [c.strip() for c in df.columns]

    conn = sqlite3.connect(DB_PATH)
    _ensure_columns(conn)
    now = datetime.now(timezone.utc).isoformat()

    dma_added = dma_updated = 0
    races_added = races_updated = 0
    cands_added = cands_updated = 0
    vacant = 0
    skipped = 0

    seen_races: set[str] = set()
    seen_cands: set[str] = set()

    def _s(v) -> str:
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()

    for _, r in df.iterrows():
        state = _s(r.get("STATE_ABBR")).upper()
        cd_raw = r.get("CDFIPS")
        cd = int(cd_raw) if pd.notna(cd_raw) else 0
        if not state or not cd:
            skipped += 1
            continue
        district = str(cd)
        dma = _s(r.get("DMA"))
        market_short = _s(r.get("MARKET")) or None
        full_name = _s(r.get("NAME"))
        party_raw = _s(r.get("PARTY")).lower()
        party = PARTY_TO_DB.get(party_raw, "U")

        race_id = _race_id(state, district)

        # ── races upsert (only if not already loaded by another script) ─────
        if race_id not in seen_races:
            existed = conn.execute(
                "SELECT 1 FROM races WHERE race_id=?", (race_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO races (race_id, cycle, office, state, district,
                                   general_date, election_name, election_scope,
                                   election_type, source, updated_at)
                VALUES (?, ?, 'us_house', ?, ?, ?, ?, 'district', 'general', ?, ?)
                ON CONFLICT(race_id) DO UPDATE SET
                    election_name = COALESCE(races.election_name, excluded.election_name),
                    updated_at    = excluded.updated_at
                """,
                (race_id, CYCLE, state, district, GENERAL_DATE,
                 f"{state}-{district} U.S. House General",
                 SOURCE, now),
            )
            if existed:
                races_updated += 1
            else:
                races_added += 1
            seen_races.add(race_id)

        # ── candidates upsert (only if we have a name; vacant seats skipped) ─
        if full_name:
            cand_id = _candidate_id(full_name, race_id)
            if cand_id not in seen_cands:
                seen_cands.add(cand_id)
                existed = conn.execute(
                    "SELECT 1 FROM candidates WHERE candidate_id=?", (cand_id,)
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO candidates (candidate_id, full_name, party, race_id,
                                            incumbent, result, updated_at)
                    VALUES (?, ?, ?, ?, 1, 'pending', ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        full_name = excluded.full_name,
                        party     = excluded.party,
                        incumbent = 1,
                        updated_at = excluded.updated_at
                    """,
                    (cand_id, full_name, party, race_id, now),
                )
                if existed:
                    cands_updated += 1
                else:
                    cands_added += 1
        else:
            vacant += 1

        # ── dma_district_map upsert ────────────────────────────────────────
        existed_dma = conn.execute(
            "SELECT 1 FROM dma_district_map WHERE dma=? AND state=? AND district=? AND cycle=?",
            (dma, state, district, CYCLE),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO dma_district_map (dma, state, district, district_label,
                                          pvi, incumbent, incumbent_party,
                                          status, cycle, source, updated_at,
                                          comcast_footprint, market_short)
            VALUES (?, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?, ?, 1, ?)
            ON CONFLICT(dma, state, district, cycle) DO UPDATE SET
                incumbent          = COALESCE(excluded.incumbent, dma_district_map.incumbent),
                incumbent_party    = COALESCE(excluded.incumbent_party, dma_district_map.incumbent_party),
                comcast_footprint  = 1,
                market_short       = excluded.market_short,
                source             = excluded.source,
                updated_at         = excluded.updated_at
            """,
            (dma, state, district, full_name or None, party,
             CYCLE, SOURCE, now, market_short),
        )
        if existed_dma:
            dma_updated += 1
        else:
            dma_added += 1

    conn.commit()
    conn.close()

    summary = {
        "rows": len(df),
        "districts_in_file": df["DISTRICTID"].nunique(),
        "dmas_in_file": df["DMA"].nunique(),
        "vacant": vacant,
        "skipped": skipped,
        "dma_district_added": dma_added,
        "dma_district_updated": dma_updated,
        "races_added": races_added,
        "races_updated": races_updated,
        "candidates_added": cands_added,
        "candidates_updated": cands_updated,
    }
    print(summary)
    return summary


if __name__ == "__main__":
    load()
