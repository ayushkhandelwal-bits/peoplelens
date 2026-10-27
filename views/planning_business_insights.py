"""Module 4 — Planning & Business Insights: plan vs actual, manpower cost,
turnover cost, scenario comparison, management recommendations and the
exportable executive report."""

from __future__ import annotations

from dataclasses import replace

import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import charts, data
from components.filters import segment_bar
from components.kpi_cards import caption, fmt_inr, fmt_int, fmt_pct, insight, metric, page_header
from components.tables import show
from src.analytics.compensation import sensitivity, turnover_summary


def render() -> None:
    v = data.data_version()
    page_header("Module 4", "Planning & Business Insights",
                "So what, and what next? Translate workforce movements into capacity gaps, cost and the estimated "
                "price of turnover — then compare scenarios and act on evidence-based recommendations.")
    f = segment_bar(v, show=("business_units", "departments"),
                    note="Plans and budgets are set by department, so only organisational filters apply here.")
    tabs = st.tabs(["Plan vs actual", "Manpower cost", "Turnover cost", "Scenario planning", "Recommendations",
                    "Executive report"])
    with tabs[0]:
        _plan(f, v)
    with tabs[1]:
        _cost(f, v)
    with tabs[2]:
        _turnover(f, v)
    with tabs[3]:
        _scenarios(f, v)
    with tabs[4]:
        _recommendations(f, v)
    with tabs[5]:
        _report(v)


def _plan(f, v) -> None:
    k = data.kpis(f, v)
    fc = data.model_meta("workforce_forecast.json", v)
    pva = data.plan_by_dept(f, f.end, v)
    c = st.columns(5)
    metric(c[0], f"Actual · {f.end:%b %Y}", fmt_int(k.active_headcount), key="active_headcount")
    metric(c[1], "Planned", fmt_int(k.planned_headcount), key="headcount_vs_plan")
    metric(c[2], "Gap vs plan", f"{k.plan_variance:+,}" if k.plan_variance is not None else "—",
           fmt_pct(k.plan_variance_pct, signed=True), key="headcount_vs_plan")
    if fc:
        d = pd.DataFrame(fc["department_forecast"])
        if f.departments:
            d = d[d.department_id.isin(f.departments)]
        elif f.business_units:
            nm = data.names(v)
            d = d[d.department_id.map(nm["dept_bu"]).isin(f.business_units)]
        t = d.groupby("month")[["forecast_headcount", "planned_headcount", "expected_exits"]].sum().reset_index()
        q = t.iloc[min(2, len(t) - 1)]
        metric(c[3], f"Forecast gap · {pd.Timestamp(q.month):%b %Y}", f"{q.forecast_headcount - q.planned_headcount:+,.0f}",
               fmt_pct((q.forecast_headcount - q.planned_headcount) / q.planned_headcount, signed=True))
        metric(c[4], "Expected exits (6m)", fmt_int(t.expected_exits.sum()), key="exits")
    pva = pva[pva.department_name != "Executive Office"].copy()
    worst_abs = pva.sort_values("variance").iloc[0]
    worst_pct = pva.sort_values("variance_pct").iloc[0]
    insight(f"Largest absolute shortfall: **{worst_abs.department_name}** ({worst_abs.variance:+.0f} heads, "
            f"{worst_abs.variance_pct:+.1%}). Largest percentage shortfall: **{worst_pct.department_name}** "
            f"({worst_pct.variance_pct:+.1%}, {worst_pct.variance:+.0f} heads). Read both: a small percentage in a "
            "large team can mean more missing capacity than a large percentage in a small one.", "warn")
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.bar(pva.sort_values("variance"), "department_name", "variance",
                                   f"Headcount vs plan (heads) · {f.end:%b %Y}", horizontal=True,
                                   height=max(340, 22 * len(pva)), text_fmt="+.0f"), config=charts.CONFIG)
    with c2:
        st.plotly_chart(charts.bar(pva.sort_values("variance_pct"), "department_name", "variance_pct",
                                   "Headcount vs plan (%)", horizontal=True, pct=True,
                                   height=max(340, 22 * len(pva))), config=charts.CONFIG)
    if fc:
        hd = pd.DataFrame(fc["hiring_demand"])
        if f.departments:
            hd = hd[hd.department_id.isin(f.departments)]
        st.markdown("**Forward view: forecast, expected exits and hiring required (next 6 months)**")
        show(hd[["department_name", "current_headcount", "forecast_headcount_end", "planned_headcount_end",
                 "expected_exits", "hires_required", "covered_by_pipeline", "additional_requisitions"]]
             .sort_values("additional_requisitions", ascending=False),
             {c_: "int" for c_ in ["current_headcount", "forecast_headcount_end", "planned_headcount_end",
                                   "expected_exits", "hires_required", "covered_by_pipeline",
                                   "additional_requisitions"]}, download="plan_vs_forecast")
        caption("Vacancy gap = plan − forecast. Hiring required = plan at horizon end − current headcount + expected "
                "exits; covered by pipeline = accepted offers × join rate + 80% of open requisitions.")


def _cost(f, v) -> None:
    cp = data.cost_plan(f, v)
    act, plan = cp.workforce_cost.sum(), cp.planned_cost.sum()
    sp = data.spend(f, v)
    k = data.kpis(f, v)
    c = st.columns(5)
    metric(c[0], "Workforce cost", fmt_inr(act), key="workforce_cost")
    metric(c[1], "Planned cost", fmt_inr(plan), key="budget_variance")
    metric(c[2], "Variance", fmt_inr(act - plan, signed=True), fmt_pct((act - plan) / plan, signed=True) if plan else None,
           key="budget_variance", inverse=True)
    metric(c[3], "Cost per employee", fmt_inr(k.cost_per_employee), key="cost_per_employee",
           help_extra="Annualised.")
    metric(c[4], "Recruitment + training", fmt_inr(sp["recruitment_cost"] + sp["training_cost"]),
           f"cost per hire {fmt_inr(sp['cost_per_hire'])}", key="cost_per_hire")
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cp.month, y=cp.workforce_cost, name="Actual", marker_color=charts.TEAL))
        fig.add_trace(go.Scatter(x=cp.month, y=cp.planned_cost, name="Plan", line=dict(color=charts.NAVY, dash="dot")))
        st.plotly_chart(charts.finish(fig, "Monthly people cost: actual vs plan (₹)", 330), config=charts.CONFIG)
    with c2:
        bv = data.budget_variance(f, v)
        st.plotly_chart(charts.bar(bv.sort_values("variance_pct"), "department_name", "variance_pct",
                                   "Cost variance vs plan by department", horizontal=True, pct=True,
                                   height=max(330, 20 * len(bv))), config=charts.CONFIG)
    over = data.budget_variance(f, v).iloc[0]
    insight(f"Cost is {fmt_pct((act - plan) / plan, signed=True)} against plan for the period. Largest overspend: "
            f"**{over.department_name}** ({fmt_inr(over.variance, signed=True)}, {over.variance_pct:+.1%}). "
            "Departments behind on headcount usually underspend — an underspend is often unfilled capacity, not "
            "efficiency.")
    c1, c2 = st.columns(2)
    with c1:
        lv = data.cost_breakdown(f, "job_level", v)
        st.plotly_chart(charts.bar(lv.assign(segment=lv.segment.astype(str)), "segment", "cost_per_employee",
                                   "Annual cost per employee by level (₹)", height=300), config=charts.CONFIG)
    with c2:
        st_ = data.salary_trend(f, v)
        st_ = st_[st_.job_level.isin(["L1", "L2", "L3", "L4", "L5"])]
        w = st_.pivot(index="month", columns="job_level", values="avg_base_salary").reset_index()
        cols = [c_ for c_ in ["L1", "L2", "L3", "L4", "L5"] if c_ in w]
        st.plotly_chart(charts.line(w, "month", cols, cols, "Average base salary by level (₹, active staff)",
                                    height=300), config=charts.CONFIG)
    caption("Workforce cost = base salary + variable pay at target + benefits, pro-rated by days employed. Salary "
            "steps each April reflect merit increases; mix changes (new joiners are paid near market) also move the "
            "averages.")
    show(data.cost_breakdown(f, "department", v)[["segment", "avg_headcount", "workforce_cost", "cost_per_employee",
                                                  "share"]],
         {"avg_headcount": "int", "workforce_cost": "inr", "cost_per_employee": "inr", "share": "pct"},
         download="cost_by_department", rename={"segment": "Department"})


def _turnover(f, v) -> None:
    base = data.exit_cost_base(f, v)
    d = data.turnover_defaults(f, v)
    st.markdown("Estimate what voluntary exits cost to replace. Defaults use **observed** cost per hire and vacancy "
                "duration from this dataset; the rest are documented assumptions you can change.")
    c = st.columns(6)
    a = replace(
        d,
        cost_per_hire=c[0].number_input("Cost per hire (₹)", 0, 1_000_000, int(d.cost_per_hire), 5_000, key="tc_cph"),
        onboarding_cost=c[1].number_input("Onboarding (₹)", 0, 500_000, int(d.onboarding_cost), 5_000, key="tc_onb"),
        ramp_months=c[2].slider("Ramp-up (months)", 0.0, 12.0, float(d.ramp_months), 0.5, key="tc_ramp"),
        ramp_productivity_loss=c[3].slider("Output lost in ramp", 0.0, 1.0, float(d.ramp_productivity_loss), 0.05,
                                           key="tc_loss"),
        vacancy_days=c[4].slider("Vacancy (days)", 0.0, 240.0, float(round(d.vacancy_days)), 5.0, key="tc_vac"),
        vacancy_coverage_gap=c[5].slider("Uncovered while vacant", 0.0, 1.0, float(d.vacancy_coverage_gap), 0.05,
                                         key="tc_gap"),
    )
    a = replace(a, include_involuntary=st.toggle("Include involuntary exits", value=False, key="tc_inv"))
    s = turnover_summary(base, a)
    k = st.columns(4)
    metric(k[0], "Exits costed", fmt_int(s["exits"]), key="exits")
    metric(k[1], "Estimated turnover cost", fmt_inr(s["total"]), key="turnover_cost")
    metric(k[2], "Per exit", fmt_inr(s["per_exit"]), key="turnover_cost")
    metric(k[3], "Annualised", fmt_inr(s["total"] * 12 / f.months), key="turnover_cost")
    c1, c2 = st.columns([2, 3])
    with c1:
        comp = pd.DataFrame({"component": list(s["components"]), "cost": list(s["components"].values())})
        comp["component"] = comp.component.str.replace("_", "-").str.capitalize()
        st.plotly_chart(charts.bar(comp, "component", "cost", "Cost components (₹)", height=300), config=charts.CONFIG)
    with c2:
        sens = sensitivity(base, a)
        sens["assumption"] = sens.assumption.map({
            "cost_per_hire": "Cost per hire", "onboarding_cost": "Onboarding cost", "ramp_months": "Ramp-up months",
            "ramp_productivity_loss": "Output lost in ramp", "vacancy_days": "Vacancy days",
            "vacancy_coverage_gap": "Uncovered while vacant"})
        st.plotly_chart(charts.tornado(sens, "assumption", "low_total", "high_total", s["total"],
                                       "Sensitivity: total cost when each assumption moves (others fixed)", 300),
                        config=charts.CONFIG)
    top = sens.iloc[-1]
    insight(f"The estimate ranges from **{fmt_inr(sens.low_total.min())}** to **{fmt_inr(sens.high_total.max())}** "
            f"across single-assumption changes; it is most sensitive to **{top.assumption.lower()}**. "
            "Treat it as an order-of-magnitude business case for retention, not an accounting loss.", "warn")
    show(s["by_department"], {"exits": "int", "total_cost": "inr", "cost_per_exit": "inr"},
         download="turnover_cost_by_department", rename={"department_name": "Department"})


def _scenarios(f, v) -> None:
    from src.models.scenarios import PRESETS, compare, with_horizon

    inp = data.scenario_inputs(v)
    h = st.select_slider("Horizon (months)", [3, 6], value=6, key="ps_h",
                         help="The approved plan extends six months beyond the data.")
    cmp = compare(with_horizon(PRESETS, h), inp)
    cols = ["scenario", "end_headcount", "gap_vs_plan", "total_hires", "total_exits", "workforce_cost",
            "turnover_cost", "end_headcount_vs_baseline", "workforce_cost_vs_baseline", "turnover_cost_vs_baseline"]
    show(cmp[cols], {"end_headcount": "int", "gap_vs_plan": "int", "total_hires": "int", "total_exits": "int",
                     "workforce_cost": "inr", "turnover_cost": "inr", "end_headcount_vs_baseline": "int",
                     "workforce_cost_vs_baseline": "inr", "turnover_cost_vs_baseline": "inr"},
         download="scenario_comparison")
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.bar(cmp, "scenario", "gap_vs_plan", f"Headcount gap vs plan after {h} months",
                                   horizontal=True, height=300, text_fmt="+,.0f"), config=charts.CONFIG)
    with c2:
        st.plotly_chart(charts.bar(cmp.iloc[1:], "scenario", "turnover_cost_vs_baseline",
                                   "Turnover cost vs baseline (₹)", horizontal=True, height=300), config=charts.CONFIG)
    hi = cmp[cmp.scenario.str.startswith("Higher")].iloc[0]
    imp = cmp[cmp.scenario.str.startswith("Improved")].iloc[0]
    insight(f"Over {h} months, two percentage points of attrition move end headcount by about "
            f"**{abs(hi.end_headcount_vs_baseline):,.0f}** heads and turnover cost by "
            f"**{fmt_inr(hi.turnover_cost_vs_baseline)}**; improving retention by the same amount saves about "
            f"{fmt_inr(-imp.turnover_cost_vs_baseline)}. Deterministic calculation on stated assumptions — see "
            "Predictive Intelligence → Scenario explorer to build a custom case.")


def _recommendations(f, v) -> None:
    from src.analytics.recommendations import THRESHOLDS

    recs = data.recommendations(f, v)
    if not recs:
        st.success("No rule threshold was met for this scope and period — nothing requires escalation.")
    for r in recs:
        rows = "".join(f"<div class='row'><span class='lbl'>{lbl}</span>{html.escape(str(r[key]))}</div>" for lbl, key in [
            ("Observation", "observation"), ("Evidence", "metric"), ("Segment", "segment"), ("Impact", "impact"),
            ("Action", "action"), ("Limitation", "limitation")])
        st.markdown(f"<div class='pl-rec'><h4>{html.escape(r['title'])}</h4>{rows}"
                    f"<div class='row' style='color:#64748B;font-size:0.78rem;margin-top:6px'>Rule: "
                    f"{html.escape(r['threshold'])}"
                    "</div></div>", unsafe_allow_html=True)
    with st.expander("All recommendation rules and thresholds"):
        for k, t in THRESHOLDS.items():
            st.markdown(f"- **{k.replace('_', ' ').capitalize()}** — {t}")


def _report(v) -> None:
    from src.reporting.monthly_report import build_report

    lo, hi = data.bounds(v)
    months = [d.date() for d in pd.date_range(lo, hi, freq="ME")][::-1]
    m = st.selectbox("Reporting month", months, format_func=lambda d: d.strftime("%B %Y"), key="rep_month")
    if st.button("Generate executive report", type="primary", icon=":material/description:"):
        with st.spinner("Calculating report from the database…"):
            st.session_state.report = build_report(m.strftime("%Y-%m"))
    rep = st.session_state.get("report")
    if rep is None:
        caption("The report is built from the database at the moment you click — headline KPIs, hiring and exits, "
                "attrition and retention, recruitment, plan vs actual, cost, significant changes, forecast, actions, "
                "data-quality status and definitions.")
        return
    c = st.columns(3)
    stem = f"peoplelens_report_{pd.Timestamp(rep.period):%Y_%m}"
    c[0].download_button("Download HTML", rep.to_html(), f"{stem}.html", "text/html", icon=":material/download:")
    c[1].download_button("Download Markdown", rep.to_markdown(), f"{stem}.md", "text/markdown",
                         icon=":material/download:")
    c[2].download_button("Download metrics CSV", pd.DataFrame(rep.metrics_rows).to_csv(index=False), f"{stem}.csv",
                         "text/csv", icon=":material/download:")
    with st.container(border=True):
        st.markdown(rep.to_markdown())
