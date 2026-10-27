"""PeopleLens — Workforce Intelligence & Planning (Streamlit entry point).

    streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="PeopleLens · Workforce Intelligence & Planning", page_icon=str(ROOT / "assets/logo.svg"),
                   layout="wide", initial_sidebar_state="expanded")
st.markdown(f"<style>{(ROOT / 'assets/styles.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

from components import data, sidebar  # noqa: E402
from views import (  # noqa: E402
    ask_peoplelens,
    data_governance,
    how_it_works,
    planning_business_insights,
    predictive_intelligence,
    talent_lifecycle,
    workforce_explorer,
)

data.bootstrap_if_needed()
if "role" not in st.session_state:
    st.session_state.role = "Analyst"
# Re-assign widget-backed state so it survives switching to pages where those widgets are not drawn.
for _k in ["role", "flt_business_units", "flt_departments", "flt_locations", "flt_levels", "flt_employment_types"]:
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]

pages = {
    "Modules": [
        st.Page(workforce_explorer.render, title="Workforce Explorer", icon=":material/groups:",
                url_path="workforce-explorer", default=True),
        st.Page(talent_lifecycle.render, title="Talent & Lifecycle", icon=":material/timeline:",
                url_path="talent-lifecycle"),
        st.Page(predictive_intelligence.render, title="Predictive Intelligence", icon=":material/insights:",
                url_path="predictive-intelligence"),
        st.Page(planning_business_insights.render, title="Planning & Business Insights",
                icon=":material/account_balance:", url_path="planning-business-insights"),
    ],
    "Workspace": [
        st.Page(ask_peoplelens.render, title="Ask PeopleLens", icon=":material/forum:", url_path="ask"),
        st.Page(data_governance.render, title="Data & Governance", icon=":material/verified_user:",
                url_path="data-governance"),
        st.Page(how_it_works.render, title="How It Works", icon=":material/account_tree:",
                url_path="how-it-works"),
    ],
}

nav = st.navigation(pages, position="hidden")
sidebar.brand()
sidebar.navigation(pages, nav)
v = data.data_version()
sidebar.controls(v)
sidebar.glance(v)
sidebar.status_block(v)
sidebar.about_block()
nav.run()
