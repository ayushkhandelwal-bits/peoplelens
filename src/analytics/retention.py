"""Early attrition, cohort retention and survival analysis of new joiners.

Right-censoring is handled explicitly: a joiner only counts towards an N-day
retention rate once N days have passed between their joining date and the data
extraction date. Survival curves use the Kaplan-Meier estimator with censoring
at the extraction date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine
from statsmodels.duration.survfunc import SurvfuncRight, survdiff

from src.analytics.common import Filters, q, wilson_ci

HORIZONS = {"30 days": 30, "60 days": 60, "90 days": 90, "6 months": 182, "12 months": 365}


def joiners(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Joiners in the period with the segment they joined into."""
    seg, params = f.segment_sql("m")
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    df = q(f"""
        SELECT m.employee_id, d.department_name, m.business_unit, l.city AS location, m.job_family, m.job_level,
               m.employment_type, e.joining_date, e.exit_date, e.exit_type,
               COALESCE(c.recruitment_source, 'Unknown') AS recruitment_source
        FROM mart_employee_month m
        JOIN employees e ON e.employee_id = m.employee_id
        JOIN departments d ON d.department_id = m.department_id
        JOIN locations l ON l.location_id = m.location_id
        LEFT JOIN candidates c ON c.associated_employee_id = e.employee_id AND c.final_disposition = 'Joined'
        WHERE m.joined_in_month = 1 AND e.joining_date BETWEEN :s AND :e {seg}""",
           params, engine, dates=["joining_date", "exit_date"])
    end = pd.Timestamp(q("SELECT MAX(snapshot_date) AS d FROM mart_employee_month", engine=engine).d.iloc[0])
    df["observed_days"] = (end - df.joining_date).dt.days
    df["tenure_days"] = (df.exit_date.fillna(end + pd.Timedelta(days=1)) - df.joining_date).dt.days
    df["exited"] = df.exit_date.notna().astype(int)
    df["cohort"] = df.joining_date.dt.to_period("Q").astype(str)
    return df


def retention_at(df: pd.DataFrame, days: int) -> tuple[float | None, int, int]:
    """Share retained after ``days`` among joiners observed for at least ``days``."""
    elig = df[df.observed_days >= days]
    if elig.empty:
        return None, 0, 0
    left = int(((elig.exited == 1) & (elig.tenure_days < days)).sum())
    return 1 - left / len(elig), len(elig), left


def cohort_table(df: pd.DataFrame, by: str = "cohort") -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(by):
        row = {by: key, "joiners": len(g)}
        for label, d in HORIZONS.items():
            r, n, _ = retention_at(g, d)
            row[label] = r
            row[f"n_{d}"] = n
        rows.append(row)
    return pd.DataFrame(rows)


def early_attrition_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, d in list(HORIZONS.items())[:3]:
        r, n, left = retention_at(df, d)
        lo, hi = wilson_ci(left, n)
        rows.append(dict(horizon=label, eligible_joiners=n, leavers=left,
                         early_attrition=None if r is None else 1 - r, ci_low=lo, ci_high=hi))
    return pd.DataFrame(rows)


def kaplan_meier(df: pd.DataFrame, group: str | None = None, max_days: int = 540) -> pd.DataFrame:
    """Kaplan-Meier retention curves (time = days since joining)."""
    out = []
    groups = [("All joiners", df)] if group is None else list(df.groupby(group))
    for name, g in groups:
        if len(g) < 15:
            continue
        t = g.tenure_days.clip(lower=0.5).values
        sf = SurvfuncRight(t, g.exited.values)
        times = np.concatenate([[0], sf.surv_times])
        surv = np.concatenate([[1.0], sf.surv_prob])
        keep = times <= max_days
        out.append(pd.DataFrame({"group": name, "days": times[keep], "retention": surv[keep], "n": len(g)}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["group", "days", "retention", "n"])


def logrank(df: pd.DataFrame, group: str) -> dict:
    d = df[df.groupby(group)[group].transform("size") >= 15]
    if d[group].nunique() < 2:
        return {"statistic": None, "p_value": None, "groups": int(d[group].nunique())}
    stat, p = survdiff(d.tenure_days.clip(lower=0.5).values, d.exited.values, d[group].values)
    return {"statistic": float(stat), "p_value": float(p), "groups": int(d[group].nunique())}


def tenure_at_exit(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    return q(f"""SELECT m.tenure_months, m.exit_type FROM mart_employee_month m
                 WHERE m.exited_in_month = 1 AND m.snapshot_date BETWEEN :s AND :e {seg}""", params, engine)
