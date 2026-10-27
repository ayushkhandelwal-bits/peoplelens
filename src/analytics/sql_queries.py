"""Curated, parameterised SQL library for the SQL Explorer.

Each entry states the business question, the SQL (portable across SQLite and
PostgreSQL; the few dialect-specific expressions are filled in at run time),
its parameters, a suggested chart and an interpretation computed from the
actual result (no pre-written numbers).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy.engine import Engine

from database.connection import days_between, get_engine, month_key, quarter_key, read_sql


def _pct(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:.1%}"


@dataclass
class LibraryQuery:
    key: str
    name: str
    question: str
    sql: str
    params: list[str]  # subset of: month, start, end, department
    chart: dict = field(default_factory=dict)  # {"type": "bar"|"line", "x":..., "y":..., "color":...}
    interpret: Callable[[pd.DataFrame], str] = lambda df: ""

    def render(self, engine: Engine | None = None) -> str:
        engine = engine or get_engine()
        return self.sql.format(
            month_ev=month_key("e.event_date", engine), month_snap=month_key("m.snapshot_date", engine),
            quarter_ev=quarter_key("e.event_date", engine),
            days_ttf=days_between("r.offer_accepted_date", "r.opened_date", engine),
        ).strip()

    def run(self, values: dict, engine: Engine | None = None, ro_engine: Engine | None = None) -> pd.DataFrame:
        engine = engine or get_engine()
        sql = self.render(engine)
        params = {k: values.get(k) for k in self.params}
        if "department" in params and not params["department"]:
            params["department"] = None
        return read_sql(sql, params, ro_engine or engine)


DEPT_CLAUSE = "(CAST(:department AS VARCHAR(60)) IS NULL OR d.department_name = :department)"


def _top(df, label, value, fmt=lambda v: f"{v:,.0f}"):
    if df.empty:
        return "No rows for the selected parameters."
    r = df.sort_values(value, ascending=False).iloc[0]
    return f"Highest: **{r[label]}** ({fmt(r[value])})."


LIBRARY: list[LibraryQuery] = [
    LibraryQuery(
        "headcount_by_department", "Headcount by department",
        "How is the active workforce distributed across departments at a month end?",
        f"""
SELECT d.department_name, d.business_unit, COUNT(*) AS active_headcount
FROM monthly_workforce_snapshots m
JOIN departments d ON d.department_id = m.department_id
WHERE m.snapshot_date = :month AND m.active_flag = 1 AND {DEPT_CLAUSE}
GROUP BY d.department_name, d.business_unit
ORDER BY active_headcount DESC""",
        ["month", "department"], {"type": "bar", "x": "department_name", "y": "active_headcount"},
        lambda df: (f"{df.active_headcount.sum():,} active employees in {len(df)} departments. "
                    + _top(df, "department_name", "active_headcount")
                    + (f" The largest department holds {df.active_headcount.max() / df.active_headcount.sum():.0%} "
                       "of the workforce." if len(df) else "")),
    ),
    LibraryQuery(
        "monthly_joiners_exits", "Monthly joiners and exits",
        "Is the organisation growing or shrinking month by month, and why?",
        """
SELECT m.snapshot_date AS month,
       SUM(m.active_flag) AS month_end_headcount,
       SUM(m.joined_in_month) AS joiners,
       SUM(m.exited_in_month) AS exits,
       SUM(m.joined_in_month) - SUM(m.exited_in_month) AS net_change
FROM mart_employee_month m
JOIN departments d ON d.department_id = m.department_id
WHERE m.snapshot_date BETWEEN :start AND :end AND {dept}
GROUP BY m.snapshot_date
ORDER BY m.snapshot_date""".replace("{dept}", DEPT_CLAUSE),
        ["start", "end", "department"], {"type": "line", "x": "month", "y": ["joiners", "exits"]},
        lambda df: ("No rows." if df.empty else
                    f"{int(df.joiners.sum()):,} joiners vs {int(df.exits.sum()):,} exits "
                    f"(net {int(df.net_change.sum()):+,}). Net change was negative in "
                    f"{int((df.net_change < 0).sum())} of {len(df)} months."),
    ),
    LibraryQuery(
        "attrition_by_tenure", "Attrition by tenure band",
        "At which stage of the employee lifecycle do people leave?",
        f"""
SELECT CASE WHEN m.tenure_months < 3 THEN '1. 0-3 months'
            WHEN m.tenure_months < 12 THEN '2. 3-12 months'
            WHEN m.tenure_months < 36 THEN '3. 1-3 years'
            ELSE '4. 3+ years' END AS tenure_band,
       SUM(m.exited_in_month) AS exits,
       SUM(m.active_flag) * 1.0 / COUNT(DISTINCT m.snapshot_date) AS avg_headcount,
       SUM(m.exited_in_month) * 12.0 / COUNT(DISTINCT m.snapshot_date)
           / (SUM(m.active_flag) * 1.0 / COUNT(DISTINCT m.snapshot_date)) AS annualised_attrition
FROM mart_employee_month m
JOIN departments d ON d.department_id = m.department_id
WHERE m.snapshot_date BETWEEN :start AND :end AND {DEPT_CLAUSE}
GROUP BY 1 ORDER BY 1""",
        ["start", "end", "department"], {"type": "bar", "x": "tenure_band", "y": "annualised_attrition"},
        lambda df: ("No rows." if df.empty else
                    _top(df, "tenure_band", "annualised_attrition", _pct)
                    + " Rates are exits / average headcount in the band, annualised; the 0-3 month band is "
                      "small, so treat its rate with caution."),
    ),
    LibraryQuery(
        "recruitment_conversion", "Recruitment conversion by source",
        "Which sourcing channels convert applicants into joiners, not just volume?",
        f"""
SELECT c.recruitment_source,
       COUNT(*) AS applications,
       SUM(CASE WHEN c.furthest_stage IN ('Interviewed','Offered','Accepted','Joined') THEN 1 ELSE 0 END) AS interviewed,
       SUM(CASE WHEN c.furthest_stage IN ('Offered','Accepted','Joined') THEN 1 ELSE 0 END) AS offered,
       SUM(CASE WHEN c.final_disposition = 'Joined' THEN 1 ELSE 0 END) AS joined,
       SUM(CASE WHEN c.final_disposition = 'Joined' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS join_yield
FROM candidates c
JOIN job_requisitions r ON r.job_requisition_id = c.job_requisition_id
JOIN departments d ON d.department_id = r.department_id
WHERE c.application_date BETWEEN :start AND :end AND {DEPT_CLAUSE}
GROUP BY c.recruitment_source
ORDER BY join_yield DESC""",
        ["start", "end", "department"], {"type": "bar", "x": "recruitment_source", "y": "join_yield"},
        lambda df: ("No rows." if df.empty else
                    f"Best yield: **{df.iloc[0].recruitment_source}** ({_pct(df.iloc[0].join_yield)} of applicants "
                    f"join) vs **{df.iloc[-1].recruitment_source}** ({_pct(df.iloc[-1].join_yield)}). Highest "
                    f"volume: {df.sort_values('applications').iloc[-1].recruitment_source}."),
    ),
    LibraryQuery(
        "compensation_by_level", "Average compensation by job level",
        "How does pay scale with level, and what does a typical role cost the employer?",
        """
SELECT m.job_level, COUNT(*) AS employees,
       AVG(c.annual_base_salary) AS avg_base_salary,
       AVG(c.annual_base_salary + c.variable_compensation + c.benefits_cost) AS avg_total_employer_cost
FROM monthly_workforce_snapshots m
JOIN departments d ON d.department_id = m.department_id
JOIN compensation_history c ON c.employee_id = m.employee_id
     AND c.effective_start_date <= m.snapshot_date
     AND (c.effective_end_date IS NULL OR c.effective_end_date >= m.snapshot_date)
WHERE m.snapshot_date = :month AND m.active_flag = 1 AND {dept}
GROUP BY m.job_level ORDER BY m.job_level""".replace("{dept}", DEPT_CLAUSE),
        ["month", "department"], {"type": "bar", "x": "job_level", "y": "avg_base_salary"},
        lambda df: ("No rows." if df.empty else
                    f"Average base salary rises from ₹{df.avg_base_salary.iloc[0] / 1e5:,.1f} L at {df.job_level.iloc[0]} "
                    f"to ₹{df.avg_base_salary.iloc[-1] / 1e5:,.1f} L at {df.job_level.iloc[-1]}. Total employer cost "
                    "adds variable pay at target and benefits (fictional INR)."),
    ),
    LibraryQuery(
        "promotion_trends", "Promotion trends by quarter",
        "How many promotions happen each quarter and how concentrated are they in the annual cycle?",
        f"""
SELECT {{quarter_ev}} AS quarter, COUNT(*) AS promotions
FROM employee_events e
JOIN employees em ON em.employee_id = e.employee_id
JOIN departments d ON d.department_id = em.department_id
WHERE e.event_type = 'Promotion' AND e.event_date BETWEEN :start AND :end AND {DEPT_CLAUSE}
GROUP BY {{quarter_ev}} ORDER BY 1""",
        ["start", "end", "department"], {"type": "bar", "x": "quarter", "y": "promotions"},
        lambda df: ("No rows." if df.empty else
                    _top(df, "quarter", "promotions")
                    + f" {df.promotions.max() / df.promotions.sum():.0%} of promotions in the range fall in that "
                      "quarter (the April review cycle). Department = current department."),
    ),
    LibraryQuery(
        "headcount_vs_plan", "Headcount vs plan by department",
        "Which departments are furthest behind or ahead of the approved workforce plan?",
        f"""
SELECT d.department_name, b.planned_headcount,
       COALESCE(a.actual_headcount, 0) AS actual_headcount,
       COALESCE(a.actual_headcount, 0) - b.planned_headcount AS variance,
       (COALESCE(a.actual_headcount, 0) - b.planned_headcount) * 1.0 / b.planned_headcount AS variance_pct
FROM workforce_budget b
JOIN departments d ON d.department_id = b.department_id
LEFT JOIN (SELECT department_id, SUM(active_flag) AS actual_headcount FROM monthly_workforce_snapshots
           WHERE snapshot_date = :month GROUP BY department_id) a ON a.department_id = b.department_id
WHERE b.planning_period = :month AND b.scenario_name = 'AOP' AND {DEPT_CLAUSE}
ORDER BY variance""",
        ["month", "department"], {"type": "bar", "x": "department_name", "y": "variance"},
        lambda df: ("No plan rows for this month." if df.empty else
                    f"Total variance {int(df.variance.sum()):+,} heads. Largest shortfall: "
                    f"**{df.iloc[0].department_name}** ({int(df.iloc[0].variance):+,}, {_pct(df.iloc[0].variance_pct)})."),
    ),
    LibraryQuery(
        "span_of_control", "Span of control",
        "How many direct reports do people managers have?",
        f"""
SELECT m.manager_id, COUNT(*) AS direct_reports
FROM monthly_workforce_snapshots m
JOIN departments d ON d.department_id = m.department_id
WHERE m.snapshot_date = :month AND m.active_flag = 1 AND m.manager_id IS NOT NULL AND {DEPT_CLAUSE}
GROUP BY m.manager_id
ORDER BY direct_reports DESC""",
        ["month", "department"], {"type": "histogram", "x": "direct_reports"},
        lambda df: ("No rows." if df.empty else
                    f"{len(df):,} managers; median span {df.direct_reports.median():.0f}, "
                    f"{int((df.direct_reports >= 12).sum())} managers with 12+ direct reports and "
                    f"{int((df.direct_reports <= 2).sum())} with 1-2."),
    ),
    LibraryQuery(
        "engagement_by_department", "Engagement by department",
        "Where is engagement highest and lowest in a survey period? (groups under 10 responses suppressed)",
        f"""
SELECT d.department_name, s.survey_period, COUNT(*) AS responses,
       AVG(s.engagement_score) AS avg_engagement,
       AVG(s.manager_support_score) AS avg_manager_support,
       (SUM(CASE WHEN s.enps_response >= 9 THEN 1 ELSE 0 END)
        - SUM(CASE WHEN s.enps_response <= 6 THEN 1 ELSE 0 END)) * 100.0 / COUNT(*) AS enps
FROM engagement_surveys s
JOIN departments d ON d.department_id = s.department_id
WHERE s.survey_date BETWEEN :start AND :end AND {DEPT_CLAUSE}
GROUP BY d.department_name, s.survey_period
HAVING COUNT(*) >= 10
ORDER BY s.survey_period DESC, avg_engagement""",
        ["start", "end", "department"], {"type": "bar", "x": "department_name", "y": "avg_engagement"},
        lambda df: ("No groups with at least 10 responses." if df.empty else
                    f"Lowest average engagement: **{df.iloc[0].department_name}** in {df.iloc[0].survey_period} "
                    f"({df.iloc[0].avg_engagement:.2f}/5, eNPS {df.iloc[0].enps:+.0f})."),
    ),
    LibraryQuery(
        "time_to_fill_by_family", "Time to fill by job family",
        "Which roles take longest to fill (requisition opened to offer accepted)?",
        f"""
SELECT r.job_family, COUNT(*) AS filled_requisitions,
       AVG({{days_ttf}}) AS avg_days_to_fill,
       MIN({{days_ttf}}) AS min_days, MAX({{days_ttf}}) AS max_days
FROM job_requisitions r
JOIN departments d ON d.department_id = r.department_id
WHERE r.offer_accepted_date BETWEEN :start AND :end AND {DEPT_CLAUSE}
GROUP BY r.job_family ORDER BY avg_days_to_fill DESC""",
        ["start", "end", "department"], {"type": "bar", "x": "job_family", "y": "avg_days_to_fill"},
        lambda df: ("No filled requisitions in range." if df.empty else
                    _top(df, "job_family", "avg_days_to_fill", lambda v: f"{v:.0f} days")
                    + " Averages exclude still-open requisitions (see Predictive Intelligence for censoring-aware "
                      "estimates)."),
    ),
    LibraryQuery(
        "exit_reasons", "Exit reasons",
        "Why do people leave, and how much is voluntary?",
        f"""
SELECT e.exit_type, e.exit_reason_category, COUNT(*) AS exits
FROM employee_events e
JOIN departments d ON d.department_id = e.previous_department_id
WHERE e.event_type = 'Exit' AND e.event_date BETWEEN :start AND :end AND {DEPT_CLAUSE}
GROUP BY e.exit_type, e.exit_reason_category ORDER BY exits DESC""",
        ["start", "end", "department"], {"type": "bar", "x": "exit_reason_category", "y": "exits", "color": "exit_type"},
        lambda df: ("No exits." if df.empty else
                    f"Top reason: **{df.iloc[0].exit_reason_category}** ({int(df.iloc[0].exits)} exits, "
                    f"{df.iloc[0].exits / df.exits.sum():.0%}). Voluntary share: "
                    f"{df[df.exit_type == 'Voluntary'].exits.sum() / df.exits.sum():.0%}."),
    ),
]

BY_KEY = {q.key: q for q in LIBRARY}
