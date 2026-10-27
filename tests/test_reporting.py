"""Monthly report, recommendations, NLP and exports."""

from __future__ import annotations

import pandas as pd

from src.analytics.common import Filters, data_bounds
from src.analytics.recommendations import generate
from src.nlp.sentiment import label, score
from src.nlp.topic_analysis import detect_themes
from src.reporting.monthly_report import build_report, write_report


def test_report_builds_from_database(engine, tmp_path):
    paths = write_report(out_dir=tmp_path)
    html = paths["html"].read_text(encoding="utf-8")
    for section in ["Headline KPIs", "Hiring and exits", "Attrition and retention", "Recruitment",
                    "Workforce plan vs actual", "Manpower cost", "Data quality status", "Definitions"]:
        assert section in html
    csv = pd.read_csv(paths["csv"])
    assert {"section", "metric", "segment", "value"} <= set(csv.columns) and len(csv) > 20


def test_report_for_a_specific_month(engine):
    lo, hi = data_bounds(engine)
    month = (pd.Timestamp(hi) - pd.offsets.MonthEnd(3)).strftime("%Y-%m")
    rep = build_report(month, engine)
    assert pd.Timestamp(month).strftime("%B %Y") == rep.period


def test_recommendations_have_required_fields(engine):
    lo, hi = data_bounds(engine)
    recs = generate(Filters(start=(pd.Timestamp(hi) - pd.offsets.MonthEnd(11)).date(), end=hi), engine)
    for r in recs:
        d = r.to_dict()
        assert all(d[k] for k in ["observation", "metric", "segment", "impact", "action", "limitation",
                                  "threshold"])


def test_sentiment_and_themes():
    assert label(score("My manager is supportive and always makes time for one-on-ones.")) == "positive"
    assert label(score("Workload is too high, we are constantly firefighting.")) == "negative"
    assert "Compensation" in detect_themes("The increment did not even match inflation.")
    assert detect_themes("Please keep the survey shorter next time.") == ["General / other"]
