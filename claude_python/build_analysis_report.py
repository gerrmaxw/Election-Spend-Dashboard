"""Build the three ranked lists requested and write them to a polished xlsx.

Tab 1 — Completed 2026 Primaries + Cost-per-Vote
  Candidates whose primary has actually happened (votes_received > 0),
  with their TV spend (broadcast + cable), total spend, votes received,
  primary result, and cost-per-vote on TV media.

Tab 2 — Broadcast-Only TV Advertisers
  Advertisers whose TV spend is 100% on broadcast — no cable, no CTV.
  These are the targets for the 'you're wasting impressions' conversation.

Tab 3 — Cable/CTV-Only TV Advertisers
  Advertisers whose TV spend is 100% on cable + CTV — no broadcast.
  These are the proof cases for 'the modern mix works.'
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH
from data.store import Store
from ingest_advertiser_file import (
    flatten_advertiser_file, parse_advertiser, resolve_advertiser,
    _candidate_index, _load_fec_lookup, resolve_via_fec,
)


# ─── Aggregation ─────────────────────────────────────────────────────────
def build_spend_by_advertiser(xlsx_path: Path, store: Store) -> pd.DataFrame:
    """One row per advertiser with parsed/resolved metadata attached."""
    flat = flatten_advertiser_file(xlsx_path)
    cand_ix = _candidate_index(store)
    fec_lookup = _load_fec_lookup(DB_PATH)

    rows = []
    for _, row in flat.iterrows():
        parsed = parse_advertiser(row["advertiser"])
        resolved = resolve_advertiser(parsed, cand_ix)
        # FEC fallback for unresolved advertisers
        if (resolved.candidate_id is None
                or resolved.race_id in (None, "unresolved")):
            fec_result = resolve_via_fec(row["advertiser"], fec_lookup, cand_ix)
            if fec_result is not None:
                resolved = fec_result

        tv_spend = row["broadcast"] + row["cable"] + row["ctv"]
        match_source = "direct" if ("FEC IE" not in (resolved.match_reason or "")
                                      and resolved.candidate_id) else \
                        ("fec_ie" if resolved.candidate_id else "unmatched")
        rows.append({
            "advertiser": row["advertiser"],
            "broadcast": row["broadcast"],
            "cable": row["cable"],
            "ctv": row["ctv"],
            "digital": row["digital"],
            "radio": row["radio"],
            "tv_spend": tv_spend,
            "total_spend": row["total"],
            "is_candidate_committee": parsed.is_candidate_committee,
            "parsed_state": parsed.state,
            "parsed_office": parsed.office.value if parsed.office else None,
            "parsed_surname": parsed.surname,
            "candidate_id": resolved.candidate_id,
            "race_id": resolved.race_id,
            "match_source": match_source,
            "match_reason": resolved.match_reason,
            "match_confidence": resolved.confidence,
        })
    return pd.DataFrame(rows)


def attach_race_results(df: pd.DataFrame) -> pd.DataFrame:
    """Left-join candidate/race result data for matched rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cand = pd.DataFrame([dict(r) for r in conn.execute("""
        SELECT c.candidate_id, c.full_name, c.party, c.result,
               c.votes_received, c.vote_share, c.margin,
               r.state, r.office, r.district, r.general_date
        FROM candidates c JOIN races r ON r.race_id = c.race_id
    """)])
    conn.close()
    merged = df.merge(cand, on="candidate_id", how="left", suffixes=("", "_cand"))
    return merged


# ─── Tab 1: Completed primaries + cost-per-vote ──────────────────────────
def tab_completed_primaries(df: pd.DataFrame) -> pd.DataFrame:
    out = df[
        df["candidate_id"].notna()
        & df["votes_received"].notna()
        & (df["votes_received"] > 0)
    ].copy()
    if out.empty:
        return out
    out["tv_cost_per_vote"] = out["tv_spend"] / out["votes_received"]
    out["total_cost_per_vote"] = out["total_spend"] / out["votes_received"]
    cols = [
        "full_name", "party", "state", "office", "district", "general_date",
        "result", "votes_received", "vote_share", "margin",
        "broadcast", "cable", "ctv", "tv_spend", "digital", "radio",
        "total_spend", "tv_cost_per_vote", "total_cost_per_vote",
        "advertiser", "match_source",
    ]
    out = out[cols].sort_values("total_spend", ascending=False)
    out = out.rename(columns={
        "full_name": "Candidate",
        "party": "Party",
        "state": "State",
        "office": "Office",
        "district": "District",
        "general_date": "Primary Date",
        "result": "Result",
        "votes_received": "Votes",
        "vote_share": "Vote Share",
        "margin": "Margin",
        "broadcast": "Broadcast $",
        "cable": "Cable $",
        "ctv": "CTV $",
        "tv_spend": "TV $ (BC+Cable+CTV)",
        "digital": "Digital $",
        "radio": "Radio $",
        "total_spend": "Total $",
        "tv_cost_per_vote": "TV $/Vote",
        "total_cost_per_vote": "Total $/Vote",
        "advertiser": "Advertiser (source)",
        "match_source": "Match Source",
    })
    return out


def tab_completed_primaries_aggregated(df: pd.DataFrame) -> pd.DataFrame:
    """One row per candidate, summing all attributed spend (direct committee
    + supporting PACs via FEC IE). This is the view to use for cost-per-vote
    comparisons — the per-advertiser rows make a candidate look cheap when
    actually PAC money doubled or tripled their real spend."""
    base = df[
        df["candidate_id"].notna()
        & df["votes_received"].notna()
        & (df["votes_received"] > 0)
    ].copy()
    if base.empty:
        return base

    agg = base.groupby("candidate_id", as_index=False).agg(
        Candidate=("full_name", "first"),
        Party=("party", "first"),
        State=("state", "first"),
        Office=("office", "first"),
        District=("district", "first"),
        PrimaryDate=("general_date", "first"),
        Result=("result", "first"),
        Votes=("votes_received", "first"),
        VoteShare=("vote_share", "first"),
        Margin=("margin", "first"),
        Broadcast=("broadcast", "sum"),
        Cable=("cable", "sum"),
        CTV=("ctv", "sum"),
        Digital=("digital", "sum"),
        Radio=("radio", "sum"),
        AttributedTotal=("total_spend", "sum"),
        AdvertiserCount=("advertiser", "count"),
        Advertisers=("advertiser", lambda s: " | ".join(sorted(set(s)))),
    )
    agg["TV_Spend"] = agg["Broadcast"] + agg["Cable"] + agg["CTV"]
    agg["TV_CostPerVote"] = agg["TV_Spend"] / agg["Votes"]
    agg["Total_CostPerVote"] = agg["AttributedTotal"] / agg["Votes"]

    agg = agg.sort_values("AttributedTotal", ascending=False)
    agg = agg.rename(columns={
        "PrimaryDate": "Primary Date",
        "VoteShare": "Vote Share",
        "Broadcast": "Broadcast $",
        "Cable": "Cable $",
        "CTV": "CTV $",
        "Digital": "Digital $",
        "Radio": "Radio $",
        "TV_Spend": "TV $ (BC+Cable+CTV)",
        "AttributedTotal": "Total Attributed $",
        "AdvertiserCount": "# Advertisers",
        "TV_CostPerVote": "TV $/Vote",
        "Total_CostPerVote": "Total $/Vote",
    })
    # Reorder columns
    ordered = ["Candidate", "Party", "State", "Office", "District",
                "Primary Date", "Result", "Votes", "Vote Share", "Margin",
                "Broadcast $", "Cable $", "CTV $", "TV $ (BC+Cable+CTV)",
                "Digital $", "Radio $", "Total Attributed $",
                "TV $/Vote", "Total $/Vote",
                "# Advertisers", "Advertisers"]
    return agg[ordered]


# ─── Tabs 2 & 3: TV-media-mix-only cohorts ───────────────────────────────
def _tv_mix_classification(row) -> str:
    """Classify by TV channels only (broadcast / cable / CTV). Digital and
    radio don't factor in — an advertiser running broadcast + digital + radio
    still counts as 'broadcast-only' because their *TV* buy is 100% broadcast.
    The pitch is about TV media mix, not total media diet."""
    bc, ca, ct = row["broadcast"], row["cable"], row["ctv"]
    tv = bc + ca + ct
    if tv <= 0:
        return "no_tv"
    has_bc = bc > 0
    has_cab = ca > 0
    has_ctv = ct > 0
    if has_bc and not has_cab and not has_ctv:
        return "broadcast_only"          # TV buy = 100% broadcast
    if not has_bc and (has_cab or has_ctv):
        return "cable_ctv_only"          # TV buy = 100% cable + CTV
    if has_bc and (has_cab or has_ctv):
        return "mixed"
    return "other"


def tab_broadcast_only(df: pd.DataFrame, min_spend: float = 25_000) -> pd.DataFrame:
    d = df.copy()
    d["tv_mix"] = d.apply(_tv_mix_classification, axis=1)
    out = d[(d["tv_mix"] == "broadcast_only") & (d["tv_spend"] >= min_spend)].copy()
    out = out.sort_values("broadcast", ascending=False)
    cols = ["advertiser", "parsed_state", "parsed_office",
            "full_name", "result", "votes_received",
            "broadcast", "cable", "ctv", "digital", "radio", "total_spend"]
    out = out[cols].rename(columns={
        "advertiser": "Advertiser",
        "parsed_state": "Likely State",
        "parsed_office": "Likely Office",
        "full_name": "Matched Candidate",
        "result": "Result",
        "votes_received": "Votes",
        "broadcast": "Broadcast $",
        "cable": "Cable $",
        "ctv": "CTV $",
        "digital": "Digital $",
        "radio": "Radio $",
        "total_spend": "Total $",
    })
    return out


def tab_cable_ctv_only(df: pd.DataFrame, min_spend: float = 25_000) -> pd.DataFrame:
    d = df.copy()
    d["tv_mix"] = d.apply(_tv_mix_classification, axis=1)
    out = d[(d["tv_mix"] == "cable_ctv_only") & (d["tv_spend"] >= min_spend)].copy()
    out["cable_plus_ctv"] = out["cable"] + out["ctv"]
    out = out.sort_values("cable_plus_ctv", ascending=False)
    cols = ["advertiser", "parsed_state", "parsed_office",
            "full_name", "result", "votes_received",
            "broadcast", "cable", "ctv", "cable_plus_ctv",
            "digital", "radio", "total_spend"]
    out = out[cols].rename(columns={
        "advertiser": "Advertiser",
        "parsed_state": "Likely State",
        "parsed_office": "Likely Office",
        "full_name": "Matched Candidate",
        "result": "Result",
        "votes_received": "Votes",
        "broadcast": "Broadcast $",
        "cable": "Cable $",
        "ctv": "CTV $",
        "cable_plus_ctv": "Cable + CTV $",
        "digital": "Digital $",
        "radio": "Radio $",
        "total_spend": "Total $",
    })
    return out


# ─── Excel writer with formatting ────────────────────────────────────────
HEADER_FILL = PatternFill("solid", start_color="1F3864")     # Comcast-ish navy
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(name="Arial", bold=True, size=14, color="1F3864")
SUBTITLE_FONT = Font(name="Arial", italic=True, size=10, color="595959")
BODY_FONT = Font(name="Arial", size=10)
ZEBRA_FILL = PatternFill("solid", start_color="F2F2F2")

CURRENCY_COLS = {"Broadcast $", "Cable $", "CTV $", "Digital $", "Radio $",
                 "Total $", "TV $ (BC+Cable+CTV)", "Cable + CTV $",
                 "TV $/Vote", "Total $/Vote", "Total Attributed $"}
INT_COLS = {"Votes", "# Advertisers"}
PCT_COLS = {"Vote Share", "Margin"}


def _write_tab(wb: Workbook, sheet_name: str, title: str, subtitle: str,
                df: pd.DataFrame) -> None:
    ws = wb.create_sheet(sheet_name)
    ws["A1"] = title
    ws["A1"].font = TITLE_FONT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, len(df.columns)))
    ws["A2"] = subtitle
    ws["A2"].font = SUBTITLE_FONT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(1, len(df.columns)))

    header_row = 4
    for ci, col in enumerate(df.columns, start=1):
        c = ws.cell(row=header_row, column=ci, value=col)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center")

    for ri, (_, row) in enumerate(df.iterrows(), start=header_row + 1):
        for ci, col in enumerate(df.columns, start=1):
            val = row[col]
            if pd.isna(val):
                val = None
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = BODY_FONT
            if ri % 2 == 0:
                cell.fill = ZEBRA_FILL
            if col in CURRENCY_COLS:
                cell.number_format = '"$"#,##0;("$"#,##0);"-"'
            elif col in INT_COLS:
                cell.number_format = "#,##0"
            elif col in PCT_COLS:
                cell.number_format = "0.0%"

    # Column widths
    for ci, col in enumerate(df.columns, start=1):
        letter = get_column_letter(ci)
        if col in {"Advertiser", "Matched Candidate", "Candidate", "Advertiser (source)"}:
            ws.column_dimensions[letter].width = 42
        elif col in CURRENCY_COLS:
            ws.column_dimensions[letter].width = 16
        elif col == "Result":
            ws.column_dimensions[letter].width = 10
        elif col in {"State", "Party", "District", "Likely State"}:
            ws.column_dimensions[letter].width = 12
        else:
            ws.column_dimensions[letter].width = 18

    ws.freeze_panes = f"A{header_row + 1}"


def _write_summary(wb: Workbook,
                    completed: pd.DataFrame,
                    broadcast_only: pd.DataFrame,
                    cable_ctv_only: pd.DataFrame,
                    total_advertisers: int) -> None:
    ws = wb.create_sheet("Summary", 0)
    ws["A1"] = "Political Ad Spend vs. 2026 Primary Results"
    ws["A1"].font = Font(name="Arial", bold=True, size=16, color="1F3864")
    ws.merge_cells("A1:F1")
    ws["A2"] = ("Scope: Home_Advertiser political spend file joined to CivicAPI "
                "2026 election results where the primary has taken place.")
    ws["A2"].font = SUBTITLE_FONT
    ws.merge_cells("A2:F2")

    rows = [
        ("Total advertisers in file",           total_advertisers),
        ("Advertisers matched to a 2026 candidate with reported votes",
                                                 len(completed)),
        ("Broadcast-only TV advertisers (≥$25K TV)", len(broadcast_only)),
        ("Cable/CTV-only TV advertisers (≥$25K TV)", len(cable_ctv_only)),
        ("", ""),
        ("Total TV spend from file (broadcast+cable+CTV)",
            completed["TV $ (BC+Cable+CTV)"].sum() if not completed.empty else 0),
        ("Total broadcast-only TV spend",
            broadcast_only["Broadcast $"].sum() if not broadcast_only.empty else 0),
        ("Total cable/CTV-only TV spend",
            (cable_ctv_only["Cable $"].sum() + cable_ctv_only["CTV $"].sum())
            if not cable_ctv_only.empty else 0),
    ]
    for i, (label, value) in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=label).font = BODY_FONT
        c = ws.cell(row=i, column=2, value=value if value != "" else None)
        c.font = BODY_FONT
        if isinstance(value, (int, float)) and label.startswith("Total") and "spend" in label.lower():
            c.number_format = '"$"#,##0'
        elif isinstance(value, (int, float)):
            c.number_format = "#,##0"

    # Key findings line
    if not completed.empty:
        wins = completed[completed["Result"] == "won"]
        losses = completed[completed["Result"] == "lost"]
        ws.cell(row=14, column=1, value="KEY FINDINGS").font = Font(
            name="Arial", bold=True, size=12, color="1F3864")
        findings = []
        if not wins.empty:
            findings.append(
                f"Among matched winners, median TV $/vote was "
                f"${wins['TV $/Vote'].median():,.2f}")
        if not losses.empty:
            findings.append(
                f"Among matched losers, median TV $/vote was "
                f"${losses['TV $/Vote'].median():,.2f}")
        if not broadcast_only.empty:
            top = broadcast_only.iloc[0]
            findings.append(
                f"Largest broadcast-only buy: {top['Advertiser']} at "
                f"${top['Broadcast $']:,.0f}")
        if not cable_ctv_only.empty:
            top = cable_ctv_only.iloc[0]
            findings.append(
                f"Largest cable/CTV-only buy: {top['Advertiser']} at "
                f"${top['Cable + CTV $']:,.0f}")
        for i, note in enumerate(findings, start=15):
            ws.cell(row=i, column=1, value=f"• {note}").font = BODY_FONT
            ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)

    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 22


def build_workbook(output_path: Path, xlsx_input: Path) -> dict:
    store = Store(DB_PATH)
    raw = build_spend_by_advertiser(xlsx_input, store)
    enriched = attach_race_results(raw)

    completed = tab_completed_primaries(enriched)
    completed_agg = tab_completed_primaries_aggregated(enriched)
    bc_only   = tab_broadcast_only(enriched)
    cc_only   = tab_cable_ctv_only(enriched)

    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    _write_summary(wb, completed, bc_only, cc_only, total_advertisers=len(raw))
    _write_tab(wb, "1a. Primaries (Per Candidate)",
               "Completed 2026 Primaries — Aggregated Per Candidate",
               "All spend attributed to each candidate (direct committee + "
               "supporting PACs via FEC Schedule E IEs) rolled up to one row "
               "per candidate. This is the right view for cost-per-vote "
               "comparisons — PAC money often dwarfs direct committee spend.",
               completed_agg)
    _write_tab(wb, "1b. Primaries (Per Advertiser)",
               "Completed 2026 Primaries — Per Advertiser Detail",
               "Same underlying data as tab 1a but broken out by advertiser "
               "(so candidates with multiple supporting PACs appear on multiple "
               "rows). Use this for transparency into which committee spent what.",
               completed)
    _write_tab(wb, "2. Broadcast-Only",
               "Broadcast-Only TV Advertisers",
               "Advertisers whose TV spend is entirely on broadcast — no cable, no CTV. "
               "Digital and radio spend do not disqualify. Ranked by broadcast $. "
               "Minimum $25K TV spend floor.",
               bc_only)
    _write_tab(wb, "3. Cable-CTV-Only",
               "Cable/CTV-Only TV Advertisers (No Broadcast)",
               "Advertisers whose TV spend is entirely on cable + CTV — no broadcast. "
               "Digital and radio spend do not disqualify. Ranked by cable + CTV $. "
               "Minimum $25K TV spend floor.",
               cc_only)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

    return {
        "completed": completed,
        "broadcast_only": bc_only,
        "cable_ctv_only": cc_only,
        "total_advertisers": len(raw),
    }


if __name__ == "__main__":
    out = ROOT / "Political_Spend_vs_Results_2026.xlsx"
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("/mnt/user-data/uploads/Home_Advertiser__12_.xlsx")
    result = build_workbook(out, inp)
    print(f"Wrote: {out}")
    print(f"  Completed primaries:  {len(result['completed'])}")
    print(f"  Broadcast-only:       {len(result['broadcast_only'])}")
    print(f"  Cable/CTV-only:       {len(result['cable_ctv_only'])}")
