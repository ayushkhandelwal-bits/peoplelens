"""Curated, parameterised query templates behind Ask PeopleLens.

Each supported intent maps to a handler that builds SQL from a fixed template
plus bound parameters (never from the question text), runs it on a read-only
connection, applies the central metric formulas and returns the evidence the
answer generator needs. Questions about forecasts or attrition drivers are
answered from the saved, validated model artefacts instead of SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from database.connection import days_between, expand_in, get_readonly_engine, read_sql
from src.analytics.common import Filters, add_months, safe_div
from src.analytics.metrics import DIMENSIONS, TENURE_ORDER, attrition_rate, enps
from src.assistant.intent_parser import ParsedQuestion


@dataclass
class Evidence:
    intent: str
    title: str
    metric_key: str | None
    sql: str | None
    params: dict
    table: pd.DataFrame
    chart: dict = field(default_factory=dict)
    headline: str = ""
    comparison: str = ""
    interpretation: str = ""
    caveat: str = ""
    source: str = "SQL template"


class Context:
    def __init__(self, engine: Engine | None, lookups: dict, data_start, data_end):
        self.engine = engine or get_readonly_engine("analyst")
        self.lookups = lookups
        self.data_start, self.data_end = data_start, data_end
        self.dept_by_name = {v: k for k, v in lookups["departments"].items()}
        self.loc_by_city = {v: k for k, v in lookups["locations"].items()}

    def filters(self, pq: ParsedQuestion, start=None, end=None) -> Filters:
        return Filters(start=start or pq.start, end=end or pq.end,
                       departments=tuple(self.dept_by_name[d] for d in pq.departments if d in self.dept_by_name),
                       locations=tuple(self.loc_by_city[c] for c in pq.locations if c in self.loc_by_city),
                       levels=tuple(pq.levels), business_units=tuple(pq.business_units))

    def run(self, sql: str, params: dict) -> pd.DataFrame:
        return read_sql(sql, params, self.engine)


def _fmt_sql(sql: str) -> str:
    return "\n".join(line.rstrip() for line in sql.strip().splitlines() if line.strip())


def _pct(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.1%}"


def _inr(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"₹{v / 1e7:,.2f} Cr" if abs(v) >= 1e7 else f"₹{v / 1e5:,.1f} L" if abs(v) >= 1e5 else f"₹{v:,.0f}"


def _mart_breakdown(ctx: Context, f: Filters, dim: str | None, select: str) -> tuple[str, dict]:
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    if dim:
        expr, join = DIMENSIONS[dim]
        sql = f"""
SELECT {expr} AS segment, {select}
FROM mart_employee_month m {join}
WHERE m.snapshot_date BETWEEN :s AND :e {seg}
GROUP BY {expr}"""
    else:
        sql = f"""
SELECT {select}
FROM mart_employee_month m
WHERE m.snapshot_date BETWEEN :s AND :e {seg}"""
    return _fmt_sql(sql), params


# ---------------------------------------------------------------- handlers

def headcount(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = f.segment_sql("m")
    params["d"] = f.end
    if pq.breakdown and pq.breakdown in DIMENSIONS:
        expr, join = DIMENSIONS[pq.breakdown]
        sql = _fmt_sql(f"""
SELECT {expr} AS segment, SUM(m.active_flag) AS active_headcount
FROM mart_employee_month m {join}
WHERE m.snapshot_date = :d {seg}
GROUP BY {expr} ORDER BY active_headcount DESC""")
    else:
        sql = _fmt_sql(f"""
SELECT SUM(m.active_flag) AS active_headcount, AVG(m.tenure_months) AS avg_tenure_months
FROM mart_employee_month m
WHERE m.snapshot_date = :d AND m.active_flag = 1 {seg}""")
    df = ctx.run(sql, params)
    prev = ctx.run(sql, dict(params, d=add_months(f.end, -12))) if not pq.breakdown else None
    if pq.breakdown:
        top = df.iloc[0] if len(df) else None
        head = (f"{int(df.active_headcount.sum()):,} active employees at {f.end:%d %b %Y}; largest "
                f"{pq.breakdown.replace('_', ' ')}: **{top.segment}** ({int(top.active_headcount):,})." if top is not None
                else "No employees match.")
        comp = ""
        chart = {"type": "bar", "x": "segment", "y": "active_headcount"}
    else:
        hc = int(df.active_headcount.iloc[0] or 0)
        head = f"**{hc:,}** active employees at {f.end:%d %b %Y}."
        p = prev.active_headcount.iloc[0] if prev is not None and len(prev) else None
        comp = (f"That is {hc - int(p):+,} ({(hc - p) / p:+.1%}) versus a year earlier ({int(p):,})."
                if p else "")
        chart = {}
    return Evidence("headcount", "Active headcount", "active_headcount", sql, params, df, chart, head, comp,
                    "Point-in-time headcount at the last month end of the period.",
                    "Excludes people who joined and left within the same month.")


def _flow(pq: ParsedQuestion, ctx: Context, kind: str) -> Evidence:
    f = ctx.filters(pq)
    col = "m.joined_in_month" if kind == "joiners" else "m.exited_in_month"
    select = f"SUM({col}) AS {kind}"
    sql, params = _mart_breakdown(ctx, f, pq.breakdown, select)
    if pq.breakdown:
        sql += f"\nORDER BY {kind} DESC"
    df = ctx.run(sql, params)
    total = int(df[kind].sum())
    pf = f.previous()
    prev = ctx.run(sql, dict(params, s=pf.start, e=pf.end)) if pf.start >= ctx.data_start else None
    head = f"**{total:,}** {kind} in {pq.period_label} ({f.start:%b %Y}–{f.end:%b %Y})."
    if pq.breakdown and len(df):
        head += f" Most: **{df.iloc[0].segment}** ({int(df.iloc[0][kind]):,})."
    comp = ""
    if prev is not None and len(prev):
        p = int(prev[kind].sum())
        comp = f"Previous {f.months}-month period: {p:,} ({safe_div(total - p, p) or 0:+.1%})." if p else ""
    return Evidence(kind, kind.capitalize(), kind, sql, params, df,
                    {"type": "bar", "x": "segment", "y": kind} if pq.breakdown else {}, head, comp,
                    "Counts by joining date (joiners) or separation date (exits).",
                    "Internal transfers are neither joiners nor exits.")


def joiners(pq, ctx):
    return _flow(pq, ctx, "joiners")


def attrition(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    select = ("SUM(m.active_flag) AS headcount_months, COUNT(DISTINCT m.snapshot_date) AS months, "
              "SUM(m.exited_in_month) AS exits, "
              "SUM(CASE WHEN m.exit_type = 'Voluntary' THEN 1 ELSE 0 END) AS voluntary_exits")
    dim = pq.breakdown or ("department" if pq.direction else None)
    sql, params = _mart_breakdown(ctx, f, dim, select)
    df = ctx.run(sql, params)
    n = f.months
    df["average_headcount"] = df.headcount_months / n
    df["attrition_rate"] = [attrition_rate(e, h, n) for e, h in zip(df.exits, df.average_headcount)]
    df["voluntary_rate"] = [attrition_rate(e, h, n) for e, h in zip(df.voluntary_exits, df.average_headcount)]
    if dim:
        df = df[(df.average_headcount >= 5) & (df.segment != "Executive Office")].sort_values(
            "attrition_rate", ascending=pq.direction == "lowest")
        top = df.iloc[0]
        head = (f"{'Lowest' if pq.direction == 'lowest' else 'Highest'} annualised attrition in {pq.period_label}: "
                f"**{top.segment}** at {_pct(top.attrition_rate)} ({int(top.exits)} exits, average headcount "
                f"{top.average_headcount:,.0f}).")
        overall = attrition_rate(df.exits.sum(), df.average_headcount.sum(), n)
        comp = f"Organisation-wide in the same scope: {_pct(overall)}. Next: " + ", ".join(
            f"{r.segment} ({_pct(r.attrition_rate)})" for r in df.iloc[1:4].itertuples()) + "."
        table = df[["segment", "exits", "average_headcount", "attrition_rate", "voluntary_rate"]]
        chart = {"type": "bar", "x": "segment", "y": "attrition_rate", "pct": True}
    else:
        r = df.iloc[0]
        head = (f"Annualised attrition in {pq.period_label}: **{_pct(r.attrition_rate)}** ({int(r.exits)} exits; "
                f"voluntary {_pct(r.voluntary_rate)}).")
        pf = f.previous()
        comp = ""
        if pf.start >= ctx.data_start:
            p = ctx.run(sql, dict(params, s=pf.start, e=pf.end)).iloc[0]
            pr = attrition_rate(p.exits, p.headcount_months / n, n)
            comp = f"Previous {n}-month period: {_pct(pr)}."
        table = df[["exits", "voluntary_exits", "average_headcount", "attrition_rate", "voluntary_rate"]]
        chart = {}
    caveat = "Rates are annualised; short periods are affected by seasonality (exits peak in April–May)."
    if n <= 3:
        caveat += " A single quarter is a short window, so differences between small departments are noisy."
    return Evidence("attrition", "Attrition rate", "attrition_rate", sql, params, table, chart, head, comp,
                    "Exits divided by average month-end headcount, annualised (× 12 / months).", caveat)


def early_attrition(pq: ParsedQuestion, ctx: Context) -> Evidence:
    if pq.breakdown == "tenure_band":
        e = attrition(ParsedQuestion(**{**pq.__dict__, "breakdown": "tenure_band", "direction": None}), ctx)
        t = e.table.copy()
        t["retention_rate"] = 1 - t.attrition_rate
        t["segment"] = pd.Categorical(t.segment, TENURE_ORDER, ordered=True)
        t = t.sort_values("segment")
        low = t.sort_values("retention_rate").iloc[0]
        e.intent, e.title, e.table = "retention", "Retention by tenure band", t
        e.headline = (f"Lowest annualised retention: **{low.segment}** tenure band at {_pct(low.retention_rate)} "
                      f"(attrition {_pct(low.attrition_rate)}).")
        e.comparison = "By band: " + ", ".join(f"{r.segment} {_pct(r.retention_rate)}" for r in t.itertuples()) + "."
        e.chart = {"type": "bar", "x": "segment", "y": "retention_rate", "pct": True}
        e.interpretation = "Retention = 1 − annualised attrition for employees currently in each tenure band."
        return e
    f = ctx.filters(pq, start=add_months(ctx.data_end, -17), end=add_months(ctx.data_end, -3))
    seg, params = f.segment_sql("m")
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end, x=ctx.data_end)
    dim_expr, join = DIMENSIONS.get(pq.breakdown or "department", DIMENSIONS["department"])
    if pq.breakdown == "source":
        dim_expr, join = "COALESCE(c.recruitment_source, 'Unknown')", ""
    tenure = days_between("COALESCE(e.exit_date, :x)", "e.joining_date", ctx.engine)
    observed = days_between(":x", "e.joining_date", ctx.engine)
    sql = _fmt_sql(f"""
SELECT {dim_expr} AS segment,
       SUM(CASE WHEN {observed} >= 90 THEN 1 ELSE 0 END) AS eligible_joiners,
       SUM(CASE WHEN {observed} >= 90 AND e.exit_date IS NOT NULL AND {tenure} < 90 THEN 1 ELSE 0 END) AS left_within_90d
FROM mart_employee_month m
JOIN employees e ON e.employee_id = m.employee_id {join}
LEFT JOIN candidates c ON c.associated_employee_id = e.employee_id AND c.final_disposition = 'Joined'
WHERE m.joined_in_month = 1 AND e.joining_date BETWEEN :s AND :e {seg}
GROUP BY {dim_expr}""")
    df = ctx.run(sql, params)
    df = df[df.eligible_joiners >= 10].copy()
    df["early_attrition_90d"] = df.left_within_90d / df.eligible_joiners
    df["retention_90d"] = 1 - df.early_attrition_90d
    df = df.sort_values("early_attrition_90d", ascending=pq.direction == "lowest")
    total = df.left_within_90d.sum() / max(1, df.eligible_joiners.sum())
    head = (f"**{_pct(total)}** of joiners from {f.start:%b %Y}–{f.end:%b %Y} left within 90 days "
            f"({int(df.left_within_90d.sum())} of {int(df.eligible_joiners.sum())}).")
    comp = (f"Highest: {df.iloc[0].segment} ({_pct(df.iloc[0].early_attrition_90d)}); lowest: "
            f"{df.iloc[-1].segment} ({_pct(df.iloc[-1].early_attrition_90d)})." if len(df) > 1 else "")
    return Evidence("early_attrition", "Early attrition (90 days)", "early_attrition", sql, params, df,
                    {"type": "bar", "x": "segment", "y": "early_attrition_90d", "pct": True}, head, comp,
                    "Joiners observed for at least 90 days who left within 90 days (right-censoring handled).",
                    "Segments with fewer than 10 eligible joiners are hidden; the cohort window ends 3 months "
                    "before the data end so every joiner can be observed for 90 days.")


def _recruitment_scope(ctx: Context, f: Filters, params: dict) -> tuple[str, dict]:
    seg, params = f.segment_sql("r", params, columns={"departments": "department_id", "locations": "location_id",
                                                      "levels": "job_level", "employment_types": None,
                                                      "business_units": None})
    if f.business_units:
        frag, params = expand_in(" AND d.business_unit IN :bu", params, "bu", list(f.business_units))
        seg += frag
    return seg, params


def time_to_hire(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = _recruitment_scope(ctx, f, {"s": (pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(),
                                              "e": f.end})
    days = days_between("a.event_date", "c.application_date", ctx.engine)
    dim = {"source": "c.recruitment_source", "department": "d.department_name", "job_family": "r.job_family",
           "job_level": "r.job_level"}.get(pq.breakdown or "source", "c.recruitment_source")
    sql = _fmt_sql(f"""
SELECT {dim} AS segment, {days} AS days_to_hire
FROM recruitment_events a
JOIN candidates c ON c.candidate_id = a.candidate_id
JOIN job_requisitions r ON r.job_requisition_id = a.job_requisition_id
JOIN departments d ON d.department_id = r.department_id
WHERE a.event_type = 'Offer Accepted' AND a.event_date BETWEEN :s AND :e {seg}""")
    raw = ctx.run(sql, params)
    df = raw.groupby("segment").days_to_hire.agg(hires="size", median_days="median",
                                                 p75_days=lambda s: s.quantile(0.75)).reset_index()
    df = df.sort_values("median_days")
    head = f"Median time to hire: **{raw.days_to_hire.median():.0f} days** across {len(raw):,} accepted offers."
    comp = "By segment: " + ", ".join(f"{r.segment} {r.median_days:.0f}d" for r in df.itertuples()) + "."
    return Evidence("time_to_hire", "Median time to hire", "time_to_hire",
                    sql + "\n-- median and 75th percentile computed per segment in pandas", params, df,
                    {"type": "bar", "x": "segment", "y": "median_days"}, head, comp,
                    "Days from the hired candidate's application to offer acceptance (median, because the "
                    "distribution is right-skewed).", "Excludes the notice period before joining.")


def time_to_fill(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = _recruitment_scope(ctx, f, {"s": (pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(),
                                              "e": f.end})
    days = days_between("r.offer_accepted_date", "r.opened_date", ctx.engine)
    dim = {"department": "d.department_name", "job_level": "r.job_level", "source": "r.job_family"}.get(
        pq.breakdown or "job_family", "r.job_family")
    sql = _fmt_sql(f"""
SELECT {dim} AS segment, {days} AS days_to_fill
FROM job_requisitions r
JOIN departments d ON d.department_id = r.department_id
WHERE r.offer_accepted_date BETWEEN :s AND :e {seg}""")
    raw = ctx.run(sql, params)
    df = raw.groupby("segment").days_to_fill.agg(filled="size", median_days="median").reset_index()
    df = df.sort_values("median_days", ascending=False)
    head = f"Median time to fill: **{raw.days_to_fill.median():.0f} days** ({len(raw):,} filled requisitions)."
    comp = f"Slowest: {df.iloc[0].segment} ({df.iloc[0].median_days:.0f} days)." if len(df) else ""
    return Evidence("time_to_fill", "Median time to fill", "time_to_fill", sql, params, df,
                    {"type": "bar", "x": "segment", "y": "median_days"}, head, comp,
                    "Days from requisition opening to offer acceptance for filled requisitions.",
                    "Still-open requisitions are excluded here, which biases the median down; the Predictive "
                    "Intelligence module gives censoring-aware (Kaplan-Meier) estimates.")


def funnel(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = _recruitment_scope(ctx, f, {"s": (pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(),
                                              "e": f.end})
    sql = _fmt_sql(f"""
SELECT c.furthest_stage, c.final_disposition, COUNT(*) AS candidates
FROM candidates c
JOIN job_requisitions r ON r.job_requisition_id = c.job_requisition_id
JOIN departments d ON d.department_id = r.department_id
WHERE c.application_date BETWEEN :s AND :e {seg}
GROUP BY c.furthest_stage, c.final_disposition""")
    raw = ctx.run(sql, params)
    order = ["Applied", "Screened", "Interviewed", "Offered", "Accepted", "Joined"]
    rank = {s: k for k, s in enumerate(order)}
    raw["rank"] = raw.furthest_stage.map(rank)
    rows = [dict(stage=s, candidates=int(raw[raw["rank"] >= k].candidates.sum())) for k, s in enumerate(order)]
    df = pd.DataFrame(rows)
    df["conversion_from_previous"] = df.candidates / df.candidates.shift(1)
    acc = raw[raw.furthest_stage.isin(["Accepted", "Joined"])].candidates.sum()
    dec = raw[raw.final_disposition == "Offer Declined"].candidates.sum()
    oar = safe_div(acc, acc + dec)
    worst = df.iloc[1:].sort_values("conversion_from_previous").iloc[0]
    head = (f"{int(df.candidates.iloc[0]):,} applications → {int(df.candidates.iloc[-1]):,} joined "
            f"({_pct(df.candidates.iloc[-1] / max(1, df.candidates.iloc[0]))}). Offer acceptance rate: **{_pct(oar)}**.")
    comp = f"Largest drop-off: into **{worst.stage}** ({_pct(worst.conversion_from_previous)} convert)."
    return Evidence("funnel", "Recruitment funnel", "stage_conversion", sql, params, df,
                    {"type": "funnel", "x": "candidates", "y": "stage"}, head, comp,
                    "Candidate cohort by application date; each stage counts candidates who reached it.",
                    "Recent applicants still in process are counted at their current stage.")


def source_effectiveness(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = _recruitment_scope(ctx, f, {"s": (pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(),
                                              "e": f.end})
    sql = _fmt_sql(f"""
SELECT c.recruitment_source AS source, COUNT(*) AS applications,
       SUM(CASE WHEN c.furthest_stage IN ('Offered','Accepted','Joined') THEN 1 ELSE 0 END) AS offers,
       SUM(CASE WHEN c.final_disposition = 'Joined' THEN 1 ELSE 0 END) AS joined
FROM candidates c
JOIN job_requisitions r ON r.job_requisition_id = c.job_requisition_id
JOIN departments d ON d.department_id = r.department_id
WHERE c.application_date BETWEEN :s AND :e {seg}
GROUP BY c.recruitment_source""")
    df = ctx.run(sql, params)
    df["join_yield"] = df.joined / df.applications
    df = df.sort_values("join_yield", ascending=False)
    head = (f"Best join yield: **{df.iloc[0].source}** ({_pct(df.iloc[0].join_yield)} of applicants joined); "
            f"weakest: **{df.iloc[-1].source}** ({_pct(df.iloc[-1].join_yield)}).")
    comp = f"Highest volume: {df.sort_values('applications').iloc[-1].source} ({int(df.applications.max()):,} applications)."
    return Evidence("source_effectiveness", "Recruitment source effectiveness", "stage_conversion", sql, params, df,
                    {"type": "bar", "x": "source", "y": "join_yield", "pct": True}, head, comp,
                    "Sources are ranked by conversion to joiners, not raw volume.",
                    "Source mix differs by role type; compare within job family before reallocating budget.")


def plan_vs_actual(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = f.segment_sql("b", columns={"locations": None, "levels": None, "employment_types": None,
                                              "business_units": None})
    if f.business_units:
        frag, params = expand_in(" AND d.business_unit IN :bu", params, "bu", list(f.business_units))
        seg += frag
    params["d"] = f.end
    sql = _fmt_sql(f"""
SELECT d.department_name AS segment, b.planned_headcount, COALESCE(a.hc, 0) AS actual_headcount
FROM workforce_budget b
JOIN departments d ON d.department_id = b.department_id
LEFT JOIN (SELECT department_id, SUM(active_flag) AS hc FROM mart_employee_month
           WHERE snapshot_date = :d GROUP BY department_id) a ON a.department_id = b.department_id
WHERE b.scenario_name = 'AOP' AND b.planning_period = :d {seg}""")
    df = ctx.run(sql, params)
    df["variance"] = df.actual_headcount - df.planned_headcount
    df["variance_pct"] = df.variance / df.planned_headcount
    df = df.sort_values("variance")
    tot_p, tot_a = df.planned_headcount.sum(), df.actual_headcount.sum()
    head = (f"At {f.end:%b %Y}: actual **{tot_a:,}** vs plan **{tot_p:,}** ({tot_a - tot_p:+,}, "
            f"{_pct(safe_div(tot_a - tot_p, tot_p))}).")
    behind = df[df.variance < 0]
    comp = (f"Largest shortfall: **{df.iloc[0].segment}** ({int(df.iloc[0].variance):+,}, "
            f"{_pct(df.iloc[0].variance_pct)}). {len(behind)} of {len(df)} departments are behind plan."
            if len(df) else "")
    caveat = "Plans are set by department; location, level and employment filters cannot be applied to them."
    if pq.locations or pq.levels:
        caveat += " Those filters were ignored for this answer."
    return Evidence("plan_vs_actual", "Headcount vs plan", "headcount_vs_plan", sql, params, df,
                    {"type": "bar", "x": "segment", "y": "variance"}, head, comp,
                    "Actual month-end headcount minus the approved operating plan (AOP) for that month.", caveat)


def budget_variance(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = f.segment_sql("b", columns={"locations": None, "levels": None, "employment_types": None,
                                              "business_units": None})
    if f.business_units:
        frag, params = expand_in(" AND d.business_unit IN :bu", params, "bu", list(f.business_units))
        seg += frag
    params.update(s=f.start, e=f.end)
    sql = _fmt_sql(f"""
SELECT d.department_name AS segment, SUM(b.planned_people_cost) AS planned_cost, SUM(COALESCE(a.cost, 0)) AS actual_cost
FROM workforce_budget b
JOIN departments d ON d.department_id = b.department_id
LEFT JOIN (SELECT snapshot_date, department_id, SUM(monthly_total_cost) AS cost FROM mart_employee_month
           GROUP BY snapshot_date, department_id) a
       ON a.snapshot_date = b.planning_period AND a.department_id = b.department_id
WHERE b.scenario_name = 'AOP' AND b.planning_period BETWEEN :s AND :e {seg}
GROUP BY d.department_name""")
    df = ctx.run(sql, params)
    df["variance"] = df.actual_cost - df.planned_cost
    df["variance_pct"] = df.variance / df.planned_cost
    df = df.sort_values("variance", ascending=False)
    tp, ta = df.planned_cost.sum(), df.actual_cost.sum()
    head = (f"People cost {pq.period_label}: actual **{_inr(ta)}** vs plan **{_inr(tp)}** "
            f"({_inr(ta - tp)}, {_pct(safe_div(ta - tp, tp))}).")
    comp = (f"Largest overspend: {df.iloc[0].segment} ({_inr(df.iloc[0].variance)}, {_pct(df.iloc[0].variance_pct)}); "
            f"largest underspend: {df.iloc[-1].segment} ({_inr(df.iloc[-1].variance)})." if len(df) > 1 else "")
    return Evidence("budget_variance", "People-cost budget variance", "budget_variance", sql, params, df,
                    {"type": "bar", "x": "segment", "y": "variance"}, head, comp,
                    "Actual simulated employer cost (base + variable at target + benefits, pro-rated) minus the "
                    "planned people cost.", "Fictional INR. Underspend usually reflects unfilled positions.")


def turnover_cost(pq: ParsedQuestion, ctx: Context) -> Evidence:
    from src.analytics.compensation import exit_costs_base, observed_defaults, turnover_summary

    f = ctx.filters(pq)
    base = exit_costs_base(f, ctx.engine)
    a = observed_defaults(ctx.engine)
    s = turnover_summary(base, a)
    df = s["by_department"]
    head = (f"Estimated turnover cost of **{s['exits']:,} voluntary exits** in {pq.period_label}: "
            f"**{_inr(s['total'])}** (≈ {_inr(s['per_exit'])} per exit).")
    comp = ", ".join(f"{k.replace('_', ' ')} {_inr(v)}" for k, v in s["components"].items())
    seg, params = f.segment_sql("m")
    sql = _fmt_sql(f"""
SELECT m.department_id, d.department_name, m.job_level, m.exit_type, COUNT(*) AS exits
FROM mart_employee_month m JOIN departments d ON d.department_id = m.department_id
WHERE m.exited_in_month = 1 AND m.snapshot_date BETWEEN :s AND :e {seg}
GROUP BY m.department_id, d.department_name, m.job_level, m.exit_type
-- x (cost per hire + onboarding + ramp-up + vacancy) from the turnover-cost model""")
    return Evidence("turnover_cost", "Estimated turnover cost", "turnover_cost", sql, dict(params, s=f.start, e=f.end),
                    df, {"type": "bar", "x": "department_name", "y": "total_cost"}, head, "Components: " + comp + ".",
                    f"Assumptions: cost per hire {_inr(a.cost_per_hire)} (observed), onboarding {_inr(a.onboarding_cost)}, "
                    f"{a.ramp_months:.0f}-month ramp at {a.ramp_productivity_loss:.0%} lost output, "
                    f"{a.vacancy_days:.0f} vacancy days at {a.vacancy_coverage_gap:.0%} uncovered.",
                    "An estimate, not an accounting loss; adjust assumptions in Planning & Business Insights.",
                    source="SQL template + turnover-cost model")


def engagement(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    if "last 12 months" in pq.period_label and "four quarters" in pq.text.lower():
        f = ctx.filters(pq, start=add_months(ctx.data_end, -11))
    seg, params = f.segment_sql("s", columns={"locations": None, "levels": None, "employment_types": None,
                                              "business_units": None})
    if f.business_units:
        frag, params = expand_in(" AND d.business_unit IN :bu", params, "bu", list(f.business_units))
        seg += frag
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    by = "d.department_name, " if pq.breakdown == "department" else ""
    sql = _fmt_sql(f"""
SELECT {by}s.survey_period, COUNT(*) AS responses, AVG(s.engagement_score) AS avg_engagement,
       SUM(CASE WHEN s.enps_response >= 9 THEN 1 ELSE 0 END) AS promoters,
       SUM(CASE WHEN s.enps_response <= 6 THEN 1 ELSE 0 END) AS detractors
FROM engagement_surveys s JOIN departments d ON d.department_id = s.department_id
WHERE s.survey_date BETWEEN :s AND :e {seg}
GROUP BY {by}s.survey_period
HAVING COUNT(*) >= 10
ORDER BY s.survey_period""")
    df = ctx.run(sql, params)
    df["enps"] = (df.promoters - df.detractors) / df.responses * 100
    df = df.drop(columns=["promoters", "detractors"])
    if pq.breakdown == "department":
        last = df[df.survey_period == df.survey_period.max()].sort_values("avg_engagement")
        head = (f"Lowest engagement in {last.survey_period.iloc[0]}: **{last.iloc[0].department_name}** "
                f"({last.iloc[0].avg_engagement:.2f}/5); highest: **{last.iloc[-1].department_name}** "
                f"({last.iloc[-1].avg_engagement:.2f}).")
        comp = ""
        chart = {"type": "bar", "x": "department_name", "y": "avg_engagement"}
        df = last
    else:
        first, last = df.iloc[0], df.iloc[-1]
        head = (f"Engagement moved from **{first.avg_engagement:.2f}** ({first.survey_period}) to "
                f"**{last.avg_engagement:.2f}** ({last.survey_period}); eNPS {first.enps:+.0f} → {last.enps:+.0f}.")
        comp = "By wave: " + ", ".join(f"{r.survey_period} {r.avg_engagement:.2f}" for r in df.itertuples()) + "."
        chart = {"type": "line", "x": "survey_period", "y": "avg_engagement"}
    return Evidence("engagement", "Engagement trend", "engagement_score", sql, params, df, chart, head, comp,
                    "Mean engagement index (1–5) and eNPS (%promoters 9–10 minus %detractors 0–6).",
                    "Groups under 10 responses are suppressed; disengaged employees respond less often.")


def forecast(pq: ParsedQuestion, ctx: Context) -> Evidence:
    from src.models.workforce_forecast import load_meta

    meta = load_meta()
    if not meta:
        return Evidence("forecast", "Headcount forecast", None, None, {}, pd.DataFrame(),
                        headline="The forecasting model has not been trained yet. Run `python scripts/train_models.py`.",
                        source="model artefact")
    fc = pd.DataFrame(meta["forecast"]).head(pq.horizon_months)
    df = fc[["month", "forecast", "lower_80", "upper_80", "expected_exits", "expected_hires"]].copy()
    if pq.departments or pq.business_units:
        d = pd.DataFrame(meta["department_forecast"])
        names = pq.departments or [n for k, n in ctx.lookups["departments"].items()
                                   if ctx.lookups["dept_bu"].get(k) in pq.business_units]
        d = d[d.department_name.isin(names)]
        df = d.groupby("month")[["forecast_headcount", "planned_headcount", "expected_exits"]].sum().reset_index()
        df = df.head(pq.horizon_months)
        head = (f"Forecast headcount for {', '.join(names)} at {pd.Timestamp(df.month.iloc[-1]):%b %Y}: "
                f"**{df.forecast_headcount.iloc[-1]:,.0f}** (plan {df.planned_headcount.iloc[-1]:,.0f}).")
        chart = {"type": "line", "x": "month", "y": ["forecast_headcount", "planned_headcount"]}
    else:
        last = df.iloc[-1]
        head = (f"Forecast headcount at {pd.Timestamp(last.month):%b %Y}: **{last.forecast:,.0f}** "
                f"(80% interval {last.lower_80:,.0f}–{last.upper_80:,.0f}).")
        chart = {"type": "line", "x": "month", "y": ["forecast", "lower_80", "upper_80"]}
    sel = meta["selected_model"]
    mae = next(m["mae"] for m in meta["model_comparison"] if m["model"] == sel)
    naive = next((m["mae"] for m in meta["model_comparison"] if m["model"] == "Naive"), None)
    comp = (f"Model: {sel}, chosen on rolling-origin backtests (MAE {mae:,.0f} heads"
            + (f" vs {naive:,.0f} for the naive baseline" if naive else "") + ").")
    return Evidence("forecast", "Headcount forecast", None, None, {}, df, chart, head, comp,
                    "Forecast from the validated forecasting artefact trained on monthly headcount and the known "
                    f"offer pipeline (trained {meta['trained_at'][:10]}).",
                    "Assumes current hiring pace and seasonal exit patterns continue; use scenarios for what-ifs.",
                    source="model artefact (workforce_forecast.json)")


def drivers(pq: ParsedQuestion, ctx: Context) -> Evidence:
    from src.models.attrition import load_meta

    meta = load_meta()
    if not meta:
        return Evidence("drivers", "Attrition drivers", None, None, {}, pd.DataFrame(),
                        headline="The attrition model has not been trained yet. Run `python scripts/train_models.py`.",
                        source="model artefact")
    co = pd.DataFrame(meta["coefficients"])
    co = co[co.feature.str.startswith("num__")].copy()
    co["direction"] = np.where(co.coefficient > 0, "higher risk", "lower risk")
    co = co.reindex(co.coefficient.abs().sort_values(ascending=False).index).head(8)
    res = meta["results"][meta["primary_model"]]
    top = co.iloc[:3]
    head = "Strongest associations with voluntary exit in the next 6 months: " + "; ".join(
        f"**{r.label}** ({r.direction}, odds ×{r.odds_ratio:.2f} per SD)" for r in top.itertuples()) + "."
    comp = (f"Model ROC-AUC on the {meta['test_date']} hold-out: {res['roc_auc']:.2f} "
            f"(0.5 = no better than chance).")
    return Evidence("drivers", "Attrition drivers", None, None, {}, co[["label", "coefficient", "odds_ratio", "direction"]],
                    {"type": "bar", "x": "label", "y": "coefficient"}, head, comp,
                    "Standardised logistic-regression coefficients from the validated attrition model.",
                    "Associations in synthetic data, not causes. Do not use for decisions about individuals.",
                    source="model artefact (attrition_model.json)")


def hiring_demand(pq: ParsedQuestion, ctx: Context) -> Evidence:
    from src.models.workforce_forecast import load_meta

    meta = load_meta()
    if not meta:
        return Evidence("hiring_demand", "Hiring demand", None, None, {}, pd.DataFrame(),
                        headline="The forecasting model has not been trained yet.", source="model artefact")
    df = pd.DataFrame(meta["hiring_demand"])
    if pq.departments:
        df = df[df.department_name.isin(pq.departments)]
    over = df[df.demand_exceeds_plan].assign(excess=lambda d: d.hires_required - d.planned_hires)
    over = over.sort_values("excess", ascending=False)
    cols = ["department_name", "current_headcount", "planned_headcount_end", "expected_exits", "hires_required",
            "planned_hires", "covered_by_pipeline", "additional_requisitions"]
    head = (f"In **{len(over)}** of {len(df)} departments the hires needed over the next {meta['horizon_months']} "
            f"months exceed the hires budgeted in the plan; largest excess: **{over.iloc[0].department_name}** "
            f"({over.iloc[0].hires_required:.0f} needed vs {over.iloc[0].planned_hires:.0f} planned)." if len(over)
            else "No department needs more hires than its plan budgets for the forecast horizon.")
    return Evidence("hiring_demand", "Hiring demand vs plan", None, None, {}, (over if len(over) else df)[cols],
                    {"type": "bar", "x": "department_name", "y": "hires_required"}, head,
                    f"Total hires required: {df.hires_required.sum():,.0f} vs {df.planned_hires.sum():,.0f} planned; "
                    f"already covered by pipeline: {df.covered_by_pipeline.sum():,.0f}.",
                    "Hires required = plan headcount at horizon end − current headcount + expected exits. "
                    "Planned hires = hires budgeted in the AOP for the same months.",
                    "Expected exits come from the forecast model; higher-than-planned attrition is the usual cause "
                    "of excess demand.", source="model artefact (workforce_forecast.json)")


def compensation(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    dim = pq.breakdown if pq.breakdown in ("department", "job_level", "location", "business_unit") else "job_level"
    select = ("SUM(m.monthly_total_cost) AS workforce_cost, SUM(m.monthly_base_cost) AS base_cost, "
              "SUM(m.active_flag) AS headcount_months")
    sql, params = _mart_breakdown(ctx, f, dim, select)
    df = ctx.run(sql, params)
    n = f.months
    df["avg_headcount"] = df.headcount_months / n
    df["cost_per_employee_annual"] = df.workforce_cost / df.avg_headcount * 12 / n
    df = df.sort_values("segment" if dim == "job_level" else "workforce_cost", ascending=dim == "job_level")
    total = df.workforce_cost.sum()
    head = (f"Workforce cost {pq.period_label}: **{_inr(total)}**; average annual cost per employee "
            f"**{_inr(total / df.avg_headcount.sum() * 12 / n)}**.")
    comp = "Cost per employee by " + dim.replace("_", " ") + ": " + ", ".join(
        f"{r.segment} {_inr(r.cost_per_employee_annual)}" for r in df.head(6).itertuples()) + "."
    return Evidence("compensation", "Workforce cost", "workforce_cost", sql, params,
                    df[["segment", "avg_headcount", "workforce_cost", "cost_per_employee_annual"]],
                    {"type": "bar", "x": "segment", "y": "cost_per_employee_annual"}, head, comp,
                    "Total employer cost (base + variable at target + benefits), pro-rated by days employed.",
                    "Individual salaries are never shown in Ask PeopleLens; figures are fictional INR.")


def performance(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = f.segment_sql("m")
    from database.connection import month_key

    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    sql = _fmt_sql(f"""
SELECT r.review_cycle, r.performance_rating, COUNT(*) AS reviews
FROM performance_reviews r
JOIN mart_employee_month m ON m.employee_id = r.employee_id
     AND {month_key('m.snapshot_date', ctx.engine)} = {month_key('r.review_date', ctx.engine)}
WHERE r.review_completion_status = 'Completed' AND r.review_date BETWEEN :s AND :e {seg}
GROUP BY r.review_cycle, r.performance_rating ORDER BY r.review_cycle, r.performance_rating""")
    df = ctx.run(sql, params)
    if df.empty:
        f2 = ctx.filters(pq, start=add_months(ctx.data_end, -17))
        params.update(s=(pd.Timestamp(f2.start) - pd.offsets.MonthBegin(1)).date())
        df = ctx.run(sql, params)
    df["share"] = df.reviews / df.groupby("review_cycle").reviews.transform("sum")
    last = df[df.review_cycle == df.review_cycle.max()]
    high = last[last.performance_rating >= 4].share.sum()
    low = last[last.performance_rating <= 2].share.sum()
    head = (f"{last.review_cycle.iloc[0]} cycle: **{_pct(high)}** rated 4–5 and **{_pct(low)}** rated 1–2 "
            f"({int(last.reviews.sum()):,} completed reviews).") if len(last) else "No completed reviews in range."
    return Evidence("performance", "Performance rating distribution", "rating_distribution", sql, params, df,
                    {"type": "bar", "x": "performance_rating", "y": "share", "color": "review_cycle", "pct": True},
                    head, "", "Share of completed reviews at each rating (annual April cycle).",
                    "Ratings reflect manager calibration.")


def promotion(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    n = f.months
    dim = pq.breakdown or ("department" if pq.direction else None)
    if dim == "job_level":
        # attribute promotions to the level the employee was promoted *from*
        seg, params = f.segment_sql("m")
        params.update(s=f.start, e=f.end)
        from database.connection import month_key

        mk_e, mk_m = month_key("e.event_date", ctx.engine), month_key("m.snapshot_date", ctx.engine)
        sql = _fmt_sql(f"""
SELECT e.previous_job_level AS segment, COUNT(*) AS promotions
FROM employee_events e
JOIN mart_employee_month m ON m.employee_id = e.employee_id AND {mk_m} = {mk_e}
WHERE e.event_type = 'Promotion' AND m.snapshot_date BETWEEN :s AND :e {seg}
GROUP BY e.previous_job_level""")
        promo = ctx.run(sql, params)
        hc_sql, hp = _mart_breakdown(ctx, f, "job_level", "SUM(m.active_flag) AS headcount_months")
        hc = ctx.run(hc_sql, hp)
        df = hc.merge(promo, on="segment", how="left").fillna({"promotions": 0})
        df["avg_headcount"] = df.headcount_months / n
        df["promotion_rate"] = [attrition_rate(p, h, n) for p, h in zip(df.promotions, df.avg_headcount)]
        df = df.sort_values("segment")
        top = df.sort_values("promotion_rate", ascending=pq.direction == "lowest").iloc[0]
        head = (f"{'Lowest' if pq.direction == 'lowest' else 'Highest'} promotion rate: **{top.segment}** "
                f"({_pct(top.promotion_rate)} of that level promoted, annualised).")
        comp = "By level promoted from: " + ", ".join(f"{r.segment} {_pct(r.promotion_rate)}" for r in df.itertuples()
                                                      if r.segment not in ("L8", "L9")) + "."
        return Evidence("promotion", "Promotion rate by level", "promotion_rate", sql, params,
                        df[["segment", "avg_headcount", "promotions", "promotion_rate"]],
                        {"type": "bar", "x": "segment", "y": "promotion_rate", "pct": True}, head, comp,
                        "Promotions out of each level divided by that level's average headcount, annualised.",
                        "Promotions concentrate in the April cycle.")
    select = ("SUM(m.promoted_in_month) AS promotions, SUM(m.transferred_in) AS transfers, "
              "SUM(m.active_flag) AS headcount_months")
    sql, params = _mart_breakdown(ctx, f, dim, select)
    df = ctx.run(sql, params)
    df["avg_headcount"] = df.headcount_months / n
    df["promotion_rate"] = [attrition_rate(p, h, n) for p, h in zip(df.promotions, df.avg_headcount)]
    df["mobility_rate"] = [attrition_rate(t, h, n) for t, h in zip(df.transfers, df.avg_headcount)]
    if dim:
        df = df[df.avg_headcount >= 5].sort_values("promotion_rate", ascending=pq.direction == "lowest")
        head = (f"{'Lowest' if pq.direction == 'lowest' else 'Highest'} promotion rate: **{df.iloc[0].segment}** "
                f"({_pct(df.iloc[0].promotion_rate)} annualised).")
    else:
        r = df.iloc[0]
        head = (f"**{int(r.promotions):,}** promotions in {pq.period_label}: an annualised promotion rate of "
                f"**{_pct(r.promotion_rate)}**; internal mobility {_pct(r.mobility_rate)}.")
    return Evidence("promotion", "Promotion and mobility", "promotion_rate", sql, params, df,
                    {"type": "bar", "x": "segment", "y": "promotion_rate", "pct": True} if dim else {}, head, "",
                    "Promotions (or transfers) divided by average headcount, annualised.",
                    "Promotions concentrate in the April cycle.")


def span_of_control(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = f.segment_sql("m")
    params["d"] = f.end
    sql = _fmt_sql(f"""
SELECT m.manager_id, COUNT(*) AS direct_reports
FROM mart_employee_month m
WHERE m.snapshot_date = :d AND m.active_flag = 1 AND m.manager_id IS NOT NULL {seg}
GROUP BY m.manager_id""")
    raw = ctx.run(sql, params)
    df = raw.direct_reports.describe(percentiles=[0.25, 0.5, 0.75, 0.9]).rename("value").reset_index()
    df.columns = ["statistic", "direct_reports"]
    head = (f"Median span of control: **{raw.direct_reports.median():.0f}** direct reports across "
            f"{len(raw):,} managers; {int((raw.direct_reports >= 12).sum())} managers have 12 or more.")
    return Evidence("span_of_control", "Span of control", "span_of_control", sql, params, df, {}, head, "",
                    "Active direct reports per manager at month end.", "Direct reports only (not whole teams).")


def exit_reasons(pq: ParsedQuestion, ctx: Context) -> Evidence:
    f = ctx.filters(pq)
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    sql = _fmt_sql(f"""
SELECT e.exit_type, e.exit_reason_category AS reason, COUNT(*) AS exits
FROM mart_employee_month m
JOIN employee_events e ON e.employee_id = m.employee_id AND e.event_type = 'Exit'
WHERE m.exited_in_month = 1 AND m.snapshot_date BETWEEN :s AND :e {seg}
GROUP BY e.exit_type, e.exit_reason_category ORDER BY exits DESC""")
    df = ctx.run(sql, params)
    df["share"] = df.exits / df.exits.sum()
    head = (f"Most common exit reason in {pq.period_label}: **{df.iloc[0].reason}** ({_pct(df.iloc[0].share)} of "
            f"{int(df.exits.sum()):,} exits)." if len(df) else "No exits in scope.")
    return Evidence("exit_reasons", "Exit reasons", "exits", sql, params, df,
                    {"type": "bar", "x": "reason", "y": "exits", "color": "exit_type"}, head, "",
                    "Primary reason recorded at separation.",
                    "Self-reported reasons are often incomplete; use alongside drivers analysis.")


HANDLERS = {
    "headcount": headcount, "joiners": joiners, "attrition": attrition, "early_attrition": early_attrition,
    "time_to_hire": time_to_hire, "time_to_fill": time_to_fill, "funnel": funnel,
    "source_effectiveness": source_effectiveness, "plan_vs_actual": plan_vs_actual,
    "budget_variance": budget_variance, "turnover_cost": turnover_cost, "engagement": engagement,
    "forecast": forecast, "drivers": drivers, "hiring_demand": hiring_demand, "compensation": compensation,
    "performance": performance, "promotion": promotion, "span_of_control": span_of_control,
    "exit_reasons": exit_reasons,
}
INTENT_LABELS = {
    "headcount": "Headcount", "joiners": "Joiners / hiring volume", "attrition": "Attrition",
    "early_attrition": "Retention / early attrition", "time_to_hire": "Time to hire", "time_to_fill": "Time to fill",
    "funnel": "Recruitment funnel", "source_effectiveness": "Recruitment source effectiveness",
    "plan_vs_actual": "Headcount vs plan", "budget_variance": "Budget variance", "turnover_cost": "Turnover cost",
    "engagement": "Engagement", "forecast": "Workforce forecast", "drivers": "Attrition drivers",
    "hiring_demand": "Hiring demand", "compensation": "Compensation & workforce cost",
    "performance": "Performance", "promotion": "Promotion & mobility", "span_of_control": "Span of control",
    "exit_reasons": "Exit reasons",
}
