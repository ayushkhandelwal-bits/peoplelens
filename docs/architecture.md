# Architecture

## Layers

| Layer | Code | Responsibility |
|---|---|---|
| Configuration | `config/settings.py` | Env-driven settings (`.env`), generation profiles (`full`, `dev`), seed |
| Synthetic source | `src/data_generation/` | Month-by-month simulation of the organisation and assembly into tables |
| Landing zone | `src/ingestion/hrms_export.py` | HRMS-style CSV export with SHA-256 manifest; injects documented noise |
| Staging | `src/ingestion/load_data.py` | Raw, untyped `stg_*` tables |
| Cleaning | `load_data.clean_tables` | Trim/case standardisation, schema-driven typing, mixed date formats, exact-duplicate removal, quarantine of invalid leaf rows |
| Quality gate | `src/ingestion/quality_checks.py`, `validation.py` | 138 rules; any *error* blocks publication |
| Core model | `database/schema.py` | 16 source tables, PK/FK/check constraints, indexes; single-transaction publish |
| Curated mart | `src/ingestion/transform.py` | `mart_employee_month` (lifecycle flags + pro-rated cost) |
| Access | `database/connection.py`, `src/governance/access_control.py` | Read-only engines, role enforcement, SQL validation |
| Analytics | `src/analytics/` | Metrics (single implementation), diagnostics, recruitment, retention, engagement, cost, recommendations |
| Models | `src/models/` | Features, attrition, forecasting, time-to-fill, scenarios, artefact registry (`artifacts/`) |
| NLP | `src/nlp/` | Cleaning, VADER sentiment, themes, TF-IDF, evaluation |
| Assistant | `src/assistant/` | Intent parsing, query templates, answer generation, policy checks |
| Reporting | `src/reporting/` | Monthly / executive report (HTML, Markdown, CSV) |
| UI | `app.py`, `views/`, `components/` | Streamlit app (custom sidebar navigation, cached data access) |

## Pipeline

```
generate ─► export CSV + manifest ─► verify manifest ─► create schema (idempotent) ─► stg_* tables
  ─► clean & cast ─► quality gate ──(errors)──► abort, record FAILED run, previous data untouched
                          │
                          ▼
             publish core tables (one transaction) ─► build mart ─► views + metric dictionary + PG roles
                          ─► post-load row-count reconciliation ─► persist quality results ─► drop staging
                          ─► pipeline_runs (timed stage log, row counts)
```

Every stage is timed and logged (`logs/pipeline.log`, `pipeline_runs.stage_log`).

## Dialect portability

SQL is written once. The few dialect-specific expressions (month bucketing, day differences, quarter labels) come
from helpers in `database/connection.py`; date parameters are normalised per dialect. The full pipeline was run on
both SQLite 3.53 and PostgreSQL 16 and produced identical row counts, quality results and model metrics.

PostgreSQL enforces `VARCHAR(n)` lengths while SQLite does not, so a quality rule checks column lengths before
publication — the PostgreSQL run surfaced (and the rule now prevents) three such issues.

## Read-only and role enforcement

* **SQLite:** read-only engine opens the file with `mode=ro`, sets `PRAGMA query_only=ON` and installs an
  *authorizer* callback that denies every non-read action; for the Viewer it also denies reading any object that is
  not an approved `v_*` view. A progress handler aborts queries after 10 seconds.
* **PostgreSQL:** the pipeline creates `peoplelens_analyst_ro` (SELECT on all tables) and `peoplelens_viewer`
  (SELECT on `v_*` views only) when passwords are provided; read-only sessions also use
  `default_transaction_read_only=on` and `statement_timeout`.

## Application

The four primary modules plus two workspace pages are `st.Page` callables; navigation is drawn under the brand in a
custom sidebar. Analytics calls are wrapped in `st.cache_data` keyed by the filters and a *data version* (latest
pipeline run id + model training time), so caches invalidate after a refresh. Models are never retrained on page
load. If the database is missing (e.g. on free hosting), the app builds it once on first launch.
