"""Recruitment simulation: requisitions, candidates and funnel events.

Each requisition is simulated when it is opened. Applicants arrive over time
and move through a fixed funnel:

    Application -> Screening -> Interview -> Offer -> Offer Accepted -> Joining

At each stage a candidate can progress, be rejected or withdraw. Stage pass
rates depend on the recruitment source (a simulation assumption). Applicants
stop arriving once an offer is accepted; candidates still in process at that
moment are closed as "Position Filled". If the accepted candidate does not
join (a "no-show"), the requisition re-opens. Everything after the data
extraction date is truncated, which leaves realistic open requisitions,
in-process candidates and accepted-but-not-yet-joined offers at the end.

Definitions used downstream:
* time-to-fill = requisition opened -> offer accepted (by the candidate who joins)
* time-to-hire = application date -> offer accepted (same candidate)
"""

from __future__ import annotations

import numpy as np

from src.data_generation.generate_compensation import offer_salary
from src.data_generation.reference_data import (
    AGENCY_FEE_PCT,
    ASSESSMENT_COST_PER_INTERVIEW,
    JOB_FAMILIES,
    RECRUITER_COST_PER_OPEN_DAY,
    RECRUITMENT_SOURCES,
    SOURCE_FIXED_COST,
)
from src.data_generation.state import Requisition, WorkforceState, ordinal

SOURCES = list(RECRUITMENT_SOURCES)
EXPERIENCE_BANDS = {1: "0-2 yrs", 2: "2-5 yrs", 3: "2-5 yrs", 4: "5-10 yrs", 5: "5-10 yrs",
                    6: "10+ yrs", 7: "10+ yrs", 8: "10+ yrs", 9: "10+ yrs"}
NOTICE_DAYS = {1: (15, 35), 2: (25, 50), 3: (30, 60), 4: (45, 75), 5: (55, 90), 6: (60, 95),
               7: (60, 100), 8: (75, 110), 9: (90, 120)}


def _pick_source(state: WorkforceState, level: int) -> str:
    w = np.array([RECRUITMENT_SOURCES[s][0] for s in SOURCES], dtype=float)
    if level > 1:
        w[SOURCES.index("Campus")] = 0.0
    if level >= 5:
        w[SOURCES.index("Recruitment Agency")] *= 2.2
        w[SOURCES.index("Job Portal")] *= 0.5
    return SOURCES[state.rng.choice(len(SOURCES), p=w / w.sum())]


def _simulate_candidate(state: WorkforceState, req: Requisition, app_day: int) -> dict:
    """Return the full (un-truncated) timeline of one applicant."""
    rng = state.rng
    source = _pick_source(state, req.level)
    _, p_cv, p_screen, p_offer, p_accept, p_join = RECRUITMENT_SOURCES[source]
    senior = req.level >= 4
    events = []  # dicts: type, day, status, decision_day, reason, offer, accepted

    def fail_status():
        return "Withdrawn" if rng.random() < 0.25 else "Rejected"

    cv_day = app_day + int(rng.integers(2, 7))
    if rng.random() >= p_cv:
        events.append(dict(type="Application", day=app_day, status="Rejected", decision=cv_day,
                           reason="CV not shortlisted"))
        return dict(source=source, events=events, outcome="Rejected")
    s_day = cv_day + int(rng.integers(1, 5))
    events.append(dict(type="Application", day=app_day, status="Shortlisted", decision=cv_day))

    i_day = s_day + int(rng.integers(5, 15)) + (int(rng.integers(0, 8)) if senior else 0)
    if rng.random() >= p_screen:
        st = fail_status()
        events.append(dict(type="Screening", day=s_day, status=st, decision=s_day + int(rng.integers(1, 4)),
                           reason="Screening - skills mismatch" if st == "Rejected" else "Candidate withdrew"))
        return dict(source=source, events=events, outcome=st)
    events.append(dict(type="Screening", day=s_day, status="Passed", decision=i_day))

    o_day = i_day + int(rng.integers(4, 13)) + (int(rng.integers(0, 10)) if senior else 0)
    if rng.random() >= p_offer:
        st = fail_status()
        events.append(dict(type="Interview", day=i_day, status=st, decision=i_day + int(rng.integers(2, 8)),
                           reason="Interview - not selected" if st == "Rejected" else "Candidate withdrew"))
        return dict(source=source, events=events, outcome=st)
    events.append(dict(type="Interview", day=i_day, status="Passed", decision=o_day))

    fam = JOB_FAMILIES.index(req.family)
    offered = offer_salary(state, req.level, fam, req.loc, o_day)
    a_day = o_day + int(rng.integers(1, 7))
    if rng.random() >= p_accept:
        reason = ["Compensation", "Counter-offer", "Location", "Accepted another offer"][rng.integers(4)]
        events.append(dict(type="Offer", day=o_day, status="Declined", decision=a_day, reason=reason,
                           offer=offered, accepted=0))
        return dict(source=source, events=events, outcome="Offer Declined")
    lo, hi = NOTICE_DAYS[req.level]
    # joins never precede the start of the HRMS history (pipeline seeded pre-window)
    j_day = max(a_day + int(rng.integers(lo, hi)), ordinal(state.cfg.start_date))
    events.append(dict(type="Offer", day=o_day, status="Accepted", decision=a_day, offer=offered, accepted=1))
    events.append(dict(type="Offer Accepted", day=a_day, status="Accepted", decision=j_day, offer=offered,
                       accepted=1))
    joins = rng.random() < p_join
    events.append(dict(type="Joining", day=j_day, status="Joined" if joins else "No-show", decision=j_day,
                       reason=None if joins else "Did not join"))
    return dict(source=source, events=events, outcome="Joined" if joins else "No-show",
                accept_day=a_day, join_day=j_day, offer=offered, app_day=app_day)


STAGE_OF = {"Application": "Applied", "Screening": "Screened", "Interview": "Interviewed",
            "Offer": "Offered", "Offer Accepted": "Accepted", "Joining": "Joined"}


def run_requisition(state: WorkforceState, req: Requisition) -> None:
    """Simulate the requisition end-to-end and register candidates/events."""
    rng = state.rng
    end_o = ordinal(state.cfg.end_date)
    cancel_day = req.opened + int(rng.integers(20, 70)) if rng.random() < 0.04 else None
    gap = 7.5 if req.level <= 3 else 11.0  # applicant inter-arrival days
    limit = req.opened + 420
    cands: list[dict] = []
    winner = None
    t = req.opened
    offers: list[tuple[int, int]] = []  # (offer day, decision day) of extended offers
    while True:
        t += max(1, int(rng.exponential(gap)))
        # Sourcing pauses while an offer is outstanding and resumes if it is declined.
        moved = True
        while moved:
            moved = False
            for o_day, d_day in offers:
                if o_day <= t < d_day:
                    t, moved = d_day, True
        if t > limit or t > end_o or (cancel_day and t >= cancel_day):
            break
        if winner is not None and t >= winner["accept_day"]:
            if winner["outcome"] == "Joined" or winner["join_day"] > end_o:
                break  # position filled
            # No-show: candidates in process were closed when the offer was
            # accepted; sourcing resumes after the missed joining date.
            for other in cands:
                if other is not winner:
                    other.setdefault("cutoff", winner["accept_day"])
            winner["final"] = True
            t = winner["join_day"]
            winner = None
            continue
        c = _simulate_candidate(state, req, t)
        c["app_day"] = t
        cands.append(c)
        for e in c["events"]:
            if e["type"] == "Offer":
                offers.append((e["day"], e["decision"]))
        if c.get("accept_day") and (winner is None or c["accept_day"] < winner["accept_day"]):
            winner = c

    cancelled = cancel_day is not None and cancel_day <= end_o and (
        winner is None or winner["accept_day"] > cancel_day)
    if winner is None and not cancelled and limit <= end_o:
        cancelled, cancel_day = True, limit  # requisition withdrawn after a long search
    if cancelled:
        winner = None
    if winner is not None and winner["accept_day"] > end_o:
        winner = None  # offer accepted after extraction: still open in the data

    fill_day = winner["accept_day"] if winner else None
    for c in cands:
        cuts = {"end": end_o}
        if c is not winner and not c.get("final"):
            if fill_day is not None:
                cuts["filled"] = fill_day
            if "cutoff" in c:
                cuts["filled"] = min(cuts.get("filled", c["cutoff"]), c["cutoff"])
            if cancelled:
                cuts["cancel"] = cancel_day
        reason = min(cuts, key=lambda k: (cuts[k], k != "end"))
        c["cut"], c["cut_reason"] = cuts[reason], reason

    if winner is not None:
        req.status = "Filled"
        req.accepted_day = winner["accept_day"]
        req.winner = winner
        if winner["outcome"] == "Joined" and winner["join_day"] <= end_o:
            req.join_day = req.closed_day = winner["join_day"]
            state.scheduled_joins.append((winner["join_day"], req))
    elif cancelled:
        req.status = "Cancelled"
        req.closed_day = cancel_day
    else:
        req.status = "Open"

    _register(state, req, cands)
    stop = min(x for x in [req.accepted_day, req.closed_day, end_o] if x is not None)
    interviews = sum(1 for c in cands for e in c.get("kept", []) if e["type"] == "Interview")
    cost = max(0, stop - req.opened) * RECRUITER_COST_PER_OPEN_DAY + interviews * ASSESSMENT_COST_PER_INTERVIEW
    if winner is not None:
        src = winner["source"]
        cost += AGENCY_FEE_PCT * winner["offer"] if src == "Recruitment Agency" else SOURCE_FIXED_COST[src]
    req.cost = round(cost, -2)


def _register(state: WorkforceState, req: Requisition, cands: list[dict]) -> None:
    for c in cands:
        cut = c["cut"]
        kept = [dict(e) for e in c["events"] if e["day"] <= cut]
        c["kept"] = kept
        if not kept:
            continue
        last = kept[-1]
        cand_id = f"CAN{len(state.candidates) + 1:06d}"
        c["candidate_id"] = cand_id
        open_ = False
        if last["decision"] > cut:
            if c["cut_reason"] == "end":
                # outcome not known yet at extraction
                if last["type"] == "Offer Accepted":
                    last["status"] = "Accepted"
                    disposition = "Offer Accepted"
                    open_ = True
                elif last["type"] == "Joining":
                    disposition = "Joined" if last["status"] == "Joined" else "No-show"
                else:
                    last["status"] = "In Progress"
                    last.pop("reason", None)
                    if last["type"] == "Offer":
                        last["accepted"] = None
                    disposition = "In Process"
                    open_ = True
            else:
                last["status"] = "Closed"
                last.pop("reason", None)
                if last["type"] == "Offer":
                    last["accepted"] = 0
                disposition = "Position Filled" if c["cut_reason"] == "filled" else "Requisition Cancelled"
        else:
            st = last["status"]
            disposition = {"Rejected": "Rejected", "Withdrawn": "Withdrawn", "Declined": "Offer Declined",
                           "Joined": "Joined", "No-show": "No-show"}.get(st)
            if disposition is None:  # decided stage but next stage beyond cut-off
                if c["cut_reason"] == "end":
                    disposition, open_ = ("Offer Accepted", True) if last["type"] == "Offer Accepted" else ("In Process", True)
                else:
                    disposition = "Position Filled" if c["cut_reason"] == "filled" else "Requisition Cancelled"
        furthest = STAGE_OF[last["type"]]
        if last["type"] == "Joining" and last["status"] != "Joined":
            furthest = "Accepted"
        cand = dict(candidate_id=cand_id, application_date=c["app_day"], job_requisition_id=req.req_id,
                    recruitment_source=c["source"], job_family=req.family, location_idx=req.loc,
                    experience_band=EXPERIENCE_BANDS[req.level], furthest_stage=furthest,
                    candidate_status="Open" if open_ else "Closed", final_disposition=disposition,
                    associated_employee_idx=None)
        state.candidates.append(cand)
        c["record"] = cand
        for e in kept:
            state.rec_events.append(dict(
                candidate_id=cand_id, job_requisition_id=req.req_id, event_type=e["type"],
                event_day=e["day"], event_status=e["status"], recruiter_idx=req.recruiter,
                recruitment_source=c["source"], rejection_reason=e.get("reason"),
                offered_compensation=round(e["offer"], -3) if e.get("offer") else None,
                accepted_flag=e.get("accepted"),
            ))


def open_requisition(state: WorkforceState, dept: int, level: int, family: str, loc: int,
                     day: int, req_type: str, hiring_manager: int | None) -> Requisition:
    hr_pool = state.special.get("recruiters", [])
    hr_pool = [r for r in hr_pool if state.active[r]]
    if not hr_pool:
        hr_dept = next(d.idx for d in state.departments if d.name == "Human Resources")
        hr_pool = [j for j in state.dept_members[hr_dept] if 2 <= state.level[j] <= 4]
        state.special["recruiters"] = hr_pool[:25]
    recruiter = int(hr_pool[state.rng.integers(len(hr_pool))]) if hr_pool else None
    req = Requisition(req_id=f"REQ{len(state.requisitions) + 1:05d}", dept=dept, loc=loc, family=family,
                      level=level, req_type=req_type, opened=day, hiring_manager=hiring_manager,
                      recruiter=recruiter)
    state.requisitions.append(req)
    run_requisition(state, req)
    return req
