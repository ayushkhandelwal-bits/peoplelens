"""Mutable simulation state shared by the generation modules.

Employees are stored column-wise in NumPy arrays (index ``i`` -> employee id
``EMP{i+1:05d}``). Dates are held as proleptic ordinals (``date.toordinal()``)
inside the simulation and converted back to ``datetime.date`` on export.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from config.settings import GenerationConfig


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def month_end(d: date) -> date:
    return add_months(month_start(d), 1) - timedelta(days=1)


def ordinal(d: date) -> int:
    return d.toordinal()


def from_ordinal(o: int | None) -> date | None:
    if o is None or o <= 0:
        return None
    return date.fromordinal(int(o))


@dataclass
class Department:
    idx: int
    department_id: str
    name: str
    business_unit: str
    family: str
    loc_names: list[str]
    loc_weights: np.ndarray
    size_weight: float
    attrition_factor: float
    growth_intent: float
    wlb: float
    climate: float = 0.0
    creation: date = date(2012, 4, 1)
    actual_growth: float = 0.0
    plan_growth: float = 0.0
    initial_size: int = 0


@dataclass
class Requisition:
    req_id: str
    dept: int
    loc: int
    family: str
    level: int
    req_type: str
    opened: int
    hiring_manager: int | None
    recruiter: int | None
    status: str = "Open"
    accepted_day: int | None = None
    closed_day: int | None = None
    hired_employee: int | None = None
    cost: float = 0.0
    winner: dict | None = None  # candidate record that will join
    join_day: int | None = None


@dataclass
class WorkforceState:
    cfg: GenerationConfig
    rng: np.random.Generator
    capacity: int = 0
    n: int = 0
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)
    departments: list[Department] = field(default_factory=list)
    loc_ids: list[str] = field(default_factory=list)
    loc_names: list[str] = field(default_factory=list)
    dept_members: dict[int, set] = field(default_factory=lambda: defaultdict(set))
    reports_count: np.ndarray | None = None
    # records
    jh_rows: list[dict] = field(default_factory=list)
    jh_open: dict[int, dict] = field(default_factory=dict)
    comp_rows: list[dict] = field(default_factory=list)
    comp_open: dict[int, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    surveys: list[dict] = field(default_factory=list)
    feedback: list[dict] = field(default_factory=list)
    feedback_labels: list[dict] = field(default_factory=list)
    training: list[dict] = field(default_factory=list)
    training_done: dict[int, list] = field(default_factory=lambda: defaultdict(list))
    budget_rows: list[dict] = field(default_factory=list)
    requisitions: list[Requisition] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    rec_events: list[dict] = field(default_factory=list)
    scheduled_joins: list[tuple] = field(default_factory=list)
    special: dict = field(default_factory=dict)

    FIELDS = {
        "dept": (np.int32, -1), "loc": (np.int32, -1), "level": (np.int32, 0),
        "family": (np.int32, -1), "etype": (np.int32, 0), "gender": (np.int32, 0),
        "join": (np.int64, 0), "exit": (np.int64, 0), "active": (np.bool_, False),
        "manager": (np.int32, -1), "ability": (np.float64, 0.0), "eng_trait": (np.float64, 0.0),
        "eng_state": (np.float64, 0.0), "mgr_quality": (np.float64, 0.0), "base": (np.float64, 0.0),
        "last_promo": (np.int64, 0), "last_mgr_change": (np.int64, 0), "last_transfer": (np.int64, 0),
        "source": (np.int32, -1), "rating": (np.int32, 0), "exp_years": (np.float64, 0.0),
        "last_event": (np.int64, 0), "shock": (np.float64, 0.0), "hazard_mult": (np.float64, 1.0),
        "hazard_until": (np.int64, 0), "contract_renewals": (np.int32, 0),
        "exit_type": (np.int32, 0),  # 0 none, 1 voluntary, 2 involuntary
    }

    def init_arrays(self, capacity: int) -> None:
        self.capacity = capacity
        for name, (dtype, default) in self.FIELDS.items():
            self.arrays[name] = np.full(capacity, default, dtype=dtype)
        self.reports_count = np.zeros(capacity, dtype=np.int32)

    def __getattr__(self, item):  # convenient access: state.level[i]
        arrays = self.__dict__.get("arrays")
        if arrays is not None and item in arrays:
            return arrays[item]
        raise AttributeError(item)

    # ------------------------------------------------------------------ ids
    @staticmethod
    def emp_id(i: int | None) -> str | None:
        if i is None or i < 0:
            return None
        return f"EMP{int(i) + 1:05d}"

    def new_index(self) -> int:
        if self.n >= self.capacity:
            raise RuntimeError("Simulation capacity exceeded; increase capacity")
        i = self.n
        self.n += 1
        return i

    # ---------------------------------------------------------- job history
    def job_day(self, i: int, day: int) -> int:
        cur = self.jh_open.get(i)
        return max(day, cur["start"]) if cur else day

    def record_job(self, i: int, day: int, reason: str, promotion: bool = False,
                   transfer: bool = False) -> None:
        a = self.arrays
        row = {
            "employee_idx": i, "start": day, "end": None, "dept": int(a["dept"][i]),
            "loc": int(a["loc"][i]), "family": int(a["family"][i]), "level": int(a["level"][i]),
            "manager": int(a["manager"][i]), "etype": int(a["etype"][i]), "reason": reason,
            "promotion": int(promotion), "transfer": int(transfer),
        }
        cur = self.jh_open.get(i)
        if cur is not None:
            if day <= cur["start"]:
                reasons = cur["reason"].split(" + ")
                if reason not in reasons:
                    reasons.append(reason)
                row["start"] = cur["start"]
                row["reason"] = " + ".join(reasons)
                row["promotion"] = max(row["promotion"], cur["promotion"])
                row["transfer"] = max(row["transfer"], cur["transfer"])
                cur.update(row)
                return
            cur["end"] = day - 1
        self.jh_rows.append(row)
        self.jh_open[i] = row

    def close_job(self, i: int, exit_day: int) -> None:
        cur = self.jh_open.pop(i, None)
        if cur is not None:
            cur["end"] = exit_day - 1
        comp = self.comp_open.pop(i, None)
        if comp is not None:
            comp["end"] = exit_day - 1

    # ------------------------------------------------------- compensation
    def record_comp(self, i: int, day: int, base: float, variable: float, benefits: float,
                    reason: str) -> None:
        row = {"employee_idx": i, "start": day, "end": None, "base": round(base, -2),
               "variable": round(variable, -2), "benefits": round(benefits, -2), "reason": reason}
        cur = self.comp_open.get(i)
        if cur is not None:
            if day <= cur["start"]:
                reasons = cur["reason"].split(" + ")
                if reason not in reasons:
                    reasons.append(reason)
                row["start"] = cur["start"]
                row["reason"] = " + ".join(reasons)
                cur.update(row)
                return
            cur["end"] = day - 1
        self.comp_rows.append(row)
        self.comp_open[i] = row

    # -------------------------------------------------------------- events
    def add_event(self, i: int, event_type: str, day: int, **kw) -> None:
        row = {"employee_idx": i, "event_type": event_type, "day": day,
               "prev_dept": None, "new_dept": None, "prev_level": None, "new_level": None,
               "prev_manager": None, "new_manager": None, "exit_type": None, "exit_reason": None}
        row.update(kw)
        prev = self.special.setdefault("_last_event_row", {}).get(i)
        if prev is not None and prev["event_type"] == event_type and prev["day"] == day:
            # same-day repeat (e.g. two reassignments): keep one event, latest state wins
            for k in ("new_dept", "new_level", "new_manager"):
                prev[k] = row[k]
            return
        self.special["_last_event_row"][i] = row
        self.events.append(row)
        if day > self.arrays["last_event"][i]:
            self.arrays["last_event"][i] = day

    # ------------------------------------------------------------ helpers
    def set_manager(self, i: int, new_mgr: int) -> None:
        old = int(self.arrays["manager"][i])
        if old >= 0:
            self.reports_count[old] -= 1
        if new_mgr >= 0:
            self.reports_count[new_mgr] += 1
        self.arrays["manager"][i] = new_mgr

    def active_indices(self) -> np.ndarray:
        return np.flatnonzero(self.arrays["active"][: self.n])
