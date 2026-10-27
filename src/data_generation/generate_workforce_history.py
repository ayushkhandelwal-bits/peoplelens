"""Point-in-time workforce history: monthly snapshots, reporting relationships
and the annual operating plan (workforce budget).

Snapshot convention
-------------------
One row per employee per calendar month in which the employee was employed for
at least one day. ``snapshot_date`` is the last day of the month.
``active_flag = 1`` when the employee is still employed at month end
(``joining_date <= snapshot_date < exit_date``). Leavers appear in their exit
month with ``active_flag = 0`` and the attributes they held on their last
working day. Consequently, for every month::

    active(month) = active(previous month) + joiners(month) - exits(month)
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import date

import numpy as np

from src.data_generation.state import WorkforceState, add_months, month_end, ordinal


class JobTimeline:
    """Fast point-in-time lookup of each employee's job-history attributes."""

    def __init__(self, state: WorkforceState):
        self.rows: dict[int, list[dict]] = defaultdict(list)
        for r in state.jh_rows:
            self.rows[r["employee_idx"]].append(r)
        self.starts = {}
        for i, rows in self.rows.items():
            rows.sort(key=lambda r: r["start"])
            self.starts[i] = [r["start"] for r in rows]

    def at(self, i: int, day: int) -> dict | None:
        starts = self.starts.get(i)
        if not starts:
            return None
        k = bisect.bisect_right(starts, day) - 1
        if k < 0:
            return None
        row = self.rows[i][k]
        if row["end"] is not None and row["end"] < day:
            return None
        return row


def months_between(d0: int, d1: int) -> int:
    a, b = date.fromordinal(d0), date.fromordinal(d1)
    m = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        m -= 1
    return max(m, 0)


def build_snapshots(state: WorkforceState, timeline: JobTimeline) -> list[dict]:
    cfg, a = state.cfg, state.arrays
    start, end = cfg.start_date, cfg.end_date
    month_ends = []
    m = start
    while m <= end:
        month_ends.append((ordinal(m), ordinal(month_end(m))))
        m = add_months(m, 1)
    out = []
    for i in range(state.n):
        j, x = int(a["join"][i]), int(a["exit"][i])
        for ms, me in month_ends:
            if j > me or (x and x <= ms):
                continue
            active = (x == 0) or (x > me)
            ref = me if active else x - 1
            row = timeline.at(i, ref)
            if row is None:  # pragma: no cover - guarded by validation
                continue
            out.append(dict(snapshot_day=me, employee_idx=i, dept=row["dept"], loc=row["loc"],
                            family=row["family"], level=row["level"], manager=row["manager"],
                            active_flag=int(active), tenure_months=months_between(j, ref),
                            etype=row["etype"]))
    return out


def build_manager_relationships(timeline: JobTimeline) -> list[dict]:
    out = []
    for i, rows in timeline.rows.items():
        cur = None
        for r in rows:
            if r["manager"] < 0:
                cur = None
                continue
            if cur and cur["manager"] == r["manager"] and cur["dept"] == r["dept"] and cur["end"] == r["start"] - 1:
                cur["end"] = r["end"]
                continue
            cur = dict(employee_idx=i, manager=r["manager"], start=r["start"], end=r["end"], dept=r["dept"])
            out.append(cur)
    return out


def create_budget(state: WorkforceState, plan_start: date, plan_end: date) -> None:
    """Annual operating plan, prepared at ``plan_start`` from the opening position.

    Planned growth is drawn independently of the growth the simulation later
    realises (both centre on the department's strategic intent), so variances
    between plan and actual are meaningful rather than mechanical.
    """
    rng, a = state.rng, state.arrays
    fy = plan_end.year
    label = f"FY{fy % 100:02d}"
    months = []
    m = plan_start
    while m <= plan_end:
        months.append(m)
        m = add_months(m, 1)
    idx = state.active_indices()
    for dept in state.departments:
        members = [i for i in idx if a["dept"][i] == dept.idx]
        opening = len(members)
        if opening == 0:
            continue
        annual_cost = 0.0
        for i in members:
            row = state.comp_open.get(int(i))
            if row:
                annual_cost += row["base"] + row["variable"] + row["benefits"]
        avg_monthly = annual_cost / opening / 12
        growth = dept.plan_growth if dept.business_unit != "Executive" else 0.0
        exit_rate = float(np.clip((state.cfg.annual_voluntary_attrition + state.cfg.annual_involuntary_attrition)
                                  * (dept.attrition_factor or 0.5) * 0.92 + rng.normal(0, 0.012), 0.04, 0.45))
        horizon = len(months) if plan_start.month == 4 else 12 - ((plan_start.month - 4) % 12)
        target = opening * (1 + growth * horizon / 12)
        uplift = 1.07 if plan_start.month == 4 else 1.0
        prev = opening
        for k, mth in enumerate(months, start=1):
            planned = int(round(opening + (target - opening) * k / horizon))
            planned_exits = prev * exit_rate / 12
            hires = max(0, int(round(planned - prev + planned_exits)))
            # Planned cost assumes the approved 7% merit budget from April.
            cost = planned * avg_monthly * uplift
            state.budget_rows.append(dict(planning_period=month_end(mth), fiscal_year=label, dept=dept.idx,
                                          planned_headcount=planned, planned_hires=hires,
                                          planned_exit_rate=round(exit_rate, 3),
                                          planned_people_cost=round(cost, -3),
                                          approved_budget_version="v1.0", scenario_name="AOP"))
            prev = planned
