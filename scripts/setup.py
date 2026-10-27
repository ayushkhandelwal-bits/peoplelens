"""Database setup utilities.

    python scripts/setup.py --create          # create tables, indexes and views (idempotent)
    python scripts/setup.py --reset           # drop and recreate everything (data is lost)
    python scripts/setup.py --emit-ddl        # regenerate database/schema.sql from database/schema.py
    python scripts/setup.py --roles           # (PostgreSQL) create/refresh read-only roles from env passwords
"""

import argparse

import _bootstrap  # noqa: F401

from config.settings import PROJECT_ROOT
from database.connection import get_engine
from database.seed import create_schema, create_views, emit_ddl, seed_metric_definitions, setup_postgres_roles


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--create", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--emit-ddl", action="store_true")
    p.add_argument("--roles", action="store_true")
    args = p.parse_args()
    engine = get_engine()
    if args.emit_ddl:
        emit_ddl(PROJECT_ROOT / "database" / "schema.sql", "postgresql")
        emit_ddl(PROJECT_ROOT / "database" / "schema_sqlite.sql", "sqlite")
        print("wrote database/schema.sql and database/schema_sqlite.sql")
    if args.create or args.reset:
        create_schema(engine, reset=args.reset)
        create_views(engine)
        seed_metric_definitions(engine)
        print(f"schema ready on {engine.url.render_as_string(hide_password=True)}")
    if args.roles:
        print("roles:", setup_postgres_roles(engine) or "none (not PostgreSQL or passwords not set)")


if __name__ == "__main__":
    main()
