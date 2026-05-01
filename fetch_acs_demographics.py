"""Pull ACS 5-year demographics for target states (state-level + congressional district)
and compute a cable-fit proxy score.

Cable-fit score (0-100, higher = better cable/linear-TV fit):
    mean of three normalized inputs —
    - median age (30y -> 0, 50y -> 100)
    - median HH income ($40k -> 0, $100k -> 100)
    - broadband-subscribed household % (40% -> 0, 90% -> 100)

These are coarse proxies; real cable subscription data is not public.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH, TARGET_STATES


STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56",
}
FIPS_TO_STATE = {v: k for k, v in STATE_FIPS.items()}

ACS_YEAR = 2022
BASE = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"

VARS = [
    "B01003_001E",  # total population
    "B01002_001E",  # median age
    "B19013_001E",  # median HH income
    "B15003_001E",  # edu denominator (pop 25+)
    "B15003_022E",  # bachelor's
    "B15003_023E",  # master's
    "B15003_024E",  # professional
    "B15003_025E",  # doctorate
    "B02001_002E",  # white alone
    "B02001_003E",  # black alone
    "B02001_005E",  # asian alone
    "B03003_003E",  # hispanic
    "B28002_001E",  # broadband denom (households)
    "B28002_007E",  # broadband subscription
    "B25077_001E",  # median home value
    # urbanicity proxies
    "B25024_001E",  # housing units denom
    "B25024_007E",  # 5-9 units
    "B25024_008E",  # 10-19 units
    "B25024_009E",  # 20-49 units
    "B25024_010E",  # 50+ units
    "B08301_001E",  # commute denom
    "B08301_010E",  # public transit commuters (excl taxicab)
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS district_demographics (
    geo_id            TEXT PRIMARY KEY,
    geo_type          TEXT NOT NULL,        -- 'state' or 'cd'
    state             TEXT NOT NULL,
    district          TEXT,                 -- CD number or NULL for state
    name              TEXT,
    acs_year          INTEGER,
    population        INTEGER,
    median_age        REAL,
    median_hh_income  REAL,
    pct_bachelors_plus REAL,
    pct_white         REAL,
    pct_black         REAL,
    pct_hispanic      REAL,
    pct_asian         REAL,
    pct_broadband     REAL,
    median_home_value REAL,
    pct_multiunit     REAL,
    pct_transit       REAL,
    urbanicity_score  REAL,
    cable_fit_score   REAL,
    ctv_fit_score     REAL,
    fetched_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_demo_state ON district_demographics(state);
CREATE INDEX IF NOT EXISTS idx_demo_type ON district_demographics(geo_type);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(district_demographics)").fetchall()}
    for col, ddl in [
        ("pct_multiunit", "REAL"),
        ("pct_transit", "REAL"),
        ("urbanicity_score", "REAL"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE district_demographics ADD COLUMN {col} {ddl}")
    conn.commit()


def clamp(value: float, lo: float, hi: float) -> float:
    if value is None:
        return 0.0
    return max(lo, min(hi, value))


def norm(value: float, lo: float, hi: float) -> float:
    """Map value in [lo, hi] to 0-100."""
    if value is None or hi == lo:
        return 0.0
    return clamp((value - lo) / (hi - lo), 0.0, 1.0) * 100


def compute_urbanicity(pct_multiunit: float | None, pct_transit: float | None) -> float:
    # Weighted blend of multi-unit housing share (strong structural signal) and transit commute share.
    mu = norm(pct_multiunit or 0, 5, 45)
    tr = norm(pct_transit or 0, 0, 25)
    return round(0.6 * mu + 0.4 * tr, 1)


def compute_scores(row: dict) -> tuple[float, float]:
    age = row.get("median_age") or 0
    income = row.get("median_hh_income") or 0
    broadband = row.get("pct_broadband") or 0
    edu = row.get("pct_bachelors_plus") or 0
    urban = row.get("urbanicity_score") or 0
    # Cable-fit: older + higher income + broadband-ready, penalize high urbanicity
    # (cord-cutting rates are higher in dense urban cores)
    cable_raw = (
        norm(age, 30, 55) * 0.30
        + norm(income, 40000, 110000) * 0.30
        + norm(broadband, 40, 95) * 0.25
        + (100 - urban) * 0.15
    )
    # CTV-fit: broadband + younger-skew + education + urbanicity
    ctv_raw = (
        norm(broadband, 50, 95) * 0.35
        + norm(50 - float(age or 0), 0, 20) * 0.20
        + norm(edu, 15, 55) * 0.20
        + urban * 0.25
    )
    return round(cable_raw, 1), round(ctv_raw, 1)


def parse_row(header: list[str], row: list) -> dict:
    rec = dict(zip(header, row))
    def num(key: str) -> float | None:
        v = rec.get(key)
        if v in (None, "", "null"):
            return None
        try:
            f = float(v)
            if f < 0:
                return None
            return f
        except ValueError:
            return None

    edu_denom = num("B15003_001E")
    bachelors = sum(filter(None, [num("B15003_022E"), num("B15003_023E"), num("B15003_024E"), num("B15003_025E")]))
    pct_bach = (bachelors / edu_denom * 100) if edu_denom else None

    pop = num("B01003_001E")
    white = num("B02001_002E")
    black = num("B02001_003E")
    asian = num("B02001_005E")
    hisp = num("B03003_003E")

    bb_denom = num("B28002_001E")
    bb = num("B28002_007E")
    pct_bb = (bb / bb_denom * 100) if bb_denom else None

    hu_denom = num("B25024_001E")
    multi = sum(filter(None, [num("B25024_007E"), num("B25024_008E"), num("B25024_009E"), num("B25024_010E")]))
    pct_multi = (multi / hu_denom * 100) if hu_denom else None

    commute_denom = num("B08301_001E")
    transit = num("B08301_010E")
    pct_transit = (transit / commute_denom * 100) if commute_denom else None

    urbanicity = compute_urbanicity(pct_multi, pct_transit)

    data = {
        "population": int(pop) if pop else None,
        "median_age": num("B01002_001E"),
        "median_hh_income": num("B19013_001E"),
        "pct_bachelors_plus": round(pct_bach, 2) if pct_bach is not None else None,
        "pct_white": round((white / pop * 100), 2) if white and pop else None,
        "pct_black": round((black / pop * 100), 2) if black and pop else None,
        "pct_hispanic": round((hisp / pop * 100), 2) if hisp and pop else None,
        "pct_asian": round((asian / pop * 100), 2) if asian and pop else None,
        "pct_broadband": round(pct_bb, 2) if pct_bb is not None else None,
        "median_home_value": num("B25077_001E"),
        "pct_multiunit": round(pct_multi, 2) if pct_multi is not None else None,
        "pct_transit": round(pct_transit, 2) if pct_transit is not None else None,
        "urbanicity_score": urbanicity,
    }
    cable, ctv = compute_scores(data)
    data["cable_fit_score"] = cable
    data["ctv_fit_score"] = ctv
    return data


def fetch(url: str, params: dict, retries: int = 3) -> list[list]:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 204:
                return []
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return []


def fetch_states(states: list[str], api_key: str | None) -> list[dict]:
    params = {
        "get": f"NAME,{','.join(VARS)}",
        "for": "state:" + ",".join(STATE_FIPS[s] for s in states if s in STATE_FIPS),
    }
    if api_key:
        params["key"] = api_key
    data = fetch(BASE, params)
    if not data:
        return []
    header, *rows = data
    out = []
    for row in rows:
        rec = dict(zip(header, row))
        state = FIPS_TO_STATE.get(rec["state"])
        parsed = parse_row(header, row)
        parsed.update(
            {
                "geo_id": f"state-{state}",
                "geo_type": "state",
                "state": state,
                "district": None,
                "name": rec.get("NAME"),
            }
        )
        out.append(parsed)
    return out


def fetch_congressional_districts(states: list[str], api_key: str | None) -> list[dict]:
    out = []
    for st in states:
        fips = STATE_FIPS.get(st)
        if not fips:
            continue
        params = {
            "get": f"NAME,{','.join(VARS)}",
            "for": "congressional district:*",
            "in": f"state:{fips}",
        }
        if api_key:
            params["key"] = api_key
        try:
            data = fetch(BASE, params)
        except Exception as exc:
            print(f"[warn] {st}: {exc}", file=sys.stderr)
            continue
        if not data:
            continue
        header, *rows = data
        for row in rows:
            rec = dict(zip(header, row))
            cd = rec.get("congressional district")
            parsed = parse_row(header, row)
            parsed.update(
                {
                    "geo_id": f"cd-{st}-{cd}",
                    "geo_type": "cd",
                    "state": st,
                    "district": cd,
                    "name": rec.get("NAME"),
                }
            )
            out.append(parsed)
        time.sleep(0.1)
    return out


def upsert(conn: sqlite3.Connection, rows: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO district_demographics
                (geo_id, geo_type, state, district, name, acs_year, population, median_age,
                 median_hh_income, pct_bachelors_plus, pct_white, pct_black, pct_hispanic,
                 pct_asian, pct_broadband, median_home_value, pct_multiunit, pct_transit,
                 urbanicity_score, cable_fit_score, ctv_fit_score, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(geo_id) DO UPDATE SET
                name=excluded.name, acs_year=excluded.acs_year,
                population=excluded.population, median_age=excluded.median_age,
                median_hh_income=excluded.median_hh_income,
                pct_bachelors_plus=excluded.pct_bachelors_plus,
                pct_white=excluded.pct_white, pct_black=excluded.pct_black,
                pct_hispanic=excluded.pct_hispanic, pct_asian=excluded.pct_asian,
                pct_broadband=excluded.pct_broadband,
                median_home_value=excluded.median_home_value,
                pct_multiunit=excluded.pct_multiunit,
                pct_transit=excluded.pct_transit,
                urbanicity_score=excluded.urbanicity_score,
                cable_fit_score=excluded.cable_fit_score,
                ctv_fit_score=excluded.ctv_fit_score,
                fetched_at=excluded.fetched_at
            """,
            (
                r["geo_id"], r["geo_type"], r["state"], r.get("district"), r.get("name"),
                ACS_YEAR, r.get("population"), r.get("median_age"), r.get("median_hh_income"),
                r.get("pct_bachelors_plus"), r.get("pct_white"), r.get("pct_black"),
                r.get("pct_hispanic"), r.get("pct_asian"), r.get("pct_broadband"),
                r.get("median_home_value"), r.get("pct_multiunit"), r.get("pct_transit"),
                r.get("urbanicity_score"), r.get("cable_fit_score"), r.get("ctv_fit_score"),
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", nargs="*", default=TARGET_STATES)
    parser.add_argument("--api-key", default=None, help="Census API key (optional; env CENSUS_API_KEY)")
    parser.add_argument("--skip-cd", action="store_true", help="Skip congressional districts")
    args = parser.parse_args()

    import os
    api_key = args.api_key or os.environ.get("CENSUS_API_KEY")

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    print(f"Fetching state-level ACS for {len(args.states)} states...")
    state_rows = fetch_states(args.states, api_key)
    print(f"  got {len(state_rows)} state rows")

    cd_rows = []
    if not args.skip_cd:
        print("Fetching congressional-district ACS...")
        cd_rows = fetch_congressional_districts(args.states, api_key)
        print(f"  got {len(cd_rows)} CD rows")

    total = upsert(conn, state_rows + cd_rows)
    conn.close()
    print(json.dumps({"states": len(state_rows), "cds": len(cd_rows), "total": total}))


if __name__ == "__main__":
    main()
