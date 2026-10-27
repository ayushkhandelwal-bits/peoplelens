"""Data & Governance: data quality, lineage, metric glossary, data dictionary,
access control (with a live enforcement test) and privacy safeguards."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from components import data
from components.kpi_cards import caption, insight, metric, page_header
from components.sidebar import current_role
from components.tables import show
from database.connection import get_engine, read_sql
from database.schema import VIEWER_VIEWS, metadata
from src.governance.access_control import PERMISSION_LABELS, ROLES, can, run_custom_sql
from src.governance.metric_dictionary import METRICS

LINEAGE = """
digraph {
  rankdir=LR; bgcolor="transparent";
  node [shape=box, style="rounded,filled", fillcolor="#FFFFFF", color="#CBD5E1", fontname="Helvetica", fontsize=11];
  edge [color="#94A3B8"];
  gen [label="Synthetic simulator\\n(seeded, month by month)", fillcolor="#E0F2FE"];
  exp [label="HRMS-style export\\nCSV + manifest (SHA-256)"];
  stg [label="Staging tables\\nstg_*"];
  cln [label="Cleaning & typing\\n(trim, case, dates, dedupe,\\nquarantine)"];
  dq [label="Quality gate\\n138 rules", fillcolor="#FEF3C7"];
  core [label="Core relational model\\n16 tables"];
  mart [label="Curated mart\\nmart_employee_month"];
  views [label="Aggregate views v_*\\n(Viewer role)"];
  models [label="Models & forecasts\\n(artifacts/)"];
  app [label="PeopleLens app,\\nreports, Ask PeopleLens", fillcolor="#CCFBF1"];
  gen -> exp -> stg -> cln -> dq -> core -> mart -> app;
  core -> views -> app; mart -> models -> app; dq -> app [style=dashed, label=" status"];
}
"""


def render() -> None:
    v = data.data_version()
    page_header("Workspace", "Data & Governance",
                "How the data is produced, checked and protected: quality results, lineage, definitions, access "
                "rules and privacy safeguards.")
    st.info("All employee records, financial amounts and analytical results in PeopleLens are **simulated**. The "
            "organisation is fictional; no real or confidential personal data is used.", icon=":material/info:")
    tabs = st.tabs(["Data quality", "Lineage & refresh", "Metric glossary", "Data dictionary", "Access control",
                    "Privacy & retention"])
    with tabs[0]:
        _quality(v)
    with tabs[1]:
        _lineage(v)
    with tabs[2]:
        _glossary()
    with tabs[3]:
        _dictionary(v)
    with tabs[4]:
        _access()
    with tabs[5]:
        _privacy()


def _quality(v) -> None:
    dq = read_sql("SELECT * FROM data_quality_results")
    c = st.columns(4)
    metric(c[0], "Checks", f"{len(dq):,}")
    metric(c[1], "Passed", f"{(dq.status == 'PASS').sum():,}")
    metric(c[2], "Warnings", f"{(dq.status == 'WARN').sum():,}")
    metric(c[3], "Failed", f"{(dq.status == 'FAIL').sum():,}")
    if len(dq):
        caption(f"Last evaluated {str(dq.checked_at.max())[:16]} (run {dq.run_id.iloc[0]}). Errors block publication; "
                "warnings are reported.")
    cat = dq.groupby("category").agg(checks=("status", "size"), failed=("status", lambda s: (s == "FAIL").sum()),
                                     warnings=("status", lambda s: (s == "WARN").sum())).reset_index()
    show(cat, {"checks": "int", "failed": "int", "warnings": "int"})
    show(dq[["status", "severity", "category", "table_name", "check_name", "failed_rows", "details"]]
         .sort_values(["status", "category"]), {"failed_rows": "int"}, height=360, download="data_quality_results")
    run = read_sql("SELECT row_counts, stage_log FROM pipeline_runs WHERE status = 'SUCCESS' "
                   "ORDER BY started_at DESC LIMIT 1")
    if len(run):
        from config.settings import REPORT_DIR

        rep = REPORT_DIR / "data_quality_report.md"
        if rep.exists() and "Cleaning actions" in rep.read_text(encoding="utf-8"):
            txt = rep.read_text(encoding="utf-8")
            part = txt.split("## Cleaning actions")[1].split("## Checks")[0]
            with st.expander("Cleaning actions applied to the raw export", icon=":material/cleaning_services:"):
                st.markdown(part)
    if can(current_role(), "run_pipeline_checks") and st.button("Re-run all checks on the live database",
                                                                icon=":material/fact_check:"):
        from src.ingestion.validation import load_tables_from_db, persist_results, validate_tables

        with st.spinner("Loading tables and running checks…"):
            res = validate_tables(load_tables_from_db(get_engine()))
            persist_results(get_engine(), "MANUAL", res)
        st.cache_data.clear()
        st.rerun()


def _lineage(v) -> None:
    st.graphviz_chart(LINEAGE, width="stretch")
    runs = read_sql("SELECT run_id, started_at, finished_at, status, profile, seed, data_start, data_end, stage_log "
                    "FROM pipeline_runs ORDER BY started_at DESC")
    if len(runs):
        r = runs.iloc[0]
        st.markdown(f"**Latest run** {r.run_id} · {r.status} · profile `{r.profile}` · seed {r.seed} · data "
                    f"{r.data_start} to {r.data_end}")
        show(pd.DataFrame(json.loads(r.stage_log)), {"seconds": "float1"})
        show(runs.drop(columns=["stage_log"]))
    caption("Rebuild everything reproducibly with `python scripts/run_pipeline.py` (same seed → same data).")


def _glossary() -> None:
    q = st.text_input("Search metrics", placeholder="e.g. attrition, time to fill, eNPS", key="gloss_q")
    cats = sorted({m.category for m in METRICS.values()})
    sel = st.pills("Category", cats, selection_mode="multi", key="gloss_cat")
    for m in METRICS.values():
        if sel and m.category not in sel:
            continue
        if q and q.lower() not in (m.name + m.definition + m.key).lower():
            continue
        with st.expander(f"{m.name} · {m.category}"):
            st.markdown(f"{m.definition}\n\n- **Formula:** `{m.formula}`\n- **Numerator / denominator:** "
                        f"{m.numerator} / {m.denominator}\n- **Grain:** {m.grain}\n- **Source:** {m.source_tables}\n"
                        f"- **Missing values:** {m.missing_values}\n- **Limitations:** {m.limitations}\n"
                        f"- **Interpretation:** {m.interpretation}")


def _dictionary(v) -> None:
    rows = []
    for t in metadata.sorted_tables:
        try:
            n = int(read_sql(f"SELECT COUNT(*) AS n FROM {t.name}").n.iloc[0])
        except Exception:
            n = None
        rows.append(dict(table=t.name, rows=n, columns=len(t.columns),
                         primary_key=", ".join(c.name for c in t.primary_key.columns),
                         foreign_keys=", ".join(sorted({f"{fk.parent.name}→{fk.column.table.name}"
                                                        for fk in t.foreign_keys}))))
    df = pd.DataFrame(rows).sort_values("table")
    show(df, {"rows": "int", "columns": "int"}, download="data_dictionary")
    pick = st.selectbox("Inspect table", df.table.tolist(), key="dict_table")
    t = metadata.tables[pick]
    show(pd.DataFrame([dict(column=c.name, type=str(c.type), nullable=c.nullable,
                            key="PK" if c.primary_key else ("FK" if c.foreign_keys else "")) for c in t.columns]))


def _access() -> None:
    perms = sorted({p for s in ROLES.values() for p in s})
    m = pd.DataFrame([{**{"Permission": PERMISSION_LABELS[p]}, **{r: "✓" if p in s else "—" for r, s in ROLES.items()}}
                      for p in perms])
    show(m)
    insight("Enforcement is layered: server-side permission checks guard row-level functions, and user SQL runs on "
            "a database connection that cannot write — for the Viewer it can read only the aggregate views "
            f"({', '.join(VIEWER_VIEWS)}). Hiding a button is never the only control.")
    st.markdown("**Live test** — run the same statement as each role and see what the database allows:")
    sql = st.text_input("SQL to test", "SELECT employee_id, job_level FROM employees LIMIT 5", key="acl_sql")
    c1, c2 = st.columns(2)
    for col, role in zip((c1, c2), ROLES):
        with col:
            st.markdown(f"**{role}**")
            try:
                out = run_custom_sql(sql, role)
                st.success(f"Allowed — {len(out)} rows")
                st.dataframe(out.head(5), hide_index=True, width="stretch")
            except PermissionError as exc:
                st.error(f"Blocked — {exc}")
            except (ValueError, TimeoutError) as exc:
                st.warning(f"Rejected — {exc}")
    caption("Demo limitation: the role is chosen in the sidebar (no login). In production it would come from the "
            "identity provider, and PostgreSQL deployments use separate SELECT-only login roles "
            "(peoplelens_analyst_ro, peoplelens_viewer).")


def _privacy() -> None:
    st.markdown("""
- **Synthetic provenance.** Every record is generated by a seeded simulator (`src/data_generation`). Fictional names
  exist only for realism; the UI shows synthetic IDs and never names, salaries or ratings of individuals.
- **Anonymous feedback by design.** Free-text comments are stored without an employee ID — only department, survey
  wave and tenure band — so text cannot be joined back to a person.
- **Small-group suppression.** Engagement and feedback aggregates based on fewer than 10 responses are suppressed.
- **No protected attributes in models.** Gender and names are excluded from model features; gender is shown only as
  workforce composition. Exit dates/reasons are excluded to prevent leakage.
- **Responsible use of predictions.** Risk scores are probabilities for groups of similar employees; they must not
  drive disciplinary, termination or other automated employment decisions. Ask PeopleLens declines such requests.
- **Least privilege.** Viewer access is limited to aggregate views at the database level; analysts get read-only
  access; only the pipeline writes.
- **Retention & deletion (assumed policy).** Raw exports are transient (regenerated, not committed to Git);
  staging tables are dropped after each successful load; quarantined rows are kept for audit in
  `data/generated/quarantine`; the database is rebuilt from source on every pipeline run.
- **Ownership (assumed).** HR Operations owns employee, job and lifecycle tables; Talent Acquisition owns
  recruitment tables; Total Rewards owns compensation; Finance owns the workforce budget; People Analytics owns
  the mart, metric dictionary and models.
""")
