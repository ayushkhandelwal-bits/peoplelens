"""Automated data-quality rules.

Every rule receives the full set of (cleaned, typed) tables as pandas
DataFrames and returns one or more :class:`CheckResult` records. Rules with
severity ``error`` block publication of the data to the application; ``warning``
rules are reported but do not block. The same rules run inside the pipeline
(before publishing) and on demand against the live database
(``scripts/validate_project.py`` and the Data & Governance page).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from database.schema import metadata

Tables = dict[str, pd.DataFrame]


@dataclass
class CheckResult:
    check_name: str
    category: str
    table_name: str
    severity: str  # error | warning
    status: str  # PASS | FAIL | WARN
    failed_rows: int
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


RULES: list[Callable[[Tables], list[CheckResult]]] = []


def rule(fn):
    RULES.append(fn)
    return fn


def _result(name, category, table, failed, severity="error", details="") -> CheckResult:
    failed = int(failed)
    status = "PASS" if failed == 0 else ("FAIL" if severity == "error" else "WARN")
    return CheckResult(name, category, table, severity, status, failed, details)


def _d(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


# ------------------------------------------------------------- structural

@rule
def primary_keys(t: Tables) -> list[CheckResult]:
    out = []
    for table in metadata.sorted_tables:
        if table.name not in t:
            continue
        pk = [c.name for c in table.primary_key.columns]
        df = t[table.name]
        dup = df.duplicated(subset=pk).sum() if pk else 0
        nulls = df[pk].isna().any(axis=1).sum() if pk else 0
        out.append(_result(f"Unique primary key ({', '.join(pk)})", "Duplicate primary keys", table.name,
                           dup + nulls, details=f"{dup} duplicates, {nulls} null keys"))
    return out


@rule
def foreign_keys(t: Tables) -> list[CheckResult]:
    out = []
    for table in metadata.sorted_tables:
        if table.name not in t:
            continue
        df = t[table.name]
        for fk in table.foreign_keys:
            col, ref_table, ref_col = fk.parent.name, fk.column.table.name, fk.column.name
            if ref_table not in t or col not in df:
                continue
            vals = df[col].dropna()
            missing = (~vals.isin(set(t[ref_table][ref_col]))).sum()
            out.append(_result(f"{col} -> {ref_table}.{ref_col}", "Missing foreign-key references",
                               table.name, missing))
    return out


@rule
def required_columns(t: Tables) -> list[CheckResult]:
    out = []
    for table in metadata.sorted_tables:
        if table.name not in t:
            continue
        df = t[table.name]
        req = [c.name for c in table.columns if not c.nullable and c.name in df]
        nulls = df[req].isna().sum()
        bad = nulls[nulls > 0]
        out.append(_result("Required columns populated", "Unexpected null values", table.name, bad.sum(),
                           details=", ".join(f"{k}: {v}" for k, v in bad.items())))
    return out


@rule
def column_lengths(t: Tables) -> list[CheckResult]:
    """Text values must fit their declared VARCHAR length (PostgreSQL enforces it; SQLite does not)."""
    from sqlalchemy import String

    out = []
    for table in metadata.sorted_tables:
        if table.name not in t:
            continue
        df = t[table.name]
        bad, cols = 0, []
        for c in table.columns:
            if isinstance(c.type, String) and c.type.length and c.name in df:
                n = int((df[c.name].dropna().astype(str).str.len() > c.type.length).sum())
                if n:
                    bad += n
                    cols.append(f"{c.name} > {c.type.length}")
        out.append(_result("Text values fit declared column lengths", "Invalid values", table.name, bad,
                           details=", ".join(cols)))
    return out


# -------------------------------------------------------------- date logic

@rule
def employee_dates(t: Tables) -> list[CheckResult]:
    e = t["employees"]
    j, x = _d(e.joining_date), _d(e.exit_date)
    bad_order = (x.notna() & (x <= j)).sum()
    status_mismatch = ((e.current_status == "Exited") != x.notna()).sum()
    type_mismatch = (x.notna() & e.exit_type.isna()).sum() + (x.isna() & e.exit_type.notna()).sum()
    return [
        _result("Exit date after joining date", "Invalid dates", "employees", bad_order),
        _result("Status consistent with exit date", "Invalid lifecycle sequences", "employees", status_mismatch),
        _result("Exit type present exactly for leavers", "Invalid lifecycle sequences", "employees", type_mismatch),
    ]


def _interval_checks(df: pd.DataFrame, key: str, start: str, end: str, table: str) -> list[CheckResult]:
    s, e = _d(df[start]), _d(df[end])
    inverted = (e.notna() & (e < s)).sum()
    d = pd.DataFrame({"k": df[key], "s": s, "e": e}).sort_values(["k", "s"])
    nxt_s = d.groupby("k")["s"].shift(-1)
    has_next = nxt_s.notna()
    overlap = (has_next & (d["e"].isna() | (d["e"] >= nxt_s))).sum()
    gap = (has_next & d["e"].notna() & (d["e"] + pd.Timedelta(days=1) < nxt_s)).sum()
    return [
        _result(f"{end} on/after {start}", "Invalid dates", table, inverted),
        _result("No overlapping effective-dated records", "Invalid dates", table, overlap),
        _result("No gaps between effective-dated records", "Invalid dates", table, gap, severity="warning"),
    ]


@rule
def effective_dating(t: Tables) -> list[CheckResult]:
    out = _interval_checks(t["job_history"], "employee_id", "effective_start_date", "effective_end_date", "job_history")
    out += _interval_checks(t["compensation_history"], "employee_id", "effective_start_date", "effective_end_date",
                            "compensation_history")
    mr = t["manager_relationships"]
    out += _interval_checks(mr, "employee_id", "effective_start_date", "effective_end_date",
                            "manager_relationships")[:2]
    # job history must span joining date -> exit date - 1
    e = t["employees"].set_index("employee_id")
    jh = t["job_history"]
    first = jh.groupby("employee_id")["effective_start_date"].min().pipe(_d)
    last_end = (jh.sort_values("effective_start_date").groupby("employee_id").tail(1)
                .set_index("employee_id")["effective_end_date"].pipe(_d))
    j = _d(e.joining_date).reindex(first.index)
    x = _d(e.exit_date).reindex(last_end.index)
    start_bad = (first != j).sum()
    end_bad = ((x.notna() & (last_end != x - pd.Timedelta(days=1))) | (x.isna() & last_end.notna())).sum()
    missing = (~e.index.isin(first.index)).sum()
    out += [
        _result("Job history starts on joining date", "Invalid lifecycle sequences", "job_history", start_bad + missing),
        _result("Job history closes the day before exit", "Invalid lifecycle sequences", "job_history", end_bad),
    ]
    return out


@rule
def lifecycle_events(t: Tables) -> list[CheckResult]:
    e = t["employees"].set_index("employee_id")
    ev = t["employee_events"].copy()
    ev["event_date"] = _d(ev.event_date)
    hires = ev[ev.event_type == "Hire"]
    exits = ev[ev.event_type == "Exit"]
    hire_n = hires.groupby("employee_id").size().reindex(e.index, fill_value=0)
    hire_date_bad = (hires.set_index("employee_id")["event_date"] != _d(e.joining_date).reindex(
        hires.employee_id).values).sum()
    exit_n = exits.groupby("employee_id").size().reindex(e.index, fill_value=0)
    leaver = e.exit_date.notna()
    exit_count_bad = ((leaver & (exit_n != 1)) | (~leaver & (exit_n != 0))).sum()
    ex = exits.set_index("employee_id")
    exit_date_bad = (ex["event_date"] != _d(e.exit_date).reindex(ex.index)).sum()
    exit_type_bad = (ex["exit_type"] != e.exit_type.reindex(ex.index)).sum()
    j = _d(e.joining_date).reindex(ev.employee_id).values
    x = _d(e.exit_date).reindex(ev.employee_id).values
    before_join = (ev.event_date.values < j).sum()
    after_exit = (~pd.isna(x) & (ev.event_date.values > x)).sum()
    return [
        _result("Exactly one Hire event per employee", "Invalid lifecycle sequences", "employee_events",
                (hire_n != 1).sum()),
        _result("Hire event dated on joining date", "Invalid lifecycle sequences", "employee_events", hire_date_bad),
        _result("Exactly one Exit event per leaver (none for active)", "Invalid lifecycle sequences",
                "employee_events", exit_count_bad),
        _result("Exit event matches employee exit date/type", "Invalid lifecycle sequences", "employee_events",
                exit_date_bad + exit_type_bad),
        _result("No events before joining or after exit", "Invalid lifecycle sequences", "employee_events",
                before_join + after_exit),
    ]


@rule
def duplicate_events(t: Tables) -> list[CheckResult]:
    ev = t["employee_events"]
    d1 = ev.duplicated(subset=["employee_id", "event_type", "event_date"]).sum()
    re = t["recruitment_events"]
    d2 = re.duplicated(subset=["candidate_id", "event_type"]).sum()
    return [
        _result("No duplicate employee events (employee, type, date)", "Duplicate events", "employee_events", d1),
        _result("One event per candidate per stage", "Duplicate events", "recruitment_events", d2),
    ]


# ------------------------------------------------------------- recruitment

STAGE_ORDER = ["Application", "Screening", "Interview", "Offer", "Offer Accepted", "Joining"]


@rule
def recruitment_sequences(t: Tables) -> list[CheckResult]:
    re = t["recruitment_events"].copy()
    re["event_date"] = _d(re.event_date)
    re["stage"] = re.event_type.map({s: k for k, s in enumerate(STAGE_ORDER)})
    unknown = re.stage.isna().sum()
    g = re.sort_values(["candidate_id", "stage"]).groupby("candidate_id")
    # missing stages: stage numbers must be 0..n-1 without holes
    counts = g.stage.agg(["min", "max", "count"])
    missing = ((counts["min"] != 0) | (counts["max"] - counts["min"] + 1 != counts["count"])).sum()
    prev_date = g.event_date.shift(1)
    out_of_order = (prev_date.notna() & (re.sort_values(["candidate_id", "stage"]).event_date < prev_date)).sum()
    c = t["candidates"].set_index("candidate_id")
    app = re[re.event_type == "Application"].set_index("candidate_id")["event_date"]
    app_mismatch = (app != _d(c.application_date).reindex(app.index)).sum()
    no_events = (~c.index.isin(re.candidate_id)).sum()
    # joined candidates <-> employees
    joined = re[(re.event_type == "Joining") & (re.event_status == "Joined")]
    cj = c.loc[c.index.intersection(joined.candidate_id)]
    emp = t["employees"].set_index("employee_id")
    no_emp = cj.associated_employee_id.isna().sum()
    jd = joined.set_index("candidate_id")["event_date"]
    emp_join = _d(emp.joining_date).reindex(cj.associated_employee_id.dropna()).values
    date_bad = (jd.reindex(cj.dropna(subset=["associated_employee_id"]).index).values != emp_join).sum()
    linked_without_join = c.associated_employee_id.notna().sum() - len(cj.dropna(subset=["associated_employee_id"]))
    return [
        _result("Known recruitment event types", "Missing recruitment stages", "recruitment_events", unknown),
        _result("No skipped funnel stages", "Missing recruitment stages", "recruitment_events", missing),
        _result("Stage dates in chronological order", "Invalid dates", "recruitment_events", out_of_order),
        _result("Application event matches candidate application date", "Invalid dates", "candidates",
                app_mismatch),
        _result("Every candidate has an application event", "Missing recruitment stages", "candidates", no_events),
        _result("Joined candidates link to an employee joining that day", "Invalid lifecycle sequences",
                "candidates", no_emp + date_bad),
        _result("Candidates linked to employees only with a Joining event", "Invalid lifecycle sequences",
                "candidates", max(0, linked_without_join)),
    ]


# ------------------------------------------------------------- reporting lines

@rule
def manager_validity(t: Tables) -> list[CheckResult]:
    out = []
    e = t["employees"]
    self_mgr = (e.current_manager_id == e.employee_id).sum() + (
        t["manager_relationships"].manager_id == t["manager_relationships"].employee_id).sum()
    out.append(_result("Nobody manages themselves", "Impossible manager relationships", "manager_relationships",
                       self_mgr))
    # cycles in the current reporting graph
    parent = {k: (v if isinstance(v, str) else None) for k, v in zip(e.employee_id, e.current_manager_id)}
    cycles = 0
    for start in parent:
        seen, node, steps = set(), start, 0
        while node is not None and steps < 50:
            if node in seen:
                cycles += 1
                break
            seen.add(node)
            node = parent.get(node)
            steps += 1
    out.append(_result("No cycles in the reporting hierarchy", "Impossible manager relationships", "employees",
                       cycles))
    active_no_mgr = ((e.current_status == "Active") & e.current_manager_id.isna() & (e.job_level != "L9")).sum()
    out.append(_result("Active employees (except CEO) have a manager", "Impossible manager relationships",
                       "employees", active_no_mgr))
    # point-in-time: manager senior to report and employed in that month
    s = t["monthly_workforce_snapshots"]
    act = s[(s.active_flag == 1) & s.manager_id.notna()][["snapshot_date", "employee_id", "manager_id", "job_level"]]
    mgr = s[["snapshot_date", "employee_id", "job_level", "active_flag"]].rename(
        columns={"employee_id": "manager_id", "job_level": "mgr_level", "active_flag": "mgr_active"})
    m = act.merge(mgr, on=["snapshot_date", "manager_id"], how="left")
    not_employed = (m.mgr_active != 1).sum()
    lvl = m.job_level.str[1:].astype(int)
    mlvl = m.mgr_level.fillna("L0").str[1:].astype(int)
    not_senior = (mlvl <= lvl).sum()
    out.append(_result("Manager employed at month end", "Impossible manager relationships",
                       "monthly_workforce_snapshots", not_employed))
    out.append(_result("Manager more senior than report", "Impossible manager relationships",
                       "monthly_workforce_snapshots", not_senior))
    return out


# --------------------------------------------------------------- snapshots

@rule
def snapshot_reconciliation(t: Tables) -> list[CheckResult]:
    s = t["monthly_workforce_snapshots"].copy()
    e = t["employees"]
    s["snapshot_date"] = _d(s.snapshot_date)
    months = sorted(s.snapshot_date.unique())
    j, x = _d(e.joining_date), _d(e.exit_date)
    hc_snap = s.groupby("snapshot_date").active_flag.sum()
    expected = pd.Series({m: int(((j <= m) & (x.isna() | (x > m))).sum()) for m in months})
    point_bad = int((hc_snap.reindex(expected.index) != expected).sum())
    # flow: hc(m) = hc(m-1) + joiners(m) - exits(m)
    flow_bad = 0
    for prev, cur in zip(months[:-1], months[1:]):
        joins = ((j > prev) & (j <= cur)).sum()
        exits = ((x > prev) & (x <= cur)).sum()
        if hc_snap[cur] != hc_snap[prev] + joins - exits:
            flow_bad += 1
    # rows only for employed months; leavers inactive in their exit month only
    inactive = s[s.active_flag == 0]
    xi = _d(e.set_index("employee_id").exit_date).reindex(inactive.employee_id).values
    wrong_inactive = int((pd.isna(xi) | (pd.DatetimeIndex(xi).to_period("M") !=
                                         inactive.snapshot_date.dt.to_period("M").values)).sum())
    # department flow incl. transfers
    ev = t["employee_events"].copy()
    ev["event_date"] = _d(ev.event_date)
    dept_bad = 0
    act = s[s.active_flag == 1].groupby(["snapshot_date", "department_id"]).size().unstack(fill_value=0)
    if len(months) > 1:
        for prev, cur in zip(months[:-1], months[1:]):
            win = ev[(ev.event_date > prev) & (ev.event_date <= cur)]
            delta = (win[win.event_type == "Hire"].groupby("new_department_id").size()
                     .sub(win[win.event_type == "Exit"].groupby("previous_department_id").size(), fill_value=0)
                     .add(win[win.event_type == "Transfer"].groupby("new_department_id").size(), fill_value=0)
                     .sub(win[win.event_type == "Transfer"].groupby("previous_department_id").size(), fill_value=0))
            diff = (act.loc[cur] - act.loc[prev]).sub(delta.reindex(act.columns, fill_value=0))
            dept_bad += int((diff.abs() > 0).sum())
    return [
        _result("Month-end headcount equals employees active at month end", "Inconsistent headcount reconciliation",
                "monthly_workforce_snapshots", point_bad),
        _result("Headcount flow: opening + joiners - exits = closing", "Inconsistent headcount reconciliation",
                "monthly_workforce_snapshots", flow_bad),
        _result("Department flow incl. transfers reconciles", "Inconsistent headcount reconciliation",
                "monthly_workforce_snapshots", dept_bad),
        _result("Inactive snapshot rows only in the exit month", "Invalid lifecycle sequences",
                "monthly_workforce_snapshots", wrong_inactive),
    ]


# ----------------------------------------------------- amounts and ranges

@rule
def amounts_and_ranges(t: Tables) -> list[CheckResult]:
    ch = t["compensation_history"]
    tr = t["training_records"]
    rq = t["job_requisitions"]
    sv = t["engagement_surveys"]
    pr = t["performance_reviews"]
    b = t["workforce_budget"]
    score_cols = ["engagement_score", "job_satisfaction_score", "manager_support_score",
                  "work_life_balance_score", "career_growth_score"]
    return [
        _result("Compensation amounts positive / non-negative", "Negative amounts", "compensation_history",
                ((ch.annual_base_salary <= 0) | (ch.variable_compensation < 0) | (ch.benefits_cost < 0)).sum()),
        _result("Training cost and hours non-negative", "Negative amounts", "training_records",
                ((tr.training_cost < 0) | (tr.training_hours < 0)).sum()),
        _result("Recruitment cost non-negative", "Negative amounts", "job_requisitions",
                (rq.recruitment_cost.fillna(0) < 0).sum()),
        _result("Survey scores within 1-5 and eNPS within 0-10", "Invalid values", "engagement_surveys",
                ((sv[score_cols] < 1) | (sv[score_cols] > 5)).any(axis=1).sum()
                + ((sv.enps_response < 0) | (sv.enps_response > 10)).sum()),
        _result("Ratings 1-5, null only when review not completed", "Invalid values", "performance_reviews",
                (pr.performance_rating.notna() & ~pr.performance_rating.between(1, 5)).sum()
                + (pr.performance_rating.isna() & (pr.review_completion_status == "Completed")).sum()),
        _result("Budget figures non-negative", "Negative amounts", "workforce_budget",
                ((b.planned_headcount < 0) | (b.planned_people_cost < 0) | (b.planned_hires < 0)).sum()),
    ]


@rule
def activity_within_employment(t: Tables) -> list[CheckResult]:
    e = t["employees"].set_index("employee_id")
    j, x = _d(e.joining_date), _d(e.exit_date)

    def outside(df, col, table, label, severity="error"):
        d = _d(df[col])
        jj = j.reindex(df.employee_id).values
        xx = x.reindex(df.employee_id).values
        bad = (d.values < jj) | (~pd.isna(xx) & (d.values >= xx))
        return _result(label, "Invalid dates", table, int(np.nansum(bad)), severity)

    sv, pr, tr = t["engagement_surveys"], t["performance_reviews"], t["training_records"]
    tr_done = tr[tr.completion_date.notna()]
    period_bad = ((_d(pr.review_period_start) > _d(pr.review_period_end))
                  | (_d(pr.review_period_end) >= _d(pr.review_date))).sum()
    return [
        outside(sv, "survey_date", "engagement_surveys", "Survey responses while employed"),
        outside(pr, "review_date", "performance_reviews", "Reviews while employed"),
        _result("Review period precedes review date", "Invalid dates", "performance_reviews", period_bad),
        outside(tr, "enrollment_date", "training_records", "Training enrolment while employed"),
        _result("Completion on/after enrolment", "Invalid dates", "training_records",
                (_d(tr_done.completion_date) < _d(tr_done.enrollment_date)).sum()),
        _result("One survey response per employee per period", "Duplicate events", "engagement_surveys",
                sv.duplicated(subset=["employee_id", "survey_period"]).sum()),
    ]


@rule
def plausibility(t: Tables) -> list[CheckResult]:
    s = t["monthly_workforce_snapshots"]
    e = t["employees"]
    months = s.snapshot_date.nunique()
    avg_hc = s.groupby("snapshot_date").active_flag.sum().mean()
    start = _d(s.snapshot_date).min() - pd.offsets.MonthBegin(1)
    exits = (_d(e.exit_date) > start).sum()
    annual = exits / avg_hc * 12 / months if avg_hc else 0
    ok = 0.05 <= annual <= 0.40
    fb = t.get("survey_feedback", pd.DataFrame({"feedback_text": []}))
    short = (fb.feedback_text.str.len() < 3).sum() if len(fb) else 0
    return [
        _result("Annual attrition within plausible range (5%-40%)", "Plausibility", "employees", 0 if ok else 1,
                severity="warning", details=f"observed {annual:.1%}"),
        _result("Feedback comments non-trivial", "Plausibility", "survey_feedback", short, severity="warning"),
    ]


def run_all(tables: Tables) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fn in RULES:
        try:
            results.extend(fn(tables))
        except Exception as exc:  # a broken rule must surface, never silently pass
            results.append(CheckResult(fn.__name__, "Rule execution", "-", "error", "FAIL", 1,
                                       f"Rule raised {type(exc).__name__}: {exc}"))
    return results


def summarise(results: list[CheckResult]) -> dict:
    df = pd.DataFrame([r.to_dict() for r in results])
    return {
        "checks": int(len(df)),
        "passed": int((df.status == "PASS").sum()),
        "warnings": int((df.status == "WARN").sum()),
        "failed": int((df.status == "FAIL").sum()),
        "blocking": bool(((df.status == "FAIL") & (df.severity == "error")).any()),
    }
