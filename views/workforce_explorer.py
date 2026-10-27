"""Module 1 — Workforce Explorer: what the workforce looks like, how it is changing,
and direct (read-only) access to the data through curated and custom SQL."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import charts, data
from components.filters import scope_text, segment_bar
from components.kpi_cards import caption, fmt_int, fmt_pct, insight, metric, page_header
from components.sidebar import current_role
from components.tables import show
from database.connection import get_readonly_engine
from src.analytics import workforce
from src.analytics.common import safe_div
from src.analytics.sql_queries import LIBRARY
from src.governance.access_control import can, require, run_custom_sql


def render() -> None:
    v = data.data_version()
    page_header("Module 1", "Workforce Explorer",
                "What does the workforce look like, where is it changing, and is it tracking the plan? "
                "Start here, then use the SQL Explorer to inspect the underlying data.")
    f = segment_bar(v)
    k = data.kpis(f, v)
    if not k.has_data:
        st.warning("No workforce records match these filters.")
        return
    kp = data.kpis(f.previous(), v)
    lo, _ = data.bounds(v)
    has_prev = f.previous().start >= lo

    cols = st.columns(6)
    metric(cols[0], "Headcount", fmt_int(k.active_headcount),
           f"{k.active_headcount - k.opening_headcount:+,}", key="active_headcount",
           help_extra=f"Change since {f.previous().end:%b %Y}.")
    metric(cols[1], "Avg headcount", fmt_int(k.average_headcount),
           f"{safe_div(k.average_headcount - kp.average_headcount, kp.average_headcount):+.1%}"
           if has_prev and kp.has_data else None, key="average_headcount", help_extra="Change vs the prior period.")
    metric(cols[2], "Net growth", fmt_pct(k.net_growth, signed=True), f"{k.joiners - k.exits:+,} net",
           key="net_growth", help_extra=f"{k.joiners:,} joiners and {k.exits:,} exits in the period.")
    metric(cols[3], "Attrition rate", fmt_pct(k.attrition_rate),
           f"{(k.attrition_rate - kp.attrition_rate) * 100:+.1f} pp" if has_prev and kp.attrition_rate
           else None, key="attrition_rate", inverse=True,
           help_extra=f"Annualised. Voluntary {fmt_pct(k.voluntary_attrition_rate)} · involuntary "
                      f"{fmt_pct(k.involuntary_attrition_rate)}. Delta vs the prior period.")
    metric(cols[4], "Avg tenure", f"{(k.average_tenure_months or 0) / 12:.1f} yrs", None, key="average_tenure")
    if k.plan_variance is not None:
        metric(cols[5], "vs plan", f"{k.plan_variance:+,}", fmt_pct(k.plan_variance_pct, signed=True),
               key="headcount_vs_plan")
    else:
        metric(cols[5], "vs plan", "n/a", None, key="headcount_vs_plan",
               help_extra="Plans are set by department; clear location/level/employment filters to compare.")
    caption(f"Scope: {scope_text(f, v)}. Hover over a KPI for its definition and formula.")

    _story(f, k, kp, has_prev, v)

    tabs = st.tabs(["Trends", "Composition", "Structure & movement", "SQL Explorer", "Records"])
    with tabs[0]:
        _trends(f, v)
    with tabs[1]:
        _composition(f, v)
    with tabs[2]:
        _structure(f, v)
    with tabs[3]:
        _sql_explorer(v)
    with tabs[4]:
        _records(f, v)


def _story(f, k, kp, has_prev, v) -> None:
    att = data.attrition_by("department", f, v) if not f.departments or len(f.departments) > 1 else None
    msgs = []
    direction = "grew" if (k.net_growth or 0) >= 0 else "shrank"
    msgs.append(f"The workforce **{direction} {abs(k.net_growth or 0):.1%}** over {f.months} months "
                f"({k.joiners:,} joiners vs {k.exits:,} exits).")
    if has_prev and kp.attrition_rate and k.attrition_rate:
        d = (k.attrition_rate - kp.attrition_rate) * 100
        msgs.append(f"Annualised attrition is **{k.attrition_rate:.1%}**, {abs(d):.1f} pp "
                    f"{'higher' if d > 0 else 'lower'} than the previous {f.months} months.")
    if att is not None and len(att) > 1:
        big = att[att.avg_headcount >= 30]
        if len(big):
            top = big.iloc[0]
            msgs.append(f"Highest attrition: **{top.segment}** at {top.attrition_rate:.1%} "
                        f"(95% CI {top.ci_low:.1%}–{top.ci_high:.1%}).")
    if k.plan_variance is not None:
        msgs.append(f"Headcount is **{abs(k.plan_variance):,} {'below' if k.plan_variance < 0 else 'above'}** plan at "
                    f"{f.end:%b %Y}.")
    insight(" ".join(msgs), "warn" if (k.plan_variance or 0) < 0 else "")


def _trends(f, v) -> None:
    hp = data.hc_vs_plan(f, v)
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        a = hp.dropna(subset=["headcount"])
        fig.add_trace(go.Scatter(x=a.month, y=a.headcount, name="Actual", mode="lines+markers",
                                 line=dict(color=charts.NAVY, width=2.4), marker=dict(size=5)))
        if hp.planned_headcount.notna().any() and not f.is_segmented:
            fig.add_trace(go.Scatter(x=hp.month, y=hp.planned_headcount, name="Plan (AOP)", mode="lines",
                                     line=dict(color=charts.TEAL, width=2, dash="dot")))
        st.plotly_chart(charts.finish(fig, "Monthly headcount vs approved plan", 340), config=charts.CONFIG)
        caption("Month-end active headcount. The dotted line includes the forward plan months beyond the data."
                if not f.is_segmented else "Plan comparison hidden: plans exist only at department level.")
    with c2:
        s = data.series(f, v)
        fig = charts.grouped_bar(s, "month", ["joiners", "exits"], ["Joiners", "Exits"], "Joiners vs exits",
                                 colors=[charts.TEAL, charts.SLATE], height=340)
        st.plotly_chart(fig, config=charts.CONFIG)
    s = data.series(f, v)
    s["opening"] = s.headcount.shift(1)
    s["monthly_attrition"] = s.exits / s[["opening", "headcount"]].mean(axis=1)
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.line(s, "month", ["monthly_attrition"], ["Exits / average headcount"],
                                    "Monthly attrition (not annualised)", pct=True, height=280), config=charts.CONFIG)
        caption("Exits peak around the April–May appraisal cycle — compare like months when reading short periods.")
    with c2:
        st.plotly_chart(charts.line(s, "month", ["voluntary_exits", "involuntary_exits"], ["Voluntary", "Involuntary"],
                                    "Exits by type", colors=[charts.AMBER, charts.SLATE], height=280),
                        config=charts.CONFIG)


def _composition(f, v) -> None:
    c1, c2 = st.columns([3, 2])
    with c1:
        d = data.composition(f, "department", v)
        st.plotly_chart(charts.bar(d, "segment", "headcount", f"Active headcount by department · {f.end:%b %Y}",
                                   horizontal=True, height=max(320, 22 * len(d)), text_fmt=","), config=charts.CONFIG)
    with c2:
        m = data.loc_level(f, v)
        if len(m):
            st.plotly_chart(charts.heatmap(m, "Location × job level", height=330), config=charts.CONFIG)
        t = data.tenure(f, v)
        st.plotly_chart(charts.histogram(t.tenure_months / 12, "Tenure distribution (years)", nbins=40, height=260,
                                         xlabel="Years of service"), config=charts.CONFIG)
        if len(t):
            caption(f"Median tenure {t.tenure_months.median() / 12:.1f} years; "
                    f"{(t.tenure_months < 12).mean():.0%} have under one year of service.")
    c = st.columns(4)
    for col, (by, title) in zip(c, [("business_unit", "Business unit"), ("job_family", "Job family"),
                                    ("employment_type", "Employment type"), ("gender", "Gender (composition only)")]):
        d = data.composition(f, by, v)
        with col:
            st.plotly_chart(charts.bar(d.head(8), "segment", "share", title, horizontal=True, pct=True, height=260),
                            config=charts.CONFIG)
    caption("Gender is reported for workforce composition only; it is never used as a model feature.")


def _structure(f, v) -> None:
    sp = data.span(f, v)
    c1, c2 = st.columns([3, 2])
    with c1:
        if len(sp):
            st.plotly_chart(charts.histogram(sp.direct_reports, "Span of control (direct reports per manager)",
                                             nbins=int(sp.direct_reports.max()), height=320, xlabel="Direct reports"),
                            config=charts.CONFIG)
    with c2:
        if len(sp):
            by_lvl = sp.groupby("manager_level").direct_reports.agg(["count", "median", "mean", "max"]).reset_index()
            by_lvl.columns = ["Manager level", "Managers", "Median span", "Mean span", "Max span"]
            st.markdown("**Span by manager level**")
            show(by_lvl, {"Mean span": "float1"})
            wide, narrow = (sp.direct_reports >= 12).sum(), (sp.direct_reports <= 2).sum()
            insight(f"**{len(sp):,}** managers; median span **{sp.direct_reports.median():.0f}**. "
                    f"{wide} managers have 12+ direct reports (coaching capacity risk) and {narrow} have 1–2 "
                    "(possible layering).", "warn" if wide > 0.02 * len(sp) else "")
    mv = data.movements(f, v)
    cols = [c for c in ["Promotion", "Transfer", "Manager Change"] if c in mv]
    if cols:
        st.plotly_chart(charts.grouped_bar(mv, "month", cols, cols, "Workforce movements per month",
                                           colors=[charts.TEAL, charts.NAVY, charts.LIGHT], height=300,
                                           barmode="stack"), config=charts.CONFIG)
        caption("Promotions concentrate in the April cycle; manager changes include reassignments after a "
                "manager leaves and reorganisations.")


def _sql_explorer(v) -> None:
    role = current_role()
    st.markdown("#### Curated query library")
    caption("Every query is parameterised and runs on a read-only connection. Interpretations are computed from "
            "the returned rows.")
    lo, hi = data.bounds(v)
    nm = data.names(v)
    pick = st.selectbox("Query", LIBRARY, format_func=lambda q: q.name, key="sql_lib")
    st.markdown(f"**Business question:** {pick.question}")
    c = st.columns(3)
    values = {}
    months = [d.date() for d in pd.date_range(lo, hi, freq="ME")]
    if "month" in pick.params:
        values["month"] = c[0].selectbox("Month end", months[::-1], format_func=lambda d: d.strftime("%b %Y"),
                                         key="sql_month")
    if "start" in pick.params:
        values["start"] = c[0].selectbox("From", months, index=max(0, len(months) - 12),
                                         format_func=lambda d: d.strftime("%b %Y"), key="sql_start")
        values["start"] = values["start"].replace(day=1)
        values["end"] = c[1].selectbox("To", months[::-1], format_func=lambda d: d.strftime("%b %Y"), key="sql_end")
    if "department" in pick.params:
        dept = c[2].selectbox("Department", ["All departments"] + sorted(nm["departments"].values()), key="sql_dept")
        values["department"] = None if dept == "All departments" else dept
    if role == "Viewer":
        st.info("The Viewer role can see the library SQL, but queries on base tables run only for Analysts. "
                "Viewers can query the approved aggregate views below.")
    st.code(pick.render(), language="sql")
    if role != "Viewer":
        try:
            df = pick.run(values, ro_engine=get_readonly_engine("analyst"))
            ch = pick.chart
            if ch and len(df):
                if ch["type"] == "histogram":
                    fig = charts.histogram(df[ch["x"]], None, nbins=int(df[ch["x"]].max()), height=280)
                elif ch["type"] == "line":
                    d = df.copy()
                    d[ch["x"]] = pd.to_datetime(d[ch["x"]])
                    fig = charts.line(d, ch["x"], ch["y"], height=280)
                else:
                    d = df.groupby(ch["x"], as_index=False)[ch["y"]].sum() if ch.get("color") else df
                    fig = charts.bar(d, ch["x"], ch["y"], horizontal=len(d) > 8, height=max(280, 22 * len(d))
                                     if len(d) > 8 else 280, pct=ch["y"] in ("join_yield", "annualised_attrition"))
                st.plotly_chart(fig, config=charts.CONFIG, key=f"sqlfig_{pick.key}")
            insight(pick.interpret(df))
            show(df, download=f"sql_{pick.key}")
        except Exception as exc:
            st.error(f"Query failed: {exc}")

    st.markdown("#### Custom read-only SQL")
    if role == "Viewer":
        caption("Viewer role: the database connection can read only the aggregate views "
                "v_headcount_monthly, v_plan_vs_actual, v_recruitment_funnel and v_engagement_department.")
        default = "SELECT department_name, SUM(headcount_eom) AS headcount\nFROM v_headcount_monthly\n" \
                  f"WHERE snapshot_date = '{hi.isoformat()}'\nGROUP BY department_name\nORDER BY headcount DESC"
    else:
        caption("Analyst role: SELECT/WITH only, one statement, results capped at 5,000 rows, 10-second limit. "
                "Writes are blocked by the database connection itself, not just by this form.")
        default = ("SELECT job_level, COUNT(*) AS employees\nFROM employees\nWHERE current_status = 'Active'\n"
                   "GROUP BY job_level\nORDER BY job_level")
    sql = st.text_area("SQL", default, height=150, key=f"custom_sql_{role}")
    if st.button("Run query", type="primary", icon=":material/play_arrow:", key="run_custom"):
        try:
            out = run_custom_sql(sql, role)
            st.success(f"{len(out):,} rows")
            show(out, download="custom_query")
        except PermissionError as exc:
            st.error(f"Permission denied — {exc}")
        except (ValueError, TimeoutError) as exc:
            st.warning(str(exc))
    with st.expander("Tables and columns", icon=":material/table_view:"):
        from database.schema import VIEWER_VIEWS, metadata

        names = list(VIEWER_VIEWS) if role == "Viewer" else [t.name for t in metadata.sorted_tables]
        if role == "Viewer":
            st.write(", ".join(names))
        else:
            rows = [dict(table=t.name, columns=", ".join(c.name for c in t.columns)) for t in metadata.sorted_tables
                    if not t.name.startswith("stg_")]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _records(f, v) -> None:
    role = current_role()
    try:
        require(role, "view_row_level")
    except PermissionError as exc:
        st.info(f"{exc}. Switch to the Analyst role in the sidebar to see synthetic row-level records.")
        return
    caption("Synthetic employee IDs only — names, pay and ratings are not shown. Limited to 500 rows; use the SQL "
            "Explorer for anything else.")
    df = workforce.employee_records(f)
    show(df, height=420, download="employee_records" if can(role, "export_data") else None)

