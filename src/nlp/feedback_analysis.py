"""Feedback analytics used by the application (aggregate outputs only)."""

from __future__ import annotations

from functools import lru_cache

import pandas as pd
from scipy import stats
from sqlalchemy.engine import Engine

from config.settings import DATASET_DIR, GENERATED_DIR, get_settings
from src.analytics.common import Filters, q
from src.nlp.sentiment import score_frame
from src.nlp.topic_analysis import theme_frame

MIN_GROUP = get_settings().min_group_size


def load_feedback(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Comments in scope. Only department filters apply (comments carry no other attributes)."""
    seg, params = f.segment_sql("fb", columns={"locations": None, "levels": None, "employment_types": None,
                                               "business_units": None})
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    df = q(f"""SELECT fb.feedback_id, fb.survey_date, fb.survey_period, fb.tenure_band, fb.feedback_text,
                      d.department_name, d.business_unit
               FROM survey_feedback fb JOIN departments d ON d.department_id = fb.department_id
               WHERE fb.survey_date BETWEEN :s AND :e {seg}""", params, engine, dates=["survey_date"])
    if f.business_units:
        df = df[df.business_unit.isin(f.business_units)]
    return score_frame(df) if len(df) else df.assign(compound=[], sentiment=[])


@lru_cache(maxsize=1)
def load_labels() -> pd.DataFrame | None:
    for p in (GENERATED_DIR / "feedback_labels.csv", DATASET_DIR / "feedback_labels.csv"):
        if p.exists():
            return pd.read_csv(p)
    return None


def sentiment_by(df: pd.DataFrame, by: str) -> pd.DataFrame:
    g = df.groupby(by).agg(comments=("compound", "size"), avg_sentiment=("compound", "mean"),
                           share_negative=("sentiment", lambda s: (s == "negative").mean()),
                           share_positive=("sentiment", lambda s: (s == "positive").mean())).reset_index()
    small = g.comments < MIN_GROUP
    for c in ("avg_sentiment", "share_negative", "share_positive"):
        g[c] = g[c].astype(float)
        g.loc[small, c] = float("nan")
    g["suppressed"] = small
    return g


def theme_trend(df: pd.DataFrame) -> pd.DataFrame:
    t = theme_frame(df)
    g = t.groupby(["survey_period", "theme"]).agg(mentions=("feedback_id", "nunique"),
                                                  avg_sentiment=("compound", "mean")).reset_index()
    totals = df.groupby("survey_period").feedback_id.nunique()
    g["share_of_comments"] = g.mentions / g.survey_period.map(totals)
    return g


def theme_summary(df: pd.DataFrame) -> pd.DataFrame:
    t = theme_frame(df)
    g = t.groupby("theme").agg(mentions=("feedback_id", "nunique"), avg_sentiment=("compound", "mean"),
                               share_negative=("sentiment", lambda s: (s == "negative").mean())).reset_index()
    g["share_of_comments"] = g.mentions / df.feedback_id.nunique()
    return g.sort_values("mentions", ascending=False)


def engagement_sentiment_association(fb: pd.DataFrame, f: Filters, engine: Engine | None = None) -> dict:
    """Spearman correlation between department-period mean engagement and mean
    comment sentiment (aggregate level only, groups >= MIN_GROUP on both sides)."""
    seg, params = f.segment_sql("s", columns={"locations": None, "levels": None, "employment_types": None,
                                              "business_units": None})
    params.update(s=(pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date(), e=f.end)
    eng = q(f"""SELECT d.department_name, s.survey_period, AVG(s.engagement_score) AS engagement,
                       COUNT(*) AS responses
                FROM engagement_surveys s JOIN departments d ON d.department_id = s.department_id
                WHERE s.survey_date BETWEEN :s AND :e {seg}
                GROUP BY d.department_name, s.survey_period""", params, engine)
    sen = fb.groupby(["department_name", "survey_period"]).agg(sentiment=("compound", "mean"),
                                                              comments=("compound", "size")).reset_index()
    m = eng.merge(sen, on=["department_name", "survey_period"])
    m = m[(m.responses >= MIN_GROUP) & (m.comments >= MIN_GROUP)]
    if len(m) < 8:
        return {"groups": int(len(m))}
    r = stats.spearmanr(m.engagement, m.sentiment)
    return {"groups": int(len(m)), "spearman_rho": float(r.statistic), "p_value": float(r.pvalue), "data": m}
