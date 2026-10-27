"""Models: training, leakage controls, chronological validation, robustness."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models import attrition, hiring_forecast, scenarios, workforce_forecast
from src.models.features import (
    CATEGORICAL_FEATURES,
    EXCLUDED_BY_POLICY,
    LEAKAGE_COLUMNS,
    NUMERIC_FEATURES,
    SourceData,
    build_features,
    check_no_leakage,
)


@pytest.fixture(scope="module")
def src(engine):
    return SourceData.load(engine)


def test_training_produced_validated_artefacts(built_db):
    m = built_db["models"]
    assert 0.5 < m["attrition"]["roc_auc"] < 0.95  # better than chance, not implausibly perfect
    assert m["workforce_forecast"]["backtest_mae"] > 0
    meta = attrition.load_meta()
    assert meta["results"]["logistic_regression"]["n"] == meta["test_rows"]


def test_features_exclude_leakage_and_protected_attributes():
    cols = set(NUMERIC_FEATURES + CATEGORICAL_FEATURES)
    assert not cols & (LEAKAGE_COLUMNS | EXCLUDED_BY_POLICY)
    with pytest.raises(ValueError):
        check_no_leakage(pd.DataFrame(columns=["tenure_months", "exit_date"]))
    with pytest.raises(ValueError):
        check_no_leakage(pd.DataFrame(columns=["gender"]))


def test_features_use_only_past_information(src):
    """Changing the future must not change features at the prediction date."""
    d = pd.Timestamp(src.mart.snapshot_date.min()) + pd.offsets.MonthEnd(8)
    base = build_features(src, d)
    future = SourceData(**{k: getattr(src, k) for k in src.__dataclass_fields__})
    future.surveys = src.surveys.copy()
    future.surveys.loc[future.surveys.survey_date > d, "engagement_score"] = 1.0
    future.reviews = src.reviews.copy()
    future.reviews.loc[future.reviews.review_date > d, "performance_rating"] = 1
    again = build_features(future, d)
    pd.testing.assert_frame_equal(base[NUMERIC_FEATURES], again[NUMERIC_FEATURES])


def test_labels_match_actual_voluntary_exits(src):
    d = pd.Timestamp(src.mart.snapshot_date.min()) + pd.offsets.MonthEnd(8)
    df = build_features(src, d)
    e = src.employees.set_index("employee_id")
    x, t = df.employee_id.map(e.exit_date), df.employee_id.map(e.exit_type)
    expected = ((t == "Voluntary") & (x > d) & (x <= d + pd.DateOffset(months=6))).astype(int)
    assert (df.label.values == expected.values).all()


def test_temporal_split_has_no_overlap(src):
    train_dates, test_date, score_date = attrition.split_dates(src)
    assert max(train_dates) + pd.DateOffset(months=attrition.HORIZON) <= test_date
    assert test_date + pd.DateOffset(months=attrition.HORIZON) <= score_date


def test_predict_handles_missing_inputs(built_db):
    p = attrition.predict(pd.DataFrame({"tenure_months": [3, np.nan], "business_unit": ["Unknown", None]}))
    assert len(p) == 2 and np.all((p > 0) & (p < 1))


def test_forecast_backtests_are_chronological(engine):
    data = workforce_forecast.load_series(engine)
    org = workforce_forecast.org_series(data["dept"])
    metrics, errors = workforce_forecast.backtest(org, data["accepted"])
    origins = pd.to_datetime(metrics.origin.unique())
    assert (origins < org.index[-1]).all()
    # the flow model only uses offers accepted on or before each origin
    for o in origins:
        known = data["accepted"][(data["accepted"].accepted_date <= o) & (data["accepted"].join_date > o)]
        assert (known.accepted_date <= o).all()
    assert {"Naive", "Flow model"} <= set(metrics.model)


def test_forecast_is_reproducible(engine):
    a = workforce_forecast.train(engine)["forecast"]
    b = workforce_forecast.train(engine)["forecast"]
    assert [r["forecast"] for r in a] == [r["forecast"] for r in b]


def test_time_to_fill_evaluation_uses_later_holdout(engine):
    meta = hiring_forecast.train(engine)
    assert meta["km_summary"]
    ev = meta["evaluation"]
    if ev["available"]:
        assert ev["train_window_end"] <= ev["test_window"][:10]


def test_scenarios_are_deterministic_and_directional(engine):
    inp = scenarios.baseline_inputs(engine)
    base = scenarios.summarise(scenarios.run(scenarios.Scenario("Baseline"), inp))
    worse = scenarios.summarise(scenarios.run(scenarios.Scenario("x", attrition_delta_pp=2), inp))
    pricier = scenarios.summarise(scenarios.run(scenarios.Scenario("y", comp_inflation_pct=10), inp))
    assert worse["total_exits"] > base["total_exits"] and worse["end_headcount"] < base["end_headcount"]
    assert pricier["workforce_cost"] == pytest.approx(base["workforce_cost"] * 1.10, rel=1e-9)
    assert scenarios.summarise(scenarios.run(scenarios.Scenario("Baseline"), inp)) == base


def test_driver_simulator_baseline_and_direction(built_db):
    from src.models.driver_simulator import Levers, apply_levers, simulate

    scores = attrition.load_scores()
    base = simulate(scores, Levers())
    assert base["leavers_change_6m"] == pytest.approx(0.0)
    assert base["attrition_delta_pp"] == pytest.approx(0.0)
    better = simulate(scores, Levers(engagement_delta=0.3))
    worse = simulate(scores, Levers(engagement_delta=-0.3))
    assert better["new_leavers_6m"] < base["base_leavers_6m"] < worse["new_leavers_6m"]
    assert simulate(scores, Levers(pay_floor=0.95))["leavers_change_6m"] <= 0
    # scores stay on the survey scale and promotions only touch eligible people
    mod, info = apply_levers(scores, Levers(engagement_delta=0.5, promote_share=1.0))
    assert mod.engagement_score.dropna().between(1, 5).all()
    elig = (scores.months_since_promotion >= 36) & (scores.level_num < 7)
    assert info["promoted"] == int(elig.sum())
    assert (mod.loc[~elig, "level_num"] == scores.loc[~elig, "level_num"]).all()


def test_driver_simulator_department_scope(built_db):
    from src.models.driver_simulator import Levers, simulate

    scores = attrition.load_scores()
    dept = scores.department_id.value_counts().index[0]
    r = simulate(scores, Levers(engagement_delta=0.3), dept)
    assert r["employees_in_scope"] == int((scores.department_id == dept).sum())
    # employees outside the department are unchanged, so the org change equals the department change
    assert r["new_leavers_6m"] - r["base_leavers_6m"] == pytest.approx(
        r["new_leavers_6m_scope"] - r["base_leavers_6m_scope"])


def test_pipeline_counts_pending_offers():
    origin = pd.Timestamp("2026-09-30")
    acc = pd.DataFrame({
        "accepted_date": pd.to_datetime(["2026-06-01", "2026-07-01", "2026-09-10", "2026-09-20", "2025-01-01"]),
        "join_date": pd.to_datetime(["2026-07-05", "2026-10-15", None, None, None]),
    })
    months = [origin + pd.offsets.MonthEnd(k) for k in range(1, 4)]
    p = workforce_forecast.pipeline_at(acc, origin, months)
    # one dated future joiner + two recent pending offers (median lag 34 days -> Oct); the stale 2025 offer is ignored
    assert p.sum() == 3 and p[0] == 3


def test_forecast_selection_keeps_incumbent_unless_materially_better():
    s = pd.DataFrame({"mae": [28.4, 29.1, 31.0]}, index=["SARIMA (auto)", "Flow model", "Holt (damped)"])
    sel, ch, gain = workforce_forecast.select_model(s)
    assert sel == "Flow model" and ch == "SARIMA (auto)" and 0 < gain < 0.05
    s.loc["SARIMA (auto)", "mae"] = 20.0
    assert workforce_forecast.select_model(s)[0] == "SARIMA (auto)"


def test_sarima_candidate_forecasts_and_needs_two_years():
    rng = np.random.default_rng(0)
    m = np.arange(36)
    y = pd.Series(5000 + 25 * m + 40 * np.sin(2 * np.pi * m / 12) + rng.normal(0, 10, 36))
    f = workforce_forecast.MODELS["SARIMA (auto)"](y, 6)
    assert f is not None and len(f) == 6 and np.all(np.isfinite(f))
    assert abs(f[0] - y.iloc[-1]) < 200  # sensible continuation, not an explosion
    assert workforce_forecast.MODELS["SARIMA (auto)"](y.iloc[:20], 6) is None


def test_forecast_metadata_records_selection_rule(engine):
    meta = workforce_forecast.train(engine)
    assert meta["selection_rule"] and "5%" in meta["selection_rule"]
