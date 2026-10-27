# Data dictionary and simulation assumptions

> All data is synthetic. "Aster Group" is fictional. Names are randomly combined from common first and last names
> and appear only in `employees.employee_name`; the application shows synthetic IDs only.

## Conventions

* **Dates** are `DATE`. Effective-dated rows run from `effective_start_date` to `effective_end_date` inclusive
  (`NULL` = current). Rows for one employee are contiguous and never overlap (quality rules).
* **Separation date:** `exit_date` is the first day the person is no longer employed. An employee is active on day
  *d* when `joining_date <= d < exit_date`.
* **Snapshot convention:** one row per employee per calendar month in which they were employed at least one day;
  `snapshot_date` is the month end; `active_flag = 1` when still employed at month end. Leavers appear in their exit
  month with `active_flag = 0` and the attributes of their last working day. Hence
  `headcount(m) = headcount(m-1) + joiners(m) - exits(m)`, verified for every month and department.
* **HRMS go-live:** reporting lines are captured from the start of the window; earlier job-history rows carry a
  NULL manager and an "HRMS Migration" row starts on go-live.
* **Currency:** fictional INR. Total employer cost = base + variable pay at target + benefits.
* **Rating scale:** 1 Unsatisfactory · 2 Needs improvement · 3 Meets expectations · 4 Exceeds · 5 Outstanding.
* **Survey scale:** 1–5 Likert indices; eNPS 0–10 (promoters 9–10, passives 7–8, detractors 0–6).

## Simulation assumptions (documented, not empirical claims)

| Area | Assumption |
|---|---|
| Organisation | 4 business units, 24 departments + Executive Office; level mix differs for frontline, technology and corporate teams |
| Reporting lines | Manager is always strictly more senior; nearest senior level in the department, load-balanced; heads report to the BU head, BU heads to the CEO |
| Pay | Band midpoint = level × family × location factor; bands inflate 6%/yr (9.5% for engineering/data); new hires paid ≈ market, tenured staff drift below (compression); April merit by rating (0–13%); promotions +10–18% |
| Voluntary exits | Monthly hazard ∝ department factor × tenure curve (high in first 3 months) × exp(−0.6 × latent engagement) × pay compression × promotion recency × recent manager change × level × contract × source of hire (first year) × calendar seasonality (peak Apr–May) × event shocks; calibrated to 15.5%/yr at the start |
| Involuntary exits | Mainly low ratings (1–2) and non-renewal of contracts at anniversaries (~30%) |
| Deliberate nulls | Location, gender and training participation have **no** direct effect on attrition |
| Engagement | Latent trait + AR(1) state + manager quality + department climate + promotion/manager-change/pay effects; survey scores are noisy proxies; disengaged staff respond less often |
| Performance | Ability + engagement + small effect of ≥16 development hours (+0.15) + noise |
| Training | Annual compliance course for all; ~3.5%/month chance of a development programme; leadership programmes favour high performers (selection bias) |
| Recruitment | Requisitions open for gaps against each department's growth trajectory; applicants arrive over time; source-specific stage pass rates; sourcing pauses while an offer is outstanding; 4% of requisitions cancelled; ~10% of accepted offers do not join (no-show) |
| Plan (AOP) | Prepared each April from the opening headcount; planned growth drawn independently of realised growth; 7% merit budget |
| Events | Service Operations West reorganisation (Jul 2025: 45% manager changes, engagement shock, higher hazard for 9 months); Customer Support climate decline from Oct 2025; Commercial hiring slowdown Jan–Jun 2026 |
| Export noise | 0.5% status case/whitespace issues, 0.4% employment-type issues, 0.2%/0.1% duplicate recruitment/employee events, 0.3% DD/MM/YYYY survey dates, 0.05% negative training costs (quarantined) |

## Configuration

`config/settings.py → GenerationConfig`: `seed`, `start_date`, `months`, `initial_headcount`, `n_departments`,
`locations`, `annual_voluntary_attrition`, `annual_involuntary_attrition`, `annual_growth`, `plan_growth`,
`hiring_volume_multiplier`, `survey_frequency_months`, `survey_response_rate`, `feedback_comment_rate`,
`performance_review_frequency_months` (12 or 6), `forecast_plan_months`. Profiles: `full` (5,400 starting employees,
36 months) and `dev` (700, 24 months).

# Data dictionary

Generated from `database/schema.py`; row counts from the default full-profile build (seed 42).

## Tables

| Table | Rows | Purpose |
|---|---:|---|
| `data_quality_results` | 138 | Latest quality-rule results. |
| `locations` | 7 | Office locations (7 Indian cities). |
| `mart_employee_month` | 204,239 | Curated mart: snapshot + joined/exited/transfer/promotion flags + pro-rated monthly cost. |
| `metric_definitions` | 35 | The metric dictionary. |
| `pipeline_runs` | 1 | Pipeline run log with stage timings and row counts. |
| `departments` | 25 | Organisational units, business unit, cost centre, current head. |
| `employees` | 9,336 | Employee master (current state). exit_date/exit_type are denormalised from employee_events and reconciled by a quality rule. |
| `survey_feedback` | 12,683 | Free-text comments WITHOUT employee ID (department, wave, tenure band only). |
| `workforce_budget` | 1,050 | Annual operating plan (AOP) by department and month, including 6 forward months. |
| `compensation_history` | 25,147 | Effective-dated pay: annual base, variable target, benefits (fictional INR). |
| `employee_events` | 20,199 | Lifecycle events: Hire, Exit, Promotion, Transfer, Manager Change (with before/after values). |
| `engagement_surveys` | 37,896 | Quarterly pulse responses (1–5 scores, eNPS 0–10) with department at survey time. |
| `job_history` | 21,987 | Effective-dated role history: department, location, family, level, manager, employment type. |
| `job_requisitions` | 4,651 | Approved vacancies: opening, offer acceptance, closure, hired employee, simulated recruitment cost. |
| `manager_relationships` | 13,706 | Effective-dated reporting lines derived from job history (captured from the HRMS go-live = window start). |
| `monthly_workforce_snapshots` | 204,239 | One row per employee per month employed; active_flag = employed at month end. |
| `performance_reviews` | 20,355 | Annual April review for the previous April–March period (rating 1–5, goal score, training hours). |
| `training_records` | 23,665 | Programme enrolments, completion, hours, cost, assessment score. |
| `candidates` | 43,862 | One row per application to a requisition: source, furthest stage reached, final disposition. |
| `recruitment_events` | 105,811 | Funnel events (Application, Screening, Interview, Offer, Offer Accepted, Joining) with outcomes. |

### `data_quality_results`

Latest quality-rule results.

| Column | Type | Null | Key |
|---|---|---|---|
| result_id | INTEGER | no | PK |
| run_id | VARCHAR(20) | no |  |
| check_name | VARCHAR(160) | no |  |
| category | VARCHAR(60) | no |  |
| table_name | VARCHAR(60) | no |  |
| severity | VARCHAR(8) | no |  |
| status | VARCHAR(6) | no |  |
| failed_rows | INTEGER | no |  |
| details | TEXT | yes |  |
| checked_at | DATETIME | no |  |

### `locations`

Office locations (7 Indian cities).

| Column | Type | Null | Key |
|---|---|---|---|
| location_id | VARCHAR(8) | no | PK |
| city | VARCHAR(40) | no |  |
| region | VARCHAR(20) | no |  |
| office_type | VARCHAR(30) | no |  |

### `mart_employee_month`

Curated mart: snapshot + joined/exited/transfer/promotion flags + pro-rated monthly cost.

| Column | Type | Null | Key |
|---|---|---|---|
| snapshot_date | DATE | no | PK |
| employee_id | VARCHAR(10) | no | PK |
| department_id | VARCHAR(8) | no |  |
| business_unit | VARCHAR(30) | no |  |
| location_id | VARCHAR(8) | no |  |
| job_family | VARCHAR(40) | no |  |
| job_level | VARCHAR(4) | no |  |
| employment_type | VARCHAR(16) | no |  |
| manager_id | VARCHAR(10) | yes |  |
| active_flag | INTEGER | no |  |
| tenure_months | INTEGER | no |  |
| joined_in_month | INTEGER | no |  |
| exited_in_month | INTEGER | no |  |
| exit_type | VARCHAR(16) | yes |  |
| transferred_in | INTEGER | no |  |
| promoted_in_month | INTEGER | no |  |
| monthly_base_cost | FLOAT | no |  |
| monthly_total_cost | FLOAT | no |  |

### `metric_definitions`

The metric dictionary.

| Column | Type | Null | Key |
|---|---|---|---|
| metric_key | VARCHAR(40) | no | PK |
| metric_name | VARCHAR(60) | no |  |
| category | VARCHAR(20) | no |  |
| definition | TEXT | no |  |
| formula | TEXT | no |  |
| numerator | TEXT | yes |  |
| denominator | TEXT | yes |  |
| grain | VARCHAR(60) | no |  |
| source_tables | VARCHAR(200) | no |  |
| missing_values | TEXT | yes |  |
| limitations | TEXT | yes |  |
| interpretation | TEXT | yes |  |

### `pipeline_runs`

Pipeline run log with stage timings and row counts.

| Column | Type | Null | Key |
|---|---|---|---|
| run_id | VARCHAR(20) | no | PK |
| started_at | DATETIME | no |  |
| finished_at | DATETIME | yes |  |
| status | VARCHAR(12) | no |  |
| profile | VARCHAR(10) | no |  |
| seed | INTEGER | no |  |
| data_start | DATE | yes |  |
| data_end | DATE | yes |  |
| stage_log | TEXT | yes |  |
| row_counts | TEXT | yes |  |

### `departments`

Organisational units, business unit, cost centre, current head.

| Column | Type | Null | Key |
|---|---|---|---|
| department_id | VARCHAR(8) | no | PK |
| department_name | VARCHAR(60) | no |  |
| business_unit | VARCHAR(30) | no |  |
| primary_job_family | VARCHAR(40) | no |  |
| cost_centre | VARCHAR(12) | no |  |
| department_head_employee_id | VARCHAR(10) | yes | FK → employees.employee_id |
| location_id | VARCHAR(8) | no | FK → locations.location_id |
| department_creation_date | DATE | no |  |
| department_status | VARCHAR(12) | no |  |

### `employees`

Employee master (current state). exit_date/exit_type are denormalised from employee_events and reconciled by a quality rule.

| Column | Type | Null | Key |
|---|---|---|---|
| employee_id | VARCHAR(10) | no | PK |
| employee_name | VARCHAR(80) | no |  |
| gender | VARCHAR(16) | no |  |
| department_id | VARCHAR(8) | no | FK → departments.department_id |
| location_id | VARCHAR(8) | no | FK → locations.location_id |
| job_family | VARCHAR(40) | no |  |
| job_level | VARCHAR(4) | no |  |
| employment_type | VARCHAR(16) | no |  |
| joining_date | DATE | no |  |
| exit_date | DATE | yes |  |
| exit_type | VARCHAR(16) | yes |  |
| current_status | VARCHAR(10) | no |  |
| current_manager_id | VARCHAR(10) | yes | FK → employees.employee_id |
| experience_before_joining | FLOAT | no |  |
| source_system | VARCHAR(20) | no |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

### `survey_feedback`

Free-text comments WITHOUT employee ID (department, wave, tenure band only).

| Column | Type | Null | Key |
|---|---|---|---|
| feedback_id | INTEGER | no | PK |
| survey_date | DATE | no |  |
| survey_period | VARCHAR(8) | no |  |
| department_id | VARCHAR(8) | no | FK → departments.department_id |
| tenure_band | VARCHAR(12) | no |  |
| feedback_text | TEXT | no |  |

### `workforce_budget`

Annual operating plan (AOP) by department and month, including 6 forward months.

| Column | Type | Null | Key |
|---|---|---|---|
| budget_id | INTEGER | no | PK |
| planning_period | DATE | no |  |
| fiscal_year | VARCHAR(6) | no |  |
| department_id | VARCHAR(8) | no | FK → departments.department_id |
| planned_headcount | INTEGER | no |  |
| planned_hires | INTEGER | no |  |
| planned_exit_rate | FLOAT | no |  |
| planned_people_cost | FLOAT | no |  |
| approved_budget_version | VARCHAR(8) | no |  |
| scenario_name | VARCHAR(20) | no |  |

### `compensation_history`

Effective-dated pay: annual base, variable target, benefits (fictional INR).

| Column | Type | Null | Key |
|---|---|---|---|
| compensation_record_id | INTEGER | no | PK |
| employee_id | VARCHAR(10) | no | FK → employees.employee_id |
| effective_start_date | DATE | no |  |
| effective_end_date | DATE | yes |  |
| annual_base_salary | FLOAT | no |  |
| variable_compensation | FLOAT | no |  |
| benefits_cost | FLOAT | no |  |
| currency | VARCHAR(3) | no |  |
| compensation_change_reason | VARCHAR(80) | no |  |

### `employee_events`

Lifecycle events: Hire, Exit, Promotion, Transfer, Manager Change (with before/after values).

| Column | Type | Null | Key |
|---|---|---|---|
| employee_event_id | INTEGER | no | PK |
| employee_id | VARCHAR(10) | no | FK → employees.employee_id |
| event_type | VARCHAR(16) | no |  |
| event_date | DATE | no |  |
| previous_department_id | VARCHAR(8) | yes | FK → departments.department_id |
| new_department_id | VARCHAR(8) | yes | FK → departments.department_id |
| previous_job_level | VARCHAR(4) | yes |  |
| new_job_level | VARCHAR(4) | yes |  |
| previous_manager_id | VARCHAR(10) | yes | FK → employees.employee_id |
| new_manager_id | VARCHAR(10) | yes | FK → employees.employee_id |
| exit_type | VARCHAR(16) | yes |  |
| exit_reason_category | VARCHAR(40) | yes |  |

### `engagement_surveys`

Quarterly pulse responses (1–5 scores, eNPS 0–10) with department at survey time.

| Column | Type | Null | Key |
|---|---|---|---|
| survey_response_id | INTEGER | no | PK |
| employee_id | VARCHAR(10) | no | FK → employees.employee_id |
| survey_date | DATE | no |  |
| survey_period | VARCHAR(8) | no |  |
| department_id | VARCHAR(8) | no | FK → departments.department_id |
| engagement_score | FLOAT | no |  |
| job_satisfaction_score | FLOAT | no |  |
| manager_support_score | FLOAT | no |  |
| work_life_balance_score | FLOAT | no |  |
| career_growth_score | FLOAT | no |  |
| enps_response | INTEGER | no |  |

### `job_history`

Effective-dated role history: department, location, family, level, manager, employment type.

| Column | Type | Null | Key |
|---|---|---|---|
| job_history_id | INTEGER | no | PK |
| employee_id | VARCHAR(10) | no | FK → employees.employee_id |
| effective_start_date | DATE | no |  |
| effective_end_date | DATE | yes |  |
| department_id | VARCHAR(8) | no | FK → departments.department_id |
| location_id | VARCHAR(8) | no | FK → locations.location_id |
| job_family | VARCHAR(40) | no |  |
| job_level | VARCHAR(4) | no |  |
| manager_id | VARCHAR(10) | yes | FK → employees.employee_id |
| employment_type | VARCHAR(16) | no |  |
| change_reason | VARCHAR(80) | no |  |
| promotion_flag | INTEGER | no |  |
| transfer_flag | INTEGER | no |  |

### `job_requisitions`

Approved vacancies: opening, offer acceptance, closure, hired employee, simulated recruitment cost.

| Column | Type | Null | Key |
|---|---|---|---|
| job_requisition_id | VARCHAR(10) | no | PK |
| department_id | VARCHAR(8) | no | FK → departments.department_id |
| location_id | VARCHAR(8) | no | FK → locations.location_id |
| job_family | VARCHAR(40) | no |  |
| job_level | VARCHAR(4) | no |  |
| requisition_type | VARCHAR(12) | no |  |
| opened_date | DATE | no |  |
| requisition_status | VARCHAR(12) | no |  |
| offer_accepted_date | DATE | yes |  |
| closed_date | DATE | yes |  |
| hired_employee_id | VARCHAR(10) | yes | FK → employees.employee_id |
| hiring_manager_id | VARCHAR(10) | yes | FK → employees.employee_id |
| recruiter_id | VARCHAR(10) | yes | FK → employees.employee_id |
| recruitment_cost | FLOAT | yes |  |

### `manager_relationships`

Effective-dated reporting lines derived from job history (captured from the HRMS go-live = window start).

| Column | Type | Null | Key |
|---|---|---|---|
| relationship_id | INTEGER | no | PK |
| employee_id | VARCHAR(10) | no | FK → employees.employee_id |
| manager_id | VARCHAR(10) | no | FK → employees.employee_id |
| effective_start_date | DATE | no |  |
| effective_end_date | DATE | yes |  |
| department_id | VARCHAR(8) | no | FK → departments.department_id |

### `monthly_workforce_snapshots`

One row per employee per month employed; active_flag = employed at month end.

| Column | Type | Null | Key |
|---|---|---|---|
| snapshot_date | DATE | no | PK |
| employee_id | VARCHAR(10) | no | PK |
| department_id | VARCHAR(8) | no | FK → departments.department_id |
| location_id | VARCHAR(8) | no | FK → locations.location_id |
| job_family | VARCHAR(40) | no |  |
| job_level | VARCHAR(4) | no |  |
| manager_id | VARCHAR(10) | yes | FK → employees.employee_id |
| active_flag | INTEGER | no |  |
| tenure_months | INTEGER | no |  |
| employment_type | VARCHAR(16) | no |  |

### `performance_reviews`

Annual April review for the previous April–March period (rating 1–5, goal score, training hours).

| Column | Type | Null | Key |
|---|---|---|---|
| review_id | INTEGER | no | PK |
| employee_id | VARCHAR(10) | no | FK → employees.employee_id |
| review_cycle | VARCHAR(8) | no |  |
| review_period_start | DATE | no |  |
| review_period_end | DATE | no |  |
| review_date | DATE | no |  |
| performance_rating | INTEGER | yes |  |
| goal_achievement_score | FLOAT | yes |  |
| training_hours | FLOAT | no |  |
| reviewer_employee_id | VARCHAR(10) | yes | FK → employees.employee_id |
| review_completion_status | VARCHAR(16) | no |  |

### `training_records`

Programme enrolments, completion, hours, cost, assessment score.

| Column | Type | Null | Key |
|---|---|---|---|
| training_record_id | INTEGER | no | PK |
| employee_id | VARCHAR(10) | no | FK → employees.employee_id |
| training_program | VARCHAR(60) | no |  |
| training_category | VARCHAR(20) | no |  |
| enrollment_date | DATE | no |  |
| completion_date | DATE | yes |  |
| training_hours | FLOAT | no |  |
| training_cost | FLOAT | no |  |
| completion_status | VARCHAR(16) | no |  |
| assessment_score | FLOAT | yes |  |

### `candidates`

One row per application to a requisition: source, furthest stage reached, final disposition.

| Column | Type | Null | Key |
|---|---|---|---|
| candidate_id | VARCHAR(10) | no | PK |
| application_date | DATE | no |  |
| job_requisition_id | VARCHAR(10) | no | FK → job_requisitions.job_requisition_id |
| recruitment_source | VARCHAR(24) | no |  |
| job_family | VARCHAR(40) | no |  |
| location_id | VARCHAR(8) | no | FK → locations.location_id |
| experience_band | VARCHAR(12) | no |  |
| furthest_stage | VARCHAR(16) | no |  |
| candidate_status | VARCHAR(8) | no |  |
| final_disposition | VARCHAR(24) | no |  |
| associated_employee_id | VARCHAR(10) | yes | FK → employees.employee_id |

### `recruitment_events`

Funnel events (Application, Screening, Interview, Offer, Offer Accepted, Joining) with outcomes.

| Column | Type | Null | Key |
|---|---|---|---|
| recruitment_event_id | INTEGER | no | PK |
| candidate_id | VARCHAR(10) | no | FK → candidates.candidate_id |
| job_requisition_id | VARCHAR(10) | no | FK → job_requisitions.job_requisition_id |
| event_type | VARCHAR(16) | no |  |
| event_date | DATE | no |  |
| event_status | VARCHAR(16) | no |  |
| recruiter_id | VARCHAR(10) | yes | FK → employees.employee_id |
| recruitment_source | VARCHAR(24) | no |  |
| rejection_reason | VARCHAR(40) | yes |  |
| offered_compensation | FLOAT | yes |  |
| accepted_flag | INTEGER | yes |  |
