# PeopleLens dataset (synthetic)

> **All records are synthetic.** They were produced by the PeopleLens workforce simulator for a fictional
> organisation. No real person, employer or confidential data is included.

This folder is the dataset the whole project is built on. It is an **HRMS-style export**: one CSV per table plus a
manifest with row counts and SHA-256 checksums. The pipeline verifies the checksums, cleans and validates the data
(138 quality rules) and loads it into PostgreSQL or SQLite — the dashboards, models and documentation figures all come
from these files.

* **Organisation:** 5,400 starting employees, 25 departments, 7 cities
* **Period:** 2023-10-01 to 2026-09-30 (36 months), simulated month by month
* **Seed:** 42 — re-running the simulator with this seed reproduces these files exactly
* **Total rows:** 544,861

| File | Rows | Contents |
|---|---:|---|
| `locations.csv` | 7 | Office cities (7) |
| `departments.csv` | 25 | Departments with business unit and head (25) |
| `employees.csv` | 9,336 | One row per person ever employed: demographics, hire and exit details |
| `job_history.csv` | 21,987 | Effective-dated job records: department, title, level, location, manager |
| `manager_relationships.csv` | 13,706 | Effective-dated reporting lines |
| `job_requisitions.csv` | 4,651 | Approved positions: opened, filled or cancelled dates, reason |
| `candidates.csv` | 43,862 | Applicants with source, furthest stage reached and outcome |
| `recruitment_events.csv` | 106,022 | Each candidate's stage events (applied → screened → … → joined) |
| `employee_events.csv` | 20,219 | Lifecycle events: hire, promotion, transfer, manager change, exit |
| `performance_reviews.csv` | 20,355 | Annual ratings and calibration |
| `engagement_surveys.csv` | 37,896 | Quarterly survey responses (engagement, manager support, career growth, eNPS) |
| `survey_feedback.csv` | 12,683 | Free-text survey comments — no employee ID (department, wave, tenure band only) |
| `compensation_history.csv` | 25,147 | Effective-dated base salary, variable pay and benefits |
| `training_records.csv` | 23,676 | Training programmes, hours, cost and completion |
| `monthly_workforce_snapshots.csv` | 204,239 | Month-end headcount snapshot per employee |
| `workforce_budget.csv` | 1,050 | Planned headcount, hires and cost per department and month (the plan) |

`feedback_labels.csv` holds the simulator's intended sentiment and theme for each comment. It is **not loaded into
the database**; it is used only to evaluate the NLP honestly.

## Deliberately imperfect

Like a real export, the raw files contain issues the pipeline must handle: stray spaces and inconsistent letter case,
duplicate rows, a few invalid values (e.g. negative training costs, which are quarantined), missing values where a
process was incomplete, and right-censoring at the extraction date (open requisitions, offers not yet joined). See
`manifest.json` → `noise_injected` for exactly what was added.

## Using it

* **Explore:** open any CSV in Excel, pandas or a SQL tool. Column definitions: [`docs/data_dictionary.md`](../../docs/data_dictionary.md).
* **Build the database:** `python scripts/run_pipeline.py` (uses this folder automatically).
* **Regenerate from code:** `python scripts/generate_data.py --publish` rewrites this folder; with seed 42 the files
  are byte-identical. `python scripts/run_pipeline.py --regenerate` simulates afresh without touching this folder.
* **Do not edit the CSVs by hand** — the checksum check will (correctly) refuse a modified export.
