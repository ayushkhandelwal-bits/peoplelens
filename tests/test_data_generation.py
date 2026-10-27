"""Synthetic data generation: size, reproducibility and internal consistency."""

from __future__ import annotations

import hashlib

import pandas as pd

from config.settings import get_generation_config
from src.data_generation.simulator import generate
from src.ingestion.load_data import clean_tables
from src.ingestion.quality_checks import run_all


def _digest(tables: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for name in sorted(tables):
        h.update(name.encode())
        h.update(pd.util.hash_pandas_object(tables[name].astype(str), index=False).values.tobytes())
    return h.hexdigest()


def _typed(tables):
    raw = {k: v.astype(str).replace({"None": None, "nan": None, "NaT": None}) for k, v in tables.items()}
    cleaned, _, _ = clean_tables(raw)
    return cleaned


def test_initial_headcount_matches_config(dev_tables):
    tables, _ = dev_tables
    cfg = get_generation_config("dev")
    e = tables["employees"]
    start = pd.Timestamp(cfg.start_date)
    joined_before = pd.to_datetime(e.joining_date) < start
    # initial workforce + a handful of pipeline hires that join exactly on day one
    assert joined_before.sum() == cfg.initial_headcount


def test_fixed_seed_is_reproducible(dev_tables):
    tables, _ = dev_tables
    again, _ = generate(get_generation_config("dev"))
    assert _digest(tables) == _digest(again)


def test_different_seed_changes_data(dev_tables):
    tables, _ = dev_tables
    other, _ = generate(get_generation_config("dev", seed=7))
    assert _digest(tables) != _digest(other)


def test_generated_data_passes_every_quality_rule(dev_tables):
    tables, _ = dev_tables
    results = run_all(_typed(tables))
    failed = [(r.table_name, r.check_name, r.failed_rows) for r in results if r.status == "FAIL"]
    assert not failed, failed


def test_lifecycle_dates_and_ids(dev_tables):
    tables, _ = dev_tables
    e = tables["employees"]
    assert e.employee_id.is_unique and e.employee_id.str.match(r"^EMP\d{5}$").all()
    x, j = pd.to_datetime(e.exit_date), pd.to_datetime(e.joining_date)
    assert (x.isna() | (x > j)).all()
    assert ((e.current_status == "Exited") == x.notna()).all()


def test_candidates_join_only_with_joining_event(dev_tables):
    tables, _ = dev_tables
    c, ev = tables["candidates"], tables["recruitment_events"]
    joined = set(ev[(ev.event_type == "Joining") & (ev.event_status == "Joined")].candidate_id)
    linked = set(c[c.associated_employee_id.notna()].candidate_id)
    assert linked == joined


def test_hires_in_window_come_from_recruitment(dev_tables):
    tables, _ = dev_tables
    cfg = get_generation_config("dev")
    e = tables["employees"]
    hires = set(e[pd.to_datetime(e.joining_date) >= pd.Timestamp(cfg.start_date)].employee_id)
    sourced = set(tables["candidates"].associated_employee_id.dropna())
    assert hires == sourced


def test_monthly_snapshots_reconcile(dev_tables):
    tables, _ = dev_tables
    s = tables["monthly_workforce_snapshots"]
    e = tables["employees"]
    s["snapshot_date"] = pd.to_datetime(s.snapshot_date)
    hc = s.groupby("snapshot_date").active_flag.sum()
    j, x = pd.to_datetime(e.joining_date), pd.to_datetime(e.exit_date)
    months = sorted(hc.index)
    for prev, cur in zip(months[:-1], months[1:]):
        joins = ((j > prev) & (j <= cur)).sum()
        exits = ((x > prev) & (x <= cur)).sum()
        assert hc[cur] == hc[prev] + joins - exits


def test_relationships_are_not_perfectly_predictive(dev_tables):
    """Attrition must not be deterministic: leavers and stayers overlap in engagement."""
    tables, _ = dev_tables
    sv = tables["engagement_surveys"].merge(tables["employees"][["employee_id", "exit_type"]], on="employee_id")
    leavers = sv[sv.exit_type == "Voluntary"].engagement_score
    stayers = sv[sv.exit_type.isna()].engagement_score
    assert leavers.mean() < stayers.mean()  # a real signal exists...
    assert leavers.quantile(0.75) > stayers.quantile(0.25)  # ...but distributions overlap heavily


def test_feedback_has_no_employee_identifier(dev_tables):
    tables, labels = dev_tables
    assert "employee_id" not in tables["survey_feedback"].columns
    assert set(labels.intended_sentiment) >= {"positive", "negative", "neutral", "mixed"}
