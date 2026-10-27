"""Validate the current database and project artefacts.

Runs every data-quality rule against the live database, stores the results
(shown in the app), and performs smoke checks of metrics, models and reports.

    python scripts/validate_project.py
"""

import sys

import _bootstrap  # noqa: F401

from config.settings import REPORT_DIR
from database.connection import get_engine, table_exists
from src.ingestion.quality_checks import summarise
from src.ingestion.validation import load_tables_from_db, persist_results, validate_tables, write_report


def main() -> int:
    engine = get_engine()
    if not table_exists("mart_employee_month", engine):
        print("Database not built yet. Run: python scripts/run_pipeline.py")
        return 1
    results = validate_tables(load_tables_from_db(engine))
    persist_results(engine, "VALIDATE", results)
    write_report(results, REPORT_DIR / "data_quality_report.md")
    summary = summarise(results)
    print(f"Data quality: {summary}")
    for r in results:
        if r.status != "PASS":
            print(f"  {r.status} {r.table_name}: {r.check_name} ({r.failed_rows}) {r.details}")

    from src.analytics.common import Filters, data_bounds
    from src.analytics.metrics import kpi_summary
    from src.models.model_evaluation import models_available

    lo, hi = data_bounds(engine)
    k = kpi_summary(Filters(start=max(lo, hi.replace(day=1)), end=hi), engine)
    print(f"KPI smoke test: headcount {k.active_headcount:,} at {hi}, attrition in {hi:%b %Y} alone (annualised) {k.attrition_rate:.1%}")
    print(f"Model artefacts present: {models_available()}")
    return 1 if summary["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
