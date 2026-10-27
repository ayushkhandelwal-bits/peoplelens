"""Automated monthly workforce report (HTML + Markdown + CSV of underlying metrics).

Everything in the report is calculated from the database and saved model
artefacts at generation time; nothing is pre-written. The report can be
produced from the command line (``scripts/generate_report.py``) or from the
Planning & Business Insights page, with no external scheduler or paid service.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.engine import Engine

from config.settings import REPORT_DIR
from src.analytics import recruitment, workforce
from src.analytics.common import Filters, add_months, data_bounds, q, safe_div
from src.analytics.compensation import cost_vs_plan, exit_costs_base, observed_defaults, turnover_summary
from src.analytics.metrics import attrition_by, kpi_summary
from src.analytics.recommendations import generate as recommendations
from src.governance.metric_dictionary import METRICS


def inr(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"₹{v / 1e7:,.2f} Cr" if abs(v) >= 1e7 else f"₹{v / 1e5:,.1f} L" if abs(v) >= 1e5 else f"₹{v:,.0f}"


def pct(v, signed=False) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.1%}" if signed else f"{v:.1%}"


@dataclass
class Section:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    tables: list[tuple[str, pd.DataFrame]] = field(default_factory=list)


@dataclass
class Report:
    title: str
    period: str
    generated_at: str
    sections: list[Section]
    metrics_rows: list[dict]

    def to_markdown(self) -> str:
        out = [f"# {self.title}", "", f"**Reporting period:** {self.period}  ", f"**Generated:** {self.generated_at}",
               "", "> All employees, amounts and results are simulated (synthetic data, fictional INR).", ""]
        for s in self.sections:
            out += [f"## {s.title}", ""]
            for p in s.paragraphs:
                out += [p, ""]
            for caption, df in s.tables:
                if caption:
                    out += [f"*{caption}*", ""]
                out += [md_table(df), ""]
        return "\n".join(out)

    def to_html(self) -> str:
        css = """
        body{font-family:Inter,Segoe UI,Helvetica,Arial,sans-serif;color:#0f172a;background:#f8fafc;margin:0}
        .wrap{max-width:960px;margin:0 auto;padding:32px 20px}
        h1{color:#172554;font-size:26px;margin:0 0 4px} h2{color:#172554;font-size:18px;margin:28px 0 8px;
        border-bottom:1px solid #e2e8f0;padding-bottom:6px} .meta{color:#475569;font-size:13px}
        .note{background:#fff;border:1px solid #e2e8f0;border-left:3px solid #0f766e;padding:8px 12px;font-size:13px;
        color:#475569;margin:14px 0} p{line-height:1.55;font-size:14px}
        table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;margin:8px 0 14px}
        th{background:#f1f5f9;color:#334155;text-align:left;font-weight:600}
        th,td{border:1px solid #e2e8f0;padding:6px 8px} td{font-variant-numeric:tabular-nums}
        .cap{color:#475569;font-size:12px;margin-top:10px}
        """
        body = [f"<h1>{html.escape(self.title)}</h1>",
                f"<div class='meta'>Reporting period: <b>{html.escape(self.period)}</b> · Generated "
                f"{html.escape(self.generated_at)}</div>",
                "<div class='note'>All employees, amounts and results are simulated (synthetic data, fictional INR)."
                "</div>"]
        for s in self.sections:
            body.append(f"<h2>{html.escape(s.title)}</h2>")
            for p in s.paragraphs:
                body.append(f"<p>{_md_inline(p)}</p>")
            for caption, df in s.tables:
                if caption:
                    body.append(f"<div class='cap'>{html.escape(caption)}</div>")
                body.append(df.to_html(index=False, border=0, escape=True))
        return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(self.title)}</title>"
                f"<meta name='viewport' content='width=device-width, initial-scale=1'><style>{css}</style></head>"
                f"<body><div class='wrap'>{''.join(body)}</div></body></html>")


def md_table(df: pd.DataFrame) -> str:
    """Minimal Markdown table (avoids an extra dependency)."""
    def cell(v):
        if isinstance(v, float):
            return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
        return str(v).replace("|", "/")
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def _md_inline(text: str) -> str:
    import re

    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)


def build_report(month: str | None = None, engine: Engine | None = None) -> Report:
    lo, hi = data_bounds(engine)
    end = (pd.Timestamp(month) + pd.offsets.MonthEnd(0)).date() if month else hi
    end = min(max(end, lo), hi)
    fm = Filters(start=end, end=end)
    prev = fm.previous()
    f12 = Filters(start=max(lo, add_months(end, -11)), end=end)
    rows: list[dict] = []

    def metric(section, name, value, segment="Organisation"):
        rows.append(dict(section=section, metric=name, segment=segment, value=value, period=f"{end:%Y-%m}"))

    k, kp = kpi_summary(fm, engine), kpi_summary(prev, engine) if prev.start >= lo else None
    k12 = kpi_summary(f12, engine)
    sections = []

    # 1 headline
    s = Section("Headline KPIs")
    tbl = pd.DataFrame([
        ("Month-end headcount", f"{k.active_headcount:,}", f"{kp.active_headcount:,}" if kp else "—",
         f"{k.active_headcount - kp.active_headcount:+,}" if kp else "—"),
        ("Joiners", f"{k.joiners:,}", f"{kp.joiners:,}" if kp else "—", f"{k.joiners - kp.joiners:+,}" if kp else "—"),
        ("Exits", f"{k.exits:,}", f"{kp.exits:,}" if kp else "—", f"{k.exits - kp.exits:+,}" if kp else "—"),
        ("Attrition (annualised, month)", pct(k.attrition_rate), pct(kp.attrition_rate) if kp else "—", ""),
        ("Attrition (trailing 12 months)", pct(k12.attrition_rate), "", ""),
        ("Voluntary attrition (trailing 12 months)", pct(k12.voluntary_attrition_rate), "", ""),
        ("Headcount vs plan", f"{k.plan_variance:+,} ({pct(k.plan_variance_pct, True)})"
         if k.plan_variance is not None else "—", "", ""),
        ("Workforce cost (month)", inr(k.workforce_cost), inr(kp.workforce_cost) if kp else "—", ""),
    ], columns=["KPI", f"{end:%b %Y}", f"{prev.end:%b %Y}", "Change"])
    s.tables.append(("", tbl))
    net = k.joiners - k.exits
    s.paragraphs.append(
        f"Headcount closed {end:%B %Y} at **{k.active_headcount:,}** ({net:+,} net: {k.joiners:,} joiners, "
        f"{k.exits:,} exits). Trailing-12-month attrition is **{pct(k12.attrition_rate)}** "
        f"({pct(k12.voluntary_attrition_rate)} voluntary).")
    for name, val in [("active_headcount", k.active_headcount), ("joiners", k.joiners), ("exits", k.exits),
                      ("attrition_rate_month_annualised", k.attrition_rate),
                      ("attrition_rate_trailing_12m", k12.attrition_rate),
                      ("voluntary_attrition_trailing_12m", k12.voluntary_attrition_rate),
                      ("headcount_vs_plan", k.plan_variance), ("workforce_cost", k.workforce_cost)]:
        metric("Headline", name, val)
    sections.append(s)

    # 2 hiring and exits by department
    s = Section("Hiring and exits")
    mv = q("""SELECT d.department_name AS department, SUM(m.joined_in_month) AS joiners,
                     SUM(m.exited_in_month) AS exits,
                     SUM(CASE WHEN m.exit_type = 'Voluntary' THEN 1 ELSE 0 END) AS voluntary_exits,
                     SUM(m.active_flag) AS month_end_headcount
              FROM mart_employee_month m JOIN departments d ON d.department_id = m.department_id
              WHERE m.snapshot_date = :d GROUP BY d.department_name ORDER BY exits DESC""", {"d": end}, engine)
    s.tables.append(("Departments ordered by exits this month", mv.head(10)))
    for r in mv.itertuples():
        metric("Hiring and exits", "joiners", r.joiners, r.department)
        metric("Hiring and exits", "exits", r.exits, r.department)
    sections.append(s)

    # 3 attrition and retention
    s = Section("Attrition and retention (trailing 12 months)")
    a = attrition_by("department", f12, engine)
    t = a.head(6)[["segment", "exits", "avg_headcount", "attrition_rate", "ci_low", "ci_high"]].copy()
    for c in ("attrition_rate", "ci_low", "ci_high"):
        t[c] = t[c].map(pct)
    t["avg_headcount"] = t.avg_headcount.round(0).astype(int)
    s.tables.append(("Highest annualised attrition (95% CI)", t.rename(columns={"segment": "department"})))
    for r in a.itertuples():
        metric("Attrition", "attrition_rate_trailing_12m", r.attrition_rate, r.segment)
    from src.analytics.retention import early_attrition_summary, joiners

    j = joiners(Filters(start=max(lo, add_months(end, -14)), end=add_months(end, -3)), engine)
    ea = early_attrition_summary(j)
    if len(ea):
        r90 = ea[ea.horizon == "90 days"].iloc[0]
        s.paragraphs.append(f"Early attrition: **{pct(r90.early_attrition)}** of joiners observed for 90 days left "
                            f"within 90 days (n = {int(r90.eligible_joiners)}).")
        metric("Retention", "early_attrition_90d", r90.early_attrition)
    sections.append(s)

    # 4 recruitment
    s = Section("Recruitment (last 3 months)")
    f3 = Filters(start=max(lo, add_months(end, -2)), end=end)
    rk = recruitment.recruitment_kpis(f3, engine)
    fun = recruitment.funnel(recruitment.candidate_cohort(f3, engine))
    fun_t = fun[["stage", "candidates", "conversion_from_previous"]].copy()
    fun_t["conversion_from_previous"] = fun_t.conversion_from_previous.map(pct)
    s.tables.append(("Candidate funnel (applications in the last 3 months)", fun_t))
    s.paragraphs.append("Recent applicants are still moving through the funnel: many accepted candidates have not "
                        "reached their joining date yet, so late-stage conversion is understated for this cohort.")
    s.paragraphs.append(f"Offer acceptance **{pct(rk['offer_acceptance_rate'])}**, joining rate "
                        f"**{pct(rk['joining_rate'])}**, median time to fill **{rk['median_time_to_fill'] or 0:.0f} days**, "
                        f"cost per hire **{inr(rk['cost_per_hire'])}**, open requisitions **{rk['open_requisitions']:,}**.")
    for key in ("applications", "offer_acceptance_rate", "joining_rate", "median_time_to_fill", "cost_per_hire",
                "open_requisitions"):
        metric("Recruitment", key, rk[key])
    sections.append(s)

    # 5 plan vs actual
    s = Section("Workforce plan vs actual")
    pva = workforce.plan_vs_actual_by_department(fm, end, engine)
    if len(pva):
        t = pva[["department_name", "planned_headcount", "actual_headcount", "variance", "variance_pct"]].copy()
        t["variance_pct"] = t.variance_pct.map(lambda v: pct(v, True))
        s.tables.append(("Largest shortfalls against the approved plan", t.head(8)))
        s.paragraphs.append(f"Organisation: {int(pva.actual_headcount.sum()):,} actual vs "
                            f"{int(pva.planned_headcount.sum()):,} planned ({int(pva.variance.sum()):+,}). "
                            f"{int((pva.variance < 0).sum())} of {len(pva)} departments are behind plan.")
        for r in pva.itertuples():
            metric("Plan vs actual", "headcount_variance", r.variance, r.department_name)
    sections.append(s)

    # 6 cost
    s = Section("Manpower cost")
    fy_start = pd.Timestamp(end.year if end.month >= 4 else end.year - 1, 4, 30).date()
    fy = Filters(start=max(lo, fy_start), end=end)
    cvp = cost_vs_plan(fy, engine)
    act, plan = cvp.workforce_cost.sum(), cvp.planned_cost.sum()
    s.paragraphs.append(f"Fiscal year to date ({fy.start:%b %Y}–{end:%b %Y}): actual people cost **{inr(act)}** vs plan "
                        f"**{inr(plan)}** ({inr(act - plan)}, {pct(safe_div(act - plan, plan), True)}).")
    base = exit_costs_base(f12, engine)
    ts = turnover_summary(base, observed_defaults(engine))
    s.paragraphs.append(f"Estimated replacement cost of {ts['exits']:,} voluntary exits in the last 12 months: "
                        f"**{inr(ts['total'])}** (≈ {inr(ts['per_exit'])} per exit; estimate, not an accounting loss).")
    metric("Cost", "fytd_actual_cost", act)
    metric("Cost", "fytd_planned_cost", plan)
    metric("Cost", "turnover_cost_12m", ts["total"])
    sections.append(s)

    # 7 significant changes
    s = Section("Significant changes vs previous month")
    if kp:
        cur = q("""SELECT d.department_name, SUM(m.active_flag) AS hc FROM mart_employee_month m
                   JOIN departments d ON d.department_id = m.department_id WHERE m.snapshot_date = :d
                   GROUP BY d.department_name""", {"d": end}, engine)
        pr = q("""SELECT d.department_name, SUM(m.active_flag) AS hc_prev FROM mart_employee_month m
                  JOIN departments d ON d.department_id = m.department_id WHERE m.snapshot_date = :d
                  GROUP BY d.department_name""", {"d": prev.end}, engine)
        ch = cur.merge(pr, on="department_name")
        ch["change"] = ch.hc - ch.hc_prev
        ch["change_pct"] = ch.change / ch.hc_prev
        sig = ch[(ch.change.abs() >= 5) & (ch.change_pct.abs() >= 0.03)].sort_values("change")
        if len(sig):
            t = sig[["department_name", "hc_prev", "hc", "change", "change_pct"]].copy()
            t["change_pct"] = t.change_pct.map(lambda v: pct(v, True))
            s.tables.append(("Departments whose headcount moved by at least 5 heads and 3%", t))
        else:
            s.paragraphs.append("No department's headcount moved by 5+ heads and 3%+ this month (threshold).")
    sections.append(s)

    # 8 forecast & actions
    from src.models.hiring_forecast import load_meta as ttf_meta
    from src.models.workforce_forecast import load_meta as fc_meta

    fc, ttf = fc_meta(), ttf_meta()
    s = Section("Forecast and suggested actions")
    if fc:
        f6 = pd.DataFrame(fc["forecast"])[["month", "forecast", "lower_80", "upper_80"]].round(0)
        s.tables.append((f"Headcount forecast ({fc['selected_model']}, 80% interval)", f6))
    a0 = observed_defaults(engine)
    per_exit = turnover_summary(base, a0)["per_exit"]
    recs = recommendations(f12, engine, fc, ttf, per_exit)
    for r in recs[:6]:
        s.paragraphs.append(f"**{r.title}.** {r.observation} *Action:* {r.action} *Limitation:* {r.limitation}")
    if not recs:
        s.paragraphs.append("No rule thresholds were met this period.")
    sections.append(s)

    # 9 data quality
    s = Section("Data quality status")
    dq = q("SELECT status, COUNT(*) AS checks FROM data_quality_results GROUP BY status", engine=engine)
    run = q("SELECT run_id, finished_at, status FROM pipeline_runs ORDER BY started_at DESC LIMIT 1", engine=engine)
    s.paragraphs.append("Checks: " + ", ".join(f"{r.status} {r.checks}" for r in dq.itertuples())
                        + (f". Last pipeline run {run.run_id.iloc[0]} ({run.status.iloc[0]}) finished "
                           f"{str(run.finished_at.iloc[0])[:16]}." if len(run) else "."))
    sections.append(s)

    s = Section("Definitions and limitations")
    for key in ("active_headcount", "attrition_rate", "time_to_fill", "workforce_cost", "headcount_vs_plan",
                "turnover_cost"):
        m = METRICS[key]
        s.paragraphs.append(f"**{m.name}:** {m.definition} *Formula:* {m.formula}.")
    s.paragraphs.append("Synthetic data: relationships reflect documented simulation assumptions and do not prove "
                        "real-world causes. Small departments produce volatile monthly rates.")
    sections.append(s)

    return Report(title="PeopleLens — Monthly Workforce Report", period=f"{end:%B %Y}",
                  generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"), sections=sections, metrics_rows=rows)


def write_report(month: str | None = None, engine: Engine | None = None, out_dir: Path | None = None
                 ) -> dict[str, Path]:
    rep = build_report(month, engine)
    out_dir = out_dir or REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"workforce_report_{pd.Timestamp(rep.period):%Y_%m}"
    paths = {"html": out_dir / f"{stem}.html", "markdown": out_dir / f"{stem}.md", "csv": out_dir / f"{stem}_metrics.csv"}
    paths["html"].write_text(rep.to_html(), encoding="utf-8")
    paths["markdown"].write_text(rep.to_markdown(), encoding="utf-8")
    pd.DataFrame(rep.metrics_rows).to_csv(paths["csv"], index=False)
    return paths
