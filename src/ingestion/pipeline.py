"""End-to-end data pipeline.

    generate -> HRMS export (CSV + manifest) -> verify manifest -> staging tables
    -> cleaning & typing -> quality gate -> transactional publish -> curated mart
    -> aggregate views + metric dictionary -> data-quality results + run log

Each stage is timed and logged; the run (successful or failed) is recorded in
``pipeline_runs`` so the application can show when data was last refreshed.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from config.settings import GENERATED_DIR, LOG_DIR, REPORT_DIR, get_generation_config
from database.connection import get_engine
from database.schema import CORE_TABLES, pipeline_runs
from database.seed import create_schema, create_views, seed_metric_definitions, setup_postgres_roles
from src.ingestion.hrms_export import read_export, write_export
from src.ingestion.load_data import clean_tables, drop_staging, publish, read_staging, stage_raw
from src.ingestion.quality_checks import summarise
from src.ingestion.transform import build_mart_employee_month
from src.ingestion.validation import ValidationError, persist_results, validate_tables, write_report

log = logging.getLogger("peoplelens.pipeline")


def configure_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(h, "_peoplelens", False) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")):
        h.setFormatter(fmt)
        h._peoplelens = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(level)


@dataclass
class PipelineResult:
    run_id: str
    status: str = "RUNNING"
    stages: list[dict] = field(default_factory=list)
    row_counts: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    error: str | None = None


class _Stage:
    def __init__(self, result: PipelineResult, name: str):
        self.result, self.name = result, name

    def __enter__(self):
        self.t0 = time.time()
        log.info("stage start: %s", self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        status = "ok" if exc is None else "failed"
        secs = round(time.time() - self.t0, 2)
        self.result.stages.append({"stage": self.name, "status": status, "seconds": secs,
                                   **({"error": str(exc)[:400]} if exc else {})})
        (log.info if exc is None else log.error)("stage %s: %s (%.1fs)", status, self.name, secs)
        return False


def generate_export(profile: str = "full", seed: int | None = None, out_dir: Path | None = None) -> dict:
    from src.data_generation.simulator import generate

    overrides = {"seed": seed} if seed is not None else {}
    cfg = get_generation_config(profile, **overrides)
    out_dir = out_dir or GENERATED_DIR
    tables, labels = generate(cfg)
    manifest = write_export(tables, labels, cfg, out_dir, profile=profile)
    return manifest


def _record_run(engine: Engine, result: PipelineResult, started: datetime, manifest: dict | None) -> None:
    cfg = (manifest or {}).get("generation_config", {})
    row = dict(run_id=result.run_id, started_at=started, finished_at=datetime.now(), status=result.status,
               profile=(manifest or {}).get("profile") or _profile_of(cfg),
               seed=int(cfg.get("seed", 0)),
               data_start=datetime.fromisoformat(cfg["start_date"]).date() if cfg.get("start_date") else None,
               data_end=datetime.fromisoformat(cfg["end_date"]).date() if cfg.get("end_date") else None,
               stage_log=json.dumps(result.stages), row_counts=json.dumps(result.row_counts))
    try:
        with engine.begin() as conn:
            conn.execute(pipeline_runs.delete().where(pipeline_runs.c.run_id == result.run_id))
            conn.execute(pipeline_runs.insert(), [row])
    except Exception as exc:  # pragma: no cover - schema may be missing after a hard failure
        log.warning("could not record pipeline run: %s", exc)


def _profile_of(cfg: dict) -> str:
    if not cfg:
        return "unknown"
    return "dev" if cfg.get("initial_headcount", 0) < 2000 else "full"


def run_pipeline(profile: str = "full", seed: int | None = None, regenerate: bool = True,
                 reset: bool = False, keep_staging: bool = False, engine: Engine | None = None,
                 out_dir: Path | None = None) -> PipelineResult:
    configure_logging()
    engine = engine or get_engine()
    out_dir = out_dir or GENERATED_DIR
    started = datetime.now()
    result = PipelineResult(run_id=started.strftime("RUN%Y%m%d%H%M%S"))
    manifest = None
    try:
        if regenerate or not (out_dir / "raw" / "manifest.json").exists():
            with _Stage(result, "generate synthetic HRMS export"):
                generate_export(profile, seed, out_dir)
        with _Stage(result, "read & verify export manifest"):
            raw, manifest = read_export(out_dir)
        with _Stage(result, "create schema"):
            create_schema(engine, reset=reset)
        with _Stage(result, "load staging tables"):
            stage_raw(engine, raw)
        with _Stage(result, "clean & cast"):
            staged = read_staging(engine, list(raw))
            cleaned, actions, quarantine = clean_tables(staged)
            if quarantine:
                qdir = out_dir / "quarantine"
                qdir.mkdir(parents=True, exist_ok=True)
                for name, df in quarantine.items():
                    df.to_csv(qdir / f"{name}.csv", index=False)
        with _Stage(result, "quality gate (pre-publish)"):
            results = validate_tables(cleaned)
            result.quality = summarise(results)
            write_report(results, REPORT_DIR / "data_quality_report.md", {"cleaning_actions": actions})
            if result.quality["blocking"]:
                raise ValidationError(results)
        with _Stage(result, "publish core tables (single transaction)"):
            result.row_counts = publish(engine, cleaned, [n for n in CORE_TABLES if n in cleaned])
        with _Stage(result, "build curated mart"):
            mart = build_mart_employee_month(cleaned)
            result.row_counts.update(publish(engine, {"mart_employee_month": mart}, ["mart_employee_month"]))
        with _Stage(result, "views, metric dictionary, roles"):
            create_views(engine)
            result.row_counts["metric_definitions"] = seed_metric_definitions(engine)
            roles = setup_postgres_roles(engine)
            if roles:
                log.info("PostgreSQL read-only roles refreshed: %s", ", ".join(roles))
        with _Stage(result, "post-load reconciliation"):
            with engine.connect() as conn:
                for name, n in result.row_counts.items():
                    db_n = conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
                    if db_n != n:
                        raise RuntimeError(f"{name}: {db_n} rows in database, {n} published")
            persist_results(engine, result.run_id, results)
        if not keep_staging:
            drop_staging(engine, list(raw))
        result.status = "SUCCESS"
    except Exception as exc:
        result.status = "FAILED"
        result.error = f"{type(exc).__name__}: {exc}"
        log.error("pipeline failed: %s", result.error)
        _record_run(engine, result, started, manifest)
        raise
    _record_run(engine, result, started, manifest)
    log.info("pipeline finished: %s rows in %d tables", f"{sum(result.row_counts.values()):,}",
             len(result.row_counts))
    return result
