"""Sidebar: product mark, analytical period, role, quick question, data status, about, credit."""

from __future__ import annotations

import streamlit as st

from components import data
from components.filters import period_selector
from src.governance.access_control import PERMISSION_LABELS, ROLES

MARK = ("<svg width='22' height='22' viewBox='0 0 64 64' aria-hidden='true'>"
        "<circle cx='17' cy='29' r='5' fill='#fff'/><rect x='11' y='37' width='12' height='15' rx='4' fill='#fff'/>"
        "<circle cx='32' cy='21' r='5.5' fill='#fff'/><rect x='25.5' y='29.5' width='13' height='22.5' rx='4' fill='#fff'/>"
        "<circle cx='47' cy='13' r='6' fill='#CCFBF1'/><rect x='40' y='22' width='14' height='30' rx='4' fill='#CCFBF1'/>"
        "</svg>")

AUTHOR = {
    "tagline": "MBA, Business Analytics · BITS Pilani",
    "linkedin": "https://www.linkedin.com/in/ayushjhanginiya/",
    "email": "ayushkhandelwal.connect@gmail.com",
}
ICON_LINKEDIN = ("<svg width='18' height='18' viewBox='0 0 24 24' fill='currentColor'><path d='M20.45 20.45h-3.56v-5.57c0"
                 "-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85"
                 " 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.56"
                 "V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73"
                 "V1.73C24 .77 23.2 0 22.22 0z'/></svg>")
ICON_MAIL = ("<svg width='19' height='19' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8' "
             "stroke-linecap='round' stroke-linejoin='round'><rect x='3' y='5' width='18' height='14' rx='2'/>"
             "<path d='m3 7 9 6 9-6'/></svg>")

ROLE_NOTES = {
    "Analyst": "<b>Analyst:</b> full read-only access — synthetic employee records, individual risk scores and SQL on "
               "all tables.",
    "Viewer": "<b>Viewer:</b> totals and charts only. Hidden: employee records (Workforce Explorer → Records), "
              "individual risk scores, CSV exports; SQL is limited to summary views by the database itself.",
}


def brand() -> None:
    st.sidebar.markdown(
        f"<div class='pl-brand'><div class='pl-brand-mark'>{MARK}</div><div class='pl-brand-name'>PeopleLens</div>"
        "</div><div class='pl-brand-sub'>Workforce Intelligence &amp; Planning</div>", unsafe_allow_html=True)


def navigation(pages: dict, current=None) -> None:
    sb = st.sidebar
    for section, items in pages.items():
        if section != "Modules":
            sb.markdown("<div class='pl-eyebrow' style='margin:8px 0 0 4px'>Workspace</div>", unsafe_allow_html=True)
        for page in items:
            if current is not None and page.url_path == current.url_path:
                with sb.container(key="pl_nav_active"):
                    st.page_link(page, label=page.title, icon=page.icon)
            else:
                sb.page_link(page, label=page.title, icon=page.icon)
    sb.divider()


def controls(v: str) -> None:
    sb = st.sidebar
    with sb:
        period_selector(v)
        st.segmented_control("Access role", list(ROLES), key="role",
                                    help="Demonstrates role-based access. Viewer sees aggregates only and its SQL "
                                         "runs on a database connection limited to aggregate views.")
        role = st.session_state.get("role") or "Analyst"
        st.markdown(f"<div class='pl-role-note'>{ROLE_NOTES[role]}</div>", unsafe_allow_html=True)


def glance(v: str) -> None:
    """Headline numbers for the selected period (whole organisation), visible from every page."""
    from components.kpi_cards import fmt_pct
    from src.analytics.common import Filters

    start, end = st.session_state.get("_period") or data.bounds(v)
    try:
        k = data.kpis(Filters(start, end), v)
    except Exception:
        return
    if not k.has_data:
        return
    var = k.plan_variance
    var_txt = "—" if var is None else f"{var:+,}"
    var_cls = "" if var is None else ("neg" if var < 0 else "pos")
    rows = [
        ("Headcount", f"{k.active_headcount:,}", f"at {end:%b %Y}", ""),
        ("Attrition", fmt_pct(k.attrition_rate), "annualised", ""),
        ("Vs plan", var_txt, "" if k.plan_variance_pct is None else fmt_pct(k.plan_variance_pct, signed=True), var_cls),
    ]
    cells = "".join(f"<div class='pl-glance-row'><span class='pl-glance-l'>{lbl}<small>{sub}</small></span>"
                    f"<span class='pl-glance-v {cls}'>{val}</span></div>" for lbl, val, sub, cls in rows)
    st.sidebar.markdown(f"<div class='pl-glance'><div class='pl-glance-h'>At a glance · {start:%b %y} – {end:%b %y}"
                        f"</div>{cells}</div>", unsafe_allow_html=True)


def status_block(v: str) -> None:
    s = data.status(v)
    sb = st.sidebar
    sb.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    with sb.expander("Data status", expanded=False, icon=":material/database:"):
        if not s.get("connected"):
            st.markdown(f"<div class='pl-status'><span class='pl-dot' style='background:#B91C1C'></span>"
                        f"Database not reachable<br>{s.get('error', '')[:120]}</div>", unsafe_allow_html=True)
            return
        dq = s.get("dq", {})
        ok = dq.get("FAIL", 0) == 0
        st.markdown(
            "<div class='pl-status'>"
            f"<span class='pl-dot' style='background:{'#15803D' if ok else '#B91C1C'}'></span>"
            f"<b>{s['backend'].capitalize()}</b> connected<br>"
            f"Employees: <b>{s.get('employees', 0):,}</b> · Snapshots: <b>{s.get('snapshots', 0):,}</b><br>"
            f"Candidates: <b>{s.get('candidates', 0):,}</b><br>"
            f"Source rows: <b>{s.get('total_rows', 0):,}</b><br>"
            f"Quality checks: <b>{dq.get('PASS', 0)}</b> passed · <b>{dq.get('WARN', 0)}</b> warn · "
            f"<b>{dq.get('FAIL', 0)}</b> failed<br>"
            f"Last refresh: <b>{s.get('last_refresh', '—')}</b></div>", unsafe_allow_html=True)


def about_block() -> None:
    sb = st.sidebar
    with sb.expander("About", expanded=False, icon=":material/info:"):
        st.markdown(
            "<div class='pl-status'>PeopleLens is an independently developed workforce analytics product. "
            "It integrates people data into one relational model, explains lifecycle patterns, forecasts "
            "workforce needs and quantifies their cost.<br><br>"
            "<b>All employees, amounts and results are simulated.</b> The organisation is fictional and no real "
            "personal data is used.</div>", unsafe_allow_html=True)
        role = current_role()
        st.caption(f"{role} permissions: " + "; ".join(PERMISSION_LABELS[p] for p in sorted(ROLES[role])))
    sb.markdown(
        "<div class='pl-credit'>Built by <b>Ayush Khandelwal</b><br>"
        f"<span class='pl-notice'>{AUTHOR['tagline']}</span>"
        "<div class='pl-social'>"
        f"<a href='{AUTHOR['linkedin']}' target='_blank' rel='noopener' title='LinkedIn' "
        f"aria-label='LinkedIn profile'>{ICON_LINKEDIN}</a>"
        f"<a href='mailto:{AUTHOR['email']}' title='{AUTHOR['email']}' aria-label='Email'>{ICON_MAIL}</a>"
        "</div></div>", unsafe_allow_html=True)


def current_role() -> str:
    return st.session_state.get("role") or "Analyst"
