"""Employee lifecycle dynamics: engagement, exits, promotions, transfers,
manager changes, performance reviews and training.

Simulation assumptions (documented in docs/data_dictionary.md):

* Voluntary exit hazard (monthly) = k x department factor x tenure curve x
  exp(-0.60 x latent engagement) x pay-compression effect x promotion recency x
  recent-manager-change x level x contract x source-of-hire (first year) x
  calendar seasonality x temporary event shocks. ``k`` is calibrated once at
  the start of the window so the organisation-wide rate matches the configured
  target; everything afterwards evolves freely.
* Location, gender and training participation have **no** direct effect on
  attrition (deliberate null relationships).
* Involuntary exits depend mainly on the latest performance rating and contract
  renewals.
* Latent engagement is never observed directly; survey scores are noisy proxies.
"""

from __future__ import annotations

import math

import numpy as np

from src.data_generation.generate_compensation import (
    comp_ratio,
    merit_increase,
    promotion_increase,
)
from src.data_generation.generate_employees import assign_manager
from src.data_generation.generate_recruitment import SOURCES
from src.data_generation.reference_data import (
    INVOLUNTARY_REASONS,
    JOB_FAMILIES,
    SEASONALITY,
    SOURCE_EARLY_ATTRITION,
    TRAINING_PROGRAMS,
    VOLUNTARY_REASONS,
)
from src.data_generation.state import WorkforceState, from_ordinal, ordinal

LEVEL_ATTRITION = {1: 1.15, 2: 1.05, 3: 1.0, 4: 0.9, 5: 0.8, 6: 0.7, 7: 0.6, 8: 0.0, 9: 0.0}


# ------------------------------------------------------------- engagement

def engagement_latent(state: WorkforceState, idx: np.ndarray, day: int,
                      cr: np.ndarray | None = None) -> np.ndarray:
    a = state.arrays
    if len(idx) == 0:
        return np.array([])
    if cr is None:
        cr = comp_ratio(state, idx, day)
    mgr = a["manager"][idx]
    mq = np.where(mgr >= 0, a["mgr_quality"][np.maximum(mgr, 0)], 0.0)
    climate = np.array([state.departments[d].climate for d in a["dept"][idx]])
    recent_promo = (day - a["last_promo"][idx]) < 365
    recent_mgr = (a["last_mgr_change"][idx] > 0) & ((day - a["last_mgr_change"][idx]) < 182)
    return (0.60 * a["eng_trait"][idx] + 0.55 * a["eng_state"][idx] + 0.35 * mq + climate
            + 0.25 * recent_promo - 0.35 * recent_mgr + 1.0 * np.clip(cr - 1, -0.3, 0.2)
            + a["shock"][idx])


def update_engagement_state(state: WorkforceState) -> None:
    idx = state.active_indices()
    a = state.arrays
    a["eng_state"][idx] = 0.78 * a["eng_state"][idx] + state.rng.normal(0, 0.45, len(idx))
    a["shock"][idx] *= 0.82  # event shocks decay


# ------------------------------------------------------------------ exits

def voluntary_multiplier(state: WorkforceState, idx: np.ndarray, day: int) -> np.ndarray:
    a = state.arrays
    tenure_m = (day - a["join"][idx]) / 30.44
    t_mult = np.select([tenure_m < 3, tenure_m < 12, tenure_m < 36, tenure_m < 84],
                       [2.1, 1.45, 1.0, 0.8], 0.65)
    cr = comp_ratio(state, idx, day)
    eng = engagement_latent(state, idx, day, cr)
    e_mult = np.exp(-0.60 * eng)
    c_mult = np.exp(-2.2 * np.clip(cr - 1, -0.35, 0.25))
    since_promo = day - a["last_promo"][idx]
    p_mult = np.where(since_promo < 365, 0.72, np.where(since_promo > 3 * 365, 1.2, 1.0))
    recent_mgr = (a["last_mgr_change"][idx] > 0) & ((day - a["last_mgr_change"][idx]) < 182)
    m_mult = np.where(recent_mgr, 1.2, 1.0)
    l_mult = np.array([LEVEL_ATTRITION[int(v)] for v in a["level"][idx]])
    k_mult = np.where(a["etype"][idx] == 1, 1.25, 1.0)
    src = a["source"][idx]
    s_mult = np.array([SOURCE_EARLY_ATTRITION[SOURCES[s]] if s >= 0 else 1.0 for s in src])
    s_mult = np.where(tenure_m < 12, s_mult, 1.0)
    d_mult = np.array([state.departments[d].attrition_factor for d in a["dept"][idx]])
    r = a["rating"][idx]
    r_mult = np.where(r == 5, 1.1, np.where((r > 0) & (r <= 2), 1.2, 1.0))
    ev_mult = np.where(a["hazard_until"][idx] >= day, a["hazard_mult"][idx], 1.0)
    return t_mult * e_mult * c_mult * p_mult * m_mult * l_mult * k_mult * s_mult * d_mult * r_mult * ev_mult


def calibrate(state: WorkforceState) -> None:
    idx = state.active_indices()
    day = ordinal(state.cfg.start_date)
    mult = voluntary_multiplier(state, idx, day)
    target_monthly = -math.log(1 - state.cfg.annual_voluntary_attrition) / 12
    state.special["k_vol"] = target_monthly / float(mult[mult > 0].mean())


def _choose_reason(state: WorkforceState, i: int, day: int, cr: float) -> str:
    a = state.arrays
    w = np.array([2.6, 1.0, 1.2, 0.7, 0.8, 0.6, 0.35, 0.9])
    if cr < 0.9:
        w[1] *= 3.0
    if a["last_mgr_change"][i] and day - a["last_mgr_change"][i] < 182:
        w[3] *= 3.0
    if day - a["last_promo"][i] > 3 * 365:
        w[2] *= 2.5
    if state.departments[int(a["dept"][i])].wlb < -0.15:
        w[4] *= 2.2
    if (day - a["join"][i]) < 180:
        w[0] *= 1.5
    return VOLUNTARY_REASONS[state.rng.choice(len(w), p=w / w.sum())]


def process_exits(state: WorkforceState, ms: int, me: int, month: int) -> list[int]:
    """Draw voluntary/involuntary exits for the month. Returns exiting indices."""
    rng, a = state.rng, state.arrays
    idx = state.active_indices()
    idx = idx[a["level"][idx] <= 7]
    if len(idx) == 0:
        return []
    mid = (ms + me) // 2
    mult = voluntary_multiplier(state, idx, mid) * SEASONALITY[month]
    # Exposure: joiners this month are only exposed for the remainder of it.
    exposure = np.clip((me - np.maximum(a["join"][idx], ms)) / (me - ms + 1), 0, 1)
    p_vol = 1 - np.exp(-state.special["k_vol"] * mult * exposure)
    inv_base = -math.log(1 - state.cfg.annual_involuntary_attrition) / 12
    r = a["rating"][idx]
    r_inv = np.select([r == 1, r == 2, r == 3, r >= 4], [6.0, 2.5, 0.75, 0.35], 0.8)
    p_inv = (1 - np.exp(-inv_base * r_inv * exposure)) * 0.75
    u = rng.random(len(idx))
    vol = u < p_vol
    inv = (~vol) & (u < p_vol + p_inv)

    # Contract renewals at each anniversary: ~30% are not renewed.
    contract = np.zeros(len(idx), dtype=bool)
    anniv_day = np.zeros(len(idx), dtype=np.int64)
    for k, i in enumerate(idx):
        if a["etype"][i] != 1 or vol[k] or inv[k]:
            continue
        j = from_ordinal(int(a["join"][i]))
        years = from_ordinal(me).year - j.year
        if years >= 1:
            try:
                anniv = ordinal(j.replace(year=j.year + years))
            except ValueError:
                anniv = ordinal(j.replace(year=j.year + years, day=28))
            if ms <= anniv <= me and rng.random() < 0.30:
                contract[k] = True
                anniv_day[k] = anniv

    exiting = []
    crs = comp_ratio(state, idx, mid)
    for k, i in enumerate(idx):
        if not (vol[k] or inv[k] or contract[k]):
            continue
        lo = max(ms + 1, int(a["join"][i]) + 1, int(a["last_event"][i]) + 1)
        if contract[k]:
            day = max(int(anniv_day[k]), lo)
        else:
            if lo > me:
                continue
            day = int(rng.integers(lo, me + 1))
        if day > me:
            continue
        i = int(i)
        a["exit"][i] = day
        a["active"][i] = False
        state.dept_members[int(a["dept"][i])].discard(i)
        if vol[k]:
            etype, reason = "Voluntary", _choose_reason(state, i, day, float(crs[k]))
            a["exit_type"][i] = 1
        else:
            a["exit_type"][i] = 2
            etype = "Involuntary"
            if contract[k]:
                reason = "End of Contract"
            else:
                w = np.array([0.55, 0.12, 0.33, 0.0])
                if a["rating"][i] in (1, 2):
                    w[0] = 0.8
                reason = INVOLUNTARY_REASONS[rng.choice(4, p=w / w.sum())]
        state.special.setdefault("pending_exits", {})[i] = (etype, reason)
        exiting.append(i)
    return exiting


# ------------------------------------------------------ reporting changes

def change_manager(state: WorkforceState, i: int, day: int, reason: str = "Manager Change",
                   exclude: set | None = None, record_event: bool = True) -> None:
    a = state.arrays
    if a["exit"][i] and a["exit"][i] <= day:
        return
    day = state.job_day(i, day)
    old = int(a["manager"][i])
    new = assign_manager(state, i, day, exclude)
    if new == old:
        return
    state.set_manager(i, new)
    a["last_mgr_change"][i] = day
    state.record_job(i, day, reason)
    if record_event:
        state.add_event(i, "Manager Change", day, prev_manager=old if old >= 0 else None,
                        new_manager=new if new >= 0 else None,
                        prev_dept=int(a["dept"][i]), new_dept=int(a["dept"][i]))


def reassign_reports(state: WorkforceState, mgr: int, day: int) -> None:
    a = state.arrays
    reports = np.flatnonzero((a["manager"][: state.n] == mgr) & (a["active"][: state.n]
                             | (a["exit"][: state.n] > day)))
    for r in reports:
        change_manager(state, int(r), day, exclude={mgr})


def fix_reporting_after_exits(state: WorkforceState, exiting: list[int]) -> None:
    """Reassign the reports of managers who left, then record the exits.

    Exit events and the closing of job history are deferred until reporting
    lines are fixed, because a leaver's own manager may leave earlier in the
    same month (the leaver is then reassigned for their final days)."""
    a = state.arrays
    for m in sorted(exiting, key=lambda j: state.exit[j]):
        if state.reports_count[m] > 0:
            reassign_reports(state, m, int(state.exit[m]))
    pending = state.special.get("pending_exits", {})
    for i in exiting:
        etype, reason = pending.pop(i)
        day = int(a["exit"][i])
        state.add_event(i, "Exit", day, prev_dept=int(a["dept"][i]), prev_level=int(a["level"][i]),
                        prev_manager=int(a["manager"][i]) if a["manager"][i] >= 0 else None,
                        exit_type=etype, exit_reason=reason)
        state.close_job(i, day)
        state.set_manager(i, -1)


def promote(state: WorkforceState, i: int, day: int) -> None:
    a = state.arrays
    day = state.job_day(i, day)
    old = int(a["level"][i])
    a["level"][i] = old + 1
    a["last_promo"][i] = day
    promotion_increase(state, i, day)
    mgr = int(a["manager"][i])
    state.record_job(i, day, "Promotion", promotion=True)
    state.add_event(i, "Promotion", day, prev_level=old, new_level=old + 1, prev_dept=int(a["dept"][i]),
                    new_dept=int(a["dept"][i]))
    if mgr >= 0 and a["level"][mgr] <= a["level"][i]:
        change_manager(state, i, day, record_event=True)


def annual_cycle(state: WorkforceState, ms: int, me: int) -> None:
    """April: performance reviews for the fiscal year just ended, promotions and
    merit increases effective 1 April."""
    rng, a = state.rng, state.arrays
    fy_end = from_ordinal(ms - 1)
    fy_start = fy_end.replace(year=fy_end.year - 1, month=4, day=1)
    write_reviews(state, ordinal(fy_start), ms - 1, ms, me)
    idx = state.active_indices()
    for i in idx:
        i = int(i)
        lvl = int(a["level"][i])
        if lvl >= 7 or (ms - a["last_promo"][i]) < 365 or a["rating"][i] == 0:
            continue
        p = {5: 0.38, 4: 0.20, 3: 0.055}.get(int(a["rating"][i]), 0.0)
        p *= {1: 1.1, 2: 1.0, 3: 0.9, 4: 0.75, 5: 0.6, 6: 0.4}[lvl]
        if rng.random() < p:
            promote(state, i, ms)
    for i in idx:
        i = int(i)
        if (ms - a["join"][i]) >= 90:  # recent joiners wait for the next cycle
            merit_increase(state, i, ms)


def write_reviews(state: WorkforceState, period_start: int, period_end: int, ms: int, me: int,
                  cycle_label: str | None = None, population: np.ndarray | None = None) -> None:
    rng, a = state.rng, state.arrays
    idx = state.active_indices() if population is None else population
    cycle = cycle_label or f"FY{(from_ordinal(period_end).year) % 100:02d}"
    cr = comp_ratio(state, idx, period_end)
    eng = engagement_latent(state, idx, period_end, cr)
    for k, i in enumerate(idx):
        i = int(i)
        if a["join"][i] > period_end - 90 or a["level"][i] >= 9:
            continue
        hours = sum(h for d, h, cat in state.training_done[i] if period_start <= d <= period_end)
        dev_hours = sum(h for d, h, cat in state.training_done[i]
                        if period_start <= d <= period_end and cat != "Compliance")
        score = 3 + 0.75 * a["ability"][i] + 0.2 * eng[k] + (0.15 if dev_hours >= 16 else 0.0) + rng.normal(0, 0.65)
        rating = int(np.digitize(score, [1.3, 2.05, 3.55, 4.35]) + 1)
        day = int(rng.integers(ms + 3, min(ms + 25, me) + 1))
        reviewer = int(a["manager"][i])
        completed = rng.random() > 0.035
        if completed:
            a["rating"][i] = rating
        state.reviews.append(dict(
            employee_idx=i, review_cycle=cycle, review_period_start=max(period_start, int(a["join"][i])),
            review_period_end=period_end, review_day=day,
            performance_rating=rating if completed else None,
            goal_achievement_score=round(float(np.clip(52 + 15 * rating + rng.normal(0, 8), 0, 150)), 1)
            if completed else None,
            training_hours=float(hours), reviewer_idx=reviewer if reviewer >= 0 else None,
            review_completion_status="Completed" if completed else "Not Completed",
        ))
        if day > a["last_event"][i]:
            a["last_event"][i] = day


def transfers_and_changes(state: WorkforceState, ms: int, me: int) -> None:
    """Internal transfers, off-cycle promotions and organisational manager changes."""
    rng, a = state.rng, state.arrays
    idx = state.active_indices()
    ops = [d for d in state.departments if d.business_unit != "Executive"]
    for i in idx:
        i = int(i)
        lvl = int(a["level"][i])
        tenure = ms - a["join"][i]
        u = rng.random()
        if lvl <= 5 and tenure > 365 and u < 0.0024 and (ms - a["last_transfer"][i]) > 365:
            src = state.departments[int(a["dept"][i])]
            if src.business_unit == "Executive":
                continue
            same_bu = [d for d in ops if d.business_unit == src.business_unit and d.idx != src.idx]
            pool = same_bu if (same_bu and rng.random() < 0.7) else [d for d in ops if d.idx != src.idx]
            w = np.array([d.size_weight for d in pool])
            dst = pool[rng.choice(len(pool), p=w / w.sum())]
            day = state.job_day(i, int(rng.integers(ms + 1, me + 1)))
            old_mgr = int(a["manager"][i])
            state.dept_members[src.idx].discard(i)
            a["dept"][i] = dst.idx
            state.dept_members[dst.idx].add(i)
            fam = JOB_FAMILIES[int(a["family"][i])]
            if fam == src.family and fam != dst.family and rng.random() < 0.6:
                a["family"][i] = JOB_FAMILIES.index(dst.family)
            if state.loc_names[int(a["loc"][i])] not in dst.loc_names or rng.random() < 0.3:
                a["loc"][i] = state.loc_names.index(dst.loc_names[rng.choice(len(dst.loc_names), p=dst.loc_weights)])
            new_mgr = assign_manager(state, i, day)
            state.set_manager(i, new_mgr)
            a["last_transfer"][i] = day
            a["last_mgr_change"][i] = day
            state.record_job(i, day, "Transfer", transfer=True)
            state.add_event(i, "Transfer", day, prev_dept=src.idx, new_dept=dst.idx, prev_level=lvl,
                            new_level=lvl, prev_manager=old_mgr if old_mgr >= 0 else None,
                            new_manager=new_mgr if new_mgr >= 0 else None)
            if state.reports_count[i] > 0:
                reassign_reports(state, i, day)
        elif u < 0.0024 + 0.0015 and lvl <= 6 and a["rating"][i] >= 4 and (ms - a["last_promo"][i]) > 365:
            promote(state, i, int(rng.integers(ms + 1, me + 1)))
        elif u < 0.0039 + 0.0022 and lvl <= 6 and a["manager"][i] >= 0:
            change_manager(state, i, int(rng.integers(ms + 1, me + 1)),
                           exclude={int(a["manager"][i])})


# ----------------------------------------------------------------- training

def enrol_training(state: WorkforceState, ms: int, me: int, month: int) -> None:
    rng, a = state.rng, state.arrays
    n = state.n
    idx = np.flatnonzero(a["active"][:n] | (a["exit"][:n] > ms))
    end_o = ordinal(state.cfg.end_date)
    for i in idx:
        i = int(i)
        lvl = int(a["level"][i])
        bu = state.departments[int(a["dept"][i])].business_unit
        chosen = []
        if month == 6:  # annual compliance refresher
            chosen.append(TRAINING_PROGRAMS[0])
        if rng.random() < 0.035:
            eligible = [p for p in TRAINING_PROGRAMS[1:] if p[4][0] <= lvl <= p[4][1]
                        and (p[5] is None or bu in p[5])]
            if eligible:
                w = np.array([3.0 if (p[1] == "Leadership" and a["rating"][i] >= 4) else 1.0 for p in eligible])
                chosen.append(eligible[rng.choice(len(eligible), p=w / w.sum())])
        for name, cat, hours, cost, _lv, _bu in chosen:
            start = int(rng.integers(ms, me + 1))
            if start <= a["join"][i] or (a["exit"][i] and start >= a["exit"][i]):
                continue
            duration = 14 if cat == "Compliance" else int(rng.integers(14, 70))
            done = start + duration
            p_complete = 0.94 if cat == "Compliance" else 0.82 + 0.03 * a["ability"][i]
            status, comp_day, score = "Completed", done, None
            if rng.random() > p_complete:
                status, comp_day = "Not Completed", None
            if comp_day and comp_day > end_o:
                status, comp_day = "In Progress", None
            if status == "Completed":
                score = round(float(np.clip(74 + 8 * a["ability"][i] + rng.normal(0, 7), 40, 100)), 1)
                state.training_done[i].append((comp_day, hours, cat))
            state.training.append(dict(employee_idx=i, training_program=name, training_category=cat,
                                       enrollment_day=start, completion_day=comp_day,
                                       training_hours=float(hours), training_cost=float(cost),
                                       completion_status=status, assessment_score=score))
