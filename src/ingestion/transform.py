"""Curated analytical layer built from the published source tables.

``mart_employee_month`` extends each monthly snapshot row with lifecycle flags
and the employer cost for that month, so dashboards can filter by any
organisational attribute and aggregate with plain SQL.

Monthly cost convention
-----------------------
* Rate = (annual base + annual variable target + annual benefits) / 12, using
  the compensation record effective on the last day the employee was employed
  in that month.
* The rate is pro-rated by the share of calendar days employed in the month
  (joiners and leavers count partially).
* ``monthly_base_cost`` is the same calculation using base salary only.
Mid-month compensation changes are therefore not split (the month-end rate
applies), which avoids double counting and is documented as a limitation.
"""

from __future__ import annotations

import pandas as pd


def build_mart_employee_month(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    s = t["monthly_workforce_snapshots"].copy()
    e = t["employees"][["employee_id", "joining_date", "exit_date", "exit_type"]].copy()
    d = t["departments"][["department_id", "business_unit"]]
    for col in ("joining_date", "exit_date"):
        e[col] = pd.to_datetime(e[col])
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"])
    s = s.merge(e, on="employee_id", how="left").merge(d, on="department_id", how="left")
    month_start = s.snapshot_date.dt.to_period("M").dt.start_time
    days_in_month = s.snapshot_date.dt.day
    s["joined_in_month"] = ((s.joining_date >= month_start) & (s.joining_date <= s.snapshot_date)).astype(int)
    s["exited_in_month"] = (s.active_flag == 0).astype(int)
    s["exit_type"] = s.exit_type.where(s.exited_in_month == 1)

    first_day = s.joining_date.where(s.joining_date > month_start, month_start)
    last_day = (s.exit_date - pd.Timedelta(days=1)).where(s.exited_in_month == 1, s.snapshot_date)
    employed_days = (last_day - first_day).dt.days + 1
    share = (employed_days.clip(lower=0) / days_in_month).clip(upper=1.0)

    # compensation in effect on the reference day (last employed day in month)
    s["ref_day"] = last_day
    ch = t["compensation_history"][["employee_id", "effective_start_date", "annual_base_salary",
                                    "variable_compensation", "benefits_cost"]].copy()
    ch["effective_start_date"] = pd.to_datetime(ch.effective_start_date)
    ch = ch.sort_values("effective_start_date")
    left = s[["employee_id", "ref_day"]].reset_index().sort_values("ref_day")
    m = pd.merge_asof(left, ch, left_on="ref_day", right_on="effective_start_date", by="employee_id",
                      direction="backward").set_index("index").reindex(s.index)
    rate_total = (m.annual_base_salary + m.variable_compensation + m.benefits_cost) / 12
    rate_base = m.annual_base_salary / 12
    s["monthly_total_cost"] = (rate_total * share).fillna(0).round(0)
    s["monthly_base_cost"] = (rate_base * share).fillna(0).round(0)

    ev = t["employee_events"][["employee_id", "event_type", "event_date", "new_department_id"]].copy()
    ev["month"] = pd.to_datetime(ev.event_date).dt.to_period("M")
    s["month"] = s.snapshot_date.dt.to_period("M")
    tr = ev[ev.event_type == "Transfer"][["employee_id", "month", "new_department_id"]].drop_duplicates(
        ["employee_id", "month"], keep="last")
    s = s.merge(tr, on=["employee_id", "month"], how="left")
    s["transferred_in"] = (s.new_department_id == s.department_id).fillna(False).astype(int)
    pr = ev[ev.event_type == "Promotion"][["employee_id", "month"]].drop_duplicates()
    pr["promoted_in_month"] = 1
    s = s.merge(pr, on=["employee_id", "month"], how="left")
    s["promoted_in_month"] = s.promoted_in_month.fillna(0).astype(int)

    cols = ["snapshot_date", "employee_id", "department_id", "business_unit", "location_id", "job_family",
            "job_level", "employment_type", "manager_id", "active_flag", "tenure_months", "joined_in_month",
            "exited_in_month", "exit_type", "transferred_in", "promoted_in_month", "monthly_base_cost",
            "monthly_total_cost"]
    return s[cols]
