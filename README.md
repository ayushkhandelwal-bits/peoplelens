# PeopleLens — Workforce Intelligence & Planning

**Turning Workforce Data into Better Business Decisions.**

PeopleLens is an independently developed, end-to-end workforce analytics product. It simulates the people data of
a fictional ~6,000-person organisation, engineers it into a validated relational model, and turns it into decisions:
where the workforce is changing, why people leave, what the next six months look like, what turnover costs, and what
a manager should do about it.

> **All employees, financial amounts and analytical results are simulated.** The organisation ("Aster Group") is
> fictional, no real or confidential personal data is used, and nothing here describes a real company.

![Workforce Explorer](docs/screenshots/workforce_explorer.png)

---

## Contents
1. [Business problem](#1-business-problem) · 2. [Product objectives](#2-product-objectives) ·
3. [Screenshots](#3-application-screenshots) · 4. [Architecture](#4-architecture) · 5. [Technology stack](#5-technology-stack) ·
6. [Synthetic data](#6-synthetic-data-generation) · 7. [Database schema](#7-database-schema) ·
8. [Analytical modules](#8-analytical-modules) · 9. [KPI definitions](#9-kpi-definitions) ·
10. [Predictive modelling](#10-predictive-modelling-methodology) · 11. [NLP & Ask PeopleLens](#11-nlp-and-ask-peoplelens) ·
12. [Business insights](#12-business-insights) · 13. [Governance & limitations](#13-governance-and-limitations) ·
14. [Installation](#14-local-installation) · 15. [Running](#15-running-the-application) · 16. [Testing](#16-testing) ·
17. [Future improvements](#17-future-improvements) · 18. [Author](#18-author)

## 1. Business problem

People data usually sits in separate systems — HRMS records, recruitment pipelines, surveys, payroll, budgets — so
leaders see headcount *reports* but struggle to answer the questions that matter: *Are we going to have the capacity
we planned for? Where is attrition genuinely abnormal rather than noisy? What does it cost us? Which levers are worth
pulling?* PeopleLens integrates these sources into one consistent model and answers them with stated uncertainty.

## 2. Product objectives

* One trusted source of truth: an effective-dated relational model with 138 automated quality rules guarding it.
* Every KPI defined once (formula, grain, limitations) and computed by one function everywhere it appears.
* Diagnostics that separate signal from noise (confidence intervals, multiple-comparison correction, confounding checks).
* Forecasts and risk models validated on held-out time periods and compared with simple baselines.
* Financial translation: plan vs actual, manpower cost, transparent turnover-cost estimates and scenarios.
* Safe access: read-only SQL, role-based access enforced by the database, small-group suppression, no individual
  employment decisions from models.
* Zero cost: open-source stack, runs offline on a laptop; no paid APIs, licences or cloud services.

## 3. Application screenshots

| Workforce Explorer | Talent & Lifecycle |
|---|---|
| ![](docs/screenshots/workforce_explorer.png) | ![](docs/screenshots/talent_lifecycle_cohorts.png) |
| **Predictive Intelligence** | **Planning & Business Insights** |
| ![](docs/screenshots/predictive_forecast.png) | ![](docs/screenshots/planning_recommendations.png) |
| **Ask PeopleLens** | **Data & Governance** |
| ![](docs/screenshots/ask_peoplelens.png) | ![](docs/screenshots/data_governance_access.png) |
| **How It Works** | **How It Works — models** |
| ![](docs/screenshots/how_it_works.png) | ![](docs/screenshots/how_it_works_models.png) |

More in [`docs/screenshots`](docs/screenshots). All screenshots are of the running application on the generated data.

## 4. Architecture

```mermaid
flowchart LR
    subgraph C["1 · Create"]
        SIM["Workforce simulator<br/>Python · NumPy · seed 42"] --> EXP["HRMS-style export<br/>17 CSVs + SHA-256 manifest"]
    end
    subgraph E["2 · Engineer & check"]
        STG["Staging tables"] --> CLN["Clean & type<br/>pandas"] --> DQ{{"Quality gate<br/>138 rules"}}
    end
    subgraph S["3 · Store"]
        DB[("PostgreSQL / SQLite<br/>16 tables")] --> MART["Mart<br/>employee × month"]
        DB --> VW["Aggregate views v_*"]
    end
    subgraph A["4 · Analyse & predict"]
        KPI["Metric layer<br/>35 KPIs"]
        STAT["Statistics<br/>SciPy · statsmodels"]
        ML["Models<br/>forecast · attrition · time to fill"]
        NLP["Text analytics<br/>VADER · TF-IDF"]
    end
    subgraph D["5 · Deliver"]
        APP["Streamlit app<br/>4 modules · Plotly"]
        ASK["Ask PeopleLens<br/>rule-based"]
        REP["Monthly report &<br/>recommendations"]
    end
    EXP -->|verify checksums| STG
    DQ -->|only if 0 errors| DB
    MART --> KPI & STAT & ML
    DB --> NLP
    KPI --> ASK & REP
    KPI & STAT & ML & NLP & ASK --> APP
    VW -.->|Viewer role| APP
```

**Models at a glance** — every model is trained on the past, tested on a later period, and kept only if it beats a
simple baseline:

| Question | Options compared | Chosen | Why |
|---|---|---|---|
| Headcount in 6 months? | Naive, Drift, Holt, Holt-Winters, Flow model | Flow model (MAE 30 vs 97 naive) | Lowest backtest error; uses the known hiring pipeline |
| Who is likely to leave? | Logistic regression, Gradient boosting | Logistic regression (AUC 0.64, lift 1.9×) | Boosting not materially better; drivers stay explainable |
| How long to fill a role? | Global median, Segment median, Gradient boosting | Kaplan-Meier P50/P80 ranges | No point model beat the baseline |
| What if…? | — | Deterministic scenario arithmetic | What-ifs must be traceable, not "predicted" |
| Comment sentiment & themes | VADER, keyword lexicon, TF-IDF | VADER + workplace lexicon | Offline, free, explainable; limits measured |
| Plain-English questions | LLM text-to-SQL (rejected) | Rule-based templates | Correct, auditable, zero cost |

The app's **How It Works** page walks through this flow step by step, with each model's diagram, live validation
results, the tech stack, design decisions and a glossary.

Details: [`docs/architecture.md`](docs/architecture.md).

```
peoplelens-workforce-intelligence/
├── app.py                     Streamlit entry point (navigation, sidebar)
├── views/                     the four modules + Ask PeopleLens + Data & Governance
├── components/                sidebar, filters, KPI cards, charts, tables, cached data access
├── config/settings.py         env-driven settings and generation profiles
├── database/                  schema.py (single source), connection.py, seed.py, generated schema.sql
├── src/
│   ├── data_generation/       month-by-month workforce simulator
│   ├── ingestion/             export, staging, cleaning, quality rules, pipeline, mart
│   ├── analytics/             metrics, workforce, recruitment, retention, engagement, performance,
│   │                          compensation, statistics, sql_queries, recommendations
│   ├── models/                features, attrition, workforce_forecast, hiring_forecast, scenarios
│   ├── nlp/                   text cleaning, sentiment, themes, feedback analytics
│   ├── assistant/             Ask PeopleLens: intent parser, query templates, answers, policy
│   ├── reporting/             monthly / executive report
│   └── governance/            metric dictionary, access control
├── scripts/                   generate, load, validate, train, report, run_pipeline, setup
├── tests/                     pytest suite + tests/ui headless browser checks
├── docs/                      architecture, data dictionary, metrics, modelling, governance, insights, deployment
└── data/samples/              small labelled synthetic extract
```

(The brief suggested `pages/`; the UI lives in `views/` because Streamlit treats a `pages/` folder as legacy
multipage navigation, which conflicts with the custom navigation used here.)

## 5. Technology stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ (tested on 3.13) |
| Database | PostgreSQL 16 (primary), SQLite (zero-setup fallback) — identical results on both |
| Access | SQLAlchemy 2 Core, psycopg 3 |
| Data | pandas, NumPy |
| Statistics | SciPy, statsmodels (logit, Kaplan-Meier, log-rank, exponential smoothing) |
| ML | scikit-learn (logistic regression, gradient boosting) |
| NLP | VADER (offline lexicon, extended with a small workplace lexicon), TF-IDF, keyword themes |
| App & charts | Streamlit, Plotly |
| Quality | pytest (94 tests), Ruff, Playwright headless UI checks |

## 6. Synthetic data generation

The data comes from a **month-by-month simulation** (`src/data_generation/`), not independent random columns:

* A fictional organisation: 4 business units, 24 departments + an executive office, 7 Indian cities, 9 job levels,
  13 job families. Reporting lines always point to a strictly more senior employee, so the hierarchy cannot contain
  cycles.
* Each month: scheduled joiners start; in April reviews, promotions and merit increases happen; exits are drawn from a
  hazard model; leavers' reports are reassigned; transfers and manager changes happen; quarterly pulse surveys run;
  requisitions open for gaps against each department's trajectory and **every hire comes through a simulated
  recruitment funnel** (application → screening → interview → offer → acceptance → joining, with rejections,
  withdrawals, declines and no-shows).
* Documented simulation assumptions (not real-world claims): engagement, pay compression, tenure, promotion recency,
  recent manager change, contract status and source of hire affect attrition; **location, gender and training have no
  direct effect** (deliberate nulls). Two narrative events: a reorganisation in Service Operations West (Jul 2025) and
  an engagement decline in Customer Support (from Oct 2025). Attrition is deliberately *not* perfectly predictable.
* Reproducible: `seed` in `config/settings.py` (same seed → identical data, verified by a test).

| Full profile (default, seed 42) | |
|---|---|
| Window | Oct 2023 – Sep 2026 (36 months) + 6 forward plan months |
| Employee master records | 9,336 (5,400 at the start; 3,925 hires in the window) |
| Source rows (16 tables) | ≈ 545,000 (+ 204,000 curated mart rows) |
| Annual attrition | 19.3% (15.5% voluntary in the last 12 months) |

The total is slightly above the brief's indicative 250–450k range because every stage of ~44,000 applications is
recorded (~107k recruitment events). Row counts were not inflated; use `--profile dev` for a ~55k-row dataset.
See [`docs/data_dictionary.md`](docs/data_dictionary.md) for all assumptions.

## 7. Database schema

16 source tables (12 from the brief, plus `locations`, `job_requisitions`, `survey_feedback` and `workforce_budget`)
and 4 operational/curated tables. Primary keys, foreign keys (deferrable, so the full load happens in one
transaction), check constraints and indexes are defined once in [`database/schema.py`](database/schema.py); the
DDL in [`database/schema.sql`](database/schema.sql) is generated from it.

`employees` · `departments` · `locations` · `job_history` (effective-dated) · `manager_relationships` ·
`job_requisitions` · `candidates` · `recruitment_events` · `employee_events` · `performance_reviews` ·
`engagement_surveys` · `survey_feedback` (no employee ID, by design) · `compensation_history` · `training_records` ·
`monthly_workforce_snapshots` · `workforce_budget` · `mart_employee_month` · `metric_definitions` ·
`data_quality_results` · `pipeline_runs`.

## 8. Analytical modules

| Module | Answers |
|---|---|
| **Workforce Explorer** | KPIs with prior-period deltas and definitions; headcount vs plan; joiners vs exits; composition; span of control; movements; curated SQL library with computed interpretations; custom read-only SQL; analyst-only record view |
| **Talent & Lifecycle** | Recruitment funnel, stage timings and source effectiveness; censoring-aware cohort retention, Kaplan-Meier by source; performance, promotion and mobility; engagement trends, eNPS, engagement→exit and manager-change analyses; feedback NLP; statistical diagnostics |
| **Predictive Intelligence** | 6-month headcount forecast with model comparison and intervals; attrition-risk model with ROC/PR/calibration; time-to-fill survival curves; hiring demand; interactive what-if scenarios; model monitoring |
| **Planning & Business Insights** | Plan vs actual (absolute and %); manpower cost vs budget; turnover-cost model with sensitivity; scenario comparison; rule-based recommendations; exportable executive report |
| **Ask PeopleLens** | Natural-language questions → parameterised SQL / model artefacts → explained answers |
| **Data & Governance** | Quality results, lineage, run log, metric glossary, data dictionary, live access-control test, privacy |
| **How It Works** | End-to-end technical walkthrough: flow diagram, step-by-step stages, each model (options, choice, validation), tech stack, design decisions, glossary |

## 9. KPI definitions

35 KPIs are defined in [`src/governance/metric_dictionary.py`](src/governance/metric_dictionary.py) (rendered in
[`docs/metric_definitions.md`](docs/metric_definitions.md), the in-app glossary and every KPI tooltip). Key choices:

* **Attrition rate** = exits in period ÷ average month-end headcount × 12 ÷ months (never exits ÷ current headcount).
* **Point-in-time vs average headcount** are separate metrics.
* **Time to fill** = requisition opened → offer accepted; **time to hire** = application → offer accepted.
* **Early attrition** counts a joiner only once the N-day window is fully observed (right-censoring).
* **eNPS** = % promoters (9–10) − % detractors (0–6).
* **Workforce cost** = base + variable at target + benefits, pro-rated by days employed.

## 10. Predictive modelling methodology

| Model | Validation | Result on the generated data |
|---|---|---|
| Headcount forecast (6 months) | Rolling-origin backtests (3 origins), vs Naive, Drift, Holt, Holt-Winters | **Flow model selected — MAE 30 heads vs 97 for naive** (Holt damped 31) |
| Attrition risk (voluntary exit ≤ 6 months) | Temporal hold-out: train 7 quarter-ends (Mar 2024–Sep 2025), test Mar 2026 | **Logistic regression ROC-AUC 0.64**, PR-AUC 0.13 vs 7.6% base rate, top-decile lift 1.9× (GBM 0.62) |
| Time to fill | Chronological hold-out + Kaplan-Meier | Segment models **do not beat** the global median (MAE ≈ 41 days) → planning uses P50/P80 distributions |

Leakage controls: features use only records dated on or before the prediction date (tested by perturbing future
data); exit dates/reasons are excluded; gender and names are excluded by policy. Honest reading: the attrition model
is useful for prioritising team-level conversations, not for predicting individuals. Full write-up:
[`docs/modelling_methodology.md`](docs/modelling_methodology.md).

## 11. NLP and Ask PeopleLens

**Feedback NLP** — 12.7k fictional comments with varied themes, neutral, mixed, hedged and sarcastic wording.
Cleaning, tokenisation, stop-word filtering, VADER sentiment (+ a small documented workplace lexicon), keyword
frequency, TF-IDF distinctive terms, multi-label themes, department sentiment (suppressed below 10 comments), theme
trends and an engagement-sentiment association. Comments carry no employee ID. The generator's intended labels are
kept outside the database to *evaluate* the NLP honestly (accuracy and sarcasm misreads are shown in the app).

**Ask PeopleLens** — a deterministic, local question-answering layer (no LLM, no API key):
intent and entity extraction (period, department, business unit, city, level, breakdown, horizon) → one of 20
curated, parameterised query templates or a validated model artefact → answer with the interpreted intent, filters,
SQL template, result table, chart, metric definition and caveat. It never executes question text as SQL, runs on a
read-only connection, declines individual-level requests and refuses high-stakes requests such as "should we fire…".

## 12. Business insights

Findings computed from the generated data (full write-up with method, impact and limitations in
[`docs/business_insights.md`](docs/business_insights.md)):

1. **Customer Support is the one clear attrition hotspot** — 30.2% annualised (95% CI 26.6–34.2%), 1.75× the rest of
   the organisation after Holm correction; only 2 of 24 departments differ once multiple comparisons are handled.
2. **Location differences are department mix** — raw χ² p < 0.001, but p = 0.73 after controlling for department.
3. **Volume ≠ value in sourcing** — Employee Referral converts 13.8% of applicants into joiners vs 4.7% for the
   highest-volume Job Portal.
4. **Low engagement precedes exits** — 10.4% of the lowest-engagement quartile leave within six months vs 4.5% of the
   highest (association).
5. **Manager changes coincide with a 0.40-point fall in manager-support scores**; the 2025 reorganisation in Service
   Operations West shows a 0.42-point engagement drop and recovery.
6. **The company is 89 heads behind plan and forecast to stay behind** (≈ −1.6% by Dec 2026); 13 departments need
   more hires than the plan budgets.
7. **Voluntary turnover costs an estimated ₹39.8 Cr a year** (≈ ₹4.3 L per exit; range ₹29.8–54.6 Cr in sensitivity).

## 13. Governance and limitations

Governance ([`docs/governance.md`](docs/governance.md)): synthetic provenance, assumed table ownership, one metric
dictionary, 138 quality rules (errors block publication), Analyst/Viewer roles enforced by the database (SQLite
authorizer + read-only file mode; PostgreSQL SELECT-only roles), suppression of groups under 10, no protected
attributes in models, no automated employment decisions, transient raw data.

Limitations: synthetic data reflects its own assumptions, so findings illustrate *method*, not real-world causes;
36 months limit seasonal modelling; role selection is a demo (no login); VADER misreads sarcasm and mixed comments;
cost figures are fictional and variable pay is at target.

## 14. Local installation

Python 3.11+ (3.13 tested). Windows PowerShell:

```powershell
git clone https://github.com/<your-username>/peoplelens-workforce-intelligence.git
cd peoplelens-workforce-intelligence
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env              # macOS/Linux: cp .env.example .env
```

**Database option A — SQLite (default, nothing to install).** Leave `PEOPLELENS_DATABASE_URL` as in `.env.example`.

**Database option B — PostgreSQL with Docker.**

```powershell
docker compose up -d db
# in .env:
# PEOPLELENS_DATABASE_URL=postgresql+psycopg://peoplelens:change-me@localhost:5432/peoplelens
# PEOPLELENS_ANALYST_PASSWORD=..., PEOPLELENS_VIEWER_PASSWORD=..., and the two role URLs (see .env.example)
```

## 15. Running the application

```powershell
python scripts/run_pipeline.py          # generate + load + validate + train models + monthly report (~1-2 min)
streamlit run app.py                    # http://localhost:8501
```

Individual steps:

```powershell
python scripts/generate_data.py [--profile dev] [--seed 7]   # synthetic HRMS export only
python scripts/load_database.py [--reset]                    # staging -> clean -> quality gate -> publish
python scripts/validate_project.py                           # re-run all quality rules on the live database
python scripts/train_models.py                               # deliberate model refresh
python scripts/generate_report.py --month 2026-09            # HTML + Markdown + CSV report in reports/
python scripts/setup.py --emit-ddl                           # regenerate database/schema.sql
```

If the database is missing, the app builds it on first launch (`PEOPLELENS_AUTO_BOOTSTRAP=true`).

## 16. Testing

```powershell
pip install -r requirements-dev.txt
pytest                # 94 tests on an isolated temporary database (~30 s)
ruff check .
# optional headless UI checks against a running app:
python -m playwright install chromium
python tests/ui/capture.py http://localhost:8501 ui_out
python tests/ui/interact.py http://localhost:8501 ui_out
```

Tests cover reproducibility, referential integrity, lifecycle and recruitment sequences, snapshot reconciliation,
detection of injected corruption, the quality gate blocking publication, KPI formulas on a hand-built fixture
(including zero denominators and empty ranges), read-only and role enforcement, leakage, temporal validation,
Ask PeopleLens answers matching central metrics, refusals, and report generation. The full pipeline has also been
run end-to-end on PostgreSQL 16 with SELECT-only roles.

## 17. Future improvements

* Authentication (OIDC) mapping identities to the Analyst/Viewer roles; row-level security in PostgreSQL.
* Optional local LLM (e.g. via Ollama) for paraphrasing questions — kept outside the critical path.
* Survival models for attrition (time-varying Cox) and hierarchical forecasts reconciled across departments.
* Incremental (CDC-style) loads instead of full rebuilds; dbt-style tests for the mart.
* Recruiter-capacity modelling in hiring plans; scenario versions saved to the budget table.

## 18. Author

**Ayush Khandelwal — MBA Business Analytics**

Independently designed and built as a portfolio project. Released under the MIT licence.
