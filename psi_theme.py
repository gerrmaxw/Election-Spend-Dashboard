"""
psi_theme.py — Political Spend Intelligence design system
Drop this file into your Streamlit project and call apply_theme() at the
top of every page script (or in a shared utils.py).

Usage
-----
    from psi_theme import apply_theme, insight_callout, chart_headline, COLORS, MEDIA_COLORWAY
    apply_theme()

    # Insight callout
    insight_callout("Candidates with <b>cable grade A/B</b> won at a 75% rate.")

    # Chart headline above a plotly chart
    chart_headline(
        "Broadcast Dependency vs. DMA Overlap",
        sub="Bubble size = total spend. Bottom-left quadrant = highest waste risk.",
        method="DMA overlap = % of district voters within the primary DMA footprint.",
    )
    st.plotly_chart(fig, use_container_width=True)
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.io as pio

# ─── Color tokens ─────────────────────────────────────────────────────────────
COLORS = {
    # Media types (Okabe-Ito colorblind-safe palette)
    "broadcast": "#D55E00",
    "cable":     "#0072B2",
    "ctv":       "#009E73",
    "digital":   "#E69F00",
    "radio":     "#CC79A7",
    # Political
    "dem":       "#1A5DAD",
    "rep":       "#CC2929",
    # Semantic
    "positive":  "#1A7A4A",
    "negative":  "#CC2929",
    "warning":   "#B8860B",
    # Shell
    "nav_bg":    "#1C2433",
    "page_bg":   "#F5F4F1",
    "card_bg":   "#FFFFFF",
    "border":    "#E2E1DC",
    "border_grid": "#F0EFE8",
    "text":      "#1A1A1A",
    "text_muted": "#5C5C5C",
    "text_faint": "#8C8C8C",
    # Comcast accent
    "comcast_red": "#DA291C",
}

# Ordered for stacked bar / chart legend (broadcast → cable → ctv → digital → radio)
MEDIA_COLORWAY = [
    COLORS["broadcast"],
    COLORS["cable"],
    COLORS["ctv"],
    COLORS["digital"],
    COLORS["radio"],
]

# ─── Plotly template ──────────────────────────────────────────────────────────
# Apply to any chart with: fig.update_layout(template="psi")
# Or set globally (done automatically by apply_theme()).

_AXIS_COMMON = dict(
    gridcolor=COLORS["border_grid"],
    linecolor=COLORS["border"],
    tickfont=dict(family="IBM Plex Mono, monospace", size=11, color=COLORS["text_faint"]),
    title_font=dict(family="IBM Plex Sans, sans-serif", size=11, color=COLORS["text_muted"]),
    zeroline=False,
    showline=True,
    ticks="outside",
    ticklen=3,
    tickcolor=COLORS["border"],
)

PSI_TEMPLATE = go.layout.Template(
    layout=go.Layout(
        font=dict(family="IBM Plex Sans, sans-serif", color=COLORS["text"], size=12),
        title=dict(
            font=dict(family="Barlow Condensed, sans-serif", size=19, color=COLORS["text"]),
            x=0,
            xanchor="left",
            pad=dict(b=4),
        ),
        paper_bgcolor=COLORS["card_bg"],
        plot_bgcolor=COLORS["card_bg"],
        colorway=MEDIA_COLORWAY,
        xaxis={**_AXIS_COMMON},
        yaxis={**_AXIS_COMMON},
        legend=dict(
            font=dict(family="IBM Plex Sans, sans-serif", size=11, color=COLORS["text_muted"]),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            orientation="h",
            yanchor="bottom",
            y=-0.25,
            xanchor="left",
            x=0,
        ),
        margin=dict(t=16, b=48, l=48, r=16),
        hoverlabel=dict(
            bgcolor=COLORS["nav_bg"],
            font=dict(family="IBM Plex Sans, sans-serif", size=11, color="#FFFFFF"),
            bordercolor=COLORS["nav_bg"],
            align="left",
        ),
        # Scatter / bubble defaults
        scattermode="group",
        # Bar defaults
        barmode="relative",
    )
)

pio.templates["psi"] = PSI_TEMPLATE


# ─── CSS injection ────────────────────────────────────────────────────────────
_CSS = """
<style>
/* ── Fonts ── */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,500;0,600;1,400&family=Barlow+Condensed:wght@500;600;700&display=swap');

/* ── Global ── */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    font-family: 'IBM Plex Sans', sans-serif !important;
    background-color: #F5F4F1 !important;
    color: #1A1A1A !important;
}

/* ── Hide Streamlit chrome (but keep sidebar toggle in toolbar) ── */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stDecoration"] { display: none !important; }
.stDeployButton { display: none !important; }
[data-testid="stToolbar"] [data-testid="stToolbarActions"] { display: none !important; }
[data-testid="stStatusWidget"] { display: none !important; }

/* Make sure the sidebar reopen button is always visible */
[data-testid="stToolbar"] {
    background: transparent !important;
    z-index: 9999 !important;
}
[data-testid="stToolbar"] button svg {
    color: #1A1A1A !important;
    fill: #1A1A1A !important;
}

/* ── Main content ── */
.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 3rem !important;
    max-width: 1200px !important;
    background-color: #F5F4F1 !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] > div:first-child {
    background-color: #1C2433 !important;
    border-right: none !important;
}
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown li,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stMultiSelect label,
[data-testid="stSidebar"] .stRadio label,
[data-testid="stSidebar"] [data-testid="stRadio"] label,
[data-testid="stSidebar"] [data-testid="stRadio"] label p,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li,
[data-testid="stSidebar"] [data-testid="stCaption"],
[data-testid="stSidebar"] .stCaption {
    color: #FFFFFF !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
}

/* Multiselect chips inside sidebar — keep dark blue text on light blue bg */
[data-testid="stSidebar"] [data-baseweb="tag"] span {
    color: #0072B2 !important;
}
/* Sidebar buttons keep dark text on their light background */
[data-testid="stSidebar"] .stButton > button {
    color: #1A1A1A !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #FFFFFF !important;
    font-family: 'Barlow Condensed', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px !important;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.08) !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background-color: rgba(255,255,255,0.06) !important;
    border-color: rgba(255,255,255,0.14) !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] span {
    color: #FFFFFF !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 13px !important;
}

/* ── Page headings ── */
h1 {
    font-family: 'Barlow Condensed', sans-serif !important;
    font-size: 28px !important;
    font-weight: 600 !important;
    color: #1A1A1A !important;
    letter-spacing: 0.2px !important;
    line-height: 1.2 !important;
}
h2 {
    font-family: 'Barlow Condensed', sans-serif !important;
    font-size: 21px !important;
    font-weight: 600 !important;
    color: #1A1A1A !important;
}
h3 {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    color: #1A1A1A !important;
}
p, li {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 14px !important;
    color: #1A1A1A !important;
    line-height: 1.55 !important;
}

/* ── st.metric cards ── */
[data-testid="stMetric"] {
    background-color: #FFFFFF !important;
    border: 1px solid #E2E1DC !important;
    border-radius: 0 !important;
    padding: 14px 16px !important;
    min-height: 94px !important;
    overflow: visible !important;
}
[data-testid="stMetric"] *,
[data-testid="stMetric"] p {
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
}
[data-testid="stMetricLabel"] > div {
    font-size: 10px !important;
    text-transform: uppercase !important;
    letter-spacing: 0.4px !important;
    color: #8C8C8C !important;
    font-weight: 500 !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    line-height: 1.2 !important;
    min-height: 2.4em !important;
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    word-break: normal !important;
}
[data-testid="stMetricValue"] > div {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 24px !important;
    font-weight: 500 !important;
    color: #1A1A1A !important;
    line-height: 1.1 !important;
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    overflow-wrap: anywhere !important;
}
[data-testid="stMetricDelta"] > div {
    font-size: 11px !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
}
/* Positive delta green, negative red */
[data-testid="stMetricDelta"][data-direction="positive"] > div { color: #1A7A4A !important; }
[data-testid="stMetricDelta"][data-direction="negative"] > div { color: #CC2929 !important; }

/* ── Custom KPI cards ── */
.psi-kpi-card {
    background-color: #FFFFFF;
    border: 1px solid #E2E1DC;
    min-height: 94px;
    padding: 13px 14px 14px;
    margin-bottom: 10px;
}
.psi-kpi-label {
    color: #4E555F;
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 10px;
    font-weight: 600;
    line-height: 1.22;
    text-transform: uppercase;
    letter-spacing: 0.35px;
    overflow-wrap: normal;
    word-break: normal;
}
.psi-kpi-value {
    color: #1A1A1A;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 22px;
    font-weight: 600;
    line-height: 1.08;
    margin-top: 8px;
    overflow-wrap: normal;
    word-break: normal;
    hyphens: none;
}
.psi-kpi-delta {
    color: #5C5C5C;
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 11px;
    line-height: 1.25;
    margin-top: 6px;
    overflow-wrap: anywhere;
}
.psi-kpi-broadcast { border-top: 3px solid #D55E00; }
.psi-kpi-cable { border-top: 3px solid #0072B2; }
.psi-kpi-ctv { border-top: 3px solid #009E73; }
.psi-kpi-digital { border-top: 3px solid #E69F00; }
.psi-kpi-positive { border-top: 3px solid #1A7A4A; }
.psi-kpi-negative { border-top: 3px solid #CC2929; }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background-color: #FFFFFF !important;
    border-bottom: 2px solid #E2E1DC !important;
    gap: 0 !important;
    padding: 0 !important;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 13px !important;
    font-weight: 400 !important;
    color: #5C5C5C !important;
    padding: 10px 20px !important;
    border-radius: 0 !important;
    border: none !important;
    background-color: transparent !important;
}
.stTabs [aria-selected="true"] {
    font-weight: 600 !important;
    color: #1A1A1A !important;
}
.stTabs [data-baseweb="tab-highlight"] {
    background-color: #DA291C !important;
    height: 2px !important;
}
.stTabs [data-baseweb="tab-border"] { display: none !important; }

/* ── Selectbox / multiselect ── */
[data-baseweb="select"] > div {
    border-radius: 0 !important;
    border-color: #D8D7D2 !important;
    background-color: #F5F4F1 !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 12px !important;
}
[data-baseweb="select"] span {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 12px !important;
    color: #1A1A1A !important;
}
[data-baseweb="select"] > div *,
[data-baseweb="select"] input {
    color: #1A1A1A !important;
    -webkit-text-fill-color: #1A1A1A !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div *,
[data-testid="stSidebar"] [data-baseweb="select"] input {
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF !important;
}
[data-baseweb="tag"] {
    border-radius: 2px !important;
    background-color: #E8F0F8 !important;
}
[data-baseweb="tag"] span {
    color: #0072B2 !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 11px !important;
}
[data-testid="stSidebar"] [data-baseweb="tag"] span {
    color: #0072B2 !important;
    -webkit-text-fill-color: #0072B2 !important;
}

/* ── Dataframe ── */
[data-testid="stDataFrame"] {
    border: 1px solid #E2E1DC !important;
    border-radius: 0 !important;
}
[data-testid="stDataFrame"] th {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 10.5px !important;
    text-transform: uppercase !important;
    letter-spacing: 0.7px !important;
    color: #8C8C8C !important;
    font-weight: 600 !important;
    background-color: #FFFFFF !important;
    border-bottom: 2px solid #E2E1DC !important;
    padding: 7px 10px !important;
}
[data-testid="stDataFrame"] td {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 12px !important;
    color: #1A1A1A !important;
    border-bottom: 1px solid #F0EFE8 !important;
    padding: 8px 10px !important;
}

/* ── Buttons ── */
.stButton > button {
    border-radius: 0 !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    border: 1px solid #D8D7D2 !important;
    background-color: #F5F4F1 !important;
    color: #1A1A1A !important;
    padding: 6px 16px !important;
    transition: all 0.12s !important;
}
.stButton > button:hover {
    border-color: #0072B2 !important;
    color: #0072B2 !important;
    background-color: #E8F0F8 !important;
}
.stButton > button[kind="primary"] {
    background-color: #0072B2 !important;
    border-color: #0072B2 !important;
    color: #FFFFFF !important;
}

/* ── st.info / callout ── */
[data-testid="stInfo"] {
    background-color: #FFF8E8 !important;
    border-left: 3px solid #E69F00 !important;
    border-radius: 0 !important;
    color: #1A1A1A !important;
}
[data-testid="stInfo"] p {
    font-size: 13.5px !important;
    line-height: 1.55 !important;
}

/* ── st.warning ── */
[data-testid="stWarning"] {
    border-radius: 0 !important;
    border-left: 3px solid #B8860B !important;
}

/* ── st.success ── */
[data-testid="stSuccess"] {
    border-radius: 0 !important;
    border-left: 3px solid #1A7A4A !important;
}

/* ── Text input ── */
[data-testid="stTextInput"] input {
    border-radius: 0 !important;
    border-color: #D8D7D2 !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 13px !important;
    background-color: #FFFFFF !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: #0072B2 !important;
    box-shadow: none !important;
}

/* ── Expander (methodology toggle) ── */
[data-testid="stExpander"] {
    border: none !important;
    border-top: 1px solid #E2E1DC !important;
    border-radius: 0 !important;
    background-color: transparent !important;
    margin-top: 8px !important;
}
[data-testid="stExpander"] summary {
    font-size: 11px !important;
    color: #8C8C8C !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    padding: 6px 0 !important;
}
[data-testid="stExpander"] summary:hover { color: #1A1A1A !important; }
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] {
    font-size: 11px !important;
    color: #6C6C6C !important;
    background-color: #F5F4F1 !important;
    padding: 8px 12px !important;
    line-height: 1.65 !important;
}

/* ── Dividers ── */
hr {
    border-color: #E2E1DC !important;
    margin: 12px 0 !important;
}

/* ── Chart container wrapper ── */
.psi-chart-wrap {
    background: #FFFFFF;
    border: 1px solid #E2E1DC;
    padding: 20px 22px 12px;
    margin-bottom: 16px;
}
.psi-chart-hed {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 18px;
    font-weight: 600;
    line-height: 1.25;
    margin-bottom: 3px;
    color: #1A1A1A;
}
.psi-chart-sub {
    font-size: 11.5px;
    color: #5C5C5C;
    margin-bottom: 12px;
}

/* ── Insight callout (manual) ── */
.psi-callout {
    background: #FFF8E8;
    border-left: 3px solid #E69F00;
    padding: 12px 16px;
    margin-bottom: 16px;
}
.psi-callout-hd {
    font-size: 9.5px;
    text-transform: uppercase;
    letter-spacing: 1.1px;
    color: #B8860B;
    font-weight: 700;
    margin-bottom: 4px;
}
.psi-callout-body {
    font-size: 13.5px;
    color: #1A1A1A;
    line-height: 1.55;
}

/* ── KPI top-border accent helper ── */
.psi-metric-broadcast [data-testid="stMetric"] { border-top: 3px solid #D55E00 !important; }
.psi-metric-cable      [data-testid="stMetric"] { border-top: 3px solid #0072B2 !important; }
.psi-metric-ctv        [data-testid="stMetric"] { border-top: 3px solid #009E73 !important; }
.psi-metric-digital    [data-testid="stMetric"] { border-top: 3px solid #E69F00 !important; }
.psi-metric-positive   [data-testid="stMetric"] { border-top: 3px solid #1A7A4A !important; }
.psi-metric-negative   [data-testid="stMetric"] { border-top: 3px solid #CC2929 !important; }

/* ── Readability / interaction polish ── */
button, [role="button"], a {
    outline-offset: 2px !important;
}
button:focus-visible,
[role="button"]:focus-visible,
[data-baseweb="tab"]:focus-visible {
    outline: 3px solid #E69F00 !important;
    box-shadow: 0 0 0 2px #FFFFFF !important;
}
.stButton > button,
.stDownloadButton > button,
[data-testid="stFileUploader"] button {
    min-height: 40px !important;
    white-space: normal !important;
    line-height: 1.25 !important;
    word-break: break-word !important;
}
.stDownloadButton > button,
[data-testid="stFileUploader"] button {
    border-radius: 0 !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    border: 1px solid #0072B2 !important;
    background-color: #FFFFFF !important;
    color: #005A8D !important;
}
.stDownloadButton > button:hover,
[data-testid="stFileUploader"] button:hover {
    background-color: #E8F0F8 !important;
    color: #003C5F !important;
}
[data-testid="stFileUploader"] {
    background-color: #FFFFFF !important;
    border: 1px solid #C9D7E4 !important;
    padding: 12px !important;
}
[data-testid="stFileUploader"] section {
    border-color: #8CB6D8 !important;
    background-color: #F8FBFD !important;
}
[data-testid="stFileUploader"] small,
[data-testid="stFileUploader"] span,
[data-testid="stFileUploader"] p {
    color: #1A1A1A !important;
}
.stTabs [data-baseweb="tab-list"] {
    overflow-x: auto !important;
    flex-wrap: nowrap !important;
}
.stTabs [data-baseweb="tab"] {
    min-height: 44px !important;
    min-width: max-content !important;
    white-space: normal !important;
    word-break: keep-all !important;
}
.stTabs [data-baseweb="tab"] p {
    color: inherit !important;
    font-size: 13px !important;
    line-height: 1.25 !important;
    margin: 0 !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label {
    border-radius: 4px !important;
    padding: 6px 8px !important;
    margin: 2px 0 !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) {
    background-color: rgba(255,255,255,0.14) !important;
    box-shadow: inset 3px 0 0 #E69F00 !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label:hover {
    background-color: rgba(255,255,255,0.09) !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label p {
    white-space: normal !important;
    line-height: 1.25 !important;
}
[data-testid="stDataFrame"] div,
[data-testid="stDataFrame"] span {
    color: #1A1A1A;
}
</style>
"""


# ─── Public API ───────────────────────────────────────────────────────────────

def apply_theme() -> None:
    """
    Inject CSS overrides and register the PSI Plotly template.
    Call once at the top of each page script (or in a shared utils.py
    that every page imports).
    """
    st.set_page_config(
        page_title="Political Spend Intelligence",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(_CSS, unsafe_allow_html=True)
    pio.templates.default = "psi"


def insight_callout(text: str, label: str = "Key Insight") -> None:
    """
    Render the amber insight callout box.

    Equivalent to the st.info() call, but styled correctly.
    Use st.info() when you want Streamlit-native fallback — the CSS
    above already re-skins st.info() to amber.

    Args:
        text:  HTML-safe string. Bold with <b>…</b>.
        label: Override the "KEY INSIGHT" label text.
    """
    st.markdown(
        f"""
        <div class="psi-callout">
            <div class="psi-callout-hd">{label}</div>
            <div class="psi-callout-body">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def chart_headline(title: str, sub: str = "", method: str = "") -> None:
    """
    Render the chart headline block above a st.plotly_chart() call.

    Pattern:
        chart_headline("Broadcast vs. DMA Overlap", sub="Bubble size = spend")
        st.plotly_chart(fig, use_container_width=True)
        # If method is set, a collapsed expander appears after the chart —
        # call chart_methodology(method) immediately after the chart.

    Args:
        title:  Bold headline (Barlow Condensed). State the takeaway, not the variable name.
        sub:    Smaller subtitle / axis-explanation line.
        method: If non-empty, renders a collapsed "Methodology note" expander BELOW
                the chart. Pass the same string to chart_methodology() instead if
                you need the expander below the chart rather than here.
    """
    html = f'<div class="psi-chart-hed">{title}</div>'
    if sub:
        html += f'<div class="psi-chart-sub">{sub}</div>'
    st.markdown(html, unsafe_allow_html=True)
    if method:
        # methodology expander goes inline after the headline
        with st.expander("▼ Methodology note"):
            st.markdown(method)


def chart_methodology(text: str) -> None:
    """
    Collapsed methodology note below a chart.
    Equivalent to: with st.expander('▼ Methodology note'): st.markdown(text)
    """
    with st.expander("▼ Methodology note"):
        st.markdown(text)


def metric_accent(color_key: str):
    """
    Context manager that wraps st.metric() in a div that applies a
    colored top-border accent via CSS class.

    Usage:
        with metric_accent("cable"):
            st.metric("Avg Cable Share", "22%", "+3 pts")

    Valid color_key values: broadcast, cable, ctv, digital, positive, negative
    """
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        st.markdown(f'<div class="psi-metric-{color_key}">', unsafe_allow_html=True)
        yield
        st.markdown("</div>", unsafe_allow_html=True)

    return _ctx()


# ─── Plotly helpers ───────────────────────────────────────────────────────────

def media_bar_chart(
    df,
    candidates_col: str,
    media_cols: list,
    orientation: str = "h",
    title: str = "",
) -> go.Figure:
    """
    Build a stacked bar chart from a DataFrame with per-media-type columns.

    Args:
        df:             DataFrame with one row per candidate.
        candidates_col: Column name for candidate labels.
        media_cols:     Ordered list of column names matching MEDIA_COLORWAY order:
                        [broadcast, cable, ctv, digital, radio].
        orientation:    'h' (default) or 'v'.
        title:          Chart title (leave empty; use chart_headline() instead).

    Returns:
        go.Figure with PSI template applied.
    """
    media_labels = ["Broadcast", "Cable", "CTV", "Digital", "Radio"]
    fig = go.Figure()
    for col, label, color in zip(media_cols, media_labels, MEDIA_COLORWAY):
        x_vals = df[col] if orientation == "v" else df[candidates_col]
        y_vals = df[candidates_col] if orientation == "v" else df[col]
        fig.add_trace(go.Bar(
            name=label,
            x=x_vals if orientation == "v" else y_vals,
            y=y_vals if orientation == "v" else x_vals,
            orientation=orientation,
            marker_color=color,
            marker_line_width=0,
        ))
    fig.update_layout(
        template="psi",
        barmode="stack",
        title=title,
        showlegend=True,
    )
    return fig


def waste_scatter(df) -> go.Figure:
    """
    Build the broadcast waste scatter (DMA overlap vs broadcast share).
    Expects columns: dma_overlap, broadcast_pct, spend, party, name.
    """
    fig = go.Figure()
    for party, color in [("D", COLORS["dem"]), ("R", COLORS["rep"])]:
        d = df[df["party"] == party]
        fig.add_trace(go.Scatter(
            mode="markers",
            name="Democrat" if party == "D" else "Republican",
            x=d["dma_overlap"],
            y=d["broadcast_pct"],
            marker=dict(
                size=d["spend"].apply(lambda s: (s / 1e6) ** 0.5 * 5),
                color=color,
                opacity=0.6,
                line=dict(color=color, width=1.5),
            ),
            text=d["name"],
            customdata=d[["spend"]],
            hovertemplate=(
                "<b>%{text}</b><br>"
                "DMA Overlap: %{x:.0f}%<br>"
                "Broadcast Share: %{y:.0f}%<br>"
                "Spend: $%{customdata[0]:.1f}M"
                "<extra></extra>"
            ),
        ))
    # High-waste quadrant annotation
    fig.add_shape(type="rect", x0=0, x1=50, y0=50, y1=100,
                  fillcolor=f"{COLORS['broadcast']}12", line_width=0, layer="below")
    fig.add_annotation(x=5, y=98, text="HIGH WASTE ZONE",
                       font=dict(size=9, color=COLORS["broadcast"], family="Barlow Condensed"),
                       showarrow=False, xanchor="left")
    fig.update_layout(
        template="psi",
        xaxis_title="DMA Overlap with District (%)",
        yaxis_title="Broadcast Share of Spend (%)",
        xaxis=dict(range=[0, 100]),
        yaxis=dict(range=[0, 100]),
    )
    return fig


# ─── Sidebar helpers ──────────────────────────────────────────────────────────

def sidebar_logo(title: str = "PSI.", subtitle: str = "Political Spend Intelligence") -> None:
    """Render the nav logo block in the sidebar."""
    st.sidebar.markdown(
        f"""
        <div style="padding:8px 0 16px;">
            <div style="font-family:'Barlow Condensed',sans-serif;font-size:22px;
                        font-weight:700;color:#fff;letter-spacing:.5px;">
                {title.replace('.','<em style="color:#DA291C;font-style:normal;">.</em>')}
            </div>
            <div style="font-size:10px;color:#FFFFFF;text-transform:uppercase;
                        letter-spacing:1.1px;margin-top:3px;opacity:0.85;">{subtitle}</div>
        </div>
        <hr/>
        """,
        unsafe_allow_html=True,
    )


def sidebar_section_label(label: str) -> None:
    """Render a nav group label in the sidebar (CORE / PITCH / SUPPLEMENTAL)."""
    st.sidebar.markdown(
        f'<div style="font-size:10px;color:#FFFFFF;text-transform:uppercase;'
        f'letter-spacing:1.2px;font-weight:700;padding:12px 0 4px;opacity:0.85;">{label}</div>',
        unsafe_allow_html=True,
    )


# ─── Streamlit component fallbacks ────────────────────────────────────────────
"""
DESIGN → STREAMLIT COMPONENT MAPPING
=====================================

Design element          Streamlit equivalent + notes
─────────────────────── ──────────────────────────────────────────────────────
KPI card strip          st.columns(4) with st.metric() inside each column.
                        Wrap each col in metric_accent("cable") for top border.

Insight callout         insight_callout("text with <b>bold</b> highlights")
                        Native fallback: st.info() (CSS above re-skins it)

Chart headline          chart_headline("Takeaway title", sub="axis note")
                        Place immediately before st.plotly_chart()

Methodology toggle      chart_methodology("Explanation text")
                        Place immediately after st.plotly_chart()
                        Native: with st.expander("▼ Methodology note"): st.markdown(...)

Left rail nav           st.sidebar with st.radio() for page selection.
                        sidebar_logo() + sidebar_section_label() for visual grouping.
                        st.sidebar.selectbox() for cycle/state/office filters.

Stacked media bars      media_bar_chart(df, "candidate", ["bc","ca","ct","di","ra"])

Broadcast waste scatter waste_scatter(df)  — expects dma_overlap, broadcast_pct, spend, party, name cols

Pitch mode toggle       st.sidebar.toggle("Pitch Mode") or st.sidebar.checkbox("▶ Pitch Mode")
                        When on: use st.set_page_config(layout="centered") and
                        increase all font sizes via CSS variable injection.
                        NOTE: This requires a custom JS component if you want
                        it to affect chart size dynamically; otherwise just
                        conditionally render a simplified view.

Grade badge             st.markdown(f'<span class="grade g{grade}">{grade}</span>',
                        unsafe_allow_html=True)
                        (Add the .grade/.gA/.gB CSS classes from the HTML to your CSS injection)

Breadcrumbs             st.markdown(f"Dashboard / **{page_name}**")

Filter bar (sticky)     Use st.sidebar for all global filters. A true sticky horizontal
                        filter bar requires custom components — not achievable natively.
"""
