"""Engagement survey analytics with small-group suppression.

Privacy rule: any aggregate built from fewer than ``MIN_GROUP`` responses is
suppressed (values replaced by NaN and flagged) before it leaves this module.
Individual responses are never returned to the UI.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy.engine import Engine

from config.settings import get_settings
from database.connection import month_key
from src.analytics.common import Filters, q, wilson_ci
from src.analytics.metrics import TENURE_BAND_SQL, TENURE_ORDER, enps

MIN_GROUP = get_settings().min_group_size
SCORES = ["engagement_score", "job_satisfaction_score", "manager_support_score", "work_life_balance_score",
          "career_growth_score"]


def suppress(df: pd.DataFrame, n_col: str = "responses", cols: list[str] | None = None) -> pd.DataFrame:
    df = df.copy()
    small = df[n_col] < MIN_GROUP
    cols = cols or [c for c in df.columns if c not in (n_col,) and df[c].dtype.kind in "fi"]
    for c in cols:
        df[c] = df[c].astype(float)
        df.loc[small, c] = np.nan
    df["suppressed"] = small
    return df


def responses(f: Filters, engine: Engine | None = None, lookback_start=None) -> pd.DataFrame:
    """Survey responses in scope joined to the respondent's segment that month (internal use)."""
    seg, params = f.segment_sql("m")
    params.update(s=lookback_start or (pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    mk_s, mk_m = month_key("s.survey_date", engine), month_key("m.snapshot_date", engine)
    return q(f"""
        SELECT s.employee_id, s.survey_date, s.survey_period, d.department_name, m.business_unit,
               m.job_level, l.city AS location, {TENURE_BAND_SQL} AS tenure_band,
               s.engagement_score, s.job_satisfaction_score, s.manager_support_score,
               s.work_life_balance_score, s.career_growth_score, s.enps_response
        FROM engagement_surveys s
        JOIN mart_employee_month m ON m.employee_id = s.employee_id AND {mk_m} = {mk_s}
        JOIN departments d ON d.department_id = s.department_id
        JOIN locations l ON l.location_id = m.location_id
        WHERE s.survey_date BETWEEN :s AND :e {seg}""", params, engine, dates=["survey_date"])


def _agg(g) -> pd.Series:
    return pd.Series({"responses": len(g), **{c: g[c].mean() for c in SCORES},
                      "enps": enps(g.enps_response)})


def trend(r: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    keys = ["survey_period"] + ([by] if by else [])
    if r.empty:
        return pd.DataFrame(columns=keys + ["responses"] + SCORES + ["enps", "suppressed"])
    out = r.groupby(keys).apply(_agg, include_groups=False).reset_index()
    if by == "tenure_band":
        out[by] = pd.Categorical(out[by], TENURE_ORDER, ordered=True)
    return suppress(out)


def department_change(r: pd.DataFrame) -> pd.DataFrame:
    """Latest vs previous survey period by department, with a Welch t-test."""
    periods = sorted(r.survey_period.unique())
    if len(periods) < 2:
        return pd.DataFrame()
    cur, prev = periods[-1], periods[-2]
    rows = []
    for dept, g in r.groupby("department_name"):
        a = g[g.survey_period == cur].engagement_score
        b = g[g.survey_period == prev].engagement_score
        if len(a) < MIN_GROUP or len(b) < MIN_GROUP:
            rows.append(dict(department=dept, current=np.nan, previous=np.nan, change=np.nan, p_value=np.nan,
                             responses=len(a), suppressed=True))
            continue
        t = stats.ttest_ind(a, b, equal_var=False)
        rows.append(dict(department=dept, current=a.mean(), previous=b.mean(), change=a.mean() - b.mean(),
                         p_value=float(t.pvalue), responses=len(a), suppressed=False))
    df = pd.DataFrame(rows)
    df.attrs.update(current=cur, previous=prev)
    return df.sort_values("change")


def response_rates(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    seg, params = f.segment_sql("m")
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    surveys = q("SELECT survey_period, MIN(survey_date) AS d, COUNT(*) AS n FROM engagement_surveys "
                "WHERE survey_date BETWEEN :s AND :e GROUP BY survey_period", params, engine, dates=["d"])
    rows = []
    for _, s in surveys.iterrows():
        p = dict(params, sd=s.d.date(), sd30=(s.d - pd.Timedelta(days=30)).date(),
                 me=(s.d + pd.offsets.MonthEnd(0)).date())
        elig = q(f"""SELECT COUNT(*) AS n FROM mart_employee_month m JOIN employees e ON e.employee_id = m.employee_id
                     WHERE m.snapshot_date = :me AND e.joining_date <= :sd30
                     AND (e.exit_date IS NULL OR e.exit_date > :sd) AND m.job_level <> 'L9' {seg}""",
                 p, engine).n.iloc[0]
        resp = q(f"""SELECT COUNT(*) AS n FROM engagement_surveys s
                     JOIN mart_employee_month m ON m.employee_id = s.employee_id AND m.snapshot_date = :me
                     WHERE s.survey_period = :sp {seg}""", dict(p, sp=s.survey_period), engine).n.iloc[0]
        rows.append(dict(survey_period=s.survey_period, eligible=int(elig), responses=int(resp),
                         response_rate=resp / elig if elig else None))
    return pd.DataFrame(rows)


def engagement_and_exit(f: Filters, engine: Engine | None = None, horizon_days: int = 182) -> dict:
    """Voluntary exit within ``horizon_days`` after a survey, by engagement quartile.

    Only responses with a fully observed horizon are used. This is an
    association: engagement is not randomly assigned.
    """
    r = responses(f, engine)
    if r.empty:
        return {}
    e = q("SELECT employee_id, exit_date, exit_type FROM employees WHERE exit_date IS NOT NULL", engine=engine,
          dates=["exit_date"])
    end = pd.Timestamp(q("SELECT MAX(snapshot_date) AS d FROM mart_employee_month", engine=engine).d.iloc[0])
    r = r[r.survey_date + pd.Timedelta(days=horizon_days) <= end].merge(e, on="employee_id", how="left")
    if r.empty:
        return {}
    r["left"] = (r.exit_type == "Voluntary") & (r.exit_date > r.survey_date) & (
        (r.exit_date - r.survey_date).dt.days <= horizon_days)
    r["quartile"] = pd.qcut(r.engagement_score.rank(method="first"), 4,
                            labels=["Q1 (lowest)", "Q2", "Q3", "Q4 (highest)"])
    g = r.groupby("quartile", observed=True).agg(responses=("left", "size"), leavers=("left", "sum"),
                                                 avg_score=("engagement_score", "mean")).reset_index()
    g["exit_rate"] = g.leavers / g.responses
    cis = [wilson_ci(k, n) for k, n in zip(g.leavers, g.responses)]
    g["ci_low"], g["ci_high"] = [c[0] for c in cis], [c[1] for c in cis]
    table = pd.crosstab(r.quartile, r.left)
    chi2, p, _, _ = stats.chi2_contingency(table) if table.shape == (4, 2) else (np.nan, np.nan, 0, 0)
    mw = stats.mannwhitneyu(r[r.left].engagement_score, r[~r.left].engagement_score, alternative="two-sided") \
        if r.left.any() and (~r.left).any() else None
    return dict(table=g, chi2=float(chi2), p_value=float(p), responses=int(len(r)),
                leaver_median=float(r[r.left].engagement_score.median()) if r.left.any() else None,
                stayer_median=float(r[~r.left].engagement_score.median()),
                mannwhitney_p=float(mw.pvalue) if mw else None, horizon_days=horizon_days)


def manager_change_effect(f: Filters, engine: Engine | None = None) -> dict:
    """Change in engagement between consecutive surveys for employees whose
    manager changed in between vs those whose manager did not change."""
    r = responses(f, engine, lookback_start=pd.Timestamp("2000-01-01").date())
    if r.empty:
        return {}
    r = r.sort_values(["employee_id", "survey_date"])
    r["prev_date"] = r.groupby("employee_id").survey_date.shift(1)
    r["prev_eng"] = r.groupby("employee_id").engagement_score.shift(1)
    r["prev_mgr"] = r.groupby("employee_id").manager_support_score.shift(1)
    pairs = r.dropna(subset=["prev_date"])
    pairs = pairs[(pairs.survey_date - pairs.prev_date).dt.days <= 100]  # consecutive quarterly waves
    pairs = pairs[pairs.survey_date >= pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)]
    ev = q("SELECT employee_id, event_date FROM employee_events WHERE event_type IN ('Manager Change', 'Transfer')",
           engine=engine, dates=["event_date"])
    m = pairs[["employee_id", "prev_date", "survey_date"]].reset_index().merge(ev, on="employee_id")
    changed_idx = m[(m.event_date > m.prev_date) & (m.event_date <= m.survey_date)]["index"].unique()
    pairs["manager_changed"] = pairs.index.isin(changed_idx)
    pairs["d_engagement"] = pairs.engagement_score - pairs.prev_eng
    pairs["d_manager_support"] = pairs.manager_support_score - pairs.prev_mgr
    a, b = pairs[pairs.manager_changed], pairs[~pairs.manager_changed]
    if len(a) < MIN_GROUP or len(b) < MIN_GROUP:
        return {}
    summary = pairs.groupby("manager_changed").agg(pairs=("d_engagement", "size"),
                                                   mean_change_engagement=("d_engagement", "mean"),
                                                   mean_change_manager_support=("d_manager_support", "mean"))
    return dict(summary=summary.reset_index(),
                p_engagement=float(stats.mannwhitneyu(a.d_engagement, b.d_engagement).pvalue),
                p_manager_support=float(stats.mannwhitneyu(a.d_manager_support, b.d_manager_support).pvalue),
                diff_engagement=float(a.d_engagement.mean() - b.d_engagement.mean()),
                diff_manager_support=float(a.d_manager_support.mean() - b.d_manager_support.mean()))
