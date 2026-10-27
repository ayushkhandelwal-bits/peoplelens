# Metric definitions

Generated from `src/governance/metric_dictionary.py` — the single source of truth used by the application, reports and Ask PeopleLens.

## Attrition

### Attrition rate (annualised) (`attrition_rate`)

**Definition.** Leavers in the period relative to the average workforce, annualised.

- **Formula:** `exits in period / average headcount x (12 / months in period)`
- **Numerator:** Exits in period
- **Denominator:** Average month-end headcount in period
- **Grain:** Period; any organisational filter
- **Source tables:** monthly_workforce_snapshots, employees
- **Missing values:** Shown as — when average headcount is zero.
- **Limitations:** Annualising short periods amplifies seasonality (April-May exits are higher). Uses month-end points for the denominator. Dividing by *current* headcount instead would overstate attrition in shrinking teams and understate it in growing teams.
- **Interpretation:** Share of the workforce lost per year at the current pace.

### Voluntary attrition rate (`voluntary_attrition_rate`)

**Definition.** Resignations (employee-initiated exits) relative to average headcount, annualised.

- **Formula:** `voluntary exits / average headcount x (12 / months)`
- **Numerator:** Voluntary exits
- **Denominator:** Average headcount
- **Grain:** Period
- **Source tables:** employees.exit_type
- **Missing values:** None.
- **Limitations:** Exit type is recorded at separation and may be misclassified.
- **Interpretation:** Regretted, controllable turnover — the main retention lever.

### Involuntary attrition rate (`involuntary_attrition_rate`)

**Definition.** Employer-initiated exits (performance, conduct, restructuring, contract end) relative to average headcount, annualised.

- **Formula:** `involuntary exits / average headcount x (12 / months)`
- **Numerator:** Involuntary exits
- **Denominator:** Average headcount
- **Grain:** Period
- **Source tables:** employees.exit_type
- **Missing values:** None.
- **Limitations:** Includes non-renewal of fixed-term contracts.
- **Interpretation:** Workforce actions initiated by the organisation.

### Early attrition (30/60/90 days) (`early_attrition`)

**Definition.** Share of a joining cohort that left within N days of joining, among joiners observed for at least N days.

- **Formula:** `leavers with tenure < N days / joiners with >= N days of observation`
- **Numerator:** Joiners who left within N days
- **Denominator:** Joiners whose N-day window is complete
- **Grain:** Joining cohort
- **Source tables:** employees
- **Missing values:** Joiners with an incomplete observation window are excluded (right-censoring).
- **Limitations:** Small cohorts produce wide confidence intervals.
- **Interpretation:** Onboarding and hiring-quality signal.

### Cohort retention (`cohort_retention`)

**Definition.** Share of a joining cohort still employed N months after joining.

- **Formula:** `1 - leavers within N months / eligible joiners`
- **Numerator:** Retained joiners
- **Denominator:** Joiners with >= N months observed
- **Grain:** Joining month or quarter
- **Source tables:** employees
- **Missing values:** Censored joiners excluded from the denominator.
- **Limitations:** Recent cohorts have fewer observable horizons.
- **Interpretation:** Durability of hiring.

### High-performer attrition (`high_performer_attrition`)

**Definition.** Annualised voluntary attrition among employees whose latest completed rating before leaving (or the period end) was 4 or 5.

- **Formula:** `voluntary exits of high performers / average high performers x 12/months`
- **Numerator:** High-performer voluntary exits
- **Denominator:** Average number of active high performers
- **Grain:** Period
- **Source tables:** performance_reviews, employees
- **Missing values:** Employees without a completed review are excluded.
- **Limitations:** Ratings are calibrated annually; new joiners have no rating.
- **Interpretation:** Loss of critical talent.

## Development

### Training participation (`training_participation`)

**Definition.** Share of average headcount enrolled in at least one non-compliance programme in the period.

- **Formula:** `distinct employees enrolled / average headcount`
- **Numerator:** Distinct learners
- **Denominator:** Average headcount
- **Grain:** Period
- **Source tables:** training_records
- **Missing values:** None.
- **Limitations:** Mandatory compliance training excluded.
- **Interpretation:** Reach of development.

### Training completion rate (`training_completion_rate`)

**Definition.** Share of decided enrolments that were completed.

- **Formula:** `completed / (completed + not completed)`
- **Numerator:** Completed
- **Denominator:** Completed + not completed
- **Grain:** Enrolment period
- **Source tables:** training_records
- **Missing values:** In-progress enrolments excluded.
- **Limitations:** —
- **Interpretation:** Programme effectiveness and engagement.

### Training cost per employee (`training_cost_per_employee`)

**Definition.** Training spend in the period divided by average headcount.

- **Formula:** `SUM(training_cost) / average headcount`
- **Numerator:** Training cost
- **Denominator:** Average headcount
- **Grain:** Period
- **Source tables:** training_records
- **Missing values:** None.
- **Limitations:** Direct programme fees only (no salary time).
- **Interpretation:** Investment in development.

## Engagement

### Engagement score (`engagement_score`)

**Definition.** Mean engagement index (1-5) across survey responses in the period.

- **Formula:** `AVG(engagement_score)`
- **Numerator:** Sum of scores
- **Denominator:** Responses
- **Grain:** Survey period
- **Source tables:** engagement_surveys
- **Missing values:** Non-respondents excluded; groups with fewer than 10 responses are suppressed.
- **Limitations:** Non-response is higher among disengaged employees (scores may be optimistic).
- **Interpretation:** Overall sentiment towards work and the organisation.

### Employee Net Promoter Score (eNPS) (`enps`)

**Definition.** Promoters (9-10) minus detractors (0-6) as a percentage of respondents; passives are 7-8.

- **Formula:** `(% promoters - % detractors) x 100, range -100 to +100`
- **Numerator:** Promoters - detractors
- **Denominator:** Respondents
- **Grain:** Survey period
- **Source tables:** engagement_surveys.enps_response
- **Missing values:** Groups below 10 responses suppressed.
- **Limitations:** Sensitive to small samples; not an average.
- **Interpretation:** Advocacy; above 0 means more promoters than detractors.

### Survey response rate (`response_rate`)

**Definition.** Responses divided by employees eligible on the survey date (active >= 30 days).

- **Formula:** `responses / eligible employees`
- **Numerator:** Responses
- **Denominator:** Eligible employees
- **Grain:** Survey period
- **Source tables:** engagement_surveys, monthly_workforce_snapshots
- **Missing values:** None.
- **Limitations:** —
- **Interpretation:** Representativeness of results.

### Feedback sentiment (`feedback_sentiment`)

**Definition.** Average VADER compound sentiment (-1 to +1) of free-text comments.

- **Formula:** `AVG(compound score)`
- **Numerator:** Sum of compound scores
- **Denominator:** Comments
- **Grain:** Department x period
- **Source tables:** survey_feedback
- **Missing values:** Groups with fewer than 10 comments suppressed.
- **Limitations:** Lexicon models misread sarcasm and mixed comments; treat as directional only.
- **Interpretation:** Tone of open feedback; read the themes, not just the score.

## Financial

### Workforce cost (`workforce_cost`)

**Definition.** Total simulated employer cost: base salary + variable pay target + benefits, pro-rated by days employed.

- **Formula:** `SUM((base + variable + benefits) / 12 x share of month employed)`
- **Numerator:** —
- **Denominator:** —
- **Grain:** Month x department
- **Source tables:** mart_employee_month.monthly_total_cost
- **Missing values:** None.
- **Limitations:** Variable pay at target (not actual payout); excludes recruitment and training spend; mid-month pay changes use the month-end rate.
- **Interpretation:** People cost run-rate.

### Cost per employee (annualised) (`cost_per_employee`)

**Definition.** Workforce cost in the period divided by average headcount, annualised.

- **Formula:** `workforce cost / average headcount x (12 / months)`
- **Numerator:** Workforce cost
- **Denominator:** Average headcount
- **Grain:** Period
- **Source tables:** mart_employee_month
- **Missing values:** None.
- **Limitations:** Mix effects (level, family, location) dominate.
- **Interpretation:** Average loaded cost of a role.

### People-cost budget variance (`budget_variance`)

**Definition.** Actual workforce cost minus planned people cost (AOP), absolute and as % of plan.

- **Formula:** `actual - planned; (actual - planned) / planned`
- **Numerator:** Actual - planned
- **Denominator:** Planned
- **Grain:** Month x department
- **Source tables:** workforce_budget, mart_employee_month
- **Missing values:** Months without a plan excluded.
- **Limitations:** Positive = overspend. The plan assumes a 7% merit budget from April.
- **Interpretation:** Cost control.

### Estimated turnover cost (`turnover_cost`)

**Definition.** Estimated replacement cost per exit: recruitment + onboarding/training + productivity ramp-up + vacancy cost, using configurable assumptions.

- **Formula:** `exits x (cost per hire + onboarding + ramp_months x ramp_loss x monthly cost + vacancy days x daily cost x coverage gap)`
- **Numerator:** —
- **Denominator:** —
- **Grain:** Period x department
- **Source tables:** mart_employee_month, job_requisitions
- **Missing values:** Uses observed averages where available, otherwise documented defaults.
- **Limitations:** An estimate, not an accounting loss. Sensitive to assumptions — see the sensitivity table.
- **Interpretation:** Order-of-magnitude financial case for retention.

## Performance

### Performance rating distribution (`rating_distribution`)

**Definition.** Share of completed reviews at each rating (1 = unsatisfactory ... 5 = outstanding).

- **Formula:** `reviews with rating r / completed reviews`
- **Numerator:** Reviews at rating r
- **Denominator:** Completed reviews
- **Grain:** Review cycle
- **Source tables:** performance_reviews
- **Missing values:** Incomplete reviews excluded.
- **Limitations:** Ratings depend on manager calibration.
- **Interpretation:** Calibration and performance mix.

## Planning

### Headcount vs plan (`headcount_vs_plan`)

**Definition.** Difference between actual month-end headcount and the approved operating plan (AOP).

- **Formula:** `actual headcount - planned headcount (and / planned headcount)`
- **Numerator:** Actual - planned
- **Denominator:** Planned
- **Grain:** Month x department
- **Source tables:** workforce_budget, monthly_workforce_snapshots
- **Missing values:** Months without a plan excluded.
- **Limitations:** The plan is a fictional AOP generated independently of the simulated actuals.
- **Interpretation:** Negative = behind plan (unfilled capacity); positive = ahead of plan.

## Recruitment

### Applications (`applications`)

**Definition.** Candidates who applied in the period.

- **Formula:** `COUNT(candidates with application_date in period)`
- **Numerator:** —
- **Denominator:** —
- **Grain:** Period
- **Source tables:** candidates
- **Missing values:** None.
- **Limitations:** One candidate = one application to one requisition.
- **Interpretation:** Top-of-funnel volume.

### Stage conversion rate (`stage_conversion`)

**Definition.** Share of candidates who reached a stage that also reached the next stage (candidate cohort by application date).

- **Formula:** `candidates reaching stage k+1 / candidates reaching stage k`
- **Numerator:** Candidates reaching next stage
- **Denominator:** Candidates reaching current stage
- **Grain:** Application cohort
- **Source tables:** candidates.furthest_stage
- **Missing values:** Candidates still in process are included at their current stage, which understates conversion for recent cohorts.
- **Limitations:** Funnel stages: Applied, Screened, Interviewed, Offered, Accepted, Joined.
- **Interpretation:** Where the funnel leaks.

### Offer acceptance rate (`offer_acceptance_rate`)

**Definition.** Share of decided offers that were accepted.

- **Formula:** `offers accepted / (offers accepted + offers declined)`
- **Numerator:** Accepted offers
- **Denominator:** Accepted + declined offers
- **Grain:** Offer cohort
- **Source tables:** recruitment_events (Offer)
- **Missing values:** Pending offers excluded.
- **Limitations:** Counter-offers and reneges are captured separately (no-show).
- **Interpretation:** Competitiveness of offers.

### Joining rate (`joining_rate`)

**Definition.** Share of accepted offers whose candidate actually joined (offer-to-join conversion).

- **Formula:** `joined / (joined + no-shows)`
- **Numerator:** Joined
- **Denominator:** Joined + no-shows
- **Grain:** Acceptance cohort
- **Source tables:** recruitment_events (Joining)
- **Missing values:** Accepted offers with a future joining date are excluded.
- **Limitations:** No-shows (reneging) are common in long notice-period markets.
- **Interpretation:** Pipeline reliability.

### Time to hire (`time_to_hire`)

**Definition.** Days from the hired candidate's application to their offer acceptance.

- **Formula:** `offer_accepted_date - application_date (hired candidates)`
- **Numerator:** —
- **Denominator:** —
- **Grain:** Hire
- **Source tables:** recruitment_events
- **Missing values:** Only candidates who accepted are measured.
- **Limitations:** Excludes notice period; reported as median because the distribution is right-skewed.
- **Interpretation:** Candidate-experience speed.

### Time to fill (`time_to_fill`)

**Definition.** Days from requisition opening (approval) to offer acceptance by the candidate who fills it.

- **Formula:** `offer_accepted_date - opened_date (filled requisitions)`
- **Numerator:** —
- **Denominator:** —
- **Grain:** Requisition
- **Source tables:** job_requisitions
- **Missing values:** Open requisitions are censored: excluding them biases the median down; the Predictive module uses Kaplan-Meier estimates that include them.
- **Limitations:** Excludes notice period before joining.
- **Interpretation:** Speed of replacing capacity.

### Cost per hire (`cost_per_hire`)

**Definition.** Simulated recruitment cost of filled requisitions divided by the number of hires.

- **Formula:** `SUM(recruitment_cost of filled requisitions) / filled requisitions`
- **Numerator:** Recruitment cost
- **Denominator:** Filled requisitions
- **Grain:** Period of offer acceptance
- **Source tables:** job_requisitions
- **Missing values:** Cancelled/open requisition costs are excluded from the numerator.
- **Limitations:** Costs are simulated: agency fees, referral bonuses, sourcing fees, recruiter time, assessments.
- **Interpretation:** Efficiency of sourcing channels.

## Workforce

### Active headcount (`active_headcount`)

**Definition.** Number of employees employed on the last day of the month (point-in-time).

- **Formula:** `COUNT(employees with joining_date <= month_end < exit_date)`
- **Numerator:** Employees active at month end
- **Denominator:** —
- **Grain:** Month-end snapshot; any organisational filter
- **Source tables:** monthly_workforce_snapshots (active_flag = 1)
- **Missing values:** Not applicable (counts).
- **Limitations:** Point-in-time: ignores people who joined and left within the month.
- **Interpretation:** Size of the workforce at a moment in time; use for capacity and span-of-control questions.

### Average headcount (`average_headcount`)

**Definition.** Mean of the month-end active headcounts over the selected months.

- **Formula:** `AVG(active_headcount over months in period)`
- **Numerator:** Sum of month-end headcounts
- **Denominator:** Number of months
- **Grain:** Period
- **Source tables:** monthly_workforce_snapshots
- **Missing values:** Months without data are excluded.
- **Limitations:** Smooths movements within a period but uses month-end points only.
- **Interpretation:** Denominator for period rates (attrition, cost per employee).

### Net workforce growth (`net_growth`)

**Definition.** Change in month-end headcount between the month before the period and the last month of the period.

- **Formula:** `(closing headcount - opening headcount) / opening headcount`
- **Numerator:** Closing - opening headcount
- **Denominator:** Opening headcount
- **Grain:** Period
- **Source tables:** monthly_workforce_snapshots
- **Missing values:** Undefined (shown as —) when the opening headcount is zero.
- **Limitations:** Equals joiners - exits (+ transfers for a department).
- **Interpretation:** Whether the workforce expanded or contracted; compare with plan.

### Joiners (`joiners`)

**Definition.** Employees whose joining date falls in the period.

- **Formula:** `COUNT(joining_date in period)`
- **Numerator:** —
- **Denominator:** —
- **Grain:** Period
- **Source tables:** mart_employee_month.joined_in_month
- **Missing values:** None.
- **Limitations:** Internal transfers are not joiners.
- **Interpretation:** Hiring delivered.

### Exits (leavers) (`exits`)

**Definition.** Employees whose separation date falls in the period.

- **Formula:** `COUNT(exit_date in period)`
- **Numerator:** —
- **Denominator:** —
- **Grain:** Period
- **Source tables:** mart_employee_month.exited_in_month
- **Missing values:** None.
- **Limitations:** Internal transfers are not exits.
- **Interpretation:** Workforce lost.

### Average tenure (`average_tenure`)

**Definition.** Mean completed months of service of employees active at the end of the period.

- **Formula:** `AVG(tenure_months) for active employees at period end`
- **Numerator:** Sum of tenure months
- **Denominator:** Active employees
- **Grain:** Period end
- **Source tables:** monthly_workforce_snapshots
- **Missing values:** None.
- **Limitations:** Pre-window service is included because joining dates pre-date the window.
- **Interpretation:** Experience depth; falling tenure can signal churn or rapid growth.

### Internal mobility rate (`internal_mobility_rate`)

**Definition.** Annualised share of employees who changed department (transfer) in the period.

- **Formula:** `transfers in period / average headcount x (12 / months)`
- **Numerator:** Transfer events
- **Denominator:** Average headcount
- **Grain:** Period
- **Source tables:** employee_events, monthly_workforce_snapshots
- **Missing values:** Zero when no transfers occurred.
- **Limitations:** Lateral moves within a department are not captured.
- **Interpretation:** Health of internal talent markets.

### Promotion rate (`promotion_rate`)

**Definition.** Annualised share of employees promoted (level increase) in the period.

- **Formula:** `promotions in period / average headcount x (12 / months)`
- **Numerator:** Promotion events
- **Denominator:** Average headcount
- **Grain:** Period
- **Source tables:** employee_events, monthly_workforce_snapshots
- **Missing values:** None.
- **Limitations:** Promotions cluster in the April cycle, so short periods can look volatile.
- **Interpretation:** Career progression; compare across departments and levels.

### Span of control (`span_of_control`)

**Definition.** Number of active direct reports per people manager at month end.

- **Formula:** `COUNT(active reports) GROUP BY manager_id`
- **Numerator:** Active direct reports
- **Denominator:** Managers with >= 1 report
- **Grain:** Month end
- **Source tables:** monthly_workforce_snapshots
- **Missing values:** Employees without a manager (CEO) excluded.
- **Limitations:** Direct reports only (not the whole sub-tree).
- **Interpretation:** Very wide spans may limit coaching; very narrow spans add management cost.
