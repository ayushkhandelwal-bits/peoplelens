"""Staging, cleaning and transactional publication of the source tables.

Flow: raw export (CSV, all text) -> ``stg_*`` staging tables -> cleaning and
type casting driven by the schema -> quality gate -> single-transaction
replacement of the core tables. If anything fails before commit the previously
published data remains untouched.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, text
from sqlalchemy.engine import Engine

from database.schema import CORE_TABLES, metadata

log = logging.getLogger(__name__)

CANONICAL = {
    ("employees", "current_status"): ["Active", "Exited"],
    ("employees", "employment_type"): ["Full-time", "Contract"],
    ("job_history", "employment_type"): ["Full-time", "Contract"],
    ("monthly_workforce_snapshots", "employment_type"): ["Full-time", "Contract"],
}
_DMY = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def stage_raw(engine: Engine, raw: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Land the raw export, untyped, in ``stg_<table>`` tables."""
    counts = {}
    for name, df in raw.items():
        df.to_sql(f"stg_{name}", engine, if_exists="replace", index=False, chunksize=20_000)
        counts[name] = len(df)
    return counts


def read_staging(engine: Engine, names: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    with engine.connect() as conn:
        for name in names:
            out[name] = pd.read_sql_query(text(f"SELECT * FROM stg_{name}"), conn)
    return out


def drop_staging(engine: Engine, names: list[str]) -> None:
    with engine.begin() as conn:
        for name in names:
            conn.execute(text(f"DROP TABLE IF EXISTS stg_{name}"))


def _parse_dates(s: pd.Series) -> tuple[pd.Series, int]:
    s = s.astype("string").str.strip()
    dmy = s.str.match(_DMY.pattern).fillna(False)
    out = pd.to_datetime(s.where(~dmy), format="%Y-%m-%d", errors="coerce")
    if dmy.any():
        out = out.where(~dmy, pd.to_datetime(s.where(dmy), format="%d/%m/%Y", errors="coerce"))
    return out, int(dmy.sum())


def clean_tables(raw: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    """Standardise text, cast types, remove exact duplicates and quarantine
    rows that break business rules in leaf tables."""
    cleaned, actions, quarantine = {}, [], {}
    for table in metadata.sorted_tables:
        name = table.name
        if name not in raw:
            continue
        df = raw[name].copy()
        # 1. text standardisation
        for col in df.columns:
            if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
                before = df[col].copy()
                df[col] = df[col].astype("string").str.strip().replace({"": pd.NA})
                changed = int((before.astype("string").fillna("<NA>") != df[col].fillna("<NA>")).sum())
                if changed:
                    actions.append(dict(table=name, action="trim whitespace", column=col, rows=changed))
        for (tname, col), allowed in CANONICAL.items():
            if tname == name and col in df:
                lookup = {a.lower(): a for a in allowed}
                fixed = df[col].str.lower().map(lookup)
                changed = int((fixed.notna() & (fixed != df[col])).sum())
                df[col] = fixed.fillna(df[col])
                if changed:
                    actions.append(dict(table=name, action="standardise category case", column=col, rows=changed))
        # 2. type casting from the schema
        for col in table.columns:
            if col.name not in df:
                continue
            if isinstance(col.type, DateTime):
                df[col.name] = pd.to_datetime(df[col.name], errors="coerce")
            elif isinstance(col.type, Date):
                df[col.name], n_dmy = _parse_dates(df[col.name])
                if n_dmy:
                    actions.append(dict(table=name, action="parse DD/MM/YYYY dates", column=col.name, rows=n_dmy))
            elif isinstance(col.type, (Integer, Boolean)):
                df[col.name] = pd.to_numeric(df[col.name], errors="coerce").astype("Int64")
            elif isinstance(col.type, Float):
                df[col.name] = pd.to_numeric(df[col.name], errors="coerce")
        # 3. exact duplicate rows
        dups = int(df.duplicated().sum())
        if dups:
            df = df.drop_duplicates().reset_index(drop=True)
            actions.append(dict(table=name, action="remove exact duplicate rows", column="*", rows=dups))
        # 4. quarantine (leaf tables only: nothing references these rows)
        if name == "training_records":
            bad = df.training_cost < 0
            if bad.any():
                quarantine[name] = df[bad].copy()
                df = df[~bad].reset_index(drop=True)
                actions.append(dict(table=name, action="quarantine negative training cost", column="training_cost",
                                    rows=int(bad.sum())))
        cleaned[name] = df
    return cleaned, actions, quarantine


def _records(df: pd.DataFrame, table) -> list[dict]:
    out = df.copy()
    for col in table.columns:
        if col.name not in out:
            continue
        if isinstance(col.type, DateTime):
            out[col.name] = out[col.name].map(lambda v: v.to_pydatetime() if pd.notna(v) else None).astype(object)
        elif isinstance(col.type, Date):
            out[col.name] = out[col.name].map(lambda v: v.date() if pd.notna(v) else None).astype(object)
    out = out.astype(object).where(out.notna(), None)
    recs = out.to_dict("records")
    for r in recs:  # numpy scalars -> python
        for k, v in r.items():
            if isinstance(v, (np.integer,)):
                r[k] = int(v)
            elif isinstance(v, (np.floating,)):
                r[k] = float(v)
            elif v is pd.NA:
                r[k] = None
    return recs


def publish(engine: Engine, tables: dict[str, pd.DataFrame], names: list[str] | None = None,
            batch: int = 20_000) -> dict[str, int]:
    """Replace the given tables atomically (single transaction)."""
    names = names or [n for n in CORE_TABLES if n in tables]
    counts = {}
    is_pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        if is_pg:
            conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            conn.execute(text("TRUNCATE " + ", ".join(names) + " CASCADE"))
        else:
            for name in reversed(names):
                conn.execute(text(f"DELETE FROM {name}"))
        for name in names:
            table = metadata.tables[name]
            recs = _records(tables[name], table)
            for k in range(0, len(recs), batch):
                conn.execute(table.insert(), recs[k:k + batch])
            counts[name] = len(recs)
            log.info("published %-30s %8d rows", name, len(recs))
    return counts


def to_python_date(v) -> date | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(v).date()
