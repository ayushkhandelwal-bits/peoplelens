"""Relational schema for PeopleLens, defined once with SQLAlchemy Core.

The same metadata creates the schema on PostgreSQL (primary target) and SQLite
(portable fallback). ``database/schema.sql`` is generated from this module by
``scripts/setup.py --emit-ddl`` so the DDL in the repository never drifts from
the code.

Conventions
-----------
* Dates are stored as DATE. A row is *effective* from ``effective_start_date``
  (inclusive) to ``effective_end_date`` (inclusive); ``NULL`` end = current.
* ``exit_date`` is the separation date: the first day the person is no longer
  on payroll. An employee is active on day *d* when
  ``joining_date <= d < exit_date`` (or exit_date is NULL).
* Circular references (department head <-> employee, employee -> manager) use
  DEFERRABLE INITIALLY DEFERRED foreign keys so a full load can happen inside a
  single transaction.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


def _fk(target: str, **kw) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="DEFERRED", **kw)


locations = Table(
    "locations",
    metadata,
    Column("location_id", String(8), primary_key=True),
    Column("city", String(40), nullable=False, unique=True),
    Column("region", String(20), nullable=False),
    Column("office_type", String(30), nullable=False),
)

departments = Table(
    "departments",
    metadata,
    Column("department_id", String(8), primary_key=True),
    Column("department_name", String(60), nullable=False, unique=True),
    Column("business_unit", String(30), nullable=False),
    Column("primary_job_family", String(40), nullable=False),
    Column("cost_centre", String(12), nullable=False, unique=True),
    Column("department_head_employee_id", String(10),
           _fk("employees.employee_id", use_alter=True, name="fk_department_head")),
    Column("location_id", String(8), _fk("locations.location_id"), nullable=False),
    Column("department_creation_date", Date, nullable=False),
    Column("department_status", String(12), nullable=False),
)

employees = Table(
    "employees",
    metadata,
    Column("employee_id", String(10), primary_key=True),
    Column("employee_name", String(80), nullable=False),
    Column("gender", String(16), nullable=False),
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    Column("location_id", String(8), _fk("locations.location_id"), nullable=False),
    Column("job_family", String(40), nullable=False),
    Column("job_level", String(4), nullable=False),
    Column("employment_type", String(16), nullable=False),
    Column("joining_date", Date, nullable=False),
    Column("exit_date", Date),
    Column("exit_type", String(16)),
    Column("current_status", String(10), nullable=False),
    Column("current_manager_id", String(10), _fk("employees.employee_id")),
    Column("experience_before_joining", Float, nullable=False),
    Column("source_system", String(20), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    CheckConstraint("exit_date IS NULL OR exit_date > joining_date", name="ck_emp_exit_after_join"),
    CheckConstraint("current_status IN ('Active','Exited')", name="ck_emp_status"),
    CheckConstraint(
        "current_manager_id IS NULL OR current_manager_id <> employee_id", name="ck_emp_not_self_mgr"
    ),
)

job_history = Table(
    "job_history",
    metadata,
    Column("job_history_id", Integer, primary_key=True),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("effective_start_date", Date, nullable=False),
    Column("effective_end_date", Date),
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    Column("location_id", String(8), _fk("locations.location_id"), nullable=False),
    Column("job_family", String(40), nullable=False),
    Column("job_level", String(4), nullable=False),
    Column("manager_id", String(10), _fk("employees.employee_id")),
    Column("employment_type", String(16), nullable=False),
    Column("change_reason", String(80), nullable=False),
    Column("promotion_flag", Integer, nullable=False),
    Column("transfer_flag", Integer, nullable=False),
    CheckConstraint(
        "effective_end_date IS NULL OR effective_end_date >= effective_start_date",
        name="ck_jh_dates",
    ),
)

manager_relationships = Table(
    "manager_relationships",
    metadata,
    Column("relationship_id", Integer, primary_key=True),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("manager_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("effective_start_date", Date, nullable=False),
    Column("effective_end_date", Date),
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    CheckConstraint("employee_id <> manager_id", name="ck_mr_not_self"),
    CheckConstraint(
        "effective_end_date IS NULL OR effective_end_date >= effective_start_date",
        name="ck_mr_dates",
    ),
)

job_requisitions = Table(
    "job_requisitions",
    metadata,
    Column("job_requisition_id", String(10), primary_key=True),
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    Column("location_id", String(8), _fk("locations.location_id"), nullable=False),
    Column("job_family", String(40), nullable=False),
    Column("job_level", String(4), nullable=False),
    Column("requisition_type", String(12), nullable=False),
    Column("opened_date", Date, nullable=False),
    Column("requisition_status", String(12), nullable=False),
    Column("offer_accepted_date", Date),
    Column("closed_date", Date),
    Column("hired_employee_id", String(10), _fk("employees.employee_id")),
    Column("hiring_manager_id", String(10), _fk("employees.employee_id")),
    Column("recruiter_id", String(10), _fk("employees.employee_id")),
    Column("recruitment_cost", Float),
    CheckConstraint("requisition_status IN ('Open','Filled','Cancelled')", name="ck_req_status"),
    CheckConstraint("recruitment_cost IS NULL OR recruitment_cost >= 0", name="ck_req_cost"),
)

candidates = Table(
    "candidates",
    metadata,
    Column("candidate_id", String(10), primary_key=True),
    Column("application_date", Date, nullable=False),
    Column("job_requisition_id", String(10), _fk("job_requisitions.job_requisition_id"), nullable=False),
    Column("recruitment_source", String(24), nullable=False),
    Column("job_family", String(40), nullable=False),
    Column("location_id", String(8), _fk("locations.location_id"), nullable=False),
    Column("experience_band", String(12), nullable=False),
    Column("furthest_stage", String(16), nullable=False),
    Column("candidate_status", String(8), nullable=False),
    Column("final_disposition", String(24), nullable=False),
    Column("associated_employee_id", String(10), _fk("employees.employee_id")),
    CheckConstraint("candidate_status IN ('Open','Closed')", name="ck_cand_status"),
)

recruitment_events = Table(
    "recruitment_events",
    metadata,
    Column("recruitment_event_id", Integer, primary_key=True),
    Column("candidate_id", String(10), _fk("candidates.candidate_id"), nullable=False),
    Column("job_requisition_id", String(10), _fk("job_requisitions.job_requisition_id"), nullable=False),
    Column("event_type", String(16), nullable=False),
    Column("event_date", Date, nullable=False),
    Column("event_status", String(16), nullable=False),
    Column("recruiter_id", String(10), _fk("employees.employee_id")),
    Column("recruitment_source", String(24), nullable=False),
    Column("rejection_reason", String(40)),
    Column("offered_compensation", Float),
    Column("accepted_flag", Integer),
    UniqueConstraint("candidate_id", "event_type", name="uq_rec_event_stage"),
)

employee_events = Table(
    "employee_events",
    metadata,
    Column("employee_event_id", Integer, primary_key=True),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("event_type", String(16), nullable=False),
    Column("event_date", Date, nullable=False),
    Column("previous_department_id", String(8), _fk("departments.department_id")),
    Column("new_department_id", String(8), _fk("departments.department_id")),
    Column("previous_job_level", String(4)),
    Column("new_job_level", String(4)),
    Column("previous_manager_id", String(10), _fk("employees.employee_id")),
    Column("new_manager_id", String(10), _fk("employees.employee_id")),
    Column("exit_type", String(16)),
    Column("exit_reason_category", String(40)),
    CheckConstraint(
        "event_type IN ('Hire','Exit','Promotion','Transfer','Manager Change')", name="ck_ee_type"
    ),
)

performance_reviews = Table(
    "performance_reviews",
    metadata,
    Column("review_id", Integer, primary_key=True),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("review_cycle", String(8), nullable=False),
    Column("review_period_start", Date, nullable=False),
    Column("review_period_end", Date, nullable=False),
    Column("review_date", Date, nullable=False),
    Column("performance_rating", Integer),
    Column("goal_achievement_score", Float),
    Column("training_hours", Float, nullable=False),
    Column("reviewer_employee_id", String(10), _fk("employees.employee_id")),
    Column("review_completion_status", String(16), nullable=False),
    CheckConstraint(
        "performance_rating IS NULL OR performance_rating BETWEEN 1 AND 5", name="ck_pr_rating"
    ),
    UniqueConstraint("employee_id", "review_cycle", name="uq_pr_emp_cycle"),
)

engagement_surveys = Table(
    "engagement_surveys",
    metadata,
    Column("survey_response_id", Integer, primary_key=True),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("survey_date", Date, nullable=False),
    Column("survey_period", String(8), nullable=False),
    # Organisational attributes captured at survey time (as survey platforms do).
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    Column("engagement_score", Float, nullable=False),
    Column("job_satisfaction_score", Float, nullable=False),
    Column("manager_support_score", Float, nullable=False),
    Column("work_life_balance_score", Float, nullable=False),
    Column("career_growth_score", Float, nullable=False),
    Column("enps_response", Integer, nullable=False),
    CheckConstraint("engagement_score BETWEEN 1 AND 5", name="ck_es_range"),
    CheckConstraint("enps_response BETWEEN 0 AND 10", name="ck_es_enps"),
    UniqueConstraint("employee_id", "survey_period", name="uq_es_emp_period"),
)

# Free-text comments are stored WITHOUT employee_id: they are linked only to
# department, survey period and tenure band, so text can never be joined back to
# an individual record (privacy-by-design for "anonymous" feedback).
survey_feedback = Table(
    "survey_feedback",
    metadata,
    Column("feedback_id", Integer, primary_key=True),
    Column("survey_date", Date, nullable=False),
    Column("survey_period", String(8), nullable=False),
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    Column("tenure_band", String(12), nullable=False),
    Column("feedback_text", Text, nullable=False),
)

compensation_history = Table(
    "compensation_history",
    metadata,
    Column("compensation_record_id", Integer, primary_key=True),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("effective_start_date", Date, nullable=False),
    Column("effective_end_date", Date),
    Column("annual_base_salary", Float, nullable=False),
    Column("variable_compensation", Float, nullable=False),
    Column("benefits_cost", Float, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("compensation_change_reason", String(80), nullable=False),
    CheckConstraint("annual_base_salary > 0", name="ck_ch_base"),
    CheckConstraint("variable_compensation >= 0 AND benefits_cost >= 0", name="ck_ch_nonneg"),
)

training_records = Table(
    "training_records",
    metadata,
    Column("training_record_id", Integer, primary_key=True),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("training_program", String(60), nullable=False),
    Column("training_category", String(20), nullable=False),
    Column("enrollment_date", Date, nullable=False),
    Column("completion_date", Date),
    Column("training_hours", Float, nullable=False),
    Column("training_cost", Float, nullable=False),
    Column("completion_status", String(16), nullable=False),
    Column("assessment_score", Float),
    CheckConstraint("training_cost >= 0 AND training_hours >= 0", name="ck_tr_nonneg"),
)

monthly_workforce_snapshots = Table(
    "monthly_workforce_snapshots",
    metadata,
    Column("snapshot_date", Date, nullable=False),
    Column("employee_id", String(10), _fk("employees.employee_id"), nullable=False),
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    Column("location_id", String(8), _fk("locations.location_id"), nullable=False),
    Column("job_family", String(40), nullable=False),
    Column("job_level", String(4), nullable=False),
    Column("manager_id", String(10), _fk("employees.employee_id")),
    Column("active_flag", Integer, nullable=False),
    Column("tenure_months", Integer, nullable=False),
    Column("employment_type", String(16), nullable=False),
    PrimaryKeyConstraint("snapshot_date", "employee_id", name="pk_snapshot"),
)

workforce_budget = Table(
    "workforce_budget",
    metadata,
    Column("budget_id", Integer, primary_key=True),
    Column("planning_period", Date, nullable=False),
    Column("fiscal_year", String(6), nullable=False),
    Column("department_id", String(8), _fk("departments.department_id"), nullable=False),
    Column("planned_headcount", Integer, nullable=False),
    Column("planned_hires", Integer, nullable=False),
    Column("planned_exit_rate", Float, nullable=False),
    Column("planned_people_cost", Float, nullable=False),
    Column("approved_budget_version", String(8), nullable=False),
    Column("scenario_name", String(20), nullable=False),
    UniqueConstraint("planning_period", "department_id", "scenario_name", name="uq_budget"),
)

# ---------------------------------------------------------------- operational
data_quality_results = Table(
    "data_quality_results",
    metadata,
    Column("result_id", Integer, primary_key=True),
    Column("run_id", String(20), nullable=False),
    Column("check_name", String(160), nullable=False),
    Column("category", String(60), nullable=False),
    Column("table_name", String(60), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("status", String(6), nullable=False),
    Column("failed_rows", Integer, nullable=False),
    Column("details", Text),
    Column("checked_at", DateTime, nullable=False),
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", String(20), primary_key=True),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("status", String(12), nullable=False),
    Column("profile", String(10), nullable=False),
    Column("seed", Integer, nullable=False),
    Column("data_start", Date),
    Column("data_end", Date),
    Column("stage_log", Text),
    Column("row_counts", Text),
)

metric_definitions = Table(
    "metric_definitions",
    metadata,
    Column("metric_key", String(40), primary_key=True),
    Column("metric_name", String(60), nullable=False),
    Column("category", String(20), nullable=False),
    Column("definition", Text, nullable=False),
    Column("formula", Text, nullable=False),
    Column("numerator", Text),
    Column("denominator", Text),
    Column("grain", String(60), nullable=False),
    Column("source_tables", String(200), nullable=False),
    Column("missing_values", Text),
    Column("limitations", Text),
    Column("interpretation", Text),
)

# ------------------------------------------------------------- curated marts
mart_employee_month = Table(
    "mart_employee_month",
    metadata,
    Column("snapshot_date", Date, nullable=False),
    Column("employee_id", String(10), nullable=False),
    Column("department_id", String(8), nullable=False),
    Column("business_unit", String(30), nullable=False),
    Column("location_id", String(8), nullable=False),
    Column("job_family", String(40), nullable=False),
    Column("job_level", String(4), nullable=False),
    Column("employment_type", String(16), nullable=False),
    Column("manager_id", String(10)),
    Column("active_flag", Integer, nullable=False),
    Column("tenure_months", Integer, nullable=False),
    Column("joined_in_month", Integer, nullable=False),
    Column("exited_in_month", Integer, nullable=False),
    Column("exit_type", String(16)),
    Column("transferred_in", Integer, nullable=False),
    Column("promoted_in_month", Integer, nullable=False),
    Column("monthly_base_cost", Float, nullable=False),
    Column("monthly_total_cost", Float, nullable=False),
    PrimaryKeyConstraint("snapshot_date", "employee_id", name="pk_mart_em"),
)

INDEXES = [
    Index("ix_emp_dept", employees.c.department_id),
    Index("ix_emp_join", employees.c.joining_date),
    Index("ix_jh_emp", job_history.c.employee_id, job_history.c.effective_start_date),
    Index("ix_mr_emp", manager_relationships.c.employee_id),
    Index("ix_mr_mgr", manager_relationships.c.manager_id),
    Index("ix_cand_req", candidates.c.job_requisition_id),
    Index("ix_cand_emp", candidates.c.associated_employee_id),
    Index("ix_re_cand", recruitment_events.c.candidate_id),
    Index("ix_re_date", recruitment_events.c.event_date),
    Index("ix_ee_emp", employee_events.c.employee_id, employee_events.c.event_date),
    Index("ix_ee_type_date", employee_events.c.event_type, employee_events.c.event_date),
    Index("ix_pr_emp", performance_reviews.c.employee_id),
    Index("ix_es_emp", engagement_surveys.c.employee_id, engagement_surveys.c.survey_date),
    Index("ix_ch_emp", compensation_history.c.employee_id, compensation_history.c.effective_start_date),
    Index("ix_tr_emp", training_records.c.employee_id),
    Index("ix_snap_emp", monthly_workforce_snapshots.c.employee_id),
    Index("ix_snap_dept", monthly_workforce_snapshots.c.department_id, monthly_workforce_snapshots.c.snapshot_date),
    Index("ix_budget_dept", workforce_budget.c.department_id, workforce_budget.c.planning_period),
    Index("ix_mart_dept", mart_employee_month.c.department_id, mart_employee_month.c.snapshot_date),
    Index("ix_mart_emp", mart_employee_month.c.employee_id),
]

# Source tables in dependency-friendly load order.
CORE_TABLES = [
    "locations", "departments", "employees", "job_history", "manager_relationships",
    "job_requisitions", "candidates", "recruitment_events", "employee_events",
    "performance_reviews", "engagement_surveys", "survey_feedback", "compensation_history",
    "training_records", "monthly_workforce_snapshots", "workforce_budget",
]

DATE_COLUMNS: dict[str, list[str]] = {
    t.name: [c.name for c in t.columns if isinstance(c.type, Date)] for t in metadata.sorted_tables
}
DATETIME_COLUMNS: dict[str, list[str]] = {
    t.name: [c.name for c in t.columns if isinstance(c.type, DateTime)] for t in metadata.sorted_tables
}

# Aggregate-only views granted to the Viewer role. They contain no employee IDs.
VIEWER_VIEWS: dict[str, str] = {
    "v_headcount_monthly": """
        SELECT m.snapshot_date, d.department_name, d.business_unit, l.city AS location,
               m.job_level, m.employment_type,
               SUM(m.active_flag) AS headcount_eom,
               SUM(m.joined_in_month) AS joiners,
               SUM(m.exited_in_month) AS exits,
               SUM(CASE WHEN m.exit_type = 'Voluntary' THEN 1 ELSE 0 END) AS voluntary_exits
        FROM mart_employee_month m
        JOIN departments d ON d.department_id = m.department_id
        JOIN locations l ON l.location_id = m.location_id
        GROUP BY m.snapshot_date, d.department_name, d.business_unit, l.city, m.job_level, m.employment_type
    """,
    "v_plan_vs_actual": """
        SELECT b.planning_period AS month, d.department_name, b.planned_headcount,
               a.headcount_eom AS actual_headcount, b.planned_people_cost,
               a.actual_cost
        FROM workforce_budget b
        JOIN departments d ON d.department_id = b.department_id
        LEFT JOIN (
            SELECT snapshot_date, department_id, SUM(active_flag) AS headcount_eom,
                   SUM(monthly_total_cost) AS actual_cost
            FROM mart_employee_month GROUP BY snapshot_date, department_id
        ) a ON a.snapshot_date = b.planning_period AND a.department_id = b.department_id
        WHERE b.scenario_name = 'AOP'
    """,
    "v_recruitment_funnel": """
        SELECT c.recruitment_source, c.job_family, c.furthest_stage, c.final_disposition,
               COUNT(*) AS candidates
        FROM candidates c
        GROUP BY c.recruitment_source, c.job_family, c.furthest_stage, c.final_disposition
    """,
    "v_engagement_department": """
        SELECT s.survey_period, d.department_name, COUNT(*) AS responses,
               AVG(s.engagement_score) AS avg_engagement,
               AVG(s.manager_support_score) AS avg_manager_support,
               AVG(s.career_growth_score) AS avg_career_growth
        FROM engagement_surveys s
        JOIN departments d ON d.department_id = s.department_id
        GROUP BY s.survey_period, d.department_name
        HAVING COUNT(*) >= 10
    """,
}
