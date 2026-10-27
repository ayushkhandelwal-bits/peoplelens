"""Shared period and segment filters.

The analytical period is chosen once in the sidebar; segment filters sit at
the top of each module. Both are stored in session state so they stay
consistent while moving between modules, and every chart and KPI on a page is
computed from the same :class:`Filters` object.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components import data
from src.analytics.common import Filters

SEGMENT_KEYS = ["flt_business_units", "flt_departments", "flt_locations", "flt_levels", "flt_employment_types"]


def month_options(lo: date, hi: date) -> list[date]:
    return [d.date() for d in pd.date_range(lo, hi, freq="ME")]


def _valid(p, opts) -> bool:
    return isinstance(p, (tuple, list)) and len(p) == 2 and p[0] in opts and p[1] in opts


def period_selector(v: str) -> tuple[date, date]:
    """Sidebar period widget. The chosen period is persisted in ``_period`` (not
    the widget key) so it survives page switches, when Streamlit discards widget state."""
    lo, hi = data.bounds(v)
    opts = month_options(lo, hi)
    if not _valid(st.session_state.get("_period"), opts):
        st.session_state._period = (opts[max(0, len(opts) - 12)], opts[-1])

    def _store():
        val = st.session_state.get("period_widget")
        if _valid(val, opts):
            st.session_state._period = tuple(val)

    # range mode is decided by passing a tuple as ``value`` (never preset the widget key)
    st.select_slider("Analytical period", options=opts, value=tuple(st.session_state._period),
                     key="period_widget", on_change=_store,
                     format_func=lambda d: d.strftime("%b %Y"),
                     help="Months are inclusive. KPIs are calculated over this period; point-in-time measures use "
                          "the last month.")
    return tuple(st.session_state._period)


def current_period(v: str) -> tuple[date, date]:
    lo, hi = data.bounds(v)
    opts = month_options(lo, hi)
    if not _valid(st.session_state.get("_period"), opts):
        st.session_state._period = (opts[max(0, len(opts) - 12)], opts[-1])
    return tuple(st.session_state._period)


def segment_bar(v: str, show: tuple[str, ...] = ("business_units", "departments", "locations", "levels",
                                                 "employment_types"), note: str | None = None) -> Filters:
    nm = data.names(v)
    start, end = current_period(v)
    labels = {"business_units": "Business unit", "departments": "Department", "locations": "Location",
              "levels": "Job level", "employment_types": "Employment type"}
    options = {
        "business_units": [b for b in nm["business_units"] if b != "Executive"] + ["Executive"],
        "departments": list(nm["departments"]),
        "locations": list(nm["locations"]),
        "levels": nm["levels"],
        "employment_types": nm["employment_types"],
    }
    fmt = {"departments": lambda k: nm["departments"].get(k, k), "locations": lambda k: nm["locations"].get(k, k)}
    active = sum(bool(st.session_state.get(f"flt_{k}")) for k in show)
    label = f"Filters · {active} active" if active else "Filters · whole organisation"
    with st.expander(label, expanded=False, icon=":material/filter_list:"):
        cols = st.columns(len(show))
        for col, key in zip(cols, show):
            opts = options[key]
            if key == "departments" and st.session_state.get("flt_business_units"):
                bus = st.session_state.flt_business_units
                opts = [d for d in opts if nm["dept_bu"].get(d) in bus]
            # keep only selections that are still valid options
            sk = f"flt_{key}"
            if sk in st.session_state:
                st.session_state[sk] = [x for x in st.session_state[sk] if x in opts]
            col.multiselect(labels[key], opts, key=sk, format_func=fmt.get(key, str), placeholder="All")
        c1, c2 = st.columns([1, 5])
        if c1.button("Reset filters", type="tertiary", icon=":material/restart_alt:"):
            for k in SEGMENT_KEYS:
                st.session_state[k] = []
            st.rerun()
        if note:
            c2.caption(note)
    return Filters(
        start=start, end=end,
        departments=tuple(st.session_state.get("flt_departments", []) if "departments" in show else ()),
        locations=tuple(st.session_state.get("flt_locations", []) if "locations" in show else ()),
        levels=tuple(st.session_state.get("flt_levels", []) if "levels" in show else ()),
        employment_types=tuple(st.session_state.get("flt_employment_types", []) if "employment_types" in show else ()),
        business_units=tuple(st.session_state.get("flt_business_units", []) if "business_units" in show else ()),
    )


def scope_text(f: Filters, v: str) -> str:
    nm = data.names(v)
    return f.describe({"departments": nm["departments"], "locations": nm["locations"]})
