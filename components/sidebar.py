"""Sidebar: product mark, analytical period, role, quick question, data status, about, credit."""

from __future__ import annotations

import streamlit as st

from components import data
from components.filters import period_selector
from src.governance.access_control import PERMISSION_LABELS, ROLES

MARK = ("<svg width='18' height='18' viewBox='0 0 64 64'><circle cx='28' cy='28' r='13' fill='none' "
        "stroke='#5EEAD4' stroke-width='6'/><line x1='38' y1='38' x2='50' y2='50' stroke='#5EEAD4' "
        "stroke-width='7' stroke-linecap='round'/></svg>")


def brand() -> None:
    st.sidebar.markdown(
        f"<div class='pl-brand'><div class='pl-brand-mark'>{MARK}</div><div class='pl-brand-name'>PeopleLens</div>"
        "</div><div class='pl-brand-sub'>Workforce Intelligence &amp; Planning</div>", unsafe_allow_html=True)


def navigation(pages: dict) -> None:
    sb = st.sidebar
    for section, items in pages.items():
        if section != "Modules":
            sb.markdown("<div class='pl-eyebrow' style='margin:8px 0 0 4px'>Workspace</div>", unsafe_allow_html=True)
        for page in items:
            sb.page_link(page, label=page.title, icon=page.icon)
    sb.divider()


def controls(v: str) -> None:
    sb = st.sidebar
    with sb:
        period_selector(v)
        st.segmented_control("Access role", list(ROLES), key="role",
                                    help="Demonstrates role-based access. Viewer sees aggregates only and its SQL "
                                         "runs on a database connection limited to aggregate views.")
        with st.form("ask_sidebar", clear_on_submit=True, border=False):
            q = st.text_input("Ask PeopleLens", placeholder="e.g. attrition by department last quarter",
                              label_visibility="visible")
            if st.form_submit_button("Ask", type="secondary", icon=":material/arrow_forward:") and q.strip():
                st.session_state.ask_question = q.strip()
                st.switch_page(st.session_state["_ask_page"])


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
    sb.markdown("<div class='pl-credit'>Built by <b>Ayush Khandelwal</b><br>"
                "<span class='pl-notice'>Synthetic data · open-source stack</span></div>", unsafe_allow_html=True)


def current_role() -> str:
    return st.session_state.get("role") or "Analyst"
