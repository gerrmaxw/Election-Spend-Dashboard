# Political Spend Intelligence — Claude Code Integration Guide

This project is a **Streamlit** app (`app/dashboard.py`) backed by a SQLite database
(`local_data/broadcast_waste_analyzer.sqlite`). A fully-specified design system has
been built for it. Your job is to implement that design faithfully.

---

## 1. Drop in the theme file

Copy **`psi_theme.py`** into the project root (same level as `app/`).
Call `apply_theme()` as the **very first statement** in every page script:

```python
# app/dashboard.py (and every pages/*.py file)
from psi_theme import (
    apply_theme, insight_callout, chart_headline,
    chart_methodology, metric_accent, sidebar_logo,
    sidebar_section_label, media_bar_chart, waste_scatter,
    COLORS, MEDIA_COLORWAY,
)

apply_theme()   # must come before any other st.* call
```

`apply_theme()` calls `st.set_page_config(layout="wide")`, injects all CSS, and sets
the PSI Plotly template as the default. Never call `st.set_page_config` again elsewhere.

---

## 2. Design tokens

All hex values live in `COLORS` dict. Use these — never hard-code color strings.

| Key | Value | Used for |
|---|---|---|
| `broadcast` | `#D55E00` | Broadcast TV spend |
| `cable` | `#0072B2` | Cable spend / primary accent |
| `ctv` | `#009E73` | CTV / streaming spend |
| `digital` | `#E69F00` | Digital spend / warning tint |
| `radio` | `#CC79A7` | Radio spend |
| `dem` | `#1A5DAD` | Democrat party |
| `rep` | `#CC2929` | Republican party |
| `positive` | `#1A7A4A` | Up deltas, coverage ✓ |
| `negative` | `#CC2929` | Down deltas |
| `warning` | `#B8860B` | Partial coverage, caution |
| `nav_bg` | `#1C2433` | Sidebar background |
| `page_bg` | `#F5F4F1` | Main content background |
| `comcast_red` | `#DA291C` | Logo accent, active nav indicator |

**Chart color order** (stacked bars, legends): broadcast → cable → ctv → digital → radio.
Always use `MEDIA_COLORWAY` as the `color_discrete_sequence` / `colorway`.

### Typography

| Role | Font | Size |
|---|---|---|
| Page / chart titles | Barlow Condensed 600 | 28px / 18–21px |
| Body, labels | IBM Plex Sans | 14px |
| Numbers, monospace | IBM Plex Mono | 28px (KPI), 11–12px (table) |

---

## 3. Sidebar navigation

The sidebar IS the left nav. Build it in `app/dashboard.py` using Streamlit's
`st.sidebar` + `st.sidebar.radio()`. Call the PSI helpers for the logo and section
grouping:

```python
sidebar_logo()                        # PSI. wordmark + subtitle
sidebar_section_label("Core")
page = st.sidebar.radio("", [
    "Setup & Coverage", "Election Calendar",
    "Spend Dashboard", "Polling Watchlist",
], label_visibility="collapsed", key="nav_core")

sidebar_section_label("Pitch")
# … etc.
```

Nine pages total, in three groups:

| Group | Pages |
|---|---|
| **Core** | Setup & Coverage, Election Calendar, Spend Dashboard, Polling Watchlist |
| **Pitch** | Broadcast Waste, Cable-in-Mix Effectiveness |
| **Supplemental** | PAC Spend Tracker, Demographics, Case Study Finder |

**Global filters** (cycle, office, states, spend floor) go below the nav groups as
`st.sidebar.selectbox()` calls under a `st.sidebar.divider()`. The CSS already skins
sidebar selects to the dark theme.

---

## 4. Page structure template

Every page follows this exact visual order:

```python
# 1. Page title
st.markdown("# Page Title")
st.caption("2026 cycle · US House · Updated Apr 24, 2026")

# 2. KPI strip — always st.columns() + st.metric() + metric_accent()
col1, col2, col3, col4 = st.columns(4)
with col1:
    with metric_accent("cable"):
        st.metric("Total Spend Tracked", "$137.3M", "↑ $4.1M vs. 30 days ago")

# 3. Insight callout (use on every page)
insight_callout(
    "The 4 highest-spending candidates allocate an average of <b>81% to broadcast</b>, "
    "despite operating in districts where the DMA covers &lt;30% of eligible voters."
)

# 4. Charts / tables in st.columns(2) where design shows two-column layout
col_left, col_right = st.columns(2)
with col_left:
    chart_headline("Media Mix by State", sub="Share of total TV spend by media type")
    st.plotly_chart(fig, use_container_width=True)
    chart_methodology("Spend per FEC disbursement purpose codes…")
```

---

## 5. KPI accent colors per page

Use `metric_accent(key)` to apply the colored top border. Match this mapping:

| Metric | Accent key |
|---|---|
| Spend / total dollars | `"cable"` |
| Candidate count | `"ctv"` |
| Coverage / freshness | `"broadcast"` |
| Positive outcome | `"positive"` |
| Negative / risk | `"negative"` |

---

## 6. Page-by-page implementation notes

### Setup & Coverage (`page_setup.py`)
- **KPIs (4):** Total Spend Tracked, Candidates Tracked, States w/ Full Coverage, Data Freshness
- **Left chart:** Horizontal stacked bars — media mix share per state. Use `go.Bar` with
  `barmode="stack"` and `orientation="h"`. One bar per state, 5 segments (broadcast→radio).
- **Right top:** Upcoming primaries table — `st.dataframe` with columns: Date, State, Race(s), Type.
  Color "Primary" badges `#E8F0F8 / #0072B2`, "Runoff" badges `#FFF3CD / #856404`.
- **Right bottom:** State coverage table — State, Races, FEC ✓, Polls (✓ or Partial), Coverage %.
  Show a thin green progress bar for coverage % using `st.progress()` or custom HTML.

### Spend Dashboard (`page_spend.py`)
- **KPIs (4):** Total Tracked Spend, Largest Spender, Avg Cable Share, Avg Broadcast Share
- **Main chart:** Horizontal stacked bar — candidates sorted by total spend (desc), 5 media segments.
  Use `media_bar_chart(df, "candidate_name", ["bc_spend","ca_spend","ct_spend","di_spend","ra_spend"])`.
  Height: `400px`.
- **Table below:** Per-candidate breakdown with columns Candidate, Pty (D/R colored), Total, Bcast%,
  Cable%, CTV%, DMA Ovlp, Grade badge.

### Broadcast Waste (`page_waste.py`) — Pitch page
- **KPIs (3):** Est. Wasted Spend ($), High-Waste Candidates (#), Avg DMA Overlap in High-Waste Group
- **Main chart (full width):** Bubble scatter — x=DMA Overlap%, y=Broadcast Share%, bubble size=√spend.
  Democrats `#1A5DAD`, Republicans `#CC2929`. Use `waste_scatter(df)`.
  Add a shaded rectangle over x<50, y>50 labeled "HIGH WASTE ZONE" in broadcast orange.
- **Right panel:** Ranked candidate table — sorted by wasted spend desc. Show waste bar (thin orange
  fill showing `broadcast_pct × (1 - dma_overlap)`).

### Cable-in-Mix Effectiveness (`page_cable.py`) — Pitch page
- **KPIs (3):** Avg Win Margin (grade A/B), Avg Win Margin (grade D/F), Cable Grade Correlation
- **Left chart:** Scatter — cable share% vs. vote margin%. One dot per candidate, party-colored.
  Add linear trend line. Annotate "Grade A/B candidates" cluster.
- **Right chart:** Bar — win rate by cable grade (A/B/C/D/F). Color bars by grade color.
- **Table:** Full candidate table with Cable Grade badge, Cable%, Vote%, Margin columns.

### Polling Watchlist (`page_polls.py`)
- **KPIs (4):** Toss-up Races, Lean D, Lean R, Races Tightening
- **Table (full width):** Race, D candidate, R candidate, D%, R%, Margin, Rating, Trend, Last Poll date.
  Color D% values `#1A5DAD`, R% values `#CC2929`. Rating badges: Toss-up=yellow, Lean=light blue/red,
  Likely/Safe=darker.
- **Trend indicators:** "tightening" = `▼` in warning amber; "widening" = `▲` in green; "stable" = `—`.

### Election Calendar (`page_calendar.py`)
- **KPIs (3):** Primaries Remaining, Next Primary Date, Runoffs Scheduled
- Show a month-by-month timeline. For each month use `st.expander(f"**{month}** — {n} events")`.
  Inside: table with Date, State, Race(s), Type. Key races get a `⬥` marker and bold text.

### PAC Spend Tracker (`page_pac.py`)
- **KPIs (3):** Total IE Spend, Super PACs Active, Party Committee Spend
- **Table:** Committee name, Type, Side (D=blue / R=red chip), Total Spend, Target Races, Action
  (Support = green chip, Oppose = red chip).
- **Chart:** Horizontal bar — PACs sorted by total spend. Dem PACs `#1A5DAD`, Rep PACs `#CC2929`.

### Demographics (`page_demo.py`)
- **KPIs (3):** Avg Cable Penetration (grade A), Avg Cable Penetration (grade D/F), College-educated
- **Table:** District, College%, Cable%, Median Income, Median Age, Population, Grade badge.
  Sort by cable% desc by default.
- **Chart:** Scatter — cable penetration vs. median income, sized by population, colored by grade.

### Case Study Finder (`page_cases.py`)
- Sidebar filters: office type, state, spend range, cable grade.
- Grid of candidate cards (3 columns): District name, party chip, total spend, cable grade badge,
  vote margin, a mini stacked bar showing media mix. Click to expand full breakdown.

---

## 7. Data wiring

The SQLite schema (from `.claude/settings.local.json` history) has these key tables:

| Table | Key columns |
|---|---|
| `races` | state, district, office, cycle, partisan_lean_d_minus_r |
| `candidates` | name, party, race_id, total_attributed_spend |
| `primaries_per_candidate` | tv_mix (broadcast/cable/ctv/digital shares), total_attributed_spend |
| `upcoming_primaries` | state, primary_date, runoff_date |
| `district_demographics` | state, district, geo_type, population, median_age, median_hh_income, pct_broadband, cable_fit_score, ctv_fit_score, pct_multiunit, urbanicity_score |
| `party_registration` | state, district, partisan_lean_d_minus_r |
| `fec_ie_by_committee` | committee_id, candidate_id, total_spent |
| `state_primary_schedule` | state, primary_date, runoff_date, has_us_senate, us_house_seats |

Load data with `@st.cache_data` on every query function. Pass the global filter selections
(cycle, office, states, spend_floor) as arguments so the cache key invalidates correctly:

```python
@st.cache_data
def load_candidates(cycle: int, office: str, states: tuple, spend_floor: int):
    conn = sqlite3.connect("local_data/broadcast_waste_analyzer.sqlite")
    # … query with WHERE clauses …
    return pd.read_sql(query, conn)
```

---

## 8. Plotly charts

All figures automatically use the PSI template (set globally by `apply_theme()`).
You never need `fig.update_layout(template="psi")` — it's already the default.

Always call `chart_headline(title, sub=...)` **before** `st.plotly_chart(fig, ...)` and
`chart_methodology(text)` **after** it. Wrap both plus the chart in a `st.container()`
with `border=True` to get the white card with border effect:

```python
with st.container(border=True):
    chart_headline("Broadcast Dependency vs. DMA Overlap",
                   sub="Bubble size = total spend. Bottom-left = highest waste risk.")
    st.plotly_chart(waste_scatter(df), use_container_width=True)
    chart_methodology("DMA overlap = % of district voters within the primary DMA footprint.")
```

---

## 9. Grade badges

Add this CSS snippet (already included in `psi_theme.py`'s `_CSS` block if you add it)
and render with inline HTML:

```python
def grade_badge(g: str) -> str:
    colors = {"A": ("#D4EDDA","#155724"), "B": ("#D1ECF1","#0C5460"),
              "C": ("#FFF3CD","#856404"), "D": ("#FFDDC1","#8B4000"), "F": ("#F8D7DA","#721C24")}
    bg, fg = colors.get(g, ("#F0EFE8", "#5C5C5C"))
    return (f'<span style="display:inline-flex;align-items:center;justify-content:center;'
            f'width:26px;height:26px;font-family:\'Barlow Condensed\',sans-serif;'
            f'font-size:17px;font-weight:700;background:{bg};color:{fg};">{g}</span>')
```

Use in dataframes via `st.markdown(..., unsafe_allow_html=True)` or in `st.data_editor`
column config with `st.column_config.TextColumn`.

---

## 10. File layout

```
project-root/
├── psi_theme.py          ← drop here (design system + Streamlit CSS/Plotly)
├── CLAUDE.md             ← this file
├── app/
│   ├── dashboard.py      ← main entrypoint; apply_theme() + sidebar nav + page routing
│   └── pages/            ← one file per page (Streamlit multi-page)
│       ├── page_setup.py
│       ├── page_spend.py
│       ├── page_waste.py
│       ├── page_cable.py
│       ├── page_polls.py
│       ├── page_calendar.py
│       ├── page_pac.py
│       ├── page_demo.py
│       └── page_cases.py
└── local_data/
    └── broadcast_waste_analyzer.sqlite
```

Each `pages/` file starts with `from psi_theme import apply_theme, ...; apply_theme()`.

---

## 11. Quick reference — HTML design ↔ Streamlit

| HTML design element | Streamlit equivalent |
|---|---|
| `.kpi-strip` + `.kpi-card` | `st.columns(N)` + `st.metric()` inside `metric_accent()` |
| `.callout` (amber box) | `insight_callout(text)` or `st.info(text)` |
| `.cw` chart wrapper | `with st.container(border=True):` + `chart_headline()` |
| `.cw-hed` / `.cw-sub` | `chart_headline(title, sub=...)` |
| `.method-btn` expander | `chart_methodology(text)` |
| `.dt` data table | `st.dataframe(df, use_container_width=True)` |
| `.sbar` stacked bars | `media_bar_chart(df, ...)` (Plotly) |
| Bubble scatter | `waste_scatter(df)` |
| `.grade` badge | `grade_badge(g)` helper + `st.markdown(..., unsafe_allow_html=True)` |
| Nav left rail | `st.sidebar` + `st.sidebar.radio()` + `sidebar_logo()` |
| Filter bar | `st.sidebar.selectbox()` calls below nav |
| Breadcrumb | `st.caption("Dashboard / **Page Name**")` |
| `.two-col` grid | `st.columns(2)` |
| Pitch mode | `st.sidebar.toggle("▶ Pitch Mode")` → conditionally hide sidebar + enlarge KPIs |
