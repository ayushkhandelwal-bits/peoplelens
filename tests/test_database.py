"""Schema, read-only enforcement and the SQL Explorer safety layer."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from database.connection import get_readonly_engine
from database.schema import CORE_TABLES, VIEWER_VIEWS
from src.governance.access_control import can, require, run_custom_sql, validate_read_only_sql


def test_all_tables_and_views_exist(engine):
    insp = inspect(engine)
    names = set(insp.get_table_names())
    assert set(CORE_TABLES) <= names
    assert set(VIEWER_VIEWS) <= set(insp.get_view_names())


def test_foreign_keys_declared(engine):
    fks = inspect(engine).get_foreign_keys("job_history")
    assert {fk["referred_table"] for fk in fks} >= {"employees", "departments", "locations"}


@pytest.mark.parametrize("sql", [
    "DELETE FROM employees",
    "UPDATE employees SET job_level = 'L9'",
    "DROP TABLE employees",
    "SELECT 1; DROP TABLE employees",
    "INSERT INTO locations VALUES ('X','X','X','X')",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'x.db' AS x",
    "WITH x AS (SELECT 1) DELETE FROM employees",
])
def test_validator_rejects_writes(sql):
    assert not validate_read_only_sql(sql).ok


@pytest.mark.parametrize("sql", [
    "SELECT * FROM employees",
    "  select count(*) from departments -- comment",
    "WITH t AS (SELECT job_level FROM employees) SELECT job_level, COUNT(*) FROM t GROUP BY 1",
    "SELECT 'update' AS word",  # keywords inside string literals are fine
])
def test_validator_allows_reads(sql):
    assert validate_read_only_sql(sql).ok


def test_readonly_connection_blocks_writes_at_database_level(engine):
    ro = get_readonly_engine("analyst")
    with ro.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM employees")).scalar() > 0
        with pytest.raises(Exception):
            c.execute(text("DELETE FROM employees"))
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM employees")).scalar() > 0


def test_viewer_limited_to_aggregate_views(engine):
    df = run_custom_sql("SELECT * FROM v_headcount_monthly LIMIT 5", "Viewer")
    assert len(df) == 5 and "employee_id" not in df.columns
    with pytest.raises(PermissionError):
        run_custom_sql("SELECT employee_id FROM employees", "Viewer")
    with pytest.raises(PermissionError):
        run_custom_sql("SELECT * FROM compensation_history", "Viewer")


def test_analyst_can_query_and_rows_are_capped(engine):
    df = run_custom_sql("SELECT * FROM monthly_workforce_snapshots", "Analyst")
    assert len(df) == 5000


def test_role_permissions():
    assert can("Analyst", "view_row_level") and not can("Viewer", "view_row_level")
    with pytest.raises(PermissionError):
        require("Viewer", "view_risk_scores")
