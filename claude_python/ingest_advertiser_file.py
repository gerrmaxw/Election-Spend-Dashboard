"""Load the 'Home_Advertiser' political spend export into the analyzer.

The file groups spend by advertiser committee name (e.g. 'Talarico for TX Senate',
'Steyer for CA Governor') with columns for Broadcast, Cable, CTV, Digital, Radio.
Each advertiser spans multiple rows — a name row followed by up to 4 data rows —
so the first job is flattening those into one row per advertiser.

The second job is parsing the committee name into (candidate_surname, state,
office), then resolving against CivicAPI candidates we've already loaded into
SQLite. PACs that don't match a candidate committee pattern go into a
separate 'pac_spend' bucket.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Load .env (simple KEY=VALUE format) so FEC_API_KEY is picked up without
# the user having to export it each session.
_env_file = ROOT.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from config.settings import DB_PATH
from data.schema import MediaType, OfficeType, SpendRecord
from data.store import Store


# ─── Committee-name parsing ──────────────────────────────────────────────
# Two dominant patterns:
#   "<Surname> for <STATE> <Office>"           → direct committee
#   "<Surname> for <Office>"                   → direct committee, state inferred
#   "<anything> PAC" / "Citizens for X" / ...  → PAC/outside group

US_STATES = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
    "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS",
    "kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD","massachusetts":"MA",
    "michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT",
    "nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ",
    "new mexico":"NM","new york":"NY","north carolina":"NC","north dakota":"ND",
    "ohio":"OH","oklahoma":"OK","oregon":"OR","pennsylvania":"PA","rhode island":"RI",
    "south carolina":"SC","south dakota":"SD","tennessee":"TN","texas":"TX",
    "utah":"UT","vermont":"VT","virginia":"VA","washington":"WA","west virginia":"WV",
    "wisconsin":"WI","wyoming":"WY",
}
STATE_CODES = set(US_STATES.values())
STATE_NAMES_BY_CODE = {v: k for k, v in US_STATES.items()}

OFFICE_PATTERNS: list[tuple[re.Pattern, OfficeType]] = [
    (re.compile(r"\bu\.?s\.?\s*senate\b|\bus senate\b", re.I), OfficeType.US_SENATE),
    (re.compile(r"\bu\.?s\.?\s*(house|representative|congress)\b", re.I), OfficeType.US_HOUSE),
    (re.compile(r"\bcongress(?:ional|man|woman)?\b", re.I), OfficeType.US_HOUSE),
    (re.compile(r"\bCD[-\s]*\d+\b", re.I), OfficeType.US_HOUSE),
    (re.compile(r"\bsenate\b", re.I), OfficeType.US_SENATE),
    (re.compile(r"\bgovernor\b", re.I), OfficeType.GOVERNOR),
    (re.compile(r"\battorney\s+general\b|\b\s*ag\s*$", re.I), OfficeType.ATTORNEY_GENERAL),
    (re.compile(r"\bstate\s+(house|assembly|representative|delegate)\b", re.I), OfficeType.STATE_HOUSE),
    (re.compile(r"\bstate\s+senate\b", re.I), OfficeType.STATE_SENATE),
]

SUPPORTING_PAC_RE = re.compile(
    r"\b(?:in\s+)?(?:support(?:ing)?\s+of|for)\s+(?P<who>[A-Z][\w\-']+(?:\s+[A-Z][\w\-']+){0,2})\s+for\s+(?P<rest>.+)$",
    re.I,
)

PAC_MARKERS = re.compile(
    r"\b(pac|fund|project|committee|citizens|coalition|action|alliance|"
    r"majority|minority|values|voice|forward|future|together|integrity|freedom|"
    r"prosperity|victory|defense|patriots|progress|fair|no on|yes on|"
    r"keep|save|stop|protect)\b",
    re.I,
)


@dataclass
class ParsedAdvertiser:
    raw_name: str
    is_candidate_committee: bool
    surname: Optional[str] = None
    state: Optional[str] = None
    office: Optional[OfficeType] = None
    notes: str = ""


def parse_advertiser(name: str) -> ParsedAdvertiser:
    """Best-effort parse. Committees are the priority; PACs flagged for manual
    lookup so we can still include their spend in aggregate analyses."""
    n = name.strip()
    pa = ParsedAdvertiser(raw_name=n, is_candidate_committee=False)

    # Try the canonical candidate-committee pattern first
    # "<name> for <optional state> <office>"
    m = re.match(r"^(?P<who>[A-Z][\w\-'\.]+(?:\s+[A-Z][\w\-'\.]+){0,2})\s+for\s+(?P<rest>.+)$", n)
    if m:
        who = m.group("who").strip()
        rest = m.group("rest").strip()

        # Find state code or name in the rest
        state: Optional[str] = None
        # Two-letter code
        sm = re.search(r"\b([A-Z]{2})\b", rest)
        if sm and sm.group(1) in STATE_CODES:
            state = sm.group(1)
        else:
            # Full state name
            for sname, sabbr in US_STATES.items():
                if re.search(rf"\b{re.escape(sname)}\b", rest, re.I):
                    state = sabbr
                    break

        # Find office
        office: Optional[OfficeType] = None
        for pat, off in OFFICE_PATTERNS:
            if pat.search(rest):
                office = off
                break

        if office is not None:
            pa.is_candidate_committee = True
            pa.surname = who.split()[-1]  # last token tends to be surname
            pa.state = state
            pa.office = office
            return pa
        # Matched "X for Y" but couldn't identify an office — probably a slogan PAC
        pa.notes = f"unmatched 'for' structure: rest={rest!r}"

    # PAC / outside group
    if PAC_MARKERS.search(n):
        pa.notes = "pac_or_outside_group"
    else:
        pa.notes = "unparseable"
    return pa


# ─── File loader ─────────────────────────────────────────────────────────
MEDIA_COLUMNS = {
    "broadcast": MediaType.BROADCAST,
    "cable":     MediaType.CABLE,
    "ctv":       MediaType.CTV,
    "digital":   MediaType.DIGITAL,
    "radio":     MediaType.RADIO,
}


def flatten_advertiser_file(path: Path) -> pd.DataFrame:
    """Read an advertiser-spend xlsx and return a normalized DataFrame.

    Supports two formats:

    1. **Legacy** (Home_Advertiser export): no header row beyond columns
       advertiser/broadcast/cable/ctv/digital/radio/grand_total. Multi-row
       per advertiser; the first row holds the name, subsequent rows hold
       sub-categories. We forward-fill and group by advertiser.

    2. **House Spend 4.27+** layout: explicit header row with columns
       Advertiser, State, District, Party, Agency, Broadcast, Cable, CTV,
       Digital, Radio, Total. One row per advertiser×agency. State/District
       enable a fast direct-resolution path.

    Returned columns always include: `advertiser`, `broadcast`, `cable`,
    `ctv`, `digital`, `radio`, `total`. New-format files additionally carry
    `state` (2-letter), `district` (digits as string), `party`, `agency`.
    """
    raw = pd.read_excel(path)
    cols = [str(c).strip().lower() for c in raw.columns]

    # Detect new format by header presence
    new_format = ("state" in cols) and ("district" in cols) and ("agency" in cols)

    if new_format:
        df = raw.copy()
        df.columns = [str(c).strip().lower() for c in df.columns]

        # State name → 2-letter code
        def _state_code(v) -> Optional[str]:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            s = str(v).strip()
            if not s or s.lower() == "national":
                return None
            if len(s) == 2 and s.upper() in STATE_CODES:
                return s.upper()
            return US_STATES.get(s.lower())

        def _district(v) -> Optional[str]:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            s = str(v).strip()
            if not s or s.lower() in ("nan", "national"):
                return None
            # Strip leading zeros: "06" → "6", "AL" stays "AL"
            return s.lstrip("0") or s

        # Preserve raw state value before normalization so we can flag
        # National PAC rows separately even when state_code → None.
        df["state_raw"] = df["state"].astype("string").str.strip()
        df["is_national"] = df["state_raw"].str.lower().eq("national").astype(int)

        df["state"]    = df["state"].apply(_state_code)
        df["district"] = df["district"].apply(_district)
        df["party"]    = df["party"].astype("string").str.strip()
        df["agency"]   = df["agency"].astype("string").str.strip()
        df["advertiser"] = df["advertiser"].astype("string").str.strip()

        for col in ["broadcast", "cable", "ctv", "digital", "radio"]:
            df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0)
        df["total"] = df[["broadcast", "cable", "ctv", "digital", "radio"]].sum(axis=1)

        # Drop rows with no advertiser or no spend
        df = df.dropna(subset=["advertiser"])
        df = df[df["total"] > 0]

        return df[[
            "advertiser", "state", "district", "party", "agency",
            "broadcast", "cable", "ctv", "digital", "radio", "total",
            "state_raw", "is_national",
        ]].reset_index(drop=True)

    # ── Legacy format ──────────────────────────────────────────────────
    df = raw.copy()
    df.columns = ["advertiser", "broadcast", "cable", "ctv", "digital",
                  "radio", "grand_total"]
    df = df.iloc[1:].copy()
    df["advertiser"] = df["advertiser"].ffill()
    df = df.dropna(subset=["advertiser"])
    for col in ["broadcast", "cable", "ctv", "digital", "radio"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    grouped = df.groupby("advertiser", as_index=False).agg({
        "broadcast": "sum", "cable": "sum", "ctv": "sum",
        "digital": "sum", "radio": "sum",
    })
    grouped["total"] = grouped[["broadcast", "cable", "ctv",
                                 "digital", "radio"]].sum(axis=1)
    grouped["state"] = None
    grouped["district"] = None
    grouped["party"] = None
    grouped["agency"] = None
    grouped["state_raw"] = None
    grouped["is_national"] = 0
    return grouped.sort_values("total", ascending=False).reset_index(drop=True)


# ─── Resolver: advertiser → candidate_id ─────────────────────────────────
@dataclass
class ResolvedAdvertiser:
    raw_name: str
    candidate_id: Optional[str]
    race_id: Optional[str]
    confidence: float
    match_reason: str
    parsed: ParsedAdvertiser = field(default=None)  # type: ignore


def _candidate_index(store: Store) -> pd.DataFrame:
    """One-stop DataFrame of all candidates + their race metadata."""
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT c.candidate_id, c.full_name, c.party, c.race_id,
               r.state, r.office, r.district, r.general_date
        FROM candidates c JOIN races r ON r.race_id = c.race_id
    """).fetchall()
    conn.close()
    return pd.DataFrame([dict(r) for r in rows])


def resolve_advertiser(parsed: ParsedAdvertiser,
                        cand_ix: pd.DataFrame) -> ResolvedAdvertiser:
    """Match a parsed committee against the candidate index.
    Requires state + office + surname to align."""
    if not parsed.is_candidate_committee or not parsed.state or not parsed.office:
        return ResolvedAdvertiser(parsed.raw_name, None, None, 0.0,
                                  "not a candidate committee", parsed)

    pool = cand_ix[
        (cand_ix["state"] == parsed.state)
        & (cand_ix["office"] == parsed.office.value)
    ]
    if pool.empty:
        return ResolvedAdvertiser(parsed.raw_name, None, None, 0.0,
                                  f"no candidates in {parsed.state} "
                                  f"{parsed.office.value}", parsed)

    # Match surname against full_name, tolerant to middle initials / punctuation.
    surname_low = parsed.surname.lower() if parsed.surname else ""
    best = None
    best_score = 0.0
    for _, row in pool.iterrows():
        full = str(row["full_name"]).lower()
        score = fuzz.partial_ratio(surname_low, full)
        if score > best_score:
            best_score = score
            best = row

    if best is None or best_score < 85:
        return ResolvedAdvertiser(parsed.raw_name, None, None, best_score,
                                  "no surname match above threshold", parsed)

    # Prefer the General if present, otherwise the primary with the most votes.
    # A candidate can appear in both a primary and a general for the same seat;
    # we pick the most recent race with reported votes.
    race_pool = cand_ix[
        (cand_ix["state"] == parsed.state)
        & (cand_ix["office"] == parsed.office.value)
        & (cand_ix["full_name"].str.lower() == str(best["full_name"]).lower())
    ]
    # Pick the candidate_id + race_id with the highest votes (most "real")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    votes_map = {
        r["candidate_id"]: r["votes_received"] or 0
        for r in conn.execute("SELECT candidate_id, votes_received FROM candidates")
    }
    conn.close()
    race_pool = race_pool.assign(
        v=lambda d: d["candidate_id"].map(votes_map).fillna(0)
    ).sort_values("v", ascending=False)
    top = race_pool.iloc[0]

    return ResolvedAdvertiser(
        raw_name=parsed.raw_name,
        candidate_id=top["candidate_id"],
        race_id=top["race_id"],
        confidence=best_score / 100.0,
        match_reason=f"matched {top['full_name']} ({top['state']} "
                     f"{top['office']}, votes={int(top.get('v', 0)):,})",
        parsed=parsed,
    )


# ─── FEC-based PAC resolver ──────────────────────────────────────────────
# Second-pass resolver that only runs for advertisers the primary parser
# couldn't resolve (i.e. PACs and outside groups). Uses the FEC Schedule E
# committee→candidate map built by fetch_fec_ie.py.

def _normalize_committee_name(name: str) -> str:
    """Strip punctuation, expand common abbreviations, collapse whitespace.
    FEC filings capitalize everything and inconsistently use periods."""
    s = name.upper()
    s = re.sub(r"['\"`]", "", s)
    s = re.sub(r"[^\w\s&]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_fec_lookup(db_path: Path, cycle: int = 2026) -> dict:
    """Build a lookup keyed by normalized committee name whose values are the
    top supporting (candidate_id, candidate_name, state, office, district,
    total_spent) tuple — i.e. the candidate this committee spent the most
    money *for* (not against) in the cycle. Committees that only oppose are
    skipped because we can't confidently map them to a single advertiser."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT committee_id, committee_name, candidate_id, candidate_name,
               office, state, district, total_spent
        FROM fec_ie_by_committee
        WHERE cycle = ? AND support_oppose = 'S'
          AND committee_name IS NOT NULL
        ORDER BY committee_id, total_spent DESC
    """, (cycle,)).fetchall()
    conn.close()

    # For each committee, first (highest-spend) row wins
    by_cid: dict[str, dict] = {}
    for r in rows:
        if r["committee_id"] not in by_cid:
            by_cid[r["committee_id"]] = dict(r)

    # Key by normalized name
    by_name: dict[str, dict] = {}
    for d in by_cid.values():
        key = _normalize_committee_name(d["committee_name"] or "")
        if not key:
            continue
        # On duplicate normalized names, keep the one with higher spend
        existing = by_name.get(key)
        if existing is None or d["total_spent"] > existing["total_spent"]:
            by_name[key] = d
    return by_name


def resolve_via_fec(advertiser_name: str, fec_lookup: dict,
                     cand_ix: pd.DataFrame,
                     threshold: int = 88) -> Optional[ResolvedAdvertiser]:
    """Fuzzy-match the advertiser name against FEC committee names. If a
    match lands, pull that committee's top supported candidate, then find
    the matching row in our CivicAPI candidate index.

    Returns None if no confident match is found — caller falls back to
    leaving the advertiser unresolved."""
    norm = _normalize_committee_name(advertiser_name)
    if not norm or not fec_lookup:
        return None

    result = process.extractOne(norm, list(fec_lookup.keys()),
                                 scorer=fuzz.WRatio, score_cutoff=threshold)
    if result is None:
        return None
    match_name, score, _ = result
    fec_row = fec_lookup[match_name]

    # Try to find the matching candidate in our CivicAPI-backed candidate
    # index. We match on FEC candidate surname + state + office.
    fec_cand_name = (fec_row.get("candidate_name") or "").upper()
    # FEC names are "LAST, FIRST MIDDLE" — extract surname
    surname = fec_cand_name.split(",")[0].strip().lower() if "," in fec_cand_name \
        else fec_cand_name.split()[-1].lower() if fec_cand_name else ""
    state = fec_row.get("state")
    fec_office = fec_row.get("office")  # "H", "S", or "P"
    office_map = {"H": "us_house", "S": "us_senate", "P": None}
    office_val = office_map.get(fec_office)

    if not (surname and state and office_val):
        # Still emit an "attributed" result even if we can't join to CivicAPI
        pa = ParsedAdvertiser(raw_name=advertiser_name,
                              is_candidate_committee=False,
                              notes=f"FEC→{fec_row.get('candidate_name')} (unjoined)")
        return ResolvedAdvertiser(
            raw_name=advertiser_name,
            candidate_id=None,
            race_id=None,
            confidence=score / 100.0,
            match_reason=f"FEC IE match to {match_name} → "
                         f"{fec_row.get('candidate_name')} ({state}, {fec_office}); "
                         f"no CivicAPI row to join",
            parsed=pa,
        )

    pool = cand_ix[
        (cand_ix["state"] == state)
        & (cand_ix["office"] == office_val)
    ]
    if pool.empty:
        pa = ParsedAdvertiser(raw_name=advertiser_name,
                              is_candidate_committee=False,
                              notes=f"FEC→{fec_row.get('candidate_name')} (no CivicAPI pool)")
        return ResolvedAdvertiser(
            raw_name=advertiser_name,
            candidate_id=None,
            race_id=None,
            confidence=score / 100.0,
            match_reason=f"FEC IE match to {match_name} → "
                         f"{fec_row.get('candidate_name')} but no {state} "
                         f"{office_val} candidates loaded",
            parsed=pa,
        )

    best = None
    best_score = 0.0
    for _, r in pool.iterrows():
        full = str(r["full_name"]).lower()
        s = fuzz.partial_ratio(surname, full)
        if s > best_score:
            best_score = s
            best = r
    if best is None or best_score < 85:
        pa = ParsedAdvertiser(raw_name=advertiser_name,
                              is_candidate_committee=False,
                              notes=f"FEC→{fec_row.get('candidate_name')} (no surname match in CivicAPI)")
        return ResolvedAdvertiser(
            raw_name=advertiser_name,
            candidate_id=None,
            race_id=None,
            confidence=score / 100.0,
            match_reason=f"FEC IE match to {match_name} → "
                         f"{fec_row.get('candidate_name')} but surname not in {state} pool",
            parsed=pa,
        )

    # Prefer the race with the most reported votes
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    votes_map = {
        r["candidate_id"]: r["votes_received"] or 0
        for r in conn.execute("SELECT candidate_id, votes_received FROM candidates")
    }
    conn.close()

    race_pool = pool[pool["full_name"].str.lower() == str(best["full_name"]).lower()]
    race_pool = race_pool.assign(
        v=lambda d: d["candidate_id"].map(votes_map).fillna(0)
    ).sort_values("v", ascending=False)
    top = race_pool.iloc[0]

    pa = ParsedAdvertiser(raw_name=advertiser_name,
                          is_candidate_committee=False,
                          notes="pac_resolved_via_fec_ie")
    return ResolvedAdvertiser(
        raw_name=advertiser_name,
        candidate_id=top["candidate_id"],
        race_id=top["race_id"],
        confidence=(score / 100.0) * 0.9,   # discount slightly — indirect match
        match_reason=f"FEC IE: '{advertiser_name}' → {match_name} → "
                     f"{top['full_name']} ({top['state']} {top['office']}, "
                     f"IE spend=${fec_row['total_spent']:,.0f})",
        parsed=pa,
    )


# ─── openFEC /candidates/search fallback ─────────────────────────────────
# Third-tier resolver. Runs when direct match + FEC IE both miss. Hits the
# openFEC candidates endpoint to see if FEC has a candidate filing that
# matches the parsed advertiser, then joins back to our CivicAPI pool.

FEC_CANDIDATES_URL = "https://api.open.fec.gov/v1/candidates/search/"
_FEC_OFFICE_FOR_API = {
    OfficeType.US_HOUSE: "H",
    OfficeType.US_SENATE: "S",
}
_FEC_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS fec_candidate_cache (
    cache_key  TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def _fec_cache_get(conn: sqlite3.Connection, key: str) -> Optional[list]:
    row = conn.execute(
        "SELECT response FROM fec_candidate_cache WHERE cache_key=?", (key,)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _fec_cache_put(conn: sqlite3.Connection, key: str, results: list) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fec_candidate_cache (cache_key, response, fetched_at) "
        "VALUES (?, ?, ?)",
        (key, json.dumps(results), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _fec_candidates_search(surname: str, state: str, office_api: str,
                             cycle: int = 2026) -> list[dict]:
    """Call openFEC /candidates/search. Results cached in sqlite. Retries
    on 429 with exponential backoff. Caches empty results for 422/4xx so
    bad queries aren't retried on re-runs."""
    if len(surname) < 3:
        return []  # FEC rejects 1-2 char queries with 422

    api_key = os.environ.get("FEC_API_KEY", "DEMO_KEY")
    cache_key = f"{cycle}|{state}|{office_api}|{surname.lower()}"

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_FEC_CACHE_SCHEMA)
    cached = _fec_cache_get(conn, cache_key)
    if cached is not None:
        conn.close()
        return cached

    params = {
        "q": surname,
        "state": state,
        "office": office_api,
        "cycle": cycle,
        "per_page": 20,
        "api_key": api_key,
    }
    url = FEC_CANDIDATES_URL + "?" + urllib.parse.urlencode(params)

    results: list = []
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", []) or []
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                wait = 5 * (2 ** attempt)  # 5, 10, 20s
                print(f"  openFEC 429 for {surname}/{state}/{office_api}; sleeping {wait}s")
                time.sleep(wait)
                continue
            if e.code in (400, 422):
                # Bad query — cache empty so we skip it next time
                break
            print(f"  openFEC error for {surname}/{state}/{office_api}: {e}")
            conn.close()
            return []
        except Exception as e:
            print(f"  openFEC request failed for {surname}/{state}/{office_api}: {e}")
            conn.close()
            return []

    _fec_cache_put(conn, cache_key, results)
    conn.close()
    # Gentle throttle to stay well under 1000/hr with headroom
    time.sleep(0.15)
    return results


def resolve_via_fec_candidates(parsed: ParsedAdvertiser,
                                 cand_ix: pd.DataFrame,
                                 threshold: int = 85) -> Optional[ResolvedAdvertiser]:
    """If we have a parsed (surname, state, office), ask openFEC whether a
    candidate by that surname has filed in that seat for 2026. Join back to
    CivicAPI if possible; otherwise return attributed metadata."""
    if not parsed.surname or not parsed.state or not parsed.office:
        return None
    office_api = _FEC_OFFICE_FOR_API.get(parsed.office)
    if not office_api:
        return None  # FEC /candidates only covers federal seats

    results = _fec_candidates_search(parsed.surname, parsed.state, office_api)
    if not results:
        return None

    # Best FEC candidate by fuzzy surname match against FEC "name" field
    surname_low = parsed.surname.lower()
    best = None
    best_score = 0.0
    for r in results:
        fec_name = (r.get("name") or "").lower()
        s = fuzz.partial_ratio(surname_low, fec_name)
        if s > best_score:
            best_score = s
            best = r
    if best is None or best_score < threshold:
        return None

    fec_name = best.get("name", "")
    district = best.get("district") or None

    # Try to join to CivicAPI
    pool = cand_ix[
        (cand_ix["state"] == parsed.state)
        & (cand_ix["office"] == parsed.office.value)
    ]
    joined = None
    join_score = 0.0
    if not pool.empty:
        for _, row in pool.iterrows():
            full = str(row["full_name"]).lower()
            s = fuzz.partial_ratio(surname_low, full)
            if s > join_score:
                join_score = s
                joined = row

    pa = ParsedAdvertiser(
        raw_name=parsed.raw_name,
        is_candidate_committee=parsed.is_candidate_committee,
        surname=parsed.surname, state=parsed.state, office=parsed.office,
        notes=f"openFEC→{fec_name}",
    )

    if joined is not None and join_score >= 85:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        votes_map = {
            r["candidate_id"]: r["votes_received"] or 0
            for r in conn.execute("SELECT candidate_id, votes_received FROM candidates")
        }
        conn.close()
        race_pool = pool[pool["full_name"].str.lower() == str(joined["full_name"]).lower()]
        race_pool = race_pool.assign(
            v=lambda d: d["candidate_id"].map(votes_map).fillna(0)
        ).sort_values("v", ascending=False)
        top = race_pool.iloc[0]
        return ResolvedAdvertiser(
            raw_name=parsed.raw_name,
            candidate_id=top["candidate_id"],
            race_id=top["race_id"],
            confidence=(best_score / 100.0) * 0.9,
            match_reason=f"openFEC candidate: '{parsed.raw_name}' → {fec_name} "
                         f"({parsed.state}-{district or '??'}) → {top['full_name']}",
            parsed=pa,
        )

    # FEC found a candidate but we can't join CivicAPI — attribute only
    return ResolvedAdvertiser(
        raw_name=parsed.raw_name,
        candidate_id=None,
        race_id=None,
        confidence=best_score / 100.0,
        match_reason=f"openFEC candidate match: {fec_name} "
                     f"({parsed.state}-{district or '??'}, {office_api}); "
                     f"no CivicAPI row to join",
        parsed=pa,
    )


# ─── Spend-record generation ─────────────────────────────────────────────
def _match_source_for(resolved: ResolvedAdvertiser) -> str:
    reason = resolved.match_reason or ""
    if resolved.candidate_id and resolved.race_id and resolved.race_id != "unresolved":
        if "openFEC candidate" in reason:
            return "fec_candidates"
        if "FEC IE" in reason:
            return "fec_ie"
        return "direct"
    return "unmatched"


def resolve_via_explicit_metadata(advertiser_row: pd.Series,
                                    cand_ix: pd.DataFrame) -> Optional[ResolvedAdvertiser]:
    """Tier 0 resolver — uses explicit State/District columns from the new
    spend file format. When an advertiser row has both state and district
    populated, we know exactly which race the spend belongs to. We just
    need to pick the right candidate within that race by surname.

    Returns None when explicit metadata is unavailable; caller falls
    through to the parsing tiers.
    """
    state = advertiser_row.get("state")
    district = advertiser_row.get("district")
    advertiser = str(advertiser_row.get("advertiser") or "").strip()
    if not state or not district or not advertiser:
        return None

    # Pool of candidates running in this exact district
    pool = cand_ix[
        (cand_ix["state"] == state)
        & (cand_ix["office"] == OfficeType.US_HOUSE.value)
        & (cand_ix["district"].astype(str) == str(district))
    ]
    if pool.empty:
        # No candidate roster for this seat yet — preserve metadata so the
        # spend row still carries district info for later analysis.
        pa = ParsedAdvertiser(
            raw_name=advertiser,
            is_candidate_committee=True,
            state=state, office=OfficeType.US_HOUSE,
            notes="explicit_meta_no_pool",
        )
        return ResolvedAdvertiser(
            raw_name=advertiser, candidate_id=None, race_id=None,
            confidence=0.0,
            match_reason=f"explicit metadata: {state}-{district} (no CivicAPI pool)",
            parsed=pa,
        )

    # Surname extraction: parse_advertiser usually pulls it from "X for Y"
    parsed = parse_advertiser(advertiser)
    parsed.state = state
    parsed.office = OfficeType.US_HOUSE
    surname = (parsed.surname or "").lower()
    # If the parser didn't find a surname, use the leading word of the advertiser
    if not surname:
        first_token = advertiser.split()[0] if advertiser else ""
        surname = re.sub(r"[^A-Za-z]", "", first_token).lower()

    if not surname:
        # Pool exists but we can't pick a candidate. Use the first row
        # (most-incumbent) and flag low confidence.
        top = pool.iloc[0]
        return ResolvedAdvertiser(
            raw_name=advertiser,
            candidate_id=top["candidate_id"],
            race_id=top["race_id"],
            confidence=0.50,
            match_reason=f"explicit metadata: {state}-{district}, no surname → fallback",
            parsed=parsed,
        )

    best, best_score = None, 0.0
    for _, c in pool.iterrows():
        score = max(
            fuzz.partial_ratio(surname, str(c["full_name"]).lower()),
            fuzz.partial_ratio(surname, str(c["full_name"]).split()[-1].lower())
            if str(c["full_name"]).strip() else 0,
        )
        if score > best_score:
            best_score = score
            best = c

    if best is None or best_score < 70:
        # Got a pool but surname doesn't fit. Still return the race so spend
        # links to the district even if candidate is uncertain.
        top = pool.iloc[0]
        return ResolvedAdvertiser(
            raw_name=advertiser,
            candidate_id=top["candidate_id"],
            race_id=top["race_id"],
            confidence=max(best_score / 100.0, 0.50),
            match_reason=(f"explicit metadata: {state}-{district}, weak surname match "
                          f"({surname!r}, best={int(best_score)})"),
            parsed=parsed,
        )

    return ResolvedAdvertiser(
        raw_name=advertiser,
        candidate_id=best["candidate_id"],
        race_id=best["race_id"],
        confidence=min(0.99, 0.85 + best_score / 1000.0),
        match_reason=(f"explicit metadata: {advertiser!r} → {best['full_name']} "
                      f"({state}-{district}, surname score={int(best_score)})"),
        parsed=parsed,
    )


def advertiser_row_to_spend_records(
    advertiser_row: pd.Series,
    resolved: ResolvedAdvertiser,
    source_batch_id: str,
    source_file: str,
) -> list[SpendRecord]:
    """One SpendRecord per non-zero media cell."""
    cand_id = resolved.candidate_id or f"unresolved::{hashlib.md5(resolved.raw_name.encode()).hexdigest()[:10]}"
    race_id = resolved.race_id or "unresolved"
    parsed = resolved.parsed
    is_national = int(advertiser_row.get("is_national") or 0)
    agency = advertiser_row.get("agency")
    if agency is not None and (isinstance(agency, float) and pd.isna(agency)):
        agency = None
    if agency:
        agency = str(agency).strip() or None
    out: list[SpendRecord] = []
    for col, media in MEDIA_COLUMNS.items():
        amount = float(advertiser_row.get(col, 0) or 0)
        if amount <= 0:
            continue
        # Include agency in the spend_id hash so multiple agencies per
        # advertiser produce distinct rows (won't collapse on conflict).
        spend_id = hashlib.md5(
            f"{source_batch_id}|{resolved.raw_name}|{agency or ''}|{media.value}|{amount}".encode()
        ).hexdigest()[:16]
        out.append(SpendRecord(
            spend_id=spend_id,
            source_batch_id=source_batch_id,
            source_file=source_file,
            advertiser_name=resolved.raw_name,
            candidate_id=cand_id,
            race_id=race_id,
            media_type=media,
            gross_amount=amount,
            match_source=_match_source_for(resolved),
            match_reason=resolved.match_reason,
            match_confidence=resolved.confidence or 0.0,
            likely_state=parsed.state if parsed else None,
            likely_office=parsed.office.value if (parsed and parsed.office) else None,
            likely_surname=parsed.surname if parsed else None,
            agency=agency,
            is_national=is_national,
        ))
    return out


def ingest(xlsx_path: Path, store: Store, verbose: bool = True,
           use_fec_fallback: bool = True,
           use_fec_candidates: bool = True) -> dict:
    """Main entry point. Returns a summary dict."""
    flat = flatten_advertiser_file(xlsx_path)
    cand_ix = _candidate_index(store)

    source_file = xlsx_path.name
    source_batch_id = hashlib.md5(
        f"{source_file}|{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:12]

    fec_lookup = _load_fec_lookup(DB_PATH) if use_fec_fallback else {}
    if verbose and use_fec_fallback:
        print(f"FEC IE lookup loaded: {len(fec_lookup)} committees")

    records: list[SpendRecord] = []
    matched_direct = 0
    matched_fec = 0
    matched_fec_cand = 0      # joined via openFEC /candidates/search
    fec_attributed = 0        # FEC found a candidate but couldn't join CivicAPI
    pac_only = 0
    unparseable = 0
    resolutions: list[ResolvedAdvertiser] = []

    # Detect office hint from filename (forward-compatible with race-type splits)
    fname_low = source_file.lower()
    if "senate" in fname_low:
        file_office = OfficeType.US_SENATE
    elif "governor" in fname_low or "gubernatorial" in fname_low:
        file_office = OfficeType.GOVERNOR
    elif "attorney" in fname_low:
        file_office = OfficeType.ATTORNEY_GENERAL
    else:
        # Default for House files (current "House Spend …" naming convention)
        file_office = OfficeType.US_HOUSE
    if verbose:
        print(f"File office inferred: {file_office.value}")

    matched_explicit = 0  # Tier 0: explicit state+district from spend file

    # Six-bucket breakdown for the upload summary:
    cand_matched = cand_unresolved = 0
    state_pac_matched = state_pac_unresolved = 0
    nat_pac_matched = nat_pac_unresolved = 0
    cand_matched_dollars = cand_unresolved_dollars = 0.0
    state_pac_matched_dollars = state_pac_unresolved_dollars = 0.0
    nat_pac_matched_dollars = nat_pac_unresolved_dollars = 0.0

    for _, row in flat.iterrows():
        # ── Tier 0: explicit metadata from new file format ──────────────────
        explicit = resolve_via_explicit_metadata(row, cand_ix) \
            if file_office == OfficeType.US_HOUSE else None
        tier0_hit = (explicit is not None
                     and explicit.candidate_id and explicit.race_id)
        if tier0_hit:
            resolved = explicit
            parsed = explicit.parsed
            # Tier 0 already resolved; treat as candidate for categorization.
            parsed.is_candidate_committee = True
            matched_explicit += 1
        else:
            parsed = parse_advertiser(row["advertiser"])
            # If file format provided an explicit state, override the parsed guess
            if row.get("state") and not parsed.state:
                parsed.state = row["state"]
            if file_office and not parsed.office:
                parsed.office = file_office
            resolved = resolve_advertiser(parsed, cand_ix)

        # FEC fallback for unresolved advertisers (typically PACs / outside groups)
        if (resolved.candidate_id is None or resolved.race_id in (None, "unresolved")) \
                and use_fec_fallback:
            fec_result = resolve_via_fec(row["advertiser"], fec_lookup, cand_ix)
            if fec_result is not None:
                if fec_result.candidate_id and fec_result.race_id:
                    resolved = fec_result
                    matched_fec += 1
                else:
                    # FEC identified the supported candidate but we don't have
                    # a CivicAPI row for them (common for races where the
                    # primary hasn't happened yet). Keep the metadata.
                    resolved = fec_result
                    fec_attributed += 1

        # Third tier: openFEC /candidates/search for parseable-but-unresolved
        # committees. Only runs if we have a surname + state + federal office.
        if (resolved.candidate_id is None or resolved.race_id in (None, "unresolved")) \
                and use_fec_candidates:
            cand_result = resolve_via_fec_candidates(parsed, cand_ix)
            if cand_result is not None:
                if cand_result.candidate_id and cand_result.race_id:
                    resolved = cand_result
                    matched_fec_cand += 1
                elif resolved.match_reason in (None, "", "no surname match above threshold"):
                    # Upgrade to attributed metadata only if we didn't already
                    # have a better FEC IE attribution.
                    resolved = cand_result
                    fec_attributed += 1

        resolutions.append(resolved)

        if resolved.candidate_id and resolved.race_id \
                and resolved.race_id != "unresolved":
            reason = resolved.match_reason or ""
            if ("FEC IE" in reason or "openFEC candidate" in reason
                    or "explicit metadata" in reason):
                pass  # already counted in matched_fec / matched_fec_cand / matched_explicit
            else:
                matched_direct += 1
        elif parsed.notes == "pac_or_outside_group":
            pac_only += 1
        else:
            unparseable += 1

        # ── Six-bucket categorization ─────────────────────────────────────
        # bucket = (candidate vs PAC) × (national vs state) × (matched vs not)
        is_resolved = bool(resolved.candidate_id and resolved.race_id
                            and resolved.race_id != "unresolved")
        is_national_row = int(row.get("is_national") or 0) == 1
        # Treat anything parse_advertiser flagged as a real candidate
        # committee as "candidate"; everything else (PAC markers, fail-to-
        # parse) as a PAC/outside group.
        is_candidate = bool(parsed.is_candidate_committee)
        row_dollars = float(
            (row.get("broadcast") or 0) + (row.get("cable") or 0)
            + (row.get("ctv") or 0) + (row.get("digital") or 0)
            + (row.get("radio") or 0)
        )
        if is_candidate:
            if is_resolved:
                cand_matched += 1
                cand_matched_dollars += row_dollars
            else:
                cand_unresolved += 1
                cand_unresolved_dollars += row_dollars
        elif is_national_row:
            if is_resolved:
                nat_pac_matched += 1
                nat_pac_matched_dollars += row_dollars
            else:
                nat_pac_unresolved += 1
                nat_pac_unresolved_dollars += row_dollars
        else:
            if is_resolved:
                state_pac_matched += 1
                state_pac_matched_dollars += row_dollars
            else:
                state_pac_unresolved += 1
                state_pac_unresolved_dollars += row_dollars

        records.extend(advertiser_row_to_spend_records(
            row, resolved, source_batch_id, source_file))

    store.upsert_spend(records)

    # ── Tier 4: roster fuzzy match ──────────────────────────────────────
    # After the spend rows are persisted, scan any still-unresolved rows
    # in this batch and fuzzy-match their parsed (likely_surname, state,
    # office) against the local 2026 candidate roster. This is the layer
    # that links advertisers to candidates loaded from CivicAPI / the
    # Wikipedia DMA dataset.
    roster_matched = 0
    try:
        # Local import — match_spend_to_candidates lives at the project root,
        # which is already on sys.path from the top of this file.
        from match_spend_to_candidates import match as _roster_match
        roster_summary = _roster_match(source_batch_id=source_batch_id, verbose=False)
        roster_matched = roster_summary.get("matched", 0)
    except Exception as e:
        if verbose:
            print(f"  Roster fuzzy match skipped: {e}")

    if verbose:
        print(f"Advertisers flattened:    {len(flat)}")
        print(f"  Explicit state+district:   {matched_explicit}")
        print(f"  Direct committee match:    {matched_direct}")
        print(f"  Via FEC IE (joined):       {matched_fec}")
        print(f"  Via openFEC candidates:    {matched_fec_cand}")
        print(f"  Via roster fuzzy match:    {roster_matched}")
        print(f"  Via FEC (attributed, unjoined):  {fec_attributed}")
        print(f"  PAC / outside group (no FEC match): {pac_only}")
        print(f"  Unparseable:               {unparseable}")
        print()
        print("Resolution breakdown (rows / dollars):")
        print(f"  Candidates matched     : {cand_matched:5d}  ${cand_matched_dollars:>15,.0f}")
        print(f"  Candidates unresolved  : {cand_unresolved:5d}  ${cand_unresolved_dollars:>15,.0f}")
        print(f"  State PACs matched     : {state_pac_matched:5d}  ${state_pac_matched_dollars:>15,.0f}")
        print(f"  State PACs unresolved  : {state_pac_unresolved:5d}  ${state_pac_unresolved_dollars:>15,.0f}")
        print(f"  National PACs matched  : {nat_pac_matched:5d}  ${nat_pac_matched_dollars:>15,.0f}")
        print(f"  National PACs unresolv : {nat_pac_unresolved:5d}  ${nat_pac_unresolved_dollars:>15,.0f}")
        print(f"Spend records written:    {len(records)}")

    return {
        "n_advertisers": len(flat),
        "n_candidates_matched":  cand_matched,
        "n_candidates_unresolved": cand_unresolved,
        "n_state_pacs_matched":  state_pac_matched,
        "n_state_pacs_unresolved": state_pac_unresolved,
        "n_national_pacs_matched":  nat_pac_matched,
        "n_national_pacs_unresolved": nat_pac_unresolved,
        "candidates_matched_dollars": cand_matched_dollars,
        "candidates_unresolved_dollars": cand_unresolved_dollars,
        "state_pacs_matched_dollars": state_pac_matched_dollars,
        "state_pacs_unresolved_dollars": state_pac_unresolved_dollars,
        "national_pacs_matched_dollars": nat_pac_matched_dollars,
        "national_pacs_unresolved_dollars": nat_pac_unresolved_dollars,
        "n_matched_explicit": matched_explicit,
        "n_matched_direct": matched_direct,
        "n_matched_fec": matched_fec,
        "n_matched_fec_candidates": matched_fec_cand,
        "n_matched_roster": roster_matched,
        "n_fec_attributed": fec_attributed,
        "n_pac": pac_only,
        "n_unparseable": unparseable,
        "n_records": len(records),
        "source_batch_id": source_batch_id,
        "resolutions": resolutions,
        "flat": flat,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", help="Path to Home_Advertiser xlsx")
    args = ap.parse_args()
    store = Store(DB_PATH)
    ingest(Path(args.xlsx), store)
