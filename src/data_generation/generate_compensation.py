"""Compensation simulation: salary bands, offers, merit increments, promotions.

Assumptions (all fictional):
* Band midpoint = level base x job-family factor x location factor, inflated
  annually (faster for technology families) from the start of the window.
* New hires are paid close to the current market midpoint; tenured staff drift
  below it unless they receive strong merit increases or promotions. This
  creates *compression*, which in turn feeds a modest attrition effect.
* Annual merit increases are effective 1 April and depend on the rating.
"""

from __future__ import annotations

import numpy as np

from src.data_generation.reference_data import (
    BAND_INFLATION,
    BAND_INFLATION_DEFAULT,
    BASE_SALARY_MID,
    BENEFITS_FIXED,
    BENEFITS_PCT,
    FAMILY_PAY_FACTOR,
    JOB_FAMILIES,
    LOCATION_PAY_FACTOR,
    MERIT_INCREASE,
    SALES_VARIABLE_UPLIFT,
    VARIABLE_PAY_PCT,
)
from src.data_generation.state import WorkforceState, ordinal


def band_mid(state: WorkforceState, level: int, family: int, loc: int, day: int) -> float:
    fam = JOB_FAMILIES[family]
    years = (day - ordinal(state.cfg.start_date)) / 365.25
    infl = BAND_INFLATION.get(fam, BAND_INFLATION_DEFAULT)
    return (BASE_SALARY_MID[level] * FAMILY_PAY_FACTOR[fam]
            * LOCATION_PAY_FACTOR[state.loc_names[loc]] * (1 + infl) ** years)


def comp_ratio(state: WorkforceState, idx: np.ndarray, day: int) -> np.ndarray:
    a = state.arrays
    mids = np.array([band_mid(state, int(a["level"][i]), int(a["family"][i]), int(a["loc"][i]), day)
                     for i in idx]) if len(idx) else np.array([])
    return a["base"][idx] / mids if len(idx) else mids


def variable_and_benefits(state: WorkforceState, i: int, base: float) -> tuple[float, float]:
    level = int(state.level[i])
    pct = VARIABLE_PAY_PCT[level]
    if JOB_FAMILIES[int(state.family[i])] == "Sales":
        pct += SALES_VARIABLE_UPLIFT
    return base * pct, base * BENEFITS_PCT + BENEFITS_FIXED


def set_salary(state: WorkforceState, i: int, base: float, day: int, reason: str) -> None:
    state.base[i] = base
    var, ben = variable_and_benefits(state, i, base)
    state.record_comp(i, day, base, var, ben, reason)


def offer_salary(state: WorkforceState, level: int, family: int, loc: int, day: int) -> float:
    ratio = float(np.clip(state.rng.normal(1.03, 0.07), 0.85, 1.25))
    return band_mid(state, level, family, loc, day) * ratio


def initial_salary(state: WorkforceState, level: int, family: int, loc: int, day: int,
                   tenure_years: float) -> float:
    mean = 1.0 - 0.014 * min(tenure_years, 10)
    ratio = float(np.clip(state.rng.normal(mean, 0.09), 0.70, 1.35))
    return band_mid(state, level, family, loc, day) * ratio


def merit_increase(state: WorkforceState, i: int, day: int) -> None:
    rating = int(state.rating[i])
    pct = MERIT_INCREASE.get(rating, 0.045) if rating else 0.045
    pct = max(0.0, pct + state.rng.normal(0, 0.012))
    if pct <= 0.001:
        return
    set_salary(state, i, state.base[i] * (1 + pct), day, "Annual Increment")


def promotion_increase(state: WorkforceState, i: int, day: int) -> None:
    """Call after ``state.level[i]`` has been raised."""
    hike = state.rng.uniform(0.10, 0.18)
    floor = 0.88 * band_mid(state, int(state.level[i]), int(state.family[i]), int(state.loc[i]), day)
    set_salary(state, i, max(state.base[i] * (1 + hike), floor), day, "Promotion")


def market_adjustments(state: WorkforceState, idx: np.ndarray, day: int) -> int:
    """October market correction for strongly under-paid, adequately-rated staff."""
    if len(idx) == 0:
        return 0
    cr = comp_ratio(state, idx, day)
    n = 0
    for i, r in zip(idx, cr):
        if r < 0.82 and state.rating[i] >= 3 and state.rng.random() < 0.25:
            target = state.base[i] / r * 0.92
            set_salary(state, int(i), target, day, "Market Adjustment")
            n += 1
    return n
