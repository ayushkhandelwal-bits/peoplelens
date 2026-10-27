"""Orchestrates the month-by-month workforce simulation and assembles tables.

Order of operations inside each simulated month
-----------------------------------------------
1. (April) a new annual operating plan is approved from the opening position.
2. Scheduled joiners start work (hires come only from the recruitment funnel).
3. (April) performance reviews for the year just ended, promotions and merit
   increases; (October) market pay adjustments.
4. Scenario events (e.g. a departmental reorganisation), when in the window.
5. Voluntary and involuntary exits; leavers' direct reports are reassigned.
6. Internal transfers, off-cycle promotions and other manager changes.
7. Engagement pulse survey (quarterly) and training enrolments.
8. Requisitions are opened for gaps against each department's headcount
   trajectory; each requisition is simulated end-to-end.
9. Latent engagement evolves.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import date, datetime

import numpy as np
import pandas as pd

from config.settings import GenerationConfig
from src.data_generation.generate_compensation import market_adjustments, set_salary
from src.data_generation.generate_employees import (
    EMPLOYMENT_TYPES,
    GENDERS,
    assign_manager,
    build_organisation,
    create_employee,
    create_initial_workforce,
    sample_level,
)
from src.data_generation.generate_lifecycle import (
    annual_cycle,
    calibrate,
    change_manager,
    enrol_training,
    fix_reporting_after_exits,
    process_exits,
    transfers_and_changes,
    update_engagement_state,
    write_reviews,
)
from src.data_generation.generate_recruitment import SOURCES, open_requisition
from src.data_generation.generate_surveys import run_survey
from src.data_generation.generate_workforce_history import (
    JobTimeline,
    build_manager_relationships,
    build_snapshots,
    create_budget,
)
from src.data_generation.reference_data import JOB_FAMILIES, LOCATIONS, SECONDARY_FAMILIES
from src.data_generation.state import (
    WorkforceState,
    add_months,
    from_ordinal,
    month_end,
    ordinal,
)

log = logging.getLogger(__name__)

# Narrative events injected into the simulation (only applied when inside the window).
SCENARIO_EVENTS = [
    dict(kind="reorg", dept="Service Operations West", month=date(2025, 7, 1), share=0.45, shock=-0.9,
         hazard=1.35, duration_months=9),
    dict(kind="climate_shift", dept="Customer Support", month=date(2025, 10, 1), delta=-0.35),
    dict(kind="hiring_slowdown", business_unit="Commercial", start=date(2026, 1, 1),
         end=date(2026, 6, 30), multiplier=0.45),
]


def _is_review_month(cfg: GenerationConfig, m: date) -> bool:
    if cfg.performance_review_frequency_months == 6:
        return m.month in (4, 10)
    return m.month == 4


def _is_survey_month(cfg: GenerationConfig, t: int) -> bool:
    return (t - 1) % cfg.survey_frequency_months == 0 and t >= 1


class Simulator:
    def __init__(self, cfg: GenerationConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.state = WorkforceState(cfg, self.rng)
        self.state.init_arrays(cfg.initial_headcount * 4 + 500)
        self.backfill = defaultdict(lambda: deque(maxlen=30))
        self.dept_reqs = defaultdict(list)

    # ---------------------------------------------------------------- setup
    def setup(self) -> None:
        st, cfg = self.state, self.cfg
        build_organisation(st)
        create_initial_workforce(st)
        start = cfg.start_date
        last_april = date(start.year if start.month >= 4 else start.year - 1, 4, 1)
        if start.month != 4:
            # Latest review cycle completed before the window (gives opening ratings).
            fy_start = ordinal(date(last_april.year - 1, 4, 1))
            write_reviews(st, fy_start, ordinal(last_april) - 1, ordinal(last_april),
                          ordinal(month_end(last_april)))
            # and the opening plan covering the rest of that fiscal year
            create_budget(st, start, date(last_april.year + 1, 3, 1))
        calibrate(st)
        self._seed_pipeline()

    def _seed_pipeline(self) -> None:
        """Requisitions already open at the start of the window (backfills for
        recent leavers and approved growth), so history does not begin with an
        artificially empty hiring pipeline."""
        st, rng, cfg = self.state, self.rng, self.cfg
        monthly = (cfg.annual_voluntary_attrition + cfg.annual_involuntary_attrition) / 12
        for back in (2, 1):
            m = add_months(cfg.start_date, -back)
            ms, me = ordinal(m), ordinal(month_end(m))
            for dept in st.departments:
                if dept.business_unit == "Executive":
                    continue
                lam = dept.initial_size * (monthly * dept.attrition_factor * 1.25 + dept.actual_growth / 12)
                for _ in range(int(rng.poisson(lam))):
                    rtype = "Backfill" if rng.random() < 0.75 else "Growth"
                    self._open(dept, sample_level(st, dept), int(rng.integers(ms, me + 1)), rtype)

    # --------------------------------------------------------------- hiring
    def _join_scheduled(self, ms: int, me: int) -> None:
        st, a = self.state, self.state.arrays
        due = sorted([x for x in st.scheduled_joins if ms <= x[0] <= me], key=lambda x: (x[0], x[1].req_id))
        st.scheduled_joins = [x for x in st.scheduled_joins if not (ms <= x[0] <= me)]
        for day, req in due:
            fam = JOB_FAMILIES.index(req.family)
            src = SOURCES.index(req.winner["source"])
            i = create_employee(st, req.dept, req.level, fam, req.loc, day, src)
            set_salary(st, i, req.winner["offer"], day, "New Hire")
            hm = req.hiring_manager
            if (hm is not None and a["active"][hm] and a["dept"][hm] == req.dept
                    and a["level"][hm] > req.level and a["level"][hm] >= 4):
                mgr = hm
            else:
                mgr = assign_manager(st, i, day)
            st.set_manager(i, mgr)
            st.record_job(i, day, "Hire")
            st.add_event(i, "Hire", day, new_dept=req.dept, new_level=req.level,
                         new_manager=mgr if mgr >= 0 else None)
            req.hired_employee = i
            if "record" in req.winner:
                req.winner["record"]["associated_employee_idx"] = i
            if req.level == 7:
                st.special.setdefault("heads", {})[req.dept] = i

    def _pipeline(self, dept: int, me: int) -> int:
        live = []
        for req in self.dept_reqs[dept]:
            done = (req.join_day is not None and req.join_day <= me) or (
                req.status == "Cancelled" and req.closed_day is not None and req.closed_day <= me)
            if not done:
                live.append(req)
        self.dept_reqs[dept] = live
        return len(live)

    def _open(self, dept, level: int, day: int, req_type: str) -> None:
        st, rng, a = self.state, self.rng, self.state.arrays
        options = [f for f in SECONDARY_FAMILIES.get(dept.business_unit, [dept.family]) if f != dept.family]
        fam = dept.family if (rng.random() < 0.85 or not options) else options[rng.integers(len(options))]
        loc = st.loc_names.index(dept.loc_names[rng.choice(len(dept.loc_names), p=dept.loc_weights)])
        managers = [j for j in st.dept_members[dept.idx] if a["level"][j] > level and a["level"][j] >= 4]
        hm = int(managers[rng.integers(len(managers))]) if managers else None
        req = open_requisition(st, dept.idx, level, fam, loc, day, req_type, hm)
        self.dept_reqs[dept.idx].append(req)

    def _hiring(self, t: int, m: date, ms: int, me: int) -> None:
        st, rng, cfg = self.state, self.rng, self.cfg
        for dept in st.departments:
            if dept.business_unit == "Executive":
                continue
            target = dept.initial_size * (1 + dept.actual_growth) ** ((t + 3) / 12)
            pipeline = len(st.dept_members[dept.idx]) + self._pipeline(dept.idx, me)
            gap = target - pipeline
            mult = cfg.hiring_volume_multiplier
            for ev in SCENARIO_EVENTS:
                if (ev["kind"] == "hiring_slowdown" and dept.business_unit == ev["business_unit"]
                        and ev["start"] <= m <= ev["end"]):
                    mult *= ev["multiplier"]
            n_open = int(np.floor(gap * mult + rng.random())) if gap > 0 else 0
            for _ in range(n_open):
                q = self.backfill[dept.idx]
                if q:
                    level, rtype = q.popleft(), "Backfill"
                else:
                    level, rtype = sample_level(st, dept), "Growth"
                self._open(dept, level, int(rng.integers(ms, me + 1)), rtype)

    # --------------------------------------------------------------- events
    def _scenario_events(self, m: date, ms: int, me: int) -> None:
        st, a, rng = self.state, self.state.arrays, self.rng
        for ev in SCENARIO_EVENTS:
            if ev["kind"] == "climate_shift" and ev["month"] == m:
                for d in st.departments:
                    if d.name == ev["dept"]:
                        d.climate += ev["delta"]
                        log.info("Scenario event applied: climate shift in %s from %s", d.name, m)
                continue
            if ev["kind"] != "reorg" or ev["month"] != m:
                continue
            dept = next((d for d in st.departments if d.name == ev["dept"]), None)
            if dept is None:
                continue
            members = sorted(st.dept_members[dept.idx])
            day = ms + 6
            for i in members:
                a["shock"][i] += ev["shock"]
                a["hazard_mult"][i] = ev["hazard"]
                a["hazard_until"][i] = ordinal(add_months(m, ev["duration_months"]))
            for i in members:
                if a["level"][i] <= 6 and a["manager"][i] >= 0 and rng.random() < ev["share"]:
                    change_manager(st, int(i), day, reason="Reorganisation",
                                   exclude={int(a["manager"][i])})
            log.info("Scenario event applied: reorganisation of %s in %s", dept.name, m)

    # ------------------------------------------------------------------ run
    def run(self) -> WorkforceState:
        st, cfg = self.state, self.cfg
        t0 = time.time()
        self.setup()
        for t in range(cfg.months):
            m = add_months(cfg.start_date, t)
            ms, me = ordinal(m), ordinal(month_end(m))
            if m.month == 4:
                create_budget(st, m, date(m.year + 1, 3, 1))
            self._join_scheduled(ms, me)
            if _is_review_month(cfg, m):
                if m.month == 4:
                    annual_cycle(st, ms, me)
                else:  # mid-year check-in (only when configured)
                    write_reviews(st, ordinal(add_months(m, -6)), ms - 1, ms, me,
                                  cycle_label=f"H1-{m.year}")
            if m.month == 10:
                market_adjustments(st, st.active_indices(), ms)
            self._scenario_events(m, ms, me)
            exits = process_exits(st, ms, me, m.month)
            for i in exits:
                lvl = int(st.level[i])
                dept = st.departments[int(st.dept[i])]
                if lvl == 7:
                    self._open(dept, 7, int(st.exit[i]), "Backfill")
                else:
                    self.backfill[dept.idx].append(lvl)
            fix_reporting_after_exits(st, exits)
            transfers_and_changes(st, ms, me)
            if _is_survey_month(cfg, t):
                run_survey(st, ms, me)
            enrol_training(st, ms, me, m.month)
            self._hiring(t, m, ms, me)
            update_engagement_state(st)
        # make sure the plan covers the forward planning horizon
        last_plan = max(r["planning_period"] for r in st.budget_rows)
        horizon_end = month_end(add_months(cfg.end_date, cfg.forecast_plan_months))
        if last_plan < horizon_end:
            nxt = add_months(last_plan, 1)
            create_budget(st, nxt, date(nxt.year + (1 if nxt.month >= 4 else 0), 3, 1))
        log.info("Simulation finished in %.1fs: %d employees, %d requisitions", time.time() - t0, st.n,
                 len(st.requisitions))
        return st


# ------------------------------------------------------------ assembly

def _d(o):
    return from_ordinal(o) if o else None


def assemble_tables(st: WorkforceState) -> dict[str, pd.DataFrame]:
    cfg, a = st.cfg, st.arrays
    eid = st.emp_id
    timeline = JobTimeline(st)
    depts = st.departments

    tables: dict[str, pd.DataFrame] = {}
    tables["locations"] = pd.DataFrame(LOCATIONS, columns=["location_id", "city", "region", "office_type"])
    loc_id = st.loc_ids

    # --- department heads: most senior active employee (L7, else L8/L9 for the executive office)
    heads = {}
    for d in depts:
        members = [i for i in range(st.n) if a["dept"][i] == d.idx and a["active"][i]]
        if d.business_unit == "Executive":
            heads[d.idx] = st.special["ceo"]
            continue
        l7 = [i for i in members if a["level"][i] == 7]
        heads[d.idx] = min(l7, key=lambda i: a["join"][i]) if l7 else None
    tables["departments"] = pd.DataFrame([dict(
        department_id=d.department_id, department_name=d.name, business_unit=d.business_unit,
        primary_job_family=d.family, cost_centre=f"CC-{1000 + 10 * (d.idx + 1)}",
        department_head_employee_id=eid(heads[d.idx]) if heads[d.idx] is not None else None,
        location_id=loc_id[st.loc_names.index(d.loc_names[0])],
        department_creation_date=d.creation, department_status="Active") for d in depts])
    dept_id = {d.idx: d.department_id for d in depts}

    now = datetime.combine(cfg.end_date, datetime.min.time())
    last_change = defaultdict(int)
    for r in st.jh_rows:
        last_change[r["employee_idx"]] = max(last_change[r["employee_idx"]], r["start"])
    emp_rows = []
    for i in range(st.n):
        exited = bool(a["exit"][i])
        emp_rows.append(dict(
            employee_id=eid(i), employee_name=st.names[i], gender=GENDERS[int(a["gender"][i])],
            department_id=dept_id[int(a["dept"][i])], location_id=loc_id[int(a["loc"][i])],
            job_family=JOB_FAMILIES[int(a["family"][i])], job_level=f"L{int(a['level'][i])}",
            employment_type=EMPLOYMENT_TYPES[int(a["etype"][i])], joining_date=_d(int(a["join"][i])),
            exit_date=_d(int(a["exit"][i])) if exited else None,
            exit_type={1: "Voluntary", 2: "Involuntary"}.get(int(a["exit_type"][i])) if exited else None,
            current_status="Exited" if exited else "Active",
            current_manager_id=None if exited else eid(int(a["manager"][i])),
            experience_before_joining=float(a["exp_years"][i]), source_system="HRMS",
            created_at=datetime.combine(_d(int(a["join"][i])), datetime.min.time()),
            updated_at=datetime.combine(_d(max(last_change[i], int(a["exit"][i]))), datetime.min.time())
            if (last_change[i] or a["exit"][i]) else now,
        ))
    tables["employees"] = pd.DataFrame(emp_rows)

    jh = sorted(st.jh_rows, key=lambda r: (r["employee_idx"], r["start"]))
    tables["job_history"] = pd.DataFrame([dict(
        job_history_id=k + 1, employee_id=eid(r["employee_idx"]), effective_start_date=_d(r["start"]),
        effective_end_date=_d(r["end"]) if r["end"] is not None else None, department_id=dept_id[r["dept"]],
        location_id=loc_id[r["loc"]], job_family=JOB_FAMILIES[r["family"]], job_level=f"L{r['level']}",
        manager_id=eid(r["manager"]) if r["manager"] >= 0 else None,
        employment_type=EMPLOYMENT_TYPES[r["etype"]], change_reason=r["reason"],
        promotion_flag=r["promotion"], transfer_flag=r["transfer"]) for k, r in enumerate(jh)])

    mr = build_manager_relationships(timeline)
    mr.sort(key=lambda r: (r["employee_idx"], r["start"]))
    tables["manager_relationships"] = pd.DataFrame([dict(
        relationship_id=k + 1, employee_id=eid(r["employee_idx"]), manager_id=eid(r["manager"]),
        effective_start_date=_d(r["start"]), effective_end_date=_d(r["end"]) if r["end"] is not None else None,
        department_id=dept_id[r["dept"]]) for k, r in enumerate(mr)])

    tables["job_requisitions"] = pd.DataFrame([dict(
        job_requisition_id=q.req_id, department_id=dept_id[q.dept], location_id=loc_id[q.loc],
        job_family=q.family, job_level=f"L{q.level}", requisition_type=q.req_type, opened_date=_d(q.opened),
        requisition_status=q.status, offer_accepted_date=_d(q.accepted_day),
        closed_date=_d(q.closed_day), hired_employee_id=eid(q.hired_employee),
        hiring_manager_id=eid(q.hiring_manager), recruiter_id=eid(q.recruiter),
        recruitment_cost=q.cost) for q in st.requisitions])

    tables["candidates"] = pd.DataFrame([dict(
        candidate_id=c["candidate_id"], application_date=_d(c["application_date"]),
        job_requisition_id=c["job_requisition_id"], recruitment_source=c["recruitment_source"],
        job_family=c["job_family"], location_id=loc_id[c["location_idx"]],
        experience_band=c["experience_band"], furthest_stage=c["furthest_stage"],
        candidate_status=c["candidate_status"], final_disposition=c["final_disposition"],
        associated_employee_id=eid(c["associated_employee_idx"]) if c["associated_employee_idx"] is not None
        else None) for c in st.candidates])

    tables["recruitment_events"] = pd.DataFrame([dict(
        recruitment_event_id=k + 1, candidate_id=e["candidate_id"], job_requisition_id=e["job_requisition_id"],
        event_type=e["event_type"], event_date=_d(e["event_day"]), event_status=e["event_status"],
        recruiter_id=eid(e["recruiter_idx"]), recruitment_source=e["recruitment_source"],
        rejection_reason=e["rejection_reason"], offered_compensation=e["offered_compensation"],
        accepted_flag=e["accepted_flag"]) for k, e in enumerate(st.rec_events)])

    ev = sorted(st.events, key=lambda e: (e["day"], e["employee_idx"]))
    tables["employee_events"] = pd.DataFrame([dict(
        employee_event_id=k + 1, employee_id=eid(e["employee_idx"]), event_type=e["event_type"],
        event_date=_d(e["day"]),
        previous_department_id=dept_id.get(e["prev_dept"]) if e["prev_dept"] is not None else None,
        new_department_id=dept_id.get(e["new_dept"]) if e["new_dept"] is not None else None,
        previous_job_level=f"L{e['prev_level']}" if e["prev_level"] else None,
        new_job_level=f"L{e['new_level']}" if e["new_level"] else None,
        previous_manager_id=eid(e["prev_manager"]) if e["prev_manager"] is not None else None,
        new_manager_id=eid(e["new_manager"]) if e["new_manager"] is not None else None,
        exit_type=e["exit_type"], exit_reason_category=e["exit_reason"]) for k, e in enumerate(ev)])

    reviews = []
    for k, r in enumerate(st.reviews):
        row = timeline.at(r["employee_idx"], r["review_day"])
        reviewer = row["manager"] if row and row["manager"] >= 0 else None
        reviews.append(dict(
            review_id=k + 1, employee_id=eid(r["employee_idx"]), review_cycle=r["review_cycle"],
            review_period_start=_d(r["review_period_start"]), review_period_end=_d(r["review_period_end"]),
            review_date=_d(r["review_day"]), performance_rating=r["performance_rating"],
            goal_achievement_score=r["goal_achievement_score"], training_hours=r["training_hours"],
            reviewer_employee_id=eid(reviewer) if reviewer is not None else None,
            review_completion_status=r["review_completion_status"]))
    tables["performance_reviews"] = pd.DataFrame(reviews)

    surveys = []
    for k, s in enumerate(st.surveys):
        row = timeline.at(s["employee_idx"], s["survey_day"])
        surveys.append(dict(
            survey_response_id=k + 1, employee_id=eid(s["employee_idx"]), survey_date=_d(s["survey_day"]),
            survey_period=s["survey_period"], department_id=dept_id[row["dept"] if row else s["dept"]],
            engagement_score=s["engagement"], job_satisfaction_score=s["job_satisfaction"],
            manager_support_score=s["manager_support"], work_life_balance_score=s["work_life_balance"],
            career_growth_score=s["career_growth"], enps_response=s["enps"]))
    tables["engagement_surveys"] = pd.DataFrame(surveys)
    tables["survey_feedback"] = pd.DataFrame([dict(
        feedback_id=f["feedback_id"], survey_date=_d(f["survey_day"]), survey_period=f["survey_period"],
        department_id=dept_id[f["dept"]], tenure_band=f["tenure_band"], feedback_text=f["feedback_text"])
        for f in st.feedback])

    comp = sorted(st.comp_rows, key=lambda r: (r["employee_idx"], r["start"]))
    tables["compensation_history"] = pd.DataFrame([dict(
        compensation_record_id=k + 1, employee_id=eid(r["employee_idx"]), effective_start_date=_d(r["start"]),
        effective_end_date=_d(r["end"]) if r["end"] is not None else None, annual_base_salary=r["base"],
        variable_compensation=r["variable"], benefits_cost=r["benefits"], currency=cfg.currency,
        compensation_change_reason=r["reason"]) for k, r in enumerate(comp)])

    training = []
    for k, r in enumerate(st.training):
        i = r["employee_idx"]
        status, comp_day, score = r["completion_status"], r["completion_day"], r["assessment_score"]
        x = int(a["exit"][i])
        if x and comp_day and comp_day >= x:
            status, comp_day, score = "Not Completed", None, None
        training.append(dict(
            training_record_id=k + 1, employee_id=eid(i), training_program=r["training_program"],
            training_category=r["training_category"], enrollment_date=_d(r["enrollment_day"]),
            completion_date=_d(comp_day) if comp_day else None, training_hours=r["training_hours"],
            training_cost=r["training_cost"], completion_status=status, assessment_score=score))
    tables["training_records"] = pd.DataFrame(training)

    snaps = build_snapshots(st, timeline)
    tables["monthly_workforce_snapshots"] = pd.DataFrame([dict(
        snapshot_date=_d(s["snapshot_day"]), employee_id=eid(s["employee_idx"]), department_id=dept_id[s["dept"]],
        location_id=loc_id[s["loc"]], job_family=JOB_FAMILIES[s["family"]], job_level=f"L{s['level']}",
        manager_id=eid(s["manager"]) if s["manager"] >= 0 else None, active_flag=s["active_flag"],
        tenure_months=s["tenure_months"], employment_type=EMPLOYMENT_TYPES[s["etype"]]) for s in snaps])

    budget = sorted(st.budget_rows, key=lambda r: (r["planning_period"], r["dept"]))
    tables["workforce_budget"] = pd.DataFrame([dict(
        budget_id=k + 1, planning_period=r["planning_period"], fiscal_year=r["fiscal_year"],
        department_id=dept_id[r["dept"]], planned_headcount=r["planned_headcount"],
        planned_hires=r["planned_hires"], planned_exit_rate=r["planned_exit_rate"],
        planned_people_cost=r["planned_people_cost"], approved_budget_version=r["approved_budget_version"],
        scenario_name=r["scenario_name"]) for k, r in enumerate(budget)])

    return tables


def generate(cfg: GenerationConfig) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Run the simulation. Returns (tables, feedback_labels)."""
    sim = Simulator(cfg)
    st = sim.run()
    tables = assemble_tables(st)
    labels = pd.DataFrame(st.feedback_labels)
    return tables, labels
