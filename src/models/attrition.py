"""Voluntary attrition risk model (6-month horizon) with temporal validation.

Design
------
* Unit of analysis: employee x prediction date (quarter ends).
* Temporal split: the test snapshot is the latest prediction date whose
  6-month outcome window is fully observed. Training snapshots are earlier
  dates whose outcome windows end on or before the test date, so no outcome
  information from the test window can leak into training.
* Threshold: chosen to maximise F1 on the most recent *training* snapshot
  (model fitted on the snapshots before it), then frozen for the test set.
* Models: a regularised logistic regression (primary, interpretable) and a
  histogram gradient-boosting classifier (challenger).
* Use: risk scores support **aggregate** workforce-planning conversations and
  earlier, supportive check-ins. They must not drive disciplinary,
  termination or any other automated employment decision.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.models.features import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    SourceData,
    build_dataset,
    check_no_leakage,
    impute_frame,
    quarter_ends,
)
from src.models.model_evaluation import (
    MODEL_VERSION,
    best_f1_threshold,
    classification_report,
    load_json,
    load_model,
    save_json,
    save_model,
)

log = logging.getLogger(__name__)
HORIZON = 6
FEATURE_LABELS = {
    "tenure_months": "Tenure (months)", "level_num": "Job level", "contract": "Contract employee",
    "engagement_score": "Latest engagement score", "engagement_missing": "No recent survey response",
    "engagement_change": "Engagement change vs previous survey", "manager_support_score": "Manager support score",
    "career_growth_score": "Career growth score", "last_rating": "Latest performance rating",
    "rating_missing": "No completed rating", "pay_vs_peer_median": "Pay vs peer median",
    "months_since_promotion": "Months since promotion/hire", "manager_change_6m": "Manager change (6m)",
    "team_size": "Manager's team size", "training_hours_12m": "Development hours (12m)",
    "dept_trailing_attrition": "Department trailing attrition",
}


def _logit_pipeline() -> Pipeline:
    pre = ColumnTransformer([
        ("num", StandardScaler(), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), CATEGORICAL_FEATURES),
    ])
    return Pipeline([("pre", pre), ("clf", LogisticRegression(C=0.5, max_iter=2000))])


def _gbm_pipeline() -> Pipeline:
    pre = ColumnTransformer([
        ("num", "passthrough", NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20, sparse_output=False), CATEGORICAL_FEATURES),
    ])
    clf = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=60,
                                         l2_regularization=1.0, random_state=42)
    return Pipeline([("pre", pre), ("clf", clf)])


def split_dates(src: SourceData) -> tuple[list[pd.Timestamp], pd.Timestamp, pd.Timestamp]:
    start, end = src.mart.snapshot_date.min(), src.mart.snapshot_date.max()
    test_date = pd.Timestamp(end) - pd.offsets.MonthEnd(HORIZON)
    first = pd.Timestamp(start) + pd.offsets.MonthEnd(5)  # allow some feature history
    last_train = test_date - pd.offsets.MonthEnd(HORIZON)
    train_dates = quarter_ends(first, last_train)
    if not train_dates:
        raise ValueError("Not enough history for a temporal split (need >= 18 months).")
    return train_dates, test_date, pd.Timestamp(end)


def train(src: SourceData | None = None) -> dict:
    src = src or SourceData.load()
    train_dates, test_date, score_date = split_dates(src)
    train_df = build_dataset(src, train_dates, HORIZON)
    test_df = build_dataset(src, [test_date], HORIZON)
    check_no_leakage(train_df[NUMERIC_FEATURES + CATEGORICAL_FEATURES])
    train_df, medians = impute_frame(train_df)
    test_df, _ = impute_frame(test_df, medians)
    X_cols = NUMERIC_FEATURES + CATEGORICAL_FEATURES

    # threshold selection on the last training snapshot (fit on earlier ones)
    val_date = train_dates[-1]
    inner = train_df[train_df.prediction_date < val_date]
    val = train_df[train_df.prediction_date == val_date]
    thresholds = {}
    for name, make in (("logistic_regression", _logit_pipeline), ("gradient_boosting", _gbm_pipeline)):
        if len(inner) and inner.label.nunique() == 2:
            m = make().fit(inner[X_cols], inner.label)
            thresholds[name] = best_f1_threshold(val.label, m.predict_proba(val[X_cols])[:, 1])
        else:
            thresholds[name] = float(train_df.label.mean() * 2)

    results, models = {}, {}
    for name, make in (("logistic_regression", _logit_pipeline), ("gradient_boosting", _gbm_pipeline)):
        model = make().fit(train_df[X_cols], train_df.label)
        p = model.predict_proba(test_df[X_cols])[:, 1]
        results[name] = classification_report(test_df.label.values, p, thresholds[name])
        models[name] = model

    # explanations
    lr = models["logistic_regression"]
    names = lr.named_steps["pre"].get_feature_names_out()
    coefs = pd.DataFrame({"feature": names, "coefficient": lr.named_steps["clf"].coef_[0]})
    coefs["odds_ratio"] = np.exp(coefs.coefficient)
    coefs["label"] = coefs.feature.str.replace("num__", "").str.replace("cat__", "").map(
        lambda f: FEATURE_LABELS.get(f, f.replace("_", " ")))
    coefs = coefs.reindex(coefs.coefficient.abs().sort_values(ascending=False).index)
    sample = test_df.sample(n=min(3000, len(test_df)), random_state=7)
    perm = permutation_importance(models["gradient_boosting"], sample[X_cols], sample.label, scoring="roc_auc",
                                  n_repeats=5, random_state=7)
    perm_df = pd.DataFrame({"feature": X_cols, "importance": perm.importances_mean, "std": perm.importances_std})
    perm_df["label"] = perm_df.feature.map(lambda f: FEATURE_LABELS.get(f, f.replace("_", " ")))
    perm_df = perm_df.sort_values("importance", ascending=False)

    auc_lr, auc_gb = results["logistic_regression"]["roc_auc"], results["gradient_boosting"]["roc_auc"]
    primary = "gradient_boosting" if auc_gb - auc_lr > 0.02 else "logistic_regression"

    # score the current workforce (no labels: outcome not yet known)
    current = build_dataset(src, [score_date], HORIZON, with_label=False)
    current, _ = impute_frame(current, medians)
    current["risk"] = models[primary].predict_proba(current[X_cols])[:, 1]
    current["risk_band"] = pd.cut(current.risk, [-1, results[primary]["threshold"] * 0.6,
                                                 results[primary]["threshold"], 2],
                                  labels=["Lower", "Elevated", "High"])

    meta = dict(
        model_version=MODEL_VERSION, trained_at=datetime.now().isoformat(timespec="seconds"),
        horizon_months=HORIZON, primary_model=primary,
        train_dates=[d.date().isoformat() for d in train_dates], test_date=test_date.date().isoformat(),
        score_date=score_date.date().isoformat(), train_rows=int(len(train_df)), test_rows=int(len(test_df)),
        train_positive_rate=float(train_df.label.mean()), test_positive_rate=float(test_df.label.mean()),
        features=X_cols, medians=medians, results=results,
        coefficients=coefs[["feature", "label", "coefficient", "odds_ratio"]].to_dict("records"),
        permutation_importance=perm_df.to_dict("records"),
        assumptions=["Label = voluntary exit within 6 months of the prediction date.",
                     "Features use only information dated on or before the prediction date.",
                     "Gender and names are excluded; location is included to test for spurious effects."],
        limitations=["Synthetic data: relationships reflect simulation assumptions, not real-world causes.",
                     "Associations are not causal: e.g. a low engagement score is a signal, not a mechanism.",
                     "Survey non-response is itself informative and is captured as a feature.",
                     "Scores are probabilities for groups of similar employees, not predictions about a person."],
    )
    save_json("attrition_model.json", meta)
    save_model("attrition_model.joblib", models[primary])
    cols = ["employee_id", "department_id"] + NUMERIC_FEATURES + CATEGORICAL_FEATURES + ["risk", "risk_band"]
    current[cols].to_csv(_scores_path(), index=False)
    log.info("attrition model trained: LR AUC %.3f, GBM AUC %.3f (primary %s)", auc_lr, auc_gb, primary)
    return meta


def _scores_path():
    from src.models.model_evaluation import artifact_path

    return artifact_path("attrition_scores.csv")


def load_meta() -> dict | None:
    return load_json("attrition_model.json")


def load_scores() -> pd.DataFrame | None:
    p = _scores_path()
    return pd.read_csv(p) if p.exists() else None


def predict(df: pd.DataFrame) -> np.ndarray:
    """Score a feature frame; tolerant of missing columns and values."""
    meta, model = load_meta(), load_model("attrition_model.joblib")
    if meta is None or model is None:
        raise RuntimeError("Attrition model not trained. Run scripts/train_models.py.")
    df = df.copy()
    for c in NUMERIC_FEATURES:
        if c not in df:
            df[c] = np.nan
    for c in CATEGORICAL_FEATURES:
        if c not in df:
            df[c] = "Unknown"
    df, _ = impute_frame(df, meta["medians"])
    return model.predict_proba(df[NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
