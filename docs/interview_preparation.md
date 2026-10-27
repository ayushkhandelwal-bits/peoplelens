# Interview preparation — PeopleLens

Concise talking points for discussing the project. Numbers refer to the default build (seed 42). Be explicit that the
data is synthetic and that this is an independent portfolio project, not work done for an employer.

## Why I built it
People decisions — hiring plans, retention spend, reorganisations — are often made from disconnected reports. I
wanted to show the full chain an analytics team owns: integrate the data, make the numbers trustworthy, explain what
is happening, forecast what comes next, put a rupee value on it, and give managers actions with the uncertainty
stated. The MBA lens: every technical component answers a business question.

## Database architecture
* 16 normalised source tables around a stable `employee_id`, effective-dated history tables (job, manager, pay), an
  event table for lifecycle reconciliation, and a monthly snapshot table for point-in-time analysis.
* A curated mart (one row per employee per month with lifecycle flags and cost) so every dashboard filters and
  aggregates the same way.
* Deferrable foreign keys allow the circular department-head ↔ employee relationship and a single-transaction load.
* Aggregate views (`v_*`) are the only objects the Viewer role can read — enforced by the database.
* *Trade-off:* `employees.exit_date` is denormalised for query simplicity; a quality rule reconciles it with the
  Exit events.

## Data generation methodology
* Month-by-month simulation, not independent random columns: exits come from a hazard model, hires from a simulated
  recruitment funnel, promotions and pay from an April cycle, surveys from a latent engagement state.
* Deliberate realism: right-censoring at the extraction date (open requisitions, pending joiners), noise in the raw
  export, nulls where processes are incomplete, effects that are weak and a few variables with **no** effect
  (location, gender) to test whether the analysis finds spurious patterns.
* Reproducible by seed; verified by a test.

## SQL design decisions
* Write once, run on PostgreSQL and SQLite; dialect differences isolated in three helper functions.
* Curated query library with parameters (never string concatenation), each with the business question and an
  interpretation computed from the rows.
* Custom SQL: single SELECT/WITH statement, keyword checks *and* a read-only connection; row cap and timeout.
* PostgreSQL enforces `VARCHAR` lengths that SQLite ignores — running on both surfaced three length issues, which I
  turned into a permanent quality rule.

## KPI definitions
* Attrition = exits ÷ **average** headcount, annualised; dividing by current headcount overstates attrition in
  shrinking teams and understates it in growing ones.
* Point-in-time vs average headcount; time-to-hire vs time-to-fill; censoring-aware early attrition; eNPS as
  %promoters − %detractors; cost pro-rated by days employed.
* One dictionary, one implementation, used by dashboards, reports and Ask PeopleLens (a test checks the assistant's
  numbers equal the central metric).

## Attrition modelling
* Employee × quarter-end panel, 6-month voluntary-exit label, features strictly dated before the prediction date.
* Temporal split (train Mar 2024–Sep 2025, test Mar 2026); threshold chosen on the last training snapshot.
* Logistic regression primary (AUC 0.64, PR-AUC 0.13 vs 7.6% base rate, top-decile lift 1.9×); gradient boosting
  not materially better, so I kept the interpretable model.
* How I'd explain AUC 0.64: "It ranks risk meaningfully better than chance, but most individual exits are not
  predictable from HR data — so we use it to prioritise teams and conversations, never to judge a person."

## Forecasting methodology
* Baselines first (naive, drift), then Holt and Holt-Winters, then a structural flow model using the accepted-offer
  pipeline and seasonal exit rates.
* Rolling-origin backtests; flow model MAE 29 heads vs 97 naive. Holt-Winters overfits with ~30 months of history.
* Seasonal-naive actually forecasts *monthly exits* slightly better than the flow model — I report that rather than
  hide it.
* Intervals from out-of-sample errors, not in-sample fit.

## Business-impact calculations
* Turnover cost = cost per hire (observed) + onboarding + ramp-up productivity loss + uncovered vacancy, with a
  sensitivity table (₹29.8–54.6 Cr around ₹39.8 Cr for 920 voluntary exits).
* Plan vs actual in heads **and** percent, because a small % in a large team can be more missing capacity.
* Scenarios are deterministic arithmetic, explicitly separated from trained models.

## NLP and its limitations
* VADER is fast, offline and explainable, but general-purpose: workplace words like "burnout" or "firefighting"
  scored neutral, so I added a small, documented workplace lexicon. It still misreads sarcasm ("Great, another
  quarter of doing two people's jobs") and mixed comments.
* I evaluate it against the generator's intended labels kept outside the database, and show misread examples in the
  app. Sentiment is reported only for groups of ≥ 10 comments and comments carry no employee ID.

## Ask PeopleLens architecture
* Deterministic intent/entity parsing → 20 curated parameterised templates or validated model artefacts → answer with
  intent, filters, SQL, table, chart, definition and caveat.
* Why not an LLM text-to-SQL: correctness, auditability, zero cost and no risk of generated write queries; an optional
  local model could later paraphrase questions without being in the critical path.
* Refuses high-stakes requests ("should we fire…") and individual-level sensitive data.

## Data governance
* Provenance, ownership, one metric dictionary, 138 rules (errors block publication), quarantine of invalid rows,
  run log, Analyst/Viewer roles enforced by the database, suppression of small groups, no protected attributes in
  models.
* The demo picks the role in the sidebar; in production it would come from an identity provider.

## Model leakage and validation
* Leakage I designed against: exit dates/reasons, future surveys or reviews, outcome windows overlapping the test
  period, using department attrition computed over the future. A test perturbs future records and asserts features
  do not change.
* Validation is always chronological; every model is compared with a simple baseline.

## Limitations of synthetic data
* Findings reflect the simulator's assumptions; the value is the method (and that it recovers built-in effects while
  rejecting spurious ones, e.g. location).
* Real data would bring messier identifiers, retroactive corrections, policy changes and survey design effects.

## Possible future enhancements
Identity-based roles and PostgreSQL row-level security; time-varying survival models; hierarchical forecast
reconciliation; recruiter-capacity planning; incremental loads; saving scenario versions to the plan table.

## Likely questions — short answers
* *What would you do first with real data?* Profile and reconcile headcount to payroll, agree metric definitions
  with HR and Finance, then build the quality gate before any dashboard.
* *How do you know an attrition difference is real?* Confidence intervals, multiple-comparison correction, and
  checking confounders (the location example).
* *What would you tell a leader tomorrow?* Customer Support is the one real hotspot (1.75× the rest); referrals are
  the best-value channel; we are ~90 heads behind plan because attrition runs above the plan's assumption and backfills
  take 2–4 months.
