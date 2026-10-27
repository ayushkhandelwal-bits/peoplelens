"""Headcount, exits and hiring-demand forecasting.

Candidate models (organisation level):

* **Naive** – the last observed headcount carried forward (baseline).
* **Drift** – random walk with the average historical monthly change.
* **Holt (damped)** – additive damped-trend exponential smoothing.
* **Holt-Winters** – additive trend + 12-month seasonality (only when at least
  two seasonal cycles are available in the training window).
* **Flow model** – a structural model: next month's headcount = headcount
  - expected exits (headcount x seasonal monthly exit rate, rescaled to the
  trailing 12-month level) + expected joiners (accepted offers already
  scheduled to join x historical offer-to-join rate, topped up to the trailing
  average hiring level).

Evaluation uses rolling-origin backtests with a fixed 6-month horizon and
strictly chronological splits (no shuffling). The model with the lowest mean
MAE across backtests is selected; prediction intervals come from backtest
errors by horizon (normal approximation), so they reflect real out-of-sample
error rather than in-sample fit.
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from database.connection import get_engine, read_sql
from src.models.model_evaluation import MODEL_VERSION, load_json, regression_metrics, save_json

log = logging.getLogger(__name__)
HORIZON = 6
Z80 = 1.2816


# ------------------------------------------------------------------- data

def load_series(engine: Engine | None = None) -> dict[str, pd.DataFrame]:
    engine = engine or get_engine()
    m = read_sql("""SELECT snapshot_date AS month, department_id, SUM(active_flag) AS headcount,
                           SUM(joined_in_month) AS joiners, SUM(exited_in_month) AS exits
                    FROM mart_employee_month GROUP BY snapshot_date, department_id""", engine=engine,
                 parse_dates=["month"])
    acc = read_sql("""SELECT a.candidate_id, a.event_date AS accepted_date, j.event_date AS join_date,
                             j.event_status AS join_status, r.department_id
                      FROM recruitment_events a
                      JOIN job_requisitions r ON r.job_requisition_id = a.job_requisition_id
                      LEFT JOIN recruitment_events j ON j.candidate_id = a.candidate_id AND j.event_type = 'Joining'
                      WHERE a.event_type = 'Offer Accepted'""", engine=engine,
                   parse_dates=["accepted_date", "join_date"])
    plan_all = read_sql("""SELECT planning_period AS month, department_id, planned_headcount, planned_hires
                           FROM workforce_budget WHERE scenario_name = 'AOP'""", engine=engine, parse_dates=["month"])
    plan = plan_all[["month", "department_id", "planned_headcount"]]
    depts = read_sql("SELECT department_id, department_name FROM departments", engine=engine)
    return {"dept": m, "accepted": acc, "plan": plan, "plan_hires": plan_all[["month", "department_id", "planned_hires"]],
            "depts": depts}


def org_series(dept: pd.DataFrame) -> pd.DataFrame:
    return dept.groupby("month")[["headcount", "joiners", "exits"]].sum().sort_index()


# --------------------------------------------------------------- models

def _naive(y: pd.Series, h: int) -> np.ndarray:
    return np.repeat(float(y.iloc[-1]), h)


def _drift(y: pd.Series, h: int) -> np.ndarray:
    slope = (y.iloc[-1] - y.iloc[0]) / max(1, len(y) - 1)
    return float(y.iloc[-1]) + slope * np.arange(1, h + 1)


def _holt(y: pd.Series, h: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(y.values.astype(float), trend="add", damped_trend=True).fit(optimized=True)
    return fit.forecast(h)


def _holt_winters(y: pd.Series, h: int) -> np.ndarray | None:
    if len(y) < 24:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(y.values.astype(float), trend="add", damped_trend=True, seasonal="add",
                                   seasonal_periods=12).fit(optimized=True)
    return fit.forecast(h)


def seasonal_exit_rates(hist: pd.DataFrame) -> dict[int, float]:
    """Monthly exit rate by calendar month, rescaled to the trailing-12-month level."""
    df = hist.copy()
    df["opening"] = df.headcount.shift(1).fillna(df.headcount)
    df["rate"] = df.exits / df.opening
    by_month = df.groupby(df.index.month).rate.mean()
    overall = df.rate.mean()
    trailing = df.rate.iloc[-12:].mean() if len(df) >= 12 else overall
    scale = trailing / overall if overall else 1.0
    rates = {m: float(by_month.get(m, overall) * scale) for m in range(1, 13)}
    return rates


PENDING_WINDOW_DAYS = 100  # an accepted offer older than this without a join record is not a live pipeline


def pipeline_at(accepted: pd.DataFrame, origin: pd.Timestamp, months: list[pd.Timestamp]) -> np.ndarray:
    """Accepted offers known at ``origin`` expected to join in each future month.

    Offers with a recorded joining date after ``origin`` use that date. Offers
    still pending (accepted recently, no joining record yet — typical at the
    extraction date) are placed at accepted date + the median accept-to-join
    gap observed before ``origin`` (never earlier than the next day).
    """
    known = accepted[(accepted.accepted_date <= origin) & (accepted.join_date > origin)]
    dates = known.join_date
    pending = accepted[(accepted.accepted_date <= origin) & accepted.join_date.isna()
                       & (accepted.accepted_date > origin - pd.Timedelta(days=PENDING_WINDOW_DAYS))]
    if len(pending):
        done = accepted[accepted.join_date <= origin]
        lag = (done.join_date - done.accepted_date).dt.days.median() if len(done) else 35
        exp = (pending.accepted_date + pd.to_timedelta(lag, unit="D")).clip(lower=origin + pd.Timedelta(days=1))
        dates = pd.concat([dates, exp])
    jm = dates + pd.offsets.MonthEnd(0)
    return np.array([int((jm == m).sum()) for m in months])


def join_rate(accepted: pd.DataFrame, origin: pd.Timestamp) -> float:
    done = accepted[(accepted.join_date <= origin) & accepted.join_status.notna()]
    if done.empty:
        return 0.88
    return float((done.join_status == "Joined").mean())


def flow_forecast(hist: pd.DataFrame, accepted: pd.DataFrame, h: int, scale_hires: float = 1.0
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    origin = hist.index[-1]
    months = [origin + pd.offsets.MonthEnd(k) for k in range(1, h + 1)]
    rates = seasonal_exit_rates(hist)
    jr = join_rate(accepted, origin)
    pipe = pipeline_at(accepted, origin, months) * jr
    avg_hires = float(hist.joiners.iloc[-6:].mean()) * scale_hires
    hc = float(hist.headcount.iloc[-1])
    out_hc, out_exits, out_hires = [], [], []
    for k, m in enumerate(months):
        exits = hc * rates[m.month]
        hires = max(pipe[k], avg_hires)
        hc = hc - exits + hires
        out_hc.append(hc)
        out_exits.append(exits)
        out_hires.append(hires)
    return np.array(out_hc), np.array(out_exits), np.array(out_hires)


MODELS = {"Naive": _naive, "Drift": _drift, "Holt (damped)": _holt, "Holt-Winters": _holt_winters}


def _forecast_all(hist: pd.DataFrame, accepted: pd.DataFrame, h: int) -> dict[str, np.ndarray]:
    out = {}
    for name, fn in MODELS.items():
        f = fn(hist.headcount, h)
        if f is not None:
            out[name] = np.asarray(f, float)
    out["Flow model"] = flow_forecast(hist, accepted, h)[0]
    return out


def backtest(org: pd.DataFrame, accepted: pd.DataFrame, h: int = HORIZON, n_origins: int = 3
             ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling-origin evaluation. Returns (metrics by model, errors by model x horizon)."""
    rows, errs = [], []
    last = len(org) - h
    origins = [last - 3 * k for k in range(n_origins) if last - 3 * k >= 12]
    for o in sorted(origins):
        hist, actual = org.iloc[:o], org.iloc[o:o + h]
        for name, f in _forecast_all(hist, accepted, h).items():
            e = f[: len(actual)] - actual.headcount.values
            for k, v in enumerate(e, start=1):
                errs.append(dict(model=name, origin=hist.index[-1], horizon=k, error=float(v)))
            rows.append(dict(model=name, origin=hist.index[-1].date().isoformat(),
                             **regression_metrics(actual.headcount.values, f[: len(actual)])))
        # exits baselines
        seas = [org.exits.iloc[o + k - 12] if o + k - 12 >= 0 else hist.exits.iloc[-1] for k in range(h)]
        flow_exits = flow_forecast(hist, accepted, h)[1]
        for name, f in (("Exits: seasonal naive", np.array(seas, float)),
                        ("Exits: trailing mean", np.repeat(hist.exits.iloc[-6:].mean(), h)),
                        ("Exits: flow model", flow_exits)):
            rows.append(dict(model=name, origin=hist.index[-1].date().isoformat(),
                             **regression_metrics(actual.exits.values, f[: len(actual)])))
    return pd.DataFrame(rows), pd.DataFrame(errs)


def _dept_forecast(data: dict, h: int) -> pd.DataFrame:
    dept, accepted = data["dept"], data["accepted"]
    org = org_series(dept)
    origin = org.index[-1]
    months = [origin + pd.offsets.MonthEnd(k) for k in range(1, h + 1)]
    org_rates = seasonal_exit_rates(org)
    org_trailing = org.exits.iloc[-12:].sum() / org.headcount.iloc[-12:].mean()
    rows = []
    for d, g in dept.groupby("department_id"):
        g = g.set_index("month").sort_index()
        if g.headcount.iloc[-1] == 0:
            continue
        trailing = g.exits.iloc[-12:].sum() / max(1, g.headcount.iloc[-12:].mean())
        ratio = trailing / org_trailing if org_trailing else 1.0
        acc = accepted[accepted.department_id == d]
        jr = join_rate(accepted, origin)
        pipe = pipeline_at(acc, origin, months) * jr
        avg_hires = float(g.joiners.iloc[-6:].mean())
        hc = float(g.headcount.iloc[-1])
        for k, m in enumerate(months):
            exits = hc * org_rates[m.month] * ratio
            hires = max(pipe[k], avg_hires)
            hc = hc - exits + hires
            rows.append(dict(department_id=d, month=m, forecast_headcount=hc, expected_exits=exits,
                             expected_hires=hires, pipeline_joiners=pipe[k]))
    out = pd.DataFrame(rows)
    out = out.merge(data["plan"], on=["department_id", "month"], how="left").merge(data["depts"], on="department_id")
    out["gap_vs_plan"] = out.forecast_headcount - out.planned_headcount
    return out


def hiring_demand(data: dict, dept_fc: pd.DataFrame, engine: Engine | None = None) -> pd.DataFrame:
    """Hires required over the horizon to reach the plan, net of the known pipeline."""
    dept = data["dept"]
    origin = dept.month.max()
    current = dept[dept.month == origin].set_index("department_id").headcount
    engine = engine or get_engine()
    open_reqs = read_sql("""SELECT department_id, COUNT(*) AS open_requisitions FROM job_requisitions
                            WHERE requisition_status = 'Open' GROUP BY department_id""", engine=engine)
    g = dept_fc.groupby(["department_id", "department_name"]).agg(
        expected_exits=("expected_exits", "sum"), pipeline_joiners=("pipeline_joiners", "sum"),
        planned_headcount_end=("planned_headcount", "last"), forecast_headcount_end=("forecast_headcount", "last"),
    ).reset_index()
    g["current_headcount"] = g.department_id.map(current)
    g = g.merge(open_reqs, on="department_id", how="left").fillna({"open_requisitions": 0})
    g["hires_required"] = (g.planned_headcount_end - g.current_headcount + g.expected_exits).clip(lower=0)
    months = sorted(dept_fc.month.unique())
    ph = data["plan_hires"]
    ph = ph[ph.month.isin(months)].groupby("department_id").planned_hires.sum()
    g["planned_hires"] = g.department_id.map(ph).fillna(0)
    g["covered_by_pipeline"] = g.pipeline_joiners + 0.8 * g.open_requisitions
    g["additional_requisitions"] = (g.hires_required - g.covered_by_pipeline).clip(lower=0).round()
    # demand above the AOP: hires needed exceed the hires the plan budgeted for these months
    g["demand_exceeds_plan"] = g.hires_required > g.planned_hires
    return g.sort_values("additional_requisitions", ascending=False)


def train(engine: Engine | None = None) -> dict:
    data = load_series(engine)
    org = org_series(data["dept"])
    metrics, errors = backtest(org, data["accepted"])
    hc_models = metrics[~metrics.model.str.startswith("Exits")]
    summary = hc_models.groupby("model")[["mae", "rmse", "mape"]].mean().sort_values("mae")
    best = summary.index[0]
    exits_summary = metrics[metrics.model.str.startswith("Exits")].groupby("model")[["mae", "rmse"]].mean()

    fc_all = _forecast_all(org, data["accepted"], HORIZON)
    origin = org.index[-1]
    months = [origin + pd.offsets.MonthEnd(k) for k in range(1, HORIZON + 1)]
    rmse_h = errors[errors.model == best].groupby("horizon").error.apply(lambda e: float(np.sqrt(np.mean(e ** 2))))
    floor = float(np.std(np.diff(org.headcount.values))) * np.sqrt(np.arange(1, HORIZON + 1)) * 0.5
    width = np.maximum(rmse_h.reindex(range(1, HORIZON + 1)).ffill().fillna(floor[0]).values, floor)
    fc = fc_all[best]
    _, exits_fc, hires_fc = flow_forecast(org, data["accepted"], HORIZON)
    forecast = pd.DataFrame({"month": months, "forecast": fc, "lower_80": fc - Z80 * width,
                             "upper_80": fc + Z80 * width, "expected_exits": exits_fc, "expected_hires": hires_fc})
    for name, f in fc_all.items():
        forecast[f"model::{name}"] = f
    dept_fc = _dept_forecast(data, HORIZON)
    demand = hiring_demand(data, dept_fc, engine)
    meta = dict(
        model_version=MODEL_VERSION, trained_at=datetime.now().isoformat(timespec="seconds"),
        horizon_months=HORIZON, selected_model=best, history_months=int(len(org)),
        history_start=org.index[0].date().isoformat(), history_end=origin.date().isoformat(),
        backtest_origins=sorted(metrics.origin.unique().tolist()),
        model_comparison=summary.reset_index().to_dict("records"),
        exits_comparison=exits_summary.reset_index().to_dict("records"),
        backtests=metrics.to_dict("records"),
        history=org.reset_index().assign(month=lambda d: d.month.dt.date.astype(str)).to_dict("records"),
        forecast=forecast.assign(month=forecast.month.dt.date.astype(str)).to_dict("records"),
        department_forecast=dept_fc.assign(month=dept_fc.month.dt.date.astype(str)).to_dict("records"),
        hiring_demand=demand.to_dict("records"),
        assumptions=["Exit rates follow the historical calendar-month pattern rescaled to the last 12 months.",
                     "Accepted offers join on their agreed date at the historical offer-to-join rate.",
                     "Hiring beyond the known pipeline continues at the trailing 6-month average.",
                     "Department forecasts ignore internal transfers (net effect is small)."],
        limitations=["36 months of history allow only limited seasonal estimation.",
                     "Prediction intervals use three backtest origins and are approximate.",
                     "Forecasts assume no policy change; use the scenario tools for what-ifs."],
    )
    save_json("workforce_forecast.json", meta)
    log.info("workforce forecast trained: selected %s (MAE %.1f)", best, summary.iloc[0].mae)
    return meta


def load_meta() -> dict | None:
    return load_json("workforce_forecast.json")
