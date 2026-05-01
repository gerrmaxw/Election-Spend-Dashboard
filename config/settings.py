from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Broadcast Waste Analyzer"
DATA_DIR = ROOT / "local_data"
UPLOAD_DIR = DATA_DIR / "uploads"
CACHE_DIR = DATA_DIR / "cache"
CASE_STUDY_DIR = DATA_DIR / "case_studies"
DB_PATH = DATA_DIR / "broadcast_waste_analyzer.sqlite"

TARGET_STATES = [
    "CA",
    "TX",
    "GA",
    "IL",
    "VA",
    "MI",
    "NY",
    "LA",
    "NH",
    "SC",
    "FL",
    "PA",
    "NJ",
    "MD",
    "IN",
    "TN",
    "AR",
    "CO",
    "MN",
    "NM",
    "MS",
    "OR",
    "MA",
    "WA",
    "DC",
    "CT",
    "DE",
    "VT",
    "UT",
]

TV_SPEND_FLOOR = 25_000.0

for directory in (DATA_DIR, UPLOAD_DIR, CACHE_DIR, CASE_STUDY_DIR):
    directory.mkdir(parents=True, exist_ok=True)
