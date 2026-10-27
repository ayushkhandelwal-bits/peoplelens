"""KPI calculations verified against a small hand-built fixture with known answers."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine

from database.schema import metadata
from src.analytics import compensation, metrics, recruitment
from src.analytics.common import Filters
from src.ingestion.load_data import publish

JAN, FEB, MAR = date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)


def _mart_row(month, emp, dept, active=1, joined=0, exited=0, exit_type=None, cost=100_000.0, promoted=0):
    return dict(snapshot_date=pd.Timestamp(month), employee_id=emp, department_id=dept,
                business_unit="Commercial" if dept == "D01" else "Operations", location_id="LOC01",
                job_family="Sales", job_level="L2", employment_type="Full-time", manager_id=None,
                active_flag=active, tenure_months=24, joined_in_month=joined, exited_in_month=exited,
                exit_type=exit_type, transferred_in=0, promoted_in_month=promoted,
                monthly_base_cost=cost * 0.8, monthly_total_cost=cost)


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    path = tmp_path_factory.mktemp("fixture") / "fixture.db"
    eng = create_engine(f"sqlite:///{path}")
    metadata.create_all(eng)
    rows = []
    for m in (JAN, FEB, MAR):
        for i in range(1, 11):
            rows.append(_mart_row(m, f"E{i:02d}", "D01", promoted=int(i == 1 and m == FEB)))
        for i in range(21, 26):
            rows.append(_mart_row(m, f"E{i:02d}", "D02"))
    rows += [_mart_row(FEB, "E11", "D01", joined=1), _mart_row(MAR, "E11", "D01"),
             _mart_row(JAN, "E12", "D01"), _mart_row(FEB, "E12", "D01"),
             _mart_row(MAR, "E12", "D01", active=0, exited=1, exit_type="Voluntary", cost=50_000.0)]
    t = {
        "locations": pd.DataFrame([dict(location_id="LOC01", city="Pune", region="West", office_type="Hub")]),
        "departments": pd.DataFrame([
            dict(department_id=d, department_name=n, business_unit=bu, primary_job_family="Sales",
                 cost_centre=f"CC{d}", department_head_employee_id=None, location_id="LOC01",
                 department_creation_date=pd.Timestamp("2015-01-01"), department_status="Active")
            for d, n, bu in [("D01", "Sales", "Commercial"), ("D02", "Ops", "Operations")]]),
        "mart_employee_month": pd.DataFrame(rows),
        "workforce_budget": pd.DataFrame([
            dict(budget_id=k, planning_period=pd.Timestamp(m), fiscal_year="FY25", department_id=d,
                 planned_headcount=hc, planned_hires=0, planned_exit_rate=0.1, planned_people_cost=hc * 100_000.0,
                 approved_budget_version="v1", scenario_name="AOP")
            for k, (m, d, hc) in enumerate([(m, d, hc) for m in (JAN, FEB, MAR) for d, hc in (("D01", 12), ("D02", 5))])]),
        "job_requisitions": pd.DataFrame([
            dict(job_requisition_id=r, department_id="D01", location_id="LOC01", job_family="Sales", job_level="L2",
                 requisition_type="Backfill", opened_date=pd.Timestamp(o), requisition_status="Filled",
                 offer_accepted_date=pd.Timestamp(a), closed_date=pd.Timestamp(a), hired_employee_id=None,
                 hiring_manager_id=None, recruiter_id=None, recruitment_cost=cost)
            for r, o, a, cost in [("R1", "2025-01-01", "2025-01-31", 50_000.0),
                                  ("R2", "2025-01-10", "2025-02-19", 70_000.0)]]),
        "candidates": pd.DataFrame([
            dict(candidate_id=c, application_date=pd.Timestamp("2025-01-05"), job_requisition_id="R1",
                 recruitment_source="Job Portal", job_family="Sales", location_id="LOC01", experience_band="2-5 yrs",
                 furthest_stage=s, candidate_status="Closed", final_disposition=d, associated_employee_id=None)
            for c, s, d in [("C1", "Joined", "Joined"), ("C2", "Screened", "Rejected"),
                            ("C3", "Offered", "Offer Declined"), ("C4", "Applied", "Rejected")]]),
    }
    publish(eng, t, ["locations", "departments", "mart_employee_month", "workforce_budget", "job_requisitions",
                     "candidates"])
    return eng


def F(start=JAN, end=MAR, **kw):
    return Filters(start=start, end=end, **kw)


def test_headcount_point_in_time_and_average(fx):
    k = metrics.kpi_summary(F(), fx)
    assert k.active_headcount == 16  # Mar: 11 in D01 (E12 left) + 5 in D02
    assert k.average_headcount == pytest.approx((16 + 17 + 16) / 3)
    assert k.joiners == 1 and k.exits == 1 and k.voluntary_exits == 1


def test_attrition_rate_formula(fx):
    k = metrics.kpi_summary(F(departments=("D01",)), fx)
    avg = (11 + 12 + 11) / 3
    assert k.attrition_rate == pytest.approx(1 / avg * 12 / 3)
    assert metrics.attrition_rate(1, avg, 3) == pytest.approx(k.attrition_rate)


def test_zero_denominator_and_empty_range(fx):
    assert metrics.attrition_rate(3, 0, 12) is None
    empty = metrics.kpi_summary(F(date(2030, 1, 31), date(2030, 3, 31)), fx)
    assert not empty.has_data and empty.attrition_rate is None
    assert metrics.kpi_summary(F(), fx).net_growth is None  # no opening month in the fixture


def test_department_without_events_has_zero_attrition(fx):
    a = metrics.attrition_by("department", F(), fx).set_index("segment")
    assert a.loc["Ops", "exits"] == 0 and a.loc["Ops", "attrition_rate"] == 0
    assert a.loc["Ops", "ci_low"] == 0 and a.loc["Ops", "ci_high"] > 0


def test_plan_variance(fx):
    k = metrics.kpi_summary(F(), fx)
    assert k.planned_headcount == 17 and k.plan_variance == -1
    assert k.plan_variance_pct == pytest.approx(-1 / 17)
    assert metrics.kpi_summary(F(locations=("LOC01",)), fx).plan_variance is None  # plan not segmentable


def test_cost_per_employee_and_budget_variance(fx):
    k = metrics.kpi_summary(F(), fx)
    total = (1_100_000 + 1_200_000 + 1_150_000) + 3 * 500_000
    assert k.workforce_cost == pytest.approx(total)
    assert k.cost_per_employee == pytest.approx(total / ((16 + 17 + 16) / 3) * 12 / 3)
    bv = compensation.budget_variance_by_department(F(), fx).set_index("department_name")
    assert bv.loc["Sales", "variance"] == pytest.approx(3_450_000 - 3_600_000)
    assert bv.loc["Ops", "variance"] == pytest.approx(0)


def test_promotion_rate(fx):
    k = metrics.kpi_summary(F(), fx)
    assert k.promotions == 1
    assert k.promotion_rate == pytest.approx(1 / ((16 + 17 + 16) / 3) * 4)


def test_recruitment_funnel_conversion(fx):
    cands = recruitment.candidate_cohort(F(), fx)
    fun = recruitment.funnel(cands)
    assert fun.candidates.tolist() == [4, 3, 2, 2, 1, 1]
    assert fun.conversion_from_previous.iloc[1] == pytest.approx(3 / 4)


def test_time_to_fill(fx):
    t = recruitment.time_to_fill(F(), fx)
    assert sorted(t.days.tolist()) == [30, 40]
    assert t.days.median() == 35


def test_enps_formula():
    assert metrics.enps(pd.Series([10, 9, 8, 7, 6, 0])) == pytest.approx((2 / 6 - 2 / 6) * 100)
    assert metrics.enps(pd.Series([10, 10, 6])) == pytest.approx((2 / 3 - 1 / 3) * 100)
    assert metrics.enps(pd.Series([], dtype=float)) is None


def test_poisson_interval_contains_rate():
    lo, hi = metrics.poisson_rate_ci(10, 100, 12)
    assert lo < 0.1 < hi
