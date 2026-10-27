"""Database connectivity, dialect helpers and read-only connections.

PeopleLens supports PostgreSQL (preferred) and SQLite (portable fallback).
Analytical SQL is written once; the few dialect-specific expressions (month
bucketing, date differences) are produced by helpers in this module.

Read-only access is enforced by the database, not only by the UI:

* SQLite: the file is opened with ``mode=ro`` and ``PRAGMA query_only=ON``, and
  an *authorizer* callback denies every non-read operation. For the Viewer
  role the authorizer also denies reads of any object that is not an approved
  aggregate view (``v_*``).
* PostgreSQL: separate login roles (``peoplelens_analyst_ro`` and
  ``peoplelens_viewer``) receive SELECT-only grants (see
  ``database/postgres_roles.sql``); every read-only session also runs with
  ``default_transaction_read_only=on`` and a statement timeout.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from config.settings import get_settings

# --------------------------------------------------------------------- engines


def _configure_sqlite(engine: Engine, foreign_keys: bool = True) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        if foreign_keys:
            cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


@lru_cache(maxsize=8)
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url.startswith("sqlite"):
        path = make_url(url).database
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        _configure_sqlite(engine)
        return engine
    return create_engine(url, future=True, pool_pre_ping=True)


def dialect_name(engine: Engine | None = None) -> str:
    return (engine or get_engine()).dialect.name


# ------------------------------------------------------ read-only connections

_ALLOWED_FUNCTIONS = None  # all SQL functions are permitted (they cannot write)


def _sqlite_authorizer(viewer: bool):
    deny, ok = sqlite3.SQLITE_DENY, sqlite3.SQLITE_OK
    read_actions = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def authorizer(action, arg1, arg2, dbname, source):
        if action in read_actions:
            return ok
        if action == sqlite3.SQLITE_READ:
            if not viewer:
                return ok
            # Viewer: only columns reached through an approved aggregate view.
            if source and source.startswith("v_"):
                return ok
            if arg1 and arg1.startswith("v_"):
                return ok
            if arg1 in {"sqlite_master", "sqlite_schema"}:
                return ok
            return deny
        if action == sqlite3.SQLITE_PRAGMA and arg1 in {"table_info", "table_xinfo"}:
            return ok
        return deny  # INSERT/UPDATE/DELETE/CREATE/DROP/ATTACH/PRAGMA/transactions

    return authorizer


@lru_cache(maxsize=4)
def get_readonly_engine(role: str = "analyst") -> Engine | None:
    """Return an engine whose sessions cannot modify data.

    ``role`` is ``analyst`` (all tables, read-only) or ``viewer`` (approved
    aggregate views only). Returns ``None`` when the configured backend cannot
    enforce the requested role (callers must then refuse the operation).
    """
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite"):
        path = Path(make_url(url).database).resolve()
        ro_url = f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true"
        engine = create_engine(ro_url, future=True)
        viewer = role == "viewer"
        timeout = settings.sql_timeout_seconds

        @event.listens_for(engine, "connect")
        def _ro_connect(dbapi_conn, _record):  # pragma: no cover - exercised in tests
            dbapi_conn.execute("PRAGMA query_only=ON")
            dbapi_conn.set_authorizer(_sqlite_authorizer(viewer))
            # Abort runaway queries: progress handler checks elapsed time.
            start = {"t": None}

            def _progress():
                now = datetime.now().timestamp()
                if start["t"] is None:
                    start["t"] = now
                return 1 if now - start["t"] > timeout else 0

            dbapi_conn.set_progress_handler(_progress, 200_000)
            dbapi_conn._pl_start = start  # type: ignore[attr-defined]

        @event.listens_for(engine, "before_cursor_execute")
        def _reset_timer(conn, *_a):  # pragma: no cover
            raw = conn.connection.dbapi_connection
            if hasattr(raw, "_pl_start"):
                raw._pl_start["t"] = None

        return engine

    # PostgreSQL
    role_url = settings.viewer_database_url if role == "viewer" else settings.analyst_readonly_database_url
    if role == "viewer" and not role_url:
        return None  # cannot enforce viewer restrictions without the viewer login
    role_url = role_url or url
    options = f"-c default_transaction_read_only=on -c statement_timeout={settings.sql_timeout_seconds * 1000}"
    return create_engine(role_url, future=True, pool_pre_ping=True, connect_args={"options": options})


# ------------------------------------------------------------- query helpers


def _normalise_params(params: dict[str, Any] | None, engine: Engine) -> dict[str, Any]:
    if not params:
        return {}
    out = {}
    for k, v in params.items():
        if isinstance(v, datetime):
            v = v.date()
        if isinstance(v, date) and engine.dialect.name == "sqlite":
            v = v.isoformat()
        out[k] = v
    return out


def expand_in(sql: str, params: dict[str, Any], name: str, values: list) -> tuple[str, dict]:
    """Expand ``:name`` into ``(:name_0, :name_1, ...)`` for IN-lists."""
    params = dict(params)
    if not values:
        return sql.replace(f":{name}", "(NULL)"), params
    keys = []
    for i, v in enumerate(values):
        key = f"{name}_{i}"
        params[key] = v
        keys.append(f":{key}")
    return sql.replace(f":{name}", "(" + ", ".join(keys) + ")"), params


def read_sql(sql: str, params: dict[str, Any] | None = None, engine: Engine | None = None,
             parse_dates: list[str] | None = None) -> pd.DataFrame:
    engine = engine or get_engine()
    with engine.connect() as conn:
        df = pd.read_sql_query(text(sql), conn, params=_normalise_params(params, engine))
    for col in parse_dates or []:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def scalar(sql: str, params: dict[str, Any] | None = None, engine: Engine | None = None):
    engine = engine or get_engine()
    with engine.connect() as conn:
        return conn.execute(text(sql), _normalise_params(params, engine)).scalar()


def table_exists(name: str, engine: Engine | None = None) -> bool:
    from sqlalchemy import inspect

    engine = engine or get_engine()
    try:
        return inspect(engine).has_table(name)
    except Exception:  # pragma: no cover - connection problems
        return False


# ------------------------------------------------------- dialect expressions


def month_key(col: str, engine: Engine | None = None) -> str:
    """SQL expression returning 'YYYY-MM' for a date column."""
    if dialect_name(engine) == "sqlite":
        return f"strftime('%Y-%m', {col})"
    return f"to_char({col}, 'YYYY-MM')"


def days_between(later: str, earlier: str, engine: Engine | None = None) -> str:
    """SQL expression for the number of days between two date columns."""
    if dialect_name(engine) == "sqlite":
        return f"(julianday({later}) - julianday({earlier}))"
    return f"({later} - {earlier})"


def quarter_key(col: str, engine: Engine | None = None) -> str:
    if dialect_name(engine) == "sqlite":
        return (
            f"(strftime('%Y', {col}) || '-Q' || ((CAST(strftime('%m', {col}) AS INTEGER) + 2) / 3))"
        )
    return f"(to_char({col}, 'YYYY') || '-Q' || to_char({col}, 'Q'))"
