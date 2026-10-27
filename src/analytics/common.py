"""Shared analytical building blocks: filters, periods and SQL helpers.

All dashboard metrics are computed from ``mart_employee_month`` (one row per
employee per month) with a filter clause produced here, which guarantees that
every chart and KPI on a page responds to the same filters in the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd
from sqlalchemy.engine import Engine

from database.connection import expand_in, get_engine, read_sql


def month_end(d: date) -> date:
    return (pd.Timestamp(d) + pd.offsets.MonthEnd(0)).date()


def add_months(d: date, n: int) -> date:
    return (pd.Timestamp(d) + pd.offsets.MonthEnd(n)).date() if n else month_end(d)


def months_in(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month + 1


@dataclass(frozen=True)
class Filters:
    """Analytical period (inclusive month-ends) and organisational segment."""

    start: date
    end: date
    departments: tuple[str, ...] = field(default_factory=tuple)  # department_id
    locations: tuple[str, ...] = field(default_factory=tuple)  # location_id
    levels: tuple[str, ...] = field(default_factory=tuple)
    employment_types: tuple[str, ...] = field(default_factory=tuple)
    business_units: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        object.__setattr__(self, "start", month_end(self.start))
        object.__setattr__(self, "end", month_end(self.end))

    @property
    def months(self) -> int:
        return months_in(self.start, self.end)

    @property
    def is_segmented(self) -> bool:
        return bool(self.locations or self.levels or self.employment_types)

    def previous(self) -> Filters:
        n = self.months
        return replace(self, start=add_months(self.start, -n), end=add_months(self.start, -1))

    def with_period(self, start: date, end: date) -> Filters:
        return replace(self, start=start, end=end)

    def segment_sql(self, alias: str = "m", params: dict | None = None,
                    columns: dict[str, str | None] | None = None) -> tuple[str, dict]:
        """WHERE fragment (starting with ' AND ...') for the organisational filters.

        ``columns`` overrides the default column for a filter; ``None`` means the
        filter does not apply to that table (callers should say so in the UI).
        """
        cols: dict[str, str | None] = {
            "departments": "department_id", "locations": "location_id", "levels": "job_level",
            "employment_types": "employment_type", "business_units": "business_unit"}
        if columns:
            cols.update(columns)
        params = dict(params or {})
        prefix = f"{alias}." if alias else ""
        sql = ""
        for name, col in cols.items():
            values = list(getattr(self, name))
            if not values or not col:
                continue
            key = f"f_{name}"
            frag, params = expand_in(f" AND {prefix}{col} IN :{key}", params, key, values)
            sql += frag
        return sql, params

    def unsupported(self, columns: dict[str, str | None]) -> list[str]:
        """Names of active filters that a table cannot honour."""
        return [k for k, v in columns.items() if v is None and getattr(self, k)]

    def describe(self, names: dict[str, dict[str, str]] | None = None) -> str:
        names = names or {}
        parts = [f"{self.start:%b %Y} – {self.end:%b %Y}"]
        for label, attr in (("Department", "departments"), ("Location", "locations"), ("Level", "levels"),
                            ("Employment", "employment_types"), ("Business unit", "business_units")):
            vals = getattr(self, attr)
            if vals:
                lookup = names.get(attr, {})
                parts.append(f"{label}: {', '.join(lookup.get(v, v) for v in vals)}")
        return " · ".join(parts)


def q(sql: str, params: dict | None = None, engine: Engine | None = None, dates: list[str] | None = None
      ) -> pd.DataFrame:
    return read_sql(sql, params, engine or get_engine(), parse_dates=dates)


def safe_div(n: float, d: float) -> float | None:
    try:
        if d is None or d == 0 or pd.isna(d):
            return None
        return float(n) / float(d)
    except (TypeError, ValueError):
        return None


def data_bounds(engine: Engine | None = None) -> tuple[date, date]:
    df = q("SELECT MIN(snapshot_date) AS lo, MAX(snapshot_date) AS hi FROM mart_employee_month", engine=engine)
    return pd.Timestamp(df.lo.iloc[0]).date(), pd.Timestamp(df.hi.iloc[0]).date()


def lookups(engine: Engine | None = None) -> dict[str, dict[str, str]]:
    d = q("SELECT department_id, department_name, business_unit FROM departments ORDER BY department_name",
          engine=engine)
    loc = q("SELECT location_id, city FROM locations ORDER BY city", engine=engine)
    return {
        "departments": dict(zip(d.department_id, d.department_name)),
        "dept_bu": dict(zip(d.department_id, d.business_unit)),
        "locations": dict(zip(loc.location_id, loc.city)),
    }


def wilson_ci(k: float, n: float, z: float = 1.96) -> tuple[float | None, float | None]:
    if not n:
        return None, None
    p = k / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z ** 2 / (4 * n ** 2)) ** 0.5) / denom
    return max(0.0, centre - half), min(1.0, centre + half)
