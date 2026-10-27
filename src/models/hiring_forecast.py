"""Time-to-fill estimation for hiring plans.

* Kaplan-Meier curves by job family use *all* requisitions, treating open ones
  as censored, so long searches are not ignored.
* A point predictor is evaluated on a chronological hold-out: requisitions
  opened in a later window (with at least nine months of follow-up, which keeps
  censoring negligible) are predicted from requisitions opened earlier.
  Candidates: global median (baseline), segment median (job family x level
  band), and gradient boosting with a median (quantile) loss.
* The planning output is the P50 and P80 time-to-fill per job family: opening a
  requisition P80 days (+ notice period) ahead of need gives an ~80% chance of
  an accepted offer in time.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sqlalchemy.engine import Engine
from statsmodels.duration.survfunc import SurvfuncRight

from database.connection import get_engine, read_sql
from src.models.model_evaluation import MODEL_VERSION, load_json, regression_metrics, save_json

log = logging.getLogger(__name__)


def level_band(level: str) -> str:
    n = int(str(level)[1:])
    return "L1-L2" if n <= 2 else "L3-L4" if n <= 4 else "L5+"


def load_requisitions(engine: Engine | None = None) -> pd.DataFrame:
    df = read_sql("""SELECT r.job_requisition_id, r.job_family, r.job_level, r.requisition_type, r.opened_date,
                            r.offer_accepted_date, r.requisition_status, r.closed_date, l.city AS location,
                            d.business_unit
                     FROM job_requisitions r JOIN locations l ON l.location_id = r.location_id
                     JOIN departments d ON d.department_id = r.department_id""", engine=engine or get_engine(),
                  parse_dates=["opened_date", "offer_accepted_date", "closed_date"])
    end = df[["opened_date", "offer_accepted_date", "closed_date"]].max().max()
    df["filled"] = df.offer_accepted_date.notna().astype(int)
    # cancelled requisitions are censored at cancellation; open ones at extraction
    stop = df.offer_accepted_date.fillna(df.closed_date).fillna(end)
    df["days"] = (stop - df.opened_date).dt.days.clip(lower=1)
    df["level_band"] = df.job_level.map(level_band)
    df.attrs["extraction_date"] = end
    return df


def km_by_family(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    curves, summary = [], []
    for fam, g in df.groupby("job_family"):
        if len(g) < 20:
            continue
        sf = SurvfuncRight(g.days.values.astype(float), g.filled.values)
        t, s = sf.surv_times, sf.surv_prob
        p50 = float(t[np.argmax(s <= 0.5)]) if (s <= 0.5).any() else None
        p80 = float(t[np.argmax(s <= 0.2)]) if (s <= 0.2).any() else None
        curves.append(pd.DataFrame({"job_family": fam, "days": np.r_[0, t], "share_unfilled": np.r_[1, s]}))
        summary.append(dict(job_family=fam, requisitions=int(len(g)), filled=int(g.filled.sum()),
                            censored=int((g.filled == 0).sum()), km_median_days=p50, km_p80_days=p80,
                            naive_median_days=float(g[g.filled == 1].days.median())))
    return pd.concat(curves, ignore_index=True), pd.DataFrame(summary).sort_values("km_median_days")


def _gbm() -> Pipeline:
    cats = ["job_family", "level_band", "requisition_type", "location", "business_unit"]
    pre = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cats)])
    return Pipeline([("pre", pre), ("reg", HistGradientBoostingRegressor(loss="quantile", quantile=0.5,
                                                                         max_iter=200, learning_rate=0.05,
                                                                         min_samples_leaf=40, random_state=42))])


def evaluate(df: pd.DataFrame) -> dict:
    end = df.attrs["extraction_date"]
    test_end = end - pd.DateOffset(months=9)
    test_start = test_end - pd.DateOffset(months=6)
    train = df[(df.opened_date < test_start) & (df.filled == 1)]
    test = df[(df.opened_date >= test_start) & (df.opened_date < test_end) & (df.requisition_status != "Cancelled")]
    still_open = int((test.filled == 0).sum())
    test = test[test.filled == 1]
    if len(train) < 50 or len(test) < 20:
        return {"available": False}
    preds = {"Global median (baseline)": np.repeat(train.days.median(), len(test))}
    seg = train.groupby(["job_family", "level_band"]).days.median()
    fam = train.groupby("job_family").days.median()
    preds["Segment median"] = [seg.get((a, b), fam.get(a, train.days.median()))
                               for a, b in zip(test.job_family, test.level_band)]
    model = _gbm().fit(train, train.days)
    preds["Gradient boosting (median loss)"] = model.predict(test)
    results = [dict(model=k, **regression_metrics(test.days.values, v)) for k, v in preds.items()]
    return {"available": True, "train_window_end": test_start.date().isoformat(),
            "test_window": f"{test_start.date()} to {test_end.date()}", "train_n": int(len(train)),
            "test_n": int(len(test)), "test_still_open": still_open, "results": results}


def train(engine: Engine | None = None) -> dict:
    df = load_requisitions(engine)
    curves, summary = km_by_family(df)
    ev = evaluate(df)
    meta = dict(
        model_version=MODEL_VERSION, trained_at=datetime.now().isoformat(timespec="seconds"),
        extraction_date=df.attrs["extraction_date"].date().isoformat(), requisitions=int(len(df)),
        km_summary=summary.to_dict("records"),
        km_curves=curves.iloc[::2].to_dict("records"),
        evaluation=ev,
        assumptions=["Time-to-fill runs from requisition opening to offer acceptance.",
                     "Open and cancelled requisitions are right-censored, not dropped.",
                     "Notice periods (acceptance to joining) are planned separately."],
        limitations=["Recruiter capacity and market conditions are not modelled.",
                     "Hold-out MAE is measured on filled requisitions only."],
    )
    save_json("hiring_forecast.json", meta)
    log.info("time-to-fill models evaluated on %s requisitions", ev.get("test_n"))
    return meta


def load_meta() -> dict | None:
    return load_json("hiring_forecast.json")
