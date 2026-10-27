# Business insights from the generated data

All figures below were computed by PeopleLens from the default full-profile dataset (seed 42, data to
30 Sep 2026; analysis period Oct 2025 – Sep 2026 unless stated). **The data is synthetic**: the findings show how the
product reasons about a workforce, not facts about any real organisation. Several patterns were deliberately built
into the simulation (documented in `data_dictionary.md`); the point is that the analytics recover them *and*
correctly decline to over-read the noise around them.

To reproduce: `python scripts/run_pipeline.py`, then open the module named under each finding.

---

## 1. One department is a genuine attrition hotspot — most "differences" are noise

* **Business question:** Which departments have abnormal attrition, as opposed to normal variation?
* **KPI:** annualised attrition rate; share of employees present who left voluntarily.
* **Method:** `metrics.attrition_by("department")` with exact Poisson 95% CIs; two-proportion test of each department
  vs the rest with Holm correction across 24 tests (Talent & Lifecycle → Statistical diagnostics).
* **Result:** Customer Support runs at **30.2%** annualised attrition (95% CI 26.6–34.2%) against **18.6%**
  organisation-wide. Its voluntary leaver share is **1.75×** the rest (20.1% vs 11.5%, Holm p < 0.0001). Only
  **2 of 24** departments differ after correction (Customer Support higher, Enterprise Sales lower); **4 more** would
  look "significant" without correcting for multiple comparisons.
* **Interpretation:** Leadership attention should go to one team, not to a league table of 24.
* **Potential impact:** 222 voluntary Customer Support exits in 12 months ≈ **₹9.6 Cr** estimated replacement cost.
* **Recommended action:** stay interviews, workload and pay-positioning review, and manager-stability check in
  Customer Support; stop escalating departments whose rates are within noise.
* **Limitations:** department differences partly reflect level and tenure mix; synthetic data.

## 2. Location looks important — until department mix is controlled for

* **Business question:** Should retention initiatives be targeted by city?
* **KPI:** voluntary leaver share by location.
* **Method:** χ² test across 7 cities, then a likelihood-ratio test of location in a logistic model with department
  fixed effects.
* **Result:** raw comparison χ² = 51.8, p < 0.001 (Jaipur 19.0% vs Bengaluru 9.9%). After controlling for department,
  **p = 0.73** — no location effect remains.
* **Interpretation:** cities with high attrition are where frontline teams (support, service operations) sit. This is
  a confounding effect — and in the simulation location was given no direct effect, so the method recovers the truth.
* **Impact / action:** do not fund city-based retention programmes; target the roles and teams instead.
* **Limitations:** observational test; a real organisation could have genuine local labour-market effects.

## 3. Sourcing: volume is not value

* **Business question:** Which recruitment channels deliver joiners efficiently?
* **KPI:** join yield (joiners ÷ applications), offer acceptance, 90-day retention, cost per hire.
* **Method:** application-cohort funnel by source (Talent & Lifecycle → Recruitment funnel; SQL library
  "Recruitment conversion by source").
* **Result:** Employee Referral converts **13.8%** of applicants into joiners with the best 90-day retention
  (**96.6%**); the highest-volume channel, Job Portal (4,800 applications), converts **4.7%**. Recruitment agencies
  convert well (9.9%) but cost the most per hire (≈ ₹2.1 L vs ₹1.0–1.4 L). A log-rank test confirms joiner survival
  differs by source (p = 0.003).
* **Interpretation:** screening effort spent on low-yield channels lengthens time-to-fill.
* **Action:** expand referral incentives; tighten Job Portal screening; reserve agencies for senior roles.
* **Limitations:** source mix differs by role; compare within job family before moving budget.

## 4. Low engagement precedes voluntary exits

* **Business question:** Is the engagement survey an early-warning signal?
* **KPI:** voluntary exit within six months of a survey response, by engagement quartile.
* **Method:** 31,005 responses with a fully observed six-month window; χ² and Mann-Whitney tests; logistic regression
  with employee-clustered errors.
* **Result:** **10.4%** of the lowest quartile left within six months vs **4.5%** of the highest (χ² p ≈ 1.6e-51). In the
  drivers model each +1 SD of engagement is associated with **0.72×** the odds of leaving; *not answering* the last
  survey is associated with **1.23×** odds. Pseudo-R² is 0.02: most attrition is not explained by these factors.
* **Interpretation:** engagement is a useful prioritisation signal for teams, not a predictor of individuals.
* **Action:** act on low-engagement teams within the quarter; track non-response as a signal.
* **Limitations:** association, not causation; disengaged employees respond less often.

## 5. Manager changes and reorganisations damage manager-support scores

* **Business question:** What happens to engagement when reporting lines change?
* **Method:** consecutive quarterly survey pairs for the same employee, with vs without a manager change in between.
* **Result:** manager-support scores fell **0.40 points** more for the 1,169 employees whose manager changed
  (Mann-Whitney p ≈ 3e-54); engagement fell 0.15 more. In Service Operations West, the July 2025 reorganisation
  coincides with engagement dropping from **3.63 to 3.21** (2025-Q2 → Q3) before recovering to 3.63 by 2026-Q3;
  its 12-month attrition is **23.6%**, second highest.
* **Action:** pair reorganisations with structured manager hand-overs and an early pulse check.
* **Limitations:** manager changes are not random (exits, reorganisations), so this is not a causal estimate.

## 6. Capacity: behind plan now, and the gap persists

* **Business question:** Will we have the workforce the plan assumes?
* **KPI:** headcount vs plan; forecast gap; hires required vs planned hires.
* **Method:** AOP comparison; validated flow-model forecast (backtest MAE 29 heads vs 97 naive); hiring-demand
  calculation net of the offer pipeline.
* **Result:** actual **6,106** vs plan **6,195** (−89, −1.4%) at Sep 2026; forecast **−75** vs plan by Dec 2026. The
  largest absolute gap is Service Operations North (−16) and the largest percentage gap Marketing (−7.0%). In **13 of 25**
  departments the hires needed over six months exceed the hires the plan budgeted.
* **Interpretation:** the shortfall is driven by attrition running above the plan's assumption plus time-to-fill
  (median ≈ 63 days, P80 well above 100 days for several families), not by insufficient requisitions alone.
* **Action:** open backfill requisitions earlier (P80 time-to-fill + notice period), prioritise the departments in the
  hiring-demand table, revisit the plan's attrition assumption.
* **Limitations:** department forecasts ignore transfers and are less precise than the organisation forecast.

## 7. Turnover has a material, quantifiable cost

* **Business question:** What is the business case for retention?
* **Method:** transparent replacement-cost model: observed cost per hire (₹1.39 L) + onboarding (₹25k) + 3-month ramp
  at 50% output + 103 vacancy days (observed) at 30% uncovered; one-at-a-time sensitivity.
* **Result:** 920 voluntary exits in 12 months ≈ **₹39.8 Cr** (≈ ₹4.3 L per exit), range **₹29.8–54.6 Cr**; most
  sensitive to ramp-up time. Scenario comparison: ±2 pp attrition moves six-month turnover cost by several crore and
  end headcount by tens of heads.
* **Action:** use the estimate to size retention investment (e.g. Customer Support) against expected savings.
* **Limitations:** an estimate, not an accounting loss; fictional INR.

## What did *not* show a meaningful pattern

* **Time to fill by segment:** segment medians and gradient boosting do **not** beat the global median on a later
  hold-out (MAE ≈ 41 days for all). Time-to-fill is reported as distributions instead of point predictions.
* **Promotion staleness:** "no promotion in 3+ years" is not significantly associated with leaving once other factors
  are included (odds ratio 0.99, p = 0.92).
* **Engagement quarter-on-quarter:** no department's engagement changed significantly between 2026-Q2 and 2026-Q3.
* **Training:** employees with 16+ development hours improved ratings slightly more (+0.16 points after matching on
  prior rating), but they were selected into programmes, so this is not evidence that training caused improvement.
