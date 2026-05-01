"""Ingest 2026 U.S. House races by TV DMA from the Wikipedia-derived dataset.

Reads `local_data/dma_house_races_2026.py` (a copy of the Streamlit data file
the user downloaded) and writes to:

  - races              (upsert one row per state-district)
  - candidates         (upsert one row per candidate per race)
  - dma_district_map   (NEW) one row per (DMA, state, district) tuple, plus
                       PVI, incumbent, status — this is the linkage we need
                       for broadcast waste analysis.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH

DATA_FILE = ROOT / "local_data" / "dma_house_races_2026.py"
CYCLE = 2026
GENERAL_DATE = "2026-11-03"
SOURCE = "Wikipedia 2026 House (Apr 2026)"

DMA_SCHEMA = """
CREATE TABLE IF NOT EXISTS dma_district_map (
    dma                TEXT NOT NULL,
    state              TEXT NOT NULL,
    district           TEXT NOT NULL,
    district_label     TEXT,
    pvi                TEXT,
    incumbent          TEXT,
    incumbent_party    TEXT,
    status             TEXT,
    cycle              INTEGER NOT NULL,
    source             TEXT,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (dma, state, district, cycle)
);
CREATE INDEX IF NOT EXISTS idx_dma_district_state ON dma_district_map(state, district);
CREATE INDEX IF NOT EXISTS idx_dma_district_dma   ON dma_district_map(dma);
"""

# District string examples we need to parse:
#   "NM-1 (Albuquerque core/east Bernalillo)"
#   "GA-3 (SW Atlanta suburbs/Columbus NE)"
#   "WY-AL (statewide)"
#   "MT-AL"
DISTRICT_RE = re.compile(r"^\s*([A-Z]{2})-([A-Z0-9]+)\s*(?:\(([^)]*)\))?", re.I)


def _load_dma_data() -> dict:
    """Parse DATA_FILE with AST and return only the DMA_DATA dict literal."""
    source = DATA_FILE.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DMA_DATA":
                    return ast.literal_eval(node.value)
    raise RuntimeError("DMA_DATA assignment not found in data file")


def _parse_district(district_str: str) -> tuple[str, str, str]:
    m = DISTRICT_RE.match(district_str)
    if not m:
        raise ValueError(f"Cannot parse district: {district_str!r}")
    state, district, label = m.group(1).upper(), m.group(2).upper(), (m.group(3) or "").strip()
    return state, district, label


def _party_to_db(party: str) -> str:
    p = (party or "").strip().lower()
    if p.startswith("dem"):
        return "D"
    if p.startswith("rep"):
        return "R"
    if p.startswith("ind"):
        return "I"
    if p.startswith("lib"):
        return "L"
    if p.startswith("gre"):
        return "G"
    return p[:1].upper() if p else "U"


def _race_id(state: str, district: str) -> str:
    return f"us_house|{CYCLE}|{state}-{district}|general"


def _candidate_id(full_name: str, race_id: str) -> str:
    h = hashlib.md5(f"{race_id}|{full_name.lower().strip()}".encode()).hexdigest()[:12]
    return f"cand_{h}"


def load() -> dict:
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Missing {DATA_FILE}")
    DMA_DATA = _load_dma_data()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DMA_SCHEMA)
    now = datetime.now(timezone.utc).isoformat()

    races_added = races_updated = 0
    cands_added = cands_updated = 0
    dma_rows = 0
    skipped = 0

    seen_races: set[str] = set()
    seen_cands: set[str] = set()

    for dma, districts in DMA_DATA.items():
        for d in districts:
            try:
                state, district, label = _parse_district(d["district"])
            except ValueError as e:
                print(f"  skip: {e}")
                skipped += 1
                continue

            race_id = _race_id(state, district)
            election_name = f"{state}-{district} U.S. House General"
            inc_party = _party_to_db(d.get("inc_party") or "")

            # ── races upsert ────────────────────────────────────────────────
            if race_id not in seen_races:
                exists = conn.execute("SELECT 1 FROM races WHERE race_id=?", (race_id,)).fetchone()
                conn.execute(
                    """
                    INSERT INTO races (race_id, cycle, office, state, district,
                                       general_date, election_name, election_scope,
                                       election_type, source, updated_at)
                    VALUES (?, ?, 'us_house', ?, ?, ?, ?, 'district', 'general', ?, ?)
                    ON CONFLICT(race_id) DO UPDATE SET
                        general_date=excluded.general_date,
                        election_name=excluded.election_name,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    (race_id, CYCLE, state, district, GENERAL_DATE,
                     election_name, SOURCE, now),
                )
                if exists:
                    races_updated += 1
                else:
                    races_added += 1
                seen_races.add(race_id)

            # ── candidates upsert ───────────────────────────────────────────
            incumbent_name = (d.get("incumbent") or "").strip().lower()
            for cand in d.get("candidates", []):
                full_name = (cand.get("name") or "").strip()
                if not full_name:
                    continue
                cand_id = _candidate_id(full_name, race_id)
                if cand_id in seen_cands:
                    continue
                seen_cands.add(cand_id)

                is_inc = 1 if full_name.lower() == incumbent_name else 0
                party = _party_to_db(cand.get("party") or "")

                exists = conn.execute(
                    "SELECT 1 FROM candidates WHERE candidate_id=?", (cand_id,)
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO candidates (candidate_id, full_name, party, race_id,
                                            incumbent, result, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        full_name=excluded.full_name,
                        party=excluded.party,
                        incumbent=excluded.incumbent,
                        updated_at=excluded.updated_at
                    """,
                    (cand_id, full_name, party, race_id, is_inc, now),
                )
                if exists:
                    cands_updated += 1
                else:
                    cands_added += 1

            # ── dma_district_map upsert ─────────────────────────────────────
            conn.execute(
                """
                INSERT INTO dma_district_map (dma, state, district, district_label,
                                              pvi, incumbent, incumbent_party,
                                              status, cycle, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dma, state, district, cycle) DO UPDATE SET
                    district_label=excluded.district_label,
                    pvi=excluded.pvi,
                    incumbent=excluded.incumbent,
                    incumbent_party=excluded.incumbent_party,
                    status=excluded.status,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (dma, state, district, label, d.get("pvi"),
                 d.get("incumbent"), inc_party, d.get("status"),
                 CYCLE, SOURCE, now),
            )
            dma_rows += 1

    conn.commit()
    conn.close()

    summary = {
        "dmas": len(DMA_DATA),
        "districts": len(seen_races),
        "races_added": races_added,
        "races_updated": races_updated,
        "candidates_added": cands_added,
        "candidates_updated": cands_updated,
        "dma_district_rows": dma_rows,
        "skipped": skipped,
    }
    print(summary)
    return summary


if __name__ == "__main__":
    load()
