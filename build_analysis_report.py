"""Refresh the SQLite-backed analysis layer for the interactive dashboard.

This script materializes the logical views the Streamlit app reads:
  - analysis_summary
  - analysis_candidate_view
  - analysis_advertiser_view
  - analysis_broadcast_only_view
  - analysis_cable_ctv_only_view
  - analysis_state_coverage
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH
from data.analysis import build_analysis_outputs
from data.store import Store, utc_now


def main() -> None:
    store = Store(DB_PATH)
    stats = build_analysis_outputs(DB_PATH)
    store.set_metadata("last_analysis_refresh", utc_now())
    print(f"Analysis refreshed at {DB_PATH}")
    print(f"  Candidate rows:  {stats['candidate_rows']}")
    print(f"  Advertiser rows: {stats['advertiser_rows']}")


if __name__ == "__main__":
    main()
