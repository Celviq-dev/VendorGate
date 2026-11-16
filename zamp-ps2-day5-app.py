# VendorGate — vendor onboarding verification console (Zamp PS-2)
#
# Run from this folder:
#   pip install -r requirements.txt
#   streamlit run zamp-ps2-day5-app.py
#
# Pages: Overview (KPIs, charts, application register with filters, CSV
# export and row drill-down), New application (form + document checklist,
# optional prefill from an existing record), Verification run (checks 1 to 5
# as live stages, then the decision and the vendor notification), Review
# queue (pending + rejected), Vendor record (verification log, notification,
# resubmission history).
#
# Look and feel: .streamlit/config.toml holds the light + dark theme (switch
# in the app menu, top right). The CSS below is a small interaction layer
# only (focus rings, cursors, transitions, reduced motion).
#
# All decisions come from zamp-ps2-day4-decision.py and all comms from
# zamp-ps2-day5-comms.py. This file renders them and never re-decides.

import importlib.util
import time
from datetime import date, datetime
from html import escape as html_escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


day4 = _load("zamp_day4", "zamp-ps2-day4-decision.py")
day5 = _load("zamp_day5", "zamp-ps2-day5-comms.py")
day3 = day4.day3

STAGE_DELAY_S = 0.45  # pause between stages so each one is visible as it runs

DOC_CHOICES = {
    "Received": None,
    "Not received": None,
    "Invoice received instead": "invoice",
    "Quotation received instead": "quotation",
    "Personal document received instead": "salary_slip",
}
WRONG_TO_CHOICE = {v: k for k, v in DOC_CHOICES.items() if v}

STATUS_LABEL = {"approved": "Approved", "pending": "Pending", "rejected": "Rejected"}
STATUS_ICON = {"approved": ":material/verified:", "pending": ":material/schedule:",
               "rejected": ":material/block:"}
CAUSE_LABEL = {
    "document_missing": "Document not received",
    "document_wrong_type": "Incorrect document",
    "document_expired": "Document expired",
    "document_expired_long": "Expired over 12 months",
    "tax_format": "Invalid tax ID",
    "tax_country": "Tax ID / country mismatch",
    "bank_details": "Bank detail mismatch",
    "required_fields": "Incomplete application",
    "contact_email": "Invalid contact email",
    "name_mismatch": "Beneficiary name mismatch",
}
STAGES = [
    ("Field validation", ":material/fact_check:"),
    ("Document verification", ":material/description:"),
    ("Cross-field consistency", ":material/compare_arrows:"),
    ("Duplicate and risk screening", ":material/policy:"),
    ("Risk scoring and decision", ":material/gavel:"),
]

# Interaction layer from the design system: visible focus, pointer cursors,
# 150-200 ms transitions, reduced motion, tabular figures. Colours, fonts and
# spacing stay in .streamlit/config.toml so both themes stay consistent.
POLISH_CSS = """<style>
button, [role="button"], [role="option"], [role="tab"], a, summary,
[data-baseweb="select"], [data-testid="stCheckbox"] label { cursor: pointer; }
button, a, [data-baseweb="select"] > div, [data-baseweb="input"] {
  transition: border-color 160ms ease, background-color 160ms ease, color 160ms ease, box-shadow 160ms ease; }
:focus-visible { outline: 2px solid var(--st-primary-color, #1d4ed8) !important; outline-offset: 2px; }
[data-testid="stMetricValue"], [data-testid="stMetricDelta"] { font-variant-numeric: tabular-nums; }
[data-testid="stAppDeployButton"] { display: none; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after {
  animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; } }

/* Cockpit components. Every neutral is mixed from currentColor (the active
   theme's text colour), so the same CSS reads correctly in light and dark. */
.vg-head { display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:.6rem 1rem;
  padding:0 .1rem .85rem; margin-bottom:1rem;
  border-bottom:1px solid color-mix(in srgb, currentColor 14%, transparent); }
.vg-head .mark { display:flex; align-items:center; gap:.75rem; }
.vg-head .logo { flex:none; width:38px; height:38px; border-radius:9px; display:flex; align-items:center;
  justify-content:center; color:#fff; font-weight:700; font-size:.8rem; letter-spacing:.08em;
  background:linear-gradient(135deg,#1d4ed8 0%,#3b82f6 100%); box-shadow:0 2px 10px rgba(29,78,216,.25); }
.vg-head h1 { margin:0; padding:0; font-size:1.35rem; line-height:1.25; font-weight:700; letter-spacing:-.01em; }
.vg-head .sub { font-size:.72rem; opacity:.6; letter-spacing:.07em; text-transform:uppercase;
  font-weight:500; margin-top:.1rem; }
.vg-head .meta { display:flex; gap:1.1rem; align-items:center; font-family:'IBM Plex Mono',monospace;
  font-size:.7rem; opacity:.65; text-transform:uppercase; letter-spacing:.08em; white-space:nowrap; }
.vg-head .dot { width:7px; height:7px; border-radius:50%; background:#16a34a; display:inline-block;
  margin-right:.4rem; box-shadow:0 0 0 3px rgba(22,163,74,.18); }
.vg-label { font-size:.64rem; font-weight:700; opacity:.55; letter-spacing:.13em;
  text-transform:uppercase; margin:.1rem 0 .45rem; }
.vg-ribbon { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:.8rem; margin:.2rem 0 1rem; }
.vg-signals { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.7rem; margin-bottom:1.1rem; }
@media (max-width: 900px) { .vg-ribbon, .vg-signals { grid-template-columns:repeat(2,minmax(0,1fr)); } }
.vg-tile { border:1px solid color-mix(in srgb, currentColor 13%, transparent); border-left:3px solid var(--c,#3b82f6);
  border-radius:10px; padding:.95rem 1.05rem; min-height:128px; display:flex; flex-direction:column;
  justify-content:space-between; background:color-mix(in srgb, currentColor 2.5%, transparent); }
.vg-tile .k-label { font-size:.64rem; font-weight:700; opacity:.6; text-transform:uppercase; letter-spacing:.12em; }
.vg-tile .k-val { font-size:1.6rem; font-weight:700; margin-top:.15rem; letter-spacing:-.01em;
  font-variant-numeric:tabular-nums; }
.vg-tile .k-tag { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.66rem;
  font-weight:600; padding:.1rem .45rem; border-radius:4px; margin-top:.25rem; width:fit-content;
  color:var(--c,#3b82f6); background:color-mix(in srgb, var(--c,#3b82f6) 13%, transparent); }
.vg-tile .k-insight { font-size:.72rem; opacity:.72; margin-top:.5rem; line-height:1.4; padding-top:.45rem;
  border-top:1px dashed color-mix(in srgb, currentColor 16%, transparent); }
.vg-chip { border:1px solid color-mix(in srgb, currentColor 13%, transparent); border-radius:9px; padding:.7rem .9rem; }
.vg-chip .sig-head { font-size:.6rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; opacity:.6;
  margin-bottom:.3rem; display:flex; align-items:center; justify-content:space-between; }
.vg-chip .sig-body { font-size:.84rem; font-weight:500; line-height:1.4; }
.vg-tag { font-family:'IBM Plex Mono',monospace; font-size:.58rem; font-weight:700; padding:.08rem .38rem;
  border-radius:3px; opacity:1; color:var(--c); background:color-mix(in srgb, var(--c) 15%, transparent); }
.vg-panel-head { display:flex; justify-content:space-between; align-items:baseline; gap:1rem;
  padding-bottom:.45rem; margin-bottom:.35rem; border-bottom:1px solid color-mix(in srgb, currentColor 9%, transparent); }
.vg-panel-head .t { font-size:.92rem; font-weight:700; }
.vg-panel-head .s { font-size:.62rem; opacity:.55; letter-spacing:.1em; text-transform:uppercase;
  font-family:'IBM Plex Mono',monospace; white-space:nowrap; }
.vg-take { margin-top:.4rem; padding:.55rem .75rem; border-left:2px solid #3b82f6; border-radius:4px;
  font-size:.76rem; line-height:1.45; background:color-mix(in srgb, #3b82f6 7%, transparent); }
.vg-take strong { color:#3b82f6; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap:0;
  border-bottom:1px solid color-mix(in srgb, currentColor 12%, transparent); }
[data-testid="stTabs"] button[role="tab"] { padding:.65rem 1.2rem; font-weight:600; letter-spacing:.03em; }
[data-testid="stTabs"] button[role="tab"] p { font-size:.8rem; text-transform:uppercase; }
</style>"""

POS, WARN, NEG, INFO = "#16a34a", "#d97706", "#dc2626", "#3b82f6"


def page_header(title, sub):
    """Branded header with a live marker and the session timestamp."""
    st.html(f"""<div class="vg-head"><div class="mark"><div class="logo">VG</div>
      <div><h1>{html_escape(title)}</h1><div class="sub">{html_escape(sub)}</div></div></div>
      <div class="meta"><div><span class="dot"></span>Live</div>
      <div>Session · {datetime.now():%Y-%m-%d %H:%M}</div><div>Policy v1.0</div></div></div>""")


def kpi_ribbon(tiles):
    """tiles: (label, value, tag, insight, colour)."""
    st.html('<div class="vg-ribbon">' + "".join(
        f'<div class="vg-tile" style="--c:{c}"><div><div class="k-label">{html_escape(lbl)}</div>'
        f'<div class="k-val">{html_escape(val)}</div><div class="k-tag">{html_escape(tag)}</div></div>'
        f'<div class="k-insight">{ins}</div></div>' for lbl, val, tag, ins, c in tiles) + "</div>")


def signal_grid(chips):
    """chips: (title, tag, body_html, colour)."""
    st.html('<div class="vg-signals">' + "".join(
        f'<div class="vg-chip"><div class="sig-head">{html_escape(t)}'
        f'<span class="vg-tag" style="--c:{c}">{html_escape(tag)}</span></div>'
        f'<div class="sig-body">{body}</div></div>' for t, tag, body, c in chips) + "</div>")


def panel_head(title, sub):
    st.html(f'<div class="vg-panel-head"><div class="t">{html_escape(title)}</div>'
            f'<div class="s">{html_escape(sub)}</div></div>')


def takeaway(text_html):
    st.html(f'<div class="vg-take">{text_html}</div>')


# --- Engine data (read-only) -------------------------------------------------
# cache_resource, not cache_data: the classes come from file-loaded modules
# that pickle cannot resolve, and these results are read-only.
@st.cache_resource
def load_all():
    vendors, results = day4.run_all()
    comms = {v.vendor_id: day5.build_comms(v, results[v.vendor_id]) for v in vendors}
    dash = day5.dashboard(vendors, results)
    # Registry of original vendors for live duplicate checks: the first
    # vendor with a given (name, tax ID) that passed checks 1+2.
    registry = {}
    for v in vendors:
        if all(c.passed for c in day3.run_checks_1_2(v)):
            registry.setdefault((day4.normalize_name(v.legal_name), v.tax_id), v)
    golden = day3.load_golden()
    return vendors, results, comms, dash, registry, golden


st.set_page_config(page_title="VendorGate", page_icon=":material/shield:", layout="wide")
st.html(POLISH_CSS)

vendors, results, comms, dash, base_registry, golden = load_all()
by_id = {v.vendor_id: v for v in vendors}
AS_OF = date.fromisoformat(dash["as_of"])

ss = st.session_state
ss.setdefault("runs", [])            # verification runs this session, newest first
ss.setdefault("live_registry", {})   # session applications that passed checks 1+2
ss.setdefault("pending_run", None)
ss.setdefault("record_id", None)

# Chart and logo colours are mid-tones that keep contrast on both the light
# and the dark background. st.context.theme.type is not used here because it
# can be stale on the first run and right after the viewer switches theme.
STATUS_COLOR = {"Approved": "#16a34a", "Pending": "#d97706", "Rejected": "#dc2626"}
ACCENT = "#3b82f6"
LOGO_SVG = ("data:image/svg+xml;utf8," + (
    "<svg xmlns='http://www.w3.org/2000/svg' width='168' height='32' viewBox='0 0 168 32'>"
    f"<path d='M14 3 4 7v8c0 6.6 4.3 12 10 14 5.7-2 10-7.4 10-14V7z' fill='{ACCENT}'/>"
    "<path d='m9.5 15.5 3 3 6-6.5' stroke='white' stroke-width='2.4' fill='none' "
    "stroke-linecap='round' stroke-linejoin='round'/>"
    "<text x='32' y='22' font-family='IBM Plex Sans,Segoe UI,sans-serif' font-size='17' "
    f"font-weight='700' fill='#64748b'>Vendor<tspan fill='{ACCENT}'>Gate</tspan></text></svg>"
).replace("#", "%23"))


# --- Shared helpers ----------------------------------------------------------
def records():
    """Every application: session runs first, then the imported register."""
    out = [{"id": r["run_id"], "vendor": r["vendor"], "decision": r["decision"],
            "comms": r["comms"], "channel": "Portal", "decided": r["when"]} for r in ss.runs]
    out += [{"id": v.vendor_id, "vendor": v, "decision": results[v.vendor_id],
             "comms": comms[v.vendor_id], "channel": "Import", "decided": v.submitted_on}
            for v in vendors]
    return out


def registry_for(v):
    """Registry of earlier vendors, excluding the application's own record."""
    reg = {**base_registry, **ss.live_registry}
    return {k: o for k, o in reg.items() if o.vendor_id != v.vendor_id}


def audit_metrics():
    """Decision quality against the audited reference cases."""
    expect = {"approve": "approved", "pending": "pending", "reject": "rejected"}
    agree = sum(results[i].status == expect[e["expected_status"]] for i, e in golden.items())
    must_not = [i for i, e in golden.items() if e["expected_status"] != "approve"]
    false_ok = sum(results[i].status == "approved" for i in must_not)
    return agree, len(golden), false_ok, len(must_not)


def status_text(d):
    return STATUS_LABEL[d.status] + (" · escalated" if d.fraud else "")


def open_record(rid):
    ss.record_id = rid
    st.switch_page(PAGE_RECORD)


def to_frame(recs):
    rows = []
    for r in recs:
        v, d = r["vendor"], r["decision"]
        try:
            age = (AS_OF - date.fromisoformat(v.submitted_on)).days
        except ValueError:
            age = None
        rows.append({
            "Application": r["id"], "Vendor": v.legal_name, "Country": v.country,
            "Status": [STATUS_LABEL[d.status]] + (["Escalated"] if d.fraud else []),
            "status_key": STATUS_LABEL[d.status], "escalated": d.fraud,
            "Risk": d.risk_score,
            "Primary issue": CAUSE_LABEL.get(next(iter(d.causes), ""), "None"),
            "Submitted": pd.to_datetime(v.submitted_on, errors="coerce"),
            "Age (days)": age, "Channel": r["channel"], "Contact": v.contact_email,
        })
    return pd.DataFrame(rows)


GRID = "rgba(148,163,184,0.18)"    # readable on both light and dark backgrounds
MUTED = "#94a3b8"
PLOT_CONFIG = {"displayModeBar": False, "displaylogo": False}


def style(fig, height=280, legend=False):
    """Shared Plotly look: transparent canvas, Plex font, quiet grid, no chrome."""
    fig.update_layout(
        height=height, margin=dict(l=8, r=16, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans, Segoe UI, sans-serif", size=12),
        showlegend=legend, bargap=0.35, hoverlabel=dict(font_size=12),
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5, title=None),
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, showline=False, tickfont_color=MUTED)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, showline=False)
    return fig


def show(fig, alt):
    st.plotly_chart(fig, theme="streamlit", config=PLOT_CONFIG, alt=alt)


@st.cache_resource
def _batch_funnel_flags():
    """Per imported vendor: which verification stages it cleared (no session state)."""
    return {v.vendor_id: funnel_flags(v, {k: o for k, o in base_registry.items()
                                          if o.vendor_id != v.vendor_id}) for v in vendors}


def funnel_flags(v, registry):
    c1 = day3.validate_fields(v)
    c2 = day3.validate_documents(v)
    c3 = day4.run_check_3(v, c1 + c2)
    orig, same_bank = day4.find_duplicate(v, registry)
    return [
        all(c.passed for c in c1),
        all(c.passed for c in c2),
        all(c.passed for c in c3),
        orig is None,
    ]


# --- Overview ---------------------------------------------------------------
def page_overview():
    page_header("Vendor Onboarding Control Center",
                f"Verification before payment · Applications as of {AS_OF:%d %b %Y}")
    recs = records()
    df = to_frame(recs)

    with st.container(border=True):
        st.html('<div class="vg-label">Scope · Status · Country · Issue</div>')
        with st.container(horizontal=True, vertical_alignment="bottom"):
            status_f = st.pills("Status", list(STATUS_LABEL.values()), selection_mode="multi")
            country_f = st.multiselect("Country", sorted(df["Country"].unique()), width=260,
                                       placeholder="All countries")
            issue_f = st.multiselect("Primary issue",
                                     sorted(i for i in df["Primary issue"].unique() if i != "None"),
                                     width=300, placeholder="All issues")
    view = df
    if status_f:
        view = view[view["status_key"].isin(status_f)]
    if country_f:
        view = view[view["Country"].isin(country_f)]
    if issue_f:
        view = view[view["Primary issue"].isin(issue_f)]

    total = len(view)
    counts = view["status_key"].value_counts().to_dict()
    if not total:
        st.info("No applications match these filters.", icon=":material/filter_alt_off:")
        return
    pct = lambda n: f"{100 * n / total:.0f}% of total"
    n_ok, n_pend, n_rej = (counts.get(s, 0) for s in ("Approved", "Pending", "Rejected"))
    n_esc = int(view["escalated"].sum())
    agree, n_aud, false_ok, must_not = audit_metrics()
    ids = set(view["Application"])
    cause_counts = pd.Series([c for r in recs if r["id"] in ids for c in r["decision"].causes]).value_counts()
    top_cause = CAUSE_LABEL.get(cause_counts.index[0], "") if len(cause_counts) else "None"
    kpi_ribbon([
        ("Applications", f"{total}", f"{len(ss.runs)} new this session",
         f"Across <strong>{view['Country'].nunique()}</strong> countries.", INFO),
        ("Approved", f"{n_ok}", pct(n_ok), "Cleared all five checks. Ready for payment set-up.", POS),
        ("Pending correction", f"{n_pend}", pct(n_pend),
         f"Most common blocker: <strong>{html_escape(top_cause)}</strong>.", WARN),
        ("Rejected", f"{n_rej}", f"{n_esc} escalated",
         "Risk signals or multiple failures. Routed to a reviewer.", NEG),
        ("False approvals", f"{false_ok} of {must_not}", "Audited cases",
         f"Policy agreement <strong>{agree} of {n_aud}</strong> on audited cases.",
         POS if false_ok == 0 else NEG),
    ])
    pend = view[view["status_key"] == "Pending"].dropna(subset=["Age (days)"])
    oldest = int(pend["Age (days)"].max()) if len(pend) else 0
    rate = 100 * n_ok / total
    signal_grid([
        ("Approval rate", "Healthy" if rate >= 40 else "Watch",
         f"{rate:.0f}% approved first time. The rest need a vendor fix or a review.",
         POS if rate >= 40 else WARN),
        ("Fraud exposure", "Contained" if n_esc else "Clear",
         f"{n_esc} application(s) with a risk signal held back from payment.", NEG if n_esc else POS),
        ("Correction backlog", "Watch" if oldest >= 3 else "On track",
         f"{n_pend} pending. Oldest has waited <strong>{oldest} days</strong> for the vendor.",
         WARN if oldest >= 3 else POS),
        ("Decision speed", "Fast", f"Median <strong>{dash['median_decision_ms']} ms</strong> for all "
                                    "five automated checks.", INFO),
    ])

    tab_pipe, tab_issues, tab_reg = st.tabs(["Pipeline", "Issues and ageing", "Application register"])
    with tab_pipe:
        pipeline_tab(view, recs, ids, counts, total)
    with tab_issues:
        issues_tab(view, cause_counts, pend)
    with tab_reg:
        register_tab(view)


def pipeline_tab(view, recs, ids, counts, total):
    left, right = st.columns([2, 3])
    with left.container(border=True, height="stretch"):
        panel_head("Status mix", "Share of applications")
        order = [s for s in STATUS_COLOR if counts.get(s)]
        donut = go.Figure(go.Pie(
            labels=order, values=[counts[s] for s in order], hole=0.7, sort=False,
            direction="clockwise", marker=dict(colors=[STATUS_COLOR[s] for s in order],
                                               line=dict(color="rgba(0,0,0,0)", width=0)),
            textinfo="none", hovertemplate="<b>%{label}</b><br>%{value} applications "
                                           "(%{percent})<extra></extra>"))
        donut.add_annotation(text=f"<b style='font-size:30px'>{total}</b><br>"
                                  f"<span style='color:{MUTED}'>applications</span>",
                             showarrow=False, font=dict(size=13))
        show(style(donut, 300, legend=True), "Donut chart of applications by status")
        takeaway(f"<strong>Read:</strong> {100 * counts.get('Approved', 0) / total:.0f}% approved. "
                 "Nothing ambiguous is auto-approved; it waits for the vendor instead.")
    with right.container(border=True, height="stretch"):
        panel_head("Verification funnel", "Checks 1 to 5 in order")
        batch = _batch_funnel_flags()
        flags = [batch[r["id"]] if r["channel"] == "Import" else funnel_flags(r["vendor"], registry_for(r["vendor"]))
                 for r in recs if r["id"] in ids]
        reached, alive = [len(flags)], [True] * len(flags)
        for i in range(4):
            alive = [a and f[i] for a, f in zip(alive, flags)]
            reached.append(sum(alive))
        reached.append(counts.get("Approved", 0))
        steps = ["Submitted", "Fields valid", "Documents verified", "Details consistent",
                 "No duplicate or risk signal", "Approved"]
        funnel = go.Figure(go.Funnel(
            y=steps, x=reached, textinfo="value+percent initial",
            textfont=dict(color="white", size=12),
            marker=dict(color=["#1e3a8a", "#1d4ed8", "#2563eb", "#3b82f6", "#60a5fa",
                               STATUS_COLOR["Approved"]]),
            connector=dict(fillcolor="rgba(59,130,246,0.12)", line=dict(width=0)),
            hovertemplate="<b>%{y}</b><br>%{x} applications<extra></extra>"))
        show(style(funnel, 300), "Funnel of applications passing each verification check")
        drops = [reached[i] - reached[i + 1] for i in range(len(steps) - 1)]
        worst = max(range(len(drops)), key=drops.__getitem__)
        takeaway(f"<strong>Biggest drop:</strong> {drops[worst]} application(s) stop at "
                 f"<strong>{steps[worst + 1].lower()}</strong>.")

    with st.container(border=True):
        panel_head("Status by country", "Applications per registration country")
        bc = view.groupby(["Country", "status_key"]).size().unstack(fill_value=0)
        bc = bc.loc[bc.sum(axis=1).sort_values().index]
        stack = go.Figure([
            go.Bar(name=s, y=bc.index, x=bc[s], orientation="h", marker=dict(color=STATUS_COLOR[s]),
                   hovertemplate=f"<b>%{{y}}</b><br>{s}: %{{x}}<extra></extra>")
            for s in STATUS_COLOR if s in bc.columns])
        stack.update_layout(barmode="stack", barcornerradius=6)
        show(style(stack, 60 + 46 * len(bc), legend=True), "Stacked bar chart of application status per country")


def issues_tab(view, cause_counts, pend):
    left, right = st.columns([3, 2])
    with left.container(border=True, height="stretch"):
        panel_head("Top issues", "All failed checks")
        if len(cause_counts):
            ic = cause_counts.rename(index=lambda c: CAUSE_LABEL.get(c, c)).sort_values()
            bar = go.Figure(go.Bar(
                x=ic.values, y=ic.index, orientation="h", text=ic.values, textposition="outside",
                cliponaxis=False, marker=dict(color=ic.values, colorscale=[[0, "#93c5fd"], [1, "#1d4ed8"]],
                                              cornerradius=6),
                hovertemplate="<b>%{y}</b><br>%{x} applications<extra></extra>"))
            bar.update_xaxes(showgrid=False, showticklabels=False)
            show(style(bar, 300), "Bar chart of issues found, most frequent first")
            takeaway(f"<strong>Fix first:</strong> {html_escape(ic.index[-1])} accounts for "
                     f"{100 * ic.iloc[-1] / ic.sum():.0f}% of all issues found.")
        else:
            st.caption("No open issues in this selection.")
    with right.container(border=True, height="stretch"):
        panel_head("Pending ageing", "Days waiting for the vendor")
        buckets = ["0–2 days", "3–5 days", "6+ days"]
        ag = pd.cut(pend["Age (days)"], bins=[-1, 2, 5, 10_000], labels=buckets).value_counts() \
            .reindex(buckets, fill_value=0)
        age = go.Figure(go.Bar(
            x=buckets, y=ag.values, text=ag.values, textposition="outside", cliponaxis=False,
            marker=dict(color=["#fcd34d", STATUS_COLOR["Pending"], "#b91c1c"], cornerradius=8),
            hovertemplate="<b>%{x}</b><br>%{y} applications<extra></extra>"))
        age.update_yaxes(showticklabels=False, showgrid=False)
        show(style(age, 270), "Bar chart of pending applications by days waiting")
        takeaway(f"<strong>Follow up:</strong> {int(ag.iloc[1:].sum())} pending application(s) "
                 "have waited 3 days or more.")


def register_tab(view):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.caption("Select a row to open the vendor record. Click a column header to sort.", width="stretch")
        st.download_button("Export CSV", view.drop(columns=["status_key", "escalated"])
                           .assign(Status=lambda x: x["Status"].str.join(" / ")).to_csv(index=False),
                           file_name=f"vendorgate-applications-{AS_OF:%Y%m%d}.csv", mime="text/csv",
                           icon=":material/download:")
    event = st.dataframe(
        view.sort_values(["Submitted", "Application"], ascending=[False, True]),
        hide_index=True, on_select="rerun", selection_mode="single-row", key="register",
        column_order=["Application", "Vendor", "Country", "Status", "Risk", "Primary issue",
                      "Submitted", "Age (days)", "Channel"],
        column_config={
            "Application": st.column_config.TextColumn(pinned=True),
            "Status": st.column_config.MultiselectColumn(
                options=["Approved", "Pending", "Rejected", "Escalated"],
                color=["green", "orange", "red", "red"]),
            "Risk": st.column_config.ProgressColumn(min_value=0, max_value=50, format="%d",
                                                    help="Risk score. 30 or more is rejected."),
            "Submitted": st.column_config.DateColumn(format="DD MMM YYYY"),
            "Age (days)": st.column_config.NumberColumn(format="%d"),
        },
    )
    rows = event.selection.rows
    if rows:
        open_record(view.sort_values(["Submitted", "Application"], ascending=[False, True])
                    .iloc[rows[0]]["Application"])


# --- New application ---------------------------------------------------------
FORM_FIELDS = ["legal_name", "bank_account_name", "tax_id", "bank_name",
               "ifsc_or_swift", "account_number", "contact_person", "contact_email", "submitted_on"]


def prefill():
    """Fill the form from an existing record (on_change callback)."""
    vid = ss.prefill
    if not vid:
        for f in FORM_FIELDS:
            ss[f"f_{f}"] = ""
        ss.f_submitted_on = date.today().isoformat()
        return
    v = by_id[vid]
    for f in FORM_FIELDS:
        ss[f"f_{f}"] = str(getattr(v, f))
    ss.f_country, ss.f_tax_id_type = v.country, v.tax_id_type
    present = {d.type: d for d in v.documents if d.status == "present"}
    wrong = [d.type for d in v.documents if d.type in day3.WRONG_DOC_TYPES]
    for doc in day3.REQUIRED_DOCS[v.country]:
        if doc in present:
            ss[f"doc_{doc}"], ss[f"exp_{doc}"] = "Received", present[doc].expiry or ""
        elif wrong:
            ss[f"doc_{doc}"], ss[f"exp_{doc}"] = WRONG_TO_CHOICE[wrong.pop(0)], ""
        else:
            ss[f"doc_{doc}"], ss[f"exp_{doc}"] = "Not received", ""


def page_application():
    page_header("New application", "Vendor details as submitted · Verification runs on submit")
    ss.setdefault("f_submitted_on", date.today().isoformat())
    st.selectbox("Start from an existing record", [""] + [v.vendor_id for v in vendors],
                 key="prefill", on_change=prefill, width=460,
                 format_func=lambda k: "Blank application" if not k else f"{k} · {by_id[k].legal_name}")
    a, b = st.columns(2)
    with a.container(border=True):
        st.subheader("Company", divider="gray")
        st.text_input("Legal entity name", key="f_legal_name")
        c1, c2 = st.columns(2)
        c1.selectbox("Country of registration", list(day3.COUNTRY_TAX_ID), key="f_country")
        c2.selectbox("Tax ID type", ["GSTIN", "PAN", "EIN", "VAT", "UEN"], key="f_tax_id_type")
        st.text_input("Tax ID", key="f_tax_id")
        st.subheader("Contact", divider="gray")
        c1, c2 = st.columns(2)
        c1.text_input("Contact name", key="f_contact_person")
        c2.text_input("Contact email", key="f_contact_email")
        st.text_input("Submission date", key="f_submitted_on", placeholder="YYYY-MM-DD")
    with b.container(border=True):
        st.subheader("Banking", divider="gray")
        st.text_input("Account holder name", key="f_bank_account_name")
        st.text_input("Bank name", key="f_bank_name")
        c1, c2 = st.columns(2)
        c1.text_input("IFSC or SWIFT/BIC", key="f_ifsc_or_swift")
        c2.text_input("Account number", key="f_account_number")
        st.subheader("Documents", divider="gray")
        for doc in day3.REQUIRED_DOCS[ss.f_country]:
            d1, d2 = st.columns([3, 2])
            d1.selectbox(day5.DOC_LABELS.get(doc, doc).capitalize(), list(DOC_CHOICES), key=f"doc_{doc}")
            d2.text_input("Valid until", key=f"exp_{doc}", placeholder="YYYY-MM-DD")
    if st.button("Submit and verify", type="primary", icon=":material/play_arrow:"):
        docs = []
        for doc in day3.REQUIRED_DOCS[ss.f_country]:
            choice = ss.get(f"doc_{doc}", "Received")
            if choice == "Received":
                docs.append(day3.Document(type=doc, status="present",
                                          expiry=(ss.get(f"exp_{doc}") or "").strip() or None))
            elif DOC_CHOICES[choice]:
                docs.append(day3.Document(type=DOC_CHOICES[choice], status="present"))
        ss.pending_run = day3.VendorSubmission(
            # Re-verifying an existing record keeps its ID so it is not
            # matched against itself as a duplicate.
            vendor_id=ss.prefill or f"APP-{datetime.now():%y%m%d}-{len(ss.runs) + 1:03d}",
            documents=docs, city="", country=ss.f_country, tax_id_type=ss.f_tax_id_type,
            **{f: (ss.get(f"f_{f}") or "").strip() for f in FORM_FIELDS},
        )
        st.switch_page(PAGE_RUN)


# --- Verification run ---------------------------------------------------------
def stage(i, checks=None, note=None, state="complete"):
    """Show one check stage: running, then its pass/fail lines."""
    title, icon = STAGES[i]
    box = st.status(f"{i + 1}. {title}", state="running", expanded=True)
    time.sleep(STAGE_DELAY_S)
    lines = [(c.passed, c.reason) for c in checks] if checks is not None else [note]
    with box:
        for ok, text in lines:
            st.markdown(f"{':material/check_circle:' if ok else ':material/cancel:'} "
                        f"{':green' if ok else ':red'}[**{'Pass' if ok else 'Fail'}**] {text}")
    failed = any(not ok for ok, _ in lines)
    box.update(label=f"{i + 1}. {title} · {'issues found' if failed else 'passed'}",
               state="error" if failed or state == "error" else "complete", expanded=failed)


def run_live(v):
    """Run checks 1 to 5 stage by stage against the engine."""
    t0 = time.perf_counter()
    c1 = day3.validate_fields(v)
    stage(0, c1)
    c2 = day3.validate_documents(v)
    stage(1, c2)
    c3 = day4.run_check_3(v, c1 + c2)
    stage(2, c3)
    dup = day4.find_duplicate(v, registry_for(v))
    orig, same_bank = dup
    if orig is None:
        stage(3, note=(True, "No existing vendor with this name and tax ID."))
    elif same_bank:
        stage(3, note=(True, f"Matches existing vendor {orig.vendor_id} with the same bank details. "
                             "Linked to the existing record."))
    else:
        stage(3, note=(False, f"Matches existing vendor {orig.vendor_id} with different bank details."))
    d = day4.decide(v, c1 + c2, c3, dup)
    causes = ", ".join(CAUSE_LABEL.get(c, c) for c in d.causes)
    stage(4, note=(d.status == "approved",
                   f"Risk score {d.risk_score}" + (f" from {causes}" if causes else "")
                   + (". Escalated to a reviewer." if d.fraud else ".")))
    engine_ms = (time.perf_counter() - t0 - 5 * STAGE_DELAY_S) * 1000

    if all(c.passed for c in c1 + c2):
        ss.live_registry.setdefault((day4.normalize_name(v.legal_name), v.tax_id), v)
    ss.runs.insert(0, {
        "run_id": v.vendor_id if v.vendor_id.startswith("APP-") else f"{v.vendor_id}-R{len(ss.runs) + 1}",
        "vendor": v, "decision": d, "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ms": round(max(engine_ms, 0), 2), "comms": day5.build_comms(v, d),
    })


def decision_block(v, d, comm, ms=None):
    reasons = "\n".join(f"- {r}" for r in d.reasons) or "- All five checks passed."
    title = f"{status_text(d)} · {v.legal_name} · risk score {d.risk_score}" + (f" · {ms} ms" if ms else "")
    alert = {"approved": st.success, "pending": st.warning, "rejected": st.error}[d.status]
    alert(reasons, title=title, icon=STATUS_ICON[d.status])
    with st.container(border=True):
        st.subheader("Vendor notification")
        st.caption(f"To {comm['to']} · {comm['subject']}")
        st.code(comm["body"], language=None, wrap_lines=True)
        st.caption("Internal note: " + comm["internal_note"])


def page_run():
    page_header("Verification run", "Five automated checks · Live stage view")
    if ss.pending_run is not None:
        v, ss.pending_run = ss.pending_run, None
        st.caption(f"Verifying {v.legal_name or 'unnamed vendor'} · {v.vendor_id}")
        run_live(v)
        r = ss.runs[0]
        decision_block(r["vendor"], r["decision"], r["comms"], r["ms"])
        return
    if not ss.runs:
        st.info("No verification has run in this session.", icon=":material/info:")
        if st.button("New application", icon=":material/add:"):
            st.switch_page(PAGE_APP)
        return
    r = ss.runs[0]
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"Latest run {r['run_id']} · {r['when']}", width="stretch")
        if st.button("Run again", icon=":material/replay:"):
            ss.pending_run = r["vendor"]
            st.rerun()
    decision_block(r["vendor"], r["decision"], r["comms"], r["ms"])


# --- Review queue -------------------------------------------------------------
def page_queue():
    page_header("Review queue", "Held for vendor correction or escalated to a reviewer")
    items = [r for r in records() if r["decision"].status != "approved"]
    with st.container(horizontal=True, vertical_alignment="bottom"):
        status = st.segmented_control("Status", ["All", "Pending", "Rejected", "Escalated"],
                                      default="All", required=True)
        causes = sorted({c for r in items for c in r["decision"].causes})
        cause = st.selectbox("Issue", ["All"] + causes, width=300,
                             format_func=lambda c: CAUSE_LABEL.get(c, c))
    shown = [r for r in items
             if (status == "All" or (status == "Escalated" and r["decision"].fraud)
                 or r["decision"].status == status.lower())
             and (cause == "All" or cause in r["decision"].causes)]
    st.caption(f"{len(shown)} application(s)")
    for r in shown:
        v, d = r["vendor"], r["decision"]
        with st.expander(f"{r['id']} · {v.legal_name} · {status_text(d)} · risk {d.risk_score}",
                         icon=STATUS_ICON[d.status]):
            st.markdown("\n".join(f"- {x}" for x in d.reasons))
            st.code(r["comms"]["body"], language=None, wrap_lines=True)
            if st.button("Open record", key=f"open_{r['id']}", icon=":material/open_in_new:"):
                open_record(r["id"])


# --- Vendor record ------------------------------------------------------------
def page_record():
    page_header("Vendor record", "Application details · Verification log · Notifications")
    recs = {r["id"]: r for r in records()}
    if ss.record_id not in recs:
        ss.record_id = next(iter(recs))
    rid = st.selectbox("Application", list(recs), index=list(recs).index(ss.record_id), width=520,
                       format_func=lambda i: f"{i} · {recs[i]['vendor'].legal_name} · "
                                             f"{status_text(recs[i]['decision'])}")
    ss.record_id = rid
    r = recs[rid]
    v, d = r["vendor"], r["decision"]
    docs = ", ".join(day5.DOC_LABELS.get(x.type, x.type) + (f" (valid until {x.expiry})" if x.expiry else "")
                     for x in v.documents) or "None received"
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.subheader(v.legal_name, width="stretch")
            st.badge(status_text(d), icon=STATUS_ICON[d.status],
                     color={"approved": "green", "pending": "orange", "rejected": "red"}[d.status])
        st.table(pd.DataFrame({"Field": ["Country", "Tax ID", "Account holder", "Bank", "Contact",
                                         "Documents", "Submitted"],
                               "Value": [v.country, f"{v.tax_id_type} {v.tax_id}", v.bank_account_name,
                                         f"{v.bank_name} · {v.ifsc_or_swift} · {v.account_number}",
                                         f"{v.contact_person} · {v.contact_email}", docs,
                                         f"{v.submitted_on} via {r['channel']}"]}).set_index("Field"),
                 alt="Vendor application details")
    with st.expander("Verification log", icon=":material/list_alt:"):
        _, log = day5.decide_single(v, registry_for(v))
        st.markdown("\n".join(
            f"- {':green' if c.passed else ':red'}[**{'Pass' if c.passed else 'Fail'}**] "
            f"`{c.name}` {c.reason}" for c in log))
    decision_block(v, d, r["comms"])

    fix = day5.SCRIPTED_RESUBMISSIONS.get(v.vendor_id)
    if not fix or r["channel"] != "Import":
        return
    st.subheader("Resubmission history")
    st.caption("The vendor replied with the correction requested in the notification.")
    if st.button("Apply vendor correction and re-verify", type="primary", icon=":material/sync:"):
        fixed = day5.apply_corrections(v, fix)
        d2, _ = day5.decide_single(fixed)
        field_label = {"country": "country", "ifsc_or_swift": "IFSC/SWIFT", "tax_id": "tax ID",
                       "tax_id_type": "tax ID type", "bank_account_name": "account holder"}
        change = ", ".join(f"attached {', '.join(day5.DOC_LABELS.get(x, x) for x in val)}" if k == "attach"
                           else f"{field_label.get(k, k.replace('_', ' '))} set to {val}"
                           for k, val in fix.items())
        r1, r2 = st.columns(2)
        for col, label, dd in ((r1, "Round 1 · original submission", d), (r2, f"Round 2 · {change}", d2)):
            with col.container(border=True, height="stretch"):
                st.caption(label)
                st.badge(f"{status_text(dd)} · risk {dd.risk_score}", icon=STATUS_ICON[dd.status],
                         color={"approved": "green", "pending": "orange", "rejected": "red"}[dd.status])
                st.markdown("\n".join(f"- {x}" for x in dd.reasons) or "- All five checks passed.")


# --- Navigation ---------------------------------------------------------------
PAGE_OVERVIEW = st.Page(page_overview, title="Overview", icon=":material/space_dashboard:",
                        url_path="overview", default=True)
PAGE_APP = st.Page(page_application, title="New application", icon=":material/add_business:",
                   url_path="new-application")
PAGE_RUN = st.Page(page_run, title="Verification run", icon=":material/play_circle:",
                   url_path="verification")
PAGE_QUEUE = st.Page(page_queue, title="Review queue", icon=":material/assignment_late:",
                     url_path="review-queue")
PAGE_RECORD = st.Page(page_record, title="Vendor record", icon=":material/badge:", url_path="vendor-record")

with st.sidebar:
    st.logo(LOGO_SVG, size="large")

nav = st.navigation({"Operations": [PAGE_OVERVIEW, PAGE_QUEUE],
                     "Onboarding": [PAGE_APP, PAGE_RUN, PAGE_RECORD]})

with st.sidebar:
    st.caption("Decisions follow the published verification policy. Ambiguous applications "
               "are held for correction; risk signals are escalated to a reviewer.")
    st.caption("Light or dark theme: app menu, top right.")

nav.run()
