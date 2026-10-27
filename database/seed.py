"""Schema creation, aggregate views, reference seeding and PostgreSQL roles.

All operations are idempotent: re-running them yields the same schema.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex, CreateTable

from database.schema import INDEXES, VIEWER_VIEWS, metadata, metric_definitions
from src.governance.metric_dictionary import as_records

log = logging.getLogger(__name__)


def drop_views(engine: Engine) -> None:
    with engine.begin() as conn:
        for name in VIEWER_VIEWS:
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))


def create_schema(engine: Engine, reset: bool = False) -> None:
    """Create all tables and indexes (``reset`` drops everything first)."""
    drop_views(engine)
    if reset:
        with engine.begin() as conn:
            if engine.dialect.name == "postgresql":
                for t in reversed(metadata.sorted_tables):
                    conn.execute(text(f"DROP TABLE IF EXISTS {t.name} CASCADE"))
            else:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                for t in reversed(metadata.sorted_tables):
                    conn.execute(text(f"DROP TABLE IF EXISTS {t.name}"))
    metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        for idx in INDEXES:
            idx.create(conn, checkfirst=True)
    log.info("schema ready (%d tables)", len(metadata.sorted_tables))


def create_views(engine: Engine) -> None:
    drop_views(engine)
    with engine.begin() as conn:
        for name, sql in VIEWER_VIEWS.items():
            conn.execute(text(f"CREATE VIEW {name} AS {sql}"))


def seed_metric_definitions(engine: Engine) -> int:
    rows = as_records()
    with engine.begin() as conn:
        conn.execute(metric_definitions.delete())
        conn.execute(metric_definitions.insert(), rows)
    return len(rows)


def emit_ddl(path: Path, dialect: str = "postgresql") -> None:
    """Write the DDL generated from the metadata (keeps schema.sql in sync)."""
    from sqlalchemy.dialects import postgresql, sqlite

    d = postgresql.dialect() if dialect == "postgresql" else sqlite.dialect()
    parts = [f"-- PeopleLens schema ({dialect}). Generated from database/schema.py — do not edit by hand.\n"]
    for table in metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=d)).strip() + ";\n")
    for idx in INDEXES:
        parts.append(str(CreateIndex(idx).compile(dialect=d)).strip() + ";\n")
    for name, sql in VIEWER_VIEWS.items():
        body = re.sub(r"\n\s+", "\n    ", sql.strip())
        parts.append(f"CREATE VIEW {name} AS\n    {body};\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def setup_postgres_roles(engine: Engine) -> list[str]:
    """Create/refresh read-only login roles. Passwords come from environment
    variables; nothing is created if they are not set."""
    if engine.dialect.name != "postgresql":
        return []
    done = []
    roles = {
        "peoplelens_analyst_ro": os.environ.get("PEOPLELENS_ANALYST_PASSWORD"),
        "peoplelens_viewer": os.environ.get("PEOPLELENS_VIEWER_PASSWORD"),
    }
    with engine.begin() as conn:
        db = conn.execute(text("SELECT current_database()")).scalar()
        for role, pwd in roles.items():
            if not pwd:
                continue
            exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar()
            verb = "ALTER" if exists else "CREATE"
            literal = "'" + pwd.replace("'", "''") + "'"  # utility statements cannot bind parameters
            conn.execute(text(f"{verb} ROLE {role} LOGIN PASSWORD {literal} NOSUPERUSER NOCREATEDB NOCREATEROLE"))
            conn.execute(text(f'GRANT CONNECT ON DATABASE "{db}" TO {role}'))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            conn.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}"))
            conn.execute(text(f"ALTER ROLE {role} SET default_transaction_read_only = on"))
            done.append(role)
        if roles["peoplelens_analyst_ro"]:
            conn.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO peoplelens_analyst_ro"))
        if roles["peoplelens_viewer"]:
            for view in VIEWER_VIEWS:
                conn.execute(text(f"GRANT SELECT ON {view} TO peoplelens_viewer"))
    return done
