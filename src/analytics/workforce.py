"""Workforce composition, structure and movement analytics (Workforce Explorer)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.engine import Engine

from src.analytics.common import Filters, q, safe_div
from src.analytics.metrics import TENURE_BAND_SQL, TENURE_ORDER, monthly_series, planned


def composition(f: Filters, by: str, engine: Engine | None = None) -> pd.DataFrame:
    """Active headcount at period end by a dimension (with share)."""
    exprs = {
        "department": ("d.department_name", "JOIN departments d ON d.department_id = m.department_id"),
        "business_unit": ("m.business_unit", ""),
        "location": ("l.city", "JOIN locations l ON l.location_id = m.location_id"),
        "job_level": ("m.job_level", ""),
        "job_family": ("m.job_family", ""),
        "employment_type": ("m.employment_type", ""),
        "gender": ("e.gender", "JOIN employees e ON e.employee_id = m.employee_id"),
        "tenure_band": (TENURE_BAND_SQL, ""),
    }
    expr, join = exprs[by]
    seg, params = f.segment_sql("m")
    params["d"] = f.end
    df = q(f"""SELECT {expr} AS segment, COUNT(*) AS headcount FROM mart_employee_month m {join}
               WHERE m.snapshot_date = :d AND m.active_flag = 1 {seg}
               GROUP BY {expr} ORDER BY headcount DESC""", params, engine)
    df["share"] = df.headcount / df.headcount.sum() if len(df) else []
    if by == "tenure_band":
        df["segment"] = pd.Categorical(df.segment, TENURE_ORDER, ordered=True)
        df = df.sort_values("segment")
    return df


def location_level_matrix(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    seg, params = f.segment_sql("m")
    params["d"] = f.end
    df = q(f"""SELECT l.city AS location, m.job_level, COUNT(*) AS headcount
               FROM mart_employee_month m JOIN locations l ON l.location_id = m.location_id
               WHERE m.snapshot_date = :d AND m.active_flag = 1 {seg}
               GROUP BY l.city, m.job_level""", params, engine)
    if df.empty:
        return df
    return df.pivot(index="location", columns="job_level", values="headcount").fillna(0).astype(int)


def tenure_distribution(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    seg, params = f.segment_sql("m")
    params["d"] = f.end
    return q(f"""SELECT m.tenure_months FROM mart_employee_month m
                 WHERE m.snapshot_date = :d AND m.active_flag = 1 {seg}""", params, engine)


def span_of_control(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Direct reports per manager at period end. The segment filter applies to
    the *reports*; managers are those with at least one report in scope."""
    seg, params = f.segment_sql("m")
    params["d"] = f.end
    return q(f"""SELECT m.manager_id, mg.job_level AS manager_level, COUNT(*) AS direct_reports
                 FROM mart_employee_month m
                 JOIN mart_employee_month mg ON mg.employee_id = m.manager_id AND mg.snapshot_date = m.snapshot_date
                 WHERE m.snapshot_date = :d AND m.active_flag = 1 AND m.manager_id IS NOT NULL {seg}
                 GROUP BY m.manager_id, mg.job_level""", params, engine)


def movements(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Monthly workforce movements: hires, exits, transfers in/out, promotions, manager changes.

    Events are attributed to the employee's segment in the event month.
    """
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    from database.connection import month_key

    mk_ev = month_key("e.event_date")
    mk_m = month_key("m.snapshot_date")
    df = q(f"""
        SELECT m.snapshot_date AS month, e.event_type, COUNT(*) AS n
        FROM employee_events e
        JOIN mart_employee_month m ON m.employee_id = e.employee_id AND {mk_m} = {mk_ev}
        WHERE m.snapshot_date BETWEEN :s AND :e AND e.event_type IN ('Transfer', 'Promotion', 'Manager Change')
        {seg}
        GROUP BY m.snapshot_date, e.event_type""", params, engine, dates=["month"])
    wide = df.pivot_table(index="month", columns="event_type", values="n", aggfunc="sum").fillna(0)
    base = monthly_series(f, engine).set_index("month")[["joiners", "exits"]]
    out = base.join(wide, how="left").fillna(0).astype(int).reset_index()
    return out


def headcount_vs_plan(f: Filters, engine: Engine | None = None, include_forward: bool = True) -> pd.DataFrame:
    actual = monthly_series(f, engine)[["month", "headcount"]]
    end = f.end
    if include_forward:
        end = (pd.Timestamp(f.end) + pd.offsets.MonthEnd(6)).date()
    plan = planned(f, engine, start=f.start, end=end)
    df = plan.merge(actual, on="month", how="outer").sort_values("month")
    df["variance"] = df.headcount - df.planned_headcount
    df["variance_pct"] = df.variance / df.planned_headcount
    return df


def plan_vs_actual_by_department(f: Filters, month, engine: Engine | None = None) -> pd.DataFrame:
    seg, params = f.segment_sql("b", columns={"locations": None, "levels": None, "employment_types": None,
                                              "business_units": None})
    params["d"] = month
    df = q(f"""
        SELECT d.department_name, d.business_unit, b.planned_headcount,
               COALESCE(a.hc, 0) AS actual_headcount, b.planned_people_cost, COALESCE(a.cost, 0) AS actual_cost
        FROM workforce_budget b
        JOIN departments d ON d.department_id = b.department_id
        LEFT JOIN (SELECT department_id, SUM(active_flag) AS hc, SUM(monthly_total_cost) AS cost
                   FROM mart_employee_month WHERE snapshot_date = :d GROUP BY department_id) a
               ON a.department_id = b.department_id
        WHERE b.scenario_name = 'AOP' AND b.planning_period = :d {seg}""", params, engine)
    if f.business_units:
        df = df[df.business_unit.isin(f.business_units)]
    df["variance"] = df.actual_headcount - df.planned_headcount
    df["variance_pct"] = [safe_div(v, p) for v, p in zip(df.variance, df.planned_headcount)]
    df["cost_variance"] = df.actual_cost - df.planned_people_cost
    df["cost_variance_pct"] = [safe_div(v, p) for v, p in zip(df.cost_variance, df.planned_people_cost)]
    return df.sort_values("variance")


def employee_records(f: Filters, engine: Engine | None = None, limit: int = 500) -> pd.DataFrame:
    """Row-level records (analyst role only): synthetic IDs, no names or pay."""
    seg, params = f.segment_sql("m")
    params.update(d=f.end, lim=limit)
    return q(f"""SELECT m.employee_id, d.department_name, l.city AS location, m.job_family, m.job_level,
                        m.employment_type, m.tenure_months, m.manager_id
                 FROM mart_employee_month m
                 JOIN departments d ON d.department_id = m.department_id
                 JOIN locations l ON l.location_id = m.location_id
                 WHERE m.snapshot_date = :d AND m.active_flag = 1 {seg}
                 ORDER BY m.employee_id LIMIT :lim""", params, engine)
