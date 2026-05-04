"""Load political spend workbooks into SQLite.

The ingester supports three source families:

* advertiser-level race files (House, Senate, Governor, Downballot)
* legacy Home_Advertiser block exports
* aggregate Spend by State / Spend by Market files

Advertiser rows write canonical media spend records plus source metadata used
by the dashboard filters. Aggregate files write `spend_aggregates`.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
try:
    from rapidfuzz import fuzz, process
    HAS_RAPIDFUZZ = True
except ImportError:  # Keep local ingests working before optional deps are installed.
    HAS_RAPIDFUZZ = False

    class _FuzzFallback:
        @staticmethod
        def partial_ratio(left: object, right: object) -> float:
            return SequenceMatcher(None, str(left).lower(), str(right).lower()).ratio() * 100

        @staticmethod
        def WRatio(left: object, right: object) -> float:
            return _FuzzFallback.partial_ratio(left, right)

    class _ProcessFallback:
        @staticmethod
        def extractOne(query: object, choices: list[str], scorer=None, score_cutoff: float = 0):
            scorer = scorer or _FuzzFallback.WRatio
            best_choice = None
            best_score = 0.0
            best_index = -1
            for idx, choice in enumerate(choices):
                score = float(scorer(query, choice))
                if score > best_score:
                    best_choice = choice
                    best_score = score
                    best_index = idx
            if best_choice is None or best_score < score_cutoff:
                return None
            return best_choice, best_score, best_index

    fuzz = _FuzzFallback()
    process = _ProcessFallback()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH
from data.schema import MediaType, OfficeType, SpendRecord
from data.store import Store


US_STATES = {
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
STATE_CODES = set(US_STATES.values())

OFFICE_PATTERNS: list[tuple[re.Pattern, OfficeType]] = [
    (re.compile(r"\bu\.?s\.?\s*senate\b|\bus senate\b", re.I), OfficeType.US_SENATE),
    (re.compile(r"\bu\.?s\.?\s*(house|representative|congress)\b", re.I), OfficeType.US_HOUSE),
    (re.compile(r"\bcongress(?:ional|man|woman)?\b", re.I), OfficeType.US_HOUSE),
    (re.compile(r"\bcd[-\s]*\d+\b", re.I), OfficeType.US_HOUSE),
    (re.compile(r"\bgovernor\b", re.I), OfficeType.GOVERNOR),
    (re.compile(r"\battorney\s+general\b|\bag\b", re.I), OfficeType.ATTORNEY_GENERAL),
    (
        re.compile(r"\bstate\s+(house|assembly|representative|delegate)\b|\bhouse of delegates\b", re.I),
        OfficeType.STATE_HOUSE,
    ),
    (re.compile(r"\bstate\s+senate\b", re.I), OfficeType.STATE_SENATE),
]

PAC_MARKERS = re.compile(
    r"\b(pac|fund|project|committee|citizens|coalition|action|alliance|"
    r"majority|minority|values|voice|forward|future|together|integrity|freedom|"
    r"prosperity|victory|defense|patriots|progress|fair|protect|jobs|super pac)\b",
    re.I,
)

MEDIA_COLUMNS = {
    "broadcast": MediaType.BROADCAST,
    "cable": MediaType.CABLE,
    "ctv": MediaType.CTV,
    "digital": MediaType.DIGITAL,
    "radio": MediaType.RADIO,
}


@dataclass
class ParsedAdvertiser:
    raw_name: str
    is_candidate_committee: bool
    surname: Optional[str] = None
    state: Optional[str] = None
    office: Optional[OfficeType] = None
    notes: str = ""


@dataclass
class ResolvedAdvertiser:
    raw_name: str
    candidate_id: Optional[str]
    race_id: Optional[str]
    confidence: float
    match_reason: str
    parsed: ParsedAdvertiser = field(default=None)  # type: ignore[assignment]
    match_source: str = "unmatched"


def _clean_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _s(value: object) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).replace("\u200b", "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def _money(value: object) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if text in {"", ".", "-", "-."}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _normalize_state(value: object) -> Optional[str]:
    text = _s(value)
    if not text:
        return None
    if text.lower() == "national":
        return "National"
    if text.lower() == "unknown":
        return "Unknown"
    if "/" in text:
        parts = [_normalize_state(part) or part.strip().upper() for part in text.split("/")]
        return "/".join(part for part in parts if part)
    upper = text.upper()
    if upper in STATE_CODES:
        return upper
    return US_STATES.get(text.lower(), text)


def _state_code(value: object) -> Optional[str]:
    state = _normalize_state(value)
    return state if state in STATE_CODES else None


def _normalize_district(value: object) -> Optional[str]:
    text = _s(value)
    if not text or text.lower() in {"national", "unknown"}:
        return None
    text = re.sub(r"^(?:[A-Z]{2}[-\s])?(?:CD[-\s]?)?", "", text, flags=re.I).strip()
    if text.isdigit():
        return str(int(text))
    try:
        numeric = float(text)
        if numeric.is_integer():
            return str(int(numeric))
    except ValueError:
        pass
    return text.upper()


def _normalize_committee_name(name: str) -> str:
    value = name.upper().strip()
    value = re.sub(r"['\"`]", "", value)
    value = re.sub(r"[^\w\s&]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _file_batch_id(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def _pick_column(columns: list[str], *aliases: str) -> Optional[str]:
    normalized = {_clean_header(column): column for column in columns}
    for alias in aliases:
        hit = normalized.get(_clean_header(alias))
        if hit:
            return hit
    for column in columns:
        clean = _clean_header(column)
        if any(_clean_header(alias) in clean for alias in aliases):
            return column
    return None


def _read_sheet(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path, sheet_name=0)


def _infer_data_type(path: Path, columns: list[str]) -> str:
    filename = path.name.lower()
    normalized = {_clean_header(column) for column in columns}
    if "grossspending" in normalized and "spend by state" in filename:
        return "state_aggregate"
    if "grossspending" in normalized and "spend by market" in filename:
        return "market_aggregate"
    if "downballotadvertiser" in normalized or "downballot" in filename:
        return "downballot"
    if "governoradvertiser" in normalized or "governor" in filename:
        return "governor"
    if "senateadvertiser" in normalized or "senate" in filename:
        return "us_senate"
    if "house" in filename:
        return "us_house"
    return "advertiser"


def _default_race_type(data_type: str) -> Optional[str]:
    return {
        "us_house": "U.S. House",
        "us_senate": "U.S. Senate",
        "governor": "Governor",
    }.get(data_type)


def _office_from_race_type(race_type: object, data_type: str | None = None) -> Optional[OfficeType]:
    text = _s(race_type) or _default_race_type(data_type or "") or ""
    value = text.lower()
    if "u.s. senate" in value or "us senate" in value:
        return OfficeType.US_SENATE
    if "u.s. house" in value or "us house" in value or "congress" in value:
        return OfficeType.US_HOUSE
    if "governor" in value:
        return OfficeType.GOVERNOR
    if "attorney general" in value:
        return OfficeType.ATTORNEY_GENERAL
    if "state senate" in value:
        return OfficeType.STATE_SENATE
    if "state house" in value or "house of delegates" in value or "assembly" in value:
        return OfficeType.STATE_HOUSE
    if data_type == "us_house":
        return OfficeType.US_HOUSE
    if data_type == "us_senate":
        return OfficeType.US_SENATE
    if data_type == "governor":
        return OfficeType.GOVERNOR
    return None


def _source_party(row: pd.Series) -> Optional[str]:
    return _s(row.get("source_party_affiliation"))


def _source_advertiser_type(row: pd.Series) -> Optional[str]:
    return _s(row.get("advertiser_type"))


def _looks_like_candidate(row: pd.Series, parsed: ParsedAdvertiser) -> bool:
    affiliation = (_source_advertiser_type(row) or "").lower()
    if "candidate" in affiliation:
        return True
    if any(marker in affiliation for marker in ["pac", "advocacy", "party", "issue", "trade", "government"]):
        return False
    advertiser = str(row.get("advertiser") or "")
    name_tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", advertiser)
    if not PAC_MARKERS.search(advertiser) and 2 <= len(name_tokens) <= 4:
        return True
    return parsed.is_candidate_committee


def parse_advertiser(name: str) -> ParsedAdvertiser:
    cleaned = str(name or "").strip()
    parsed = ParsedAdvertiser(raw_name=cleaned, is_candidate_committee=False)
    if not cleaned:
        parsed.notes = "blank"
        return parsed

    match = re.match(r"^(?P<who>[A-Z][\w\-'\.]+(?:\s+[A-Z][\w\-'\.]+){0,4})\s+for\s+(?P<rest>.+)$", cleaned)
    if match:
        who = match.group("who").strip()
        rest = match.group("rest").strip()
        parsed.surname = re.sub(r"[^A-Za-z'\-]", "", who.split()[-1]) or None

        for state_name, code in US_STATES.items():
            if re.search(rf"\b{re.escape(state_name)}\b", rest, re.I):
                parsed.state = code
                break
        code_match = re.search(r"\b([A-Z]{2})\b", rest)
        if code_match and code_match.group(1) in STATE_CODES:
            parsed.state = code_match.group(1)

        for pattern, office_type in OFFICE_PATTERNS:
            if pattern.search(rest):
                parsed.office = office_type
                parsed.is_candidate_committee = True
                return parsed

        parsed.notes = f"unmatched 'for' structure: {rest}"
        return parsed

    parsed.notes = "pac_or_outside_group" if PAC_MARKERS.search(cleaned) else "unparseable"
    return parsed


def _flatten_legacy_export(path: Path, source_batch_id: str) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, header=0, usecols=range(7))
    else:
        df = pd.read_excel(path, sheet_name=0, header=0, usecols="A:G")
    df.columns = ["advertiser", "broadcast", "cable", "ctv", "digital", "radio", "grand_total"]
    df["advertiser"] = df["advertiser"].apply(_s)
    df = df[~df["advertiser"].fillna("").str.lower().eq("grand total")].copy()
    for column in ["broadcast", "cable", "ctv", "digital", "radio", "grand_total"]:
        df[column] = df[column].map(_money)
    df["advertiser"] = df["advertiser"].replace({"": None}).ffill()
    df = df[df["advertiser"].notna()].copy()
    grouped = (
        df.groupby("advertiser", as_index=False)[["broadcast", "cable", "ctv", "digital", "radio", "grand_total"]]
        .sum()
        .reset_index(drop=True)
    )
    grouped["total"] = grouped.apply(
        lambda row: row["grand_total"] if row["grand_total"] > 0 else sum(row[col] for col in MEDIA_COLUMNS),
        axis=1,
    )
    grouped["source_batch_id"] = source_batch_id
    grouped["source_data_type"] = "legacy_advertiser"
    grouped["source_race_type"] = None
    grouped["source_state"] = None
    grouped["source_district"] = None
    grouped["source_party_affiliation"] = None
    grouped["advertiser_type"] = None
    grouped["agency"] = None
    grouped["is_national"] = 0
    return grouped[grouped["total"] > 0].sort_values("total", ascending=False).reset_index(drop=True)


def flatten_advertiser_file(path: Path) -> pd.DataFrame:
    source_batch_id = _file_batch_id(path)
    raw = _read_sheet(path)
    columns = [str(column).strip() for column in raw.columns]
    data_type = _infer_data_type(path, columns)
    aggregate_col = _pick_column(columns, "gross spending")
    if aggregate_col:
        return pd.DataFrame()

    advertiser_col = _pick_column(
        columns,
        "advertiser",
        "house advertiser",
        "senate advertiser",
        "governor advertiser",
        "downballot advertiser",
    )
    media_cols = {media: _pick_column(columns, media) for media in MEDIA_COLUMNS}
    if not advertiser_col or not any(media_cols.values()):
        return _flatten_legacy_export(path, source_batch_id)

    state_col = _pick_column(columns, "state")
    district_col = _pick_column(columns, "district", "cdfips")
    race_type_col = _pick_column(columns, "race type", "office")
    party_col = _pick_column(columns, "party affiliation", "party")
    advertiser_type_col = _pick_column(columns, "affiliation type", "advertiser type")
    agency_col = _pick_column(columns, "agency")
    total_col = _pick_column(columns, "grand total", "total")

    rows: list[dict] = []
    for idx, record in raw.iterrows():
        advertiser = _s(record.get(advertiser_col))
        if not advertiser or advertiser.lower() == "grand total":
            continue

        row: dict[str, object] = {
            "advertiser": advertiser,
            "source_batch_id": source_batch_id,
            "source_data_type": data_type,
            "source_race_type": _s(record.get(race_type_col)) if race_type_col else _default_race_type(data_type),
            "source_state": _normalize_state(record.get(state_col)) if state_col else None,
            "source_district": _normalize_district(record.get(district_col)) if district_col else None,
            "source_party_affiliation": _s(record.get(party_col)) if party_col else None,
            "advertiser_type": _s(record.get(advertiser_type_col)) if advertiser_type_col else None,
            "agency": _s(record.get(agency_col)) if agency_col else None,
            "is_national": 1 if (state_col and str(record.get(state_col)).strip().lower() == "national") else 0,
            "source_row_number": int(idx) + 2,
        }
        for media, source_column in media_cols.items():
            row[media] = _money(record.get(source_column)) if source_column else 0.0
        row["grand_total"] = _money(record.get(total_col)) if total_col else 0.0
        row["total"] = row["grand_total"] or sum(float(row[media]) for media in MEDIA_COLUMNS)
        if float(row["total"]) > 0:
            rows.append(row)

    return pd.DataFrame(rows).sort_values("total", ascending=False).reset_index(drop=True)


def flatten_aggregate_file(path: Path) -> pd.DataFrame:
    raw = _read_sheet(path)
    columns = [str(column).strip() for column in raw.columns]
    gross_col = _pick_column(columns, "gross spending")
    share_col = _pick_column(columns, "% of total amount along market or state")
    if not gross_col:
        return pd.DataFrame()

    label_candidates = [column for column in columns if column not in {gross_col, share_col}]
    label_col = label_candidates[0] if label_candidates else columns[0]
    aggregate_type = "market" if "market" in path.name.lower() else "state"
    source_batch_id = _file_batch_id(path)
    rows: list[dict] = []
    for idx, record in raw.iterrows():
        label = _s(record.get(label_col))
        gross = _money(record.get(gross_col))
        if not label or gross <= 0:
            continue
        aggregate_id = hashlib.md5(f"{source_batch_id}|{aggregate_type}|row:{idx}".encode()).hexdigest()[:24]
        rows.append(
            {
                "aggregate_id": aggregate_id,
                "source_batch_id": source_batch_id,
                "source_file": path.name,
                "aggregate_type": aggregate_type,
                "label": label,
                "share_of_total": _money(record.get(share_col)) if share_col else None,
                "gross_spending": gross,
            }
        )
    return pd.DataFrame(rows)


def _candidate_index(store: Store) -> pd.DataFrame:
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT c.candidate_id, c.full_name, c.party, c.race_id, c.votes_received,
                   r.state, r.office, r.district, r.general_date, r.election_name
            FROM candidates c
            JOIN races r ON r.race_id = c.race_id
            """
        ).fetchall()
    finally:
        conn.close()
    return pd.DataFrame([dict(row) for row in rows])


def _district_equal(left: object, right: object) -> bool:
    return (_normalize_district(left) or "") == (_normalize_district(right) or "")


def _best_candidate_by_surname(pool: pd.DataFrame, surname: str) -> tuple[Optional[pd.Series], float]:
    if pool.empty or not surname:
        return None, 0.0
    best = None
    best_score = 0.0
    surname = surname.lower()
    for _, row in pool.iterrows():
        full_name = str(row["full_name"]).lower()
        last = full_name.split()[-1] if full_name.split() else full_name
        score = max(fuzz.partial_ratio(surname, full_name), fuzz.partial_ratio(surname, last))
        if score > best_score:
            best = row
            best_score = score
    return best, best_score


def resolve_advertiser(parsed: ParsedAdvertiser, cand_ix: pd.DataFrame) -> ResolvedAdvertiser:
    if not parsed.is_candidate_committee or not parsed.state or not parsed.office or cand_ix.empty:
        return ResolvedAdvertiser(parsed.raw_name, None, None, 0.0, "not a candidate committee", parsed)

    pool = cand_ix[(cand_ix["state"] == parsed.state) & (cand_ix["office"] == parsed.office.value)]
    best, best_score = _best_candidate_by_surname(pool, parsed.surname or "")
    if best is None or best_score < 85:
        return ResolvedAdvertiser(parsed.raw_name, None, None, best_score / 100.0, "no surname match", parsed)

    race_pool = pool[pool["full_name"].str.lower() == str(best["full_name"]).lower()].copy()
    race_pool["votes_received"] = race_pool["votes_received"].fillna(0)
    top = race_pool.sort_values("votes_received", ascending=False).iloc[0]
    return ResolvedAdvertiser(
        raw_name=parsed.raw_name,
        candidate_id=top["candidate_id"],
        race_id=top["race_id"],
        confidence=best_score / 100.0,
        match_reason=f"matched {top['full_name']} ({top['state']} {top['office']})",
        parsed=parsed,
        match_source="direct",
    )


def resolve_via_explicit_metadata(row: pd.Series, cand_ix: pd.DataFrame) -> Optional[ResolvedAdvertiser]:
    state = _state_code(row.get("source_state"))
    office = _office_from_race_type(row.get("source_race_type"), str(row.get("source_data_type") or ""))
    advertiser = str(row.get("advertiser") or "").strip()
    if not state or not office or not advertiser or cand_ix.empty:
        return None

    parsed = parse_advertiser(advertiser)
    parsed.state = state
    parsed.office = office
    parsed.is_candidate_committee = _looks_like_candidate(row, parsed)
    if not parsed.surname:
        tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", advertiser)
        parsed.surname = tokens[-1] if tokens else None

    pool = cand_ix[(cand_ix["state"] == state) & (cand_ix["office"] == office.value)].copy()
    district = _normalize_district(row.get("source_district"))
    if district and office == OfficeType.US_HOUSE:
        pool = pool[pool["district"].apply(lambda value: _district_equal(value, district))]

    if pool.empty:
        return ResolvedAdvertiser(
            advertiser,
            None,
            None,
            0.0,
            f"explicit metadata: {state}{('-' + district) if district else ''} no candidate pool",
            parsed,
            match_source="unmatched",
        )

    best, best_score = _best_candidate_by_surname(pool, parsed.surname or "")
    if best is None or best_score < 70:
        return ResolvedAdvertiser(
            advertiser,
            None,
            None,
            best_score / 100.0,
            f"explicit metadata: weak surname match ({state}{('-' + district) if district else ''})",
            parsed,
            match_source="unmatched",
        )

    return ResolvedAdvertiser(
        raw_name=advertiser,
        candidate_id=best["candidate_id"],
        race_id=best["race_id"],
        confidence=min(0.99, 0.85 + best_score / 1000.0),
        match_reason=f"explicit metadata: {advertiser} -> {best['full_name']} ({state})",
        parsed=parsed,
        match_source="direct",
    )


def _load_fec_lookup(db_path: Path, cycle: int = 2026) -> dict[str, dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        def _table_exists(name: str) -> bool:
            return conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone() is not None

        rows: list[dict] = []
        if _table_exists("fec_ie_by_committee"):
            rows.extend(
                dict(row)
                for row in conn.execute(
                    """
                    SELECT committee_id, committee_name, candidate_id, candidate_name,
                           office, state, district, total_spent
                    FROM fec_ie_by_committee
                    WHERE cycle = ? AND support_oppose = 'S' AND committee_name IS NOT NULL
                    ORDER BY committee_id, total_spent DESC
                    """,
                    (cycle,),
                ).fetchall()
            )
        if _table_exists("fec_committee_summary") and _table_exists("fec_candidates"):
            rows.extend(
                dict(row)
                for row in conn.execute(
                    """
                    SELECT cs.committee_id,
                           cs.committee_name,
                           COALESCE(NULLIF(cs.candidate_id, ''), l.candidate_id) AS candidate_id,
                           fc.candidate_name,
                           fc.office,
                           fc.state,
                           fc.district,
                           COALESCE(cs.independent_expenditures, cs.total_disbursements, 0) AS total_spent
                    FROM fec_committee_summary cs
                    LEFT JOIN fec_candidate_committee_links l
                      ON l.committee_id = cs.committee_id
                     AND l.fec_election_year = ?
                    LEFT JOIN fec_candidates fc
                      ON fc.candidate_id = COALESCE(NULLIF(cs.candidate_id, ''), l.candidate_id)
                    WHERE cs.election_year = ?
                      AND cs.committee_name IS NOT NULL
                      AND fc.candidate_id IS NOT NULL
                    ORDER BY cs.committee_id, total_spent DESC
                    """,
                    (cycle, cycle),
                ).fetchall()
            )
        if _table_exists("fec_committee_financial_summaries") and _table_exists("fec_candidates"):
            rows.extend(
                dict(row)
                for row in conn.execute(
                    """
                    SELECT s.committee_id,
                           s.committee_name,
                           l.candidate_id,
                           fc.candidate_name,
                           fc.office,
                           fc.state,
                           fc.district,
                           COALESCE(s.total_disbursements, 0) AS total_spent
                    FROM fec_committee_financial_summaries s
                    JOIN fec_candidate_committee_links l
                      ON l.committee_id = s.committee_id
                     AND l.fec_election_year = ?
                    JOIN fec_candidates fc
                      ON fc.candidate_id = l.candidate_id
                    WHERE s.committee_name IS NOT NULL
                    ORDER BY s.committee_id, total_spent DESC
                    """,
                    (cycle,),
                ).fetchall()
            )
    finally:
        conn.close()

    by_committee: dict[str, dict] = {}
    for row in rows:
        committee_id = row.get("committee_id")
        if committee_id and committee_id not in by_committee:
            by_committee[committee_id] = dict(row)

    by_name: dict[str, dict] = {}
    for value in by_committee.values():
        key = _normalize_committee_name(value["committee_name"] or "")
        if key:
            by_name[key] = value
    return by_name


def resolve_via_fec(
    advertiser_name: str,
    fec_lookup: dict[str, dict],
    cand_ix: pd.DataFrame,
    threshold: int = 88,
) -> Optional[ResolvedAdvertiser]:
    if not fec_lookup:
        return None

    normalized = _normalize_committee_name(advertiser_name)
    if HAS_RAPIDFUZZ:
        result = process.extractOne(normalized, list(fec_lookup.keys()), scorer=fuzz.WRatio, score_cutoff=threshold)
        if result is None:
            return None
        committee_name, score, _ = result
    else:
        committee_name = normalized if normalized in fec_lookup else None
        if committee_name is None:
            return None
        score = 100.0
    fec_row = fec_lookup[committee_name]
    fec_candidate_name = str(fec_row.get("candidate_name") or "").upper()
    surname = fec_candidate_name.split(",")[0].strip().lower() if "," in fec_candidate_name else ""
    office_map = {"H": OfficeType.US_HOUSE.value, "S": OfficeType.US_SENATE.value, "P": None}
    office = office_map.get(fec_row.get("office"))
    state = fec_row.get("state")

    parsed = ParsedAdvertiser(advertiser_name, False, surname=surname or None, state=state, notes="pac_resolved_via_fec_ie")
    if office:
        parsed.office = OfficeType(office)

    if not surname or not office or cand_ix.empty:
        return ResolvedAdvertiser(
            advertiser_name,
            None,
            None,
            score / 100.0,
            f"FEC IE match to {committee_name}, but no candidate join",
            parsed,
            match_source="fec_ie",
        )

    pool = cand_ix[(cand_ix["state"] == state) & (cand_ix["office"] == office)]
    best, best_score = _best_candidate_by_surname(pool, surname)
    if best is None or best_score < 85:
        return None

    race_pool = pool[pool["full_name"].str.lower() == str(best["full_name"]).lower()].copy()
    race_pool["votes_received"] = race_pool["votes_received"].fillna(0)
    top = race_pool.sort_values("votes_received", ascending=False).iloc[0]
    return ResolvedAdvertiser(
        advertiser_name,
        top["candidate_id"],
        top["race_id"],
        (score / 100.0) * 0.9,
        f"FEC IE: '{advertiser_name}' -> {committee_name} -> {top['full_name']}",
        parsed,
        match_source="fec_ie",
    )


def advertiser_row_to_spend_records(
    advertiser_row: pd.Series,
    resolved: ResolvedAdvertiser,
    source_file: str,
    source_batch_id: str,
) -> list[SpendRecord]:
    candidate_id = resolved.candidate_id or f"unresolved::{hashlib.md5(resolved.raw_name.encode()).hexdigest()[:12]}"
    race_id = resolved.race_id or "unresolved"
    records: list[SpendRecord] = []
    agency = _s(advertiser_row.get("agency"))
    for column, media in MEDIA_COLUMNS.items():
        amount = float(advertiser_row.get(column, 0) or 0)
        if amount <= 0:
            continue
        spend_id = hashlib.md5(
            "|".join(
                [
                    source_batch_id,
                    resolved.raw_name,
                    agency or "",
                    str(advertiser_row.get("source_row_number") or ""),
                    media.value,
                    f"{amount:.2f}",
                ]
            ).encode()
        ).hexdigest()[:24]
        records.append(
            SpendRecord(
                spend_id=spend_id,
                source_batch_id=source_batch_id,
                source_file=source_file,
                source_data_type=_s(advertiser_row.get("source_data_type")),
                source_race_type=_s(advertiser_row.get("source_race_type")),
                source_state=_s(advertiser_row.get("source_state")),
                source_district=_s(advertiser_row.get("source_district")),
                source_party_affiliation=_s(advertiser_row.get("source_party_affiliation")),
                advertiser_type=_s(advertiser_row.get("advertiser_type")),
                advertiser_name=resolved.raw_name,
                candidate_id=candidate_id,
                race_id=race_id,
                media_type=media,
                gross_amount=amount,
                match_source=resolved.match_source,
                match_reason=resolved.match_reason,
                match_confidence=resolved.confidence,
                likely_state=resolved.parsed.state,
                likely_office=resolved.parsed.office.value if resolved.parsed.office else None,
                likely_surname=resolved.parsed.surname,
                agency=agency,
                is_national=int(advertiser_row.get("is_national") or 0),
            )
        )
    return records


def ingest(
    xlsx_path: Path,
    store: Store,
    verbose: bool = True,
    use_fec_fallback: bool = True,
) -> dict:
    source_batch_id = _file_batch_id(xlsx_path)
    source_file = xlsx_path.name
    aggregate_df = flatten_aggregate_file(xlsx_path)
    if not aggregate_df.empty:
        inserted = store.upsert_spend_aggregates(aggregate_df.to_dict("records"))
        store.set_metadata("last_uploaded_file", source_file)
        summary = {
            "source_file": source_file,
            "source_batch_id": source_batch_id,
            "source_data_type": f"{aggregate_df['aggregate_type'].iloc[0]}_aggregate",
            "advertisers_flattened": 0,
            "aggregate_rows": inserted,
            "records_written": 0,
        }
        if verbose:
            print(summary)
        return summary

    flat = flatten_advertiser_file(xlsx_path)
    cand_ix = _candidate_index(store)
    fec_lookup = _load_fec_lookup(DB_PATH) if use_fec_fallback else {}

    records: list[SpendRecord] = []
    matched_direct = 0
    matched_fec = 0
    unresolved = 0

    for _, row in flat.iterrows():
        explicit = resolve_via_explicit_metadata(row, cand_ix)
        if explicit and explicit.candidate_id:
            resolved = explicit
        else:
            parsed = parse_advertiser(str(row["advertiser"]))
            state = _state_code(row.get("source_state"))
            office = _office_from_race_type(row.get("source_race_type"), str(row.get("source_data_type") or ""))
            if state:
                parsed.state = state
            if office:
                parsed.office = office
            parsed.is_candidate_committee = _looks_like_candidate(row, parsed)
            resolved = resolve_advertiser(parsed, cand_ix)
            if explicit and not resolved.candidate_id:
                resolved = explicit

        if (resolved.candidate_id is None or resolved.race_id is None) and use_fec_fallback:
            fec_result = resolve_via_fec(str(row["advertiser"]), fec_lookup, cand_ix)
            if fec_result is not None:
                resolved = fec_result

        if resolved.match_source == "direct" and resolved.candidate_id:
            matched_direct += 1
        elif resolved.match_source == "fec_ie" and resolved.candidate_id:
            matched_fec += 1
        else:
            unresolved += 1

        records.extend(
            advertiser_row_to_spend_records(
                advertiser_row=row,
                resolved=resolved,
                source_file=source_file,
                source_batch_id=source_batch_id,
            )
        )

    inserted = store.upsert_spend(records)
    store.set_metadata("last_uploaded_file", source_file)

    summary = {
        "source_file": source_file,
        "source_batch_id": source_batch_id,
        "source_data_type": flat["source_data_type"].iloc[0] if not flat.empty else "unknown",
        "advertisers_flattened": len(flat),
        "aggregate_rows": 0,
        "matched_direct": matched_direct,
        "matched_fec": matched_fec,
        "unresolved": unresolved,
        "records_written": inserted,
    }

    if verbose:
        print(summary)
    return summary


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/Users/gerritmaxwell/Downloads/Home_Advertiser (12).xlsx")
    store = Store(DB_PATH)
    ingest(path, store, verbose=True)


if __name__ == "__main__":
    main()
