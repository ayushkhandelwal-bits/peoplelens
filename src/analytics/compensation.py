"""Manpower cost, compensation and turnover-cost analytics.

All cost figures are simulated (fictional INR). "Workforce cost" is the total
employer cost (base + variable target + benefits) pro-rated by days employed;
see the metric dictionary for the exact definition.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import pandas as pd
from sqlalchemy.engine import Engine

from src.analytics.common import Filters, q, safe_div
from src.analytics.metrics import LEVEL_ORDER, monthly_series, planned


def cost_vs_plan(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    a = monthly_series(f, engine)[["month", "headcount", "workforce_cost", "base_cost"]]
    p = planned(f, engine)[["month", "planned_headcount", "planned_cost"]]
    df = a.merge(p, on="month", how="left")
    df["variance"] = df.workforce_cost - df.planned_cost
    df["variance_pct"] = df.variance / df.planned_cost
    df["cost_per_head"] = df.workforce_cost / df.headcount
    return df


def cost_breakdown(f: Filters, by: str, engine: Engine | None = None) -> pd.DataFrame:
    expr, join = {
        "department": ("d.department_name", "JOIN departments d ON d.department_id = m.department_id"),
        "job_level": ("m.job_level", ""), "business_unit": ("m.business_unit", ""),
        "job_family": ("m.job_family", ""),
        "location": ("l.city", "JOIN locations l ON l.location_id = m.location_id"),
    }[by]
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    df = q(f"""SELECT {expr} AS segment, SUM(m.monthly_total_cost) AS workforce_cost,
                      SUM(m.monthly_base_cost) AS base_cost, SUM(m.active_flag) AS hc_months
               FROM mart_employee_month m {join}
               WHERE m.snapshot_date BETWEEN :s AND :e {seg} GROUP BY {expr}""", params, engine)
    df["avg_headcount"] = df.hc_months / f.months
    df["cost_per_employee"] = df.workforce_cost / df.avg_headcount * 12 / f.months
    df["share"] = df.workforce_cost / df.workforce_cost.sum()
    if by == "job_level":
        df["segment"] = pd.Categorical(df.segment, LEVEL_ORDER, ordered=True)
        return df.sort_values("segment")
    return df.sort_values("workforce_cost", ascending=False)


def salary_trend(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Average annual base salary of active employees by level and month (compression lens)."""
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    df = q(f"""SELECT m.snapshot_date AS month, m.job_level,
                      AVG(m.monthly_base_cost * 12) AS avg_base_salary, COUNT(*) AS n
               FROM mart_employee_month m
               WHERE m.snapshot_date BETWEEN :s AND :e AND m.active_flag = 1 AND m.joined_in_month = 0 {seg}
               GROUP BY m.snapshot_date, m.job_level""", params, engine, dates=["month"])
    return df


def compensation_growth(f: Filters, engine: Engine | None = None) -> dict:
    s = monthly_series(f, engine)
    if len(s) < 2:
        return {}
    first, last = s.iloc[0], s.iloc[-1]
    cph_first = safe_div(first.workforce_cost, first.headcount)
    cph_last = safe_div(last.workforce_cost, last.headcount)
    return dict(cost_per_head_start=cph_first, cost_per_head_end=cph_last,
                growth=safe_div((cph_last or 0) - (cph_first or 0), cph_first))


def budget_variance_by_department(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Cumulative actual vs planned cost and month-end headcount vs plan for the period."""
    seg, params = f.segment_sql("b", columns={"locations": None, "levels": None, "employment_types": None,
                                              "business_units": None})
    params.update(s=f.start, e=f.end)
    df = q(f"""
        SELECT d.department_name, d.business_unit, SUM(b.planned_people_cost) AS planned_cost,
               SUM(COALESCE(a.cost, 0)) AS actual_cost
        FROM workforce_budget b JOIN departments d ON d.department_id = b.department_id
        LEFT JOIN (SELECT snapshot_date, department_id, SUM(monthly_total_cost) AS cost
                   FROM mart_employee_month GROUP BY snapshot_date, department_id) a
               ON a.snapshot_date = b.planning_period AND a.department_id = b.department_id
        WHERE b.scenario_name = 'AOP' AND b.planning_period BETWEEN :s AND :e {seg}
        GROUP BY d.department_name, d.business_unit""", params, engine)
    if f.business_units:
        df = df[df.business_unit.isin(f.business_units)]
    df["variance"] = df.actual_cost - df.planned_cost
    df["variance_pct"] = df.variance / df.planned_cost
    return df.sort_values("variance", ascending=False)


def recruitment_and_training_spend(f: Filters, engine: Engine | None = None) -> dict:
    seg, params = f.segment_sql("r", columns={"locations": "location_id", "levels": "job_level",
                                              "employment_types": None, "business_units": None})
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    rec = q(f"""SELECT COALESCE(SUM(recruitment_cost), 0) AS cost, COUNT(*) AS n FROM job_requisitions r
                WHERE r.requisition_status = 'Filled' AND r.offer_accepted_date BETWEEN :s AND :e {seg}""",
            params, engine)
    tr = q("""SELECT COALESCE(SUM(training_cost), 0) AS cost FROM training_records
              WHERE enrollment_date BETWEEN :s AND :e""", params, engine)
    return dict(recruitment_cost=float(rec.cost.iloc[0]), filled_requisitions=int(rec.n.iloc[0]),
                cost_per_hire=safe_div(rec.cost.iloc[0], rec.n.iloc[0]), training_cost=float(tr.cost.iloc[0]))


# ------------------------------------------------------------ turnover cost

@dataclass(frozen=True)
class TurnoverAssumptions:
    """Transparent, configurable assumptions for estimating replacement cost."""

    cost_per_hire: float = 60_000.0  # replaced by the observed average when available
    onboarding_cost: float = 25_000.0  # induction, equipment, buddy time
    ramp_months: float = 3.0  # months until a replacement is fully productive
    ramp_productivity_loss: float = 0.5  # average output shortfall during ramp-up
    vacancy_days: float = 75.0  # replaced by observed median time-to-fill + notice when available
    vacancy_coverage_gap: float = 0.3  # share of the role's value not covered while vacant
    include_involuntary: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


SENSITIVITY_RANGES = {
    "cost_per_hire": (0.6, 1.5), "onboarding_cost": (0.6, 1.6), "ramp_months": (2 / 3, 2.0),
    "ramp_productivity_loss": (0.6, 1.2), "vacancy_days": (0.7, 1.4), "vacancy_coverage_gap": (0.0, 1.67),
}


def observed_defaults(engine: Engine | None = None, f: Filters | None = None) -> TurnoverAssumptions:
    params = {}
    where = ""
    if f is not None:
        params = {"s": (pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), "e": f.end}
        where = "AND offer_accepted_date BETWEEN :s AND :e"
    r = q(f"""SELECT AVG(recruitment_cost) AS cph FROM job_requisitions
              WHERE requisition_status = 'Filled' {where}""", params, engine)
    ttf = q(f"""SELECT opened_date, offer_accepted_date, closed_date FROM job_requisitions
                WHERE requisition_status = 'Filled' AND closed_date IS NOT NULL {where}""", params, engine,
            dates=["opened_date", "offer_accepted_date", "closed_date"])
    a = TurnoverAssumptions()
    cph = r.cph.iloc[0]
    if cph is not None and not pd.isna(cph):
        a = replace(a, cost_per_hire=round(float(cph), -3))
    if len(ttf):
        # vacancy ~ requisition open -> replacement joins (fill time + notice period)
        a = replace(a, vacancy_days=float((ttf.closed_date - ttf.opened_date).dt.days.median()))
    return a


def exit_costs_base(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Exits in the period with the monthly cost rate of an equivalent role
    (average full-month cost of active staff in the same department and level)."""
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    exits = q(f"""SELECT m.department_id, d.department_name, m.job_level, m.exit_type, COUNT(*) AS exits
                  FROM mart_employee_month m JOIN departments d ON d.department_id = m.department_id
                  WHERE m.exited_in_month = 1 AND m.snapshot_date BETWEEN :s AND :e {seg}
                  GROUP BY m.department_id, d.department_name, m.job_level, m.exit_type""", params, engine)
    rates = q("""SELECT department_id, job_level, AVG(monthly_total_cost) AS monthly_rate
                 FROM mart_employee_month WHERE active_flag = 1 AND joined_in_month = 0
                 AND snapshot_date BETWEEN :s AND :e GROUP BY department_id, job_level""",
              {"s": f.start, "e": f.end}, engine)
    lvl = q("""SELECT job_level, AVG(monthly_total_cost) AS lvl_rate FROM mart_employee_month
               WHERE active_flag = 1 AND joined_in_month = 0 AND snapshot_date BETWEEN :s AND :e
               GROUP BY job_level""", {"s": f.start, "e": f.end}, engine)
    df = exits.merge(rates, on=["department_id", "job_level"], how="left").merge(lvl, on="job_level", how="left")
    df["monthly_rate"] = df.monthly_rate.fillna(df.lvl_rate)
    return df.drop(columns=["lvl_rate"])


def turnover_cost(base: pd.DataFrame, a: TurnoverAssumptions) -> pd.DataFrame:
    df = base if a.include_involuntary else base[base.exit_type == "Voluntary"]
    df = df.copy()
    df["recruitment"] = a.cost_per_hire
    df["onboarding"] = a.onboarding_cost
    df["ramp_up"] = a.ramp_months * a.ramp_productivity_loss * df.monthly_rate
    df["vacancy"] = a.vacancy_days / 30.44 * df.monthly_rate * a.vacancy_coverage_gap
    df["cost_per_exit"] = df[["recruitment", "onboarding", "ramp_up", "vacancy"]].sum(axis=1)
    df["total_cost"] = df.cost_per_exit * df.exits
    return df


def turnover_summary(base: pd.DataFrame, a: TurnoverAssumptions) -> dict:
    df = turnover_cost(base, a)
    exits = float(df.exits.sum())
    comp = {c: float((df[c] * df.exits).sum()) for c in ["recruitment", "onboarding", "ramp_up", "vacancy"]}
    by_dept = df.groupby("department_name").agg(exits=("exits", "sum"), total_cost=("total_cost", "sum"))
    by_dept["cost_per_exit"] = by_dept.total_cost / by_dept.exits
    return dict(exits=int(exits), total=float(df.total_cost.sum()),
                per_exit=safe_div(df.total_cost.sum(), exits), components=comp,
                by_department=by_dept.sort_values("total_cost", ascending=False).reset_index())


def sensitivity(base: pd.DataFrame, a: TurnoverAssumptions) -> pd.DataFrame:
    """One-at-a-time sensitivity of total turnover cost to each assumption."""
    total = turnover_summary(base, a)["total"]
    rows = []
    for k, (lo, hi) in SENSITIVITY_RANGES.items():
        v = getattr(a, k)
        t_lo = turnover_summary(base, replace(a, **{k: v * lo}))["total"]
        t_hi = turnover_summary(base, replace(a, **{k: v * hi}))["total"]
        rows.append(dict(assumption=k, base_value=v, low_value=v * lo, high_value=v * hi, low_total=t_lo,
                         high_total=t_hi, swing=abs(t_hi - t_lo), base_total=total))
    return pd.DataFrame(rows).sort_values("swing")
