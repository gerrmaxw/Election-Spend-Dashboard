from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from config.settings import DB_PATH, TARGET_STATES, TV_SPEND_FLOOR


OUTPUT_TABLES = [
    "summary_kpis",
    "primaries_per_candidate",
    "primaries_per_advertiser",
    "broadcast_only_advertisers",
    "cable_ctv_only_advertisers",
    "upcoming_primaries",
    "state_coverage",
    "analysis_summary",
    "analysis_candidate_view",
    "analysis_advertiser_view",
    "analysis_broadcast_only_view",
    "analysis_cable_ctv_only_view",
    "analysis_state_coverage",
    "analysis_spend_aggregates",
]


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def _tv_mix_flags(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["broadcast_only"] = (
        (frame["broadcast_spend"] > 0)
        & (frame["cable_spend"] == 0)
        & (frame["ctv_spend"] == 0)
    )
    frame["cable_ctv_only"] = (
        (frame["broadcast_spend"] == 0)
        & ((frame["cable_spend"] + frame["ctv_spend"]) > 0)
    )
    frame["mixed_tv"] = (
        (frame["broadcast_spend"] > 0)
        & ((frame["cable_spend"] > 0) | (frame["ctv_spend"] > 0))
    )
    frame["no_cable_tv"] = (
        (frame["cable_spend"] == 0)
        & ((frame["broadcast_spend"] > 0) | (frame["ctv_spend"] > 0))
    )
    frame["tv_mix"] = "none"
    frame.loc[frame["mixed_tv"], "tv_mix"] = "mixed_tv"
    frame.loc[frame["cable_ctv_only"], "tv_mix"] = "cable_ctv_only"
    frame.loc[frame["broadcast_only"], "tv_mix"] = "broadcast_only"
    frame["no_cable_tv_type"] = "none"
    frame.loc[
        (frame["cable_spend"] == 0) & (frame["broadcast_spend"] > 0) & (frame["ctv_spend"] > 0),
        "no_cable_tv_type",
    ] = "broadcast_ctv_no_cable"
    frame.loc[
        (frame["cable_spend"] == 0) & (frame["broadcast_spend"] == 0) & (frame["ctv_spend"] > 0),
        "no_cable_tv_type",
    ] = "ctv_only"
    frame.loc[
        (frame["cable_spend"] == 0) & (frame["broadcast_spend"] > 0) & (frame["ctv_spend"] == 0),
        "no_cable_tv_type",
    ] = "broadcast_only"
    return frame


def _office_label(value: str | None) -> str:
    if not value:
        return ""
    return str(value).replace("_", " ").title()


def _build_state_coverage(
    races: pd.DataFrame,
    candidates: pd.DataFrame,
    advertiser_rollup: pd.DataFrame,
    conn: sqlite3.Connection,
) -> pd.DataFrame:
    state_df = pd.DataFrame({"state": TARGET_STATES})
    race_counts = races.groupby("state", dropna=False).agg(races=("race_id", "nunique")).reset_index()
    candidate_counts = (
        candidates.groupby("state", dropna=False).agg(candidates=("candidate_id", "nunique")).reset_index()
    )
    spend_counts = (
        advertiser_rollup.groupby("state", dropna=False).agg(spend_records=("advertiser_name", "nunique")).reset_index()
    )

    try:
        ie_counts = pd.read_sql_query(
            """
            SELECT state, COUNT(*) AS ies_linked
            FROM fec_ie_by_committee
            WHERE state IS NOT NULL AND TRIM(state) <> ''
            GROUP BY state
            """,
            conn,
        )
    except Exception:
        ie_counts = pd.DataFrame(columns=["state", "ies_linked"])

    coverage = state_df.merge(race_counts, on="state", how="left")
    coverage = coverage.merge(candidate_counts, on="state", how="left")
    coverage = coverage.merge(spend_counts, on="state", how="left")
    coverage = coverage.merge(ie_counts, on="state", how="left").fillna(0)
    coverage["status"] = coverage.apply(
        lambda row: "Full"
        if row["races"] > 0 and row["candidates"] > 0 and row["ies_linked"] > 0
        else "Partial"
        if row["races"] > 0 or row["spend_records"] > 0
        else "Needs Re-fetch",
        axis=1,
    )
    coverage["needs_refresh"] = coverage["status"].ne("Full").astype(int)
    return coverage


def build_analysis_outputs(db_path: Path = DB_PATH) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        today = pd.Timestamp(date.today())
        spend = pd.read_sql_query("SELECT * FROM spend_records", conn)
        races = pd.read_sql_query(
            """
            SELECT race_id, cycle, office, state, district, general_date, election_name, election_scope, election_type
            FROM races
            """,
            conn,
        )
        candidates = pd.read_sql_query(
            """
            SELECT c.candidate_id, c.full_name, c.party, c.result, c.votes_received, c.vote_share, c.margin,
                   r.race_id, r.state, r.office, r.district, r.general_date, r.election_name, r.election_type
            FROM candidates c
            JOIN races r ON r.race_id = c.race_id
            """,
            conn,
        )

        for table in OUTPUT_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")

        if spend.empty:
            empty_summary = pd.DataFrame(
                columns=[
                    "total_advertisers",
                    "matched_advertisers",
                    "broadcast_only_advertiser_count",
                    "cable_ctv_only_advertiser_count",
                    "total_tv_spend",
                    "broadcast_only_tv_spend",
                    "cable_ctv_only_tv_spend",
                    "winner_median_tv_per_vote",
                    "loser_median_tv_per_vote",
                    "largest_broadcast_only_advertiser",
                    "largest_broadcast_only_spend",
                    "largest_cable_ctv_advertiser",
                    "largest_cable_ctv_spend",
                ]
            )
            empty_summary.to_sql("summary_kpis", conn, index=False, if_exists="replace")
            empty_summary.to_sql("analysis_summary", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("primaries_per_candidate", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("primaries_per_advertiser", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("broadcast_only_advertisers", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("cable_ctv_only_advertisers", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("upcoming_primaries", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("analysis_candidate_view", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("analysis_advertiser_view", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("analysis_broadcast_only_view", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("analysis_cable_ctv_only_view", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("state_coverage", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("analysis_state_coverage", conn, index=False, if_exists="replace")
            pd.DataFrame().to_sql("analysis_spend_aggregates", conn, index=False, if_exists="replace")
            conn.commit()
            return {"candidate_rows": 0, "advertiser_rows": 0, "upcoming_rows": 0}

        spend["gross_amount"] = spend["gross_amount"].astype(float)
        metadata_defaults = {
            "agency": "",
            "source_data_type": "legacy_advertiser",
            "source_race_type": "",
            "source_state": "",
            "source_district": "",
            "source_party_affiliation": "",
            "advertiser_type": "",
            "is_national": 0,
        }
        for column, default in metadata_defaults.items():
            if column not in spend.columns:
                spend[column] = default
            spend[column] = spend[column].fillna(default)
        if "general_date" in races.columns:
            races["general_date"] = pd.to_datetime(races["general_date"], errors="coerce")
        if "general_date" in candidates.columns:
            candidates["general_date"] = pd.to_datetime(candidates["general_date"], errors="coerce")

        pivot_index = [
            "advertiser_name",
            "candidate_id",
            "race_id",
            "match_source",
            "match_reason",
            "match_confidence",
            "likely_state",
            "likely_office",
            "source_batch_id",
            "source_file",
            "agency",
            "source_data_type",
            "source_race_type",
            "source_state",
            "source_district",
            "source_party_affiliation",
            "advertiser_type",
            "is_national",
        ]
        for column in pivot_index:
            if column not in spend.columns:
                spend[column] = ""
        spend[pivot_index] = spend[pivot_index].fillna("")

        pivot = (
            spend.groupby([*pivot_index, "media_type"], dropna=False)["gross_amount"]
            .sum()
            .unstack("media_type", fill_value=0)
            .reset_index()
        )
        pivot.columns.name = None
        for column in ["broadcast_tv", "cable", "ctv", "digital", "radio", "mail", "other"]:
            if column not in pivot.columns:
                pivot[column] = 0.0

        advertiser = pivot.merge(candidates, on=["candidate_id", "race_id"], how="left")
        advertiser["candidate_name"] = advertiser["full_name"].fillna("")
        advertiser["matched_candidate"] = advertiser["candidate_name"].replace("", pd.NA)
        advertiser["state"] = advertiser["state"].fillna(advertiser["likely_state"]).fillna("")
        advertiser["state"] = advertiser["state"].replace("", pd.NA).fillna(advertiser["source_state"]).fillna("")
        advertiser["office"] = advertiser["office"].fillna(advertiser["likely_office"]).fillna("")
        advertiser["district"] = advertiser["district"].fillna(advertiser["source_district"]).fillna("")
        advertiser["party"] = (
            advertiser["party"].fillna("").replace("", pd.NA)
            .fillna(advertiser["source_party_affiliation"]).fillna("")
        )
        advertiser["primary_date"] = pd.to_datetime(advertiser["general_date"], errors="coerce")
        advertiser["result"] = advertiser["result"].fillna("unmatched")
        advertiser["votes"] = advertiser["votes_received"].fillna(0).astype(float)
        advertiser["vote_share"] = advertiser["vote_share"].fillna(0.0).astype(float)
        advertiser["margin"] = advertiser["margin"].fillna(0.0).astype(float)
        advertiser["broadcast_spend"] = advertiser["broadcast_tv"].astype(float)
        advertiser["cable_spend"] = advertiser["cable"].astype(float)
        advertiser["ctv_spend"] = advertiser["ctv"].astype(float)
        advertiser["digital_spend"] = advertiser["digital"].astype(float)
        advertiser["radio_spend"] = advertiser["radio"].astype(float)
        advertiser["tv_spend"] = (
            advertiser["broadcast_spend"] + advertiser["cable_spend"] + advertiser["ctv_spend"]
        )
        advertiser["cable_ctv_spend"] = advertiser["cable_spend"] + advertiser["ctv_spend"]
        advertiser["total_attributed_spend"] = (
            advertiser["tv_spend"] + advertiser["digital_spend"] + advertiser["radio_spend"]
        )
        advertiser["total_spend"] = advertiser["total_attributed_spend"]
        advertiser["tv_per_vote"] = advertiser.apply(lambda row: _safe_ratio(row["tv_spend"], row["votes"]), axis=1)
        advertiser["total_per_vote"] = advertiser.apply(
            lambda row: _safe_ratio(row["total_attributed_spend"], row["votes"]), axis=1
        )
        advertiser["race_name"] = advertiser["election_name"].fillna(advertiser["office"])
        advertiser["completed_primary"] = advertiser["primary_date"].notna() & advertiser["primary_date"].lt(today)
        advertiser["upcoming_primary"] = advertiser["primary_date"].notna() & advertiser["primary_date"].ge(today)
        advertiser = _tv_mix_flags(advertiser)

        advertiser_rollup = advertiser[
            [
                "advertiser_name",
                "candidate_id",
                "candidate_name",
                "matched_candidate",
                "party",
                "source_party_affiliation",
                "advertiser_type",
                "source_data_type",
                "source_race_type",
                "source_state",
                "source_district",
                "agency",
                "is_national",
                "state",
                "office",
                "district",
                "primary_date",
                "result",
                "votes",
                "vote_share",
                "margin",
                "broadcast_spend",
                "cable_spend",
                "ctv_spend",
                "cable_ctv_spend",
                "tv_spend",
                "digital_spend",
                "radio_spend",
                "total_spend",
                "total_attributed_spend",
                "tv_per_vote",
                "total_per_vote",
                "match_source",
                "match_reason",
                "match_confidence",
                "race_id",
                "race_name",
                "likely_state",
                "likely_office",
                "broadcast_only",
                "cable_ctv_only",
                "mixed_tv",
                "no_cable_tv",
                "no_cable_tv_type",
                "tv_mix",
                "completed_primary",
                "upcoming_primary",
                "source_file",
                "source_batch_id",
            ]
        ].copy()

        matched_advertiser_rows = advertiser_rollup[
            advertiser_rollup["candidate_name"].ne("") & advertiser_rollup["votes"].gt(0)
        ].copy()

        candidate_spend_rollup = (
            matched_advertiser_rows.groupby(
                [
                    "candidate_id",
                    "candidate_name",
                    "party",
                    "state",
                    "office",
                    "district",
                    "primary_date",
                    "result",
                    "votes",
                    "vote_share",
                    "margin",
                    "race_id",
                    "race_name",
                ],
                dropna=False,
            )
            .agg(
                broadcast_spend=("broadcast_spend", "sum"),
                cable_spend=("cable_spend", "sum"),
                ctv_spend=("ctv_spend", "sum"),
                tv_spend=("tv_spend", "sum"),
                digital_spend=("digital_spend", "sum"),
                radio_spend=("radio_spend", "sum"),
                total_attributed_spend=("total_attributed_spend", "sum"),
                advertiser_count=("advertiser_name", "nunique"),
                advertiser_list=("advertiser_name", lambda values: ", ".join(sorted(set(values)))),
                agencies=("agency", lambda values: ", ".join(sorted({str(v) for v in values if str(v).strip()}))),
                source_data_types=("source_data_type", lambda values: ", ".join(sorted({str(v) for v in values if str(v).strip()}))),
                source_race_types=("source_race_type", lambda values: ", ".join(sorted({str(v) for v in values if str(v).strip()}))),
                source_party_affiliations=(
                    "source_party_affiliation",
                    lambda values: ", ".join(sorted({str(v) for v in values if str(v).strip()})),
                ),
                advertiser_types=("advertiser_type", lambda values: ", ".join(sorted({str(v) for v in values if str(v).strip()}))),
            )
            .reset_index()
        )
        candidate_base = candidates[
            [
                "candidate_id",
                "full_name",
                "party",
                "result",
                "votes_received",
                "vote_share",
                "margin",
                "race_id",
                "state",
                "office",
                "district",
                "general_date",
                "election_name",
                "election_type",
            ]
        ].copy()
        candidate_base.rename(
            columns={
                "full_name": "candidate_name",
                "votes_received": "votes",
                "general_date": "primary_date",
                "election_name": "race_name",
            },
            inplace=True,
        )
        candidate_base["district"] = candidate_base["district"].fillna("")
        candidate_base["primary_date"] = pd.to_datetime(candidate_base["primary_date"], errors="coerce")
        candidate_base["votes"] = candidate_base["votes"].fillna(0).astype(float)
        candidate_base["vote_share"] = candidate_base["vote_share"].fillna(0.0).astype(float)
        candidate_base["margin"] = candidate_base["margin"].fillna(0.0).astype(float)
        candidate_base["result"] = candidate_base["result"].fillna("unknown")

        candidate_view = candidate_base.merge(
            candidate_spend_rollup,
            on=[
                "candidate_id",
                "candidate_name",
                "party",
                "state",
                "office",
                "district",
                "primary_date",
                "result",
                "votes",
                "vote_share",
                "margin",
                "race_id",
                "race_name",
            ],
            how="left",
        )
        for column in [
            "broadcast_spend",
            "cable_spend",
            "ctv_spend",
            "tv_spend",
            "digital_spend",
            "radio_spend",
            "total_attributed_spend",
        ]:
            candidate_view[column] = candidate_view[column].fillna(0.0)
        candidate_view["advertiser_count"] = candidate_view["advertiser_count"].fillna(0).astype(int)
        candidate_view["advertiser_list"] = candidate_view["advertiser_list"].fillna("")
        for column in [
            "agencies",
            "source_data_types",
            "source_race_types",
            "source_party_affiliations",
            "advertiser_types",
        ]:
            if column not in candidate_view.columns:
                candidate_view[column] = ""
            candidate_view[column] = candidate_view[column].fillna("")
        candidate_view["primary_date"] = pd.to_datetime(candidate_view["primary_date"], errors="coerce")
        candidate_view["tv_per_vote"] = candidate_view.apply(
            lambda row: _safe_ratio(row["tv_spend"], row["votes"]), axis=1
        )
        candidate_view["total_per_vote"] = candidate_view.apply(
            lambda row: _safe_ratio(row["total_attributed_spend"], row["votes"]), axis=1
        )
        candidate_view["completed_primary"] = candidate_view["primary_date"].lt(today)
        candidate_view["upcoming_primary"] = candidate_view["primary_date"].ge(today)
        candidate_view = _tv_mix_flags(candidate_view)

        primaries_per_advertiser = matched_advertiser_rows.rename(
            columns={
                "advertiser_name": "advertiser_name",
                "candidate_name": "candidate_name",
            }
        ).copy()
        primaries_per_advertiser["primary_date"] = primaries_per_advertiser["primary_date"].dt.strftime("%Y-%m-%d")

        candidate_view["primary_date"] = candidate_view["primary_date"].dt.strftime("%Y-%m-%d")

        broadcast_only_advertisers = advertiser_rollup[
            advertiser_rollup["broadcast_only"] & advertiser_rollup["tv_spend"].ge(TV_SPEND_FLOOR)
        ].copy()
        cable_ctv_only_advertisers = advertiser_rollup[
            advertiser_rollup["cable_ctv_only"] & advertiser_rollup["tv_spend"].ge(TV_SPEND_FLOOR)
        ].copy()
        for frame in [broadcast_only_advertisers, cable_ctv_only_advertisers]:
            frame["primary_date"] = frame["primary_date"].dt.strftime("%Y-%m-%d")

        def _largest(frame: pd.DataFrame, spend_col: str) -> tuple[str, float]:
            if frame.empty:
                return ("", 0.0)
            row = frame.sort_values(spend_col, ascending=False).iloc[0]
            return str(row["advertiser_name"]), float(row[spend_col])

        winners = candidate_view[candidate_view["result"] == "won"]
        losers = candidate_view[candidate_view["result"] == "lost"]
        spent_candidates = candidate_view[candidate_view["tv_spend"] > 0]
        spent_winners = spent_candidates[spent_candidates["result"] == "won"]
        spent_losers = spent_candidates[spent_candidates["result"] == "lost"]
        largest_broadcast_name, largest_broadcast_spend = _largest(broadcast_only_advertisers, "broadcast_spend")
        largest_cable_name, largest_cable_spend = _largest(cable_ctv_only_advertisers, "cable_ctv_spend")

        summary_kpis = pd.DataFrame(
            [
                {
                    "total_advertisers": int(spend["advertiser_name"].dropna().nunique()),
                    "matched_advertisers": int(matched_advertiser_rows["advertiser_name"].dropna().nunique()),
                    "broadcast_only_advertiser_count": int(
                        broadcast_only_advertisers["advertiser_name"].dropna().nunique()
                    ),
                    "cable_ctv_only_advertiser_count": int(
                        cable_ctv_only_advertisers["advertiser_name"].dropna().nunique()
                    ),
                    "total_tv_spend": float(advertiser_rollup["tv_spend"].sum()),
                    "broadcast_only_tv_spend": float(broadcast_only_advertisers["tv_spend"].sum()),
                    "cable_ctv_only_tv_spend": float(cable_ctv_only_advertisers["cable_ctv_spend"].sum()),
                    "winner_median_tv_per_vote": spent_winners["tv_per_vote"].median(),
                    "loser_median_tv_per_vote": spent_losers["tv_per_vote"].median(),
                    "largest_broadcast_only_advertiser": largest_broadcast_name,
                    "largest_broadcast_only_spend": largest_broadcast_spend,
                    "largest_cable_ctv_advertiser": largest_cable_name,
                    "largest_cable_ctv_spend": largest_cable_spend,
                }
            ]
        )

        upcoming_races = races[
            races["state"].isin(TARGET_STATES)
            & races["general_date"].notna()
            & races["general_date"].ge(today)
        ].copy()
        upcoming_races["primary_date"] = upcoming_races["general_date"]
        upcoming_races["days_until_primary"] = (upcoming_races["primary_date"] - today).dt.days

        if not upcoming_races.empty:
            candidate_counts = (
                candidates.groupby("race_id", dropna=False)
                .agg(candidate_count=("candidate_id", "nunique"))
                .reset_index()
            )
            upcoming_candidate_rollup = (
                candidate_view[candidate_view["upcoming_primary"]]
                .sort_values(["race_id", "tv_spend"], ascending=[True, False])
                .groupby("race_id", as_index=False)
                .first()
            )
            upcoming_primaries = upcoming_races.merge(candidate_counts, on="race_id", how="left")
            upcoming_primaries = upcoming_primaries.merge(
                upcoming_candidate_rollup[
                    [
                        "race_id",
                        "candidate_name",
                        "tv_spend",
                        "tv_mix",
                    ]
                ].rename(
                    columns={
                        "candidate_name": "top_tv_spend_candidate",
                        "tv_spend": "top_tv_spend_amount",
                        "tv_mix": "top_tv_mix",
                    }
                ),
                on="race_id",
                how="left",
            )
            upcoming_primaries["has_spend_data"] = upcoming_primaries["top_tv_spend_amount"].fillna(0).gt(0)
            upcoming_primaries["top_tv_mix"] = upcoming_primaries["top_tv_mix"].fillna("none")
            upcoming_primaries["top_tv_spend_candidate"] = upcoming_primaries["top_tv_spend_candidate"].fillna("")
            upcoming_primaries["top_tv_spend_amount"] = upcoming_primaries["top_tv_spend_amount"].fillna(0.0)
            upcoming_primaries["office_label"] = upcoming_primaries["office"].map(_office_label)
            upcoming_primaries["primary_date"] = upcoming_primaries["primary_date"].dt.strftime("%Y-%m-%d")
            upcoming_primaries = upcoming_primaries[
                [
                    "race_id",
                    "state",
                    "office",
                    "district",
                    "primary_date",
                    "days_until_primary",
                    "candidate_count",
                    "has_spend_data",
                    "top_tv_spend_candidate",
                    "top_tv_spend_amount",
                    "top_tv_mix",
                    "election_name",
                    "election_type",
                    "office_label",
                ]
            ].rename(columns={"election_name": "race_name"})
        else:
            upcoming_primaries = pd.DataFrame(
                columns=[
                    "race_id",
                    "state",
                    "office",
                    "district",
                    "primary_date",
                    "days_until_primary",
                    "candidate_count",
                    "has_spend_data",
                    "top_tv_spend_candidate",
                    "top_tv_spend_amount",
                    "top_tv_mix",
                    "race_name",
                    "election_type",
                    "office_label",
                ]
            )

        state_coverage = _build_state_coverage(races, candidates, advertiser_rollup, conn)
        try:
            spend_aggregates = pd.read_sql_query(
                """
                SELECT aggregate_type, label, share_of_total, gross_spending,
                       source_file, source_batch_id, updated_at
                FROM spend_aggregates
                """,
                conn,
            )
        except Exception:
            spend_aggregates = pd.DataFrame(
                columns=[
                    "aggregate_type",
                    "label",
                    "share_of_total",
                    "gross_spending",
                    "source_file",
                    "source_batch_id",
                    "updated_at",
                ]
            )

        summary_kpis.to_sql("summary_kpis", conn, index=False, if_exists="replace")
        candidate_view.to_sql("primaries_per_candidate", conn, index=False, if_exists="replace")
        primaries_per_advertiser.to_sql("primaries_per_advertiser", conn, index=False, if_exists="replace")
        broadcast_only_advertisers.to_sql("broadcast_only_advertisers", conn, index=False, if_exists="replace")
        cable_ctv_only_advertisers.to_sql("cable_ctv_only_advertisers", conn, index=False, if_exists="replace")
        upcoming_primaries.to_sql("upcoming_primaries", conn, index=False, if_exists="replace")
        state_coverage.to_sql("state_coverage", conn, index=False, if_exists="replace")
        spend_aggregates.to_sql("analysis_spend_aggregates", conn, index=False, if_exists="replace")

        summary_kpis.rename(
            columns={
                "total_advertisers": "total_advertisers_ingested",
                "matched_advertisers": "matched_advertisers_with_votes",
                "broadcast_only_advertiser_count": "broadcast_only_tv_advertisers",
                "cable_ctv_only_advertiser_count": "cable_ctv_only_tv_advertisers",
                "broadcast_only_tv_spend": "total_broadcast_only_tv_spend",
                "cable_ctv_only_tv_spend": "total_cable_ctv_only_tv_spend",
                "winner_median_tv_per_vote": "winner_median_tv_cost_per_vote",
                "loser_median_tv_per_vote": "loser_median_tv_cost_per_vote",
                "largest_broadcast_only_advertiser": "largest_broadcast_only_buy_name",
                "largest_broadcast_only_spend": "largest_broadcast_only_buy_amount",
                "largest_cable_ctv_advertiser": "largest_cable_ctv_only_buy_name",
                "largest_cable_ctv_spend": "largest_cable_ctv_only_buy_amount",
            }
        ).to_sql("analysis_summary", conn, index=False, if_exists="replace")

        legacy_candidate = candidate_view.rename(
            columns={
                "candidate_name": "candidate",
                "total_attributed_spend": "total_spend",
                "tv_per_vote": "tv_cost_per_vote",
                "total_per_vote": "total_cost_per_vote",
                "advertiser_list": "advertisers",
            }
        )
        legacy_advertiser = advertiser_rollup.rename(
            columns={
                "advertiser_name": "advertiser",
                "candidate_name": "candidate",
                "tv_per_vote": "tv_cost_per_vote",
                "total_per_vote": "total_cost_per_vote",
                "race_name": "race",
            }
        )
        legacy_broadcast = broadcast_only_advertisers.rename(
            columns={
                "advertiser_name": "advertiser",
                "candidate_name": "candidate",
                "tv_per_vote": "tv_cost_per_vote",
                "total_per_vote": "total_cost_per_vote",
                "race_name": "race",
            }
        )
        legacy_cable = cable_ctv_only_advertisers.rename(
            columns={
                "advertiser_name": "advertiser",
                "candidate_name": "candidate",
                "tv_per_vote": "tv_cost_per_vote",
                "total_per_vote": "total_cost_per_vote",
                "race_name": "race",
            }
        )
        legacy_state = state_coverage.rename(columns={"races": "race_count", "candidates": "candidate_count"})

        legacy_candidate.to_sql("analysis_candidate_view", conn, index=False, if_exists="replace")
        legacy_advertiser.to_sql("analysis_advertiser_view", conn, index=False, if_exists="replace")
        legacy_broadcast.to_sql("analysis_broadcast_only_view", conn, index=False, if_exists="replace")
        legacy_cable.to_sql("analysis_cable_ctv_only_view", conn, index=False, if_exists="replace")
        legacy_state.to_sql("analysis_state_coverage", conn, index=False, if_exists="replace")
        conn.commit()
        return {
            "candidate_rows": len(candidate_view),
            "advertiser_rows": len(advertiser_rollup),
            "upcoming_rows": len(upcoming_primaries),
        }
    finally:
        conn.close()
