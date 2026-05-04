"""Bulk-load all dashboard source files from a folder or explicit file list."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from build_analysis_report import main as refresh_analysis
from config.settings import DB_PATH
from data.store import Store
from ingest_advertiser_file import ingest as ingest_spend
from ingest_fec_files import detect_kind as detect_fec_kind
from ingest_fec_files import ingest as ingest_fec
from ingest_house_roster import load as ingest_house_roster
from ingest_political_windows import load as ingest_political_windows
from ingest_polls import ingest as ingest_polls


DEFAULT_SOURCE_DIR = Path("/Users/gerritmaxwell/Downloads/Political Spend")


def _headers(path: Path) -> set[str]:
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, nrows=0)
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, nrows=0)
        else:
            return set()
    except Exception:
        return set()
    return {str(column).strip().upper() for column in frame.columns}


def _clean(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _is_spend_like(headers: set[str]) -> bool:
    clean = {_clean(header) for header in headers}
    if "grossspending" in clean:
        return True
    has_media = bool(clean & {"broadcast", "cable", "ctv", "digital", "radio"})
    has_advertiser = any("advertiser" in header for header in clean)
    return has_media and has_advertiser


def detect_kind(path: Path) -> str:
    if detect_fec_kind(path):
        return "fec"
    headers = _headers(path)
    if {"STATE", "REGION", "MARKET/DMA", "WINDOW TYPE", "WINDOW OPEN DATE", "ELECTION DATE"}.issubset(headers):
        return "political_windows"
    if {"STATE", "REGION", "MARKET", "WINDOW TYPE", "WINDOW OPEN DATE", "ELECTION DATE"}.issubset(headers):
        return "political_windows"
    if {"STATE_ABBR", "CDFIPS", "NAME", "LAST_NAME", "PARTY"}.issubset(headers):
        return "house_roster"
    if {"STATE", "DISTRICT", "CANDIDATE_NAME", "PARTY"}.issubset(headers):
        return "house_roster"
    if {"DATE", "RACE", "STATE", "RACE TYPE", "POLLSTER", "CANDIDATE/OPTION 1", "RESULT 1"}.issubset(headers):
        return "polling"
    if path.suffix.lower() in {".xlsx", ".xls", ".csv"} and _is_spend_like(headers):
        return "spend"
    return "unsupported"


def discover_files(source_dir: Path) -> list[Path]:
    suffixes = {".xlsx", ".xls", ".csv", ".txt"}
    return sorted(path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in suffixes)


def sort_key(path: Path) -> tuple[int, str]:
    order = {
        "fec": 0,
        "house_roster": 1,
        "political_windows": 2,
        "polling": 3,
        "spend": 4,
        "unsupported": 99,
    }
    return (order.get(detect_kind(path), 99), path.name.lower())


def ingest_path(path: Path, store: Store) -> dict:
    kind = detect_kind(path)
    if kind == "fec":
        return {"kind": kind, **ingest_fec(path, db_path=DB_PATH)}
    if kind == "house_roster":
        return {"kind": kind, **ingest_house_roster(path)}
    if kind == "political_windows":
        summary = ingest_political_windows(path)
        store.set_metadata("last_political_windows_ingest", pd.Timestamp.utcnow().isoformat())
        return {"kind": kind, **summary}
    if kind == "polling":
        return {"kind": kind, **ingest_polls(path, verbose=False)}
    if kind == "spend":
        return {"kind": kind, **ingest_spend(path, store, verbose=False)}
    return {"kind": kind, "file": path.name, "skipped": True}


def ingest_all(paths: list[Path], refresh: bool = True) -> list[dict]:
    store = Store(DB_PATH)
    results: list[dict] = []
    for path in sorted(paths, key=sort_key):
        try:
            results.append(ingest_path(path, store))
        except Exception as exc:
            results.append({"kind": detect_kind(path), "file": path.name, "error": f"{type(exc).__name__}: {exc}"})
    if refresh:
        refresh_summary = refresh_analysis()
        results.append({"kind": "analysis", "file": "build_analysis_report.py", **(refresh_summary or {})})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path, help="Files to ingest. If omitted, --directory is used.")
    parser.add_argument("--directory", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--no-refresh", action="store_true", help="Skip analysis rebuild after ingest")
    args = parser.parse_args()

    paths = args.paths or discover_files(args.directory)
    results = ingest_all(paths, refresh=not args.no_refresh)
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
