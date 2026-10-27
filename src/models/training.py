"""Deliberate model refresh: trains and evaluates all predictive components."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from src.models import attrition, hiring_forecast, workforce_forecast
from src.models.features import SourceData

log = logging.getLogger(__name__)


def train_all(engine: Engine | None = None) -> dict:
    fc = workforce_forecast.train(engine)
    ttf = hiring_forecast.train(engine)
    att = attrition.train(SourceData.load(engine))
    primary = att["primary_model"]
    best = next(m for m in fc["model_comparison"] if m["model"] == fc["selected_model"])
    naive = next((m for m in fc["model_comparison"] if m["model"] == "Naive"), None)
    ev = ttf.get("evaluation", {})
    return {
        "workforce_forecast": {"selected_model": fc["selected_model"], "backtest_mae": round(best["mae"], 1),
                               "naive_mae": round(naive["mae"], 1) if naive else None},
        "time_to_fill": {r["model"]: round(r["mae"], 1) for r in ev.get("results", [])},
        "attrition": {"primary_model": primary,
                      "roc_auc": round(att["results"][primary]["roc_auc"], 3),
                      "pr_auc": round(att["results"][primary]["pr_auc"], 3),
                      "base_rate": round(att["results"][primary]["base_rate"], 3),
                      "test_date": att["test_date"]},
    }
