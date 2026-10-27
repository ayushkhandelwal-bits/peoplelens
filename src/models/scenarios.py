"""Deterministic workforce scenario engine (what-if calculations).

This is **not** a trained model: it projects headcount and cost forward with
explicit, editable assumptions so a planner can see the consequence of a
change. For each department and month ``t``::

    exits_t   = headcount_{t-1} x (baseline annual exit rate + delta_pp) / 12
    hires_t   = known pipeline joiners_t + (baseline monthly hires x (1 + hiring change)
                + extra target / hiring months)          [0 during an added hiring delay]
    headcount_t = headcount_{t-1} - exits_t + hires_t
    cost_t    = headcount_t x monthly cost per head x (1 + compensation inflation)
    turnover_cost_t = exits_t x estimated replacement cost per exit

Baselines come from the last 12 months of actuals (exit rate), the last
6 months (hiring pace) and the latest month (cost per head).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from database.connection import get_engine, read_sql
from src.models.workforce_forecast import join_rate, load_series, pipeline_at


@dataclass(frozen=True)
class Scenario:
    name: str = "Baseline"
    attrition_delta_pp: float = 0.0  # annual percentage points added to every department
    hiring_change_pct: float = 0.0  # relative change in hiring pace
    hiring_delay_months: int = 0  # additional months before new (non-pipeline) hires arrive
    comp_inflation_pct: float = 0.0  # increase in cost per head
    extra_headcount: dict = field(default_factory=dict)  # department_id -> additional heads to hire
    horizon_months: int = 6

    def to_dict(self) -> dict:
        return asdict(self)


PRESETS = [
    Scenario("Baseline"),
    Scenario("Higher attrition (+2 pp)", attrition_delta_pp=2.0),
    Scenario("Improved retention (-2 pp)", attrition_delta_pp=-2.0),
    Scenario("Increased hiring (+15%)", hiring_change_pct=15.0),
    Scenario("Hiring delay (+1 month)", hiring_delay_months=1),
    Scenario("Compensation inflation (+8%)", comp_inflation_pct=8.0),
]


def baseline_inputs(engine: Engine | None = None, cost_per_exit: float | None = None) -> dict:
    engine = engine or get_engine()
    data = load_series(engine)
    dept = data["dept"]
    origin = dept.month.max()
    cost = read_sql("""SELECT department_id, SUM(monthly_total_cost) AS cost, SUM(active_flag) AS hc
                       FROM mart_employee_month WHERE snapshot_date = :d GROUP BY department_id""",
                    {"d": origin.date()}, engine)
    rows = []
    for d, g in dept.groupby("department_id"):
        g = g.sort_values("month")
        hc0 = float(g.headcount.iloc[-1])
        if hc0 == 0:
            continue
        last12 = g.iloc[-12:]
        rate = float(last12.exits.sum() / max(1.0, last12.headcount.mean()) * 12 / len(last12))
        rows.append(dict(department_id=d, headcount=hc0, annual_exit_rate=rate,
                         monthly_hires=float(g.joiners.iloc[-6:].mean())))
    base = pd.DataFrame(rows).merge(cost, on="department_id", how="left").merge(data["depts"], on="department_id")
    base["cost_per_head"] = base.cost / base.hc
    months = [origin + pd.offsets.MonthEnd(k) for k in range(1, 25)]
    jr = join_rate(data["accepted"], origin)
    pipes = {d: pipeline_at(data["accepted"][data["accepted"].department_id == d], origin, months) * jr
             for d in base.department_id}
    if cost_per_exit is None:
        from src.analytics.compensation import observed_defaults

        a = observed_defaults(engine)
        cost_per_exit = a.cost_per_hire + a.onboarding_cost + (a.ramp_months * a.ramp_productivity_loss
                                                               + a.vacancy_days / 30.44 * a.vacancy_coverage_gap
                                                               ) * float((base.cost_per_head * base.headcount).sum()
                                                                         / base.headcount.sum())
    return dict(origin=origin, base=base, months=months, pipeline=pipes, plan=data["plan"],
                cost_per_exit=float(cost_per_exit))


def run(s: Scenario, inputs: dict) -> pd.DataFrame:
    base, months = inputs["base"], inputs["months"][: s.horizon_months]
    rows = []
    hiring_months = max(1, s.horizon_months - s.hiring_delay_months)
    for _, b in base.iterrows():
        hc = b.headcount
        rate = max(0.0, b.annual_exit_rate + s.attrition_delta_pp / 100)
        pipe = inputs["pipeline"].get(b.department_id, np.zeros(len(months)))
        extra = float(s.extra_headcount.get(b.department_id, 0)) / hiring_months
        for k, m in enumerate(months):
            exits = hc * rate / 12
            new = 0.0 if k < s.hiring_delay_months else b.monthly_hires * (1 + s.hiring_change_pct / 100) + extra
            # the known pipeline is not affected by delays; it replaces part of the run-rate
            p = pipe[k] if k < len(pipe) else 0.0
            hires = max(p, new) if k >= s.hiring_delay_months else p
            hc = hc - exits + hires
            cost = hc * b.cost_per_head * (1 + s.comp_inflation_pct / 100)
            rows.append(dict(scenario=s.name, department_id=b.department_id, department_name=b.department_name,
                             month=m, headcount=hc, exits=exits, hires=hires, workforce_cost=cost,
                             turnover_cost=exits * inputs["cost_per_exit"]))
    df = pd.DataFrame(rows)
    plan = inputs["plan"]
    df = df.merge(plan, on=["department_id", "month"], how="left")
    return df


def summarise(df: pd.DataFrame) -> dict:
    last = df[df.month == df.month.max()]
    return dict(scenario=df.scenario.iloc[0], end_headcount=float(last.headcount.sum()),
                planned_headcount_end=float(last.planned_headcount.sum()) if last.planned_headcount.notna().any()
                else None,
                total_hires=float(df.hires.sum()), total_exits=float(df.exits.sum()),
                workforce_cost=float(df.workforce_cost.sum()), turnover_cost=float(df.turnover_cost.sum()))


def compare(scenarios: list[Scenario], inputs: dict) -> pd.DataFrame:
    rows = [summarise(run(s, inputs)) for s in scenarios]
    out = pd.DataFrame(rows)
    base = out.iloc[0]
    for col in ["end_headcount", "total_hires", "total_exits", "workforce_cost", "turnover_cost"]:
        out[f"{col}_vs_baseline"] = out[col] - base[col]
    out["gap_vs_plan"] = out.end_headcount - out.planned_headcount_end
    return out


def with_horizon(scenarios: list[Scenario], months: int) -> list[Scenario]:
    return [replace(s, horizon_months=months) for s in scenarios]
