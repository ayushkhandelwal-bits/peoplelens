"""Point-in-time feature engineering for attrition modelling.

For a prediction date *d* (a month end) the population is every employee active
at *d* (levels L1-L7). Every feature is computed only from records dated on or
before *d*; the label is a **voluntary** exit in the following ``horizon``
months. Fields that are only known after the outcome — exit dates, exit types,
exit reasons, later reviews or surveys — are never used as features.

Protected or identifying attributes (name, gender) are excluded by policy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from database.connection import get_engine, read_sql

NUMERIC_FEATURES = [
    "tenure_months", "level_num", "contract", "engagement_score", "engagement_missing", "engagement_change",
    "manager_support_score", "career_growth_score", "last_rating", "rating_missing", "pay_vs_peer_median",
    "months_since_promotion", "manager_change_6m", "team_size", "training_hours_12m",
    "dept_trailing_attrition",
]
CATEGORICAL_FEATURES = ["business_unit", "location", "recruitment_source"]
LEAKAGE_COLUMNS = {"exit_date", "exit_type", "exit_reason_category", "label", "current_status"}
EXCLUDED_BY_POLICY = {"employee_name", "gender"}


@dataclass
class SourceData:
    mart: pd.DataFrame
    surveys: pd.DataFrame
    reviews: pd.DataFrame
    events: pd.DataFrame
    training: pd.DataFrame
    employees: pd.DataFrame
    sources: pd.DataFrame
    locations: dict

    @classmethod
    def load(cls, engine: Engine | None = None) -> SourceData:
        engine = engine or get_engine()
        mart = read_sql("""SELECT snapshot_date, employee_id, department_id, business_unit, location_id, job_level,
                                  employment_type, manager_id, active_flag, tenure_months, exited_in_month,
                                  monthly_base_cost, job_family, joined_in_month
                           FROM mart_employee_month""", engine=engine, parse_dates=["snapshot_date"])
        surveys = read_sql("""SELECT employee_id, survey_date, engagement_score, manager_support_score,
                                     career_growth_score FROM engagement_surveys""", engine=engine,
                           parse_dates=["survey_date"])
        reviews = read_sql("""SELECT employee_id, review_date, performance_rating FROM performance_reviews
                              WHERE review_completion_status = 'Completed'""", engine=engine,
                           parse_dates=["review_date"])
        events = read_sql("""SELECT employee_id, event_type, event_date FROM employee_events
                             WHERE event_type IN ('Promotion', 'Manager Change', 'Transfer', 'Hire')""",
                          engine=engine, parse_dates=["event_date"])
        training = read_sql("""SELECT employee_id, completion_date, training_hours FROM training_records
                               WHERE completion_status = 'Completed' AND training_category <> 'Compliance'""",
                            engine=engine, parse_dates=["completion_date"])
        employees = read_sql("SELECT employee_id, joining_date, exit_date, exit_type FROM employees",
                             engine=engine, parse_dates=["joining_date", "exit_date"])
        sources = read_sql("""SELECT associated_employee_id AS employee_id, recruitment_source FROM candidates
                              WHERE final_disposition = 'Joined'""", engine=engine)
        loc = read_sql("SELECT location_id, city FROM locations", engine=engine)
        return cls(mart, surveys, reviews, events, training, employees, sources, dict(zip(loc.location_id, loc.city)))


def _latest_before(df: pd.DataFrame, date_col: str, d: pd.Timestamp, cols: list[str]) -> pd.DataFrame:
    x = df[df[date_col] <= d].sort_values(date_col)
    return x.groupby("employee_id")[cols].last()


def build_features(src: SourceData, d: pd.Timestamp, horizon_months: int = 6, with_label: bool = True
                   ) -> pd.DataFrame:
    d = pd.Timestamp(d)
    m = src.mart
    pop = m[(m.snapshot_date == d) & (m.active_flag == 1)].copy()
    pop = pop[pop.job_level.str[1:].astype(int) <= 7]
    if pop.empty:
        return pop
    pop["prediction_date"] = d
    pop["level_num"] = pop.job_level.str[1:].astype(int)
    pop["contract"] = (pop.employment_type == "Contract").astype(int)
    pop["location"] = pop.location_id.map(src.locations)

    # engagement: latest response within 6 months, and change vs the previous response
    sv = src.surveys[(src.surveys.survey_date <= d) & (src.surveys.survey_date > d - pd.DateOffset(months=6))]
    sv = sv.sort_values("survey_date")
    last = sv.groupby("employee_id")[["engagement_score", "manager_support_score", "career_growth_score"]].last()
    allsv = src.surveys[src.surveys.survey_date <= d].sort_values("survey_date")
    prev = allsv.groupby("employee_id").engagement_score.apply(lambda s: s.iloc[-1] - s.iloc[-2] if len(s) > 1 else 0)
    pop = pop.join(last, on="employee_id").join(prev.rename("engagement_change"), on="employee_id")
    pop["engagement_missing"] = pop.engagement_score.isna().astype(int)

    rv = _latest_before(src.reviews, "review_date", d, ["performance_rating"])
    pop = pop.join(rv.rename(columns={"performance_rating": "last_rating"}), on="employee_id")
    pop["rating_missing"] = pop.last_rating.isna().astype(int)

    # pay relative to the median of active peers at the same level and job family on date d
    peers = m[(m.snapshot_date == d) & (m.active_flag == 1) & (m.joined_in_month == 0)]
    med = peers.groupby(["job_level", "job_family"]).monthly_base_cost.median().rename("peer_median")
    pop = pop.join(med, on=["job_level", "job_family"])
    pop["pay_vs_peer_median"] = pop.monthly_base_cost / pop.peer_median
    # joiners in their first month are pro-rated: fall back to 1.0
    pop.loc[pop.joined_in_month == 1, "pay_vs_peer_median"] = 1.0

    ev = src.events[src.events.event_date <= d]
    promo = ev[ev.event_type.isin(["Promotion", "Hire"])].groupby("employee_id").event_date.max()
    pop["months_since_promotion"] = ((d - pop.employee_id.map(promo)).dt.days / 30.44).round(1)
    mc = ev[(ev.event_type.isin(["Manager Change", "Transfer"])) & (ev.event_date > d - pd.Timedelta(days=182))]
    pop["manager_change_6m"] = pop.employee_id.isin(mc.employee_id).astype(int)

    all_active = m[(m.snapshot_date == d) & (m.active_flag == 1)]
    span = all_active.groupby("manager_id").size()
    pop["team_size"] = pop.manager_id.map(span).fillna(0)

    tr = src.training[(src.training.completion_date <= d) & (src.training.completion_date > d - pd.Timedelta(days=365))]
    pop["training_hours_12m"] = pop.employee_id.map(tr.groupby("employee_id").training_hours.sum()).fillna(0)

    # department trailing-12-month attrition (known at d)
    win = m[(m.snapshot_date > d - pd.DateOffset(months=12)) & (m.snapshot_date <= d)]
    g = win.groupby("department_id").agg(exits=("exited_in_month", "sum"), hc=("active_flag", "sum"))
    months = max(1, win.snapshot_date.nunique())
    g["rate"] = g.exits / (g.hc / months) * 12 / months
    pop["dept_trailing_attrition"] = pop.department_id.map(g.rate)

    pop["recruitment_source"] = pop.employee_id.map(src.sources.set_index("employee_id").recruitment_source)
    pop["recruitment_source"] = pop.recruitment_source.fillna("Pre-window / unknown")

    if with_label:
        e = src.employees.set_index("employee_id")
        x = pop.employee_id.map(e.exit_date)
        t = pop.employee_id.map(e.exit_type)
        horizon_end = d + pd.DateOffset(months=horizon_months)
        pop["label"] = ((t == "Voluntary") & (x > d) & (x <= horizon_end)).astype(int)
    keep = ["prediction_date", "employee_id", "department_id"] + NUMERIC_FEATURES + CATEGORICAL_FEATURES
    if with_label:
        keep.append("label")
    return pop[keep].reset_index(drop=True)


def build_dataset(src: SourceData, dates: list[pd.Timestamp], horizon_months: int = 6, with_label: bool = True
                  ) -> pd.DataFrame:
    frames = [build_features(src, d, horizon_months, with_label) for d in dates]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def quarter_ends(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    return [pd.Timestamp(x) for x in pd.date_range(start, end, freq="QE")]


def check_no_leakage(df: pd.DataFrame) -> None:
    bad = (LEAKAGE_COLUMNS - {"label"}) & set(df.columns)
    banned = EXCLUDED_BY_POLICY & set(df.columns)
    if bad or banned:
        raise ValueError(f"Leakage/policy columns present in features: {sorted(bad | banned)}")


def impute_frame(df: pd.DataFrame, medians: dict | None = None) -> tuple[pd.DataFrame, dict]:
    df = df.copy()
    medians = medians or {c: float(np.nanmedian(df[c].astype(float))) if df[c].notna().any() else 0.0
                          for c in NUMERIC_FEATURES}
    for c in NUMERIC_FEATURES:
        df[c] = df[c].astype(float).fillna(medians.get(c, 0.0))
    for c in CATEGORICAL_FEATURES:
        df[c] = df[c].fillna("Unknown").astype(str)
    return df, medians
