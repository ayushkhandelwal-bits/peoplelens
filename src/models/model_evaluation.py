"""Evaluation utilities and the model artefact registry."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

from config.settings import ARTIFACT_DIR

MODEL_VERSION = "1.0.0"


# ------------------------------------------------------------ regression

def regression_metrics(actual, predicted) -> dict:
    a, p = np.asarray(actual, float), np.asarray(predicted, float)
    err = p - a
    mape = float(np.mean(np.abs(err) / np.where(a == 0, np.nan, np.abs(a)))) if len(a) else None
    return {"mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2))),
            "mape": None if mape is None or np.isnan(mape) else mape, "bias": float(np.mean(err)), "n": int(len(a))}


# -------------------------------------------------------- classification

def best_f1_threshold(y, p) -> float:
    prec, rec, thr = precision_recall_curve(y, p)
    f1 = 2 * prec * rec / np.where(prec + rec == 0, 1, prec + rec)
    if len(thr) == 0:
        return 0.5
    return float(thr[int(np.nanargmax(f1[:-1]))])


def classification_report(y, p, threshold: float) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    fpr, tpr, _ = roc_curve(y, p)
    prec, rec, _ = precision_recall_curve(y, p)
    # reliability (calibration) by decile of predicted risk
    bins = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False)
    calib = pd.DataFrame({"bin": bins, "p": p, "y": y}).groupby("bin").agg(predicted=("p", "mean"),
                                                                         observed=("y", "mean"), n=("y", "size"))
    top = pd.Series(p).rank(ascending=False, method="first") <= max(1, int(len(p) * 0.1))
    lift = (y[top.values].mean() / y.mean()) if y.mean() > 0 else None
    return {
        "roc_auc": float(roc_auc_score(y, p)), "pr_auc": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)), "base_rate": float(y.mean()), "threshold": float(threshold),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)), "f1": float(f1_score(y, pred, zero_division=0)),
        "accuracy": float((pred == y).mean()), "top_decile_lift": None if lift is None else float(lift),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "roc_curve": {"fpr": fpr[:: max(1, len(fpr) // 200)].tolist(), "tpr": tpr[:: max(1, len(tpr) // 200)].tolist()},
        "pr_curve": {"precision": prec[:: max(1, len(prec) // 200)].tolist(),
                     "recall": rec[:: max(1, len(rec) // 200)].tolist()},
        "calibration": calib.reset_index().to_dict("records"), "n": int(len(y)),
    }


# --------------------------------------------------------------- registry

def artifact_path(name: str) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_DIR / name


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, pd.DataFrame):
        return o.to_dict("records")
    raise TypeError(type(o))


def save_json(name: str, payload: dict) -> Path:
    path = artifact_path(name)
    path.write_text(json.dumps(payload, default=_jsonable, indent=2), encoding="utf-8")
    return path


def load_json(name: str) -> dict | None:
    path = artifact_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_model(name: str, model) -> Path:
    path = artifact_path(name)
    joblib.dump(model, path)
    return path


def load_model(name: str):
    path = artifact_path(name)
    return joblib.load(path) if path.exists() else None


def models_available() -> bool:
    return all(artifact_path(n).exists() for n in ("attrition_model.json", "workforce_forecast.json",
                                                    "hiring_forecast.json"))
