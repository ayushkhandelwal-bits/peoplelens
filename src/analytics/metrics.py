"""Core KPI calculations — the one implementation of each metric.

Definitions live in ``src/governance/metric_dictionary.py``; this module is
the only place that turns them into numbers. Pages, reports and Ask
PeopleLens all call these functions, so a KPI cannot drift between modules.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy.engine import Engine

from src.analytics.common import Filters, add_months, q, safe_div

TENURE_BAND_SQL = """CASE
    WHEN m.tenure_months < 3 THEN '0-3 months'
    WHEN m.tenure_months < 6 THEN '3-6 months'
    WHEN m.tenure_months < 12 THEN '6-12 months'
    WHEN m.tenure_months < 24 THEN '1-2 years'
    WHEN m.tenure_months < 60 THEN '2-5 years'
    ELSE '5+ years' END"""
TENURE_ORDER = ["0-3 months", "3-6 months", "6-12 months", "1-2 years", "2-5 years", "5+ years"]
LEVEL_ORDER = [f"L{i}" for i in range(1, 10)]


# ---------------------------------------------------------------- formulas

def annualise(rate: float | None, months: int) -> float | None:
    return None if rate is None else rate * 12 / months


def attrition_rate(exits: float, avg_headcount: float, months: int) -> float | None:
    """exits / average headcount x 12 / months (metric: attrition_rate)."""
    return annualise(safe_div(exits, avg_headcount), months)


def poisson_rate_ci(exits: float, avg_headcount: float, months: int, alpha: float = 0.05
                    ) -> tuple[float | None, float | None]:
    """Exact (Garwood) CI for an annualised event rate per person-year."""
    exposure = avg_headcount * months / 12
    if not exposure:
        return None, None
    lo = stats.chi2.ppf(alpha / 2, 2 * exits) / 2 if exits > 0 else 0.0
    hi = stats.chi2.ppf(1 - alpha / 2, 2 * (exits + 1)) / 2
    return lo / exposure, hi / exposure


def enps(responses: pd.Series) -> float | None:
    """eNPS = %promoters (9-10) - %detractors (0-6), x100."""
    r = pd.Series(responses).dropna()
    if r.empty:
        return None
    return float(((r >= 9).mean() - (r <= 6).mean()) * 100)


# ------------------------------------------------------------ base series

def monthly_series(f: Filters, engine: Engine | None = None, start=None, end=None) -> pd.DataFrame:
    seg, params = f.segment_sql("m")
    params.update(s=start or f.start, e=end or f.end)
    df = q(f"""
        SELECT m.snapshot_date AS month,
               SUM(m.active_flag) AS headcount,
               SUM(m.joined_in_month) AS joiners,
               SUM(m.exited_in_month) AS exits,
               SUM(CASE WHEN m.exit_type = 'Voluntary' THEN 1 ELSE 0 END) AS voluntary_exits,
               SUM(CASE WHEN m.exit_type = 'Involuntary' THEN 1 ELSE 0 END) AS involuntary_exits,
               SUM(m.transferred_in) AS transfers_in,
               SUM(m.promoted_in_month) AS promotions,
               SUM(m.monthly_total_cost) AS workforce_cost,
               SUM(m.monthly_base_cost) AS base_cost
        FROM mart_employee_month m
        WHERE m.snapshot_date BETWEEN :s AND :e {seg}
        GROUP BY m.snapshot_date ORDER BY m.snapshot_date""", params, engine, dates=["month"])
    return df


def headcount_at(f: Filters, month, engine: Engine | None = None) -> int:
    seg, params = f.segment_sql("m")
    params["d"] = month
    df = q(f"SELECT SUM(m.active_flag) AS hc FROM mart_employee_month m WHERE m.snapshot_date = :d {seg}",
           params, engine)
    v = df.hc.iloc[0]
    return 0 if v is None or pd.isna(v) else int(v)


def planned(f: Filters, engine: Engine | None = None, start=None, end=None) -> pd.DataFrame:
    """AOP by month for the departments in scope. Plans are set at department
    level, so location/level/employment filters cannot be applied to them."""
    seg, params = f.segment_sql("b", columns={"locations": None, "levels": None, "employment_types": None,
                                              "business_units": None})
    bu = ""
    if f.business_units:
        from database.connection import expand_in

        bu, params = expand_in(" AND d.business_unit IN :bus", params, "bus", list(f.business_units))
    params.update(s=start or f.start, e=end or f.end)
    return q(f"""
        SELECT b.planning_period AS month, SUM(b.planned_headcount) AS planned_headcount,
               SUM(b.planned_hires) AS planned_hires, SUM(b.planned_people_cost) AS planned_cost
        FROM workforce_budget b JOIN departments d ON d.department_id = b.department_id
        WHERE b.scenario_name = 'AOP' AND b.planning_period BETWEEN :s AND :e {seg}{bu}
        GROUP BY b.planning_period ORDER BY b.planning_period""", params, engine, dates=["month"])


# --------------------------------------------------------------- summary

@dataclass
class KPISummary:
    months: int
    opening_headcount: int
    active_headcount: int
    average_headcount: float
    joiners: int
    exits: int
    voluntary_exits: int
    involuntary_exits: int
    attrition_rate: float | None
    voluntary_attrition_rate: float | None
    involuntary_attrition_rate: float | None
    net_growth: float | None
    average_tenure_months: float | None
    promotions: int
    promotion_rate: float | None
    transfers_in: int
    workforce_cost: float
    cost_per_employee: float | None
    planned_headcount: int | None
    plan_variance: int | None
    plan_variance_pct: float | None
    has_data: bool = True


def kpi_summary(f: Filters, engine: Engine | None = None) -> KPISummary:
    s = monthly_series(f, engine)
    n = f.months
    opening = headcount_at(f, add_months(f.start, -1), engine)
    if s.empty:
        return KPISummary(n, opening, 0, 0, 0, 0, 0, 0, None, None, None, None, None, 0, None, 0, 0.0, None,
                          None, None, None, has_data=False)
    avg = float(s.headcount.mean())
    closing = int(s.headcount.iloc[-1])
    seg, params = f.segment_sql("m")
    params["d"] = f.end
    ten = q(f"SELECT AVG(m.tenure_months) AS t FROM mart_employee_month m WHERE m.snapshot_date = :d "
            f"AND m.active_flag = 1 {seg}", params, engine).t.iloc[0]
    plan_hc = var = var_pct = None
    if not f.is_segmented:
        p = planned(f, engine, start=f.end, end=f.end)
        if not p.empty:
            plan_hc = int(p.planned_headcount.iloc[-1])
            var = closing - plan_hc
            var_pct = safe_div(var, plan_hc)
    cost = float(s.workforce_cost.sum())
    return KPISummary(
        months=n, opening_headcount=opening, active_headcount=closing, average_headcount=avg,
        joiners=int(s.joiners.sum()), exits=int(s.exits.sum()), voluntary_exits=int(s.voluntary_exits.sum()),
        involuntary_exits=int(s.involuntary_exits.sum()),
        attrition_rate=attrition_rate(s.exits.sum(), avg, n),
        voluntary_attrition_rate=attrition_rate(s.voluntary_exits.sum(), avg, n),
        involuntary_attrition_rate=attrition_rate(s.involuntary_exits.sum(), avg, n),
        net_growth=safe_div(closing - opening, opening),
        average_tenure_months=None if ten is None or pd.isna(ten) else float(ten),
        promotions=int(s.promotions.sum()), promotion_rate=attrition_rate(s.promotions.sum(), avg, n),
        transfers_in=int(s.transfers_in.sum()), workforce_cost=cost,
        cost_per_employee=annualise(safe_div(cost, avg), n),
        planned_headcount=plan_hc, plan_variance=var, plan_variance_pct=var_pct,
    )


# ------------------------------------------------------- breakdown rates

DIMENSIONS = {
    "department": ("d.department_name", "JOIN departments d ON d.department_id = m.department_id"),
    "business_unit": ("m.business_unit", ""),
    "location": ("l.city", "JOIN locations l ON l.location_id = m.location_id"),
    "job_level": ("m.job_level", ""),
    "job_family": ("m.job_family", ""),
    "employment_type": ("m.employment_type", ""),
    "tenure_band": (TENURE_BAND_SQL, ""),
}


def attrition_by(dim: str, f: Filters, engine: Engine | None = None, voluntary_only: bool = False
                 ) -> pd.DataFrame:
    """Annualised attrition by a dimension, with exact Poisson 95% CIs.

    Exits are attributed to the segment the leaver was in during their exit month.
    """
    expr, join = DIMENSIONS[dim]
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    exit_expr = ("CASE WHEN m.exit_type = 'Voluntary' THEN 1 ELSE 0 END" if voluntary_only
                 else "m.exited_in_month")
    df = q(f"""
        SELECT {expr} AS segment, m.snapshot_date AS month, SUM(m.active_flag) AS hc,
               SUM({exit_expr}) AS exits
        FROM mart_employee_month m {join}
        WHERE m.snapshot_date BETWEEN :s AND :e {seg}
        GROUP BY {expr}, m.snapshot_date""", params, engine)
    if df.empty:
        return pd.DataFrame(columns=["segment", "avg_headcount", "exits", "attrition_rate", "ci_low", "ci_high"])
    g = df.groupby("segment").agg(avg_headcount=("hc", lambda x: x.sum() / f.months), exits=("exits", "sum"))
    g = g.reset_index()
    g["attrition_rate"] = [attrition_rate(e, h, f.months) for e, h in zip(g.exits, g.avg_headcount)]
    cis = [poisson_rate_ci(e, h, f.months) for e, h in zip(g.exits, g.avg_headcount)]
    g["ci_low"] = [c[0] for c in cis]
    g["ci_high"] = [c[1] for c in cis]
    if dim == "tenure_band":
        g["segment"] = pd.Categorical(g.segment, TENURE_ORDER, ordered=True)
        g = g.sort_values("segment")
    elif dim == "job_level":
        g = g.sort_values("segment")
    else:
        g = g.sort_values("attrition_rate", ascending=False)
    return g.reset_index(drop=True)


def exit_reasons(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    return q(f"""
        SELECT e.exit_type, e.exit_reason_category AS reason, COUNT(*) AS exits
        FROM mart_employee_month m
        JOIN employee_events e ON e.employee_id = m.employee_id AND e.event_type = 'Exit'
        WHERE m.exited_in_month = 1 AND m.snapshot_date BETWEEN :s AND :e {seg}
        GROUP BY e.exit_type, e.exit_reason_category ORDER BY exits DESC""", params, engine)


def pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0) or pd.isna(prev):
        return None
    return (cur - prev) / prev


def two_proportion_test(k1: int, n1: int, k2: int, n2: int) -> float | None:
    """Two-sided p-value (normal approximation) for a difference in proportions."""
    if min(n1, n2) == 0:
        return None
    p = (k1 + k2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (k1 / n1 - k2 / n2) / se
    return float(2 * stats.norm.sf(abs(z)))
