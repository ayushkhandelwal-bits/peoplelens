"""Attrition driver simulator.

Applies changes to the *inputs* of the trained attrition model (engagement,
manager support, career growth, pay position, promotions, training, manager
stability) for the current workforce, re-scores everyone with the same model
and reports the change in expected voluntary leavers. The implied change in
annual attrition can then be fed into the headcount scenario engine.

Important: the model describes associations in historical data, not proven
causes. Results indicate direction and rough size for planning, not a promise
that an intervention will deliver exactly this effect. Training hours are a
model input but show no protective association in this data, so they are not
offered as a lever.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from src.models.attrition import predict

PROMOTION_WAIT_MONTHS = 36
MAX_LEVEL = 7


@dataclass(frozen=True)
class Levers:
    engagement_delta: float = 0.0  # points on the 1-5 survey scale, for employees with a survey response
    manager_support_delta: float = 0.0  # points on the 1-5 scale
    career_growth_delta: float = 0.0  # points on the 1-5 scale
    pay_floor: float | None = None  # raise anyone paid below this share of their peer median up to it (e.g. 0.95)
    promote_share: float = 0.0  # share of employees waiting 36+ months (below the top level) who are promoted
    stable_managers: bool = False  # no manager changes in the next six months

    def to_dict(self) -> dict:
        return asdict(self)

    def is_baseline(self) -> bool:
        return self == Levers()


LEVER_LABELS = {
    "engagement_delta": "Engagement",
    "manager_support_delta": "Manager support",
    "career_growth_delta": "Career growth",
    "pay_floor": "Close pay gaps",
    "promote_share": "Promotions",
    "stable_managers": "Manager stability",
}


def apply_levers(df: pd.DataFrame, lv: Levers) -> tuple[pd.DataFrame, dict]:
    """Return the modified feature frame and side information (who was promoted / given a pay adjustment)."""
    out = df.copy()
    info = {"promoted": 0, "pay_adjusted": 0}
    for col, delta in [("engagement_score", lv.engagement_delta), ("manager_support_score", lv.manager_support_delta),
                       ("career_growth_score", lv.career_growth_delta)]:
        if delta:
            has = out[col].notna()
            out.loc[has, col] = (out.loc[has, col] + delta).clip(1.0, 5.0)
    if lv.pay_floor:
        below = out.pay_vs_peer_median.notna() & (out.pay_vs_peer_median < lv.pay_floor)
        info["pay_adjusted"] = int(below.sum())
        info["pay_uplift_ratio"] = (lv.pay_floor / out.loc[below, "pay_vs_peer_median"] - 1).rename("uplift")
        out.loc[below, "pay_vs_peer_median"] = lv.pay_floor
    if lv.promote_share > 0:
        elig = out[(out.months_since_promotion >= PROMOTION_WAIT_MONTHS) & (out.level_num < MAX_LEVEL)]
        n = int(round(len(elig) * lv.promote_share))
        if n:
            # merit order: best latest rating first, then longest wait (deterministic)
            pick = elig.sort_values(["last_rating", "months_since_promotion"], ascending=[False, False],
                                    na_position="last").index[:n]
            out.loc[pick, "level_num"] = out.loc[pick, "level_num"] + 1
            out.loc[pick, "months_since_promotion"] = 0
            info["promoted"] = n
            info["eligible_for_promotion"] = int(len(elig))
    if lv.stable_managers:
        info["manager_changes_avoided"] = int((out.manager_change_6m == 1).sum())
        out["manager_change_6m"] = 0
    return out, info


def _annual(p6: np.ndarray) -> np.ndarray:
    """Six-month leaving probability to an annual probability, assuming a constant hazard."""
    return 1 - (1 - np.clip(p6, 0, 1)) ** 2


def simulate(scores: pd.DataFrame, lv: Levers, department_id: str | None = None,
             salaries: pd.Series | None = None) -> dict:
    """Re-score the workforce with the levers applied (optionally to one department only)."""
    pop = scores.reset_index(drop=True)
    base_p = predict(pop)
    target = pop.department_id == department_id if department_id else pd.Series(True, index=pop.index)
    mod, info = apply_levers(pop[target], lv)
    new_p = base_p.copy()
    if len(mod):
        new_p[target.to_numpy()] = predict(mod)
    n = len(pop)
    t = target.to_numpy()
    res = dict(
        employees=n, employees_in_scope=int(t.sum()),
        base_leavers_6m=float(base_p.sum()), new_leavers_6m=float(new_p.sum()),
        base_leavers_6m_scope=float(base_p[t].sum()), new_leavers_6m_scope=float(new_p[t].sum()),
        base_annual_rate=float(_annual(base_p).mean()), new_annual_rate=float(_annual(new_p).mean()),
        base_annual_rate_scope=float(_annual(base_p[t]).mean()) if t.any() else None,
        new_annual_rate_scope=float(_annual(new_p[t]).mean()) if t.any() else None,
        promoted=info.get("promoted", 0), eligible_for_promotion=info.get("eligible_for_promotion", 0),
        pay_adjusted=info.get("pay_adjusted", 0), manager_changes_avoided=info.get("manager_changes_avoided", 0),
    )
    res["leavers_change_6m"] = res["new_leavers_6m"] - res["base_leavers_6m"]
    # organisation-wide change in annual attrition, in percentage points (feeds the scenario engine)
    res["attrition_delta_pp"] = (res["new_annual_rate"] - res["base_annual_rate"]) * 100
    res["pay_cost_annual"] = None
    if lv.pay_floor and salaries is not None and "pay_uplift_ratio" in info:
        up = info["pay_uplift_ratio"]
        sal = pop.loc[t].loc[up.index, "employee_id"].map(salaries)
        res["pay_cost_annual"] = float((sal * up).sum())
    return res


def lever_contributions(scores: pd.DataFrame, lv: Levers, department_id: str | None = None) -> pd.DataFrame:
    """Change in expected six-month leavers from each lever on its own (they do not add up exactly)."""
    rows = []
    base = Levers()
    for field, label in LEVER_LABELS.items():
        val = getattr(lv, field)
        if val == getattr(base, field):
            continue
        single = Levers(**{field: val})
        r = simulate(scores, single, department_id)
        rows.append(dict(lever=label, leavers_change_6m=r["leavers_change_6m"]))
    return pd.DataFrame(rows, columns=["lever", "leavers_change_6m"])
