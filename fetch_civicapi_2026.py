"""
Fetch 2026 state-level races (and their candidates) from CivicAPI for a
fixed list of states, and write them into the analyzer's SQLite store using
the canonical schema.

Scope: Governor, Lt Governor, Attorney General, Secretary of State, Treasurer,
Comptroller/Auditor, State Senate, State House, plus US Senate and US House
(so the FEC-matched federal races are covered too). County/municipal races
(Commissioner, Sheriff, Clerk, Committeeperson) are filtered out.

Pagination: CivicAPI accepts limit + offset; we page 200 at a time until a
page comes back short.

Run:
    export CIVIC_API_KEY=...  # optional, CivicAPI is currently unauthenticated
    python fetch_civicapi_2026.py
    python fetch_civicapi_2026.py --states IL CA --dry-run   # preview only
    python fetch_civicapi_2026.py --include-primaries        # default: general only

The script is idempotent — re-running upserts by race_id and does not
duplicate rows. Raw JSON responses are cached under data/cache/civic/ so
re-runs don't re-hit the API for pages already fetched today.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

import requests

# Make the analyzer package importable regardless of CWD
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH
from data.schema import Candidate, OfficeType, Race, RaceResult
from data.store import Store


# ─── Config ──────────────────────────────────────────────────────────────
TARGET_STATES = [
    "CA", "TX", "GA", "IL", "VA", "MI", "NY", "LA", "NH", "SC",
    "FL", "PA", "NJ", "MD", "IN", "TN", "AR", "CO", "MN", "NM",
    "MS", "OR", "MA", "WA", "DC", "CT", "DE", "VT",
]

# CivicAPI `type` → our OfficeType enum. Anything not listed here is dropped.
# Keys are lowercased and stripped before matching.
OFFICE_MAP: dict[str, OfficeType] = {
    "us senate": OfficeType.US_SENATE,
    "senate": OfficeType.US_SENATE,               # sometimes just "Senate"
    "us house": OfficeType.US_HOUSE,
    "house":    OfficeType.US_HOUSE,              # ambiguous; resolved by state+district check below
    "house of representatives": OfficeType.US_HOUSE,
    "us representative": OfficeType.US_HOUSE,
    "united states representative": OfficeType.US_HOUSE,
    "governor": OfficeType.GOVERNOR,
    "attorney general": OfficeType.ATTORNEY_GENERAL,
    "state senate": OfficeType.STATE_SENATE,
    "state house": OfficeType.STATE_HOUSE,
    "state assembly": OfficeType.STATE_HOUSE,     # NY, CA, WI call it Assembly
    "state representative": OfficeType.STATE_HOUSE,
    "state delegate": OfficeType.STATE_HOUSE,     # VA, MD
    "house of delegates": OfficeType.STATE_HOUSE, # VA
    # Kept under OTHER so they land in the store if desired, but filtered by default
    "lieutenant governor": OfficeType.OTHER,
    "secretary of state": OfficeType.OTHER,
    "state treasurer": OfficeType.OTHER,
    "comptroller": OfficeType.OTHER,
    "auditor": OfficeType.OTHER,
}

DEFAULT_ALLOW_OFFICES = {
    OfficeType.US_SENATE, OfficeType.US_HOUSE,
    OfficeType.GOVERNOR, OfficeType.ATTORNEY_GENERAL,
    OfficeType.STATE_SENATE, OfficeType.STATE_HOUSE,
}

BASE_URL = "https://www.civicapi.org/api/v2"
PAGE_SIZE = 200
CACHE_DIR = ROOT / "data" / "cache" / "civic"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── HTTP with caching and polite rate limiting ──────────────────────────
class CivicClient:
    def __init__(self, base_url: str = BASE_URL, cache_ttl_hours: int = 12):
        self.base_url = base_url
        self.cache_ttl = cache_ttl_hours * 3600
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "broadcast-waste-analyzer/1.0"})

    def _cache_path(self, path: str, params: dict) -> Path:
        key = f"{path}?{json.dumps(params, sort_keys=True)}"
        h = hashlib.sha256(key.encode()).hexdigest()[:20]
        return CACHE_DIR / f"{h}.json"

    def get(self, path: str, params: dict) -> dict:
        cp = self._cache_path(path, params)
        if cp.exists() and (time.time() - cp.stat().st_mtime) < self.cache_ttl:
            return json.loads(cp.read_text())
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, timeout=30)
                if r.status_code == 503:
                    # CivicAPI occasionally 503s transiently
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                payload = r.json()
                cp.write_text(json.dumps(payload))
                time.sleep(0.25)  # polite
                return payload
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f"unreachable: {url}")

    def search_races(
        self,
        start_date: str,
        end_date: str,
        province: str,
        query: Optional[str] = None,
    ) -> Iterator[dict]:
        """Yield races one at a time, handling pagination transparently."""
        offset = 0
        seen_ids: set[int] = set()
        while True:
            params = {
                "startDate": start_date,
                "endDate": end_date,
                "province": province,
                "limit": PAGE_SIZE,
                "offset": offset,
            }
            if query:
                params["query"] = query
            data = self.get("/race/search", params)
            races = data.get("races", [])
            if not races:
                return
            page_ids = [int(r.get("id")) for r in races if r.get("id") is not None]
            if page_ids and set(page_ids).issubset(seen_ids):
                return
            for r in races:
                if r.get("id") in seen_ids:
                    continue
                seen_ids.add(int(r["id"]))
                yield r
            if len(races) < PAGE_SIZE:
                return
            offset += PAGE_SIZE


# ─── Parsing ─────────────────────────────────────────────────────────────
def classify_office(
    raw_type: str,
    province: str,
    district: Optional[str],
    election_name: Optional[str] = None,
) -> Optional[OfficeType]:
    """Map CivicAPI's free-form `type` field to our enum.
    Returns None for anything we don't want to ingest."""
    key = (raw_type or "").strip().lower()
    election_key = (election_name or "").strip().lower()
    combined = " ".join(part for part in [key, election_key] if part)

    # Prefer district patterns when they're explicit; they are more reliable
    # than CivicAPI's free-form `type` for state legislative / congressional races.
    if district:
        district_upper = district.upper()
        if re.search(r"\bHD\b", district_upper):
            return OfficeType.STATE_HOUSE
        if re.search(r"\bSD\b", district_upper):
            return OfficeType.STATE_SENATE
        if re.search(r"\bCD\b|^[A-Z]{2}-\d+$", district_upper):
            return OfficeType.US_HOUSE

    mapped = OFFICE_MAP.get(key)
    if mapped is not None:
        return mapped

    heuristic_patterns: list[tuple[str, OfficeType]] = [
        (r"\bhouse of representatives\b|\bunited states representative\b|\bus house\b", OfficeType.US_HOUSE),
        (r"\bunited states senator\b|\bus senate\b", OfficeType.US_SENATE),
        (r"\bstate senate\b|\bstate senator\b", OfficeType.STATE_SENATE),
        (r"\bstate house\b|\bstate representative\b|\bstate assembly\b|\bhouse of delegates\b|\bstate delegate\b", OfficeType.STATE_HOUSE),
        (r"\bgovernor\b", OfficeType.GOVERNOR),
        (r"\battorney general\b", OfficeType.ATTORNEY_GENERAL),
    ]
    for pattern, office in heuristic_patterns:
        if re.search(pattern, combined):
            return office

    # Disambiguate bare "House" / "Senate" using the district field.
    # US House districts look like "IL-07" or "IL-CD-07"; state house districts
    # look like "IL-HD-104". If the raw type is just "House" or "Senate" we
    # assume US-level only when district matches the CD pattern.
    if key in {"house", "senate"} and district:
        if re.search(r"\bCD\b|^[A-Z]{2}-\d+$", district.upper()):
            return OfficeType.US_HOUSE if "house" in key else OfficeType.US_SENATE
        if re.search(r"\bHD\b|\bSD\b", district.upper()):
            return OfficeType.STATE_HOUSE if "HD" in district.upper() else OfficeType.STATE_SENATE
    return None


DISTRICT_NUMBER_RE = re.compile(r"(\d+)\s*$")


def normalize_district(raw: Optional[str], office: OfficeType, election_name: Optional[str] = None) -> Optional[str]:
    """Extract a canonical district string.
    US House: zero-padded 2-digit ('07'). State leg: whatever trailing number
    CivicAPI provides, zero-padded to 3 digits for stability ('104')."""
    if not raw or office in {OfficeType.US_SENATE, OfficeType.GOVERNOR,
                             OfficeType.ATTORNEY_GENERAL}:
        raw_to_use = election_name or raw
    else:
        raw_to_use = raw
    if not raw_to_use or office in {OfficeType.US_SENATE, OfficeType.GOVERNOR, OfficeType.ATTORNEY_GENERAL}:
        return None
    m = DISTRICT_NUMBER_RE.search(raw_to_use)
    if not m and election_name:
        m = re.search(r"\bdistrict\s+(\d+)\b", election_name, re.I)
    if not m:
        return None
    num = m.group(1)
    if office == OfficeType.US_HOUSE:
        return num.zfill(2)
    return num.zfill(3)


def parse_election_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def make_race_id(cycle: int, office: OfficeType, state: str,
                 district: Optional[str], election_type: str,
                 civic_id: int) -> str:
    """Stable identifier. We include election_type because Primary and
    General for the same seat need to be stored as distinct races.
    Also include the CivicAPI id as a final disambiguator — some states
    have multiple primary dates (e.g. runoffs) for the same seat."""
    parts = [str(cycle), office.value, state.upper()]
    if district:
        parts.append(district)
    parts.append(election_type.lower().replace(" ", "_"))
    parts.append(f"civic{civic_id}")
    return "-".join(parts)


def to_schema(race_json: dict, *, allow_offices: set[OfficeType],
              include_primaries: bool) -> Optional[tuple[Race, list[Candidate]]]:
    """Transform a CivicAPI race payload into (Race, [Candidate]).
    Returns None if the race should be skipped."""
    civic_id = race_json.get("id")
    if civic_id is None:
        return None
    raw_type = race_json.get("type", "")
    province = race_json.get("province", "")
    district_raw = race_json.get("district")
    election_type = race_json.get("election_type") or "General"

    if not include_primaries and election_type.lower() != "general":
        return None

    office = classify_office(raw_type, province, district_raw, race_json.get("election_name"))
    if office is None or office not in allow_offices:
        return None

    election_date = parse_election_date(race_json.get("election_date"))
    cycle = election_date.year if election_date else 2026

    district = normalize_district(district_raw, office, race_json.get("election_name"))
    race_id = make_race_id(cycle, office, province, district,
                           election_type, int(civic_id))

    race = Race(
        race_id=race_id,
        cycle=cycle,
        office=office,
        state=province.upper(),
        district=district,
        general_date=election_date,
        election_name=race_json.get("election_name"),
        election_scope=race_json.get("election_scope"),
        election_type=election_type,
    )

    cands_out: list[Candidate] = []
    raw_candidates = race_json.get("candidates", []) or []

    # Winner flag + vote totals. CivicAPI sometimes reports percent out of 100,
    # sometimes 0–1. Normalize to 0–1 and compute margin ourselves.
    total_votes = sum((c.get("votes") or 0) for c in raw_candidates)
    sorted_rows = sorted(raw_candidates,
                         key=lambda c: c.get("votes") or 0, reverse=True)
    winner_votes = sorted_rows[0].get("votes", 0) if sorted_rows else 0
    runner_up_votes = sorted_rows[1].get("votes", 0) if len(sorted_rows) > 1 else 0

    for c in raw_candidates:
        name = (c.get("name") or "").strip()
        if not name or name.lower() in {"write-in", "write in", "other"}:
            # Skip placeholders — they pollute match rates downstream
            continue
        votes = c.get("votes") or 0
        share = (votes / total_votes) if total_votes else None
        # Flag winners explicitly. Honor CivicAPI's `winner` if present and true;
        # otherwise infer from vote totals (only when votes have actually landed).
        is_winner = bool(c.get("winner")) or (
            votes > 0 and votes == winner_votes
        )
        if total_votes == 0:
            result = RaceResult.PENDING
        else:
            result = RaceResult.WON if is_winner else RaceResult.LOST

        margin: Optional[float] = None
        if is_winner and total_votes:
            margin = (votes - runner_up_votes) / total_votes
        elif votes and total_votes:
            margin = (votes - winner_votes) / total_votes  # negative for losers

        # Synthesize a stable candidate_id. FEC IDs aren't available via
        # CivicAPI, so we build one from (race_id, normalized name).
        name_key = re.sub(r"[^a-z0-9]+", "_",
                          name.lower()).strip("_")[:40]
        cand_id = f"civic-{civic_id}-{name_key}"

        cands_out.append(Candidate(
            candidate_id=cand_id,
            full_name=name,
            party=c.get("party"),
            race_id=race_id,
            incumbent=bool(c.get("incumbent", False)),
            result=result,
            votes_received=votes or None,
            vote_share=share,
            margin=margin,
        ))

    return race, cands_out


# ─── Orchestration ───────────────────────────────────────────────────────
def fetch_state(client: CivicClient, state: str, year: int,
                 include_primaries: bool,
                 allow_offices: set[OfficeType]) -> tuple[list[Race], list[Candidate], dict]:
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    races: list[Race] = []
    cands: list[Candidate] = []
    stats = {"raw_total": 0, "kept": 0, "dropped_office": 0,
             "dropped_primary": 0, "no_candidates": 0}

    for raw in client.search_races(start, end, state):
        stats["raw_total"] += 1
        result = to_schema(raw, allow_offices=allow_offices,
                            include_primaries=include_primaries)
        if result is None:
            # distinguish the drop reason for logging
            et = (raw.get("election_type") or "").lower()
            if not include_primaries and et and et != "general":
                stats["dropped_primary"] += 1
            else:
                stats["dropped_office"] += 1
            continue
        race, race_cands = result
        if not race_cands:
            stats["no_candidates"] += 1
            continue
        races.append(race)
        cands.extend(race_cands)
        stats["kept"] += 1
    return races, cands, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", nargs="+", default=TARGET_STATES,
                        help="Two-letter state codes. Defaults to the 28-state list.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--include-primaries", action="store_true",
                        help="Include primary elections (default: general only).")
    parser.add_argument("--include-all-offices", action="store_true",
                        help="Include Lt Gov, SoS, Treasurer, etc. "
                             "(default: federal + gov/AG + state leg only).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse but don't write to SQLite.")
    parser.add_argument("--db", default=str(DB_PATH),
                        help=f"SQLite path (default: {DB_PATH})")
    args = parser.parse_args()

    allow_offices = set(OFFICE_MAP.values()) if args.include_all_offices \
        else DEFAULT_ALLOW_OFFICES

    client = CivicClient()
    store = None if args.dry_run else Store(Path(args.db))

    grand = {"states": 0, "races": 0, "candidates": 0}
    per_state_stats: list[tuple[str, dict]] = []

    print(f"\nFetching {args.year} races from CivicAPI for {len(args.states)} states")
    print(f"Scope: {'general+primary' if args.include_primaries else 'general only'}, "
          f"offices={'all' if args.include_all_offices else 'fed+gov+AG+state leg'}")
    if args.dry_run:
        print("*** DRY RUN — nothing will be written to SQLite ***")
    print("-" * 72)

    for state in args.states:
        try:
            races, cands, stats = fetch_state(
                client, state, args.year,
                include_primaries=args.include_primaries,
                allow_offices=allow_offices,
            )
        except Exception as e:
            print(f"  {state}: ERROR {e}")
            continue

        per_state_stats.append((state, stats))
        if not args.dry_run and races:
            store.upsert_races(races)
            store.upsert_candidates(cands)
        grand["states"] += 1
        grand["races"] += len(races)
        grand["candidates"] += len(cands)
        print(f"  {state}: kept {stats['kept']:>4} / {stats['raw_total']:>4} races "
              f"({len(cands):>5} candidates) "
              f"[dropped: {stats['dropped_office']} off-scope offices, "
              f"{stats['dropped_primary']} primaries, "
              f"{stats['no_candidates']} w/o candidates]")

    print("-" * 72)
    print(f"Totals: {grand['states']} states, "
          f"{grand['races']} races, {grand['candidates']} candidates")
    if args.dry_run:
        print("(dry run — database not modified)")
    else:
        print(f"Written to {args.db}")


if __name__ == "__main__":
    main()
