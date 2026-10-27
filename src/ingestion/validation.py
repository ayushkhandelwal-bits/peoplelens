"""Run the quality rules, persist results and produce a readable report."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from database.schema import CORE_TABLES, data_quality_results
from src.ingestion.load_data import clean_tables
from src.ingestion.quality_checks import CheckResult, run_all, summarise


class ValidationError(RuntimeError):
    def __init__(self, results: list[CheckResult]):
        failed = [r for r in results if r.status == "FAIL"]
        msg = "; ".join(f"{r.table_name}: {r.check_name} ({r.failed_rows})" for r in failed[:8])
        super().__init__(f"{len(failed)} blocking data-quality check(s) failed: {msg}")
        self.results = results


def validate_tables(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    return run_all(tables)


def load_tables_from_db(engine: Engine) -> dict[str, pd.DataFrame]:
    raw = {}
    with engine.connect() as conn:
        for name in CORE_TABLES:
            raw[name] = pd.read_sql_query(text(f"SELECT * FROM {name}"), conn)
    for df in raw.values():  # align with the cleaned (string-typed) representation
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].astype("string")
    cleaned, _, _ = clean_tables(raw)
    return cleaned


def persist_results(engine: Engine, run_id: str, results: list[CheckResult]) -> None:
    now = datetime.now()
    rows = [dict(run_id=run_id, checked_at=now, **{k: v for k, v in r.to_dict().items()}) for r in results]
    with engine.begin() as conn:
        conn.execute(data_quality_results.delete())
        if rows:
            conn.execute(data_quality_results.insert(), rows)


def write_report(results: list[CheckResult], path: Path, extra: dict | None = None) -> None:
    summary = summarise(results)
    lines = ["# Data-quality report", "", f"Generated: {datetime.now():%Y-%m-%d %H:%M}", "",
             f"**{summary['passed']} passed, {summary['warnings']} warnings, {summary['failed']} failed** "
             f"of {summary['checks']} checks.", ""]
    if extra:
        lines += ["## Cleaning actions", "", "| Table | Action | Column | Rows |", "|---|---|---|---:|"]
        for a in extra.get("cleaning_actions", []):
            lines.append(f"| {a['table']} | {a['action']} | {a['column']} | {a['rows']} |")
        lines.append("")
    lines += ["## Checks", "", "| Status | Category | Table | Check | Failed rows | Details |",
              "|---|---|---|---|---:|---|"]
    for r in sorted(results, key=lambda r: (r.status != "FAIL", r.status != "WARN", r.category)):
        lines.append(f"| {r.status} | {r.category} | {r.table_name} | {r.check_name} | {r.failed_rows} | "
                     f"{r.details} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps({"summary": summary, "results": [r.to_dict() for r in results]},
                                                    indent=2), encoding="utf-8")
