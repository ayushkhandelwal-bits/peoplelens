"""Diagnostic statistics that answer business questions with stated uncertainty.

* Which departments' attrition differs from the rest of the organisation?
  Two-proportion tests per department with Holm correction for multiple
  comparisons (24 simultaneous tests would otherwise produce false alarms).
* Does location matter once department mix is accounted for? A raw
  chi-square test vs a likelihood-ratio test in a logistic model with
  department fixed effects (a confounding check).
* Which factors are associated with voluntary exit within six months?
  An interpretable logistic regression (statsmodels) with odds ratios and
  95% confidence intervals on point-in-time features.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from sqlalchemy.engine import Engine
from statsmodels.stats.multitest import multipletests

from src.analytics.common import Filters, q
from src.analytics.metrics import two_proportion_test


def at_risk_population(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """One row per employee present in the period: did they leave (voluntarily) in it?

    Segment attributes are taken from the employee's first month in the period.
    """
    seg, params = f.segment_sql("m")
    params.update(s=f.start, e=f.end)
    df = q(f"""SELECT m.employee_id, m.snapshot_date, d.department_name, l.city AS location, m.job_level,
                      m.exited_in_month, m.exit_type
               FROM mart_employee_month m JOIN departments d ON d.department_id = m.department_id
               JOIN locations l ON l.location_id = m.location_id
               WHERE m.snapshot_date BETWEEN :s AND :e AND m.job_level NOT IN ('L8', 'L9') {seg}""", params, engine)
    df = df.sort_values("snapshot_date")
    first = df.groupby("employee_id")[["department_name", "location", "job_level"]].first()
    df["vol"] = ((df.exited_in_month == 1) & (df.exit_type == "Voluntary")).astype(int)
    first["left_voluntarily"] = df.groupby("employee_id").vol.max()
    return first.reset_index()


def department_attrition_tests(pop: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    pop = pop[pop.department_name != "Executive Office"]  # no attrition is simulated for executives
    rows = []
    total_k, total_n = pop.left_voluntarily.sum(), len(pop)
    for dept, g in pop.groupby("department_name"):
        k, n = g.left_voluntarily.sum(), len(g)
        rk, rn = total_k - k, total_n - n
        p = two_proportion_test(int(k), int(n), int(rk), int(rn))
        rows.append(dict(department=dept, employees=n, leavers=int(k), rate=k / n if n else np.nan,
                         rest_rate=rk / rn if rn else np.nan, p_value=p))
    df = pd.DataFrame(rows).dropna(subset=["p_value"])
    if df.empty:
        return df
    df["rate_ratio"] = df.rate / df.rest_rate
    df["p_holm"] = multipletests(df.p_value, method="holm")[1]
    df["significant_after_correction"] = df.p_holm < alpha
    df["significant_uncorrected"] = df.p_value < alpha
    return df.sort_values("rate_ratio", ascending=False)


def location_confounding(pop: pd.DataFrame) -> dict:
    tab = pd.crosstab(pop.location, pop.left_voluntarily)
    if tab.shape[1] < 2:
        return {}
    chi2, p_raw, dof, _ = stats.chi2_contingency(tab)
    n = tab.values.sum()
    cramers_v = float(np.sqrt(chi2 / (n * (min(tab.shape) - 1))))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base = smf.logit("left_voluntarily ~ C(department_name)", data=pop).fit(disp=False, maxiter=200)
        full = smf.logit("left_voluntarily ~ C(department_name) + C(location)", data=pop).fit(disp=False, maxiter=200)
    lr = 2 * (full.llf - base.llf)
    df_diff = full.df_model - base.df_model
    p_adj = float(stats.chi2.sf(lr, df_diff))
    rates = pop.groupby("location").left_voluntarily.agg(["mean", "size"]).reset_index()
    return dict(chi2=float(chi2), p_raw=float(p_raw), dof=int(dof), cramers_v=cramers_v, lr_stat=float(lr),
                p_adjusted=p_adj, rates=rates)


DRIVER_LABELS = {
    "engagement_z": "Engagement score (+1 SD)", "tenure_lt_12m": "Tenure under 12 months",
    "pay_gap": "Pay 10% below peer median", "manager_change_6m": "Manager change in last 6 months",
    "stale_promotion": "No promotion in 3+ years", "contract": "Contract employee",
    "low_rating": "Latest rating 1-2", "high_rating": "Latest rating 4-5",
    "engagement_missing": "Did not answer the last survey",
}


def attrition_drivers(engine: Engine | None = None, dates: list[pd.Timestamp] | None = None) -> dict:
    """Logistic regression: voluntary exit within 6 months on point-in-time features."""
    from src.models.features import SourceData, build_dataset, quarter_ends

    src = SourceData.load(engine)
    end = src.mart.snapshot_date.max() - pd.offsets.MonthEnd(6)
    dates = dates or quarter_ends(src.mart.snapshot_date.min() + pd.offsets.MonthEnd(5), end)
    df = build_dataset(src, dates, 6)
    if df.empty or df.label.nunique() < 2:
        return {}
    eng = df.engagement_score
    df["engagement_z"] = ((eng - eng.mean()) / eng.std()).fillna(0)
    df["tenure_lt_12m"] = (df.tenure_months < 12).astype(int)
    df["pay_gap"] = ((1 - df.pay_vs_peer_median.fillna(1)).clip(-0.5, 0.5) / 0.10)
    df["stale_promotion"] = (df.months_since_promotion > 36).astype(int)
    df["low_rating"] = (df.last_rating <= 2).astype(int)
    df["high_rating"] = (df.last_rating >= 4).astype(int)
    cols = list(DRIVER_LABELS)
    X = sm.add_constant(df[cols].astype(float))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = sm.Logit(df.label.astype(float), X).fit(disp=False, maxiter=200, cov_type="cluster",
                                                     cov_kwds={"groups": pd.factorize(df.employee_id)[0]})
    ci = fit.conf_int()
    out = pd.DataFrame({"factor": [DRIVER_LABELS[c] for c in cols], "odds_ratio": np.exp(fit.params[cols]),
                        "ci_low": np.exp(ci.loc[cols, 0]), "ci_high": np.exp(ci.loc[cols, 1]),
                        "p_value": fit.pvalues[cols]})
    out = out.sort_values("odds_ratio", ascending=False).reset_index(drop=True)
    return dict(table=out, n=int(len(df)), employees=int(df.employee_id.nunique()), events=int(df.label.sum()),
                pseudo_r2=float(fit.prsquared), dates=[d.date().isoformat() for d in dates])
