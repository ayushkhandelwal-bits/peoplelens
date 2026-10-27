"""Evidence-based management recommendations generated from calculated outputs.

Every rule states its threshold. A recommendation is only produced when the
underlying evidence meets that threshold; each one carries the observation,
supporting metric, segment, potential impact, suggested action and a
limitation. Wording is deliberately investigative ("review", "check") because
the evidence is associational.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd
from sqlalchemy.engine import Engine
from statsmodels.stats.multitest import multipletests

from src.analytics.common import Filters, add_months
from src.analytics.metrics import two_proportion_test


@dataclass
class Recommendation:
    title: str
    observation: str
    metric: str
    segment: str
    impact: str
    action: str
    limitation: str
    threshold: str
    priority: int  # 1 = highest

    def to_dict(self) -> dict:
        return asdict(self)


def _inr(v: float) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.1f} L"
    return f"₹{v:,.0f}"


THRESHOLDS = {
    "plan_gap": "Forecast headcount at least 5 heads and 3% below plan three months ahead.",
    "early_attrition": "90-day attrition of the last 12 months' joiners above the rest of the organisation "
                       "with Holm-adjusted p < 0.05 and at least 20 eligible joiners.",
    "voluntary_hotspot": "Voluntary leaver share above the rest of the organisation with Holm-adjusted p < 0.05.",
    "source": "Join yield below half of the best source, with at least 200 applications.",
    "cost_variance": "Period cost above plan by more than 3% and ₹10 L.",
    "engagement_drop": "Quarter-on-quarter engagement fall of at least 0.15 points (p < 0.05, >= 30 responses).",
    "time_to_fill": "Kaplan-Meier P80 time-to-fill above 120 days.",
}


def generate(f: Filters, engine: Engine | None = None, forecast: dict | None = None,
             ttf: dict | None = None, cost_per_exit: float | None = None) -> list[Recommendation]:
    recs: list[Recommendation] = []
    recs += _plan_gap(forecast)
    recs += _early_attrition(f, engine)
    recs += _voluntary_hotspots(f, engine, cost_per_exit)
    recs += _source(f, engine)
    recs += _cost_variance(f, engine)
    recs += _engagement(f, engine)
    recs += _time_to_fill(ttf)
    return sorted(recs, key=lambda r: r.priority)


def _plan_gap(forecast: dict | None) -> list[Recommendation]:
    if not forecast:
        return []
    fc = pd.DataFrame(forecast["department_forecast"])
    if fc.empty:
        return []
    months = sorted(fc.month.unique())
    target = months[min(2, len(months) - 1)]
    t = fc[fc.month == target].dropna(subset=["planned_headcount"])
    t = t.assign(gap_pct=t.gap_vs_plan / t.planned_headcount)
    hit = t[(t.gap_vs_plan <= -5) & (t.gap_pct <= -0.03)].sort_values("gap_vs_plan")
    demand = pd.DataFrame(forecast.get("hiring_demand", [])).set_index("department_id") if forecast.get(
        "hiring_demand") else pd.DataFrame()
    mae = next((m["mae"] for m in forecast["model_comparison"] if m["model"] == forecast["selected_model"]), None)
    out = []
    for _, r in hit.head(3).iterrows():
        add = demand.loc[r.department_id, "additional_requisitions"] if r.department_id in demand.index else None
        out.append(Recommendation(
            title=f"{r.department_name}: workforce gap against plan",
            observation=f"{r.department_name} is forecast to be {abs(r.gap_vs_plan):.0f} heads "
                        f"({abs(r.gap_pct):.0%}) below the approved plan by {pd.Timestamp(target):%b %Y}.",
            metric=f"Forecast {r.forecast_headcount:.0f} vs plan {r.planned_headcount:.0f}",
            segment=f"{r.department_name}, next quarter",
            impact="Planned capacity will not be in place; service levels or delivery commitments may be at risk.",
            action="Review recruitment capacity and expected exits before finalising the hiring plan"
                   + (f"; about {add:.0f} additional requisitions would be needed to reach plan." if add else "."),
            limitation=f"Organisation-level forecast backtest MAE ≈ {mae:.0f} heads; department forecasts are "
                       "less precise and ignore transfers." if mae else "Forecast uncertainty applies.",
            threshold=THRESHOLDS["plan_gap"], priority=1))
    return out


def _early_attrition(f: Filters, engine) -> list[Recommendation]:
    from src.analytics.retention import joiners

    j = joiners(f.with_period(add_months(f.end, -17), add_months(f.end, -3)), engine)
    j = j[j.observed_days >= 90]
    if len(j) < 50:
        return []
    j["left90"] = ((j.exited == 1) & (j.tenure_days < 90)).astype(int)
    rows = []
    for dept, g in j.groupby("department_name"):
        if len(g) < 20:
            continue
        rest = j[j.department_name != dept]
        p = two_proportion_test(int(g.left90.sum()), len(g), int(rest.left90.sum()), len(rest))
        rows.append(dict(dept=dept, n=len(g), rate=g.left90.mean(), rest=rest.left90.mean(), p=p))
    df = pd.DataFrame(rows).dropna()
    if df.empty:
        return []
    df["p_holm"] = multipletests(df.p, method="holm")[1]
    hit = df[(df.p_holm < 0.05) & (df.rate > df.rest)].sort_values("rate", ascending=False)
    return [Recommendation(
        title=f"{r.dept}: early attrition of new joiners",
        observation=f"{r.rate:.0%} of {r.dept} joiners left within 90 days, vs {r.rest:.0%} elsewhere.",
        metric=f"90-day attrition {r.rate:.1%} (n = {r.n} joiners), Holm-adjusted p = {r.p_holm:.3f}",
        segment=f"{r.dept}, joiners observed at least 90 days",
        impact="Each early leaver costs the full recruitment and onboarding investment with little return.",
        action="Review onboarding, role previews and the sourcing mix for this department; "
               "interview recent early leavers.",
        limitation="Association only; small cohorts make the rate volatile.",
        threshold=THRESHOLDS["early_attrition"], priority=2) for r in hit.head(2).itertuples()]


def _voluntary_hotspots(f: Filters, engine, cost_per_exit: float | None) -> list[Recommendation]:
    from src.analytics.statistics import at_risk_population, department_attrition_tests

    pop = at_risk_population(f, engine)
    tests = department_attrition_tests(pop)
    if tests.empty:
        return []
    hit = tests[tests.significant_after_correction & (tests.rate_ratio > 1)].head(2)
    out = []
    for r in hit.itertuples():
        impact = (f"Estimated replacement cost of these {r.leavers} voluntary exits ≈ {_inr(r.leavers * cost_per_exit)} "
                  "(turnover-cost model, default assumptions)." if cost_per_exit else
                  "Elevated replacement cost and lost productivity.")
        out.append(Recommendation(
            title=f"{r.department}: voluntary attrition hotspot",
            observation=f"{r.rate:.1%} of employees present in {r.department} left voluntarily in the period, "
                        f"{r.rate_ratio:.1f}× the rest of the organisation ({r.rest_rate:.1%}).",
            metric=f"Rate ratio {r.rate_ratio:.2f}, Holm-adjusted p = {r.p_holm:.3g}",
            segment=f"{r.department}, {f.start:%b %Y}–{f.end:%b %Y}", impact=impact,
            action="Run stay interviews and review workload, pay positioning and manager stability in this team.",
            limitation="Department mix (level, tenure) partly explains differences; see the drivers model.",
            threshold=THRESHOLDS["voluntary_hotspot"], priority=2))
    return out


def _source(f: Filters, engine) -> list[Recommendation]:
    from src.analytics.recruitment import source_effectiveness

    s = source_effectiveness(f, engine)
    if s.empty or len(s) < 2:
        return []
    best = s.iloc[0]
    weak = s[(s.applications >= 200) & (s.join_yield < 0.5 * best.join_yield)]
    out = []
    for r in weak.itertuples():
        out.append(Recommendation(
            title=f"Sourcing mix: {r.source} converts poorly",
            observation=f"{r.source} produced {r.applications:,} applications but only {r.join_yield:.1%} joined, "
                        f"vs {best.join_yield:.1%} for {best.source}.",
            metric=f"Join yield {r.join_yield:.1%}; 90-day retention {r.retention_90d:.0%}"
                   if pd.notna(r.retention_90d) else f"Join yield {r.join_yield:.1%}",
            segment="All requisitions in scope", impact="Screening effort is spent on low-yield applicants, "
                                                        "lengthening time-to-fill.",
            action=f"Shift effort towards {best.source} and tighten screening criteria for {r.source} postings.",
            limitation="Source quality differs by role type; compare within job family before reallocating budget.",
            threshold=THRESHOLDS["source"], priority=3))
    return out[:1]


def _cost_variance(f: Filters, engine) -> list[Recommendation]:
    from src.analytics.compensation import budget_variance_by_department

    df = budget_variance_by_department(f, engine)
    hit = df[(df.variance_pct > 0.03) & (df.variance > 1e6)].head(2)
    return [Recommendation(
        title=f"{r.department_name}: people cost above plan",
        observation=f"{r.department_name} spent {_inr(r.actual_cost)} against a plan of {_inr(r.planned_cost)} "
                    f"({r.variance_pct:+.1%}).",
        metric=f"Cost variance {_inr(r.variance)}", segment=f"{r.department_name}, {f.start:%b %Y}–{f.end:%b %Y}",
        impact="Overspend reduces headroom for planned hiring later in the year.",
        action="Check whether the overspend comes from headcount above plan, level mix or pay increases.",
        limitation="Plan assumes a 7% merit budget; variable pay is at target, not actual payout.",
        threshold=THRESHOLDS["cost_variance"], priority=3) for r in hit.itertuples()]


def _engagement(f: Filters, engine) -> list[Recommendation]:
    from src.analytics.engagement import department_change, responses

    r = responses(f, engine)
    if r.empty:
        return []
    ch = department_change(r)
    if ch.empty:
        return []
    hit = ch[(~ch.suppressed) & (ch.change <= -0.15) & (ch.p_value < 0.05) & (ch.responses >= 30)]
    cur, prev = ch.attrs.get("current"), ch.attrs.get("previous")
    return [Recommendation(
        title=f"{x.department}: engagement fell this quarter",
        observation=f"Average engagement in {x.department} fell from {x.previous:.2f} to {x.current:.2f} "
                    f"between {prev} and {cur}.",
        metric=f"Change {x.change:+.2f} points (n = {x.responses}, p = {x.p_value:.3f})",
        segment=f"{x.department}, {cur}",
        impact="Lower engagement is associated with higher voluntary exits in the following six months.",
        action="Share results with the team, run listening sessions and check recent workload or manager changes.",
        limitation="Survey non-response is higher among disengaged employees; true decline may be larger.",
        threshold=THRESHOLDS["engagement_drop"], priority=2) for x in hit.head(2).itertuples()]


def _time_to_fill(ttf: dict | None) -> list[Recommendation]:
    if not ttf:
        return []
    s = pd.DataFrame(ttf.get("km_summary", []))
    hit = s[s.km_p80_days > 120] if "km_p80_days" in s else pd.DataFrame()
    return [Recommendation(
        title=f"{r.job_family}: long time-to-fill",
        observation=f"20% of {r.job_family} requisitions take longer than {r.km_p80_days:.0f} days to reach an "
                    f"accepted offer (median {r.km_median_days:.0f} days).",
        metric=f"Kaplan-Meier P80 = {r.km_p80_days:.0f} days ({r.requisitions} requisitions, {r.censored} still open)",
        segment=f"{r.job_family} requisitions", impact="Backfills arrive months after exits, widening plan gaps.",
        action=f"Open {r.job_family} requisitions about {r.km_p80_days:.0f} days (plus notice period) before the "
               "need date, and consider pipelining for recurring roles.",
        limitation="Does not model recruiter capacity or market changes.",
        threshold=THRESHOLDS["time_to_fill"], priority=3) for r in hit.head(2).itertuples()]
