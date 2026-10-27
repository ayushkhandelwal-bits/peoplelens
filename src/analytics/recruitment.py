"""Recruitment funnel analytics.

Cohort convention: candidates are grouped by **application date** within the
selected period; their progress is measured up to the data extraction date.
Candidates still in process are counted at the stage they have reached, which
understates conversion for the most recent applications (documented caveat).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.engine import Engine

from src.analytics.common import Filters, q, safe_div
from src.analytics.metrics import LEVEL_ORDER

STAGES = ["Applied", "Screened", "Interviewed", "Offered", "Accepted", "Joined"]
STAGE_RANK = {s: k for k, s in enumerate(STAGES)}
EVENT_STAGE = {"Application": "Applied", "Screening": "Screened", "Interview": "Interviewed",
               "Offer": "Offered", "Offer Accepted": "Accepted", "Joining": "Joined"}
REQ_COLUMNS = {"departments": "department_id", "locations": "location_id", "levels": "job_level",
               "employment_types": None, "business_units": None}


def _period_start(f: Filters):
    return (pd.Timestamp(f.start) - pd.offsets.MonthBegin(1)).date()


def _req_filter(f: Filters, params: dict) -> tuple[str, dict]:
    seg, params = f.segment_sql("r", params, columns=REQ_COLUMNS)
    if f.business_units:
        from database.connection import expand_in

        frag, params = expand_in(" AND d.business_unit IN :bu", params, "bu", list(f.business_units))
        seg += frag
    return seg, params


def candidate_cohort(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    seg, params = _req_filter(f, {"s": _period_start(f), "e": f.end})
    df = q(f"""
        SELECT c.candidate_id, c.application_date, c.recruitment_source, c.job_family, c.furthest_stage,
               c.final_disposition, c.candidate_status, c.associated_employee_id, r.job_requisition_id,
               r.job_level, d.department_name, d.business_unit
        FROM candidates c
        JOIN job_requisitions r ON r.job_requisition_id = c.job_requisition_id
        JOIN departments d ON d.department_id = r.department_id
        WHERE c.application_date BETWEEN :s AND :e {seg}""", params, engine, dates=["application_date"])
    df["stage_rank"] = df.furthest_stage.map(STAGE_RANK)
    return df


def funnel(cands: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for k, stage in enumerate(STAGES):
        n = int((cands.stage_rank >= k).sum())
        rows.append(dict(stage=stage, candidates=n))
    df = pd.DataFrame(rows)
    df["conversion_from_previous"] = df.candidates / df.candidates.shift(1)
    df["conversion_from_applied"] = df.candidates / df.candidates.iloc[0] if len(df) and df.candidates.iloc[0] else None
    df["drop_off"] = df.candidates.shift(1) - df.candidates
    return df


def offer_outcomes(f: Filters, engine: Engine | None = None) -> dict:
    """Offer acceptance and joining rates for offers / acceptances dated in the period."""
    seg, params = _req_filter(f, {"s": _period_start(f), "e": f.end})
    df = q(f"""
        SELECT ev.event_type, ev.event_status, COUNT(*) AS n
        FROM recruitment_events ev
        JOIN job_requisitions r ON r.job_requisition_id = ev.job_requisition_id
        JOIN departments d ON d.department_id = r.department_id
        WHERE ev.event_type IN ('Offer', 'Joining') AND ev.event_date BETWEEN :s AND :e {seg}
        GROUP BY ev.event_type, ev.event_status""", params, engine)
    get = lambda t, s: int(df[(df.event_type == t) & (df.event_status == s)].n.sum())  # noqa: E731
    acc, dec = get("Offer", "Accepted"), get("Offer", "Declined")
    joined, noshow = get("Joining", "Joined"), get("Joining", "No-show")
    return dict(offers_extended=int(df[df.event_type == "Offer"].n.sum()), offers_accepted=acc,
                offers_declined=dec, offer_acceptance_rate=safe_div(acc, acc + dec),
                joined=joined, no_shows=noshow, joining_rate=safe_div(joined, joined + noshow))


def stage_durations(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Median days between consecutive stages for candidates who progressed."""
    seg, params = _req_filter(f, {"s": _period_start(f), "e": f.end})
    ev = q(f"""
        SELECT ev.candidate_id, ev.event_type, ev.event_date
        FROM recruitment_events ev
        JOIN candidates c ON c.candidate_id = ev.candidate_id
        JOIN job_requisitions r ON r.job_requisition_id = ev.job_requisition_id
        JOIN departments d ON d.department_id = r.department_id
        WHERE c.application_date BETWEEN :s AND :e {seg}""", params, engine, dates=["event_date"])
    if ev.empty:
        return pd.DataFrame(columns=["transition", "median_days", "p75_days", "candidates"])
    w = ev.pivot_table(index="candidate_id", columns="event_type", values="event_date", aggfunc="min")
    order = ["Application", "Screening", "Interview", "Offer", "Offer Accepted", "Joining"]
    rows = []
    for a, b in zip(order[:-1], order[1:]):
        if a in w and b in w:
            d = (w[b] - w[a]).dt.days.dropna()
            rows.append(dict(transition=f"{EVENT_STAGE[a]} → {EVENT_STAGE[b]}", median_days=float(d.median()),
                             p75_days=float(d.quantile(0.75)), candidates=int(len(d))))
    return pd.DataFrame(rows)


def time_to_hire(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    """Hired candidates (offer accepted in period): application -> acceptance days."""
    seg, params = _req_filter(f, {"s": _period_start(f), "e": f.end})
    df = q(f"""
        SELECT c.candidate_id, c.recruitment_source, r.job_family, r.job_level, d.department_name,
               c.application_date, ev.event_date AS accepted_date
        FROM recruitment_events ev
        JOIN candidates c ON c.candidate_id = ev.candidate_id
        JOIN job_requisitions r ON r.job_requisition_id = ev.job_requisition_id
        JOIN departments d ON d.department_id = r.department_id
        WHERE ev.event_type = 'Offer Accepted' AND ev.event_date BETWEEN :s AND :e {seg}""",
           params, engine, dates=["application_date", "accepted_date"])
    df["days"] = (df.accepted_date - df.application_date).dt.days
    return df


def time_to_fill(f: Filters, engine: Engine | None = None, include_open: bool = False) -> pd.DataFrame:
    """Requisitions with offer accepted in the period (opened -> accepted days).

    ``include_open`` adds requisitions still open at the period end with their
    elapsed days and ``filled = 0`` (censored observations for survival analysis).
    """
    seg, params = _req_filter(f, {"s": _period_start(f), "e": f.end})
    df = q(f"""
        SELECT r.job_requisition_id, r.job_family, r.job_level, r.requisition_type, d.department_name,
               r.opened_date, r.offer_accepted_date, r.requisition_status, r.recruitment_cost
        FROM job_requisitions r JOIN departments d ON d.department_id = r.department_id
        WHERE ((r.offer_accepted_date BETWEEN :s AND :e)
               OR (:inc = 1 AND r.opened_date <= :e AND r.requisition_status = 'Open'
                   AND r.offer_accepted_date IS NULL)) {seg}""",
           {**params, "inc": int(include_open)}, engine, dates=["opened_date", "offer_accepted_date"])
    end = pd.Timestamp(f.end)
    df["filled"] = df.offer_accepted_date.notna().astype(int)
    df["days"] = (df.offer_accepted_date.fillna(end) - df.opened_date).dt.days
    df["job_level"] = pd.Categorical(df.job_level, LEVEL_ORDER, ordered=True)
    return df


def source_effectiveness(f: Filters, engine: Engine | None = None) -> pd.DataFrame:
    cands = candidate_cohort(f, engine)
    if cands.empty:
        return pd.DataFrame()
    g = cands.groupby("recruitment_source")
    df = pd.DataFrame({
        "applications": g.size(),
        "interviewed": g.stage_rank.apply(lambda s: int((s >= 2).sum())),
        "offered": g.stage_rank.apply(lambda s: int((s >= 3).sum())),
        "accepted": g.stage_rank.apply(lambda s: int((s >= 4).sum())),
        "joined": g.stage_rank.apply(lambda s: int((s >= 5).sum())),
        "declined": g.final_disposition.apply(lambda s: int((s == "Offer Declined").sum())),
    })
    df["interview_rate"] = df.interviewed / df.applications
    df["offer_rate"] = df.offered / df.applications
    df["offer_acceptance"] = df.accepted / (df.accepted + df.declined).replace(0, pd.NA)
    df["join_yield"] = df.joined / df.applications
    # quality of hire proxy: 90-day retention of hires from this source (observed >= 90 days)
    ret = q("""SELECT c.recruitment_source, e.joining_date, e.exit_date
               FROM candidates c JOIN employees e ON e.employee_id = c.associated_employee_id""", engine=engine,
            dates=["joining_date", "exit_date"])
    ret = ret[ret.recruitment_source.isin(df.index)]
    end = pd.Timestamp(q("SELECT MAX(snapshot_date) AS d FROM mart_employee_month", engine=engine).d.iloc[0])
    obs = ret[(end - ret.joining_date).dt.days >= 90]
    left = (obs.exit_date.notna() & ((obs.exit_date - obs.joining_date).dt.days < 90))
    r90 = (1 - left.groupby(obs.recruitment_source).mean()).rename("retention_90d")
    n90 = obs.groupby("recruitment_source").size().rename("hires_observed_90d")
    df = df.join(r90).join(n90)
    tth = time_to_hire(f, engine).groupby("recruitment_source").days.median().rename("median_time_to_hire")
    df = df.join(tth)
    cost = q("""
        SELECT c.recruitment_source, AVG(r.recruitment_cost) AS cost_per_hire
        FROM job_requisitions r
        JOIN candidates c ON c.associated_employee_id = r.hired_employee_id AND c.job_requisition_id = r.job_requisition_id
        WHERE r.requisition_status = 'Filled' AND r.offer_accepted_date BETWEEN :s AND :e
        GROUP BY c.recruitment_source""", {"s": _period_start(f), "e": f.end}, engine)
    df = df.join(cost.set_index("recruitment_source"))
    return df.reset_index().rename(columns={"recruitment_source": "source"}).sort_values("join_yield",
                                                                                        ascending=False)


def recruitment_kpis(f: Filters, engine: Engine | None = None) -> dict:
    cands = candidate_cohort(f, engine)
    fun = funnel(cands)
    oo = offer_outcomes(f, engine)
    tth = time_to_hire(f, engine)
    ttf = time_to_fill(f, engine)
    filled = ttf[ttf.filled == 1]
    open_reqs = q("SELECT COUNT(*) AS n FROM job_requisitions WHERE opened_date <= :e AND "
                  "(offer_accepted_date IS NULL OR offer_accepted_date > :e) AND "
                  "(closed_date IS NULL OR closed_date > :e)", {"e": f.end}, engine).n.iloc[0]
    return dict(
        applications=int(len(cands)),
        screened=int(fun.candidates.iloc[1]) if len(fun) > 1 else 0,
        interview_conversion=safe_div(fun.candidates.iloc[3], fun.candidates.iloc[2]) if len(fun) > 3 else None,
        offer_rate=safe_div(fun.candidates.iloc[3], fun.candidates.iloc[0]) if len(fun) > 3 else None,
        median_time_to_hire=float(tth.days.median()) if len(tth) else None,
        median_time_to_fill=float(filled.days.median()) if len(filled) else None,
        cost_per_hire=float(filled.recruitment_cost.mean()) if len(filled) else None,
        open_requisitions=int(open_reqs),
        **oo,
    )
