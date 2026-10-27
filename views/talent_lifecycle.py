"""Module 2 — Talent & Lifecycle: recruitment, early attrition, performance and
mobility, engagement, employee feedback and statistical diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import charts, data
from components.filters import segment_bar
from components.kpi_cards import caption, fmt_inr, fmt_int, fmt_pct, insight, metric, page_header
from components.tables import show
from src.analytics import engagement, recruitment, retention
from src.analytics.common import Filters
from src.analytics.engagement import MIN_GROUP


def render() -> None:
    v = data.data_version()
    page_header("Module 2", "Talent & Lifecycle",
                "Why are outcomes moving? Follow people from application to exit — where the funnel leaks, which "
                "joiners leave early, how careers progress, and what engagement and feedback signal.")
    f = segment_bar(v, note="Recruitment uses the requisition's department, location and level; feedback comments "
                            "carry only department.")
    tabs = st.tabs(["Recruitment funnel", "Early attrition & cohorts", "Performance & mobility",
                    "Engagement", "Employee feedback", "Statistical diagnostics"])
    with tabs[0]:
        _recruitment(f, v)
    with tabs[1]:
        _cohorts(f, v)
    with tabs[2]:
        _performance(f, v)
    with tabs[3]:
        _engagement(f, v)
    with tabs[4]:
        _feedback(f, v)
    with tabs[5]:
        _diagnostics(f, v)


# ------------------------------------------------------------------ recruitment

def _recruitment(f: Filters, v: str) -> None:
    rk = data.rec_kpis(f, v)
    c = st.columns(6)
    metric(c[0], "Applications", fmt_int(rk["applications"]), key="applications")
    metric(c[1], "Offer acceptance", fmt_pct(rk["offer_acceptance_rate"]), key="offer_acceptance_rate")
    metric(c[2], "Joining rate", fmt_pct(rk["joining_rate"]), key="joining_rate")
    metric(c[3], "Time to hire", f"{rk['median_time_to_hire'] or 0:.0f} days", key="time_to_hire",
           help_extra="Median.")
    metric(c[4], "Time to fill", f"{rk['median_time_to_fill'] or 0:.0f} days", key="time_to_fill",
           help_extra="Median of filled requisitions.")
    metric(c[5], "Cost per hire", fmt_inr(rk["cost_per_hire"]), key="cost_per_hire")
    cands = data.candidates(f, v)
    if cands.empty:
        st.info("No candidates in scope.")
        return
    fun = recruitment.funnel(cands)
    worst = fun.iloc[1:].sort_values("conversion_from_previous").iloc[0]
    insight(f"Of **{fun.candidates.iloc[0]:,}** applications, **{fun.candidates.iloc[-1]:,}** have joined so far "
            f"({fun.candidates.iloc[-1] / fun.candidates.iloc[0]:.1%}). The steepest stage is into **{worst.stage}** "
            f"({worst.conversion_from_previous:.0%} convert). {rk['open_requisitions']:,} requisitions are open at "
            f"{f.end:%b %Y}.")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.plotly_chart(charts.funnel(fun.stage.tolist(), fun.candidates.tolist(), "Candidate funnel "
                                      "(application cohort)", height=330), config=charts.CONFIG)
        caption("Candidates who applied in the period, counted at the furthest stage reached. Recent applicants are "
                "still in process, so late stages are understated for the newest cohort.")
    with c2:
        sd = data.stage_durations(f, v)
        if len(sd):
            st.plotly_chart(charts.bar(sd, "transition", "median_days", "Median days between stages", horizontal=True,
                                       height=330, text_fmt=".0f"), config=charts.CONFIG)
    st.markdown("#### Source effectiveness")
    caption("Ranked by join yield (joiners ÷ applications), not volume. 90-day retention uses all hires from the "
            "source with at least 90 days of observation.")
    src = data.sources(f, v)
    if len(src):
        show(src[["source", "applications", "interview_rate", "offer_rate", "offer_acceptance", "join_yield",
                  "retention_90d", "median_time_to_hire", "cost_per_hire"]],
             {"applications": "int", "interview_rate": "pct", "offer_rate": "pct", "offer_acceptance": "pct",
              "join_yield": "pct", "retention_90d": "pct", "median_time_to_hire": "float1", "cost_per_hire": "inr"},
             download="source_effectiveness")
        best, vol = src.iloc[0], src.sort_values("applications").iloc[-1]
        insight(f"**{best.source}** converts {best.join_yield:.1%} of applicants into joiners; the highest-volume "
                f"channel, **{vol.source}**, converts {vol.join_yield:.1%}. Volume alone is a poor guide to "
                "sourcing value.")
    c1, c2 = st.columns(2)
    with c1:
        t = data.tth(f, v)
        if len(t):
            st.plotly_chart(charts.histogram(t.days, "Time to hire (application → acceptance)", nbins=40,
                                             height=280, xlabel="Days"), config=charts.CONFIG)
    with c2:
        t = data.ttf(f, v)
        t = t[t.filled == 1]
        if len(t):
            st.plotly_chart(charts.histogram(t.days, "Time to fill (requisition opened → acceptance)", nbins=40,
                                             color=charts.NAVY, height=280, xlabel="Days"), config=charts.CONFIG)
    caption("Both distributions are right-skewed, so medians are reported. Notice periods after acceptance are "
            "excluded; still-open requisitions are excluded here (see Predictive Intelligence for censoring-aware "
            "estimates).")


# ----------------------------------------------------------------- cohorts

def _cohorts(f: Filters, v: str) -> None:
    lo, _ = data.bounds(v)
    window = st.radio("Joining cohorts", ["All joiners in the history", "Joiners in the selected period"],
                      horizontal=True, key="cohort_window",
                      help="Longer windows allow 6- and 12-month retention to be observed.")
    fj = f.with_period(lo, f.end) if window.startswith("All") else f
    j = data.joiners(fj, v)
    if j.empty:
        st.info("No joiners in scope.")
        return
    ea = retention.early_attrition_summary(j)
    c = st.columns(4)
    metric(c[0], "Joiners", fmt_int(len(j)), key="joiners")
    for col, (_, r) in zip(c[1:], ea.iterrows()):
        metric(col, f"Left within {r.horizon}", fmt_pct(r.early_attrition), key="early_attrition",
               help_extra=f"{int(r.leavers)} of {int(r.eligible_joiners)} eligible joiners; 95% CI "
                          f"{fmt_pct(r.ci_low)}–{fmt_pct(r.ci_high)}.")
    ct = retention.cohort_table(j)
    hz = list(retention.HORIZONS)
    heat = ct.set_index("cohort")[hz].copy()
    for label, d in retention.HORIZONS.items():  # suppress cells observed for fewer than 20 joiners
        heat.loc[ct.set_index("cohort")[f"n_{d}"] < 20, label] = np.nan
    c1, c2 = st.columns([3, 2])
    with c1:
        st.plotly_chart(charts.heatmap(heat, "Retention by joining quarter (blank = not yet observable)", pct=True,
                                       height=max(280, 28 * len(heat) + 60), zmin=0.7, zmax=1.0),
                        config=charts.CONFIG)
        caption("A joiner counts towards an N-day rate only once N days have passed — recent cohorts are not "
                "assumed to have survived.")
    with c2:
        km = retention.kaplan_meier(j, "recruitment_source")
        lr = retention.logrank(j, "recruitment_source")
        fig = go.Figure()
        for k, (name, g) in enumerate(km.groupby("group")):
            fig.add_trace(go.Scatter(x=g.days, y=g.retention, mode="lines", line_shape="hv", name=str(name),
                                     line=dict(width=2, color=charts.PALETTE[k % len(charts.PALETTE)])))
        st.plotly_chart(charts.finish(fig, "Kaplan-Meier retention by source of hire", 330, pct_y=True),
                        config=charts.CONFIG)
        if lr.get("p_value") is not None:
            caption(f"Log-rank test across {lr['groups']} sources: χ² = {lr['statistic']:.1f}, p = {lr['p_value']:.3g}. "
                    "'Unknown' = hired before the recruitment history begins.")
    seg = st.selectbox("Compare 90-day attrition by", ["department_name", "job_family", "recruitment_source",
                                                      "location", "job_level"],
                       format_func=lambda s: s.replace("_name", "").replace("_", " ").capitalize(), key="ea_dim")
    rows = []
    for key, g in j.groupby(seg):
        r, n, left = retention.retention_at(g, 90)
        if n >= 15:
            from src.analytics.common import wilson_ci

            lo_, hi_ = wilson_ci(left, n)
            rows.append(dict(segment=key, eligible=n, early_attrition=1 - r, ci_low=lo_, ci_high=hi_))
    if rows:
        d = pd.DataFrame(rows).sort_values("early_attrition", ascending=False)
        st.plotly_chart(charts.bar(d, "segment", "early_attrition", "90-day attrition with 95% CI (n ≥ 15)",
                                   horizontal=True, pct=True, height=max(280, 24 * len(d)),
                                   error=("ci_low", "ci_high")), config=charts.CONFIG)
        overall = 1 - retention.retention_at(j, 90)[0]
        hot = d[d.ci_low > overall]
        insight((f"Joiners in **{', '.join(map(str, hot.segment.head(3)))}** leave within 90 days more often than the "
                 f"overall {overall:.1%}, and the confidence intervals exclude the overall rate." if len(hot) else
                 f"No segment's 90-day attrition is clearly above the overall {overall:.1%} once uncertainty is "
                 "considered."), "warn" if len(hot) else "")
    te = data.tenure_exit(f, v)
    if len(te):
        fig = go.Figure()
        for k, (t, g) in enumerate(te.groupby("exit_type")):
            fig.add_trace(go.Histogram(x=g.tenure_months, name=t, nbinsx=48,
                                       marker_color=[charts.AMBER, charts.TEAL][k % 2]))
        fig.update_layout(barmode="stack")
        st.plotly_chart(charts.finish(fig, "Tenure at exit (months), selected period", 280), config=charts.CONFIG)


# --------------------------------------------------------------- performance

def _performance(f: Filters, v: str) -> None:
    rv = data.reviews(f, v)
    c1, c2 = st.columns([3, 2])
    with c1:
        if len(rv):
            t = rv.groupby(["review_cycle", "performance_rating"]).size().rename("n").reset_index()
            t["share"] = t.n / t.groupby("review_cycle").n.transform("sum")
            fig = go.Figure()
            colors = {1: charts.RED, 2: "#F59E0B", 3: "#94A3B8", 4: "#5EAAA8", 5: charts.TEAL}
            for r in range(1, 6):
                g = t[t.performance_rating == r]
                fig.add_trace(go.Bar(x=g.review_cycle, y=g.share, name=f"Rating {r}", marker_color=colors[r]))
            fig.update_layout(barmode="stack")
            st.plotly_chart(charts.finish(fig, "Rating distribution by review cycle", 320, pct_y=True),
                            config=charts.CONFIG)
            caption("Ratings: 1 unsatisfactory · 2 needs improvement · 3 meets · 4 exceeds · 5 outstanding. "
                    "Annual April cycle covering the previous April–March.")
    with c2:
        if len(rv):
            last = rv[rv.review_cycle == rv.review_cycle.max()]
            by = last.groupby("department_name").agg(reviews=("performance_rating", "size"),
                                                     avg_rating=("performance_rating", "mean"),
                                                     high=("performance_rating", lambda s: (s >= 4).mean()))
            by = by[by.reviews >= MIN_GROUP].sort_values("avg_rating", ascending=False).reset_index()
            st.markdown(f"**{last.review_cycle.iloc[0]} by department**")
            show(by, {"avg_rating": "float2", "high": "pct", "reviews": "int"}, height=300,
                 rename={"high": "Rated 4-5"})
    c1, c2 = st.columns(2)
    with c1:
        p = data.promotions(f, "department", v)
        p = p[p.avg_headcount >= 20]
        st.plotly_chart(charts.bar(p, "segment", "promotion_rate", "Promotion rate by department (annualised)",
                                   horizontal=True, pct=True, height=max(300, 22 * len(p)),
                                   error=("ci_low", "ci_high")), config=charts.CONFIG)
    with c2:
        pl = data.promotions(f, "job_level", v)
        pl = pl[pl.segment.astype(str).isin([f"L{i}" for i in range(1, 7)])]
        st.plotly_chart(charts.bar(pl.assign(segment=pl.segment.astype(str)), "segment", "promotion_rate",
                                   "Promotion rate by level promoted from", pct=True, height=300,
                                   error=("ci_low", "ci_high")), config=charts.CONFIG)
        pm = data.promotions(f, "department", v)
        pm = pm[pm.avg_headcount >= 20].sort_values("mobility_rate", ascending=False)
        if len(pm):
            insight(f"Internal mobility is highest in **{pm.iloc[0].segment}** ({pm.iloc[0].mobility_rate:.1%} of "
                    f"headcount transferring in per year) and lowest in **{pm.iloc[-1].segment}** "
                    f"({pm.iloc[-1].mobility_rate:.1%}).")
    st.markdown("#### Learning and development")
    tr = data.training(f, v)
    c = st.columns(4)
    metric(c[0], "Participation", fmt_pct(tr["participation"]), key="training_participation")
    metric(c[1], "Completion rate", fmt_pct(tr["completion_rate"]), key="training_completion_rate")
    metric(c[2], "Hours per employee", f"{tr['hours_per_employee'] or 0:.1f}", key="training_participation",
           help_extra="Completed hours ÷ average headcount.")
    metric(c[3], "Cost per employee", fmt_inr(tr["cost_per_employee"]), key="training_cost_per_employee")
    eff = data.training_effect(v)
    if eff:
        insight(f"Employees with 16+ development hours in a review year improved their rating by "
                f"**{eff['raw_difference']:+.2f}** points more than others (raw). Comparing like with like on prior "
                f"rating, the difference is **{eff['adjusted_difference']:+.2f}**. Trained employees started from a "
                f"higher average rating ({eff['prior_rating_trained']:.2f} vs {eff['prior_rating_untrained']:.2f}), "
                "so selection and regression to the mean matter — this is an association, not proof that training "
                "caused better performance.", "warn")
    show(tr["by_category"], {"completion_rate": "pct", "cost": "inr", "hours": "int", "enrolments": "int"})


# ---------------------------------------------------------------- engagement

def _engagement(f: Filters, v: str) -> None:
    lo, _ = data.bounds(v)
    fe = f.with_period(lo, f.end)
    r = data.survey_responses(fe, v)
    if r.empty:
        st.info("No survey responses in scope.")
        return
    tr = engagement.trend(r)
    last, prev = tr.iloc[-1], tr.iloc[-2] if len(tr) > 1 else None
    rr = data.response_rates(f, v)
    c = st.columns(4)
    metric(c[0], f"Engagement ({last.survey_period})", f"{last.engagement_score:.2f} / 5",
           f"{last.engagement_score - prev.engagement_score:+.2f}" if prev is not None else None,
           key="engagement_score")
    metric(c[1], "eNPS", f"{last.enps:+.0f}", f"{last.enps - prev.enps:+.0f}" if prev is not None else None,
           key="enps")
    metric(c[2], "Responses", fmt_int(last.responses), key="response_rate")
    metric(c[3], "Response rate", fmt_pct(rr.response_rate.iloc[-1]) if len(rr) else "—", key="response_rate")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.plotly_chart(charts.line(tr, "survey_period", engagement.SCORES,
                                    ["Engagement", "Job satisfaction", "Manager support", "Work-life balance",
                                     "Career growth"], "Survey scores by wave (1–5)", height=330, hover_fmt=".2f"),
                        config=charts.CONFIG)
    with c2:
        tb = engagement.trend(r, "tenure_band")
        tb = tb[tb.survey_period == tb.survey_period.max()].sort_values("tenure_band")
        st.plotly_chart(charts.bar(tb.assign(tenure_band=tb.tenure_band.astype(str)), "tenure_band",
                                   "engagement_score", f"Engagement by tenure · {last.survey_period}", height=330,
                                   text_fmt=".2f", hover_fmt=".2f"), config=charts.CONFIG)
    dt = engagement.trend(r, "department_name")
    z = dt.pivot(index="department_name", columns="survey_period", values="engagement_score").dropna(how="all")
    st.plotly_chart(charts.heatmap(z.round(2), "Engagement by department and wave (groups < 10 suppressed)",
                                   fmt=".2f", height=max(320, 22 * len(z) + 60),
                                   colorscale=[[0, "#FEF3C7"], [0.5, "#F8FAFC"], [1, "#5EAAA8"]],
                                   zmid=float(np.nanmean(z.values))), config=charts.CONFIG)
    ch = engagement.department_change(r)
    if len(ch):
        sig = ch[(ch.p_value < 0.05) & (~ch.suppressed)]
        down = sig[sig.change < 0]
        insight((f"Between {ch.attrs['previous']} and {ch.attrs['current']}, engagement fell significantly in "
                 + ", ".join(f"**{x.department}** ({x.change:+.2f})" for x in down.itertuples())
                 + "." if len(down) else
                 f"No department's engagement changed significantly between {ch.attrs['previous']} and "
                 f"{ch.attrs['current']} (Welch t-test, p < 0.05)."), "warn" if len(down) else "")
    c1, c2 = st.columns(2)
    with c1:
        ee = data.engagement_exit(f.with_period(lo, f.end), v)
        if ee:
            t = ee["table"]
            st.plotly_chart(charts.bar(t.assign(quartile=t.quartile.astype(str)), "quartile", "exit_rate",
                                       "Voluntary exit within 6 months, by engagement quartile", pct=True,
                                       height=300, error=("ci_low", "ci_high")), config=charts.CONFIG)
            caption(f"{ee['responses']:,} responses with a fully observed 6-month window. χ² p = {ee['p_value']:.2g}; "
                    f"median score of leavers {ee['leaver_median']:.2f} vs stayers {ee['stayer_median']:.2f} "
                    f"(Mann-Whitney p = {ee['mannwhitney_p']:.2g}). Association, not causation.")
    with c2:
        mc = data.manager_change(f.with_period(lo, f.end), v)
        if mc:
            s = mc["summary"].copy()
            s["group"] = np.where(s.manager_changed, "Manager changed between waves", "Same manager")
            fig = charts.grouped_bar(s, "group", ["mean_change_engagement", "mean_change_manager_support"],
                                     ["Δ engagement", "Δ manager support"], "Change between consecutive surveys",
                                     colors=[charts.NAVY, charts.TEAL], height=300)
            st.plotly_chart(fig, config=charts.CONFIG)
            caption(f"Manager-support change differs by {mc['diff_manager_support']:+.2f} points (Mann-Whitney "
                    f"p = {mc['p_manager_support']:.2g}); engagement by {mc['diff_engagement']:+.2f} "
                    f"(p = {mc['p_engagement']:.2g}). Manager changes are not random (reorganisations, exits), so "
                    "this does not show that a manager change causes disengagement.")


# ---------------------------------------------------------------- feedback

def _feedback(f: Filters, v: str) -> None:
    from src.nlp import feedback_analysis as fa
    from src.nlp.sentiment import ambiguous_examples, evaluate_against_labels
    from src.nlp.topic_analysis import distinctive_terms, evaluate_themes, keyword_frequency, theme_frame

    lo, _ = data.bounds(v)
    fb = data.feedback(f.with_period(lo, f.end) if st.toggle("Use full history", value=True, key="fb_hist") else f, v)
    if fb.empty:
        st.info("No comments in scope.")
        return
    caption("Comments are stored without employee identifiers (department, wave and tenure band only). Groups with "
            f"fewer than {MIN_GROUP} comments are suppressed. Sentiment is a lexicon estimate (VADER), not a measure of "
            "what any individual feels.")
    ts = fa.theme_summary(fb)
    c1, c2 = st.columns([3, 2])
    with c1:
        st.plotly_chart(charts.bar(ts, "theme", "share_of_comments", f"Themes in {len(fb):,} comments", horizontal=True,
                                   pct=True, height=320), config=charts.CONFIG)
    with c2:
        show(ts[["theme", "mentions", "avg_sentiment", "share_negative"]],
             {"mentions": "int", "avg_sentiment": "float2", "share_negative": "pct"}, height=320)
    neg = ts[ts.mentions >= MIN_GROUP].sort_values("share_negative", ascending=False).iloc[0]
    insight(f"**{neg.theme}** is the most negative theme: {neg.share_negative:.0%} of comments mentioning it read as "
            f"negative (average compound score {neg.avg_sentiment:+.2f}).", "warn")
    tt = fa.theme_trend(fb)
    top = ts.theme.head(5).tolist()
    tt = tt[tt.theme.isin(top)].pivot(index="survey_period", columns="theme", values="share_of_comments").reset_index()
    st.plotly_chart(charts.line(tt, "survey_period", top, top, "Theme share of comments by wave", pct=True,
                                height=300), config=charts.CONFIG)
    c1, c2 = st.columns(2)
    with c1:
        sd = fa.sentiment_by(fb, "department_name").sort_values("avg_sentiment")
        show(sd[["department_name", "comments", "avg_sentiment", "share_negative", "suppressed"]],
             {"comments": "int", "avg_sentiment": "float2", "share_negative": "pct"}, height=320,
             rename={"department_name": "Department"})
    with c2:
        kw = keyword_frequency(fb.feedback_text, 15)
        st.plotly_chart(charts.bar(kw, "term", "count", "Most frequent terms (stop-words removed)", horizontal=True,
                                   height=320), config=charts.CONFIG)
    with st.expander("Distinctive terms (TF-IDF) and engagement association", icon=":material/text_fields:"):
        t = theme_frame(fb)
        show(distinctive_terms(t[t.theme != "General / other"], "theme"))
        assoc = fa.engagement_sentiment_association(fb, f.with_period(lo, f.end))
        if assoc.get("spearman_rho") is not None:
            st.markdown(f"Across **{assoc['groups']}** department-waves with ≥ {MIN_GROUP} responses and comments, "
                        f"mean engagement and mean comment sentiment correlate at Spearman ρ = "
                        f"**{assoc['spearman_rho']:.2f}** (p = {assoc['p_value']:.2g}). Aggregate-level association only.")
    with st.expander("How reliable is the text analysis?", icon=":material/rule:"):
        labels = fa.load_labels()
        if labels is not None:
            ev = evaluate_against_labels(fb, labels)
            th = evaluate_themes(fb, labels)
            st.markdown(
                f"Against the generator's intended labels (synthetic ground truth kept outside the database): "
                f"sentiment agreement **{ev['accuracy']:.0%}** on {ev['n']:,} single-tone comments; "
                f"{ev['sarcasm_misread']:.0%} of sarcastic comments are misread; mixed comments "
                f"({ev['mixed_comments']:,}) have no single correct label. Theme detection recovers the intended theme "
                f"for **{th['recall_any']:.0%}** of themed comments (precision {th['precision']:.0%}).")
            st.markdown("**Examples the lexicon model gets wrong or cannot resolve** (text only):")
            show(ambiguous_examples(fb, labels), {"compound": "float2"})
        else:
            st.caption("Evaluation labels not available in this environment.")


# ---------------------------------------------------------------- statistics

def _diagnostics(f: Filters, v: str) -> None:
    from src.analytics.statistics import department_attrition_tests, location_confounding

    pop = data.stats_population(f, v)
    st.markdown("#### Which departments really differ?")
    caption("Share of employees present in the period who left voluntarily, each department vs the rest. 24 tests at "
            "once would produce false alarms, so p-values are Holm-adjusted.")
    t = department_attrition_tests(pop)
    if len(t):
        t["status"] = np.where(t.significant_after_correction, "Significant after correction",
                               np.where(t.significant_uncorrected, "Only before correction", "Not significant"))
        fig = go.Figure()
        cmap = {"Significant after correction": charts.AMBER, "Only before correction": "#FCD34D",
                "Not significant": "#CBD5E1"}
        for s_, g in t.groupby("status"):
            fig.add_trace(go.Bar(y=g.department, x=g.rate_ratio - 1, base=1, orientation="h", name=s_,
                                 marker_color=cmap[s_], hovertemplate="%{y}<br>rate ratio %{x:.2f}<extra></extra>"))
        fig.add_vline(x=1, line=dict(color=charts.NAVY, width=1))
        fig.update_yaxes(categoryorder="array", categoryarray=t.sort_values("rate_ratio").department.tolist())
        st.plotly_chart(charts.finish(fig, "Voluntary leaver rate ratio vs rest of organisation", max(340, 20 * len(t))),
                        config=charts.CONFIG)
        sig = t[t.significant_after_correction]
        only = t[t.significant_uncorrected & ~t.significant_after_correction]
        insight(f"**{len(sig)}** department(s) differ after correction ("
                + ", ".join(f"{x.department} ×{x.rate_ratio:.2f}" for x in sig.head(4).itertuples())
                + f"). {len(only)} more would look 'significant' without correcting for multiple comparisons.")
        with st.expander("Test details"):
            show(t[["department", "employees", "leavers", "rate", "rest_rate", "rate_ratio", "p_value", "p_holm"]],
                 {"rate": "pct", "rest_rate": "pct", "rate_ratio": "float2", "p_value": "float2", "p_holm": "float2",
                  "employees": "int", "leavers": "int"}, download="department_attrition_tests")
    st.markdown("#### Does location matter, or is it department mix?")
    lc = location_confounding(pop)
    if lc:
        c1, c2 = st.columns([2, 3])
        with c1:
            show(lc["rates"].rename(columns={"mean": "voluntary_leaver_rate", "size": "employees"}),
                 {"voluntary_leaver_rate": "pct", "employees": "int"})
        with c2:
            raw_sig = lc["p_raw"] < 0.05
            adj_sig = lc["p_adjusted"] < 0.05
            insight(f"Raw comparison across locations: χ² = {lc['chi2']:.1f}, p = {lc['p_raw']:.2g} (Cramér's V "
                    f"{lc['cramers_v']:.2f}). After controlling for department in a logistic model: likelihood-ratio "
                    f"p = **{lc['p_adjusted']:.2g}**. "
                    + ("Location differences largely disappear once department mix is accounted for — they reflect "
                       "where frontline teams sit, not the city itself." if raw_sig and not adj_sig else
                       "Location remains associated with leaving after controlling for department." if adj_sig else
                       "There is no meaningful location effect either way."))
    st.markdown("#### What is associated with leaving within six months?")
    d = data.drivers(v)
    if d:
        tbl = d["table"]
        fig = go.Figure(go.Scatter(x=tbl.odds_ratio, y=tbl.factor, mode="markers",
                                   marker=dict(size=9, color=np.where(tbl.p_value < 0.05, charts.TEAL, charts.LIGHT)),
                                   error_x=dict(type="data", symmetric=False, array=tbl.ci_high - tbl.odds_ratio,
                                                arrayminus=tbl.odds_ratio - tbl.ci_low, color=charts.LIGHT),
                                   hovertemplate="%{y}<br>odds ratio %{x:.2f}<extra></extra>"))
        fig.add_vline(x=1, line=dict(color=charts.NAVY, width=1, dash="dot"))
        fig.update_xaxes(type="log", title="Odds ratio (log scale, 95% CI)")
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(charts.finish(fig, "Logistic regression: voluntary exit within 6 months", 360),
                        config=charts.CONFIG)
        caption(f"{d['n']:,} employee-quarter observations ({d['employees']:,} employees, {d['events']:,} exits) at "
                f"{len(d['dates'])} quarter-ends; standard errors clustered by employee; pseudo-R² {d['pseudo_r2']:.3f}. "
                "Teal points are significant at 5%. Odds ratios describe association in this synthetic dataset.")
        top = tbl[tbl.p_value < 0.05].sort_values("odds_ratio")
        if len(top):
            insight(f"Strongest protective association: **{top.iloc[0].factor}** (odds ×{top.iloc[0].odds_ratio:.2f}); "
                    f"strongest risk association: **{top.iloc[-1].factor}** (odds ×{top.iloc[-1].odds_ratio:.2f}). "
                    "Pseudo-R² is low: most attrition is not explained by these factors, which is typical and a "
                    "reason to use them for prioritising conversations, not predicting individuals.")
