"""Module 3 — Predictive Intelligence: validated forecasts, attrition risk,
time-to-fill estimates, transparent what-if scenarios and model monitoring."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import charts, data
from components.kpi_cards import caption, fmt_inr, fmt_int, fmt_pct, insight, metric, page_header
from components.sidebar import current_role
from components.tables import show
from src.governance.access_control import can


def _models_or_train(v: str) -> tuple[dict, dict, dict] | None:
    fc = data.model_meta("workforce_forecast.json", v)
    att = data.model_meta("attrition_model.json", v)
    ttf = data.model_meta("hiring_forecast.json", v)
    if fc and att and ttf:
        return fc, att, ttf
    st.warning("Models have not been trained yet. Training is deliberate (never on page refresh).")
    if st.button("Train and evaluate models now", type="primary", icon=":material/model_training:"):
        from src.models.training import train_all

        with st.spinner("Training forecasting, time-to-fill and attrition models…"):
            train_all()
        st.cache_data.clear()
        st.rerun()
    return None


def render() -> None:
    v = data.data_version()
    page_header("Module 3", "Predictive Intelligence",
                "What is likely to happen next, and how sure are we? Forecasts and risk models are validated on "
                "held-out time periods and compared with simple baselines before they are shown.")
    m = _models_or_train(v)
    if m is None:
        return
    fc, att, ttf = m
    tabs = st.tabs(["Workforce forecast", "Attrition risk", "Hiring & time to fill", "Scenario explorer",
                    "Model monitoring"])
    with tabs[0]:
        _forecast(fc)
    with tabs[1]:
        _attrition(att, v)
    with tabs[2]:
        _ttf(ttf, fc)
    with tabs[3]:
        _scenarios(v)
    with tabs[4]:
        _monitoring(fc, att, ttf)


def _forecast(fc: dict) -> None:
    hist = pd.DataFrame(fc["history"]).assign(month=lambda d: pd.to_datetime(d.month))
    f = pd.DataFrame(fc["forecast"]).assign(month=lambda d: pd.to_datetime(d.month))
    comp = pd.DataFrame(fc["model_comparison"])
    best = comp.iloc[0]
    naive = comp[comp.model == "Naive"].iloc[0]
    c = st.columns(4)
    metric(c[0], f"Forecast · {f.month.iloc[-1]:%b %Y}", fmt_int(f.forecast.iloc[-1]),
           f"{f.forecast.iloc[-1] - hist.headcount.iloc[-1]:+,.0f} vs today")
    metric(c[1], "80% interval", f"{f.lower_80.iloc[-1]:,.0f}–{f.upper_80.iloc[-1]:,.0f}")
    metric(c[2], "Selected model", fc["selected_model"])
    metric(c[3], "Backtest MAE", f"{best.mae:,.0f} heads", f"{best.mae - naive.mae:+,.0f} vs naive", inverse=True,
           help_extra="Mean absolute error over rolling-origin 6-month backtests.")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist.month, y=hist.headcount, name="Actual", line=dict(color=charts.NAVY, width=2.4)))
    charts.band(fig, f.month, f.lower_80, f.upper_80)
    join = pd.DataFrame({"month": [hist.month.iloc[-1]], "forecast": [hist.headcount.iloc[-1]]})
    fj = pd.concat([join, f[["month", "forecast"]]])
    fig.add_trace(go.Scatter(x=fj.month, y=fj.forecast, name=f"Forecast ({fc['selected_model']})",
                             line=dict(color=charts.TEAL, width=2.4, dash="dash")))
    for name in [c_.split("::")[1] for c_ in f.columns if c_.startswith("model::")]:
        if name != fc["selected_model"]:
            fig.add_trace(go.Scatter(x=f.month, y=f[f"model::{name}"], name=name, visible="legendonly",
                                     line=dict(width=1.4, dash="dot")))
    st.plotly_chart(charts.finish(fig, "Organisation headcount: history and 6-month forecast", 360),
                    config=charts.CONFIG)
    caption("Other candidate models can be switched on in the legend. Intervals come from out-of-sample backtest "
            "errors by horizon.")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**Model comparison (rolling-origin backtests, 6-month horizon)**")
        show(comp, {"mae": "float1", "rmse": "float1", "mape": "pct"},
             rename={"mae": "MAE (heads)", "rmse": "RMSE", "mape": "MAPE"})
        better = (naive.mae - best.mae) / naive.mae
        insight(f"The **{fc['selected_model']}** reduces forecast error by **{better:.0%}** versus carrying the last "
                f"value forward, because it uses the known offer pipeline and seasonal exit pattern instead of "
                "extrapolating the trend.")
    with c2:
        st.markdown("**Expected flows**")
        show(f[["month", "expected_exits", "expected_hires", "forecast"]].assign(month=f.month.dt.strftime("%b %Y")),
             {"expected_exits": "int", "expected_hires": "int", "forecast": "int"})
        ex = pd.DataFrame(fc["exits_comparison"])
        caption("Exit forecast error (MAE, exits per month): " + ", ".join(
            f"{r.model.replace('Exits: ', '')} {r.mae:.1f}" for r in ex.itertuples()) + ".")
    d = pd.DataFrame(fc["department_forecast"])
    last = d[(d.month == d.month.max()) & (d.department_name != "Executive Office")].dropna(
        subset=["planned_headcount"]).copy()
    last["gap_pct"] = last.gap_vs_plan / last.planned_headcount
    last = last.sort_values("gap_vs_plan")
    st.plotly_chart(charts.bar(last, "department_name", "gap_vs_plan",
                               f"Forecast gap vs plan by department · {pd.Timestamp(last.month.iloc[0]):%b %Y}",
                               horizontal=True, height=max(320, 22 * len(last)), text_fmt="+.0f",
                               highlight=last[last.gap_pct <= -0.03].department_name.tolist()), config=charts.CONFIG)
    caption("Amber = forecast at least 3% below plan. Department forecasts apply the organisation's seasonal exit "
            "pattern scaled to each department's trailing attrition and its own offer pipeline.")


def _attrition(att: dict, v: str) -> None:
    primary = att["primary_model"]
    res = att["results"][primary]
    other = [k for k in att["results"] if k != primary][0]
    c = st.columns(5)
    metric(c[0], "ROC-AUC (hold-out)", f"{res['roc_auc']:.3f}",
           f"{res['roc_auc'] - att['results'][other]['roc_auc']:+.3f} vs {other.replace('_', ' ')}",
           help_extra="0.5 = random ordering, 1.0 = perfect.")
    metric(c[1], "PR-AUC", f"{res['pr_auc']:.3f}", f"base rate {res['base_rate']:.1%}",
           help_extra="Average precision; compare with the base rate of leavers.")
    metric(c[2], "Precision", fmt_pct(res["precision"]), help_extra=f"At threshold {res['threshold']:.3f}.")
    metric(c[3], "Recall", fmt_pct(res["recall"]))
    metric(c[4], "Top-decile lift", f"{res['top_decile_lift']:.2f}×",
           help_extra="Leaver rate in the highest-risk 10% ÷ overall leaver rate.")
    insight(f"Trained on {att['train_rows']:,} employee-quarters ({att['train_dates'][0]} to {att['train_dates'][-1]}), "
            f"tested on the {att['test_rows']:,} employees active on **{att['test_date']}** and their actual voluntary "
            f"exits over the next 6 months. The model ranks risk better than chance (AUC {res['roc_auc']:.2f}) but is "
            f"far from certain: at the chosen threshold it flags {res['confusion']['tp'] + res['confusion']['fp']:,} "
            f"people to find {res['confusion']['tp']:,} of {res['confusion']['tp'] + res['confusion']['fn']:,} leavers. "
            "That is useful for prioritising team-level conversations, not for judging individuals.", "warn")
    c1, c2, c3 = st.columns(3)
    with c1:
        fig = go.Figure()
        for k, (name, r) in enumerate(att["results"].items()):
            fig.add_trace(go.Scatter(x=r["roc_curve"]["fpr"], y=r["roc_curve"]["tpr"], name=name.replace("_", " "),
                                     line=dict(color=[charts.TEAL, charts.SLATE][k], width=2)))
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], name="Chance", line=dict(color=charts.LIGHT, dash="dot")))
        fig.update_xaxes(title="False positive rate")
        fig.update_yaxes(title="True positive rate")
        st.plotly_chart(charts.finish(fig, "ROC curve (hold-out)", 320), config=charts.CONFIG)
    with c2:
        fig = go.Figure()
        for k, (name, r) in enumerate(att["results"].items()):
            fig.add_trace(go.Scatter(x=r["pr_curve"]["recall"], y=r["pr_curve"]["precision"],
                                     name=name.replace("_", " "), line=dict(color=[charts.TEAL, charts.SLATE][k])))
        fig.add_hline(y=res["base_rate"], line=dict(color=charts.LIGHT, dash="dot"))
        fig.update_xaxes(title="Recall")
        fig.update_yaxes(title="Precision")
        st.plotly_chart(charts.finish(fig, "Precision-recall (dotted = base rate)", 320), config=charts.CONFIG)
    with c3:
        cal = pd.DataFrame(res["calibration"])
        fig = go.Figure(go.Scatter(x=cal.predicted, y=cal.observed, mode="lines+markers",
                                   line=dict(color=charts.TEAL), name="Model"))
        mx = float(max(cal.predicted.max(), cal.observed.max())) * 1.1
        fig.add_trace(go.Scatter(x=[0, mx], y=[0, mx], line=dict(color=charts.LIGHT, dash="dot"), name="Perfect"))
        fig.update_xaxes(title="Predicted probability", tickformat=".0%")
        fig.update_yaxes(tickformat=".0%", title="Observed rate")
        st.plotly_chart(charts.finish(fig, f"Calibration by decile · Brier {res['brier']:.3f}", 320),
                        config=charts.CONFIG)
    c1, c2 = st.columns([2, 3])
    with c1:
        cm = res["confusion"]
        z = pd.DataFrame([[cm["tn"], cm["fp"]], [cm["fn"], cm["tp"]]], index=["Stayed", "Left"],
                         columns=["Predicted stay", "Predicted leave"])
        st.plotly_chart(charts.heatmap(z, "Confusion matrix (hold-out)", height=260), config=charts.CONFIG)
    with c2:
        co = pd.DataFrame(att["coefficients"])
        co = co[co.feature.str.startswith("num__")].copy()
        co = co.reindex(co.coefficient.abs().sort_values(ascending=False).index).head(10).sort_values("coefficient")
        fig = go.Figure(go.Bar(x=co.coefficient, y=co.label, orientation="h",
                               marker_color=np.where(co.coefficient > 0, charts.AMBER, charts.TEAL)))
        st.plotly_chart(charts.finish(fig, "Logistic regression: standardised coefficients", 300),
                        config=charts.CONFIG)
        caption("Amber = associated with higher risk, teal = lower risk, per one standard deviation. Associations, "
                "not causes; location is included as a test variable and shows little effect.")
    st.markdown("#### Current risk distribution (score date " + att["score_date"] + ")")
    sc = data.risk_scores(v)
    if sc is not None:
        nm = data.names(v)
        sc["department"] = sc.department_id.map(nm["departments"])
        by = sc.groupby("department").agg(employees=("risk", "size"), mean_risk=("risk", "mean"),
                                          high_risk_share=("risk_band", lambda s: (s == "High").mean()))
        by["expected_leavers_6m"] = sc.groupby("department").risk.sum()
        by = by.sort_values("expected_leavers_6m", ascending=False).reset_index()
        c1, c2 = st.columns([3, 2])
        with c1:
            st.plotly_chart(charts.bar(by.head(15), "department", "expected_leavers_6m",
                                       "Expected voluntary leavers in the next 6 months (sum of probabilities)",
                                       horizontal=True, height=380, text_fmt=".0f"), config=charts.CONFIG)
        with c2:
            show(by, {"employees": "int", "mean_risk": "pct", "high_risk_share": "pct", "expected_leavers_6m": "float1"},
                 height=380, download="attrition_risk_by_department")
        role = current_role()
        with st.expander("Employee-level scores (synthetic, Analyst only)", icon=":material/lock:"):
            if not can(role, "view_risk_scores"):
                st.info("The Viewer role sees aggregate risk only.")
            else:
                st.warning("Synthetic IDs only. Scores are not to be used for disciplinary, termination or other "
                           "employment decisions; they indicate where a supportive conversation may help.")
                show(sc.sort_values("risk", ascending=False)[["employee_id", "department", "tenure_months",
                                                             "level_num", "risk", "risk_band"]].head(100),
                     {"risk": "pct", "tenure_months": "int"}, height=300)


def _ttf(ttf: dict, fc: dict) -> None:
    s = pd.DataFrame(ttf["km_summary"])
    curves = pd.DataFrame(ttf["km_curves"])
    ev = ttf["evaluation"]
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        for k, (fam, g) in enumerate(curves.groupby("job_family")):
            fig.add_trace(go.Scatter(x=g.days, y=g.share_unfilled, name=fam, line_shape="hv",
                                     line=dict(width=1.8, color=charts.PALETTE[k % len(charts.PALETTE)])))
        fig.update_xaxes(title="Days since requisition opened", range=[0, 240])
        st.plotly_chart(charts.finish(fig, "Share of requisitions still unfilled (Kaplan-Meier)", 380, pct_y=True),
                        config=charts.CONFIG)
    with c2:
        show(s[["job_family", "requisitions", "censored", "km_median_days", "km_p80_days", "naive_median_days"]],
             {"requisitions": "int", "censored": "int", "km_median_days": "int", "km_p80_days": "int",
              "naive_median_days": "int"}, height=380,
             rename={"km_median_days": "P50 days (KM)", "km_p80_days": "P80 days (KM)",
                     "naive_median_days": "Median, filled only"})
    caption("Open and cancelled requisitions are treated as censored observations. Plan requisitions P80 days (plus "
            "the notice period) before the date the person is needed.")
    if ev.get("available"):
        r = pd.DataFrame(ev["results"])
        base = r[r.model.str.startswith("Global")].mae.iloc[0]
        bestm = r.sort_values("mae").iloc[0]
        st.markdown(f"**Point-prediction hold-out** — trained on {ev['train_n']:,} requisitions opened before "
                    f"{ev['train_window_end']}, tested on {ev['test_n']:,} opened {ev['test_window']}.")
        show(r, {"mae": "float1", "rmse": "float1", "mape": "pct", "bias": "float1", "n": "int"})
        insight(("Segment-level information does **not** beat the global median here "
                 f"(best MAE {bestm.mae:.1f} vs baseline {base:.1f} days): in this dataset time-to-fill is driven "
                 "mostly by candidate-level chance, so the planning tool uses distributions (P50/P80) rather than "
                 "a point model." if bestm.mae >= base - 1 else
                 f"**{bestm.model}** improves on the global median (MAE {bestm.mae:.1f} vs {base:.1f} days)."),
                "warn" if bestm.mae >= base - 1 else "")
    st.markdown("#### Hiring demand to reach plan (next 6 months)")
    hd = pd.DataFrame(fc["hiring_demand"])
    show(hd[["department_name", "current_headcount", "planned_headcount_end", "expected_exits", "hires_required",
             "planned_hires", "pipeline_joiners", "open_requisitions", "additional_requisitions"]]
         .sort_values("additional_requisitions", ascending=False),
         {"current_headcount": "int", "planned_headcount_end": "int", "expected_exits": "int", "hires_required": "int",
          "planned_hires": "int", "pipeline_joiners": "int", "open_requisitions": "int",
          "additional_requisitions": "int"}, download="hiring_demand")
    over = hd[hd.demand_exceeds_plan]
    insight(f"Hires required = plan headcount at the horizon − current headcount + expected exits. In **{len(over)}** "
            f"departments this exceeds the hires the plan budgeted ({over.hires_required.sum():,.0f} needed vs "
            f"{over.planned_hires.sum():,.0f} planned) — usually because attrition is running above the plan's "
            "assumption.")


def _scenarios(v: str) -> None:
    from src.models.scenarios import Scenario, run, summarise

    inp = data.scenario_inputs(v)
    base = inp["base"]
    st.markdown("Adjust assumptions and see the deterministic projection recalculate. This is arithmetic on stated "
                "assumptions — **not** a trained prediction.")
    c = st.columns(5)
    att = c[0].slider("Attrition change (pp / year)", -5.0, 5.0, 0.0, 0.5, key="sc_att")
    hire = c[1].slider("Hiring pace change (%)", -50, 50, 0, 5, key="sc_hire")
    delay = c[2].slider("Extra hiring delay (months)", 0, 3, 0, key="sc_delay")
    comp = c[3].slider("Compensation increase (%)", 0.0, 15.0, 0.0, 0.5, key="sc_comp")
    horizon = c[4].select_slider("Horizon (months)", [3, 6, 9, 12], value=6, key="sc_h")
    extra_dept = st.selectbox("Additional headcount target for a department", ["None"] + base.department_name.tolist(),
                              key="sc_dept")
    extra_n = st.number_input("Additional heads", 0, 500, 0, 5, key="sc_extra") if extra_dept != "None" else 0
    extra = {base[base.department_name == extra_dept].department_id.iloc[0]: extra_n} if extra_n else {}
    custom = Scenario("Custom", att, hire, delay, comp, extra, horizon)
    b_df = run(Scenario("Baseline", horizon_months=horizon), inp)
    c_df = run(custom, inp)
    b, s = summarise(b_df), summarise(c_df)
    k = st.columns(5)
    metric(k[0], "End headcount", fmt_int(s["end_headcount"]), f"{s['end_headcount'] - b['end_headcount']:+,.0f} vs baseline")
    metric(k[1], "Hires", fmt_int(s["total_hires"]), f"{s['total_hires'] - b['total_hires']:+,.0f}")
    metric(k[2], "Exits", fmt_int(s["total_exits"]), f"{s['total_exits'] - b['total_exits']:+,.0f}", inverse=True)
    metric(k[3], "Workforce cost", fmt_inr(s["workforce_cost"]),
           fmt_inr(s["workforce_cost"] - b["workforce_cost"], signed=True), inverse=True)
    metric(k[4], "Turnover cost", fmt_inr(s["turnover_cost"]),
           fmt_inr(s["turnover_cost"] - b["turnover_cost"], signed=True), inverse=True)
    g = pd.concat([b_df, c_df]).groupby(["scenario", "month"]).headcount.sum().reset_index()
    w = g.pivot(index="month", columns="scenario", values="headcount").reset_index()
    plan = b_df.groupby("month").planned_headcount.sum(min_count=1).reset_index()
    w = w.merge(plan, on="month", how="left")
    ys = ["Baseline", "Custom"] + (["planned_headcount"] if w.planned_headcount.notna().any() else [])
    st.plotly_chart(charts.line(w, "month", ys, ["Baseline", "Your scenario", "Plan (AOP)"][:len(ys)],
                                "Projected headcount", colors=[charts.SLATE, charts.TEAL, charts.NAVY],
                                dash=["solid", "solid", "dot"], height=320), config=charts.CONFIG)
    with st.expander("Assumptions used", icon=":material/tune:"):
        show(base[["department_name", "headcount", "annual_exit_rate", "monthly_hires", "cost_per_head"]],
             {"headcount": "int", "annual_exit_rate": "pct", "monthly_hires": "float1", "cost_per_head": "inr"})
        caption(f"Replacement cost per exit: {fmt_inr(inp['cost_per_exit'])} (turnover-cost model defaults). Plans "
                "exist only for 6 forward months; longer horizons show no plan line beyond that.")


def _monitoring(fc: dict, att: dict, ttf: dict) -> None:
    rows = [
        dict(model="Workforce forecast", version=fc["model_version"], trained=fc["trained_at"],
             data=f"{fc['history_months']} months to {fc['history_end']}",
             validation=f"Rolling origins {', '.join(fc['backtest_origins'])}",
             metric=f"MAE {pd.DataFrame(fc['model_comparison']).iloc[0].mae:,.0f} heads ({fc['selected_model']})"),
        dict(model="Attrition risk (6 months)", version=att["model_version"], trained=att["trained_at"],
             data=f"{att['train_rows']:,} train / {att['test_rows']:,} test rows",
             validation=f"Temporal hold-out at {att['test_date']}",
             metric=f"ROC-AUC {att['results'][att['primary_model']]['roc_auc']:.3f} ({att['primary_model'].replace('_', ' ')})"),
        dict(model="Time to fill", version=ttf["model_version"], trained=ttf["trained_at"],
             data=f"{ttf['requisitions']:,} requisitions", validation=ttf["evaluation"].get("test_window", "—"),
             metric=", ".join(f"{r['model'].split(' (')[0]} MAE {r['mae']:.0f}d" for r in ttf["evaluation"].get("results", []))),
    ]
    show(pd.DataFrame(rows))
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Assumptions**")
        for a in fc["assumptions"] + att["assumptions"] + ttf["assumptions"]:
            st.markdown(f"- {a}")
    with c2:
        st.markdown("**Known limitations**")
        for a in fc["limitations"] + att["limitations"] + ttf["limitations"]:
            st.markdown(f"- {a}")
    caption("Models are retrained deliberately with `python scripts/train_models.py` (or the button shown when "
            "artefacts are missing), never automatically on page load.")
