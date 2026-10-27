"""Organisation set-up, the opening workforce, new-hire creation and reporting lines.

Reporting-line rule: a manager is always *strictly more senior* (higher job
level) than every direct report. Because level strictly increases along every
reporting chain, the hierarchy can never contain a cycle, and nobody can report
to themselves. Managers are chosen within the department (nearest senior level,
load-balanced by current span of control); department heads (L7) report to their
business-unit head (L8), who reports to the chief executive (L9).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from src.data_generation.generate_compensation import initial_salary, set_salary
from src.data_generation.reference_data import (
    BUSINESS_UNITS,
    DEPARTMENTS,
    EXECUTIVE_OFFICE,
    FIRST_NAMES_F,
    FIRST_NAMES_M,
    FOUNDED_YEAR,
    FRONTLINE_DEPTS,
    JOB_FAMILIES,
    LAST_NAMES,
    LEVEL_MIX_DEFAULT,
    LEVEL_MIX_FRONTLINE,
    LEVEL_MIX_TECH,
    LOCATIONS,
    SECONDARY_FAMILIES,
)
from src.data_generation.state import Department, WorkforceState, add_months, ordinal

GENDERS = ["Female", "Male", "Undisclosed"]
EMPLOYMENT_TYPES = ["Full-time", "Contract"]
NEW_DEPARTMENT_YEARS = {"Data Science & Analytics": 2018, "Information Security": 2019,
                        "Growth & Partnerships": 2020, "Design & UX": 2017}


def build_organisation(state: WorkforceState) -> None:
    cfg, rng = state.cfg, state.rng
    state.loc_ids = [loc[0] for loc in LOCATIONS]
    state.loc_names = [loc[1] for loc in LOCATIONS]
    allowed = [loc for loc in cfg.locations if loc in state.loc_names] or state.loc_names
    specs = list(DEPARTMENTS)
    n = max(5, min(cfg.n_departments, len(specs)))
    chosen = specs[:n]
    if not any(s[0] == "Human Resources" for s in chosen):  # recruiters live in HR
        chosen[-1] = next(s for s in specs if s[0] == "Human Resources")
    for k, spec in enumerate(chosen + [(*EXECUTIVE_OFFICE, 0.0, 0.0, 0.0, 0.0)]):
        name, bu, fam, locs, size_w, att, growth, wlb = spec
        loc_pairs = [(c, w) for c, w in locs.items() if c in allowed] or [(allowed[0], 1.0)]
        weights = np.array([w for _, w in loc_pairs], dtype=float)
        year = NEW_DEPARTMENT_YEARS.get(name, FOUNDED_YEAR)
        dept = Department(
            idx=k, department_id=f"D{k + 1:02d}", name=name, business_unit=bu, family=fam,
            loc_names=[c for c, _ in loc_pairs], loc_weights=weights / weights.sum(),
            size_weight=size_w, attrition_factor=att * float(rng.lognormal(0, 0.08)) if att else 0.0,
            growth_intent=growth, wlb=wlb, climate=float(rng.normal(0, 0.12)),
            creation=date(year, 4, 1),
        )
        # Realised growth and planned (AOP) growth are drawn independently around
        # the same strategic intent, so plan-vs-actual variance is meaningful.
        dept.actual_growth = cfg.annual_growth + (growth - 0.065) + float(rng.normal(0, 0.03))
        dept.plan_growth = cfg.plan_growth + (growth - 0.065) + float(rng.normal(0, 0.025))
        state.departments.append(dept)


def random_name(state: WorkforceState, gender: int) -> str:
    rng = state.rng
    pool = FIRST_NAMES_F if gender == 0 else FIRST_NAMES_M if gender == 1 else FIRST_NAMES_F + FIRST_NAMES_M
    return f"{pool[rng.integers(len(pool))]} {LAST_NAMES[rng.integers(len(LAST_NAMES))]}"


def _family_for(state: WorkforceState, dept: Department) -> int:
    if dept.business_unit == "Executive":
        return JOB_FAMILIES.index("Executive")
    if state.rng.random() < 0.85:
        return JOB_FAMILIES.index(dept.family)
    options = [f for f in SECONDARY_FAMILIES[dept.business_unit] if f != dept.family]
    return JOB_FAMILIES.index(options[state.rng.integers(len(options))])


def level_mix(dept: Department) -> dict[int, float]:
    if dept.name in FRONTLINE_DEPTS:
        return LEVEL_MIX_FRONTLINE
    if dept.business_unit == "Technology":
        return LEVEL_MIX_TECH
    return LEVEL_MIX_DEFAULT


def sample_level(state: WorkforceState, dept: Department) -> int:
    mix = level_mix(dept)
    levels = np.array(list(mix))
    p = np.array(list(mix.values()))
    return int(state.rng.choice(levels, p=p / p.sum()))


def experience_for_level(state: WorkforceState, level: int) -> float:
    lo, hi = {1: (0, 2), 2: (1, 4), 3: (3, 7), 4: (5, 10), 5: (7, 13), 6: (10, 17), 7: (13, 22),
              8: (16, 25), 9: (20, 30)}[level]
    return round(float(state.rng.uniform(lo, hi)), 1)


def create_employee(state: WorkforceState, dept_idx: int, level: int, family: int, loc: int,
                    join_day: int, source: int = -1, etype: int | None = None) -> int:
    rng, a = state.rng, state.arrays
    i = state.new_index()
    gender = int(rng.choice(3, p=[0.38, 0.60, 0.02]))
    a["dept"][i], a["level"][i], a["family"][i], a["loc"][i] = dept_idx, level, family, loc
    a["gender"][i] = gender
    a["etype"][i] = etype if etype is not None else int(rng.random() < (0.10 if level <= 3 else 0.03))
    a["join"][i] = join_day
    a["active"][i] = True
    a["ability"][i] = rng.normal(0, 1)
    a["eng_trait"][i] = rng.normal(0, 1)
    a["eng_state"][i] = rng.normal(0, 0.6)
    a["mgr_quality"][i] = rng.normal(0, 1)
    a["source"][i] = source
    a["last_promo"][i] = join_day
    a["last_mgr_change"][i] = 0
    a["exp_years"][i] = experience_for_level(state, level)
    state.names.append(random_name(state, gender))
    state.dept_members[dept_idx].add(i)
    return i


# ------------------------------------------------------------ reporting lines

def _bu_head(state: WorkforceState, bu: str) -> int:
    return state.special.get("bu_heads", {}).get(bu, state.special.get("ceo", -1))


def assign_manager(state: WorkforceState, i: int, day: int, exclude: set | None = None) -> int:
    """Choose a manager for employee ``i`` effective ``day`` (does not record history)."""
    a = state.arrays
    lv = int(a["level"][i])
    dept = state.departments[int(a["dept"][i])]
    if lv >= 9:
        return -1
    if lv == 8:
        return state.special["ceo"]
    if lv == 7:
        return _bu_head(state, dept.business_unit)
    exclude = exclude or set()
    members = [j for j in state.dept_members[dept.idx]
               if j != i and j not in exclude and a["level"][j] > lv and a["level"][j] >= 4
               and (a["exit"][j] == 0 or a["exit"][j] > day)]
    if not members:
        return _bu_head(state, dept.business_unit)
    levels = a["level"][members]
    preferred = (4, 5) if lv <= 3 else (lv + 1, lv + 2)
    pool = [m for m, lvl in zip(members, levels) if lvl in preferred]
    if not pool:
        min_lvl = levels.min()
        pool = [m for m, lvl in zip(members, levels) if lvl == min_lvl]
    load = state.reports_count[pool].astype(float)
    w = 1.0 / (1.0 + load) ** 2
    return int(pool[state.rng.choice(len(pool), p=w / w.sum())])


# --------------------------------------------------------- opening workforce

def create_initial_workforce(state: WorkforceState) -> None:
    cfg, rng, a = state.cfg, state.rng, state.arrays
    start = cfg.start_date
    start_o = ordinal(start)
    ops = [d for d in state.departments if d.business_unit != "Executive"]
    weights = np.array([d.size_weight for d in ops])
    exec_size = 6
    sizes = rng.multinomial(cfg.initial_headcount - exec_size - len(ops), weights / weights.sum()) + 1

    plan = []  # (dept, level, family, loc, tenure_years)
    exec_dept = next(d for d in state.departments if d.business_unit == "Executive")
    plan.append((exec_dept, 9, JOB_FAMILIES.index("Executive"), 0))
    for _bu in BUSINESS_UNITS:
        plan.append((exec_dept, 8, JOB_FAMILIES.index("Executive"), 0))
    plan.append((exec_dept, 6, JOB_FAMILIES.index("Executive"), 0))  # chief of staff
    for dept, size in zip(ops, sizes):
        loc_for = lambda d=dept: state.loc_names.index(d.loc_names[rng.choice(len(d.loc_names), p=d.loc_weights)])  # noqa: E731
        plan.append((dept, 7, JOB_FAMILIES.index(dept.family), state.loc_names.index(dept.loc_names[0])))
        if size > 160:  # large departments carry a second L7
            plan.append((dept, 7, JOB_FAMILIES.index(dept.family), loc_for()))
        for _ in range(int(size) - 1):
            plan.append((dept, sample_level(state, dept), _family_for(state, dept), loc_for()))
        dept.initial_size = int(size)

    # tenure: longer for senior levels, bounded by company/department founding
    records = []
    for dept, level, fam, loc in plan:
        mean_years = {1: 1.6, 2: 2.6, 3: 3.4, 4: 4.2, 5: 4.8, 6: 5.5, 7: 6.0, 8: 7.0, 9: 9.0}[level]
        tenure = float(rng.gamma(1.6, mean_years / 1.6))
        max_tenure = (start - max(dept.creation, date(FOUNDED_YEAR, 4, 1))).days / 365.25
        tenure = min(tenure, max_tenure - 0.05)
        tenure = max(tenure, 0.02)
        join = start - timedelta(days=int(tenure * 365.25))
        records.append((join, dept, level, fam, loc, tenure))
    records.sort(key=lambda r: r[0])  # chronological employee IDs

    bu_heads: dict[str, int] = {}
    for join, dept, level, fam, loc, tenure in records:
        etype = 0 if level >= 4 else int(rng.random() < 0.10)
        i = create_employee(state, dept.idx, level, fam, loc, ordinal(join), -1, etype)
        a["exp_years"][i] = experience_for_level(state, level)
        if level == 9:
            state.special["ceo"] = i
        elif level == 8:
            bu = [b for b in BUSINESS_UNITS if b not in bu_heads][0]
            bu_heads[bu] = i
        # Time in current level: a pre-window promotion for those not hired into it.
        if level > 1 and tenure > 1.2 and rng.random() < 0.7:
            months_in_level = int(rng.uniform(3, min(tenure * 12 - 6, 48)))
            a["last_promo"][i] = ordinal(add_months(start, -months_in_level)) + int(rng.integers(0, 27))
        else:
            a["last_promo"][i] = ordinal(join)
        # contract employees renewal clock
        a["ability"][i] += 0.15 * min(tenure, 5) / 5  # survivors of past attrition skew slightly stronger
    state.special["bu_heads"] = bu_heads

    # Reporting lines, most senior first so managers exist before reports.
    order = sorted(range(state.n), key=lambda j: -int(a["level"][j]))
    for i in order:
        mgr = assign_manager(state, i, start_o)
        state.set_manager(i, mgr)

    # Pre-window job history, opening salary. Reporting lines are only captured
    # from the HRMS go-live date (= start of the window): earlier job-history rows
    # carry a NULL manager, and an "HRMS Migration" row starts on go-live.
    last_april = date(start.year if start.month >= 4 else start.year - 1, 4, 1)
    for i in range(state.n):
        join_o = int(a["join"][i])
        promo_o = int(a["last_promo"][i])
        lvl = int(a["level"][i])
        mgr_now = int(a["manager"][i])
        a["manager"][i] = -1
        if promo_o > join_o + 30 and lvl > 1:
            a["level"][i] = lvl - 1
            state.record_job(i, join_o, "Hire")
            a["level"][i] = lvl
            state.record_job(i, promo_o, "Promotion", promotion=True)
            state.add_event(i, "Hire", join_o, new_dept=int(a["dept"][i]), new_level=lvl - 1)
            state.add_event(i, "Promotion", promo_o, prev_level=lvl - 1, new_level=lvl,
                            prev_dept=int(a["dept"][i]), new_dept=int(a["dept"][i]))
        else:
            state.record_job(i, join_o, "Hire")
            state.add_event(i, "Hire", join_o, new_dept=int(a["dept"][i]), new_level=lvl)
        a["manager"][i] = mgr_now
        if mgr_now >= 0:
            state.record_job(i, start_o, "HRMS Migration")
        tenure_years = (start_o - join_o) / 365.25
        eff = max(join_o, ordinal(last_april), promo_o if promo_o > join_o + 30 else 0)
        reason = "New Hire" if eff == join_o else "Promotion" if eff == promo_o else "Annual Increment"
        base = initial_salary(state, lvl, int(a["family"][i]), int(a["loc"][i]), eff, tenure_years)
        set_salary(state, i, base, eff, reason)
        a["last_event"][i] = 0
