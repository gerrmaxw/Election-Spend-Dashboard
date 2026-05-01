"""Fuzzy-match unresolved spend_records to the candidate roster.

Walks every spend_records row with race_id='unresolved' that has parsed
metadata (likely_state, likely_office, likely_surname) and tries to match
it to a candidate in the local DB. On a confident match, updates the
spend row's candidate_id and race_id.

Run after `ingest_dma_races.py` to convert the bulk of the $2B+ unresolved
spend into linked records.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz

from config.settings import DB_PATH

THRESHOLD = 88   # min surname match score


def _surname_of(full_name: str) -> str:
    """Last whitespace-delimited token of full_name (lowercased)."""
    return (full_name or "").strip().split()[-1].lower() if full_name else ""


def match(source_batch_id: str | None = None, verbose: bool = True) -> dict:
    """Fuzzy-match unresolved spend rows to the candidate roster.

    Args:
        source_batch_id: If provided, only match rows from that ingest batch
                         (used by ingest_advertiser_file.ingest() to scope work
                         to the just-uploaded file). If None, scans all
                         unresolved spend rows in the DB.
        verbose: Print progress.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Build candidate index: (state, office) → list[(candidate_id, race_id, full_name)]
    cands = conn.execute("""
        SELECT c.candidate_id, c.race_id, c.full_name, r.state, r.office
        FROM candidates c JOIN races r ON r.race_id=c.race_id
        WHERE r.cycle = 2026
    """).fetchall()
    by_so: dict[tuple[str, str], list[dict]] = {}
    for c in cands:
        key = (c["state"], c["office"])
        by_so.setdefault(key, []).append(dict(c))
    if verbose:
        print(f"Indexed {len(cands)} candidates across {len(by_so)} state/office buckets")

    # Pull unresolved spend with parseable metadata, optionally batch-scoped
    base_q = """
        SELECT spend_id, advertiser_name, likely_state, likely_office, likely_surname
        FROM spend_records
        WHERE race_id = 'unresolved'
          AND likely_surname IS NOT NULL
          AND likely_state   IS NOT NULL
          AND likely_office  IS NOT NULL
    """
    if source_batch_id:
        rows = conn.execute(base_q + " AND source_batch_id = ?",
                            (source_batch_id,)).fetchall()
    else:
        rows = conn.execute(base_q).fetchall()
    if verbose:
        scope = f" in batch {source_batch_id}" if source_batch_id else ""
        print(f"Unresolved spend rows{scope}: {len(rows)}")

    now = datetime.now(timezone.utc).isoformat()
    matched = 0
    no_pool = 0
    no_match = 0

    for r in rows:
        pool = by_so.get((r["likely_state"], r["likely_office"]))
        if not pool:
            no_pool += 1
            continue

        sur = (r["likely_surname"] or "").lower()
        best, best_score = None, 0.0
        for c in pool:
            score = fuzz.partial_ratio(sur, _surname_of(c["full_name"]))
            # Also try full-name match for hyphenated/multi-token surnames
            score2 = fuzz.partial_ratio(sur, (c["full_name"] or "").lower())
            score = max(score, score2)
            if score > best_score:
                best_score = score
                best = c

        if best is None or best_score < THRESHOLD:
            no_match += 1
            continue

        conn.execute(
            """
            UPDATE spend_records SET
                candidate_id    = ?,
                race_id         = ?,
                match_source    = 'roster_fuzzy',
                match_reason    = ?,
                match_confidence = ?,
                updated_at      = ?
            WHERE spend_id = ?
            """,
            (best["candidate_id"], best["race_id"],
             f"roster fuzzy match: '{r['advertiser_name']}' → "
             f"{best['full_name']} ({best['state']} {best['office']}, score={int(best_score)})",
             best_score / 100.0, now, r["spend_id"]),
        )
        matched += 1

    conn.commit()
    conn.close()
    summary = {
        "rows_examined": len(rows),
        "matched": matched,
        "no_candidate_pool": no_pool,
        "no_confident_match": no_match,
        "threshold": THRESHOLD,
    }
    if verbose:
        print(summary)
    return summary


if __name__ == "__main__":
    match()
