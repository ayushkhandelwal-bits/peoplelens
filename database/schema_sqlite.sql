-- PeopleLens schema (sqlite). Generated from database/schema.py — do not edit by hand.

CREATE TABLE data_quality_results (
	result_id INTEGER NOT NULL, 
	run_id VARCHAR(20) NOT NULL, 
	check_name VARCHAR(160) NOT NULL, 
	category VARCHAR(60) NOT NULL, 
	table_name VARCHAR(60) NOT NULL, 
	severity VARCHAR(8) NOT NULL, 
	status VARCHAR(6) NOT NULL, 
	failed_rows INTEGER NOT NULL, 
	details TEXT, 
	checked_at DATETIME NOT NULL, 
	PRIMARY KEY (result_id)
);

CREATE TABLE locations (
	location_id VARCHAR(8) NOT NULL, 
	city VARCHAR(40) NOT NULL, 
	region VARCHAR(20) NOT NULL, 
	office_type VARCHAR(30) NOT NULL, 
	PRIMARY KEY (location_id), 
	UNIQUE (city)
);

CREATE TABLE mart_employee_month (
	snapshot_date DATE NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	department_id VARCHAR(8) NOT NULL, 
	business_unit VARCHAR(30) NOT NULL, 
	location_id VARCHAR(8) NOT NULL, 
	job_family VARCHAR(40) NOT NULL, 
	job_level VARCHAR(4) NOT NULL, 
	employment_type VARCHAR(16) NOT NULL, 
	manager_id VARCHAR(10), 
	active_flag INTEGER NOT NULL, 
	tenure_months INTEGER NOT NULL, 
	joined_in_month INTEGER NOT NULL, 
	exited_in_month INTEGER NOT NULL, 
	exit_type VARCHAR(16), 
	transferred_in INTEGER NOT NULL, 
	promoted_in_month INTEGER NOT NULL, 
	monthly_base_cost FLOAT NOT NULL, 
	monthly_total_cost FLOAT NOT NULL, 
	CONSTRAINT pk_mart_em PRIMARY KEY (snapshot_date, employee_id)
);

CREATE TABLE metric_definitions (
	metric_key VARCHAR(40) NOT NULL, 
	metric_name VARCHAR(60) NOT NULL, 
	category VARCHAR(20) NOT NULL, 
	definition TEXT NOT NULL, 
	formula TEXT NOT NULL, 
	numerator TEXT, 
	denominator TEXT, 
	grain VARCHAR(60) NOT NULL, 
	source_tables VARCHAR(200) NOT NULL, 
	missing_values TEXT, 
	limitations TEXT, 
	interpretation TEXT, 
	PRIMARY KEY (metric_key)
);

CREATE TABLE pipeline_runs (
	run_id VARCHAR(20) NOT NULL, 
	started_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	status VARCHAR(12) NOT NULL, 
	profile VARCHAR(10) NOT NULL, 
	seed INTEGER NOT NULL, 
	data_start DATE, 
	data_end DATE, 
	stage_log TEXT, 
	row_counts TEXT, 
	PRIMARY KEY (run_id)
);

CREATE TABLE departments (
	department_id VARCHAR(8) NOT NULL, 
	department_name VARCHAR(60) NOT NULL, 
	business_unit VARCHAR(30) NOT NULL, 
	primary_job_family VARCHAR(40) NOT NULL, 
	cost_centre VARCHAR(12) NOT NULL, 
	department_head_employee_id VARCHAR(10), 
	location_id VARCHAR(8) NOT NULL, 
	department_creation_date DATE NOT NULL, 
	department_status VARCHAR(12) NOT NULL, 
	PRIMARY KEY (department_id), 
	UNIQUE (department_name), 
	UNIQUE (cost_centre), 
	CONSTRAINT fk_department_head FOREIGN KEY(department_head_employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(location_id) REFERENCES locations (location_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE employees (
	employee_id VARCHAR(10) NOT NULL, 
	employee_name VARCHAR(80) NOT NULL, 
	gender VARCHAR(16) NOT NULL, 
	department_id VARCHAR(8) NOT NULL, 
	location_id VARCHAR(8) NOT NULL, 
	job_family VARCHAR(40) NOT NULL, 
	job_level VARCHAR(4) NOT NULL, 
	employment_type VARCHAR(16) NOT NULL, 
	joining_date DATE NOT NULL, 
	exit_date DATE, 
	exit_type VARCHAR(16), 
	current_status VARCHAR(10) NOT NULL, 
	current_manager_id VARCHAR(10), 
	experience_before_joining FLOAT NOT NULL, 
	source_system VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (employee_id), 
	CONSTRAINT ck_emp_exit_after_join CHECK (exit_date IS NULL OR exit_date > joining_date), 
	CONSTRAINT ck_emp_status CHECK (current_status IN ('Active','Exited')), 
	CONSTRAINT ck_emp_not_self_mgr CHECK (current_manager_id IS NULL OR current_manager_id <> employee_id), 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(location_id) REFERENCES locations (location_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(current_manager_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE survey_feedback (
	feedback_id INTEGER NOT NULL, 
	survey_date DATE NOT NULL, 
	survey_period VARCHAR(8) NOT NULL, 
	department_id VARCHAR(8) NOT NULL, 
	tenure_band VARCHAR(12) NOT NULL, 
	feedback_text TEXT NOT NULL, 
	PRIMARY KEY (feedback_id), 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE workforce_budget (
	budget_id INTEGER NOT NULL, 
	planning_period DATE NOT NULL, 
	fiscal_year VARCHAR(6) NOT NULL, 
	department_id VARCHAR(8) NOT NULL, 
	planned_headcount INTEGER NOT NULL, 
	planned_hires INTEGER NOT NULL, 
	planned_exit_rate FLOAT NOT NULL, 
	planned_people_cost FLOAT NOT NULL, 
	approved_budget_version VARCHAR(8) NOT NULL, 
	scenario_name VARCHAR(20) NOT NULL, 
	PRIMARY KEY (budget_id), 
	CONSTRAINT uq_budget UNIQUE (planning_period, department_id, scenario_name), 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE compensation_history (
	compensation_record_id INTEGER NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	effective_start_date DATE NOT NULL, 
	effective_end_date DATE, 
	annual_base_salary FLOAT NOT NULL, 
	variable_compensation FLOAT NOT NULL, 
	benefits_cost FLOAT NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	compensation_change_reason VARCHAR(80) NOT NULL, 
	PRIMARY KEY (compensation_record_id), 
	CONSTRAINT ck_ch_base CHECK (annual_base_salary > 0), 
	CONSTRAINT ck_ch_nonneg CHECK (variable_compensation >= 0 AND benefits_cost >= 0), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE employee_events (
	employee_event_id INTEGER NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	event_type VARCHAR(16) NOT NULL, 
	event_date DATE NOT NULL, 
	previous_department_id VARCHAR(8), 
	new_department_id VARCHAR(8), 
	previous_job_level VARCHAR(4), 
	new_job_level VARCHAR(4), 
	previous_manager_id VARCHAR(10), 
	new_manager_id VARCHAR(10), 
	exit_type VARCHAR(16), 
	exit_reason_category VARCHAR(40), 
	PRIMARY KEY (employee_event_id), 
	CONSTRAINT ck_ee_type CHECK (event_type IN ('Hire','Exit','Promotion','Transfer','Manager Change')), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(previous_department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(new_department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(previous_manager_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(new_manager_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE engagement_surveys (
	survey_response_id INTEGER NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	survey_date DATE NOT NULL, 
	survey_period VARCHAR(8) NOT NULL, 
	department_id VARCHAR(8) NOT NULL, 
	engagement_score FLOAT NOT NULL, 
	job_satisfaction_score FLOAT NOT NULL, 
	manager_support_score FLOAT NOT NULL, 
	work_life_balance_score FLOAT NOT NULL, 
	career_growth_score FLOAT NOT NULL, 
	enps_response INTEGER NOT NULL, 
	PRIMARY KEY (survey_response_id), 
	CONSTRAINT ck_es_range CHECK (engagement_score BETWEEN 1 AND 5), 
	CONSTRAINT ck_es_enps CHECK (enps_response BETWEEN 0 AND 10), 
	CONSTRAINT uq_es_emp_period UNIQUE (employee_id, survey_period), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE job_history (
	job_history_id INTEGER NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	effective_start_date DATE NOT NULL, 
	effective_end_date DATE, 
	department_id VARCHAR(8) NOT NULL, 
	location_id VARCHAR(8) NOT NULL, 
	job_family VARCHAR(40) NOT NULL, 
	job_level VARCHAR(4) NOT NULL, 
	manager_id VARCHAR(10), 
	employment_type VARCHAR(16) NOT NULL, 
	change_reason VARCHAR(80) NOT NULL, 
	promotion_flag INTEGER NOT NULL, 
	transfer_flag INTEGER NOT NULL, 
	PRIMARY KEY (job_history_id), 
	CONSTRAINT ck_jh_dates CHECK (effective_end_date IS NULL OR effective_end_date >= effective_start_date), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(location_id) REFERENCES locations (location_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(manager_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE job_requisitions (
	job_requisition_id VARCHAR(10) NOT NULL, 
	department_id VARCHAR(8) NOT NULL, 
	location_id VARCHAR(8) NOT NULL, 
	job_family VARCHAR(40) NOT NULL, 
	job_level VARCHAR(4) NOT NULL, 
	requisition_type VARCHAR(12) NOT NULL, 
	opened_date DATE NOT NULL, 
	requisition_status VARCHAR(12) NOT NULL, 
	offer_accepted_date DATE, 
	closed_date DATE, 
	hired_employee_id VARCHAR(10), 
	hiring_manager_id VARCHAR(10), 
	recruiter_id VARCHAR(10), 
	recruitment_cost FLOAT, 
	PRIMARY KEY (job_requisition_id), 
	CONSTRAINT ck_req_status CHECK (requisition_status IN ('Open','Filled','Cancelled')), 
	CONSTRAINT ck_req_cost CHECK (recruitment_cost IS NULL OR recruitment_cost >= 0), 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(location_id) REFERENCES locations (location_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(hired_employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(hiring_manager_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(recruiter_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE manager_relationships (
	relationship_id INTEGER NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	manager_id VARCHAR(10) NOT NULL, 
	effective_start_date DATE NOT NULL, 
	effective_end_date DATE, 
	department_id VARCHAR(8) NOT NULL, 
	PRIMARY KEY (relationship_id), 
	CONSTRAINT ck_mr_not_self CHECK (employee_id <> manager_id), 
	CONSTRAINT ck_mr_dates CHECK (effective_end_date IS NULL OR effective_end_date >= effective_start_date), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(manager_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE monthly_workforce_snapshots (
	snapshot_date DATE NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	department_id VARCHAR(8) NOT NULL, 
	location_id VARCHAR(8) NOT NULL, 
	job_family VARCHAR(40) NOT NULL, 
	job_level VARCHAR(4) NOT NULL, 
	manager_id VARCHAR(10), 
	active_flag INTEGER NOT NULL, 
	tenure_months INTEGER NOT NULL, 
	employment_type VARCHAR(16) NOT NULL, 
	CONSTRAINT pk_snapshot PRIMARY KEY (snapshot_date, employee_id), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(department_id) REFERENCES departments (department_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(location_id) REFERENCES locations (location_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(manager_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE performance_reviews (
	review_id INTEGER NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	review_cycle VARCHAR(8) NOT NULL, 
	review_period_start DATE NOT NULL, 
	review_period_end DATE NOT NULL, 
	review_date DATE NOT NULL, 
	performance_rating INTEGER, 
	goal_achievement_score FLOAT, 
	training_hours FLOAT NOT NULL, 
	reviewer_employee_id VARCHAR(10), 
	review_completion_status VARCHAR(16) NOT NULL, 
	PRIMARY KEY (review_id), 
	CONSTRAINT ck_pr_rating CHECK (performance_rating IS NULL OR performance_rating BETWEEN 1 AND 5), 
	CONSTRAINT uq_pr_emp_cycle UNIQUE (employee_id, review_cycle), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(reviewer_employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE training_records (
	training_record_id INTEGER NOT NULL, 
	employee_id VARCHAR(10) NOT NULL, 
	training_program VARCHAR(60) NOT NULL, 
	training_category VARCHAR(20) NOT NULL, 
	enrollment_date DATE NOT NULL, 
	completion_date DATE, 
	training_hours FLOAT NOT NULL, 
	training_cost FLOAT NOT NULL, 
	completion_status VARCHAR(16) NOT NULL, 
	assessment_score FLOAT, 
	PRIMARY KEY (training_record_id), 
	CONSTRAINT ck_tr_nonneg CHECK (training_cost >= 0 AND training_hours >= 0), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE candidates (
	candidate_id VARCHAR(10) NOT NULL, 
	application_date DATE NOT NULL, 
	job_requisition_id VARCHAR(10) NOT NULL, 
	recruitment_source VARCHAR(24) NOT NULL, 
	job_family VARCHAR(40) NOT NULL, 
	location_id VARCHAR(8) NOT NULL, 
	experience_band VARCHAR(12) NOT NULL, 
	furthest_stage VARCHAR(16) NOT NULL, 
	candidate_status VARCHAR(8) NOT NULL, 
	final_disposition VARCHAR(24) NOT NULL, 
	associated_employee_id VARCHAR(10), 
	PRIMARY KEY (candidate_id), 
	CONSTRAINT ck_cand_status CHECK (candidate_status IN ('Open','Closed')), 
	FOREIGN KEY(job_requisition_id) REFERENCES job_requisitions (job_requisition_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(location_id) REFERENCES locations (location_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(associated_employee_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE recruitment_events (
	recruitment_event_id INTEGER NOT NULL, 
	candidate_id VARCHAR(10) NOT NULL, 
	job_requisition_id VARCHAR(10) NOT NULL, 
	event_type VARCHAR(16) NOT NULL, 
	event_date DATE NOT NULL, 
	event_status VARCHAR(16) NOT NULL, 
	recruiter_id VARCHAR(10), 
	recruitment_source VARCHAR(24) NOT NULL, 
	rejection_reason VARCHAR(40), 
	offered_compensation FLOAT, 
	accepted_flag INTEGER, 
	PRIMARY KEY (recruitment_event_id), 
	CONSTRAINT uq_rec_event_stage UNIQUE (candidate_id, event_type), 
	FOREIGN KEY(candidate_id) REFERENCES candidates (candidate_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(job_requisition_id) REFERENCES job_requisitions (job_requisition_id) DEFERRABLE INITIALLY DEFERRED, 
	FOREIGN KEY(recruiter_id) REFERENCES employees (employee_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX ix_emp_dept ON employees (department_id);

CREATE INDEX ix_emp_join ON employees (joining_date);

CREATE INDEX ix_jh_emp ON job_history (employee_id, effective_start_date);

CREATE INDEX ix_mr_emp ON manager_relationships (employee_id);

CREATE INDEX ix_mr_mgr ON manager_relationships (manager_id);

CREATE INDEX ix_cand_req ON candidates (job_requisition_id);

CREATE INDEX ix_cand_emp ON candidates (associated_employee_id);

CREATE INDEX ix_re_cand ON recruitment_events (candidate_id);

CREATE INDEX ix_re_date ON recruitment_events (event_date);

CREATE INDEX ix_ee_emp ON employee_events (employee_id, event_date);

CREATE INDEX ix_ee_type_date ON employee_events (event_type, event_date);

CREATE INDEX ix_pr_emp ON performance_reviews (employee_id);

CREATE INDEX ix_es_emp ON engagement_surveys (employee_id, survey_date);

CREATE INDEX ix_ch_emp ON compensation_history (employee_id, effective_start_date);

CREATE INDEX ix_tr_emp ON training_records (employee_id);

CREATE INDEX ix_snap_emp ON monthly_workforce_snapshots (employee_id);

CREATE INDEX ix_snap_dept ON monthly_workforce_snapshots (department_id, snapshot_date);

CREATE INDEX ix_budget_dept ON workforce_budget (department_id, planning_period);

CREATE INDEX ix_mart_dept ON mart_employee_month (department_id, snapshot_date);

CREATE INDEX ix_mart_emp ON mart_employee_month (employee_id);

CREATE VIEW v_headcount_monthly AS
    SELECT m.snapshot_date, d.department_name, d.business_unit, l.city AS location,
    m.job_level, m.employment_type,
    SUM(m.active_flag) AS headcount_eom,
    SUM(m.joined_in_month) AS joiners,
    SUM(m.exited_in_month) AS exits,
    SUM(CASE WHEN m.exit_type = 'Voluntary' THEN 1 ELSE 0 END) AS voluntary_exits
    FROM mart_employee_month m
    JOIN departments d ON d.department_id = m.department_id
    JOIN locations l ON l.location_id = m.location_id
    GROUP BY m.snapshot_date, d.department_name, d.business_unit, l.city, m.job_level, m.employment_type;

CREATE VIEW v_plan_vs_actual AS
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
    WHERE b.scenario_name = 'AOP';

CREATE VIEW v_recruitment_funnel AS
    SELECT c.recruitment_source, c.job_family, c.furthest_stage, c.final_disposition,
    COUNT(*) AS candidates
    FROM candidates c
    GROUP BY c.recruitment_source, c.job_family, c.furthest_stage, c.final_disposition;

CREATE VIEW v_engagement_department AS
    SELECT s.survey_period, d.department_name, COUNT(*) AS responses,
    AVG(s.engagement_score) AS avg_engagement,
    AVG(s.manager_support_score) AS avg_manager_support,
    AVG(s.career_growth_score) AS avg_career_growth
    FROM engagement_surveys s
    JOIN departments d ON d.department_id = s.department_id
    GROUP BY s.survey_period, d.department_name
    HAVING COUNT(*) >= 10;
