"""Cached access to the analytical layer for the Streamlit UI.

Every cached function takes ``v`` — the current data version (latest pipeline
run id plus model training time) — so caches are invalidated automatically
after a refresh. Nothing here computes metrics itself; it only calls the
analytics modules.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from config.settings import ARTIFACT_DIR, get_settings
from database.connection import get_engine, read_sql, table_exists
from src.analytics import compensation, engagement, metrics, performance, recruitment, retention, workforce
from src.analytics.common import Filters, data_bounds, lookups

TTL = 3600


def database_ready() -> bool:
    try:
        return table_exists("mart_employee_month") and bool(
            read_sql("SELECT COUNT(*) AS n FROM mart_employee_month").n.iloc[0])
    except Exception:
        return False


def bootstrap_if_needed() -> None:
    """Build data (and models) on first launch when the database is empty.

    Intended for free hosting where no database file is shipped. Uses the
    configured profile; the full profile takes roughly one to two minutes.
    """
    settings = get_settings()
    if database_ready():
        from src.models.model_evaluation import models_available

        if not models_available() and settings.auto_bootstrap:
            with st.status("Training predictive models for the first time…", expanded=False):
                from src.models.training import train_all

                train_all()
        return
    if not settings.auto_bootstrap:
        st.error("The database has not been built. Run `python scripts/run_pipeline.py` and reload.")
        st.stop()
    with st.status(f"Building the synthetic workforce database ({settings.profile} profile)… "
                   "this happens once.", expanded=True) as status:
        from src.ingestion.pipeline import run_pipeline
        from src.models.training import train_all

        st.write("Generating, validating and loading data…")
        run_pipeline(settings.profile)
        st.write("Training and evaluating models…")
        train_all()
        status.update(label="Database ready", state="complete")
    st.cache_data.clear()


def data_version() -> str:
    try:
        run = read_sql("SELECT run_id FROM pipeline_runs WHERE status = 'SUCCESS' ORDER BY started_at DESC LIMIT 1")
        rid = run.run_id.iloc[0] if len(run) else "none"
    except Exception:
        rid = "none"
    m = ARTIFACT_DIR / "attrition_model.json"
    mt = str(int(m.stat().st_mtime)) if m.exists() else "0"
    return f"{rid}-{mt}"


@st.cache_data(ttl=TTL, show_spinner=False)
def bounds(v: str) -> tuple[date, date]:
    return data_bounds()


@st.cache_data(ttl=TTL, show_spinner=False)
def names(v: str) -> dict:
    lk = lookups()
    levels = read_sql("SELECT DISTINCT job_level FROM mart_employee_month ORDER BY job_level").job_level.tolist()
    etypes = read_sql("SELECT DISTINCT employment_type FROM mart_employee_month").employment_type.tolist()
    lk["levels"] = levels
    lk["employment_types"] = sorted(etypes)
    lk["business_units"] = sorted(set(lk["dept_bu"].values()))
    return lk


@st.cache_data(ttl=TTL, show_spinner=False)
def status(v: str) -> dict:
    out = {"connected": True, "backend": get_engine().dialect.name}
    try:
        counts = read_sql("""SELECT (SELECT COUNT(*) FROM employees) AS employees,
                                    (SELECT COUNT(*) FROM monthly_workforce_snapshots) AS snapshots,
                                    (SELECT COUNT(*) FROM candidates) AS candidates,
                                    (SELECT COUNT(*) FROM recruitment_events) AS recruitment_events""").iloc[0]
        out.update({k: int(v) for k, v in counts.items()})
        run = read_sql("SELECT run_id, finished_at, status, row_counts FROM pipeline_runs ORDER BY started_at DESC LIMIT 1")
        if len(run):
            out["last_refresh"] = str(run.finished_at.iloc[0])[:16]
            out["run_status"] = run.status.iloc[0]
            rc = json.loads(run.row_counts.iloc[0] or "{}")
            out["total_rows"] = int(sum(v for k, v in rc.items() if not k.startswith("mart_")
                                        and k != "metric_definitions"))
        dq = read_sql("SELECT status, COUNT(*) AS n FROM data_quality_results GROUP BY status")
        out["dq"] = dict(zip(dq.status, dq.n.astype(int)))
    except Exception as exc:
        out = {"connected": False, "error": str(exc)}
    return out


# ------------------------------------------------------------ analytics

@st.cache_data(ttl=TTL, show_spinner=False)
def kpis(f: Filters, v: str) -> metrics.KPISummary:
    return metrics.kpi_summary(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def series(f: Filters, v: str) -> pd.DataFrame:
    return metrics.monthly_series(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def hc_vs_plan(f: Filters, v: str) -> pd.DataFrame:
    return workforce.headcount_vs_plan(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def composition(f: Filters, by: str, v: str) -> pd.DataFrame:
    return workforce.composition(f, by)


@st.cache_data(ttl=TTL, show_spinner=False)
def loc_level(f: Filters, v: str) -> pd.DataFrame:
    return workforce.location_level_matrix(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def tenure(f: Filters, v: str) -> pd.DataFrame:
    return workforce.tenure_distribution(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def span(f: Filters, v: str) -> pd.DataFrame:
    return workforce.span_of_control(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def movements(f: Filters, v: str) -> pd.DataFrame:
    return workforce.movements(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def attrition_by(dim: str, f: Filters, v: str, voluntary: bool = False) -> pd.DataFrame:
    return metrics.attrition_by(dim, f, voluntary_only=voluntary)


@st.cache_data(ttl=TTL, show_spinner=False)
def exit_reasons(f: Filters, v: str) -> pd.DataFrame:
    return metrics.exit_reasons(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def plan_by_dept(f: Filters, month: date, v: str) -> pd.DataFrame:
    return workforce.plan_vs_actual_by_department(f, month)


@st.cache_data(ttl=TTL, show_spinner=False)
def candidates(f: Filters, v: str) -> pd.DataFrame:
    return recruitment.candidate_cohort(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def rec_kpis(f: Filters, v: str) -> dict:
    return recruitment.recruitment_kpis(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def stage_durations(f: Filters, v: str) -> pd.DataFrame:
    return recruitment.stage_durations(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def sources(f: Filters, v: str) -> pd.DataFrame:
    return recruitment.source_effectiveness(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def tth(f: Filters, v: str) -> pd.DataFrame:
    return recruitment.time_to_hire(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def ttf(f: Filters, v: str) -> pd.DataFrame:
    return recruitment.time_to_fill(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def joiners(f: Filters, v: str) -> pd.DataFrame:
    return retention.joiners(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def tenure_exit(f: Filters, v: str) -> pd.DataFrame:
    return retention.tenure_at_exit(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def reviews(f: Filters, v: str) -> pd.DataFrame:
    return performance.reviews(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def promotions(f: Filters, by: str, v: str) -> pd.DataFrame:
    return performance.promotion_rates(f, by)


@st.cache_data(ttl=TTL, show_spinner=False)
def training(f: Filters, v: str) -> dict:
    return performance.training_summary(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def training_effect(v: str) -> dict:
    return performance.training_rating_change()


@st.cache_data(ttl=TTL, show_spinner=False)
def survey_responses(f: Filters, v: str) -> pd.DataFrame:
    """Internal only: callers must aggregate with suppression before display."""
    return engagement.responses(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def response_rates(f: Filters, v: str) -> pd.DataFrame:
    return engagement.response_rates(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def engagement_exit(f: Filters, v: str) -> dict:
    return engagement.engagement_and_exit(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def manager_change(f: Filters, v: str) -> dict:
    return engagement.manager_change_effect(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def cost_plan(f: Filters, v: str) -> pd.DataFrame:
    return compensation.cost_vs_plan(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def cost_breakdown(f: Filters, by: str, v: str) -> pd.DataFrame:
    return compensation.cost_breakdown(f, by)


@st.cache_data(ttl=TTL, show_spinner=False)
def salary_trend(f: Filters, v: str) -> pd.DataFrame:
    return compensation.salary_trend(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def budget_variance(f: Filters, v: str) -> pd.DataFrame:
    return compensation.budget_variance_by_department(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def spend(f: Filters, v: str) -> dict:
    return compensation.recruitment_and_training_spend(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def exit_cost_base(f: Filters, v: str) -> pd.DataFrame:
    return compensation.exit_costs_base(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def turnover_defaults(f: Filters, v: str) -> compensation.TurnoverAssumptions:
    return compensation.observed_defaults(f=f)


@st.cache_data(ttl=TTL, show_spinner=False)
def stats_population(f: Filters, v: str) -> pd.DataFrame:
    from src.analytics.statistics import at_risk_population

    return at_risk_population(f)


@st.cache_data(ttl=TTL, show_spinner="Fitting the drivers model…")
def drivers(v: str) -> dict:
    from src.analytics.statistics import attrition_drivers

    return attrition_drivers()


@st.cache_data(ttl=TTL, show_spinner=False)
def feedback(f: Filters, v: str) -> pd.DataFrame:
    from src.nlp.feedback_analysis import load_feedback

    return load_feedback(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def scenario_inputs(v: str) -> dict:
    from src.models.scenarios import baseline_inputs

    return baseline_inputs()


@st.cache_data(ttl=TTL, show_spinner=False)
def model_meta(name: str, v: str) -> dict | None:
    from src.models.model_evaluation import load_json

    return load_json(name)


@st.cache_data(ttl=TTL, show_spinner=False)
def risk_scores(v: str) -> pd.DataFrame | None:
    from src.models.attrition import load_scores

    return load_scores()


@st.cache_data(ttl=TTL, show_spinner="Generating recommendations…")
def recommendations(f: Filters, v: str) -> list[dict]:
    from src.analytics.recommendations import generate

    fc = model_meta("workforce_forecast.json", v)
    tt = model_meta("hiring_forecast.json", v)
    base = compensation.exit_costs_base(f)
    per_exit = compensation.turnover_summary(base, compensation.observed_defaults(f=f))["per_exit"]
    return [r.to_dict() for r in generate(f, None, fc, tt, per_exit)]
