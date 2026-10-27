"""Performance, promotion, mobility and learning analytics."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy.engine import Engine

from src.analytics.common import Filters, q, safe_div, wilson_ci
from src.analytics.metrics import LEVEL_ORDER, attrition_rate, monthly_series

RATING_LABELS = {1: "1 – Unsatisfactory", 2: "2 – Needs improvement", 3: "3 – Meets expectations",
                 4: "4 – Exceeds expectations", 5: "5 – Outstanding"}


def reviews(f: Filters, engine: Engine | None = None, all_cycles: bool = True) -> pd.DataFrame:
    """Completed reviews joined to the employee's segment in the review month."""
    from database.connection import month_key

    seg, params = f.segment_sql("m")
    mk_r, mk_m = month_key("r.review_date", engine), month_key("m.snapshot_date", engine)
    where = "" if all_cycles else " AND r.review_date BETWEEN :s AND :e"
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    return q(f"""
        SELECT r.employee_id, r.review_cycle, r.review_date, r.performance_rating, r.goal_achievement_score,
               r.training_hours, d.department_name, m.job_level, m.business_unit
        FROM performance_reviews r
        JOIN mart_employee_month m ON m.employee_id = r.employee_id AND {mk_m} = {mk_r}
        JOIN departments d ON d.department_id = m.department_id
        WHERE r.review_completion_status = 'Completed' {where} {seg}""", params, engine, dates=["review_date"])


def rating_distribution(rv: pd.DataFrame, by: str = "review_cycle") -> pd.DataFrame:
    if rv.empty:
        return rv
    t = rv.groupby([by, "performance_rating"]).size().rename("reviews").reset_index()
    t["share"] = t.reviews / t.groupby(by).reviews.transform("sum")
    return t


def promotion_rates(f: Filters, by: str, engine: Engine | None = None) -> pd.DataFrame:
    """Annualised promotion and transfer rates by department, business unit or level.

    For levels, promotions are attributed to the level promoted *from*. The
    Wilson CI is on the share of the average workforce promoted per year.
    """
    from database.connection import month_key

    expr = {"department": "d.department_name", "job_level": "m.job_level", "business_unit": "m.business_unit"}[by]
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    df = q(f"""SELECT {expr} AS segment, SUM(m.active_flag) AS hc_months, SUM(m.promoted_in_month) AS promotions,
                      SUM(m.transferred_in) AS transfers
               FROM mart_employee_month m JOIN departments d ON d.department_id = m.department_id
               WHERE m.snapshot_date BETWEEN :s AND :e {seg} GROUP BY {expr}""", params, engine)
    if by == "job_level":
        mk_e, mk_m = month_key("e.event_date", engine), month_key("m.snapshot_date", engine)
        promo = q(f"""SELECT e.previous_job_level AS segment, COUNT(*) AS promotions_from
                      FROM employee_events e
                      JOIN mart_employee_month m ON m.employee_id = e.employee_id AND {mk_m} = {mk_e}
                      WHERE e.event_type = 'Promotion' AND m.snapshot_date BETWEEN :s AND :e {seg}
                      GROUP BY e.previous_job_level""", params, engine)
        df = df.merge(promo, on="segment", how="left")
        df["promotions"] = df.promotions_from.fillna(0)
        df = df.drop(columns=["promotions_from"])
    df["avg_headcount"] = df.hc_months / f.months
    df["promotion_rate"] = [attrition_rate(p, h, f.months) for p, h in zip(df.promotions, df.avg_headcount)]
    df["mobility_rate"] = [attrition_rate(t, h, f.months) for t, h in zip(df.transfers, df.avg_headcount)]
    years = f.months / 12
    cis = [wilson_ci(p, h * years) if h else (None, None) for p, h in zip(df.promotions, df.avg_headcount)]
    df["ci_low"], df["ci_high"] = [c[0] for c in cis], [c[1] for c in cis]
    if by == "job_level":
        df["segment"] = pd.Categorical(df.segment, LEVEL_ORDER, ordered=True)
        return df.sort_values("segment")
    return df.sort_values("promotion_rate", ascending=False)


def training_summary(f: Filters, engine: Engine | None = None) -> dict:
    seg, params = f.segment_sql("m")
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    from database.connection import month_key

    mk_t, mk_m = month_key("t.enrollment_date", engine), month_key("m.snapshot_date", engine)
    tr = q(f"""SELECT t.employee_id, t.training_program, t.training_category, t.completion_status, t.training_hours,
                      t.training_cost, t.assessment_score
               FROM training_records t
               JOIN mart_employee_month m ON m.employee_id = t.employee_id AND {mk_m} = {mk_t}
               WHERE t.enrollment_date BETWEEN :s AND :e {seg}""", params, engine)
    s = monthly_series(f, engine)
    avg_hc = float(s.headcount.mean()) if len(s) else 0
    dev = tr[tr.training_category != "Compliance"]
    decided = tr[tr.completion_status.isin(["Completed", "Not Completed"])]
    completed = tr[tr.completion_status == "Completed"]
    by_cat = tr.groupby("training_category").agg(
        enrolments=("employee_id", "size"),
        completion_rate=("completion_status", lambda x: safe_div((x == "Completed").sum(),
                                                                 x.isin(["Completed", "Not Completed"]).sum())),
        cost=("training_cost", "sum"), hours=("training_hours", "sum")).reset_index()
    return dict(
        participation=safe_div(dev.employee_id.nunique(), avg_hc),
        completion_rate=safe_div(len(completed), len(decided)),
        hours_per_employee=safe_div(completed.training_hours.sum(), avg_hc),
        cost_per_employee=safe_div(tr.training_cost.sum(), avg_hc),
        total_cost=float(tr.training_cost.sum()), enrolments=int(len(tr)), by_category=by_cat,
        by_program=tr.groupby("training_program").agg(enrolments=("employee_id", "size"),
                                                       avg_assessment=("assessment_score", "mean")).reset_index(),
    )


def training_rating_change(engine: Engine | None = None) -> dict:
    """Rating change between consecutive cycles: employees who completed >= 16
    development hours in the later review period vs those who did not.

    To limit regression-to-the-mean bias (high performers are more likely to
    be nominated for leadership programmes) the comparison is made within strata
    of the prior rating and then averaged with the trained group's weights.
    This remains observational evidence, not a causal estimate.
    """
    rv = q("""SELECT employee_id, review_cycle, review_period_start, review_period_end, performance_rating
              FROM performance_reviews WHERE review_completion_status = 'Completed'""", engine=engine,
           dates=["review_period_start", "review_period_end"])
    tr = q("""SELECT employee_id, completion_date, training_hours FROM training_records
              WHERE completion_status = 'Completed' AND training_category <> 'Compliance'""", engine=engine,
           dates=["completion_date"])
    rv = rv.sort_values(["employee_id", "review_period_end"])
    rv["prev_rating"] = rv.groupby("employee_id").performance_rating.shift(1)
    pairs = rv.dropna(subset=["prev_rating"]).copy()
    if pairs.empty:
        return {}
    m = pairs[["employee_id", "review_period_start", "review_period_end"]].reset_index().merge(tr, on="employee_id")
    m = m[(m.completion_date >= m.review_period_start) & (m.completion_date <= m.review_period_end)]
    hours = m.groupby("index").training_hours.sum()
    pairs["dev_hours"] = hours.reindex(pairs.index).fillna(0)
    pairs["trained"] = pairs.dev_hours >= 16
    pairs["change"] = pairs.performance_rating - pairs.prev_rating
    strata = pairs.groupby(["prev_rating", "trained"]).change.agg(["mean", "size"]).unstack()
    strata = strata.dropna()
    if strata.empty:
        return {}
    w = strata[("size", True)]
    diff = (strata[("mean", True)] - strata[("mean", False)])
    adjusted = float((diff * w).sum() / w.sum())
    a, b = pairs[pairs.trained].change, pairs[~pairs.trained].change
    raw = float(a.mean() - b.mean())
    p = float(stats.mannwhitneyu(a, b).pvalue) if len(a) and len(b) else np.nan
    return dict(pairs=int(len(pairs)), trained=int(len(a)), raw_difference=raw, adjusted_difference=adjusted,
                p_value_raw=p, prior_rating_trained=float(pairs[pairs.trained].prev_rating.mean()),
                prior_rating_untrained=float(pairs[~pairs.trained].prev_rating.mean()),
                strata=strata.reset_index())
