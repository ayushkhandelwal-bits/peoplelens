"""Central metric dictionary — the single source of truth for KPI definitions.

Every KPI shown in the application, the reports and Ask PeopleLens is computed
by a function in ``src/analytics/metrics.py`` that follows the definition here.
The dictionary is also written to the ``metric_definitions`` table and rendered
in the in-app glossary and ``docs/metric_definitions.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    category: str
    definition: str
    formula: str
    numerator: str
    denominator: str
    grain: str
    source_tables: str
    missing_values: str
    limitations: str
    interpretation: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metric_key"] = d.pop("key")
        d["metric_name"] = d.pop("name")
        return d

    @property
    def tooltip(self) -> str:
        return f"{self.definition}\n\nFormula: {self.formula}"


M = Metric
METRICS: dict[str, Metric] = {m.key: m for m in [
    # ---------------------------------------------------------------- workforce
    M("active_headcount", "Active headcount", "Workforce",
      "Number of employees employed on the last day of the month (point-in-time).",
      "COUNT(employees with joining_date <= month_end < exit_date)",
      "Employees active at month end", "—", "Month-end snapshot; any organisational filter",
      "monthly_workforce_snapshots (active_flag = 1)", "Not applicable (counts).",
      "Point-in-time: ignores people who joined and left within the month.",
      "Size of the workforce at a moment in time; use for capacity and span-of-control questions."),
    M("average_headcount", "Average headcount", "Workforce",
      "Mean of the month-end active headcounts over the selected months.",
      "AVG(active_headcount over months in period)", "Sum of month-end headcounts", "Number of months",
      "Period", "monthly_workforce_snapshots", "Months without data are excluded.",
      "Smooths movements within a period but uses month-end points only.",
      "Denominator for period rates (attrition, cost per employee)."),
    M("net_growth", "Net workforce growth", "Workforce",
      "Change in month-end headcount between the month before the period and the last month of the period.",
      "(closing headcount - opening headcount) / opening headcount",
      "Closing - opening headcount", "Opening headcount", "Period", "monthly_workforce_snapshots",
      "Undefined (shown as —) when the opening headcount is zero.",
      "Equals joiners - exits (+ transfers for a department).",
      "Whether the workforce expanded or contracted; compare with plan."),
    M("joiners", "Joiners", "Workforce", "Employees whose joining date falls in the period.",
      "COUNT(joining_date in period)", "—", "—", "Period", "mart_employee_month.joined_in_month",
      "None.", "Internal transfers are not joiners.", "Hiring delivered."),
    M("exits", "Exits (leavers)", "Workforce", "Employees whose separation date falls in the period.",
      "COUNT(exit_date in period)", "—", "—", "Period", "mart_employee_month.exited_in_month",
      "None.", "Internal transfers are not exits.", "Workforce lost."),
    M("average_tenure", "Average tenure", "Workforce",
      "Mean completed months of service of employees active at the end of the period.",
      "AVG(tenure_months) for active employees at period end", "Sum of tenure months", "Active employees",
      "Period end", "monthly_workforce_snapshots", "None.",
      "Pre-window service is included because joining dates pre-date the window.",
      "Experience depth; falling tenure can signal churn or rapid growth."),
    M("internal_mobility_rate", "Internal mobility rate", "Workforce",
      "Annualised share of employees who changed department (transfer) in the period.",
      "transfers in period / average headcount x (12 / months)", "Transfer events",
      "Average headcount", "Period", "employee_events, monthly_workforce_snapshots",
      "Zero when no transfers occurred.", "Lateral moves within a department are not captured.",
      "Health of internal talent markets."),
    M("promotion_rate", "Promotion rate", "Workforce",
      "Annualised share of employees promoted (level increase) in the period.",
      "promotions in period / average headcount x (12 / months)", "Promotion events", "Average headcount",
      "Period", "employee_events, monthly_workforce_snapshots", "None.",
      "Promotions cluster in the April cycle, so short periods can look volatile.",
      "Career progression; compare across departments and levels."),
    M("span_of_control", "Span of control", "Workforce",
      "Number of active direct reports per people manager at month end.",
      "COUNT(active reports) GROUP BY manager_id", "Active direct reports", "Managers with >= 1 report",
      "Month end", "monthly_workforce_snapshots", "Employees without a manager (CEO) excluded.",
      "Direct reports only (not the whole sub-tree).",
      "Very wide spans may limit coaching; very narrow spans add management cost."),
    M("headcount_vs_plan", "Headcount vs plan", "Planning",
      "Difference between actual month-end headcount and the approved operating plan (AOP).",
      "actual headcount - planned headcount (and / planned headcount)", "Actual - planned", "Planned",
      "Month x department", "workforce_budget, monthly_workforce_snapshots", "Months without a plan excluded.",
      "The plan is a fictional AOP generated independently of the simulated actuals.",
      "Negative = behind plan (unfilled capacity); positive = ahead of plan."),
    # ---------------------------------------------------------------- attrition
    M("attrition_rate", "Attrition rate (annualised)", "Attrition",
      "Leavers in the period relative to the average workforce, annualised.",
      "exits in period / average headcount x (12 / months in period)", "Exits in period",
      "Average month-end headcount in period", "Period; any organisational filter",
      "monthly_workforce_snapshots, employees", "Shown as — when average headcount is zero.",
      "Annualising short periods amplifies seasonality (April-May exits are higher). Uses month-end "
      "points for the denominator. Dividing by *current* headcount instead would overstate attrition "
      "in shrinking teams and understate it in growing teams.",
      "Share of the workforce lost per year at the current pace."),
    M("voluntary_attrition_rate", "Voluntary attrition rate", "Attrition",
      "Resignations (employee-initiated exits) relative to average headcount, annualised.",
      "voluntary exits / average headcount x (12 / months)", "Voluntary exits", "Average headcount", "Period",
      "employees.exit_type", "None.", "Exit type is recorded at separation and may be misclassified.",
      "Regretted, controllable turnover — the main retention lever."),
    M("involuntary_attrition_rate", "Involuntary attrition rate", "Attrition",
      "Employer-initiated exits (performance, conduct, restructuring, contract end) relative to average "
      "headcount, annualised.", "involuntary exits / average headcount x (12 / months)",
      "Involuntary exits", "Average headcount", "Period", "employees.exit_type", "None.",
      "Includes non-renewal of fixed-term contracts.", "Workforce actions initiated by the organisation."),
    M("early_attrition", "Early attrition (30/60/90 days)", "Attrition",
      "Share of a joining cohort that left within N days of joining, among joiners observed for at least N "
      "days.", "leavers with tenure < N days / joiners with >= N days of observation",
      "Joiners who left within N days", "Joiners whose N-day window is complete", "Joining cohort",
      "employees", "Joiners with an incomplete observation window are excluded (right-censoring).",
      "Small cohorts produce wide confidence intervals.", "Onboarding and hiring-quality signal."),
    M("cohort_retention", "Cohort retention", "Attrition",
      "Share of a joining cohort still employed N months after joining.",
      "1 - leavers within N months / eligible joiners", "Retained joiners", "Joiners with >= N months observed",
      "Joining month or quarter", "employees", "Censored joiners excluded from the denominator.",
      "Recent cohorts have fewer observable horizons.", "Durability of hiring."),
    M("high_performer_attrition", "High-performer attrition", "Attrition",
      "Annualised voluntary attrition among employees whose latest completed rating before leaving (or the "
      "period end) was 4 or 5.", "voluntary exits of high performers / average high performers x 12/months",
      "High-performer voluntary exits", "Average number of active high performers", "Period",
      "performance_reviews, employees", "Employees without a completed review are excluded.",
      "Ratings are calibrated annually; new joiners have no rating.", "Loss of critical talent."),
    # ---------------------------------------------------------------- recruitment
    M("applications", "Applications", "Recruitment", "Candidates who applied in the period.",
      "COUNT(candidates with application_date in period)", "—", "—", "Period", "candidates", "None.",
      "One candidate = one application to one requisition.", "Top-of-funnel volume."),
    M("stage_conversion", "Stage conversion rate", "Recruitment",
      "Share of candidates who reached a stage that also reached the next stage (candidate cohort by "
      "application date).", "candidates reaching stage k+1 / candidates reaching stage k",
      "Candidates reaching next stage", "Candidates reaching current stage", "Application cohort",
      "candidates.furthest_stage", "Candidates still in process are included at their current stage, which "
      "understates conversion for recent cohorts.", "Funnel stages: Applied, Screened, Interviewed, Offered, "
      "Accepted, Joined.", "Where the funnel leaks."),
    M("offer_acceptance_rate", "Offer acceptance rate", "Recruitment",
      "Share of decided offers that were accepted.", "offers accepted / (offers accepted + offers declined)",
      "Accepted offers", "Accepted + declined offers", "Offer cohort", "recruitment_events (Offer)",
      "Pending offers excluded.", "Counter-offers and reneges are captured separately (no-show).",
      "Competitiveness of offers."),
    M("joining_rate", "Joining rate", "Recruitment",
      "Share of accepted offers whose candidate actually joined (offer-to-join conversion).",
      "joined / (joined + no-shows)", "Joined", "Joined + no-shows", "Acceptance cohort",
      "recruitment_events (Joining)", "Accepted offers with a future joining date are excluded.",
      "No-shows (reneging) are common in long notice-period markets.", "Pipeline reliability."),
    M("time_to_hire", "Time to hire", "Recruitment",
      "Days from the hired candidate's application to their offer acceptance.",
      "offer_accepted_date - application_date (hired candidates)", "—", "—", "Hire",
      "recruitment_events", "Only candidates who accepted are measured.",
      "Excludes notice period; reported as median because the distribution is right-skewed.",
      "Candidate-experience speed."),
    M("time_to_fill", "Time to fill", "Recruitment",
      "Days from requisition opening (approval) to offer acceptance by the candidate who fills it.",
      "offer_accepted_date - opened_date (filled requisitions)", "—", "—", "Requisition",
      "job_requisitions", "Open requisitions are censored: excluding them biases the median down; the "
      "Predictive module uses Kaplan-Meier estimates that include them.",
      "Excludes notice period before joining.", "Speed of replacing capacity."),
    M("cost_per_hire", "Cost per hire", "Recruitment",
      "Simulated recruitment cost of filled requisitions divided by the number of hires.",
      "SUM(recruitment_cost of filled requisitions) / filled requisitions", "Recruitment cost",
      "Filled requisitions", "Period of offer acceptance", "job_requisitions",
      "Cancelled/open requisition costs are excluded from the numerator.",
      "Costs are simulated: agency fees, referral bonuses, sourcing fees, recruiter time, assessments.",
      "Efficiency of sourcing channels."),
    # ---------------------------------------------------------------- performance & development
    M("rating_distribution", "Performance rating distribution", "Performance",
      "Share of completed reviews at each rating (1 = unsatisfactory ... 5 = outstanding).",
      "reviews with rating r / completed reviews", "Reviews at rating r", "Completed reviews", "Review cycle",
      "performance_reviews", "Incomplete reviews excluded.", "Ratings depend on manager calibration.",
      "Calibration and performance mix."),
    M("training_participation", "Training participation", "Development",
      "Share of average headcount enrolled in at least one non-compliance programme in the period.",
      "distinct employees enrolled / average headcount", "Distinct learners", "Average headcount", "Period",
      "training_records", "None.", "Mandatory compliance training excluded.", "Reach of development."),
    M("training_completion_rate", "Training completion rate", "Development",
      "Share of decided enrolments that were completed.", "completed / (completed + not completed)",
      "Completed", "Completed + not completed", "Enrolment period", "training_records",
      "In-progress enrolments excluded.", "—", "Programme effectiveness and engagement."),
    M("training_cost_per_employee", "Training cost per employee", "Development",
      "Training spend in the period divided by average headcount.", "SUM(training_cost) / average headcount",
      "Training cost", "Average headcount", "Period", "training_records", "None.",
      "Direct programme fees only (no salary time).", "Investment in development."),
    # ---------------------------------------------------------------- engagement
    M("engagement_score", "Engagement score", "Engagement",
      "Mean engagement index (1-5) across survey responses in the period.", "AVG(engagement_score)",
      "Sum of scores", "Responses", "Survey period", "engagement_surveys",
      "Non-respondents excluded; groups with fewer than 10 responses are suppressed.",
      "Non-response is higher among disengaged employees (scores may be optimistic).",
      "Overall sentiment towards work and the organisation."),
    M("enps", "Employee Net Promoter Score (eNPS)", "Engagement",
      "Promoters (9-10) minus detractors (0-6) as a percentage of respondents; passives are 7-8.",
      "(% promoters - % detractors) x 100, range -100 to +100", "Promoters - detractors", "Respondents",
      "Survey period", "engagement_surveys.enps_response", "Groups below 10 responses suppressed.",
      "Sensitive to small samples; not an average.", "Advocacy; above 0 means more promoters than detractors."),
    M("response_rate", "Survey response rate", "Engagement",
      "Responses divided by employees eligible on the survey date (active >= 30 days).",
      "responses / eligible employees", "Responses", "Eligible employees", "Survey period",
      "engagement_surveys, monthly_workforce_snapshots", "None.", "—", "Representativeness of results."),
    M("feedback_sentiment", "Feedback sentiment", "Engagement",
      "Average VADER compound sentiment (-1 to +1) of free-text comments.", "AVG(compound score)",
      "Sum of compound scores", "Comments", "Department x period", "survey_feedback",
      "Groups with fewer than 10 comments suppressed.",
      "Lexicon models misread sarcasm and mixed comments; treat as directional only.",
      "Tone of open feedback; read the themes, not just the score."),
    # ---------------------------------------------------------------- financial
    M("workforce_cost", "Workforce cost", "Financial",
      "Total simulated employer cost: base salary + variable pay target + benefits, pro-rated by days "
      "employed.", "SUM((base + variable + benefits) / 12 x share of month employed)", "—", "—",
      "Month x department", "mart_employee_month.monthly_total_cost", "None.",
      "Variable pay at target (not actual payout); excludes recruitment and training spend; mid-month pay "
      "changes use the month-end rate.", "People cost run-rate."),
    M("cost_per_employee", "Cost per employee (annualised)", "Financial",
      "Workforce cost in the period divided by average headcount, annualised.",
      "workforce cost / average headcount x (12 / months)", "Workforce cost", "Average headcount", "Period",
      "mart_employee_month", "None.", "Mix effects (level, family, location) dominate.",
      "Average loaded cost of a role."),
    M("budget_variance", "People-cost budget variance", "Financial",
      "Actual workforce cost minus planned people cost (AOP), absolute and as % of plan.",
      "actual - planned; (actual - planned) / planned", "Actual - planned", "Planned", "Month x department",
      "workforce_budget, mart_employee_month", "Months without a plan excluded.",
      "Positive = overspend. The plan assumes a 7% merit budget from April.", "Cost control."),
    M("turnover_cost", "Estimated turnover cost", "Financial",
      "Estimated replacement cost per exit: recruitment + onboarding/training + productivity ramp-up + "
      "vacancy cost, using configurable assumptions.",
      "exits x (cost per hire + onboarding + ramp_months x ramp_loss x monthly cost + vacancy days x daily "
      "cost x coverage gap)", "—", "—", "Period x department", "mart_employee_month, job_requisitions",
      "Uses observed averages where available, otherwise documented defaults.",
      "An estimate, not an accounting loss. Sensitive to assumptions — see the sensitivity table.",
      "Order-of-magnitude financial case for retention."),
]}


def get(key: str) -> Metric:
    return METRICS[key]


def as_records() -> list[dict]:
    return [m.to_dict() for m in METRICS.values()]


def to_markdown() -> str:
    lines = ["# Metric definitions", "",
             "Generated from `src/governance/metric_dictionary.py` — the single source of truth used by the "
             "application, reports and Ask PeopleLens.", ""]
    cat = None
    for m in sorted(METRICS.values(), key=lambda x: x.category):
        if m.category != cat:
            cat = m.category
            lines += [f"## {cat}", ""]
        lines += [f"### {m.name} (`{m.key}`)", "", f"**Definition.** {m.definition}", "",
                  f"- **Formula:** `{m.formula}`", f"- **Numerator:** {m.numerator}",
                  f"- **Denominator:** {m.denominator}", f"- **Grain:** {m.grain}",
                  f"- **Source tables:** {m.source_tables}", f"- **Missing values:** {m.missing_values}",
                  f"- **Limitations:** {m.limitations}", f"- **Interpretation:** {m.interpretation}", ""]
    return "\n".join(lines)
