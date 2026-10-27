# Data governance

## Provenance
All records are produced by the seeded simulator in `src/data_generation/` and exported as a simulated HRMS extract
(`data/dataset/raw/*.csv` + `manifest.json` with row counts and SHA-256 checksums), published in the repository. Ingestion refuses an export
whose checksums or row counts do not match. No real HRMS (or any external system) is connected; no real personal data
is used. The export is version-controlled (stored byte-for-byte, no line-ending conversion) and reproducible from seed 42;
the derived database and model artefacts are rebuilt rather than committed.

## Ownership (assumed for the fictional organisation)

| Domain | Tables | Owner |
|---|---|---|
| Core HR | employees, departments, locations, job_history, manager_relationships, employee_events, monthly_workforce_snapshots | HR Operations |
| Talent acquisition | job_requisitions, candidates, recruitment_events | Talent Acquisition |
| Performance & learning | performance_reviews, training_records | Talent Management |
| Listening | engagement_surveys, survey_feedback | Employee Experience |
| Reward | compensation_history | Total Rewards |
| Planning | workforce_budget | Finance (FP&A) |
| Analytical layer | mart_employee_month, metric_definitions, models | People Analytics |

## Metric definitions
One dictionary (`src/governance/metric_dictionary.py`) → `metric_definitions` table, in-app glossary, KPI tooltips
and `docs/metric_definitions.md`. Each KPI has one implementation in `src/analytics/metrics.py`.

## Data-quality rules
138 rules in `src/ingestion/quality_checks.py`: primary keys, foreign keys, required columns, column lengths,
effective-dating (no overlaps/gaps; history spans joining → exit), lifecycle events (one hire, one exit for leavers,
nothing outside employment), recruitment sequences (no skipped stages, chronological dates, joined candidates ↔
employees), reporting lines (no self-management, no cycles, manager employed and more senior each month), snapshot
reconciliation (point-in-time, flow, department flow including transfers), amounts and ranges, activity within
employment, duplicates, plausibility. *Errors* block publication; *warnings* are reported. Results are stored in
`data_quality_results` and shown in the app; the cleaning log and a Markdown report are in `reports/`.

## Access permissions

| Permission | Analyst | Viewer |
|---|:-:|:-:|
| Aggregate dashboards and reports, report export | ✓ | ✓ |
| Custom read-only SQL on all tables | ✓ | — |
| Custom read-only SQL on aggregate views (`v_*`) | — | ✓ |
| Row-level synthetic records (IDs only) | ✓ | — |
| Employee-level risk scores | ✓ | — |
| CSV export of analytical tables, re-run quality checks | ✓ | — |

Enforcement: server-side `require()` checks for row-level functions; user SQL is statically validated and then
executed on a **read-only connection whose limits are enforced by the database** (SQLite: read-only file +
`query_only` + authorizer; Viewer can read only `v_*` views. PostgreSQL: `peoplelens_analyst_ro` and
`peoplelens_viewer` SELECT-only roles, read-only transactions, statement timeout). Only the pipeline writes.
**Limitation:** the demo selects the role in the sidebar; production would map identities from an identity provider.

## Privacy safeguards
* Synthetic IDs in the UI; names, individual salaries and ratings are never displayed.
* Survey comments are stored without employee IDs (department, wave, tenure band only).
* Survey and comment aggregates under 10 responses are suppressed.
* Protected attributes (gender) and names are excluded from models; gender appears only as workforce composition.
* Predictions describe groups; the product refuses requests for firing/disciplinary decisions and individual-level
  sensitive data.

## Model governance
Temporal validation, baseline comparisons, leakage tests, stored assumptions/limitations per artefact, deliberate
retraining. See `modelling_methodology.md`.

## Retention and deletion (assumed policy)
Raw exports are transient and regenerated; staging tables are dropped after each successful load; quarantined rows are
kept in `data/generated/quarantine/` for audit; every pipeline run rebuilds the database from the source; run logs keep
the history of refreshes.
