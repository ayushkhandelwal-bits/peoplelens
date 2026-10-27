"""Pipeline: staging, cleaning, the quality gate and transactional publication."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import inspect, text

from database.connection import get_engine
from src.ingestion.hrms_export import ManifestError, read_export
from src.ingestion.load_data import clean_tables
from src.ingestion.quality_checks import run_all


def test_pipeline_succeeds_and_passes_quality(built_db):
    r = built_db["pipeline"]
    assert r.status == "SUCCESS"
    assert r.quality["failed"] == 0 and not r.quality["blocking"]
    assert r.row_counts["employees"] > 900


def test_staging_tables_dropped_and_mart_built(engine):
    names = set(inspect(engine).get_table_names())
    assert not any(n.startswith("stg_") for n in names)
    with engine.connect() as c:
        snap = c.execute(text("SELECT COUNT(*) FROM monthly_workforce_snapshots")).scalar()
        mart = c.execute(text("SELECT COUNT(*) FROM mart_employee_month")).scalar()
        runs = c.execute(text("SELECT status FROM pipeline_runs ORDER BY started_at DESC")).scalar()
    assert snap == mart and runs == "SUCCESS"


def test_cleaning_fixes_injected_noise(built_db):
    from config.settings import GENERATED_DIR

    raw, manifest = read_export(GENERATED_DIR)
    cleaned, actions, quarantine = clean_tables(raw)
    kinds = {a["action"] for a in actions}
    assert "remove exact duplicate rows" in kinds
    assert "parse DD/MM/YYYY dates" in kinds
    assert set(cleaned["employees"].current_status) == {"Active", "Exited"}
    assert "training_records" in quarantine and (quarantine["training_records"].training_cost < 0).all()
    assert manifest["noise_injected"]


def test_manifest_detects_tampering(built_db, tmp_path):
    import shutil

    from config.settings import GENERATED_DIR

    shutil.copytree(GENERATED_DIR / "raw", tmp_path / "raw")
    with open(tmp_path / "raw" / "employees.csv", "a", encoding="utf-8") as fh:
        fh.write("tampered\n")
    with pytest.raises(ManifestError):
        read_export(tmp_path)


def _clean_db_tables(engine):
    from src.ingestion.validation import load_tables_from_db

    return load_tables_from_db(engine)


@pytest.mark.parametrize("corruption,expected", [
    ("dup_pk", "Duplicate primary keys"),
    ("orphan_fk", "Missing foreign-key references"),
    ("exit_before_join", "Invalid dates"),
    ("snapshot_drop", "Inconsistent headcount reconciliation"),
    ("self_manager", "Impossible manager relationships"),
    ("negative_pay", "Negative amounts"),
])
def test_quality_checks_report_failures(engine, corruption, expected):
    t = {k: v.copy() for k, v in _clean_db_tables(engine).items()}
    if corruption == "dup_pk":
        t["employees"] = pd.concat([t["employees"], t["employees"].head(1)])
    elif corruption == "orphan_fk":
        t["job_history"].loc[t["job_history"].index[0], "department_id"] = "D99"
    elif corruption == "exit_before_join":
        i = t["employees"][t["employees"].exit_date.notna()].index[0]
        t["employees"].loc[i, "exit_date"] = t["employees"].loc[i, "joining_date"] - pd.Timedelta(days=5)
    elif corruption == "snapshot_drop":
        s = t["monthly_workforce_snapshots"]
        t["monthly_workforce_snapshots"] = s.drop(s[s.active_flag == 1].index[:3])
    elif corruption == "self_manager":
        e = t["employees"]
        i = e[e.current_manager_id.notna()].index[0]
        e.loc[i, "current_manager_id"] = e.loc[i, "employee_id"]
    elif corruption == "negative_pay":
        t["compensation_history"].loc[t["compensation_history"].index[0], "annual_base_salary"] = -1
    failed = {r.category for r in run_all(t) if r.status == "FAIL"}
    assert expected in failed


def test_failed_gate_does_not_overwrite_published_data(engine, monkeypatch):
    """A blocking failure must raise before publication; existing data stays intact."""
    from src.ingestion import pipeline as pl
    from src.ingestion.validation import ValidationError

    with engine.connect() as c:
        before = c.execute(text("SELECT COUNT(*) FROM employees")).scalar()

    def corrupt(tables):
        tables["employees"] = pd.concat([tables["employees"], tables["employees"].head(2)])
        return run_all(tables)

    monkeypatch.setattr(pl, "validate_tables", corrupt)
    with pytest.raises(ValidationError):
        pl.run_pipeline("dev", regenerate=False)
    with get_engine().connect() as c:
        after = c.execute(text("SELECT COUNT(*) FROM employees")).scalar()
        status = c.execute(text("SELECT status FROM pipeline_runs ORDER BY started_at DESC")).scalar()
    assert after == before and status == "FAILED"
    # restore a successful latest run for the remaining tests
    monkeypatch.undo()
    assert pl.run_pipeline("dev", regenerate=False).status == "SUCCESS"


def test_committed_dataset_is_intact():
    """The dataset kept in Git must match its manifest byte for byte (checksums and row counts)."""
    from config.settings import DATASET_DIR
    from src.ingestion.hrms_export import read_export

    if not (DATASET_DIR / "raw" / "manifest.json").exists():
        pytest.skip("no committed dataset in this checkout")
    tables, manifest = read_export(DATASET_DIR)
    assert manifest["profile"] == "full" and manifest["generation_config"]["seed"] == 42
    assert len(tables) == 16 and (DATASET_DIR / "feedback_labels.csv").exists()


def test_pipeline_uses_committed_dataset_when_profile_matches(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    from src.ingestion import pipeline

    ds = tmp_path / "dataset"
    pipeline.generate_export("dev", None, ds)
    monkeypatch.setattr(pipeline, "DATASET_DIR", ds)
    called = []
    monkeypatch.setattr(pipeline, "generate_export", lambda *a, **k: called.append(a))
    eng = create_engine(f"sqlite:///{(tmp_path / 'ds.db').as_posix()}")
    r = pipeline.run_pipeline("dev", engine=eng)
    assert r.status == "SUCCESS" and not called
    assert not (ds / "quarantine").exists()  # nothing is written into the committed folder
    # --regenerate bypasses the dataset and calls the simulator (stubbed here, so that run stops early)
    from src.ingestion.hrms_export import ManifestError

    with pytest.raises(ManifestError):
        pipeline.run_pipeline("dev", engine=eng, use_dataset=False, out_dir=tmp_path / "gen")
    assert len(called) == 1
