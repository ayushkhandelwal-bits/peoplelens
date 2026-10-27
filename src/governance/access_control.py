"""Role-based access control and read-only SQL execution.

Two demonstration roles:

* **Analyst** – may query any table read-only, see row-level synthetic records
  (IDs only, never names or salaries in the UI) and employee-level risk scores.
* **Viewer** – aggregate reports and approved aggregate views (``v_*``) only.

Enforcement is layered so that hiding a button is never the only control:

1. ``require(role, permission)`` is called by the server-side functions that
   return row-level data; it raises ``PermissionError`` for the Viewer.
2. Custom SQL is validated (single read-only statement, no dangerous keywords)
   and executed on a **read-only database connection** whose permissions are
   enforced by the database itself (SQLite authorizer + read-only file mode, or
   PostgreSQL SELECT-only roles). For the Viewer, that connection can only read
   the ``v_*`` aggregate views.

Limitation: the demo lets the user pick a role in the sidebar (there is no
login). In a deployment the role would come from the identity provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd
import sqlparse
from sqlalchemy import text

from config.settings import get_settings
from database.connection import get_readonly_engine

ROLES = {
    "Analyst": {"view_aggregates", "view_row_level", "run_custom_sql", "view_risk_scores", "export_data",
                "export_reports", "run_pipeline_checks"},
    "Viewer": {"view_aggregates", "run_custom_sql_views", "export_reports"},
}
PERMISSION_LABELS = {
    "view_aggregates": "Aggregate dashboards and reports",
    "view_row_level": "Row-level synthetic employee records (IDs only)",
    "run_custom_sql": "Custom read-only SQL on all tables",
    "run_custom_sql_views": "Custom read-only SQL on approved aggregate views only",
    "view_risk_scores": "Employee-level attrition risk scores",
    "export_data": "CSV export of analytical tables",
    "export_reports": "Export of aggregate reports",
    "run_pipeline_checks": "Re-run data-quality checks",
}


def can(role: str, permission: str) -> bool:
    return permission in ROLES.get(role, set())


def require(role: str, permission: str) -> None:
    if not can(role, permission):
        raise PermissionError(f"The {role} role is not permitted to: {PERMISSION_LABELS.get(permission, permission)}")


# --------------------------------------------------------------- custom SQL

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|attach|detach|pragma|"
    r"vacuum|reindex|analyze|copy|call|exec|execute|do|set|reset|begin|commit|rollback|savepoint|release|"
    r"lock|listen|notify|load|import|into|returning|pg_sleep|pg_read_file|lo_import|lo_export)\b",
    re.IGNORECASE,
)


@dataclass
class SQLCheck:
    ok: bool
    reason: str = ""
    sql: str = ""


def validate_read_only_sql(sql: str) -> SQLCheck:
    """Static validation; the read-only connection is the actual enforcement."""
    if not sql or not sql.strip():
        return SQLCheck(False, "Enter a query.")
    if len(sql) > 8000:
        return SQLCheck(False, "Query is too long (8,000 characters maximum).")
    stripped = sqlparse.format(sql, strip_comments=True).strip().rstrip(";").strip()
    statements = [s for s in sqlparse.split(stripped) if s.strip()]
    if len(statements) != 1:
        return SQLCheck(False, "Only a single statement is allowed.")
    stmt = sqlparse.parse(statements[0])[0]
    first = stmt.token_first(skip_cm=True, skip_ws=True)
    if first is None or first.normalized.upper() not in {"SELECT", "WITH"}:
        return SQLCheck(False, "Only SELECT (or WITH ... SELECT) queries are allowed.")
    # scan tokens outside string literals for forbidden keywords
    code = re.sub(r"'(?:''|[^'])*'", "''", statements[0])
    m = FORBIDDEN.search(code)
    if m:
        return SQLCheck(False, f"Keyword '{m.group(0).upper()}' is not allowed in the SQL Explorer.")
    return SQLCheck(True, sql=statements[0])


def run_custom_sql(sql: str, role: str) -> pd.DataFrame:
    if not (can(role, "run_custom_sql") or can(role, "run_custom_sql_views")):
        raise PermissionError(f"The {role} role cannot run SQL.")
    check = validate_read_only_sql(sql)
    if not check.ok:
        raise ValueError(check.reason)
    engine = get_readonly_engine("analyst" if can(role, "run_custom_sql") else "viewer")
    if engine is None:
        raise PermissionError("Viewer SQL requires the read-only viewer database login "
                              "(PEOPLELENS_VIEWER_DATABASE_URL); it is not configured.")
    limit = get_settings().sql_row_limit
    wrapped = f"SELECT * FROM ({check.sql}) AS q LIMIT {limit}"
    try:
        with engine.connect() as conn:
            return pd.read_sql_query(text(wrapped), conn)
    except Exception as exc:
        msg = str(getattr(exc, "orig", None) or exc.__cause__ or exc).split("\n")[0]
        if "): " in msg:
            msg = msg.split("): ", 1)[1]  # drop the driver prefix
        if "not authorized" in msg or "permission denied" in msg or "prohibited" in msg:
            raise PermissionError(f"Blocked by the database: {msg}") from exc
        if "interrupted" in msg or "statement timeout" in msg or "canceling statement" in msg:
            raise TimeoutError("Query exceeded the time limit and was cancelled.") from exc
        raise ValueError(msg) from exc
