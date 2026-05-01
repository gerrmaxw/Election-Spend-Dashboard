from __future__ import annotations

import sqlite3
import subprocess
import sys
import importlib
import html
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from build_analysis_report import main as refresh_analysis
from config.settings import APP_NAME, DB_PATH, TARGET_STATES, TV_SPEND_FLOOR, UPLOAD_DIR
from data.store import Store
from ingest_advertiser_file import ingest
from ingest_house_roster import load as ingest_house_roster
from ingest_political_windows import load as ingest_political_windows
from ingest_polls import ingest as ingest_poll_file
import psi_theme as _psi_theme

importlib.reload(_psi_theme)
from psi_theme import (
    COLORS as PSI_COLORS,
    MEDIA_COLORWAY,
    apply_theme,
    chart_headline,
    chart_methodology,
    insight_callout,
    metric_accent,
    sidebar_logo,
    sidebar_section_label,
)


apply_theme()

STORE = Store(DB_PATH)
TODAY = pd.Timestamp(date.today())

COLORS = {
    "won": "#2ECC71",
    "lost": "#E74C3C",
    "broadcast_only": "#E67E22",
    "cable_ctv_only": "#2980B9",
    "mixed_tv": "#8E44AD",
    "gray": "#B0B7C3",
    "light_win": "#EAF7EF",
    "light_loss": "#FDEDEC",
    "light_broadcast": "#FDF0E6",
    "light_neutral": "#FAFAFA",
    "alert": "#F39C12",
}

OFFICE_LABELS = {
    "governor": "Governor",
    "us_senate": "US Senate",
    "us_house": "US House",
    "president": "President",
    "generic_ballot": "Generic Ballot",
    "national_issue": "National Issue",
    "attorney_general": "Attorney General",
    "state_senate": "State Senate",
    "state_house": "State House",
    "other": "Other",
}

OFFICE_ORDER = {
    "us_house": 0,
    "us_senate": 1,
    "governor": 2,
    "attorney_general": 3,
    "state_senate": 4,
    "state_house": 5,
    "other": 99,
}

TV_MIX_LABELS = {
    "broadcast_only": "Broadcast-Only",
    "cable_only": "Cable Only",
    "ctv_only": "CTV Only",
    "broadcast_ctv": "Broadcast + CTV",
    "broadcast_cable": "Broadcast + Cable",
    "cable_ctv_only": "Cable/CTV-Only",
    "mixed_tv": "Mixed (Broadcast + Cable + CTV)",
    "none": "None",
}

TV_COHORT_OPTIONS = [
    "broadcast_only",
    "cable_only",
    "ctv_only",
    "broadcast_ctv",
    "broadcast_cable",
    "cable_ctv_only",
    "mixed_tv",
]

CABLE_OR_CTV_COHORTS = {
    "cable_only",
    "ctv_only",
    "broadcast_ctv",
    "broadcast_cable",
    "cable_ctv_only",
    "mixed_tv",
}


def classify_tv_cohort(bc: float, ca: float, ct: float) -> str:
    bc = bc or 0.0
    ca = ca or 0.0
    ct = ct or 0.0
    if bc <= 0 and ca <= 0 and ct <= 0:
        return "none"
    has_bc = bc > 0
    has_ca = ca > 0
    has_ct = ct > 0
    if has_bc and not has_ca and not has_ct:
        return "broadcast_only"
    if has_ca and not has_bc and not has_ct:
        return "cable_only"
    if has_ct and not has_bc and not has_ca:
        return "ctv_only"
    if has_bc and has_ct and not has_ca:
        return "broadcast_ctv"
    if has_bc and has_ca and not has_ct:
        return "broadcast_cable"
    if has_ca and has_ct and not has_bc:
        return "cable_ctv_only"
    return "mixed_tv"


def apply_tv_cohort(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not {"broadcast_spend", "cable_spend", "ctv_spend"}.issubset(df.columns):
        return df
    df = df.copy()
    df["tv_mix"] = [
        classify_tv_cohort(bc, ca, ct)
        for bc, ca, ct in zip(
            df["broadcast_spend"].fillna(0),
            df["cable_spend"].fillna(0),
            df["ctv_spend"].fillna(0),
        )
    ]
    df["broadcast_only"] = (df["tv_mix"] == "broadcast_only").astype(int)
    return df

NO_CABLE_TV_LABELS = {
    "broadcast_only": "Broadcast Only",
    "ctv_only": "CTV Only",
    "broadcast_ctv_no_cable": "Broadcast + CTV",
    "none": "None",
}


def run_script(script_name: str, args: list[str] | None = None) -> tuple[bool, str]:
    command = [sys.executable, str(ROOT / script_name)]
    if args:
        command.extend(args)
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
        st.cache_data.clear()
        return True, completed.stdout.strip() or f"Completed {script_name}"
    except subprocess.CalledProcessError as exc:
        return False, (exc.stdout or "") + "\n" + (exc.stderr or "")


@st.cache_data(show_spinner=False)
def load_table(name: str, cache_token: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(f"SELECT * FROM {name}", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def load_spend_aggregates(cache_token: str) -> pd.DataFrame:
    return load_table("spend_aggregates", cache_token)


@st.cache_data(show_spinner=False)
def load_house_roster(cache_token: str) -> pd.DataFrame:
    return load_table("house_roster", cache_token)


def _file_headers(path: Path) -> set[str]:
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, nrows=0)
        else:
            frame = pd.read_excel(path, nrows=0)
    except Exception:
        return set()
    return {str(column).strip().upper() for column in frame.columns}


def ingest_uploaded_data_file(path: Path) -> dict:
    headers = _file_headers(path)
    if {"STATE", "REGION", "MARKET/DMA", "WINDOW TYPE", "WINDOW OPEN DATE", "ELECTION DATE"}.issubset(headers):
        summary = ingest_political_windows(path)
        STORE.set_metadata("last_political_windows_ingest", pd.Timestamp.utcnow().isoformat())
        return {"file": path.name, "kind": "political_windows", **summary}
    if {"STATE_ABBR", "CDFIPS", "NAME", "LAST_NAME", "PARTY"}.issubset(headers):
        summary = ingest_house_roster(path)
        return {"file": path.name, "kind": "house_roster", **summary}
    if {"DATE", "RACE", "STATE", "RACE TYPE", "POLLSTER", "CANDIDATE/OPTION 1", "RESULT 1"}.issubset(headers):
        summary = ingest_poll_file(path, verbose=False)
        STORE.set_metadata("last_polling_ingest", pd.Timestamp.utcnow().isoformat())
        return {"file": path.name, "kind": "polling", **summary}
    summary = ingest(path, STORE, verbose=False)
    return {"file": path.name, "kind": summary.get("source_data_type", "spend"), **summary}


def format_currency(value: float | int | None, digits: int = 0) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"${float(value):,.{digits}f}"


def format_compact_currency(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    amount = float(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000_000:
        return f"{sign}${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.1f}K"
    return f"{sign}${amount:,.0f}"


def format_pct(value: float | int | None, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:,.{digits}f}%"


def format_int(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{int(value):,}"


def format_timestamp(value: str | None) -> str:
    if not value or value == "—":
        return "—"
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M")


def csv_download(df: pd.DataFrame, label: str, file_name: str) -> None:
    st.download_button(
        label,
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=file_name,
        mime="text/csv",
    )


def office_label(value: str | None) -> str:
    return OFFICE_LABELS.get(str(value or ""), str(value or "").replace("_", " ").title())


def sort_offices(values: list[str]) -> list[str]:
    return sorted(values, key=lambda value: (OFFICE_ORDER.get(str(value), 50), office_label(value)))


def tv_mix_label(value: str | None) -> str:
    return TV_MIX_LABELS.get(str(value or ""), str(value or "").replace("_", " ").title())


def no_cable_tv_label(value: str | None) -> str:
    return NO_CABLE_TV_LABELS.get(str(value or ""), str(value or "").replace("_", " ").title())


def coerce_dates(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(frame[column], errors="coerce")


def compute_log_x(frame: pd.DataFrame, column: str) -> bool:
    values = frame.loc[frame[column] > 0, column]
    if values.empty:
        return False
    return values.max() / max(values.min(), 1) >= 10


def style_result_table(frame: pd.DataFrame, result_col: str = "result", cohort_col: str | None = None):
    def _row_style(row: pd.Series) -> list[str]:
        background = COLORS["light_neutral"]
        result = str(row.get(result_col, "")).lower()
        if result == "won":
            background = COLORS["light_win"]
        elif result == "lost":
            background = COLORS["light_loss"]
        if cohort_col and str(row.get(cohort_col, "")) == "broadcast_only":
            background = COLORS["light_broadcast"]
        return [f"background-color: {background}"] * len(row)

    return frame.style.apply(_row_style, axis=1)


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)


def kpi_row(items: list[tuple], max_cols: int | None = None) -> None:
    """Render a row of KPI metrics. Each item may be:
       (label, value, delta)                — plain metric
       (label, value, delta, accent_key)    — metric with PSI top-border accent.
    accent_key ∈ {broadcast, cable, ctv, digital, positive, negative}.
    """
    col_count = max_cols or (4 if len(items) > 4 else len(items))
    col_count = max(1, min(col_count, len(items)))
    for start in range(0, len(items), col_count):
        row_items = items[start : start + col_count]
        cols = st.columns(len(row_items))
        for col, item in zip(cols, row_items):
            label, value, delta = item[0], item[1], item[2]
            accent = item[3] if len(item) >= 4 else None
            accent_class = f" psi-kpi-{accent}" if accent else ""
            value_html = html.escape(str(value))
            delta_html = ""
            if delta is not None:
                delta_html = f'<div class="psi-kpi-delta">{html.escape(str(delta))}</div>'
            with col:
                st.markdown(
                    f"""
                    <div class="psi-kpi-card{accent_class}">
                        <div class="psi-kpi-label">{html.escape(str(label))}</div>
                        <div class="psi-kpi-value">{value_html}</div>
                        {delta_html}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


def upcoming_election_dates(races_df: pd.DataFrame) -> pd.DataFrame:
    if races_df.empty:
        return pd.DataFrame()
    frame = races_df.copy()
    frame["primary_date"] = coerce_dates(frame, "primary_date")
    frame = frame[frame["primary_date"] >= TODAY].copy()
    if frame.empty:
        return frame
    grouped = (
        frame.groupby(["state", "primary_date"], dropna=False)
        .agg(
            races=("race_id", "nunique"),
            offices=("office", lambda values: ", ".join(sorted({office_label(v) for v in values if v})[:4])),
            example_races=("race_name", lambda values: ", ".join(list(pd.Series(values).dropna().astype(str).unique())[:3])),
        )
        .reset_index()
        .sort_values(["primary_date", "state"])
    )
    grouped["primary_date"] = grouped["primary_date"].dt.strftime("%Y-%m-%d")
    return grouped


def ensure_filter_state(candidate_df: pd.DataFrame, upcoming_df: pd.DataFrame) -> None:
    all_states = sorted(
        {
            *[v for v in candidate_df.get("state", pd.Series(dtype=str)).dropna().unique() if v],
            *[v for v in upcoming_df.get("state", pd.Series(dtype=str)).dropna().unique() if v],
        }
    )
    all_offices = sorted(
        {
            *[v for v in candidate_df.get("office", pd.Series(dtype=str)).dropna().unique() if v],
            *[v for v in upcoming_df.get("office", pd.Series(dtype=str)).dropna().unique() if v],
        }
    )
    dates = pd.concat(
        [
            coerce_dates(candidate_df, "primary_date"),
            coerce_dates(upcoming_df, "primary_date"),
        ],
        ignore_index=True,
    ).dropna()
    defaults = {
        "filter_states": ["All"],
        "filter_offices": ["us_house"] if "us_house" in all_offices else ["All"],
        "filter_party": "All",
        "filter_timing": "All",
        "filter_tv_mix": "All",
        "filter_tv_spend_floor": int(TV_SPEND_FLOOR),
        "filter_date_range": (
            dates.min().date() if not dates.empty else date(2026, 1, 1),
            dates.max().date() if not dates.empty else date(2026, 12, 31),
        ),
        "filter_state_options": all_states,
        "filter_office_options": all_offices,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_global_filters(candidate_df: pd.DataFrame, upcoming_df: pd.DataFrame) -> dict[str, object]:
    ensure_filter_state(candidate_df, upcoming_df)
    st.sidebar.header("Filters")

    state_options = ["All"] + sorted(st.session_state.get("filter_state_options", []))
    office_options = ["All"] + sort_offices(list(st.session_state.get("filter_office_options", [])))
    party_options = ["All", "Democratic", "Republican", "Other"]
    timing_options = ["All", "Completed Primaries", "Upcoming Primaries", "Next 90 Days"]
    tv_mix_options = ["All", *TV_COHORT_OPTIONS]

    selected_states = st.sidebar.multiselect("State", state_options, key="filter_states")
    selected_offices = st.sidebar.multiselect(
        "Office",
        office_options,
        format_func=office_label,
        key="filter_offices",
    )
    party = st.sidebar.selectbox("Party", party_options, key="filter_party")
    timing = st.sidebar.selectbox("Primary Timing", timing_options, key="filter_timing")
    tv_mix = st.sidebar.selectbox(
        "TV Cohort",
        tv_mix_options,
        format_func=lambda value: value if value == "All" else tv_mix_label(value),
        key="filter_tv_mix",
    )

    all_dates = pd.concat(
        [
            coerce_dates(candidate_df, "primary_date"),
            coerce_dates(upcoming_df, "primary_date"),
        ],
        ignore_index=True,
    ).dropna()
    if not all_dates.empty:
        st.sidebar.date_input(
            "Primary Date Range",
            min_value=all_dates.min().date(),
            max_value=all_dates.max().date(),
            key="filter_date_range",
        )

    st.sidebar.slider(
        "TV Spend Floor",
        min_value=0,
        max_value=250000,
        step=5000,
        key="filter_tv_spend_floor",
    )

    if "filter_require_spend" not in st.session_state:
        st.session_state["filter_require_spend"] = True
    st.sidebar.checkbox(
        "Only candidates with ad spend",
        key="filter_require_spend",
        help="Hide candidates/races that have $0 in attributed advertising spend.",
    )

    return {
        "states": selected_states,
        "offices": selected_offices,
        "party": party,
        "timing": timing,
        "tv_mix": tv_mix,
        "date_range": st.session_state["filter_date_range"],
        "tv_spend_floor": float(st.session_state["filter_tv_spend_floor"]),
        "require_spend": bool(st.session_state["filter_require_spend"]),
    }


def apply_timing_filter(frame: pd.DataFrame, filters: dict[str, object], allow_null_dates: bool = False) -> pd.DataFrame:
    if "primary_date" not in frame.columns:
        return frame
    filtered = frame.copy()
    dates = coerce_dates(filtered, "primary_date")
    start_date, end_date = filters["date_range"]
    date_mask = (dates >= pd.to_datetime(start_date)) & (dates <= pd.to_datetime(end_date))
    if allow_null_dates:
        date_mask = date_mask | dates.isna()
    filtered = filtered[date_mask]

    timing = str(filters["timing"])
    if timing == "Completed Primaries":
        mask = dates < TODAY
        filtered = filtered[mask | (dates.isna() if allow_null_dates else False)]
    elif timing == "Upcoming Primaries":
        filtered = filtered[dates >= TODAY]
    elif timing == "Next 90 Days":
        filtered = filtered[(dates >= TODAY) & (dates <= TODAY + pd.Timedelta(days=90))]
    return filtered


def apply_candidate_filters(
    frame: pd.DataFrame,
    filters: dict[str, object],
    *,
    apply_tv_floor: bool = False,
) -> pd.DataFrame:
    filtered = frame.copy()
    states = filters["states"]
    offices = filters["offices"]
    if "All" not in states:
        filtered = filtered[filtered["state"].isin(states)]
    if "All" not in offices:
        filtered = filtered[filtered["office"].isin(offices)]

    party = str(filters["party"])
    if party == "Other":
        filtered = filtered[~filtered["party"].isin(["Democratic", "Republican", "", None])]
    elif party != "All":
        filtered = filtered[filtered["party"] == party]

    filtered = apply_timing_filter(filtered, filters)

    tv_mix = str(filters["tv_mix"])
    if tv_mix != "All":
        filtered = filtered[filtered["tv_mix"] == tv_mix]

    if apply_tv_floor and "tv_spend" in filtered.columns:
        filtered = filtered[filtered["tv_spend"] >= float(filters["tv_spend_floor"])]
    if filters.get("require_spend") and "total_attributed_spend" in filtered.columns:
        filtered = filtered[filtered["total_attributed_spend"] > 0]
    return filtered


def apply_advertiser_filters(
    frame: pd.DataFrame,
    filters: dict[str, object],
    allow_null_dates: bool = True,
    *,
    apply_tv_floor: bool = False,
) -> pd.DataFrame:
    filtered = frame.copy()
    states = filters["states"]
    offices = filters["offices"]
    if "All" not in states:
        filtered = filtered[filtered["state"].isin(states)]
    if "All" not in offices:
        filtered = filtered[filtered["office"].isin(offices)]

    party = str(filters["party"])
    if "party" in filtered.columns:
        if party == "Other":
            filtered = filtered[~filtered["party"].isin(["Democratic", "Republican", "", None])]
        elif party != "All":
            filtered = filtered[filtered["party"] == party]

    filtered = apply_timing_filter(filtered, filters, allow_null_dates=allow_null_dates)

    tv_mix = str(filters["tv_mix"])
    if tv_mix != "All" and "tv_mix" in filtered.columns:
        filtered = filtered[filtered["tv_mix"] == tv_mix]

    if apply_tv_floor and "tv_spend" in filtered.columns:
        filtered = filtered[filtered["tv_spend"] >= float(filters["tv_spend_floor"])]
    if filters.get("require_spend") and "total_spend" in filtered.columns:
        filtered = filtered[filtered["total_spend"] > 0]
    return filtered


def apply_upcoming_filters(frame: pd.DataFrame, filters: dict[str, object]) -> pd.DataFrame:
    filtered = frame.copy()
    states = filters["states"]
    offices = filters["offices"]
    if "All" not in states:
        filtered = filtered[filtered["state"].isin(states)]
    if "All" not in offices:
        filtered = filtered[filtered["office"].isin(offices)]
    filtered = apply_timing_filter(filtered, filters)
    tv_mix = str(filters["tv_mix"])
    if tv_mix != "All":
        filtered = filtered[filtered["top_tv_mix"] == tv_mix]
    return filtered


def completed_primaries(candidate_df: pd.DataFrame) -> pd.DataFrame:
    dates = coerce_dates(candidate_df, "primary_date")
    return candidate_df[dates < TODAY].copy()


def upcoming_candidates(candidate_df: pd.DataFrame) -> pd.DataFrame:
    dates = coerce_dates(candidate_df, "primary_date")
    return candidate_df[dates >= TODAY].copy()


def page_setup(
    filters: dict[str, object],
    summary_df: pd.DataFrame,
    coverage_df: pd.DataFrame,
    races_df: pd.DataFrame,
    candidate_df: pd.DataFrame,
    metadata: dict[str, str],
) -> None:
    page_header(
        "Setup / Data Health",
        "Check pipeline freshness, match coverage, and state-level readiness before a demo.",
    )
    total_advertisers = int(summary_df.iloc[0]["total_advertisers"]) if not summary_df.empty else 0
    matched_advertisers = int(summary_df.iloc[0]["matched_advertisers"]) if not summary_df.empty else 0
    match_rate = matched_advertisers / total_advertisers if total_advertisers else 0
    states_loaded = int((coverage_df["races"] > 0).sum()) if not coverage_df.empty else 0

    kpi_row(
        [
            ("Total Advertisers Ingested", format_int(total_advertisers), None, "ctv"),
            ("Advertisers Matched to Candidates", format_int(matched_advertisers), None, "ctv"),
            ("Match Rate", format_pct(match_rate), None, "positive" if match_rate >= 0.5 else "negative"),
            ("States With CivicAPI Data", f"{states_loaded}/{len(TARGET_STATES)}", None, "broadcast"),
            ("Last Data Refresh", format_timestamp(metadata.get("last_analysis_refresh")), None, "broadcast"),
        ]
    )

    insight_callout(
        "<b>House districts are subsets of DMAs.</b> Cable + CTV geo-targeting hits "
        "voters who can actually choose this candidate — broadcast spillover does not.",
        label="Pitch Premise",
    )

    st.subheader("US House 2026 Focus")
    house_df = candidate_df[candidate_df.get("office") == "us_house"] if "office" in candidate_df.columns else candidate_df.iloc[0:0]
    house_races = house_df["race_id"].nunique() if not house_df.empty else 0
    house_spending_races = (
        house_df[house_df["total_attributed_spend"] > 0]["race_id"].nunique()
        if not house_df.empty and "total_attributed_spend" in house_df.columns
        else 0
    )
    battleground_count = 0
    try:
        with sqlite3.connect(DB_PATH) as conn:
            battleground_count = conn.execute(
                "SELECT COUNT(*) FROM party_registration "
                "WHERE district IS NOT NULL AND partisan_lean_d_minus_r IS NOT NULL "
                "AND ABS(partisan_lean_d_minus_r) <= 5"
            ).fetchone()[0]
    except sqlite3.DatabaseError:
        pass
    kpi_row(
        [
            ("House Races Loaded", format_int(house_races), None),
            ("House Races With Ad Spend", format_int(house_spending_races), None),
            ("Battleground CDs (|2024 margin| ≤ 5pp)", format_int(battleground_count), None),
        ]
    )

    try:
        with sqlite3.connect(DB_PATH) as conn:
            def _safe_count(table: str) -> int:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
                    (table,),
                ).fetchone()
                if not exists:
                    return 0
                return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

            spend_sources = pd.read_sql_query(
                """
                SELECT COALESCE(NULLIF(source_data_type, ''), 'legacy_advertiser') AS data_type,
                       COUNT(DISTINCT source_file) AS files,
                       COUNT(*) AS records,
                       SUM(gross_amount) AS spend
                FROM spend_records
                GROUP BY COALESCE(NULLIF(source_data_type, ''), 'legacy_advertiser')
                ORDER BY spend DESC
                """,
                conn,
            )
            aggregate_count = _safe_count("spend_aggregates")
            window_count = _safe_count("political_windows")
            roster_count = _safe_count("house_roster")
            poll_count = _safe_count("polls")
    except Exception:
        spend_sources = pd.DataFrame()
        aggregate_count = window_count = roster_count = poll_count = 0

    st.subheader("Data Source Status")
    kpi_row(
        [
            ("Advertiser Spend Records", format_int(total_advertisers), format_timestamp(metadata.get("last_advertiser_ingest")), "ctv"),
            ("State / Market Aggregate Rows", format_int(aggregate_count), format_timestamp(metadata.get("last_spend_aggregate_ingest")), "digital"),
            ("Political Window Rows", format_int(window_count), format_timestamp(metadata.get("last_political_windows_ingest", metadata.get("last_analysis_refresh"))), "broadcast"),
            ("House Roster Districts", format_int(roster_count), format_timestamp(metadata.get("last_house_roster_ingest")), "positive"),
            ("Polling Rows", format_int(poll_count), format_timestamp(metadata.get("last_polling_ingest")), "cable"),
        ]
    )
    if not spend_sources.empty:
        source_view = spend_sources.rename(
            columns={
                "data_type": "Data Type",
                "files": "Files",
                "records": "Spend Records",
                "spend": "Gross Spend",
            }
        )
        st.dataframe(
            source_view,
            width="stretch",
            hide_index=True,
            column_config={"Gross Spend": st.column_config.NumberColumn(format="$%.0f")},
        )

    top_left, top_right = st.columns([1.0, 1.2])
    if not coverage_df.empty:
        coverage_plot = coverage_df.copy()
        coverage_plot["coverage_score"] = coverage_plot["status"].map({"Needs Re-fetch": 0, "Partial": 1, "Full": 2})
        with top_left:
            fig = px.choropleth(
                coverage_plot,
                locations="state",
                locationmode="USA-states",
                scope="usa",
                color="coverage_score",
                color_continuous_scale=["#D5DBDB", "#85C1E9", "#1F618D"],
                hover_data={"races": True, "candidates": True, "spend_records": True, "ies_linked": True, "coverage_score": False},
                title="Coverage by State",
            )
            fig.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=0, t=48, b=0))
            st.plotly_chart(fig, use_container_width=True)

        with top_right:
            state_table = coverage_df.rename(
                columns={
                    "state": "State",
                    "races": "Races",
                    "candidates": "Candidates",
                    "spend_records": "Spend Records",
                    "ies_linked": "IEs Linked",
                    "status": "Status",
                }
            )
            st.subheader("Coverage Table")
            st.dataframe(state_table, width="stretch", hide_index=True)

    st.subheader("Upcoming 2026 Election Dates")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            sched = pd.read_sql_query(
                "SELECT state, primary_date, runoff_date, has_us_senate, "
                "us_house_seats, us_house_note, general_date "
                "FROM state_primary_schedule WHERE state IN ({})".format(
                    ",".join("?" * len(TARGET_STATES))
                ),
                conn, params=TARGET_STATES,
            )
    except Exception:
        sched = pd.DataFrame()

    if sched.empty:
        st.info("No 2026 primary calendar loaded. Run `ingest_primary_schedule.py`.")
    else:
        today_str = TODAY.strftime("%Y-%m-%d")
        events = []
        for _, r in sched.iterrows():
            if r["primary_date"] and r["primary_date"] >= today_str:
                events.append({"State": r["state"], "Election Date": r["primary_date"], "Type": "Primary"})
            if r["runoff_date"] and r["runoff_date"] >= today_str:
                events.append({"State": r["state"], "Election Date": r["runoff_date"], "Type": "Runoff"})
            if r["general_date"] and r["general_date"] >= today_str:
                events.append({"State": r["state"], "Election Date": r["general_date"], "Type": "General"})
        events_df = pd.DataFrame(events).sort_values(["Election Date", "State"]).reset_index(drop=True)

        covered_states = sorted(sched[sched["primary_date"].notna() & (sched["primary_date"] >= today_str)]["state"].unique())
        missing_states = [s for s in TARGET_STATES if s not in covered_states]
        next_date = events_df["Election Date"].iloc[0] if not events_df.empty else "—"
        kpi_row([
            ("Upcoming Elections", format_int(len(events_df)), None),
            ("Target States w/ Upcoming Primary", format_int(len(covered_states)), None),
            ("Next Election Date", next_date, None),
            ("Target States Already Past Primary", format_int(len(missing_states)), None),
        ])
        if missing_states:
            st.caption(f"Primary already occurred (or not scheduled) for: {', '.join(missing_states)}")

        sched_view = sched.copy()
        sched_view["U.S. Senate"] = sched_view["has_us_senate"].map(lambda v: "Yes" if v == 1 else ("No" if v == 0 else "—"))
        sched_view["U.S. House Seats"] = sched_view.apply(
            lambda r: str(int(r["us_house_seats"])) if pd.notna(r["us_house_seats"]) else (r["us_house_note"] or "—"),
            axis=1,
        )
        sched_view = sched_view.rename(columns={
            "state": "State",
            "primary_date": "Primary",
            "runoff_date": "Runoff",
            "general_date": "General",
        })[["State", "Primary", "Runoff", "U.S. Senate", "U.S. House Seats", "General"]].fillna("—")
        st.dataframe(sched_view, width="stretch", hide_index=True)
        csv_download(sched_view, "Download 2026 Election Dates", "upcoming_2026_election_dates.csv")

    st.subheader("File Upload + Refresh")
    upload_files = st.file_uploader(
        "Upload spend, market-calendar, or House roster Excel files",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
    )
    if st.button("Ingest Uploaded Files", type="primary", disabled=not upload_files):
        with st.spinner("Ingesting advertiser files and rebuilding the analysis layer..."):
            results = []
            for uploaded in upload_files or []:
                destination = UPLOAD_DIR / uploaded.name
                destination.write_bytes(uploaded.getbuffer())
                results.append(ingest_uploaded_data_file(destination))
            refresh_analysis()
            st.cache_data.clear()
        result_df = pd.DataFrame(results)
        st.success("Ingest complete. Analysis views refreshed.")
        st.dataframe(result_df, width="stretch", hide_index=True)
        csv_download(result_df, "Download Recent Uploads", "recent_uploads.csv")

    empty_or_partial_states = (
        coverage_df[coverage_df["status"].isin(["Needs Re-fetch", "Partial"])]["state"].tolist()
        if not coverage_df.empty
        else TARGET_STATES
    )
    button_col1, button_col2, button_col3 = st.columns(3)
    if button_col1.button("Re-fetch CivicAPI for Empty/Partial States"):
        args = ["--states", *empty_or_partial_states, "--include-primaries"]
        with st.spinner("Refreshing CivicAPI for the selected states..."):
            ok, output = run_script("fetch_civicapi_2026.py", args)
        st.success(output) if ok else st.error(output)
    if button_col2.button("Re-fetch FEC IEs"):
        args = ["--states", *TARGET_STATES]
        with st.spinner("Refreshing FEC IE linkages..."):
            ok, output = run_script("fetch_fec_ie.py", args)
        st.success(output) if ok else st.error(output)
    if button_col3.button("Re-run Analysis"):
        with st.spinner("Rebuilding analysis views..."):
            ok, output = run_script("build_analysis_report.py")
        st.success(output) if ok else st.error(output)

    refresh_table = pd.DataFrame(
        [
            {"Source": "Advertiser Ingest", "Timestamp": metadata.get("last_advertiser_ingest", "—")},
            {"Source": "CivicAPI", "Timestamp": metadata.get("last_civicapi_refresh", "—")},
            {"Source": "FEC IE", "Timestamp": metadata.get("last_fec_refresh", "—")},
            {"Source": "Polling", "Timestamp": metadata.get("last_polling_ingest", "—")},
            {"Source": "Analysis", "Timestamp": metadata.get("last_analysis_refresh", "—")},
            {"Source": "Last Uploaded File", "Timestamp": metadata.get("last_uploaded_file", "—")},
        ]
    )
    st.subheader("Refresh Log")
    st.dataframe(refresh_table, width="stretch", hide_index=True)


def page_completed_primaries(candidate_df: pd.DataFrame, filters: dict[str, object]) -> None:
    base = completed_primaries(candidate_df)
    filtered = apply_candidate_filters(base, filters, apply_tv_floor=False)
    page_header(
        "Completed Primaries & Cost-Per-Vote",
        "For decided races, compare TV mix, outcome, and cost per vote across broadcast-only, Cable/CTV-only, and mixed TV campaigns.",
    )
    if filtered.empty:
        st.info("No completed primaries match the current filters.")
        return

    winners = filtered[filtered["result"] == "won"]
    losers = filtered[filtered["result"] == "lost"]
    broadcast = filtered[filtered["broadcast_only"] == 1]
    cable_ctv = filtered[filtered["tv_mix"] == "cable_ctv_only"]
    mixed = filtered[filtered["tv_mix"] == "mixed_tv"]
    matched_spend_count = int(filtered[filtered["advertiser_count"] > 0].shape[0]) if "advertiser_count" in filtered.columns else int(filtered.shape[0])

    cable_in_mix = filtered[filtered["tv_mix"].isin(CABLE_OR_CTV_COHORTS)]
    kpi_row(
        [
            ("Winners Median TV $/Vote", format_currency(winners["tv_per_vote"].median(), 2), None),
            ("Losers Median TV $/Vote", format_currency(losers["tv_per_vote"].median(), 2), None),
            ("Broadcast-Only Win Rate", format_pct(broadcast["result"].eq("won").mean() if not broadcast.empty else None), None),
            ("Cable/CTV-in-Mix Win Rate", format_pct(cable_in_mix["result"].eq("won").mean() if not cable_in_mix.empty else None), None),
            ("Completed Primaries With Matched Spend", format_int(matched_spend_count), None),
        ]
    )

    cohort_order = TV_COHORT_OPTIONS
    cohort_rate = (
        filtered[filtered["tv_mix"].isin(cohort_order)]
        .groupby("tv_mix", dropna=False)
        .agg(wins=("result", lambda values: int((values == "won").sum())), total=("candidate_id", "nunique"))
        .reset_index()
    )
    cohort_rate["losses"] = cohort_rate["total"] - cohort_rate["wins"]
    cohort_rate["win_rate"] = cohort_rate["wins"] / cohort_rate["total"]
    cohort_rate["loss_rate"] = cohort_rate["losses"] / cohort_rate["total"]
    long_rate = cohort_rate.melt(
        id_vars="tv_mix",
        value_vars=["win_rate", "loss_rate"],
        var_name="outcome",
        value_name="rate",
    )
    long_rate["Outcome"] = long_rate["outcome"].map({"win_rate": "Win %", "loss_rate": "Loss %"})
    long_rate["TV Cohort"] = long_rate["tv_mix"].map(tv_mix_label)

    chart_row = st.columns(2)
    with chart_row[0]:
        grouped = px.bar(
            long_rate,
            x="TV Cohort",
            y="rate",
            color="Outcome",
            barmode="group",
            color_discrete_map={"Win %": COLORS["won"], "Loss %": COLORS["lost"]},
            title="Win Rate by TV Mix Cohort",
        )
        grouped.update_layout(yaxis_tickformat=".0%", margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(grouped, use_container_width=True)

    with chart_row[1]:
        box_source = filtered[filtered["tv_mix"].isin(cohort_order)].copy()
        box_source["TV Cohort"] = box_source["tv_mix"].map(tv_mix_label)
        box = px.box(
            box_source,
            x="TV Cohort",
            y="tv_per_vote",
            color="TV Cohort",
            color_discrete_map={
                "Broadcast-Only": COLORS["broadcast_only"],
                "Cable/CTV-Only": COLORS["cable_ctv_only"],
                "Mixed": COLORS["mixed_tv"],
            },
            title="TV $/Vote by TV Cohort",
        )
        box.update_layout(showlegend=False, margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(box, use_container_width=True)

    scatter_source = filtered[filtered["tv_spend"] > 0].copy()
    scatter_source["TV Cohort"] = scatter_source["tv_mix"].map(tv_mix_label)
    scatter = px.scatter(
        scatter_source,
        x="tv_spend",
        y="vote_share",
        color="TV Cohort",
        symbol="result",
        size="total_attributed_spend",
        hover_name="candidate_name",
        hover_data=["state", "office", "district", "broadcast_spend", "cable_spend", "ctv_spend"],
        title="TV Spend vs Vote Share",
        color_discrete_map={
            "Broadcast-Only": COLORS["broadcast_only"],
            "Cable/CTV-Only": COLORS["cable_ctv_only"],
            "Mixed": COLORS["mixed_tv"],
        },
        log_x=compute_log_x(scatter_source, "tv_spend"),
    )
    scatter.update_layout(margin=dict(l=0, r=0, t=48, b=0))
    st.plotly_chart(scatter, use_container_width=True)

    detail = filtered[
        [
            "candidate_name",
            "state",
            "office",
            "district",
            "tv_mix",
            "broadcast_spend",
            "cable_spend",
            "ctv_spend",
            "digital_spend",
            "radio_spend",
            "tv_spend",
            "tv_per_vote",
            "result",
            "votes",
            "vote_share",
            "margin",
            "advertiser_count",
            "advertiser_list",
        ]
    ].copy()
    detail["office"] = detail["office"].map(office_label)
    detail["tv_mix"] = detail["tv_mix"].map(tv_mix_label)
    detail.rename(
        columns={
            "candidate_name": "Candidate",
            "state": "State",
            "office": "Office",
            "district": "District",
            "tv_mix": "TV Cohort",
            "broadcast_spend": "Broadcast $",
            "cable_spend": "Cable $",
            "ctv_spend": "CTV $",
            "digital_spend": "Digital $",
            "radio_spend": "Radio $",
            "tv_spend": "TV $",
            "tv_per_vote": "TV $/Vote",
            "result": "Result",
            "votes": "Votes",
            "vote_share": "Vote Share",
            "margin": "Margin",
            "advertiser_count": "# Advertisers",
            "advertiser_list": "Advertisers",
        },
        inplace=True,
    )
    st.subheader("Completed Primaries Detail")
    csv_download(detail, "Download Completed Primaries", "completed_primaries_cost_per_vote.csv")
    st.dataframe(style_result_table(detail, result_col="Result"), width="stretch", hide_index=True)


def page_broadcast_waste(
    candidate_df: pd.DataFrame,
    broadcast_df: pd.DataFrame,
    cable_df: pd.DataFrame,
    filters: dict[str, object],
) -> None:
    filtered_candidates = apply_candidate_filters(completed_primaries(candidate_df), filters, apply_tv_floor=True)
    filtered_broadcast = apply_advertiser_filters(broadcast_df, filters, allow_null_dates=True, apply_tv_floor=True)
    filtered_cable = apply_advertiser_filters(cable_df, filters, allow_null_dates=True, apply_tv_floor=True)

    page_header(
        "Broadcast Waste",
        "Isolate campaigns that skipped Cable entirely (Broadcast / CTV / Radio / Digital only) and compare them with Cable-in-mix winners. House races show the sharpest waste because districts are subsets of DMAs.",
    )

    cable_free = filtered_candidates.copy()
    cable_free["cable_spend_filled"] = cable_free.get("cable_spend", 0).fillna(0)
    cable_free["non_cable_spend"] = (
        cable_free.get("broadcast_spend", 0).fillna(0)
        + cable_free.get("ctv_spend", 0).fillna(0)
        + cable_free.get("radio_spend", 0).fillna(0)
        + cable_free.get("digital_spend", 0).fillna(0)
    )
    broadcast_candidates = cable_free[
        (cable_free["cable_spend_filled"] == 0) & (cable_free["non_cable_spend"] > 0)
    ].copy()
    cable_candidates = filtered_candidates[filtered_candidates.get("cable_spend", 0).fillna(0) > 0]

    loss_rate = 1 - broadcast_candidates["result"].eq("won").mean() if not broadcast_candidates.empty else None
    st.markdown(
        f"""
        <div style="background:{COLORS['alert']}22;border-left:6px solid {COLORS['alert']};padding:16px 18px;border-radius:6px;margin-bottom:16px;">
        <strong>{format_pct(loss_rate) if loss_rate is not None else "—"} of cable-free candidates (Broadcast / CTV / Radio / Digital, no Cable) lost their primary.</strong><br/>
        These campaigns spent {format_currency(broadcast_candidates['non_cable_spend'].sum())} across non-cable channels with zero Cable.
        </div>
        """,
        unsafe_allow_html=True,
    )

    kpi_row(
        [
            ("Cable-Free Candidates (TV ≥ 25K)", format_int(broadcast_candidates.shape[0]), None, "broadcast"),
            ("Cable-Free Win Rate", format_pct(broadcast_candidates["result"].eq("won").mean() if not broadcast_candidates.empty else None), None, "negative"),
            ("Cable-in-Mix Win Rate", format_pct(cable_candidates["result"].eq("won").mean() if not cable_candidates.empty else None), None, "positive"),
            ("Cable-Free Non-Cable Spend", format_currency(broadcast_candidates["non_cable_spend"].sum()), None, "cable"),
        ]
    )

    chart_row = st.columns(2)
    if not broadcast_candidates.empty:
        with chart_row[0]:
            bar = px.bar(
                broadcast_candidates.sort_values("non_cable_spend", ascending=True),
                x="non_cable_spend",
                y="candidate_name",
                orientation="h",
                color="result",
                color_discrete_map={"won": COLORS["won"], "lost": COLORS["lost"]},
                hover_data=["state", "office", "district", "votes", "broadcast_spend", "ctv_spend", "digital_spend", "radio_spend"],
                title="Cable-Free Candidates by Non-Cable Spend",
            )
            bar.update_layout(margin=dict(l=0, r=0, t=48, b=0))
            st.plotly_chart(bar, use_container_width=True)

        with chart_row[1]:
            donut = px.pie(
                broadcast_candidates.assign(Outcome=broadcast_candidates["result"].str.title()),
                names="Outcome",
                color="Outcome",
                hole=0.55,
                title="Win vs Loss Among Cable-Free Candidates",
                color_discrete_map={"Won": COLORS["won"], "Lost": COLORS["lost"]},
            )
            donut.update_layout(margin=dict(l=0, r=0, t=48, b=0))
            st.plotly_chart(donut, use_container_width=True)
    else:
        st.info("No cable-free candidates meet the current filters.")

    if not broadcast_candidates.empty:
        state_rollup = (
            broadcast_candidates.groupby("state", dropna=False)
            .agg(
                total_non_cable_spend=("non_cable_spend", "sum"),
                cable_free_candidates=("candidate_name", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            )
            .reset_index()
        )
        state_map = px.choropleth(
            state_rollup,
            locations="state",
            locationmode="USA-states",
            scope="usa",
            color="total_non_cable_spend",
            hover_data=["cable_free_candidates"],
            title="Cable-Free Non-Cable Spend by State",
            color_continuous_scale=["#FDF2E9", "#E67E22", "#A04000"],
        )
        state_map.update_layout(margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(state_map, use_container_width=True)

    st.subheader("Broadcast or CTV Without Cable")
    st.caption("This cohort includes candidates using Broadcast only, CTV only, or Broadcast + CTV, with zero Cable.")
    no_cable_candidates = filtered_candidates[
        (filtered_candidates["no_cable_tv"] == 1) & (filtered_candidates["tv_spend"] >= float(filters["tv_spend_floor"]))
    ].copy()
    if no_cable_candidates.empty:
        st.info("No no-cable TV candidates meet the current filters.")
    else:
        no_cable_candidates["No-Cable TV Type"] = no_cable_candidates["no_cable_tv_type"].map(no_cable_tv_label)
        kpi_row(
            [
                ("No-Cable TV Candidates", format_int(no_cable_candidates.shape[0]), None),
                ("Broadcast Only", format_int(int((no_cable_candidates["no_cable_tv_type"] == "broadcast_only").sum())), None),
                ("CTV Only", format_int(int((no_cable_candidates["no_cable_tv_type"] == "ctv_only").sum())), None),
                ("Broadcast + CTV", format_int(int((no_cable_candidates["no_cable_tv_type"] == "broadcast_ctv_no_cable").sum())), None),
            ]
        )
        chart_cols = st.columns(2)
        with chart_cols[0]:
            no_cable_bar = px.bar(
                no_cable_candidates.sort_values("tv_spend", ascending=False).head(15),
                x="tv_spend",
                y="candidate_name",
                orientation="h",
                color="No-Cable TV Type",
                hover_data=["state", "office", "district", "broadcast_spend", "ctv_spend", "result"],
                title="Top No-Cable TV Candidates",
                color_discrete_map={
                    "Broadcast Only": COLORS["broadcast_only"],
                    "CTV Only": "#5DADE2",
                    "Broadcast + CTV": "#7D3C98",
                },
            )
            no_cable_bar.update_layout(yaxis=dict(autorange="reversed"), margin=dict(l=0, r=0, t=48, b=0))
            st.plotly_chart(no_cable_bar, use_container_width=True)
        with chart_cols[1]:
            no_cable_result = px.scatter(
                no_cable_candidates,
                x="tv_spend",
                y="vote_share",
                color="No-Cable TV Type",
                symbol="result",
                size="total_attributed_spend",
                hover_name="candidate_name",
                title="No-Cable TV Spend vs Vote Share",
                color_discrete_map={
                    "Broadcast Only": COLORS["broadcast_only"],
                    "CTV Only": "#5DADE2",
                    "Broadcast + CTV": "#7D3C98",
                },
                log_x=compute_log_x(no_cable_candidates, "tv_spend"),
            )
            no_cable_result.update_layout(margin=dict(l=0, r=0, t=48, b=0))
            st.plotly_chart(no_cable_result, use_container_width=True)
        no_cable_detail = no_cable_candidates[
            [
                "candidate_name",
                "state",
                "office",
                "district",
                "broadcast_spend",
                "ctv_spend",
                "tv_spend",
                "no_cable_tv_type",
                "result",
                "votes",
                "vote_share",
            ]
        ].copy()
        no_cable_detail["office"] = no_cable_detail["office"].map(office_label)
        no_cable_detail["no_cable_tv_type"] = no_cable_detail["no_cable_tv_type"].map(no_cable_tv_label)
        no_cable_detail.rename(
            columns={
                "candidate_name": "Candidate",
                "state": "State",
                "office": "Office",
                "district": "District",
                "broadcast_spend": "Broadcast $",
                "ctv_spend": "CTV $",
                "tv_spend": "TV $",
                "no_cable_tv_type": "No-Cable TV Type",
                "result": "Result",
                "votes": "Votes",
                "vote_share": "Vote Share",
            },
            inplace=True,
        )
        csv_download(no_cable_detail, "Download No-Cable TV Candidates", "no_cable_tv_candidates.csv")
        st.dataframe(style_result_table(no_cable_detail, result_col="Result"), width="stretch", hide_index=True)

    detail = filtered_broadcast[
        [
            "advertiser_name",
            "candidate_name",
            "state",
            "office",
            "district",
            "broadcast_spend",
            "cable_spend",
            "ctv_spend",
            "digital_spend",
            "radio_spend",
            "tv_spend",
            "result",
            "votes",
            "tv_mix",
        ]
    ].copy()
    detail["office"] = detail["office"].map(office_label)
    detail.rename(
        columns={
            "advertiser_name": "Advertiser",
            "candidate_name": "Matched Candidate",
            "state": "State",
            "office": "Office",
            "district": "District",
            "broadcast_spend": "Broadcast $",
            "cable_spend": "Cable $",
            "ctv_spend": "CTV $",
            "digital_spend": "Digital $",
            "radio_spend": "Radio $",
            "tv_spend": "TV $",
            "result": "Result",
            "votes": "Votes",
            "tv_mix": "TV Cohort",
        },
        inplace=True,
    )
    detail["TV Cohort"] = detail["TV Cohort"].map(tv_mix_label)
    st.subheader("Broadcast-Only Advertiser Detail")
    csv_download(detail, "Download Broadcast-Only Advertisers", "broadcast_only_advertisers.csv")
    st.dataframe(style_result_table(detail, result_col="Result"), width="stretch", hide_index=True)


def page_race_explorer(
    candidate_df: pd.DataFrame,
    advertiser_df: pd.DataFrame,
    filters: dict[str, object],
) -> None:
    filtered_candidates = apply_candidate_filters(candidate_df, filters, apply_tv_floor=False)
    filtered_advertisers = apply_advertiser_filters(advertiser_df, filters, apply_tv_floor=False)
    page_header(
        "Race Explorer",
        "Pick a race, especially a US House district, and compare media mix, spend, and results candidate by candidate.",
    )
    if filtered_candidates.empty:
        st.info("No races match the current filters.")
        return

    state = st.selectbox("State", sorted(filtered_candidates["state"].dropna().unique()))
    state_df = filtered_candidates[filtered_candidates["state"] == state]
    office_options = sort_offices(list(state_df["office"].dropna().unique()))
    default_office_index = office_options.index("us_house") if "us_house" in office_options else 0
    office = st.selectbox(
        "Office",
        office_options,
        index=default_office_index,
        format_func=office_label,
    )
    office_df = state_df[state_df["office"] == office]
    race = st.selectbox("Race", sorted(office_df["race_name"].dropna().unique()))
    race_candidates = office_df[office_df["race_name"] == race].copy()
    race_advertisers = filtered_advertisers[
        (filtered_advertisers["state"] == state)
        & (filtered_advertisers["office"] == office)
        & (filtered_advertisers["race_name"] == race)
    ].copy()
    if race_candidates.empty:
        st.info("No candidate rows are available for that race.")
        return

    winner_row = race_candidates.sort_values(["result", "vote_share"], ascending=[True, False]).iloc[0]
    kpi_row(
        [
            ("Race", race, None),
            ("Date", str(race_candidates["primary_date"].iloc[0]), None),
            ("Winner", str(winner_row["candidate_name"]), None),
            ("Winner Vote Share", format_pct(winner_row["vote_share"]), None),
            ("Total TV Spend in Race", format_currency(race_candidates["tv_spend"].sum()), None),
        ]
    )

    roster_token = STORE.get_metadata().get("last_house_roster_ingest", "")
    roster_df = load_house_roster(roster_token)
    if office == "us_house" and not roster_df.empty:
        dist_raw = race_candidates["district"].dropna().astype(str).iloc[0] if race_candidates["district"].notna().any() else ""
        district_key = str(int(dist_raw)) if dist_raw.isdigit() else dist_raw.lstrip("0")
        roster_hit = roster_df[
            (roster_df["state"].astype(str) == str(state))
            & (roster_df["district"].astype(str).str.lstrip("0").replace("", "0") == str(district_key or "0"))
        ].copy()
        if not roster_hit.empty:
            incumbent = roster_hit.iloc[0]
            incumbent_name = incumbent.get("incumbent_name") or "Vacant"
            incumbent_party = incumbent.get("incumbent_party") or "—"
            st.markdown("**House roster context**")
            kpi_row(
                [
                    ("Current Rep / Incumbent", str(incumbent_name), None, "positive"),
                    ("Incumbent Party", str(incumbent_party), None, "ctv"),
                    ("Roster Source", str(incumbent.get("source_file") or "House roster"), None, "broadcast"),
                ]
            )

    demo = load_demographics()
    if not demo.empty:
        target = None
        if office == "us_house":
            dist_raw = race_candidates["district"].dropna().astype(str).iloc[0] if race_candidates["district"].notna().any() else None
            if dist_raw:
                digits = "".join(c for c in dist_raw if c.isdigit()).zfill(2)
                hit = demo[(demo["geo_type"] == "cd") & (demo["state"] == state) & (demo["district"].astype(str).str.zfill(2) == digits)]
                if not hit.empty:
                    target = hit.iloc[0]
        if target is None:
            hit = demo[(demo["geo_type"] == "state") & (demo["state"] == state)]
            if not hit.empty:
                target = hit.iloc[0]
        if target is not None:
            st.markdown("**District / State demographics** — ACS 5-year 2022")
            lean_val = target.get("partisan_lean_d_minus_r") if "partisan_lean_d_minus_r" in target.index else None
            lean_str = f"{lean_val:+.1f} pp" if pd.notna(lean_val) else "—"
            kpi_row(
                [
                    ("Population", format_int(target.get("population")), None),
                    ("Median Age", f"{target.get('median_age'):.1f}" if pd.notna(target.get("median_age")) else "—", None),
                    ("Median HH $", format_currency(target.get("median_hh_income")), None),
                    ("Urbanicity", f"{target.get('urbanicity_score'):.1f}" if "urbanicity_score" in target.index and pd.notna(target.get("urbanicity_score")) else "—", None),
                    ("D−R Lean", lean_str, None),
                    ("Cable-Fit", f"{target.get('cable_fit_score'):.1f}" if pd.notna(target.get("cable_fit_score")) else "—", None),
                    ("CTV-Fit", f"{target.get('ctv_fit_score'):.1f}" if pd.notna(target.get("ctv_fit_score")) else "—", None),
                ]
            )

    st.subheader("Election Summary")
    st.caption("Formatted to mirror the IL CD 07 / IL CD 08 case-study tabs: committee spend on the left, primary results on the right.")
    summary_left, summary_right = st.columns([1.4, 1.0])
    with summary_left:
        advertiser_summary = race_advertisers[
            [
                "advertiser_name",
                "match_source",
                "broadcast_spend",
                "cable_spend",
                "ctv_spend",
                "digital_spend",
                "radio_spend",
                "total_spend",
            ]
        ].copy() if not race_advertisers.empty else pd.DataFrame(
            columns=[
                "advertiser_name",
                "match_source",
                "broadcast_spend",
                "cable_spend",
                "ctv_spend",
                "digital_spend",
                "radio_spend",
                "total_spend",
            ]
        )
        total_row = pd.DataFrame(
            [
                {
                    "advertiser_name": "Grand Total",
                    "match_source": "Total",
                    "broadcast_spend": advertiser_summary["broadcast_spend"].sum(),
                    "cable_spend": advertiser_summary["cable_spend"].sum(),
                    "ctv_spend": advertiser_summary["ctv_spend"].sum(),
                    "digital_spend": advertiser_summary["digital_spend"].sum(),
                    "radio_spend": advertiser_summary["radio_spend"].sum(),
                    "total_spend": advertiser_summary["total_spend"].sum(),
                }
            ]
        )
        advertiser_summary = pd.concat(
            [total_row, advertiser_summary.sort_values("total_spend", ascending=False)],
            ignore_index=True,
        )
        advertiser_summary.rename(
            columns={
                "advertiser_name": "Candidate / Committee",
                "match_source": "Agency / Match",
                "broadcast_spend": "Broadcast",
                "cable_spend": "Cable",
                "ctv_spend": "CTV",
                "digital_spend": "Digital",
                "radio_spend": "Radio",
                "total_spend": "Grand Total",
            },
            inplace=True,
        )
        for column in ["Broadcast", "Cable", "CTV", "Digital", "Radio", "Grand Total"]:
            advertiser_summary[column] = advertiser_summary[column].map(
                lambda value: round(float(value), 2) if pd.notna(value) else value
            )
        st.dataframe(advertiser_summary, width="stretch", hide_index=True)

    with summary_right:
        results_summary = race_candidates[
            ["candidate_name", "total_attributed_spend", "vote_share", "result"]
        ].copy()
        results_summary.sort_values(["vote_share", "total_attributed_spend"], ascending=[False, False], inplace=True)
        results_summary.rename(
            columns={
                "candidate_name": "Primary Results",
                "total_attributed_spend": "Total Ad Spend",
                "vote_share": "Vote %",
                "result": "Result",
            },
            inplace=True,
        )
        results_summary["Total Ad Spend"] = results_summary["Total Ad Spend"].map(
            lambda value: round(float(value), 2) if pd.notna(value) else value
        )
        results_summary["Vote %"] = results_summary["Vote %"].map(
            lambda value: round(float(value) * 100, 2) if pd.notna(value) else value
        )
        st.dataframe(style_result_table(results_summary, result_col="Result"), width="stretch", hide_index=True)

    stacked = race_candidates.melt(
        id_vars=["candidate_name", "result"],
        value_vars=["broadcast_spend", "cable_spend", "ctv_spend", "digital_spend", "radio_spend"],
        var_name="channel",
        value_name="spend",
    )
    stacked["channel"] = stacked["channel"].map(
        {
            "broadcast_spend": "Broadcast",
            "cable_spend": "Cable",
            "ctv_spend": "CTV",
            "digital_spend": "Digital",
            "radio_spend": "Radio",
        }
    )
    stack_fig = px.bar(
        stacked,
        x="candidate_name",
        y="spend",
        color="channel",
        title=f"{race} — Media Mix per Candidate",
        color_discrete_map={
            "Broadcast": COLORS["broadcast_only"],
            "Cable": COLORS["cable_ctv_only"],
            "CTV": "#5DADE2",
            "Digital": "#95A5A6",
            "Radio": "#D7DBDD",
        },
    )
    stack_fig.update_layout(barmode="stack", margin=dict(l=0, r=0, t=48, b=0))
    st.plotly_chart(stack_fig, use_container_width=True)

    st.subheader("Per-Candidate Media Mix %")
    pie_channel_colors = {
        "Broadcast": COLORS["broadcast_only"],
        "Cable": COLORS["cable_ctv_only"],
        "CTV": "#5DADE2",
        "Digital": "#95A5A6",
        "Radio": "#D7DBDD",
    }
    pie_candidates = race_candidates[race_candidates["total_attributed_spend"] > 0].copy()
    if pie_candidates.empty:
        st.info("No spend attributed to candidates in this race yet.")
    else:
        pie_cols = st.columns(min(len(pie_candidates), 4) or 1)
        for idx, (_, row) in enumerate(pie_candidates.iterrows()):
            values = [
                row.get("broadcast_spend", 0) or 0,
                row.get("cable_spend", 0) or 0,
                row.get("ctv_spend", 0) or 0,
                row.get("digital_spend", 0) or 0,
                row.get("radio_spend", 0) or 0,
            ]
            labels = ["Broadcast", "Cable", "CTV", "Digital", "Radio"]
            if sum(values) <= 0:
                continue
            fig = go.Figure(
                go.Pie(
                    labels=labels,
                    values=values,
                    hole=0.45,
                    marker=dict(colors=[pie_channel_colors[l] for l in labels]),
                    textinfo="label+percent",
                    sort=False,
                )
            )
            result_tag = str(row.get("result", "")).title() or "—"
            fig.update_layout(
                title=f"{row['candidate_name']} ({result_tag})",
                margin=dict(l=0, r=0, t=48, b=0),
                showlegend=False,
                height=320,
            )
            with pie_cols[idx % len(pie_cols)]:
                st.plotly_chart(fig, use_container_width=True)

    lower = st.columns(2)
    with lower[0]:
        race_candidates["TV Cohort"] = race_candidates["tv_mix"].map(tv_mix_label)
        scatter = px.scatter(
            race_candidates,
            x="tv_spend",
            y="vote_share",
            color="TV Cohort",
            symbol="result",
            size="total_attributed_spend",
            hover_name="candidate_name",
            title="TV Spend vs Vote Share in This Race",
            color_discrete_map={
                "Broadcast-Only": COLORS["broadcast_only"],
                "Cable/CTV-Only": COLORS["cable_ctv_only"],
                "Mixed": COLORS["mixed_tv"],
            },
            log_x=compute_log_x(race_candidates, "tv_spend"),
        )
        scatter.update_layout(margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(scatter, use_container_width=True)

    with lower[1]:
        detail = race_candidates[
            [
                "candidate_name",
                "party",
                "broadcast_spend",
                "cable_spend",
                "ctv_spend",
                "digital_spend",
                "radio_spend",
                "total_attributed_spend",
                "tv_mix",
                "votes",
                "vote_share",
                "margin",
                "result",
            ]
        ].copy()
        detail["tv_mix"] = detail["tv_mix"].map(tv_mix_label)
        detail.rename(
            columns={
                "candidate_name": "Candidate",
                "party": "Party",
                "broadcast_spend": "Broadcast $",
                "cable_spend": "Cable $",
                "ctv_spend": "CTV $",
                "digital_spend": "Digital $",
                "radio_spend": "Radio $",
                "total_attributed_spend": "Total $",
                "tv_mix": "TV Cohort",
                "votes": "Votes",
                "vote_share": "Vote Share",
                "margin": "Margin",
                "result": "Result",
            },
            inplace=True,
        )
        csv_download(detail, "Download Race Explorer Detail", "race_explorer_detail.csv")
        st.dataframe(style_result_table(detail, result_col="Result"), width="stretch", hide_index=True)

    if not race_advertisers.empty:
        with st.expander("Advertiser and PAC attribution for this race"):
            advertiser_detail = race_advertisers[
                [
                    "advertiser_name",
                    "candidate_name",
                    "match_source",
                    "broadcast_spend",
                    "cable_spend",
                    "ctv_spend",
                    "digital_spend",
                    "radio_spend",
                    "total_spend",
                ]
            ].rename(
                columns={
                    "advertiser_name": "Advertiser",
                    "candidate_name": "Candidate",
                    "match_source": "Match Source",
                    "broadcast_spend": "Broadcast $",
                    "cable_spend": "Cable $",
                    "ctv_spend": "CTV $",
                    "digital_spend": "Digital $",
                    "radio_spend": "Radio $",
                    "total_spend": "Total $",
                }
            )
            st.dataframe(advertiser_detail, width="stretch", hide_index=True)


def bar_from_candidates(frame: pd.DataFrame, x_col: str, title: str, color: str) -> go.Figure:
    top = frame.sort_values(x_col, ascending=False).head(12).copy()
    if top.empty:
        return go.Figure()
    label = top.apply(
        lambda row: f"{row['candidate_name']} ({row['state']}" + (f"-{row['district']}" if row.get("district") else "") + ")",
        axis=1,
    )
    fig = go.Figure(
        go.Bar(
            x=top[x_col],
            y=label,
            orientation="h",
            marker_color=color,
            hovertemplate="%{y}<br>$%{x:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(title=title, yaxis=dict(autorange="reversed"), margin=dict(l=0, r=0, t=48, b=0))
    return fig


def page_active_outlook(
    candidate_df: pd.DataFrame,
    upcoming_df: pd.DataFrame,
    filters: dict[str, object],
) -> None:
    page_header(
        "Active Races & Outlook",
        "Track upcoming primaries, see which races already have TV spend, and surface heavy broadcast vs heavy Cable/CTV campaigns before Election Day.",
    )
    filtered_upcoming = apply_upcoming_filters(upcoming_df, filters)
    active_candidates = apply_candidate_filters(upcoming_candidates(candidate_df), filters, apply_tv_floor=False)

    tab_overview, tab_leaders = st.tabs(["Upcoming Primaries Overview", "Active TV Spend Leaders"])

    with tab_overview:
        next_90 = filtered_upcoming[coerce_dates(filtered_upcoming, "primary_date") <= TODAY + pd.Timedelta(days=90)]
        kpi_row(
            [
                ("Upcoming Primaries (Next 90 Days)", format_int(next_90.shape[0]), None),
                ("States With Upcoming Primaries", format_int(filtered_upcoming["state"].nunique() if not filtered_upcoming.empty else 0), None),
                ("Races With Current TV Spend", format_int(filtered_upcoming["has_spend_data"].sum() if not filtered_upcoming.empty else 0), None),
            ]
        )
        if filtered_upcoming.empty:
            st.info("No upcoming primaries match the current filters.")
        else:
            timeline = filtered_upcoming.copy()
            timeline["Event"] = timeline.apply(
                lambda row: f"{row['state']} {office_label(row['office'])}" + (f" {row['district']}" if row.get("district") else ""),
                axis=1,
            )
            timeline["primary_date"] = coerce_dates(timeline, "primary_date")
            timeline_chart = px.scatter(
                timeline,
                x="primary_date",
                y="Event",
                color="top_tv_mix",
                size="top_tv_spend_amount",
                hover_name="race_name",
                hover_data=["days_until_primary", "candidate_count", "top_tv_spend_candidate"],
                title="Upcoming Primary Calendar",
                color_discrete_map={
                    "broadcast_only": COLORS["broadcast_only"],
                    "cable_ctv_only": COLORS["cable_ctv_only"],
                    "mixed_tv": COLORS["mixed_tv"],
                    "none": COLORS["gray"],
                },
            )
            timeline_chart.update_layout(margin=dict(l=0, r=0, t=48, b=0))
            st.plotly_chart(timeline_chart, use_container_width=True)

            overview_table = filtered_upcoming[
                [
                    "state",
                    "office",
                    "district",
                    "primary_date",
                    "days_until_primary",
                    "has_spend_data",
                    "top_tv_spend_candidate",
                    "top_tv_mix",
                    "top_tv_spend_amount",
                ]
            ].copy()
            overview_table["office"] = overview_table["office"].map(office_label)
            overview_table["top_tv_mix"] = overview_table["top_tv_mix"].map(tv_mix_label)
            overview_table.rename(
                columns={
                    "state": "State",
                    "office": "Office",
                    "district": "District",
                    "primary_date": "Primary Date",
                    "days_until_primary": "Days Until Primary",
                    "has_spend_data": "Has Spend Data?",
                    "top_tv_spend_candidate": "Top TV Spender",
                    "top_tv_mix": "Top TV Cohort",
                    "top_tv_spend_amount": "Top TV Spend",
                },
                inplace=True,
            )
            csv_download(overview_table, "Download Upcoming Primaries", "upcoming_primaries_overview.csv")
            st.dataframe(overview_table, width="stretch", hide_index=True)

    with tab_leaders:
        if active_candidates.empty:
            st.info("No upcoming races currently have matched candidate spend records under these filters.")
        else:
            broadcast_heavy = active_candidates[
                (active_candidates["broadcast_spend"] > 0)
                & (active_candidates["broadcast_spend"] >= active_candidates["cable_spend"] + active_candidates["ctv_spend"])
            ].copy()
            cable_heavy = active_candidates[
                (active_candidates["cable_spend"] + active_candidates["ctv_spend"] > 0)
                & (active_candidates["cable_spend"] + active_candidates["ctv_spend"] > active_candidates["broadcast_spend"])
            ].copy()

            charts = st.columns(2)
            with charts[0]:
                st.plotly_chart(
                    bar_from_candidates(
                        broadcast_heavy.assign(broadcast_heavy_tv=broadcast_heavy["broadcast_spend"]),
                        "broadcast_heavy_tv",
                        "Top Broadcast-Heavy Active Campaigns",
                        COLORS["broadcast_only"],
                    ),
                    use_container_width=True,
                )
            with charts[1]:
                cable_heavy = cable_heavy.assign(cable_heavy_tv=cable_heavy["cable_spend"] + cable_heavy["ctv_spend"])
                st.plotly_chart(
                    bar_from_candidates(
                        cable_heavy,
                        "cable_heavy_tv",
                        "Top Cable/CTV-Heavy Active Campaigns",
                        COLORS["cable_ctv_only"],
                    ),
                    use_container_width=True,
                )

            leader_table = active_candidates[
                [
                    "candidate_name",
                    "state",
                    "office",
                    "district",
                    "broadcast_spend",
                    "cable_spend",
                    "ctv_spend",
                    "digital_spend",
                    "radio_spend",
                    "tv_spend",
                    "tv_mix",
                    "primary_date",
                ]
            ].copy()
            leader_table["Days Until Primary"] = (
                pd.to_datetime(leader_table["primary_date"], errors="coerce") - TODAY
            ).dt.days
            leader_table["office"] = leader_table["office"].map(office_label)
            leader_table["tv_mix"] = leader_table["tv_mix"].map(tv_mix_label)
            leader_table.rename(
                columns={
                    "candidate_name": "Candidate",
                    "state": "State",
                    "office": "Office",
                    "district": "District",
                    "broadcast_spend": "Broadcast $",
                    "cable_spend": "Cable $",
                    "ctv_spend": "CTV $",
                    "digital_spend": "Digital $",
                    "radio_spend": "Radio $",
                    "tv_spend": "TV $",
                    "tv_mix": "TV Cohort",
                    "primary_date": "Primary Date",
                },
                inplace=True,
            )
            csv_download(leader_table, "Download Active TV Spend Leaders", "active_tv_spend_leaders.csv")
            st.dataframe(leader_table, width="stretch", hide_index=True)


def load_demographics() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    try:
        demo = pd.read_sql_query("SELECT * FROM district_demographics", conn)
    except Exception:
        demo = pd.DataFrame()
    try:
        party = pd.read_sql_query("SELECT * FROM party_registration", conn)
    except Exception:
        party = pd.DataFrame()
    conn.close()
    if demo.empty or party.empty:
        return demo
    return demo.merge(
        party[
            [
                "geo_id", "registered_dem", "registered_rep", "registered_npa",
                "total_registered", "partisan_lean_d_minus_r", "source", "as_of",
            ]
        ],
        on="geo_id",
        how="left",
        suffixes=("", "_party"),
    )


def page_demographics(filters: dict[str, object]) -> None:
    page_header(
        "District Demographics & Cable Fit",
        "ACS 5-year 2022 data by state and congressional district, with a cable-fit proxy score (age + income + broadband).",
    )
    demo = load_demographics()
    if demo.empty:
        st.info("No demographics loaded. Run `python fetch_acs_demographics.py`.")
        return

    states = filters["states"]
    view = demo.copy()
    if "All" not in states:
        view = view[view["state"].isin(states)]

    geo_choice = st.radio("View", ["Congressional Districts", "States"], horizontal=True)
    view = view[view["geo_type"] == ("cd" if geo_choice == "Congressional Districts" else "state")].copy()
    if view.empty:
        st.info("No rows for this selection.")
        return

    kpi_row(
        [
            ("Rows", format_int(view.shape[0]), None),
            ("Median Age (median)", f"{view['median_age'].median():.1f}", None),
            ("Median HH Income (median)", format_currency(view["median_hh_income"].median()), None),
            ("Urbanicity (median)", f"{view['urbanicity_score'].median():.1f}" if "urbanicity_score" in view.columns else "—", None),
            ("Cable-Fit (median)", f"{view['cable_fit_score'].median():.1f}", None),
            ("CTV-Fit (median)", f"{view['ctv_fit_score'].median():.1f}", None),
        ]
    )

    if geo_choice == "States":
        fig = px.choropleth(
            view,
            locations="state",
            locationmode="USA-states",
            scope="usa",
            color="cable_fit_score",
            hover_name="name",
            hover_data=["median_age", "median_hh_income", "pct_broadband", "ctv_fit_score"],
            color_continuous_scale=["#D6EAF8", COLORS["cable_ctv_only"]],
            title="Cable-Fit Score by State",
        )
        fig.update_layout(margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(fig, use_container_width=True)
    else:
        top = view.sort_values("cable_fit_score", ascending=False).head(25).copy()
        top["label"] = top["state"] + "-" + top["district"].astype(str)
        fig = px.bar(
            top,
            x="cable_fit_score",
            y="label",
            orientation="h",
            color="median_hh_income",
            hover_data=["median_age", "pct_broadband", "pct_bachelors_plus", "population"],
            color_continuous_scale=["#EBF5FB", "#1F618D"],
            title="Top 25 Cable-Fit Congressional Districts",
        )
        fig.update_layout(yaxis=dict(autorange="reversed"), margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(fig, use_container_width=True)

    scatter_view = view.copy()
    scatter_view["population"] = pd.to_numeric(scatter_view["population"], errors="coerce").fillna(0).clip(lower=0)
    scatter_view = scatter_view[scatter_view["population"] > 0]
    row2 = st.columns(2)
    with row2[0]:
        scatter = px.scatter(
            scatter_view,
            x="median_age",
            y="median_hh_income",
            size="population",
            color="cable_fit_score",
            color_continuous_scale=["#EBF5FB", COLORS["cable_ctv_only"]],
            hover_name="name",
            hover_data=["state", "district", "pct_broadband", "ctv_fit_score"],
            title="Age × Income (bubble = population, color = cable-fit)",
        )
        scatter.update_layout(margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(scatter, use_container_width=True)
    with row2[1]:
        scatter2 = px.scatter(
            scatter_view,
            x="ctv_fit_score",
            y="cable_fit_score",
            size="population",
            color="state",
            hover_name="name",
            hover_data=["district", "median_age", "median_hh_income", "pct_broadband"],
            title="CTV-Fit vs Cable-Fit",
        )
        scatter2.update_layout(margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(scatter2, use_container_width=True)

    base_cols = [
        "state", "district", "name", "population", "median_age", "median_hh_income",
        "pct_bachelors_plus", "pct_white", "pct_black", "pct_hispanic", "pct_asian",
        "pct_broadband", "pct_multiunit", "pct_transit", "urbanicity_score",
        "median_home_value", "cable_fit_score", "ctv_fit_score",
    ]
    party_cols = [c for c in ["partisan_lean_d_minus_r", "registered_dem", "registered_rep", "total_registered", "source", "as_of"] if c in view.columns]
    display = view[[c for c in base_cols + party_cols if c in view.columns]].copy()
    rename = {
        "state": "State", "district": "District", "name": "Name",
        "population": "Population", "median_age": "Median Age",
        "median_hh_income": "Median HH $", "pct_bachelors_plus": "% BA+",
        "pct_white": "% White", "pct_black": "% Black",
        "pct_hispanic": "% Hispanic", "pct_asian": "% Asian",
        "pct_broadband": "% Broadband", "pct_multiunit": "% Multi-Unit",
        "pct_transit": "% Transit", "urbanicity_score": "Urbanicity",
        "median_home_value": "Median Home $",
        "cable_fit_score": "Cable-Fit", "ctv_fit_score": "CTV-Fit",
        "partisan_lean_d_minus_r": "D−R Lean (pp)",
        "registered_dem": "Reg. D", "registered_rep": "Reg. R",
        "total_registered": "Total Reg.", "source": "Party Source", "as_of": "As Of",
    }
    display.rename(columns=rename, inplace=True)
    csv_download(display, "Download Demographics", "district_demographics.csv")
    st.dataframe(display, width="stretch", hide_index=True)
    caption = (
        "Fit scores are proxies — not measurements. Cable-Fit weights: age 30% + income 30% + broadband 25% + "
        "(inverse) urbanicity 15%. CTV-Fit weights: broadband 35% + youth 20% + education 20% + urbanicity 25%. "
        "Urbanicity blends multi-unit housing share (60%) and transit commute share (40%)."
    )
    if party_cols:
        caption += " Party-registration / partisan-lean data loaded from your CSV."
    else:
        caption += (
            " No party-registration data loaded yet — drop a CSV into "
            "`python ingest_party_registration.py <path>` to add D−R lean / registration columns."
        )
    st.caption(caption)


def page_polling(candidate_df: pd.DataFrame, filters: dict[str, object]) -> None:
    page_header(
        "Polling Center",
        "Track RealClear-style race boards, national issue polling, trend lines, and polling-vs-TV-spend mismatches.",
    )
    conn = sqlite3.connect(DB_PATH)
    try:
        polls = pd.read_sql_query("SELECT * FROM polls", conn)
        answers = pd.read_sql_query(
            "SELECT poll_id, candidate_name, party, pct FROM poll_answers", conn
        )
    except Exception:
        polls = pd.DataFrame()
        answers = pd.DataFrame()
    finally:
        conn.close()

    if polls.empty:
        st.info(
            "No polling data yet. Upload a RealClearPolling workbook on Setup / Data Health, "
            "or run `python ingest_polls.py path/to/polls.xlsx`."
        )
        return

    for column, default in {
        "race_name": "",
        "race_type": "",
        "winner": "",
        "spread": None,
        "source_file": "",
        "sample_size": None,
        "population": "",
        "sponsors": "",
        "source_url": "",
        "notes": "",
    }.items():
        if column not in polls.columns:
            polls[column] = default
    polls["end_date_dt"] = pd.to_datetime(polls["end_date"], errors="coerce")
    polls["start_date_dt"] = pd.to_datetime(polls.get("start_date"), errors="coerce")
    polls["race_label"] = polls["race_name"].fillna("").replace("", pd.NA)
    fallback_label = polls.apply(
        lambda row: " ".join(
            [
                str(row.get("state") or "").strip(),
                office_label(row.get("office")),
                str(row.get("district") or "").strip(),
            ]
        ).strip(),
        axis=1,
    )
    polls["race_label"] = polls["race_label"].fillna(fallback_label).fillna("Unlabeled Poll")
    polls["race_type"] = polls["race_type"].fillna("").replace("", "Uncategorized")
    polls["office"] = polls["office"].fillna("").replace("", "other")
    polls["state"] = polls["state"].fillna("Unknown")
    polls["pollster"] = polls["pollster"].fillna("Unknown")
    polls["fetched_at"] = polls["fetched_at"].fillna("")

    if answers.empty:
        st.info("Polling rows are loaded, but no candidate/option answers are available yet.")
        return
    answers["pct"] = pd.to_numeric(answers["pct"], errors="coerce")
    joined = answers.merge(
        polls[
            [
                "poll_id",
                "race_id",
                "state",
                "office",
                "district",
                "race_label",
                "race_type",
                "pollster",
                "end_date",
                "end_date_dt",
                "sample_size",
                "population",
                "winner",
                "spread",
                "source_file",
            ]
        ],
        on="poll_id",
        how="inner",
    )

    def _multi_filter(frame: pd.DataFrame, column: str, selected: list[str]) -> pd.DataFrame:
        if not selected:
            return frame
        return frame[frame[column].isin(selected)].copy()

    def _latest_poll_ids(frame: pd.DataFrame, per_race: int = 5) -> set[str]:
        return set(
            frame.sort_values(["race_label", "end_date_dt"], ascending=[True, False])
            .groupby("race_label", dropna=False)
            .head(per_race)["poll_id"]
        )

    def _race_board(poll_frame: pd.DataFrame, answer_frame: pd.DataFrame, per_race: int = 5) -> pd.DataFrame:
        if poll_frame.empty or answer_frame.empty:
            return pd.DataFrame()
        poll_ids = _latest_poll_ids(poll_frame, per_race=per_race)
        avg_source = answer_frame[answer_frame["poll_id"].isin(poll_ids)].copy()
        if avg_source.empty:
            return pd.DataFrame()
        rows: list[dict] = []
        for race_label, group in avg_source.groupby("race_label", dropna=False):
            race_polls = poll_frame[poll_frame["race_label"] == race_label].sort_values("end_date_dt", ascending=False)
            averages = (
                group.groupby("candidate_name", dropna=False)
                .agg(avg_pct=("pct", "mean"), polls=("poll_id", "nunique"), party=("party", "first"))
                .reset_index()
                .sort_values("avg_pct", ascending=False)
            )
            if averages.empty or race_polls.empty:
                continue
            leader = averages.iloc[0]
            second = averages.iloc[1] if len(averages) > 1 else None
            latest = race_polls.iloc[0]
            margin = float(leader["avg_pct"] - second["avg_pct"]) if second is not None else None
            rows.append(
                {
                    "Race / Topic": race_label,
                    "State": latest.get("state"),
                    "Office": office_label(latest.get("office")),
                    "Race Type": latest.get("race_type"),
                    "Polls": int(race_polls["poll_id"].nunique()),
                    "Latest Poll": str(latest.get("end_date") or "")[:10],
                    "Latest Pollster": latest.get("pollster"),
                    "Leader": leader["candidate_name"],
                    "Leader Avg": float(leader["avg_pct"]),
                    "Runner-Up": second["candidate_name"] if second is not None else "",
                    "Runner-Up Avg": float(second["avg_pct"]) if second is not None else None,
                    "Margin": margin,
                    "Last Winner": latest.get("winner"),
                    "Last Spread": latest.get("spread"),
                }
            )
        board = pd.DataFrame(rows)
        if board.empty:
            return board
        return board.sort_values(["Latest Poll", "Polls", "Race / Topic"], ascending=[False, False, True])

    filter_cols = st.columns(2)
    with filter_cols[0]:
        race_type_options = sorted(polls["race_type"].dropna().astype(str).unique())
        selected_race_types = st.multiselect("Race Type (blank = all)", race_type_options, default=[], key="poll_race_types")
    with filter_cols[1]:
        state_options = sorted(polls["state"].dropna().astype(str).unique())
        selected_poll_states = st.multiselect("State (blank = all)", state_options, default=[], key="poll_states")
    filter_cols = st.columns(2)
    with filter_cols[0]:
        office_options = sorted(polls["office"].dropna().astype(str).unique(), key=office_label)
        selected_poll_offices = st.multiselect(
            "Office / Topic (blank = all)",
            office_options,
            default=[],
            format_func=office_label,
            key="poll_offices",
        )
    with filter_cols[1]:
        pollster_options = sorted(polls["pollster"].dropna().astype(str).unique())
        selected_pollsters = st.multiselect("Pollster (blank = all)", pollster_options, default=[], key="poll_pollsters")

    view = polls.copy()
    view = _multi_filter(view, "race_type", selected_race_types)
    view = _multi_filter(view, "state", selected_poll_states)
    view = _multi_filter(view, "office", selected_poll_offices)
    view = _multi_filter(view, "pollster", selected_pollsters)
    date_values = view["end_date_dt"].dropna()
    if not date_values.empty:
        date_range = st.date_input(
            "Poll Date Range",
            value=(date_values.min().date(), date_values.max().date()),
            min_value=polls["end_date_dt"].dropna().min().date(),
            max_value=polls["end_date_dt"].dropna().max().date(),
            key="poll_date_range",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
            view = view[(view["end_date_dt"] >= start_date) & (view["end_date_dt"] <= end_date)].copy()

    filtered_answers = joined[joined["poll_id"].isin(view["poll_id"])].copy()
    board = _race_board(view, filtered_answers)

    kpi_row(
        [
            ("Polls Tracked", format_int(view.shape[0]), None),
            ("Races / Topics Covered", format_int(view["race_label"].nunique()), None),
            ("States Covered", format_int(view["state"].dropna().nunique()), None),
            ("Latest Poll Date", view["end_date_dt"].max().strftime("%Y-%m-%d") if not view.empty and pd.notna(view["end_date_dt"].max()) else "—", None),
            ("Last Poll Import", format_timestamp(view["fetched_at"].max()) if not view.empty else "—", None),
        ]
    )

    if view.empty:
        st.info("No polls match the current filters.")
        return

    tab_board, tab_detail, tab_trends, tab_watch, tab_all = st.tabs(
        ["Board", "Race Detail", "Trends", "Poll vs Spend", "All Polls"]
    )

    with tab_board:
        if board.empty:
            st.info("No polling board rows are available for the current filters.")
        else:
            display_board = board.copy()
            for col in ["Leader Avg", "Runner-Up Avg", "Margin", "Last Spread"]:
                if col in display_board.columns:
                    display_board[col] = display_board[col].map(lambda value: round(float(value), 1) if pd.notna(value) else value)
            st.subheader("Latest Polling Board")
            st.dataframe(display_board, width="stretch", hide_index=True)
            csv_download(display_board, "Download Polling Board", "polling_board.csv")

            chart_source = board.dropna(subset=["Margin"]).sort_values("Margin", ascending=True).tail(20)
            if not chart_source.empty:
                chart_source["Label"] = chart_source["Race / Topic"].str.replace("2026 ", "", regex=False)
                fig = px.bar(
                    chart_source,
                    x="Margin",
                    y="Label",
                    orientation="h",
                    color="Office",
                    hover_data=["Leader", "Leader Avg", "Runner-Up", "Runner-Up Avg", "Polls", "Latest Pollster"],
                    title="Largest Current Polling Margins",
                )
                fig.update_layout(margin=dict(l=0, r=0, t=48, b=0), yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig, use_container_width=True)

            recent = view.sort_values("end_date_dt", ascending=False).head(80).copy()
            recent["Office"] = recent["office"].map(office_label)
            timeline = px.scatter(
                recent,
                x="end_date_dt",
                y="race_label",
                color="Office",
                hover_data=["pollster", "state", "race_type", "winner", "spread"],
                title="Latest Polls by Race / Topic",
            )
            timeline.update_layout(margin=dict(l=0, r=0, t=48, b=0), xaxis_title="", yaxis_title="")
            st.plotly_chart(timeline, use_container_width=True)

    race_options = sorted(view["race_label"].dropna().astype(str).unique())
    default_race = 0
    for idx, label in enumerate(race_options):
        if "Generic Congressional Vote" in label:
            default_race = idx
            break

    def _race_poll_table(race_polls: pd.DataFrame, race_answers: pd.DataFrame) -> pd.DataFrame:
        answer_text = (
            race_answers.sort_values(["poll_id", "pct"], ascending=[True, False])
            .groupby("poll_id")
            .apply(lambda grp: " | ".join(f"{r.candidate_name} {r.pct:.0f}" for r in grp.itertuples()))
            .reset_index(name="Result")
        )
        table = race_polls.merge(answer_text, on="poll_id", how="left").sort_values("end_date_dt", ascending=False)
        table = table[
            [
                "end_date",
                "pollster",
                "winner",
                "spread",
                "Result",
                "sample_size",
                "population",
                "source_file",
            ]
        ].rename(
            columns={
                "end_date": "Date",
                "pollster": "Pollster",
                "winner": "Leader",
                "spread": "Margin",
                "sample_size": "N",
                "population": "Pop.",
                "source_file": "Source",
            }
        )
        table["Margin"] = table["Margin"].map(lambda value: round(float(value), 1) if pd.notna(value) else value)
        return table

    with tab_detail:
        selected_race = st.selectbox("Race / Topic", race_options, index=default_race, key="poll_detail_race")
        race_polls = view[view["race_label"] == selected_race].copy()
        race_answers = filtered_answers[filtered_answers["race_label"] == selected_race].copy()
        race_board = _race_board(race_polls, race_answers, per_race=5)
        if race_board.empty:
            st.info("No answer rows are available for this race/topic.")
        else:
            row = race_board.iloc[0]
            kpi_row(
                [
                    ("Current Leader", row.get("Leader", "—"), None, "positive"),
                    ("Average Margin", f"{float(row.get('Margin')):+.1f}" if pd.notna(row.get("Margin")) else "—", None, "broadcast"),
                    ("Polls", format_int(race_polls["poll_id"].nunique()), None, "ctv"),
                    ("Latest Poll", row.get("Latest Poll", "—"), row.get("Latest Pollster"), "cable"),
                ],
                max_cols=2,
            )
            avg = (
                race_answers.groupby("candidate_name", dropna=False)
                .agg(avg_pct=("pct", "mean"), polls=("poll_id", "nunique"))
                .reset_index()
                .sort_values("avg_pct", ascending=True)
            )
            fig = px.bar(
                avg,
                x="avg_pct",
                y="candidate_name",
                orientation="h",
                color="candidate_name",
                title=f"{selected_race} — Polling Average",
                hover_data=["polls"],
            )
            fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=48, b=0), xaxis_title="Average %", yaxis_title="")
            st.plotly_chart(fig, use_container_width=True)
            trend = race_answers.dropna(subset=["end_date_dt", "pct"]).sort_values("end_date_dt")
            if not trend.empty:
                line = px.line(
                    trend,
                    x="end_date_dt",
                    y="pct",
                    color="candidate_name",
                    markers=True,
                    hover_data=["pollster", "race_type"],
                    title=f"{selected_race} — Poll Trend",
                )
                line.update_layout(margin=dict(l=0, r=0, t=48, b=0), xaxis_title="", yaxis_title="%")
                st.plotly_chart(line, use_container_width=True)
            detail_table = _race_poll_table(race_polls, race_answers)
            st.subheader("Polls in This Race / Topic")
            st.dataframe(detail_table, width="stretch", hide_index=True)
            csv_download(detail_table, "Download Race Polls", "race_poll_detail.csv")

    with tab_trends:
        trend_race = st.selectbox("Trend Race / Topic", race_options, index=default_race, key="poll_trend_race")
        trend_answers = filtered_answers[filtered_answers["race_label"] == trend_race].dropna(subset=["end_date_dt", "pct"]).copy()
        if trend_answers.empty:
            st.info("No trend rows are available for this race/topic.")
        else:
            trend_answers.sort_values(["candidate_name", "end_date_dt"], inplace=True)
            fig = px.line(
                trend_answers,
                x="end_date_dt",
                y="pct",
                color="candidate_name",
                markers=True,
                hover_data=["pollster", "state", "race_type"],
                title=f"{trend_race} — RealClearPolling Trend",
            )
            fig.update_layout(margin=dict(l=0, r=0, t=48, b=0), xaxis_title="", yaxis_title="Polling %")
            st.plotly_chart(fig, use_container_width=True)
            pollster_counts = (
                trend_answers.groupby("pollster", dropna=False)["poll_id"]
                .nunique()
                .reset_index(name="Polls")
                .sort_values("Polls", ascending=False)
            )
            st.dataframe(pollster_counts.rename(columns={"pollster": "Pollster"}), width="stretch", hide_index=True)

    with tab_watch:
        if candidate_df.empty or "race_id" not in candidate_df.columns:
            st.info("Candidate spend tables are not loaded, so polling-vs-spend comparisons are unavailable.")
        else:
            leaders = (
                filtered_answers.dropna(subset=["race_id"])
                .sort_values(["race_id", "end_date_dt", "pct"], ascending=[True, False, False])
                .groupby("race_id", as_index=False)
                .first()[
                    [
                        "race_id",
                        "state",
                        "office",
                        "district",
                        "race_label",
                        "candidate_name",
                        "party",
                        "pct",
                        "pollster",
                        "end_date",
                    ]
                ]
                .rename(columns={"candidate_name": "poll_leader", "pct": "poll_leader_pct"})
            )
            if leaders.empty:
                st.info("No polls matched existing race IDs yet. National issue polls and unmatched race-board rows are shown in the other tabs.")
            else:
                candidate_spend = (
                    candidate_df.groupby("race_id")
                    .apply(
                        lambda g: g.sort_values("tv_spend", ascending=False).head(1)[
                            ["candidate_name", "tv_spend", "tv_mix", "broadcast_spend", "cable_spend", "ctv_spend"]
                        ].rename(
                            columns={
                                "candidate_name": "tv_leader",
                                "tv_spend": "tv_leader_spend",
                                "tv_mix": "tv_leader_mix",
                            }
                        )
                    )
                    .reset_index(level=1, drop=True)
                    .reset_index()
                )
                watch = leaders.merge(candidate_spend, on="race_id", how="left")
                watch["mismatch"] = watch.apply(
                    lambda r: bool(r.get("tv_leader"))
                    and str(r.get("tv_leader", "")).strip().lower() != str(r.get("poll_leader", "")).strip().lower(),
                    axis=1,
                )
                flagged = watch[watch["mismatch"]].copy()
                st.subheader("Polling vs TV Spend Watchlist")
                if flagged.empty:
                    st.caption("No polling/TV-spend leader mismatches in the current slice.")
                    flagged = watch.copy()
                display = flagged[
                    [
                        "race_label",
                        "state",
                        "office",
                        "district",
                        "poll_leader",
                        "poll_leader_pct",
                        "tv_leader",
                        "tv_leader_spend",
                        "tv_leader_mix",
                        "broadcast_spend",
                        "cable_spend",
                        "ctv_spend",
                        "pollster",
                        "end_date",
                    ]
                ].copy()
                display["office"] = display["office"].map(office_label)
                display["tv_leader_mix"] = display["tv_leader_mix"].map(tv_mix_label)
                for col in ["tv_leader_spend", "broadcast_spend", "cable_spend", "ctv_spend"]:
                    display[col] = display[col].map(lambda value: round(float(value), 2) if pd.notna(value) else value)
                display.rename(
                    columns={
                        "race_label": "Race / Topic",
                        "state": "State",
                        "office": "Office",
                        "district": "District",
                        "poll_leader": "Poll Leader",
                        "poll_leader_pct": "Poll %",
                        "tv_leader": "TV Spend Leader",
                        "tv_leader_spend": "TV Leader $",
                        "tv_leader_mix": "TV Cohort",
                        "broadcast_spend": "Broadcast $",
                        "cable_spend": "Cable $",
                        "ctv_spend": "CTV $",
                        "pollster": "Pollster",
                        "end_date": "Poll Date",
                    },
                    inplace=True,
                )
                st.dataframe(display, width="stretch", hide_index=True)
                csv_download(display, "Download Poll vs Spend Watchlist", "polling_vs_spend_watchlist.csv")

    with tab_all:
        answer_text = (
            filtered_answers.sort_values(["poll_id", "pct"], ascending=[True, False])
            .groupby("poll_id")
            .apply(lambda grp: " | ".join(f"{r.candidate_name} {r.pct:.0f}" for r in grp.itertuples()))
            .reset_index(name="Results")
        )
        poll_table = view.merge(answer_text, on="poll_id", how="left").sort_values("end_date_dt", ascending=False).copy()
        poll_table["Office"] = poll_table["office"].map(office_label)
        poll_table = poll_table.rename(
            columns={
                "race_label": "Race / Topic",
                "race_type": "Race Type",
                "state": "State",
                "district": "District",
                "pollster": "Pollster",
                "start_date": "Start",
                "end_date": "End",
                "sample_size": "N",
                "population": "Pop.",
                "winner": "Leader",
                "spread": "Margin",
                "sponsors": "Sponsors",
                "source_file": "Source File",
                "notes": "Notes",
                "fetched_at": "Fetched",
            }
        )
        keep_cols = [
            "End",
            "Race / Topic",
            "Race Type",
            "State",
            "Office",
            "District",
            "Pollster",
            "Results",
            "Leader",
            "Margin",
            "N",
            "Pop.",
            "Source File",
            "Notes",
        ]
        poll_table = poll_table[[column for column in keep_cols if column in poll_table.columns]]
        st.subheader("All Polls")
        st.dataframe(poll_table, width="stretch", hide_index=True)
        csv_download(poll_table, "Download All Polls", "all_polls.csv")


def page_spend_dashboard(candidate_df: pd.DataFrame, filters: dict[str, object]) -> None:
    page_header(
        "Spend Dashboard",
        "Slice campaign spend by data type, race type, party affiliation, advertiser type, state, market/DMA, and media channel.",
    )

    media_cols = {
        "Broadcast": "broadcast_spend",
        "Cable": "cable_spend",
        "CTV": "ctv_spend",
        "Digital": "digital_spend",
        "Radio": "radio_spend",
    }
    media_colors = {
        "Broadcast": PSI_COLORS["broadcast"],
        "Cable": PSI_COLORS["cable"],
        "CTV": PSI_COLORS["ctv"],
        "Digital": PSI_COLORS["digital"],
        "Radio": PSI_COLORS["radio"],
    }

    metadata = STORE.get_metadata()
    aggregate_df = load_spend_aggregates("|".join(metadata.values()))
    tab_candidates, tab_states, tab_markets = st.tabs(["Candidate Spend", "State Aggregates", "Market / DMA Aggregates"])

    def _split_options(frame: pd.DataFrame, column: str) -> list[str]:
        if column not in frame.columns:
            return []
        values: set[str] = set()
        for value in frame[column].fillna("").astype(str):
            values.update(part.strip() for part in value.split(",") if part.strip())
        return sorted(values)

    def _contains_any(series: pd.Series, selected: list[str]) -> pd.Series:
        if not selected:
            return pd.Series(True, index=series.index)
        selected_set = set(selected)
        return series.fillna("").astype(str).apply(
            lambda value: bool({part.strip() for part in value.split(",") if part.strip()} & selected_set)
        )

    with tab_candidates:
        base = apply_candidate_filters(candidate_df, filters, apply_tv_floor=False)
        for column in ["source_data_types", "source_race_types", "source_party_affiliations", "advertiser_types", "agencies"]:
            if column not in base.columns:
                base[column] = ""
            base[column] = base[column].fillna("")
        base = base[base["total_attributed_spend"].fillna(0) > 0].copy()
        if base.empty:
            st.info("No candidate spend matches the current sidebar filters.")
            return

        f1, f2 = st.columns(2)
        data_type_options = _split_options(base, "source_data_types")
        race_type_options = _split_options(base, "source_race_types")
        party_affiliation_options = _split_options(base, "source_party_affiliations")
        advertiser_type_options = _split_options(base, "advertiser_types")
        with f1:
            selected_data_types = st.multiselect(
                "Data Type (blank = all)",
                data_type_options,
                default=[],
                key="spend_data_type",
            )
        with f2:
            selected_race_types = st.multiselect(
                "Race Type (blank = all)",
                race_type_options,
                default=[],
                key="spend_race_type",
            )
        f3, f4 = st.columns(2)
        with f3:
            selected_source_parties = st.multiselect(
                "Party Affiliation (blank = all)",
                party_affiliation_options,
                default=[],
                key="spend_party_affiliation",
            )
        with f4:
            selected_advertiser_types = st.multiselect(
                "Advertiser Type (blank = all)",
                advertiser_type_options,
                default=[],
                key="spend_advertiser_type",
            )

        base = base[
            _contains_any(base["source_data_types"], selected_data_types)
            & _contains_any(base["source_race_types"], selected_race_types)
            & _contains_any(base["source_party_affiliations"], selected_source_parties)
            & _contains_any(base["advertiser_types"], selected_advertiser_types)
        ].copy()
        if base.empty:
            st.info("No candidates match those source metadata filters.")
            return

        ctl1, ctl2, ctl3 = st.columns([1.2, 1, 1.2])
        with ctl1:
            selected_media = st.multiselect(
                "Media types",
                list(media_cols.keys()),
                default=list(media_cols.keys()),
                key="spend_media_types",
            )
        if not selected_media:
            st.info("Select at least one media type.")
            return
        selected_cols = [media_cols[m] for m in selected_media]
        excluded_cols = [col for media, col in media_cols.items() if media not in selected_media]
        if excluded_cols:
            excluded_spend = base[excluded_cols].fillna(0).sum(axis=1)
            base = base[excluded_spend == 0].copy()
        base["selected_spend"] = base[selected_cols].sum(axis=1)
        base = base[base["selected_spend"] > 0].copy()
        if base.empty:
            st.info("No candidates used only the selected media types.")
            return

        max_spend = float(base["selected_spend"].max())
        with ctl2:
            spend_min = st.number_input("Min $ (selected media)", min_value=0.0, value=0.0, step=1000.0)
        with ctl3:
            spend_range = st.slider(
                "Spend range (selected media)",
                min_value=0.0,
                max_value=max(max_spend, 1.0),
                value=(float(spend_min), max(max_spend, 1.0)),
                step=max(max_spend / 100.0, 1.0),
            )
        base = base[(base["selected_spend"] >= spend_range[0]) & (base["selected_spend"] <= spend_range[1])].copy()
        if base.empty:
            st.info("No candidates fall inside that spend range.")
            return

        kpi_row(
            [
                ("Candidates", format_int(base.shape[0]), None, "ctv"),
                ("Total Spend (selected media)", format_compact_currency(base["selected_spend"].sum()), None, "cable"),
                ("Median / Candidate", format_compact_currency(base["selected_spend"].median()), None, "cable"),
                ("Top State", str(base.groupby("state")["selected_spend"].sum().idxmax()) if not base.empty else "—", None, "broadcast"),
                ("Races Represented", format_int(base["race_id"].nunique() if "race_id" in base.columns else base.groupby(["state", "office", "district"]).ngroups), None, "ctv"),
            ]
        )

        row1 = st.columns(2)
        with row1[0]:
            totals = pd.DataFrame(
                {
                    "Media": list(media_cols.keys()),
                    "Spend": [
                        float(base[media_cols[m]].sum()) if m in selected_media else 0.0
                        for m in media_cols.keys()
                    ],
                }
            )
            fig = px.bar(
                totals,
                x="Media",
                y="Spend",
                color="Media",
                color_discrete_map=media_colors,
                title="Total Spend by Media Type",
            )
            fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=48, b=0))
            st.plotly_chart(fig, use_container_width=True)

        with row1[1]:
            race_type_totals = (
                base.assign(SourceRaceType=base["source_race_types"].replace("", "Unknown"))
                .groupby("SourceRaceType")["selected_spend"].sum()
                .reset_index()
                .sort_values("selected_spend", ascending=True)
            )
            fig = px.bar(
                race_type_totals,
                x="selected_spend",
                y="SourceRaceType",
                orientation="h",
                title="Spend by Source Race Type",
                color="selected_spend",
                color_continuous_scale=["#D6EAF8", "#1F618D"],
            )
            fig.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=0, t=48, b=0), xaxis_title="Spend", yaxis_title="")
            st.plotly_chart(fig, use_container_width=True)

        def _canonical_party(raw: object) -> str:
            text = str(raw or "").strip()
            if not text:
                return "Unknown"
            parts = [p.strip() for p in text.split(",") if p.strip()]
            buckets: set[str] = set()
            for part in parts:
                lower = part.lower()
                if "democratic-aligned" in lower or "democrat-aligned" in lower:
                    buckets.add("Democratic-aligned")
                elif "republican-aligned" in lower:
                    buckets.add("Republican-aligned")
                elif lower in {"democratic", "democrat"}:
                    buckets.add("Democratic")
                elif lower == "republican":
                    buckets.add("Republican")
                elif "nonpartisan" in lower:
                    buckets.add("Nonpartisan")
                elif "progressive" in lower:
                    buckets.add("Progressive")
                elif "bipartisan" in lower:
                    buckets.add("Bipartisan")
                elif "unknown" in lower or "not verified" in lower or "not party-affiliated" in lower:
                    buckets.add("Unknown")
                else:
                    buckets.add(part)
            if not buckets:
                return "Unknown"
            if len(buckets) == 1:
                return next(iter(buckets))
            if buckets <= {"Democratic", "Democratic-aligned"}:
                return "Democratic"
            if buckets <= {"Republican", "Republican-aligned"}:
                return "Republican"
            if buckets <= {"Unknown"}:
                return "Unknown"
            if "Democratic" in buckets and "Republican" in buckets:
                return "Mixed (D/R)"
            return "Mixed / Other"

        row2 = st.columns(2)
        with row2[0]:
            race_type_totals = (
                base.assign(RaceType=base["office"].map(office_label).replace("", "Unknown"))
                .groupby("RaceType")["selected_spend"].sum()
                .reset_index()
                .sort_values("selected_spend", ascending=True)
            )
            fig = px.bar(
                race_type_totals,
                x="selected_spend",
                y="RaceType",
                orientation="h",
                title="Spend by Race Type",
                color="selected_spend",
                color_continuous_scale=["#D6EAF8", "#1F618D"],
            )
            fig.update_layout(
                coloraxis_showscale=False,
                margin=dict(l=160, r=24, t=56, b=40),
                xaxis_title="Spend",
                yaxis_title="",
                yaxis=dict(automargin=True, tickfont=dict(size=11)),
                height=max(360, 38 * len(race_type_totals) + 120),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Race type is the candidate's office (Governor, U.S. Senate, U.S. House, etc.).")
        with row2[1]:
            party_totals = (
                base.assign(SourceParty=base["source_party_affiliations"].map(_canonical_party))
                .groupby("SourceParty")["selected_spend"].sum()
                .reset_index()
                .sort_values("selected_spend", ascending=True)
            )
            party_color_map = {
                "Democratic": "#1F618D",
                "Democratic-aligned": "#5DADE2",
                "Republican": "#C0392B",
                "Republican-aligned": "#E59866",
                "Nonpartisan": "#7D8C99",
                "Progressive": "#117864",
                "Bipartisan": "#7E57C2",
                "Mixed (D/R)": "#8E44AD",
                "Mixed / Other": "#95A5A6",
                "Unknown": "#BDC3C7",
            }
            fig = px.bar(
                party_totals,
                x="selected_spend",
                y="SourceParty",
                orientation="h",
                title="Spend by Party Affiliation",
                color="SourceParty",
                color_discrete_map=party_color_map,
            )
            fig.update_layout(
                showlegend=False,
                margin=dict(l=170, r=24, t=56, b=40),
                xaxis_title="Spend",
                yaxis_title="",
                yaxis=dict(automargin=True, tickfont=dict(size=11)),
                height=max(360, 38 * len(party_totals) + 120),
            )
            st.plotly_chart(fig, use_container_width=True)

        state_totals = base.groupby("state")["selected_spend"].sum().reset_index()
        state_map = px.choropleth(
            state_totals,
            locations="state",
            locationmode="USA-states",
            scope="usa",
            color="selected_spend",
            color_continuous_scale=["#EBF5FB", COLORS["cable_ctv_only"]],
            title="Spend by State",
        )
        state_map.update_layout(margin=dict(l=0, r=0, t=48, b=0))
        st.plotly_chart(state_map, use_container_width=True)

        stacked = base.melt(
            id_vars=["candidate_name", "state", "office", "district", "party", "result"],
            value_vars=selected_cols,
            var_name="channel_col",
            value_name="spend",
        )
        channel_reverse = {v: k for k, v in media_cols.items()}
        stacked["Media"] = stacked["channel_col"].map(channel_reverse)
        # Build an enriched display label per candidate: "Name — Party · Race (ST)".
        PARTY_ABBREV = {
            "Democratic": "D",
            "Democrat": "D",
            "Republican": "R",
            "Independent": "I",
            "Libertarian": "L",
            "Green": "G",
            "Nonpartisan": "NP",
        }

        def _short_party(value: object) -> str:
            text = str(value or "").strip()
            if not text:
                return ""
            return PARTY_ABBREV.get(text, text)

        candidate_meta = (
            base.sort_values("selected_spend", ascending=False)
            .drop_duplicates(subset=["candidate_name"])
            .set_index("candidate_name")
        )
        all_ordered_candidates = candidate_meta.index.tolist()
        TOP_CANDIDATE_LIMIT = 50
        ordered_candidates = all_ordered_candidates[:TOP_CANDIDATE_LIMIT]

        def _display_label(name: str) -> str:
            row = candidate_meta.loc[name]
            party = _short_party(row.get("party"))
            race = office_label(row.get("office")) if row.get("office") else ""
            state = str(row.get("state") or "").strip()
            district = str(row.get("district") or "").strip()
            race_bits = []
            if race:
                race_bits.append(race)
            if state:
                loc = state if not district else f"{state}-{district}"
                race_bits.append(loc)
            tail_parts = []
            if party:
                tail_parts.append(party)
            if race_bits:
                tail_parts.append(" ".join(race_bits))
            tail = " · ".join(tail_parts)
            return f"{name} — {tail}" if tail else name

        label_lookup = {name: _display_label(name) for name in ordered_candidates}
        ordered_labels = [label_lookup[name] for name in ordered_candidates]
        stacked_all = stacked[stacked["candidate_name"].isin(ordered_candidates)].copy()
        stacked_all["candidate_label"] = stacked_all["candidate_name"].map(label_lookup)
        stacked_all["candidate_label"] = pd.Categorical(
            stacked_all["candidate_label"], categories=ordered_labels, ordered=True
        )
        showing = len(ordered_candidates)
        total_candidates = len(all_ordered_candidates)
        chart_title = (
            f"Top {showing} of {total_candidates} Candidates — Spend by Media"
            if total_candidates > showing
            else f"All {showing} Candidates — Spend by Media"
        )
        fig = px.bar(
            stacked_all,
            x="spend",
            y="candidate_label",
            color="Media",
            orientation="h",
            color_discrete_map=media_colors,
            hover_data=["state", "office", "district", "result", "party"],
            title=chart_title,
        )
        # Give every row enough vertical space so labels do not overlap or get clipped.
        chart_height = max(500, 28 * showing + 120)
        max_label_len = max((len(label) for label in ordered_labels), default=10)
        left_margin = min(440, max(200, 7 * max_label_len))
        fig.update_layout(
            barmode="stack",
            yaxis=dict(autorange="reversed", automargin=True, tickfont=dict(size=11), title=""),
            margin=dict(l=left_margin, r=20, t=56, b=40),
            height=chart_height,
        )
        st.plotly_chart(fig, use_container_width=True)

        detail_columns = [
            "candidate_name",
            "party",
            "source_party_affiliations",
            "advertiser_types",
            "source_data_types",
            "source_race_types",
            "agencies",
            "state",
            "office",
            "district",
            "result",
            "vote_share",
            *selected_cols,
            "selected_spend",
            "total_attributed_spend",
            "tv_mix",
        ]
        display = base[[column for column in detail_columns if column in base.columns]].copy()
        display["office"] = display["office"].map(office_label)
        display["tv_mix"] = display["tv_mix"].map(tv_mix_label)
        rename = {
            "candidate_name": "Candidate",
            "party": "Candidate Party",
            "source_party_affiliations": "Source Party Affiliation",
            "advertiser_types": "Advertiser Type",
            "source_data_types": "Data Type",
            "source_race_types": "Source Race Type",
            "agencies": "Agencies",
            "state": "State",
            "office": "Office",
            "district": "District",
            "result": "Result",
            "vote_share": "Vote Share",
            "selected_spend": "Selected Media $",
            "total_attributed_spend": "Total $",
            "tv_mix": "TV Cohort",
        }
        rename.update({v: f"{k} $" for k, v in media_cols.items() if v in display.columns})
        display.rename(columns=rename, inplace=True)
        for col in [c for c in display.columns if c.endswith("$")]:
            display[col] = display[col].map(lambda v: round(float(v), 2) if pd.notna(v) else v)
        display["Vote Share"] = display["Vote Share"].map(lambda v: round(float(v) * 100, 2) if pd.notna(v) else v)
        st.subheader("Candidate Detail")
        csv_download(display, "Download Spend Dashboard", "spend_dashboard.csv")
        st.dataframe(style_result_table(display, result_col="Result"), width="stretch", hide_index=True)

    def _aggregate_tab(kind: str, title: str) -> None:
        data = aggregate_df[aggregate_df.get("aggregate_type", pd.Series(dtype=str)) == kind].copy() if not aggregate_df.empty else pd.DataFrame()
        if data.empty:
            st.info(f"No {title.lower()} aggregate spend has been ingested yet.")
            return
        labels = sorted(data["label"].dropna().astype(str).unique())
        selected_labels = st.multiselect(f"{title} (blank = all)", labels, default=[], key=f"agg_{kind}_labels")
        if selected_labels:
            data = data[data["label"].isin(selected_labels)].copy()
        kpi_row(
            [
                ("Rows", format_int(data.shape[0]), None),
                ("Total Gross Spend", format_compact_currency(data["gross_spending"].sum()), None, "cable"),
                ("Largest", str(data.sort_values("gross_spending", ascending=False)["label"].iloc[0]), None, "broadcast"),
            ]
        )
        chart_data = data.sort_values("gross_spending", ascending=True).tail(30)
        row_count = len(chart_data)
        fig = px.bar(
            chart_data,
            x="gross_spending",
            y="label",
            orientation="h",
            title=f"Top {min(30, row_count)} {title} by Gross Spend",
            color="gross_spending",
            color_continuous_scale=["#D6EAF8", "#1F618D"],
        )
        # Size the chart so every bar + label is visible and not clipped.
        chart_height = max(420, 28 * row_count + 120)
        max_label_len = max((len(str(label)) for label in chart_data["label"]), default=10)
        left_margin = min(320, max(80, 8 * max_label_len))
        fig.update_layout(
            coloraxis_showscale=False,
            margin=dict(l=left_margin, r=24, t=56, b=48),
            height=chart_height,
            xaxis_title="Gross Spend",
            yaxis_title="",
            yaxis=dict(automargin=True, tickfont=dict(size=11)),
        )
        st.plotly_chart(fig, use_container_width=True)
        if kind == "state":
            states = data[data["label"].astype(str).str.len().eq(2)].copy()
            if not states.empty:
                fig = px.choropleth(
                    states,
                    locations="label",
                    locationmode="USA-states",
                    scope="usa",
                    color="gross_spending",
                    color_continuous_scale=["#EBF5FB", COLORS["cable_ctv_only"]],
                    title="Aggregate Spend by State",
                )
                fig.update_layout(margin=dict(l=0, r=0, t=48, b=0))
                st.plotly_chart(fig, use_container_width=True)
        table = data.rename(
            columns={
                "label": title,
                "share_of_total": "% of Total",
                "gross_spending": "Gross Spending",
                "source_file": "Source File",
            }
        )[[title, "% of Total", "Gross Spending", "Source File"]]
        csv_download(table, f"Download {title} Aggregates", f"{kind}_spend_aggregates.csv")
        st.dataframe(
            table,
            width="stretch",
            hide_index=True,
            column_config={
                "% of Total": st.column_config.NumberColumn(format="%.2f"),
                "Gross Spending": st.column_config.NumberColumn(format="$%.0f"),
            },
        )

    with tab_states:
        _aggregate_tab("state", "State")
    with tab_markets:
        _aggregate_tab("market", "Market / DMA")


def page_election_calendar(
    races_df: pd.DataFrame,
    upcoming_df: pd.DataFrame,
    candidate_df: pd.DataFrame,
    filters: dict[str, object],
) -> None:
    page_header(
        "Election Calendar & Race-Type Map",
        "See primary dates across the year and which states have Governor, Senate, House, or statewide exec races on the ballot.",
    )

    try:
        with sqlite3.connect(DB_PATH) as conn:
            schedule_df = pd.read_sql_query(
                "SELECT state, primary_date, runoff_date, has_us_senate, "
                "us_house_seats, us_house_note, general_date, source "
                "FROM state_primary_schedule ORDER BY primary_date, state",
                conn,
            )
    except Exception:
        schedule_df = pd.DataFrame()

    if not schedule_df.empty:
        st.subheader("Official 2026 State Primary Calendar")
        states_filter = filters["states"]
        cal_view = schedule_df[schedule_df["state"].isin(TARGET_STATES)].copy()
        if "All" not in states_filter:
            cal_view = cal_view[cal_view["state"].isin(states_filter)]
        cal_view["U.S. Senate"] = cal_view["has_us_senate"].map(
            lambda v: "Yes" if v == 1 else ("No" if v == 0 else "—")
        )
        cal_view["U.S. House Seats"] = cal_view.apply(
            lambda r: str(int(r["us_house_seats"])) if pd.notna(r["us_house_seats"]) else (r["us_house_note"] or "—"),
            axis=1,
        )
        cal_view = cal_view.rename(columns={
            "state": "State",
            "primary_date": "State Primary",
            "runoff_date": "Runoff",
            "general_date": "General",
            "source": "Source",
        })[["State", "State Primary", "Runoff", "U.S. Senate", "U.S. House Seats", "General", "Source"]]
        cal_view = cal_view.fillna("—")
        st.dataframe(cal_view, width="stretch", hide_index=True)
        csv_download(cal_view, "Download Primary Calendar", "state_primary_schedule_2026.csv")
        st.caption(f"Source: FVAP 2026 Primary Elections (as of Oct 2025). Showing {len(cal_view)} of {len(TARGET_STATES)} target states.")

    # ─── Political windows (DMA-level open + election dates) ──────────────
    try:
        with sqlite3.connect(DB_PATH) as conn:
            windows_df = pd.read_sql_query(
                "SELECT state, region, dma, window_type, window_open, election_date "
                "FROM political_windows WHERE cycle = 2026 ORDER BY election_date, window_open",
                conn,
            )
    except Exception:
        windows_df = pd.DataFrame()

    if not windows_df.empty:
        st.subheader("Political Advertising Windows by Market (DMA)")
        st.caption(
            "Each row is a window during which political advertising rates are governed by FCC "
            "lowest-unit-charge rules. Multi-state DMAs (e.g. DC/MD/VA) are kept verbatim; "
            "the November 3, 2026 General is consolidated into a single national window."
        )

        # Filter controls
        wcol1, wcol2, wcol3 = st.columns([1.2, 1.2, 1])
        with wcol1:
            wtypes = sorted(windows_df["window_type"].unique())
            sel_wtypes = st.multiselect(
                "Window type (blank = all)",
                wtypes,
                default=[],
                key="cal_wtype",
            )
        with wcol2:
            target_only = st.toggle(
                "Target states only",
                value=True,
                help=f"Limit to {len(TARGET_STATES)} target states (multi-state DMAs match if any state qualifies).",
                key="cal_targets_only",
            )
        with wcol3:
            upcoming_only = st.toggle(
                "Upcoming only",
                value=True,
                help="Show only windows whose election date is today or later.",
                key="cal_upcoming_only",
            )

        wf = windows_df.copy()
        if sel_wtypes:
            wf = wf[wf["window_type"].isin(sel_wtypes)]
        if target_only:
            target_set = set(TARGET_STATES)
            wf = wf[wf["state"].apply(lambda s: bool(set(str(s).split("/")) & target_set))]
        states_filter = filters.get("states") if isinstance(filters, dict) else None
        if states_filter and "All" not in states_filter:
            wf = wf[wf["state"].apply(lambda s: bool(set(str(s).split("/")) & set(states_filter)))]
        if upcoming_only:
            wf = wf[wf["election_date"] >= TODAY.strftime("%Y-%m-%d")]

        # Consolidate the national November 3, 2026 General into one row
        nov3 = (wf["window_type"] == "GENERAL") & (wf["election_date"] == "2026-11-03")
        nov3_rows = wf[nov3]
        non_nov3 = wf[~nov3]
        if not nov3_rows.empty:
            consolidated = pd.DataFrame([{
                "state": "ALL",
                "region": "NATIONAL",
                "dma": f"All markets ({nov3_rows['dma'].nunique()} DMAs)",
                "window_type": "GENERAL",
                "window_open": nov3_rows["window_open"].min(),
                "election_date": "2026-11-03",
            }])
            wf_display = pd.concat([consolidated, non_nov3], ignore_index=True)
        else:
            wf_display = non_nov3

        wf_display = wf_display.sort_values(["election_date", "window_open", "state", "dma"])

        # KPI strip
        n_windows = len(wf_display)
        n_dmas = windows_df["dma"].nunique()
        next_open = wf_display[wf_display["window_open"] >= TODAY.strftime("%Y-%m-%d")]
        next_open_date = next_open["window_open"].min() if not next_open.empty else "—"
        next_election = wf_display[wf_display["election_date"] >= TODAY.strftime("%Y-%m-%d")]
        next_election_date = next_election["election_date"].min() if not next_election.empty else "—"
        next_open_display = (
            pd.to_datetime(next_open_date).strftime("%b %d, %Y") if next_open_date != "—" else "—"
        )
        next_election_display = (
            pd.to_datetime(next_election_date).strftime("%b %d, %Y") if next_election_date != "—" else "—"
        )
        kc1, kc2, kc3, kc4 = st.columns(4)
        kc1.metric("Windows shown", f"{n_windows:,}")
        kc2.metric("DMAs covered", f"{n_dmas}")
        kc3.metric("Next window opens", next_open_display)
        kc4.metric("Next election date", next_election_display)

        type_summary = (
            wf_display.groupby("window_type", dropna=False)
            .agg(
                windows=("dma", "count"),
                markets=("dma", "nunique"),
                first_open=("window_open", "min"),
                next_election=("election_date", "min"),
            )
            .reset_index()
            .sort_values(["next_election", "first_open", "window_type"])
        )
        if not type_summary.empty:
            type_summary = type_summary.rename(
                columns={
                    "window_type": "Window Type",
                    "windows": "Windows",
                    "markets": "Markets / DMAs",
                    "first_open": "First Open",
                    "next_election": "Next Election",
                }
            )
            st.dataframe(type_summary, width="stretch", hide_index=True)

        # Display table
        view = wf_display.rename(columns={
            "state": "State",
            "region": "Region",
            "dma": "Market / DMA",
            "window_type": "Window Type",
            "window_open": "Window Opens",
            "election_date": "Election Date",
        })[["Election Date", "Window Type", "Window Opens", "State", "Region", "Market / DMA"]]
        st.dataframe(view, width="stretch", hide_index=True)
        csv_download(view, "Download Political Windows", "political_windows_2026.csv")

        # Timeline chart of windows by month
        wt = wf_display.copy()
        wt["election_dt"] = pd.to_datetime(wt["election_date"])
        wt["month"] = wt["election_dt"].dt.to_period("M").dt.to_timestamp()
        month_counts = wt.groupby(["month", "Window Type" if "Window Type" in wt.columns else "window_type"]).size() \
            .reset_index(name="windows")
        month_counts = month_counts.rename(columns={month_counts.columns[1]: "window_type"})
        timeline_w = px.bar(
            month_counts,
            x="month",
            y="windows",
            color="window_type",
            title="Political Windows by Month & Type",
        )
        timeline_w.update_layout(
            barmode="stack",
            margin=dict(l=0, r=0, t=48, b=0),
            xaxis_title="",
            yaxis_title="Windows",
        )
        st.plotly_chart(timeline_w, use_container_width=True)

    if not candidate_df.empty and "race_id" in candidate_df.columns:
        cols = [c for c in ["race_id", "state", "office", "district", "primary_date", "race_name"] if c in candidate_df.columns]
        base = candidate_df[cols].drop_duplicates(subset=["race_id"]).copy()
    elif not races_df.empty:
        base = races_df.copy()
    else:
        base = upcoming_df.copy()
    if base.empty:
        st.info("No race data loaded yet.")
        return

    base["primary_date"] = coerce_dates(base, "primary_date")
    states = filters["states"]
    offices = filters["offices"]
    working = base.copy()
    if "All" not in states:
        working = working[working["state"].isin(states)]
    if "All" not in offices:
        working = working[working["office"].isin(offices)]
    start, end = filters["date_range"]
    working = working[
        (working["primary_date"].isna())
        | ((working["primary_date"] >= pd.to_datetime(start)) & (working["primary_date"] <= pd.to_datetime(end)))
    ]

    statewide_offices = {"governor", "us_senate", "attorney_general"}
    by_state = (
        working.dropna(subset=["state"])
        .groupby("state")["office"]
        .apply(lambda s: sorted({str(v) for v in s if v}))
        .reset_index(name="offices")
    )
    if not schedule_df.empty:
        all_states = schedule_df[schedule_df["state"].isin(TARGET_STATES)][
            ["state", "has_us_senate", "us_house_seats"]
        ].copy()
        by_state = all_states.merge(by_state, on="state", how="left")
        by_state["offices"] = by_state["offices"].apply(lambda v: v if isinstance(v, list) else [])
        by_state["offices"] = by_state.apply(
            lambda r: sorted(set(r["offices"])
                             | ({"us_senate"} if r["has_us_senate"] == 1 else set())
                             | ({"us_house"} if pd.notna(r["us_house_seats"]) and r["us_house_seats"] > 0 else set())),
            axis=1,
        )

    by_state["has_governor"] = by_state["offices"].apply(lambda o: "governor" in o)
    by_state["has_senate"] = by_state["offices"].apply(lambda o: "us_senate" in o)
    by_state["has_house"] = by_state["offices"].apply(lambda o: "us_house" in o)
    by_state["has_ag"] = by_state["offices"].apply(lambda o: "attorney_general" in o)
    by_state["has_statewide"] = by_state["offices"].apply(lambda o: bool(set(o) & statewide_offices))
    by_state["statewide_count"] = by_state["offices"].apply(lambda o: len(set(o) & statewide_offices))
    by_state["office_labels"] = by_state["offices"].apply(
        lambda o: ", ".join(office_label(x) for x in sort_offices(o))
    )
    if "us_house_seats" in by_state.columns:
        by_state["house_seats_label"] = by_state["us_house_seats"].apply(
            lambda v: f"{int(v)} House seats" if pd.notna(v) and v > 0 else "—"
        )

    race_type = st.selectbox(
        "Color map by",
        ["Any statewide exec race", "Governor", "US Senate", "U.S. House", "Attorney General", "Race-type count"],
    )
    if race_type == "Governor":
        by_state["color_val"] = by_state["has_governor"].astype(int)
        scale = ["#E5E8E8", COLORS["mixed_tv"]]
    elif race_type == "US Senate":
        by_state["color_val"] = by_state["has_senate"].astype(int)
        scale = ["#E5E8E8", COLORS["cable_ctv_only"]]
    elif race_type == "U.S. House":
        by_state["color_val"] = by_state["us_house_seats"].fillna(0).astype(int) if "us_house_seats" in by_state.columns else by_state["has_house"].astype(int)
        scale = ["#EBF5FB", "#1F618D"]
    elif race_type == "Attorney General":
        by_state["color_val"] = by_state["has_ag"].astype(int)
        scale = ["#E5E8E8", COLORS["broadcast_only"]]
    elif race_type == "Race-type count":
        by_state["color_val"] = by_state["offices"].apply(lambda o: len(set(o)))
        scale = ["#EAECEE", "#1F618D"]
    else:
        by_state["color_val"] = by_state["has_statewide"].astype(int)
        scale = ["#E5E8E8", COLORS["won"]]

    hover = {"office_labels": True, "color_val": False}
    if "house_seats_label" in by_state.columns:
        hover["house_seats_label"] = True
    map_fig = px.choropleth(
        by_state,
        locations="state",
        locationmode="USA-states",
        scope="usa",
        color="color_val",
        hover_name="state",
        hover_data=hover,
        color_continuous_scale=scale,
        title=f"States With: {race_type}",
    )
    map_fig.update_layout(
        coloraxis_showscale=(race_type in ("U.S. House", "Race-type count")),
        margin=dict(l=0, r=0, t=48, b=0),
    )
    st.plotly_chart(map_fig, use_container_width=True)

    cal = working.dropna(subset=["primary_date"]).copy()
    if cal.empty:
        st.info("No dated primaries in the current filter range.")
        return
    cal["month"] = cal["primary_date"].dt.to_period("M").dt.to_timestamp()
    cal["office_display"] = cal["office"].map(office_label)
    month_rollup = (
        cal.groupby(["month", "office_display"])
        .size()
        .reset_index(name="races")
        .sort_values("month")
    )
    timeline = px.bar(
        month_rollup,
        x="month",
        y="races",
        color="office_display",
        title="Primary Calendar — Races by Month & Office",
    )
    timeline.update_layout(barmode="stack", margin=dict(l=0, r=0, t=48, b=0), xaxis_title="", yaxis_title="Races")
    st.plotly_chart(timeline, use_container_width=True)

    upcoming_mask = cal["primary_date"] >= TODAY
    upcoming_cal = cal[upcoming_mask].copy()
    if not upcoming_cal.empty:
        table = (
            upcoming_cal.groupby(["primary_date", "state"])
            .agg(
                races=("race_id", "nunique") if "race_id" in upcoming_cal.columns else ("state", "count"),
                offices=("office", lambda v: ", ".join(sorted({office_label(x) for x in v if x}))),
            )
            .reset_index()
            .sort_values(["primary_date", "state"])
        )
        table["primary_date"] = table["primary_date"].dt.strftime("%Y-%m-%d")
        table.rename(columns={"primary_date": "Date", "state": "State", "races": "Races", "offices": "Offices"}, inplace=True)
        st.subheader("Upcoming Primary Dates")
        st.dataframe(table, width="stretch", hide_index=True)
        csv_download(table, "Download Election Calendar", "election_calendar.csv")


def page_case_study_finder(candidate_df: pd.DataFrame, filters: dict[str, object]) -> None:
    page_header(
        "Case Study Finder",
        "Every candidate whose state primary has already happened, who did not win the race, "
        "and who did not spend on cable TV — sourced directly from spend records so candidates "
        "like Jason Friedman (whose primary view row is missing spend) still surface.",
    )

    # Query spend_records + candidates + races + state_primary_schedule directly.
    # This bypasses the precomputed `primaries_per_candidate` table, which
    # silently zero-spends candidates whose race shell is general-only
    # (Jason Friedman) or whose entity-resolution attribution is split.
    conn = sqlite3.connect(DB_PATH)
    try:
        query = """
        WITH cand_spend AS (
            SELECT s.candidate_id,
                SUM(CASE WHEN s.media_type='broadcast_tv' THEN s.gross_amount ELSE 0 END) AS broadcast_spend,
                SUM(CASE WHEN s.media_type='cable'        THEN s.gross_amount ELSE 0 END) AS cable_spend,
                SUM(CASE WHEN s.media_type='ctv'          THEN s.gross_amount ELSE 0 END) AS ctv_spend,
                SUM(CASE WHEN s.media_type='digital'      THEN s.gross_amount ELSE 0 END) AS digital_spend,
                SUM(CASE WHEN s.media_type='radio'        THEN s.gross_amount ELSE 0 END) AS radio_spend,
                SUM(s.gross_amount) AS total_spend
            FROM spend_records s
            WHERE s.race_id != 'unresolved'
            GROUP BY s.candidate_id
        ),
        state_primary AS (
            SELECT state, MIN(primary_date) AS state_primary_date
            FROM state_primary_schedule
            WHERE primary_date IS NOT NULL AND primary_date != ''
            GROUP BY state
        )
        SELECT
            c.candidate_id, c.full_name, c.party, c.result, c.vote_share,
            r.state, r.office, r.district, r.race_id, r.election_name,
            sp.state_primary_date,
            COALESCE(cs.broadcast_spend, 0) AS broadcast_spend,
            COALESCE(cs.cable_spend, 0)     AS cable_spend,
            COALESCE(cs.ctv_spend, 0)       AS ctv_spend,
            COALESCE(cs.digital_spend, 0)   AS digital_spend,
            COALESCE(cs.radio_spend, 0)     AS radio_spend,
            COALESCE(cs.total_spend, 0)     AS total_spend
        FROM candidates c
        JOIN races r ON r.race_id = c.race_id
        LEFT JOIN cand_spend cs    ON cs.candidate_id = c.candidate_id
        LEFT JOIN state_primary sp ON sp.state = r.state
        WHERE r.cycle = 2026
        """
        df = pd.read_sql(query, conn)
    finally:
        conn.close()

    if df.empty:
        st.info("No 2026 candidate records loaded.")
        return

    # ─── Apply global filters (state / office / party) ──────────────────
    states = filters.get("states", ["All"])
    offices = filters.get("offices", ["All"])
    if isinstance(states, list) and "All" not in states:
        df = df[df["state"].isin(states)]
    if isinstance(offices, list) and "All" not in offices:
        df = df[df["office"].isin(offices)]
    party = str(filters.get("party", "All"))
    if party == "Other":
        df = df[~df["party"].isin(["Democratic", "Republican", "", None])]
    elif party != "All":
        df = df[df["party"] == party]

    # ─── Collapse multiple candidate_ids per (name, state, office) ──────
    # Same problem as the zero-cable page: the same person can have a
    # primary shell + a general shell, or a redistricting leftover.
    today = pd.Timestamp.today().normalize()
    df["name_key"] = df["full_name"].astype(str).str.lower().str.strip()
    df["__party_priority"] = df["party"].astype(str).str.upper().isin(
        {"D", "R", "DEM", "REP", "DEMOCRAT", "DEMOCRATIC", "REPUBLICAN"}
    ).map({True: 0, False: 1})
    df = (
        df.sort_values(
            by=["name_key", "state", "office", "__party_priority", "total_spend"],
            ascending=[True, True, True, True, False],
        )
        .groupby(["name_key", "state", "office"], as_index=False)
        .agg(
            full_name=("full_name", "first"),
            party=("party", "first"),
            district=("district", "first"),
            race_id=("race_id", "first"),
            election_name=("election_name", "first"),
            result=("result", "first"),
            vote_share=("vote_share", "max"),
            state_primary_date=("state_primary_date", "first"),
            # Sum spend across stale duplicates (entity-resolver fan-out).
            broadcast_spend=("broadcast_spend", "max"),
            cable_spend=("cable_spend", "max"),
            ctv_spend=("ctv_spend", "max"),
            digital_spend=("digital_spend", "max"),
            radio_spend=("radio_spend", "max"),
            total_spend=("total_spend", "max"),
        )
    )

    # ─── Filter to "did not win, didn't spend on cable, election ran" ──
    # cable_spend < $1k counts as "didn't spend on cable" (covers stray
    # rounding-artifact rows like Paul Friedman's $14 cable line).
    primary_dt = pd.to_datetime(df["state_primary_date"], errors="coerce")
    election_passed = primary_dt.notna() & (primary_dt < today)

    NEAR_ZERO_CABLE = 1000.0  # dollars
    df["cable_share"] = df["cable_spend"] / df["total_spend"].replace(0, pd.NA)
    cable_clean = (df["cable_spend"] < NEAR_ZERO_CABLE) | (df["cable_share"].fillna(0) < 0.005)

    df = df[
        election_passed
        & (df["result"] != "won")
        & cable_clean
        & (df["total_spend"] > 0)
    ].copy()

    if df.empty:
        st.info(
            "No candidates match: state primary already happened, didn't win, "
            "and zero (or near-zero) cable spend."
        )
        return

    # ─── Build display frame ────────────────────────────────────────────
    df["TV Spend"] = df["broadcast_spend"] + df["ctv_spend"]
    def _tv_mix_from_spend(row: pd.Series) -> str:
        b, c, ct = float(row["broadcast_spend"]), float(row["cable_spend"]), float(row["ctv_spend"])
        return classify_tv_cohort(b, c, ct)

    df["tv_mix_code"] = df.apply(_tv_mix_from_spend, axis=1)
    df["TV Mix"] = df["tv_mix_code"].map(tv_mix_label)
    df["Office"] = df["office"].map(office_label)
    df["Vote %"] = (df["vote_share"].fillna(0) * 100).round(2)
    df["Race"] = df["state"] + df["district"].fillna("").astype(str).map(
        lambda d: f"-{d}" if d else ""
    )

    show = df.rename(
        columns={
            "full_name": "Candidate",
            "party": "Party",
            "state": "State",
            "district": "District",
            "result": "Result",
            "state_primary_date": "Primary Date",
            "broadcast_spend": "Broadcast $",
            "cable_spend": "Cable $",
            "ctv_spend": "CTV $",
            "digital_spend": "Digital $",
            "radio_spend": "Radio $",
            "total_spend": "Total $",
        }
    )
    cols = [
        "Candidate", "Party", "State", "District", "Office", "Race",
        "Primary Date", "Result", "Vote %", "TV Mix",
        "Broadcast $", "CTV $", "Digital $", "Radio $", "Cable $", "Total $",
    ]
    show = show[cols].sort_values("Total $", ascending=False).reset_index(drop=True)

    # ─── KPIs ───────────────────────────────────────────────────────────
    kpi_row(
        [
            ("No-Cable Losing Candidates", format_int(show.shape[0]), None),
            ("States Represented", format_int(show["State"].nunique()), None),
            ("Total $ Spent (no cable)", format_currency(float(show["Total $"].sum())), None),
            ("Median Spend per Candidate", format_currency(float(show["Total $"].median())), None),
        ]
    )

    # ─── Top-20 chart ───────────────────────────────────────────────────
    chart_df = show.head(20).copy()
    chart_df["Label"] = (
        chart_df["Candidate"]
        + " ("
        + chart_df["Party"].fillna("").astype(str)
        + " · "
        + chart_df["Race"]
        + ")"
    )
    fig = go.Figure()
    for col, label, color in zip(
        ["Broadcast $", "CTV $", "Digital $", "Radio $"],
        ["Broadcast", "CTV", "Digital", "Radio"],
        [PSI_COLORS["broadcast"], PSI_COLORS["ctv"], PSI_COLORS["digital"], PSI_COLORS["radio"]],
    ):
        fig.add_trace(
            go.Bar(
                name=label,
                y=chart_df["Label"],
                x=chart_df[col],
                orientation="h",
                marker_color=color,
                marker_line_width=0,
            )
        )
    fig.update_layout(
        barmode="stack",
        title="Top 20 no-cable losing candidates by total spend",
        height=max(420, 28 * len(chart_df) + 120),
        margin=dict(l=240, r=24, t=56, b=40),
        xaxis_title="Spend ($)",
        yaxis=dict(autorange="reversed", automargin=True, tickfont=dict(size=11), title=""),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ─── Table ──────────────────────────────────────────────────────────
    st.subheader("All no-cable losing candidates")
    csv_download(show, "Download Case Studies", "case_studies_no_cable_losers.csv")
    st.dataframe(
        show,
        width="stretch",
        hide_index=True,
        column_config={
            c: st.column_config.NumberColumn(format="$%.0f")
            for c in ["Broadcast $", "CTV $", "Digital $", "Radio $", "Cable $", "Total $"]
        }
        | {"Vote %": st.column_config.NumberColumn(format="%.2f")},
    )


def page_pac_spend(filters: dict[str, object]) -> None:
    page_header(
        "PAC Spend Tracker",
        "FEC independent-expenditure data: which PACs are spending, who they're supporting or opposing, and where the money is going.",
    )
    try:
        with sqlite3.connect(DB_PATH) as conn:
            ie = pd.read_sql_query(
                "SELECT cycle, committee_id, committee_name, candidate_id, candidate_name, "
                "office, state, district, support_oppose, total_spent, transaction_count "
                "FROM fec_ie_by_committee WHERE cycle = 2026",
                conn,
            )
    except Exception:
        st.info("No FEC IE data loaded yet. Run `fetch_fec_ie.py` to populate.")
        return
    if ie.empty:
        st.info("No FEC IE data in the 2026 cycle yet.")
        return

    office_map = {"S": "us_senate", "H": "us_house", "P": "president"}
    ie["office_normalized"] = ie["office"].map(office_map).fillna(ie["office"])
    ie["Stance"] = ie["support_oppose"].map({"S": "Support", "O": "Oppose"}).fillna(ie["support_oppose"])

    states = filters["states"]
    offices = filters["offices"]
    working = ie.copy()
    if "All" not in states:
        working = working[working["state"].isin(states)]
    if "All" not in offices:
        working = working[working["office_normalized"].isin(offices)]

    stance_filter = st.radio("Stance", ["All", "Support", "Oppose"], horizontal=True)
    if stance_filter != "All":
        working = working[working["Stance"] == stance_filter]

    if working.empty:
        st.info("No PAC spend matches the current filters.")
        return

    total_spend = float(working["total_spent"].sum())
    support_spend = float(working[working["Stance"] == "Support"]["total_spent"].sum())
    oppose_spend = float(working[working["Stance"] == "Oppose"]["total_spent"].sum())
    kpi_row([
        ("Total IE Spend", format_currency(total_spend), None),
        ("Support Spend", format_currency(support_spend), None),
        ("Oppose Spend", format_currency(oppose_spend), None),
        ("Active PACs", format_int(working["committee_id"].nunique()), None),
        ("Candidates Targeted", format_int(working["candidate_id"].nunique()), None),
    ])

    row1 = st.columns(2)
    with row1[0]:
        top_pacs = (
            working.groupby("committee_name")["total_spent"].sum()
            .sort_values(ascending=True).tail(20).reset_index()
        )
        fig = px.bar(
            top_pacs, x="total_spent", y="committee_name", orientation="h",
            title="Top 20 PACs by IE Spend",
            color="total_spent",
            color_continuous_scale=["#EBF5FB", "#1F618D"],
        )
        fig.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=0, t=48, b=0), yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    with row1[1]:
        top_cands = (
            working.groupby(["candidate_name", "Stance"])["total_spent"].sum().reset_index()
            .sort_values("total_spent", ascending=False).head(40)
        )
        fig = px.bar(
            top_cands, x="total_spent", y="candidate_name", color="Stance", orientation="h",
            color_discrete_map={"Support": COLORS["won"], "Oppose": COLORS["lost"]},
            title="Top Candidates by IE Spend (support vs oppose)",
        )
        fig.update_layout(yaxis=dict(autorange="reversed"), margin=dict(l=0, r=0, t=48, b=0), yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    state_roll = working.groupby("state")["total_spent"].sum().reset_index()
    state_map = px.choropleth(
        state_roll, locations="state", locationmode="USA-states", scope="usa",
        color="total_spent", color_continuous_scale=["#EBF5FB", COLORS["cable_ctv_only"]],
        title="IE Spend by State",
    )
    state_map.update_layout(margin=dict(l=0, r=0, t=48, b=0))
    st.plotly_chart(state_map, use_container_width=True)

    st.subheader("PAC → Candidate Detail")
    detail = working.copy()
    detail["Office"] = detail["office_normalized"].map(office_label)
    detail = detail.rename(columns={
        "committee_name": "PAC",
        "candidate_name": "Candidate",
        "state": "State",
        "district": "District",
        "total_spent": "Spend",
        "transaction_count": "# Transactions",
    })[["PAC", "Candidate", "Stance", "State", "Office", "District", "Spend", "# Transactions"]]
    detail = detail.sort_values("Spend", ascending=False)
    csv_download(detail, "Download PAC Spend", "pac_spend.csv")
    st.dataframe(detail, width="stretch", hide_index=True)


def _letter_grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"


def page_cable_effectiveness(candidate_df: pd.DataFrame, filters: dict[str, object]) -> None:
    page_header(
        "Cable-in-Mix Effectiveness",
        "For every race with both a Cable-in-mix candidate and a non-cable TV opponent, grade how the Cable-in-mix candidate performed vs the rest of the field.",
    )
    base = apply_candidate_filters(completed_primaries(candidate_df), filters, apply_tv_floor=False)
    if base.empty:
        st.info("No completed primaries match the current filters.")
        return
    base = base[base["tv_spend"].fillna(0) > 0].copy()
    base["cable_spend_f"] = base.get("cable_spend", 0).fillna(0)
    base["is_cable"] = base["cable_spend_f"] > 0

    rows = []
    for race_id, grp in base.groupby("race_id"):
        cable = grp[grp["is_cable"]]
        other = grp[~grp["is_cable"]]
        if cable.empty or other.empty:
            continue
        cable_best = cable.sort_values("vote_share", ascending=False).iloc[0]
        other_best = other.sort_values("vote_share", ascending=False).iloc[0]
        cable_won = int((cable["result"] == "won").any())
        cable_vs = float(cable_best.get("vote_share") or 0) * 100
        other_vs = float(other_best.get("vote_share") or 0) * 100
        vote_lift = cable_vs - other_vs
        cable_cpv = float(cable_best.get("tv_per_vote") or 0)
        other_cpv = float(other_best.get("tv_per_vote") or 0)
        cpv_edge = (other_cpv - cable_cpv) if (cable_cpv > 0 and other_cpv > 0) else None
        rows.append({
            "race_id": race_id,
            "State": cable_best["state"],
            "Office": office_label(cable_best["office"]),
            "District": cable_best.get("district") or "",
            "Race": cable_best.get("race_name", ""),
            "Cable Candidate": cable_best["candidate_name"],
            "Cable Won": "Won" if cable_won else "Lost",
            "Cable Vote %": round(cable_vs, 2),
            "Field Best Vote %": round(other_vs, 2),
            "Vote Lift (pp)": round(vote_lift, 2),
            "Cable TV $/Vote": round(cable_cpv, 2),
            "Field TV $/Vote": round(other_cpv, 2),
            "CPV Edge ($/vote saved)": round(cpv_edge, 2) if cpv_edge is not None else None,
            "Cable TV $": float(cable_best.get("tv_spend") or 0),
        })

    if not rows:
        st.info("No races have both a Cable-in-mix candidate and a non-cable opponent in this filter set.")
        return

    df = pd.DataFrame(rows)
    win_rate = (df["Cable Won"] == "Won").mean()
    median_lift = df["Vote Lift (pp)"].median()
    cpv_series = df["CPV Edge ($/vote saved)"].dropna()
    median_cpv_edge = cpv_series.median() if not cpv_series.empty else 0.0

    win_score = win_rate * 100
    lift_score = max(0.0, min(100.0, 50 + median_lift * 5))
    cpv_score = max(0.0, min(100.0, 50 + float(median_cpv_edge) * 2))
    overall = 0.5 * win_score + 0.3 * lift_score + 0.2 * cpv_score
    grade = _letter_grade(overall)

    grade_colors = {"A": "#27AE60", "B": "#2ECC71", "C": "#F1C40F", "D": "#E67E22", "F": "#C0392B"}
    st.markdown(
        f"""
        <div style="background:{grade_colors[grade]}22;border-left:8px solid {grade_colors[grade]};padding:18px 22px;border-radius:8px;margin-bottom:16px;">
        <div style="font-size:42px;font-weight:700;color:{grade_colors[grade]};line-height:1;">Grade: {grade}</div>
        <div style="margin-top:6px;">Overall effectiveness score: <strong>{overall:.1f}/100</strong> across {len(df)} head-to-head races.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    kpi_row([
        ("Head-to-Head Races", format_int(len(df)), None, "ctv"),
        ("Cable-in-Mix Win Rate", format_pct(win_rate), None, "positive" if win_rate >= 0.5 else "negative"),
        ("Median Vote Lift (pp)", f"{median_lift:+.2f}", None, "positive" if median_lift >= 0 else "negative"),
        ("Median CPV Edge ($/vote)", f"${float(median_cpv_edge):+,.2f}" if cpv_series.shape[0] else "—", None, "cable"),
    ])

    sub = st.columns(3)
    with sub[0]:
        with metric_accent("cable"):
            st.metric("Win-Rate Sub-Score", f"{win_score:.0f}/100")
    with sub[1]:
        with metric_accent("ctv"):
            st.metric("Vote-Lift Sub-Score", f"{lift_score:.0f}/100")
    with sub[2]:
        with metric_accent("positive" if cpv_score >= 50 else "negative"):
            st.metric("CPV-Edge Sub-Score", f"{cpv_score:.0f}/100")

    st.caption(
        "Grading: 50% Cable-in-mix win rate, 30% vote-share lift vs best non-cable opponent, 20% TV $/vote savings. "
        "Lift and CPV scores are anchored at 50 (parity) and move ±5 points per pp / ±2 per $ saved."
    )

    chart = px.bar(
        df.sort_values("Vote Lift (pp)", ascending=True).tail(25),
        x="Vote Lift (pp)",
        y="Cable Candidate",
        orientation="h",
        color="Cable Won",
        color_discrete_map={"Won": COLORS["won"], "Lost": COLORS["lost"]},
        hover_data=["State", "Office", "District", "Cable Vote %", "Field Best Vote %", "CPV Edge ($/vote saved)"],
        title="Top 25 Cable-in-Mix Races by Vote Lift vs Field",
    )
    chart.update_layout(margin=dict(l=0, r=0, t=48, b=0))
    st.plotly_chart(chart, use_container_width=True)

    st.subheader("Race-by-Race Detail")
    csv_download(df.drop(columns=["race_id"]), "Download Cable Effectiveness", "cable_effectiveness.csv")
    st.dataframe(df.drop(columns=["race_id"]), width="stretch", hide_index=True)


def main() -> None:
    metadata = STORE.get_metadata()
    cache_token = "|".join(
        [
            metadata.get("last_analysis_refresh", ""),
            metadata.get("last_civicapi_refresh", ""),
            metadata.get("last_fec_refresh", ""),
            metadata.get("last_advertiser_ingest", ""),
        ]
    )

    summary_df = load_table("summary_kpis", cache_token)
    coverage_df = load_table("state_coverage", cache_token)
    candidate_df = load_table("primaries_per_candidate", cache_token)
    candidate_df = apply_tv_cohort(candidate_df)
    advertiser_df = load_table("primaries_per_advertiser", cache_token)
    broadcast_df = load_table("broadcast_only_advertisers", cache_token)
    cable_df = load_table("cable_ctv_only_advertisers", cache_token)
    upcoming_df = load_table("upcoming_primaries", cache_token)
    races_df = load_table("upcoming_primaries", cache_token)

    sidebar_logo("PSI.", "Political Spend Intelligence")

    CORE_PAGES = [
        "Setup / Data Health",
        "Election Calendar",
        "Spend Dashboard",
        "Polling Center",
    ]
    PITCH_PAGES = [
        "Broadcast Waste",
        "Cable-in-Mix Effectiveness",
        "Zero-Cable Opportunities",
    ]
    SUPP_PAGES = [
        "PAC Spend Tracker",
        "Demographics",
        "Completed Primaries & Cost-Per-Vote",
        "Case Study Finder",
        "Race Explorer",
        "Active Races & Outlook",
    ]
    ALL_PAGES = CORE_PAGES + PITCH_PAGES + SUPP_PAGES

    if "selected_page" not in st.session_state:
        st.session_state.selected_page = "Setup / Data Health"

    def _on_nav_change(group_key: str, group_pages: list[str]) -> None:
        choice = st.session_state.get(group_key)
        if choice:
            st.session_state.selected_page = choice
            for k in ("nav_core", "nav_pitch", "nav_supp"):
                if k != group_key:
                    st.session_state[k] = None

    def _idx(group: list[str]) -> int | None:
        cur = st.session_state.selected_page
        return group.index(cur) if cur in group else None

    sidebar_section_label("Core")
    st.sidebar.radio(
        "Core",
        CORE_PAGES,
        index=_idx(CORE_PAGES),
        key="nav_core",
        label_visibility="collapsed",
        on_change=_on_nav_change,
        args=("nav_core", CORE_PAGES),
    )
    sidebar_section_label("Pitch")
    st.sidebar.radio(
        "Pitch",
        PITCH_PAGES,
        index=_idx(PITCH_PAGES),
        key="nav_pitch",
        label_visibility="collapsed",
        on_change=_on_nav_change,
        args=("nav_pitch", PITCH_PAGES),
    )
    sidebar_section_label("Supplemental")
    st.sidebar.radio(
        "Supplemental",
        SUPP_PAGES,
        index=_idx(SUPP_PAGES),
        key="nav_supp",
        label_visibility="collapsed",
        on_change=_on_nav_change,
        args=("nav_supp", SUPP_PAGES),
    )
    st.sidebar.divider()
    page = st.session_state.selected_page

    filters = render_global_filters(candidate_df, upcoming_df)

    if page == "Setup / Data Health":
        page_setup(filters, summary_df, coverage_df, races_df, candidate_df, metadata)
        return

    if candidate_df.empty:
        st.title(page)
        st.info("No analysis tables are loaded yet. Fetch CivicAPI data, ingest an advertiser file, then re-run analysis.")
        return

    if page == "Election Calendar":
        page_election_calendar(races_df, upcoming_df, candidate_df, filters)
    elif page == "Spend Dashboard":
        page_spend_dashboard(candidate_df, filters)
    elif page == "Polling Center":
        page_polling(candidate_df, filters)
    elif page == "Demographics":
        page_demographics(filters)
    elif page == "Completed Primaries & Cost-Per-Vote":
        page_completed_primaries(candidate_df, filters)
    elif page == "Broadcast Waste":
        page_broadcast_waste(candidate_df, broadcast_df, cable_df, filters)
    elif page == "PAC Spend Tracker":
        page_pac_spend(filters)
    elif page == "Cable-in-Mix Effectiveness":
        page_cable_effectiveness(candidate_df, filters)
    elif page == "Zero-Cable Opportunities":
        page_zero_cable(filters)
    elif page == "Case Study Finder":
        page_case_study_finder(candidate_df, filters)
    elif page == "Race Explorer":
        page_race_explorer(candidate_df, advertiser_df, filters)
    else:
        page_active_outlook(candidate_df, upcoming_df, filters)


def page_zero_cable(filters: dict[str, object]) -> None:
    """Active 2026 campaigns spending real money on TV but $0 on cable.

    These are the highest-leverage pitch leads: they're already TV buyers
    (proves budget + intent), they have a still-pending election (room to
    convert), and they have made a category decision against cable that
    can be challenged with district/DMA overlap data.
    """
    page_header(
        "Zero-Cable Opportunities",
        "Active 2026 campaigns spending real money on broadcast, CTV, digital, or radio "
        "but $0 on cable — or only a trivial amount of cable when total spend exceeds $1M.",
    )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Active = primary still upcoming OR no primary recorded but cycle=2026
    query = """
    WITH cand_spend AS (
        SELECT
            s.candidate_id,
            SUM(CASE WHEN s.media_type = 'broadcast_tv' THEN s.gross_amount ELSE 0 END) AS broadcast_spend,
            SUM(CASE WHEN s.media_type = 'cable'     THEN s.gross_amount ELSE 0 END) AS cable_spend,
            SUM(CASE WHEN s.media_type = 'ctv'       THEN s.gross_amount ELSE 0 END) AS ctv_spend,
            SUM(CASE WHEN s.media_type = 'digital'   THEN s.gross_amount ELSE 0 END) AS digital_spend,
            SUM(CASE WHEN s.media_type = 'radio'     THEN s.gross_amount ELSE 0 END) AS radio_spend,
            SUM(s.gross_amount) AS total_spend,
            MAX(s.match_source) AS match_source,
            GROUP_CONCAT(DISTINCT NULLIF(s.agency, '')) AS agencies
        FROM spend_records s
        WHERE s.race_id != 'unresolved'
        GROUP BY s.candidate_id
    ),
    primary_dates AS (
        SELECT
            state,
            MIN(CASE WHEN primary_date >= date('now') THEN primary_date END) AS next_primary,
            MIN(primary_date) AS state_primary_date
        FROM state_primary_schedule
        GROUP BY state
    ),
    -- Per-candidate_id spend so we can rank districts within a person.
    cand_id_total AS (
        SELECT
            c.candidate_id,
            LOWER(TRIM(c.full_name)) AS name_key,
            r.state, r.office,
            r.district,
            CAST(NULLIF(REPLACE(LTRIM(COALESCE(r.district,''),'0'),' ',''), '')
                 AS TEXT) AS district_key,
            COALESCE(cs.total_spend, 0) AS cid_total_spend
        FROM candidates c
        JOIN races r ON r.race_id = c.race_id
        LEFT JOIN cand_spend cs ON cs.candidate_id = c.candidate_id
        WHERE r.cycle = 2026
    ),
    -- Pick a single canonical district per (person, state, office). Drops
    -- stale candidate_ids left over from old maps (e.g., Kevin Kiley CA-6
    -- after redistricting moved him to CA-3, where the CA-6 row is a
    -- shell with party 'I'). Priority:
    --   1) candidate_id whose party is a recognized major party (D/R)
    --      — stale shell rows often have placeholder parties like 'I'
    --   2) highest individual spend on that candidate_id
    --   3) lowest district number for determinism
    canonical_district AS (
        SELECT name_key, state, office, district, district_key
        FROM (
            SELECT cit.name_key, cit.state, cit.office, cit.district, cit.district_key,
                   ROW_NUMBER() OVER (
                       PARTITION BY cit.name_key, cit.state, cit.office
                       ORDER BY
                           CASE WHEN UPPER(COALESCE(c.party,'')) IN
                                ('D','R','DEM','REP','DEMOCRAT','DEMOCRATIC','REPUBLICAN')
                                THEN 0 ELSE 1 END ASC,
                           cit.cid_total_spend DESC,
                           CAST(NULLIF(cit.district_key,'') AS INTEGER) ASC NULLS LAST,
                           cit.district_key ASC
                   ) AS rn
            FROM cand_id_total cit
            JOIN candidates c ON c.candidate_id = cit.candidate_id
        ) t
        WHERE rn = 1
    ),
    -- Collapse duplicate candidate records to one row per person+state+office.
    cand_dedup AS (
        SELECT
            LOWER(TRIM(c.full_name)) AS name_key,
            r.state                  AS state,
            r.office                 AS office,
            MIN(c.full_name)         AS full_name,
            MIN(c.party)             AS party,
            MAX(c.incumbent)         AS incumbent,
            MAX(CASE WHEN c.result = 'won'  THEN 1 ELSE 0 END) AS won_primary,
            MAX(CASE WHEN c.result = 'lost' THEN 1 ELSE 0 END) AS lost_primary
        FROM candidates c JOIN races r ON r.race_id = c.race_id
        WHERE r.cycle = 2026
        GROUP BY name_key, state, r.office
    ),
    cand_spend_dedup AS (
        SELECT
            cd.name_key, cd.state, cd.office,
            cdis.district, cdis.district_key,
            MIN(cd.full_name) AS full_name,
            MIN(cd.party)     AS party,
            MAX(cd.incumbent) AS incumbent,
            MAX(cd.won_primary)  AS won_primary,
            MAX(cd.lost_primary) AS lost_primary,
            SUM(cs.broadcast_spend) AS broadcast_spend,
            SUM(cs.cable_spend)     AS cable_spend,
            SUM(cs.ctv_spend)       AS ctv_spend,
            SUM(cs.digital_spend)   AS digital_spend,
            SUM(cs.radio_spend)     AS radio_spend,
            SUM(cs.total_spend)     AS total_spend,
            GROUP_CONCAT(DISTINCT cs.match_source) AS match_source,
            GROUP_CONCAT(DISTINCT cs.agencies)     AS agencies
        FROM cand_dedup cd
        LEFT JOIN canonical_district cdis
               ON cdis.name_key = cd.name_key
              AND cdis.state    = cd.state
              AND cdis.office   = cd.office
        JOIN candidates c2 ON LOWER(TRIM(c2.full_name)) = cd.name_key
        JOIN races r2      ON r2.race_id = c2.race_id AND r2.state = cd.state AND r2.office = cd.office
        JOIN cand_spend cs ON cs.candidate_id = c2.candidate_id
        WHERE r2.cycle = 2026
          -- Only count spend for the candidate_id tied to the canonical district.
          AND CAST(NULLIF(REPLACE(LTRIM(COALESCE(r2.district,''),'0'),' ',''), '') AS TEXT)
              = cdis.district_key
        GROUP BY cd.name_key, cd.state, cd.office, cdis.district, cdis.district_key
    )
    SELECT
        csd.full_name,
        csd.party,
        csd.incumbent,
        csd.state,
        csd.district,
        csd.office,
        csd.broadcast_spend, csd.cable_spend, csd.ctv_spend,
        csd.digital_spend,   csd.radio_spend,  csd.total_spend,
        csd.match_source,
        csd.agencies,
        csd.won_primary,
        csd.lost_primary,
        pd.next_primary,
        pd.state_primary_date,
        GROUP_CONCAT(DISTINCT dm.dma) AS dmas,
        MAX(dm.comcast_footprint)    AS comcast_footprint,
        COALESCE(MAX(NULLIF(dm.incumbent, '')), MAX(NULLIF(hr.incumbent_name, ''))) AS current_rep,
        MAX(hr.incumbent_party)      AS current_rep_party
    FROM cand_spend_dedup csd
    LEFT JOIN primary_dates pd ON pd.state = csd.state
    LEFT JOIN dma_district_map dm
           ON dm.state = csd.state
          AND CAST(NULLIF(REPLACE(LTRIM(COALESCE(dm.district,''),'0'),' ',''), '') AS TEXT) = csd.district_key
          AND dm.cycle = 2026
    LEFT JOIN house_roster hr
           ON hr.state = csd.state
          AND CAST(NULLIF(REPLACE(LTRIM(COALESCE(hr.district,''),'0'),' ',''), '') AS TEXT) = csd.district_key
          AND hr.cycle = 2026
    -- Cable filter: $0 cable, OR very small cable share when total spend is large.
    WHERE (
            csd.cable_spend = 0
            OR (
                csd.total_spend > 1000000
                AND csd.cable_spend < 50000
                AND csd.cable_spend < csd.total_spend * 0.05
            )
          )
      AND (csd.broadcast_spend + csd.ctv_spend) > 0
    GROUP BY csd.name_key, csd.state, csd.office, csd.district_key
    ORDER BY csd.total_spend DESC
    """
    df = pd.read_sql(query, conn)
    conn.close()

    if df.empty:
        st.info("No active 2026 campaigns with TV spend and zero cable were found.")
        return

    # ─── Drop candidates whose election has already happened ─────────────
    # Many candidates carry result='pending' even after their state primary
    # has run (e.g., Jason Friedman: IL primary 2026-03-17, lost, but row
    # still says pending). Treat the state's primary_date as authoritative:
    # if that date is in the past, the candidate must have a 'won' result
    # to remain (they advanced to the general). Otherwise they are
    # considered eliminated and excluded from active opportunities.
    today = pd.Timestamp.today().normalize()
    primary_dt = pd.to_datetime(df["state_primary_date"], errors="coerce")
    primary_passed = primary_dt.notna() & (primary_dt < today)
    won = df["won_primary"].fillna(0).astype(int).eq(1)
    lost = df["lost_primary"].fillna(0).astype(int).eq(1)

    n_dropped_lost = int(lost.sum())
    n_dropped_past = int((primary_passed & ~won & ~lost).sum())
    df = df[~lost & (~primary_passed | won)].reset_index(drop=True)
    if df.empty:
        st.info("All zero-cable candidates have already had their election. Re-ingest data to see new active campaigns.")
        return

    # ─── Filters (in-page, on top of global) ─────────────────────────────
    fcol1, fcol2, fcol3, fcol4 = st.columns([1, 1, 1, 1])
    with fcol1:
        floor = st.number_input(
            "Min total non-cable TV spend ($)",
            min_value=0, max_value=10_000_000, value=25_000, step=5_000,
            key="zc_floor",
        )
    with fcol2:
        offices = sorted(df["office"].unique())
        sel_offices = st.multiselect(
            "Office", offices,
            default=[o for o in ["us_house"] if o in offices] or offices,
            key="zc_office",
        )
    with fcol3:
        parties = sorted(df["party"].dropna().unique())
        sel_parties = st.multiselect(
            "Party", parties, default=parties, key="zc_party",
        )
    with fcol4:
        footprint_only = st.toggle(
            "Comcast footprint only",
            value=True,
            help="Limit to candidates whose district sits in a Comcast Advertising DMA.",
            key="zc_footprint_only",
        )

    # Apply global state filter if set
    state_filter = filters.get("states") if isinstance(filters, dict) else None
    df_f = df.copy()
    df_f = df_f[df_f["broadcast_spend"] + df_f["ctv_spend"] + df_f["digital_spend"] + df_f["radio_spend"] >= floor]
    if sel_offices:
        df_f = df_f[df_f["office"].isin(sel_offices)]
    if sel_parties:
        df_f = df_f[df_f["party"].isin(sel_parties)]
    if state_filter and "All" not in state_filter:
        df_f = df_f[df_f["state"].isin(state_filter)]
    if footprint_only and "comcast_footprint" in df_f.columns:
        df_f = df_f[df_f["comcast_footprint"].fillna(0).astype(int) == 1]

    if df_f.empty:
        st.info("No campaigns match the current filters. Lower the spend floor, widen office/party, or turn off the Comcast footprint toggle.")
        return

    # ─── KPI strip ──────────────────────────────────────────────────────
    n = len(df_f)
    total_noncable = float(df_f[["broadcast_spend", "ctv_spend", "digital_spend", "radio_spend"]].sum().sum())
    bcast_total = float(df_f["broadcast_spend"].sum())
    avg_bcast_share = (
        df_f["broadcast_spend"].sum()
        / df_f[["broadcast_spend", "ctv_spend", "digital_spend", "radio_spend"]].sum().sum()
        if total_noncable > 0 else 0.0
    )

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        with metric_accent("ctv"):
            st.metric("Active campaigns", f"{n:,}")
    with k2:
        with metric_accent("cable"):
            st.metric("Total non-cable TV spend", f"${total_noncable/1e6:,.1f}M")
    with k3:
        with metric_accent("broadcast"):
            st.metric("Broadcast share (avg)", f"{avg_bcast_share*100:.0f}%")
    with k4:
        with metric_accent("positive"):
            # Conservative shift estimate: 25% of broadcast → cable
            st.metric("Convertible (25% of bcast)", f"${bcast_total*0.25/1e6:,.1f}M")

    insight_callout(
        f"<b>{n} active 2026 campaigns</b> are buying TV but skipping cable entirely "
        f"(or with only trivial cable above the $1M threshold). "
        f"Combined non-cable TV spend: <b>${total_noncable/1e6:,.1f}M</b>. "
        f"These are confirmed TV buyers — the category decision against cable is the only barrier."
    )
    if n_dropped_lost or n_dropped_past:
        bits = []
        if n_dropped_lost:
            bits.append(f"{n_dropped_lost} marked 'lost' in their primary")
        if n_dropped_past:
            bits.append(f"{n_dropped_past} from states whose primary has already passed without a recorded win")
        st.caption("Excluded inactive candidates: " + "; ".join(bits) + ".")

    # ─── Chart: top 20 by broadcast spend ───────────────────────────────
    top = df_f.nlargest(20, "broadcast_spend").copy()
    top["label"] = top["full_name"] + " (" + top["state"] + "-" + top["district"].fillna("") + ")"

    with st.container(border=True):
        chart_headline(
            "Top 20 zero-cable campaigns by broadcast spend",
            sub="Each bar is a 2026 campaign already spending on broadcast TV with $0 on cable.",
        )
        fig = go.Figure()
        for col, label, color in zip(
            ["broadcast_spend", "ctv_spend", "digital_spend", "radio_spend"],
            ["Broadcast", "CTV", "Digital", "Radio"],
            [PSI_COLORS["broadcast"], PSI_COLORS["ctv"], PSI_COLORS["digital"], PSI_COLORS["radio"]],
        ):
            fig.add_trace(go.Bar(
                name=label, y=top["label"], x=top[col],
                orientation="h", marker_color=color, marker_line_width=0,
            ))
        fig.update_layout(
            barmode="stack",
            height=max(400, 22 * len(top)),
            xaxis_title="Spend ($)",
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True)
        chart_methodology(
            "Cable spend = 0 across all source records. Broadcast/CTV/Digital/Radio "
            "summed from spend_records joined to candidates joined to 2026 races. "
            "Match sources include direct committee, FEC IE fallback, and roster fuzzy."
        )

    # ─── Detail table ───────────────────────────────────────────────────
    show = df_f.copy()
    show["Race"] = show["state"] + "-" + show["district"].fillna("")
    show["Total"] = show["total_spend"]
    show["Broadcast"] = show["broadcast_spend"]
    show["CTV"] = show["ctv_spend"]
    show["Digital"] = show["digital_spend"]
    show["Radio"] = show["radio_spend"]
    if "comcast_footprint" in show.columns:
        show["Footprint"] = show["comcast_footprint"].fillna(0).astype(int).map({1: "✓", 0: "—"})
    else:
        show["Footprint"] = "—"
    if "current_rep" not in show.columns:
        show["current_rep"] = ""
    if "current_rep_party" not in show.columns:
        show["current_rep_party"] = ""
    if "agencies" not in show.columns:
        show["agencies"] = ""
    show["agencies"] = show["agencies"].fillna("").astype(str)
    show = show[[
        "full_name", "party", "Race", "office", "current_rep", "current_rep_party", "next_primary",
        "Total", "Broadcast", "CTV", "Digital", "Radio",
        "Footprint", "agencies", "dmas", "match_source",
    ]].rename(columns={
        "full_name": "Candidate", "party": "Party", "office": "Office",
        "current_rep": "Current Rep",
        "current_rep_party": "Current Rep Party",
        "next_primary": "Next primary",
        "agencies": "Agencies",
        "dmas": "DMAs", "match_source": "Source",
    })
    st.markdown("### All zero-cable campaigns")
    st.dataframe(
        show,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Total":     st.column_config.NumberColumn(format="$%.0f"),
            "Broadcast": st.column_config.NumberColumn(format="$%.0f"),
            "CTV":       st.column_config.NumberColumn(format="$%.0f"),
            "Digital":   st.column_config.NumberColumn(format="$%.0f"),
            "Radio":     st.column_config.NumberColumn(format="$%.0f"),
        },
    )

    # ─── Agency × media responsibilities (filtered to current view) ─────
    st.markdown("### Agency responsibilities by candidate")
    st.caption(
        "One row per (candidate, agency). Dollar cells show what each "
        "agency is buying; blanks mean that agency doesn't handle that "
        "medium for the candidate. Useful for outreach prep — you can "
        "see at a glance who's running broadcast vs. CTV vs. digital "
        "for any given campaign."
    )

    # Limit to candidates currently shown in the filtered view above
    name_states = set(zip(df_f["full_name"].astype(str), df_f["state"].astype(str)))
    if name_states:
        conn2 = sqlite3.connect(DB_PATH)
        agency_q = """
        SELECT
            c.full_name,
            r.state,
            r.district,
            COALESCE(NULLIF(s.agency, ''), 'Unassigned') AS agency,
            SUM(CASE WHEN s.media_type='broadcast_tv' THEN s.gross_amount ELSE 0 END) AS broadcast,
            SUM(CASE WHEN s.media_type='cable'     THEN s.gross_amount ELSE 0 END) AS cable,
            SUM(CASE WHEN s.media_type='ctv'       THEN s.gross_amount ELSE 0 END) AS ctv,
            SUM(CASE WHEN s.media_type='digital'   THEN s.gross_amount ELSE 0 END) AS digital,
            SUM(CASE WHEN s.media_type='radio'     THEN s.gross_amount ELSE 0 END) AS radio,
            SUM(s.gross_amount) AS total
        FROM spend_records s
        JOIN candidates c ON c.candidate_id = s.candidate_id
        JOIN races r      ON r.race_id      = c.race_id
        WHERE r.cycle = 2026 AND s.race_id != 'unresolved'
        GROUP BY c.full_name, r.state, r.district, agency
        HAVING SUM(s.gross_amount) > 0
        ORDER BY c.full_name, total DESC
        """
        agency_df = pd.read_sql(agency_q, conn2)
        conn2.close()

        # Restrict to candidates shown in the filtered view above
        agency_df = agency_df[agency_df.apply(
            lambda r: (str(r["full_name"]), str(r["state"])) in name_states, axis=1
        )]

        if agency_df.empty:
            st.info("No agency-level rows match the current candidate filter.")
        else:
            agency_df["Race"] = agency_df["state"] + "-" + agency_df["district"].fillna("")
            # Replace zeros with NaN so the column shows blank instead of $0
            for col in ["broadcast", "cable", "ctv", "digital", "radio"]:
                agency_df[col] = agency_df[col].replace(0, pd.NA)
            agency_view = agency_df.rename(columns={
                "full_name": "Candidate",
                "agency": "Agency",
                "broadcast": "Broadcast",
                "cable": "Cable",
                "ctv": "CTV",
                "digital": "Digital",
                "radio": "Radio",
                "total": "Total",
            })[["Candidate", "Race", "Agency", "Broadcast", "Cable", "CTV", "Digital", "Radio", "Total"]]
            st.dataframe(
                agency_view,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Total":     st.column_config.NumberColumn(format="$%.0f"),
                    "Broadcast": st.column_config.NumberColumn(format="$%.0f"),
                    "Cable":     st.column_config.NumberColumn(format="$%.0f"),
                    "CTV":       st.column_config.NumberColumn(format="$%.0f"),
                    "Digital":   st.column_config.NumberColumn(format="$%.0f"),
                    "Radio":     st.column_config.NumberColumn(format="$%.0f"),
                },
            )
            csv_download(agency_view, "Download agency × media", "zero_cable_agencies_by_media.csv")


if __name__ == "__main__":
    main()
