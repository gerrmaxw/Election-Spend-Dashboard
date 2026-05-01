"""FEC Schedule E fetcher — builds a committee→candidate lookup for
independent expenditures (IEs), used to resolve PAC advertiser names in the
Home_Advertiser spend file to the candidate(s) they actually supported.

Uses the /schedules/schedule_e/by_candidate/ aggregation endpoint, which
returns one row per (committee_id, candidate_id) with summed IE dollars per
cycle. This is dramatically cheaper than paginating all 8,000+ raw transactions.

Federal only. FEC doesn't carry state-level IEs (gubernatorial, AG, state leg
PAC spending). Those gaps are documented and left for manual resolution.

Output: populates a new `fec_ie_by_committee` table in the analyzer SQLite
with columns (cycle, committee_id, committee_name, candidate_id,
candidate_name, office, state, district, support_oppose, total_spent,
transaction_count).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH, CACHE_DIR


BASE = "https://api.open.fec.gov/v1"
# Only 28 target states — matches the earlier CivicAPI scope. Keeps the fetch
# efficient and focused on the same geography as the rest of the analyzer.
TARGET_STATES = [
    "CA", "TX", "GA", "IL", "VA", "MI", "NY", "LA", "NH", "SC",
    "FL", "PA", "NJ", "MD", "IN", "TN", "AR", "CO", "MN", "NM",
    "MS", "OR", "MA", "WA", "DC", "CT", "DE", "VT",
]


def _fec_key() -> str:
    key = os.environ.get("FEC_API_KEY", "").strip()
    if not key:
        print("ERROR: set FEC_API_KEY in your environment first.")
        print("  export FEC_API_KEY='<your key>'")
        sys.exit(1)
    return key


_fec_cache_dir = CACHE_DIR / "fec_ie"
_fec_cache_dir.mkdir(parents=True, exist_ok=True)


def _get(path: str, params: dict, cache_ttl_hours: int = 24) -> dict:
    """GET with on-disk caching and rate-limit backoff."""
    params = {**params, "api_key": _fec_key()}
    key = f"{path}?{json.dumps({k: v for k, v in params.items() if k != 'api_key'}, sort_keys=True)}"
    h = hashlib.sha256(key.encode()).hexdigest()[:20]
    cp = _fec_cache_dir / f"{h}.json"
    if cp.exists() and (time.time() - cp.stat().st_mtime) < cache_ttl_hours * 3600:
        return json.loads(cp.read_text())

    for attempt in range(5):
        try:
            r = requests.get(f"{BASE}{path}", params=params, timeout=30)
            if r.status_code in (429, 502, 503, 504):
                wait = min(60, 2 ** (attempt + 2))
                print(f"  {r.status_code} on {path}, sleeping {wait}s…")
                time.sleep(wait)
                continue
            r.raise_for_status()
            payload = r.json()
            cp.write_text(json.dumps(payload))
            time.sleep(0.4)  # stay under 120/min for upgraded keys, well under 1000/hr
            return payload
        except requests.RequestException as e:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


# ─── IE aggregation fetchers ─────────────────────────────────────────────
@dataclass
class IELink:
    cycle: int
    committee_id: str
    committee_name: Optional[str]
    candidate_id: str
    candidate_name: Optional[str]
    office: str                        # "H", "S", or "P"
    state: Optional[str]
    district: Optional[str]
    support_oppose: str                # "S" or "O"
    total_spent: float
    transaction_count: int


def _paginate_raw(cycle: int, office_code: str, state: Optional[str],
                   support_oppose: str) -> Iterator[dict]:
    """Paginate /schedules/schedule_e/ using last_index cursor pagination.
    The by_candidate aggregation endpoint requires district for House, which
    forces 435 API calls. The raw endpoint filtered by candidate_office lets
    us pull all of a state's House IEs in one paginated sweep."""
    params_base = {
        "cycle": cycle,
        "candidate_office": office_code,
        "support_oppose_indicator": support_oppose,
        "per_page": 100,
        "sort": "expenditure_date",  # deterministic for cursor pagination
    }
    if state:
        params_base["candidate_office_state"] = state

    last_index = None
    last_date = None
    while True:
        params = dict(params_base)
        if last_index:
            params["last_index"] = last_index
            params["last_expenditure_date"] = last_date
        data = _get("/schedules/schedule_e/", params)
        results = data.get("results", [])
        if not results:
            return
        for r in results:
            yield r
        pag = data.get("pagination", {}) or {}
        last = pag.get("last_indexes") or {}
        new_last_index = last.get("last_index")
        new_last_date = last.get("last_expenditure_date")
        if not new_last_index or new_last_index == last_index:
            return
        last_index = new_last_index
        last_date = new_last_date


def aggregate_client_side(records: Iterator[dict], cycle: int,
                           office_code: str, support_oppose: str
                           ) -> list[IELink]:
    """Roll raw transactions up to (committee_id, candidate_id) totals."""
    buckets: dict[tuple, dict] = {}
    for r in records:
        cid = r.get("committee_id")
        cand_id = r.get("candidate_id")
        if not cid or not cand_id:
            continue
        amount = float(r.get("expenditure_amount") or 0)
        # Guard against the occasional bad filing (e.g. $1B typos)
        if amount > 500_000_000 or amount < 0:
            continue
        key = (cid, cand_id)
        b = buckets.get(key)
        if b is None:
            cm = r.get("committee") or {}
            b = {
                "committee_name": cm.get("name") or r.get("committee_name"),
                "candidate_name": r.get("candidate_name"),
                "state": r.get("candidate_office_state"),
                "district": r.get("candidate_office_district"),
                "total": 0.0,
                "count": 0,
            }
            buckets[key] = b
        b["total"] += amount
        b["count"] += 1
        # Fill in committee name if we didn't have it yet
        if not b["committee_name"]:
            cm = r.get("committee") or {}
            b["committee_name"] = cm.get("name") or r.get("committee_name")

    out: list[IELink] = []
    for (cid, cand_id), b in buckets.items():
        out.append(IELink(
            cycle=cycle,
            committee_id=cid,
            committee_name=b["committee_name"],
            candidate_id=cand_id,
            candidate_name=b["candidate_name"],
            office=office_code,
            state=b["state"],
            district=b["district"],
            support_oppose=support_oppose,
            total_spent=b["total"],
            transaction_count=b["count"],
        ))
    return out


def fetch_ie_for_office_state(cycle: int, office_code: str,
                               state: Optional[str]) -> Iterator[IELink]:
    """Pulls both S and O indicators, returns aggregated IELinks."""
    for so in ("S", "O"):
        records = _paginate_raw(cycle, office_code, state, so)
        yield from aggregate_client_side(records, cycle, office_code, so)


def fetch_all(cycle: int, states: list[str]) -> list[IELink]:
    """House + Senate across all target states, plus President (nationwide)."""
    out: list[IELink] = []
    for state in states:
        print(f"  {state}: senate…", end=" ", flush=True)
        sen = list(fetch_ie_for_office_state(cycle, "S", state))
        print(f"{len(sen)} committees; house…", end=" ", flush=True)
        hse = list(fetch_ie_for_office_state(cycle, "H", state))
        print(f"{len(hse)} committees")
        out.extend(sen)
        out.extend(hse)
    # Presidential IEs don't take a state
    print("  president (all)…", end=" ", flush=True)
    pres = list(fetch_ie_for_office_state(cycle, "P", None))
    print(f"{len(pres)} committees")
    out.extend(pres)
    return out


# ─── Persistence ─────────────────────────────────────────────────────────
IE_SCHEMA = """
CREATE TABLE IF NOT EXISTS fec_ie_by_committee (
    cycle INTEGER,
    committee_id TEXT,
    committee_name TEXT,
    candidate_id TEXT,
    candidate_name TEXT,
    office TEXT,
    state TEXT,
    district TEXT,
    support_oppose TEXT,
    total_spent REAL,
    transaction_count INTEGER,
    PRIMARY KEY (cycle, committee_id, candidate_id, support_oppose)
);
CREATE INDEX IF NOT EXISTS idx_ie_committee ON fec_ie_by_committee(committee_id);
CREATE INDEX IF NOT EXISTS idx_ie_name ON fec_ie_by_committee(committee_name);
CREATE INDEX IF NOT EXISTS idx_ie_candidate ON fec_ie_by_committee(candidate_id);
"""


def persist(links: list[IELink], db_path: Path) -> int:
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(IE_SCHEMA)
        rows = [(l.cycle, l.committee_id, l.committee_name, l.candidate_id,
                 l.candidate_name, l.office, l.state, l.district,
                 l.support_oppose, l.total_spent, l.transaction_count)
                for l in links if l.committee_id and l.candidate_id]
        conn.executemany(
            "INSERT OR REPLACE INTO fec_ie_by_committee "
            "(cycle, committee_id, committee_name, candidate_id, candidate_name, "
            " office, state, district, support_oppose, total_spent, transaction_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


# ─── Lookup API used by the ingest ───────────────────────────────────────
def committee_name_to_candidates(db_path: Path, cycle: int = 2026) -> dict:
    """Return a dict keyed by normalized committee name, whose values are a
    list of (candidate_id, candidate_name, support_oppose, total_spent).
    The ingest uses this to fuzzy-match advertiser names → committee →
    candidate(s) supported."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT committee_id, committee_name, candidate_id, candidate_name,
               office, state, district, support_oppose, total_spent
        FROM fec_ie_by_committee
        WHERE cycle = ? AND committee_name IS NOT NULL
        ORDER BY total_spent DESC
    """, (cycle,)).fetchall()
    conn.close()

    out: dict[str, list[dict]] = {}
    for r in rows:
        name_key = (r["committee_name"] or "").strip().upper()
        if not name_key:
            continue
        out.setdefault(name_key, []).append(dict(r))
    return out


def committees_primary_beneficiary(db_path: Path,
                                     cycle: int = 2026) -> dict[str, dict]:
    """For each committee, return the single candidate it spent the most
    *supporting* (not opposing). This is the canonical PAC→candidate map."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT committee_id, committee_name, candidate_id, candidate_name,
               office, state, district, total_spent
        FROM fec_ie_by_committee
        WHERE cycle = ? AND support_oppose = 'S'
        ORDER BY committee_id, total_spent DESC
    """, (cycle,)).fetchall()
    conn.close()

    by_committee: dict[str, dict] = {}
    for r in rows:
        cid = r["committee_id"]
        if cid not in by_committee:
            by_committee[cid] = dict(r)
    return by_committee


# ─── CLI ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", type=int, default=2026)
    ap.add_argument("--states", nargs="+", default=TARGET_STATES)
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    print(f"Fetching FEC Schedule E IE aggregations for cycle {args.cycle}")
    print(f"  states: {len(args.states)} | plus presidential IEs")
    print("-" * 72)
    links = fetch_all(args.cycle, args.states)
    print("-" * 72)
    # Summary before persisting
    print(f"Total links fetched: {len(links):,}")
    support = [l for l in links if l.support_oppose == "S"]
    oppose  = [l for l in links if l.support_oppose == "O"]
    print(f"  supporting: {len(support):,} (${sum(l.total_spent for l in support):,.0f})")
    print(f"  opposing:   {len(oppose):,} (${sum(l.total_spent for l in oppose):,.0f})")

    n = persist(links, Path(args.db))
    print(f"Wrote {n:,} rows to {args.db}::fec_ie_by_committee")

    # Show a teaser — top 10 committees by support spend
    print("\nTop 10 committees by support IE spend (2026):")
    by_comm: dict[str, float] = {}
    names: dict[str, str] = {}
    for l in support:
        by_comm[l.committee_id] = by_comm.get(l.committee_id, 0) + l.total_spent
        if l.committee_name and l.committee_id not in names:
            names[l.committee_id] = l.committee_name
    top = sorted(by_comm.items(), key=lambda kv: kv[1], reverse=True)[:10]
    for cid, total in top:
        print(f"  ${total:>14,.0f}  {cid}  {names.get(cid, '?')}")


if __name__ == "__main__":
    main()
