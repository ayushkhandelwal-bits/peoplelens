"""How PeopleLens Works: an end-to-end technical walkthrough.

One page that explains the whole product as a flow: where the data comes
from, how it is checked and stored, how metrics, statistics and models are
built (which model, why, and how well it does), and how the results reach the
screens. Model results are read from the trained artefacts so the page always
matches the current build.
"""

from __future__ import annotations

import html
import json

import pandas as pd
import streamlit as st

from components import charts, data
from components.kpi_cards import caption, chips, insight, metric, page_header
from components.tables import show
from config.settings import ARTIFACT_DIR

# ---------------------------------------------------------------------------
# Diagrams (Graphviz DOT, rendered in the browser — no system install needed)
# ---------------------------------------------------------------------------

_NODE = 'node [shape=box, style="rounded,filled", fillcolor="#FFFFFF", color="#CBD5E1", fontname="Helvetica", fontsize=11, margin="0.18,0.08"];'
_EDGE = 'edge [color="#64748B", arrowsize=0.7, fontname="Helvetica", fontsize=9, fontcolor="#475569"];'

LANES = [
    ("Create the data", [
        ("Workforce simulator", "Month-by-month company: exits, hiring funnel, promotions, pay, surveys, comments",
         "Python · NumPy · seed 42"),
        ("HRMS-style export", "16 CSV files + manifest with row counts and SHA-256 fingerprints, kept in the repo", "CSV"),
    ]),
    ("Engineer & check", [
        ("Staging", "Raw files loaded untouched; fingerprints verified first", "SQLAlchemy"),
        ("Clean & type", "Trim, standardise, parse dates, remove duplicates, quarantine bad rows", "pandas"),
        ("Quality gate", "138 rules — any blocking failure stops the load", "rule engine"),
    ]),
    ("Store", [
        ("Relational database", "16 linked tables with full job, manager and pay history", "PostgreSQL · SQLite"),
        ("Curated mart", "One row per employee per month — every screen counts the same way", "SQL"),
        ("Aggregate views", "The only objects the Viewer role can read", "DB roles"),
    ]),
    ("Analyse & predict", [
        ("Metric layer", "35 KPIs, one definition and one formula each", "Python · SQL"),
        ("Statistics", "Significance tests, odds ratios, survival curves", "SciPy · statsmodels"),
        ("Models", "Headcount forecast, attrition risk, time to fill, scenarios", "scikit-learn · statsmodels"),
        ("Text analytics", "Sentiment, themes, distinctive words in comments", "VADER · TF-IDF"),
    ]),
    ("Deliver", [
        ("Streamlit app", "4 modules, interactive charts, filters, roles", "Streamlit · Plotly"),
        ("Ask PeopleLens", "Plain-English questions → tested SQL templates", "rule-based"),
        ("Recommendations & report", "Threshold-based actions; monthly HTML / Markdown / CSV report", "Python"),
    ]),
]


def _lanes_html() -> str:
    parts = []
    for i, (title, nodes) in enumerate(LANES, 1):
        cards = "".join(
            f"<div class='pl-node{' gate' if name == 'Quality gate' else ''}'><div class='pl-node-t'>{html.escape(name)}"
            f"</div><div class='pl-node-d'>{html.escape(desc)}</div><div class='pl-node-k'>{html.escape(tools)}</div>"
            "</div>" for name, desc, tools in nodes)
        parts.append(f"<div class='pl-lane'><div class='pl-lane-h'><span>{i}</span>{html.escape(title)}</div>{cards}"
                     "</div>")
    return "<div class='pl-flow'>" + "<div class='pl-arrow'>➜</div>".join(parts) + "</div>"


def _table(df: pd.DataFrame) -> None:
    """Static table that wraps long text (st.dataframe truncates it)."""
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row) + "</tr>"
                   for row in df.itertuples(index=False))
    st.markdown(f"<table class='pl-table'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>",
                unsafe_allow_html=True)


MODEL_FLOWS = {
    "forecast": f"""
digraph {{ rankdir=LR; bgcolor="transparent"; {_NODE} {_EDGE}
  a [label="Monthly headcount,\\nhires & exits\\n(30+ months)"];
  b [label="Accepted offers with\\nfuture joining dates"];
  c [label="6 candidate models\\nNaive · Drift · Holt · Holt-Winters\\nSARIMA · Flow model", fillcolor="#E0F2FE"];
  d [label="Rolling backtest\\n3 origins × 6 months\\n(train on past only)", fillcolor="#FEF3C7"];
  e [label="Keep flow model unless a\\nchallenger is >5% better"];
  f [label="6-month forecast\\n+ 80% range\\n+ hiring demand", fillcolor="#CCFBF1"];
  a -> c; b -> c; c -> d -> e -> f;
}}""",
    "attrition": f"""
digraph {{ rankdir=LR; bgcolor="transparent"; {_NODE} {_EDGE}
  a [label="Employee × quarter-end\\nsnapshots"];
  b [label="Features known on that date\\nengagement, pay vs peers, level,\\ntenure, rating, manager change…"];
  l [label="Label: left voluntarily\\nin next 6 months?"];
  c [label="Logistic regression\\nvs Gradient boosting", fillcolor="#E0F2FE"];
  d [label="Time split\\ntrain 2024–25 → test Mar 2026", fillcolor="#FEF3C7"];
  f [label="Risk by team & driver\\n(never used to judge a person)", fillcolor="#CCFBF1"];
  a -> b -> c; l -> c; c -> d -> f;
}}""",
    "ttf": f"""
digraph {{ rankdir=LR; bgcolor="transparent"; {_NODE} {_EDGE}
  a [label="Requisitions\\nopened → filled / still open"];
  c [label="Kaplan-Meier survival curves\\n(open roles counted correctly)", fillcolor="#E0F2FE"];
  m [label="Point models vs\\nglobal median baseline", fillcolor="#FEF3C7"];
  f [label="Plan with P50 / P80 days\\nper job family", fillcolor="#CCFBF1"];
  a -> c -> f; a -> m; m -> f [label=" none beat baseline", style=dashed];
}}""",
    "ask": f"""
digraph {{ rankdir=LR; bgcolor="transparent"; {_NODE} {_EDGE}
  q [label="Question in\\nplain English"];
  p [label="Intent parser\\nkeyword & phrase rules"];
  e [label="Entity extraction\\ndepartment, period, metric\\n(fuzzy match to real names)"];
  g [label="Safety check\\nrefuse firing / individual data", fillcolor="#FEE2E2"];
  t [label="1 of 20 tested SQL templates\\n(parameters, never pasted text)", fillcolor="#E0F2FE"];
  r [label="Answer + chart + SQL\\n+ definition + caveat", fillcolor="#CCFBF1"];
  q -> p -> e -> g -> t -> r;
}}""",
}


def _load(name: str) -> dict:
    path = ARTIFACT_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _stage(num: int, title: str, what: str, tools: list[str], why: str, numbers: str, where: str) -> None:
    with st.container(border=True):
        st.markdown(f"<div class='pl-eyebrow'>Step {num}</div>", unsafe_allow_html=True)
        st.markdown(f"#### {title}")
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            st.markdown(f"**What happens.** {what}")
            st.markdown(f"**Why it's done this way.** {why}")
        with c2:
            st.markdown("**Tools**")
            chips(tools)
            st.markdown(f"**In numbers.** {numbers}")
            st.markdown(f"**See it in the app.** {where}")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render() -> None:
    v = data.data_version()
    s = data.status(v)
    page_header("Workspace", "How PeopleLens Works",
                "The technical story end to end: how the data is created, checked and stored, which statistical "
                "methods and models are used and why, how well they perform, and how results reach each screen.")

    c = st.columns(5)
    metric(c[0], "Employees simulated", f"{s.get('employees', 0):,}")
    metric(c[1], "Source rows", f"{s.get('total_rows', 0):,}")
    dq = s.get("dq", {})
    metric(c[2], "Quality checks passed", f"{dq.get('PASS', 0)} / {sum(dq.values()) if dq else 0}")
    metric(c[3], "Defined KPIs", "35")
    metric(c[4], "Automated tests", "102")

    tabs = st.tabs(["End-to-end flow", "Step by step", "Models explained", "Tech stack", "Design decisions",
                    "Key terms"])
    with tabs[0]:
        _flow()
    with tabs[1]:
        _steps(s)
    with tabs[2]:
        _models()
    with tabs[3]:
        _stack()
    with tabs[4]:
        _decisions()
    with tabs[5]:
        _terms()


def _flow() -> None:
    insight("**Read left to right.** Data is **created** by a simulator, **engineered and "
            "checked** like a real HR export, **stored** in a relational database, **analysed and modelled**, "
            "then **delivered** through the app, the monthly report and recommendations. Nothing reaches the "
            "dashboards unless every blocking quality rule passes.")
    st.markdown(_lanes_html(), unsafe_allow_html=True)
    caption("The same flow runs locally, in Docker with PostgreSQL, and on Streamlit Community Cloud (SQLite). "
            "One command rebuilds everything: python scripts/run_pipeline.py — same seed, same data.")


def _steps(s: dict) -> None:
    fc = _load("workforce_forecast.json")
    at = _load("attrition_model.json")
    sel = fc.get("selected_model", "Flow model")
    auc = at.get("results", {}).get(at.get("primary_model", "logistic_regression"), {}).get("roc_auc")
    _stage(1, "Create a realistic synthetic company",
           "A simulator steps through 36 months (Oct 2023 – Sep 2026). Each month it decides who leaves (a risk "
           "score driven by engagement, pay, tenure and team), runs hiring for open roles through a full funnel "
           "(applied → screened → interviewed → offered → joined), gives promotions and raises in an April cycle, "
           "runs quarterly surveys and writes free-text comments.",
           ["Python", "NumPy", "pandas", "seed 42"],
           "Real HR data is confidential. Simulating cause and effect (rather than random columns) means the analysis "
           "has genuine patterns to find — and a few deliberate non-patterns (location, gender) to check the "
           "analysis does not invent effects.",
           f"{s.get('employees', 0):,} employees, 25 departments, 7 cities, {s.get('candidates', 0):,} candidates.",
           "Sidebar → Data status.")
    _stage(2, "Export it like a real HR system",
           "The tables are written as CSV files with a manifest listing row counts and a SHA-256 fingerprint of each "
           "file. Realistic mess is injected on purpose: stray spaces, mixed case, duplicates, a few invalid values.",
           ["CSV", "SHA-256 manifest"],
           "Analytics teams receive exports, not clean tables. The fingerprint proves the files were not altered or "
           "half-written before loading.",
           "16 CSV files, kept in the repository (data/dataset); the load refuses to start if any count or fingerprint differs.",
           "Data & Governance → Lineage & refresh.")
    _stage(3, "Clean, validate and only then publish",
           "Raw files land in staging tables. pandas trims and standardises text, parses dates, removes duplicates and "
           "moves impossible rows to a quarantine folder. Then 138 quality rules run: keys, relationships, "
           "no overlapping job records, nobody managed by themselves, headcount that reconciles month to month, "
           "and more. Any blocking failure stops the load and the previous data stays live.",
           ["pandas", "SQLAlchemy", "custom rule engine"],
           "A dashboard is only as trustworthy as its inputs. A quality gate that blocks publication is how "
           "production data teams prevent silently wrong numbers.",
           f"138 rules · last build: {s.get('dq', {}).get('PASS', 0)} passed, {s.get('dq', {}).get('FAIL', 0)} failed.",
           "Data & Governance → Data quality.")
    _stage(4, "Store it in a relational model",
           "16 normalised tables around one employee ID — history tables keep every job, manager and pay change with "
           "start/end dates. Everything is loaded in one transaction. A curated table (one row per employee per "
           "month) and aggregate views are built on top.",
           ["PostgreSQL 16", "SQLite", "SQLAlchemy Core", "SQL views"],
           "History tables let you ask 'what did the company look like on any date'. The monthly mart means every "
           "screen filters and counts the same way. PostgreSQL is the production target; SQLite lets the free demo "
           "run with no server — the same SQL works on both.",
           f"{s.get('total_rows', 0):,} source rows; ~750k including the mart.",
           "Data & Governance → Data dictionary · SQL Explorer.")
    _stage(5, "Define every KPI once",
           "35 metrics (attrition, time to hire, eNPS, plan variance, cost per head…) each have one written "
           "definition and one Python implementation. Dashboards, the report and Ask PeopleLens all call the same "
           "function.",
           ["Python", "SQL", "metric dictionary"],
           "The classic analytics failure is two reports showing two different attrition numbers. One definition "
           "removes that — a test even checks Ask PeopleLens returns the identical figure.",
           "Attrition = exits ÷ average headcount, annualised (not ÷ current headcount).",
           "Data & Governance → Metric glossary · KPI tooltips (ⓘ).")
    _stage(6, "Diagnose with statistics",
           "Before claiming a department is 'high attrition', the app tests it: confidence intervals, two-proportion "
           "tests corrected for multiple comparisons (Holm), a logistic regression giving odds ratios for drivers, "
           "survival curves for retention by hiring source, and a confounding check (does location matter once "
           "department is accounted for?).",
           ["SciPy", "statsmodels"],
           "With 24 departments, some will look different by chance. Correcting for that separates real hotspots "
           "from noise — only 2 departments stay significant.",
           "Customer Support attrition ≈ 1.75× the rest; location shows no real effect (as designed).",
           "Talent & Lifecycle → Statistical diagnostics.")
    _stage(7, "Predict: forecasts and risk",
           "Three models answer three planning questions: how headcount will move (6-month forecast), which groups "
           "are more likely to leave (attrition risk), and how long roles take to fill. Each is tested on time "
           "periods it never saw and compared against a simple baseline. A scenario tool then applies 'what-if' "
           "arithmetic (e.g. attrition +3 points, hiring freeze).",
           ["scikit-learn", "statsmodels", "joblib"],
           "A model is only worth using if it beats the simplest reasonable guess on future data. Where it does not "
           "(time to fill), the app says so and plans with ranges instead.",
           f"Forecast: {sel} selected · Attrition model ROC-AUC {auc:.2f}" if auc else f"Forecast: {sel} selected.",
           "Predictive Intelligence (all tabs) · Models explained tab here.")
    _stage(8, "Read the comments",
           "Survey comments are cleaned, scored for sentiment (VADER plus a small workplace dictionary), tagged with "
           "themes (career, workload, manager, pay…) and compared across departments with TF-IDF.",
           ["VADER", "scikit-learn TF-IDF", "regex lexicon"],
           "Transparent, offline and free — every score can be explained. Comments carry no employee ID and groups "
           "under 10 comments are hidden.",
           "Accuracy and sarcasm misreads are measured and shown, not hidden.",
           "Talent & Lifecycle → Engagement & feedback.")
    _stage(9, "Answer questions and recommend actions",
           "Ask PeopleLens turns a plain-English question into one of 20 tested SQL queries and explains the "
           "answer. Rule-based recommendations fire when metrics cross thresholds (e.g. early attrition high in a "
           "team) and each shows its evidence.",
           ["rule-based parser", "parameterised SQL"],
           "No paid AI API: answers are always correct for supported questions, fully auditable, and the engine can "
           "never write to the database.",
           "20 question types · high-stakes questions (e.g. firing) are refused.",
           "Ask PeopleLens · Planning & Business Insights.")
    _stage(10, "Deliver, protect and report",
           "The Streamlit app presents four modules with Plotly charts; a monthly executive report is generated as "
           "HTML/Markdown/CSV. Two roles (Analyst, Viewer) are enforced by the database connection itself, not just "
           "hidden buttons.",
           ["Streamlit", "Plotly", "read-only DB roles"],
           "Governance has to hold even if someone types their own SQL: the Viewer connection physically cannot read "
           "employee-level tables or write anything.",
           "SQL runs read-only with a row cap and 10-second timeout.",
           "Data & Governance → Access control.")


def _model_card(title: str, question: str, flow_key: str) -> None:
    st.markdown(f"### {title}")
    st.markdown(f"<div class='pl-lede'><b>Business question:</b> {question}</div>", unsafe_allow_html=True)
    st.graphviz_chart(MODEL_FLOWS[flow_key], width="stretch")


def _models() -> None:
    fc, at, hf = _load("workforce_forecast.json"), _load("attrition_model.json"), _load("hiring_forecast.json")
    insight("**One rule for every model:** train only on the past, test on a later period it "
            "never saw, and compare against a simple baseline. Keep the model only if it clearly wins — and say so "
            "when it doesn't.")

    summary = pd.DataFrame([
        ["Headcount forecast", "Naive, Drift, Holt, Holt-Winters, SARIMA, Flow model", fc.get("selected_model", "Flow model"),
         "SARIMA a near-tie; flow kept (explainable, uses hiring pipeline, powers what-ifs)", "Rolling backtest, 3 origins"],
        ["Attrition risk", "Logistic regression, Gradient boosting", "Logistic regression",
         "Boosting not materially better; LR is explainable", "Train 2024–25 → test Mar 2026"],
        ["Time to fill", "Global median, Segment median, Gradient boosting", "Kaplan-Meier ranges",
         "No point model beat the baseline", "Chronological hold-out"],
        ["Attrition drivers (what-if)", "—", "Re-score with the attrition model",
         "Links engagement, pay, promotions to leavers, headcount and cost", "Baseline & direction tests"],
        ["Scenarios", "—", "Deterministic arithmetic", "What-ifs must be transparent, not 'predicted'", "Direction tests"],
        ["Comment sentiment", "VADER", "VADER + workplace lexicon", "Offline, free, explainable",
         "Against generator labels"],
        ["Ask PeopleLens", "LLM text-to-SQL (rejected)", "Rule-based templates", "Correct, auditable, zero cost",
         "Answers = central metrics (test)"],
    ], columns=["Problem", "Options compared", "Chosen", "Why", "How it was validated"])
    _table(summary)

    st.divider()
    _model_card("1 · Headcount forecast", "How many people will we have over the next six months, and how many "
                "must we hire to reach plan?", "forecast")
    comp = pd.DataFrame(fc.get("model_comparison", []))
    if len(comp):
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            comp = comp.sort_values("mae")
            fig = charts.bar(comp, "model", "mae", title="Average error in backtests (heads, lower is better)",
                             horizontal=True, height=260)
            st.plotly_chart(fig, width="stretch")
        with c2:
            best = comp[comp.model == fc.get("selected_model", "Flow model")].iloc[0]
            naive = comp[comp.model == "Naive"]
            sar = comp[comp.model.str.startswith("SARIMA")]
            cut = (1 - best.mae / naive.mae.iloc[0]) if len(naive) else None
            st.markdown(
                f"**Chosen: {best.model}.** It works like a bank statement: next month = this month − expected "
                "exits (seasonal exit rate) + expected hires (offers already accepted, plus the recent hiring pace).\n\n"
                f"**Result:** average error **{best.mae:.0f} heads** on a ~6,000 workforce"
                + (f", **{cut:.0%} better** than simply assuming no change." if cut else ".")
                + (f"\n\n**SARIMA** (classic statistical model, settings chosen by AIC on training data only) scored "
                   f"**{sar.mae.iloc[0]:.1f}** — a near-tie. The rule is to replace the incumbent only for a >5% gain, "
                   "so the explainable flow model, which also powers the what-if tools, is kept." if len(sar) else "")
                + "\n\n**Why not Holt-Winters?** It needs several full years to learn seasonality; with ~30 months it "
                  "over-fits. **Honest note:** for monthly exits alone, a seasonal-naive guess is slightly better — "
                  "reported in Model monitoring.")

    st.divider()
    _model_card("2 · Attrition risk", "Which teams and groups are more likely to lose people voluntarily in the next "
                "six months, and what is associated with it?", "attrition")
    res = at.get("results", {})
    lr, gb = res.get("logistic_regression", {}), res.get("gradient_boosting", {})
    if lr:
        c = st.columns(4)
        metric(c[0], "ROC-AUC", f"{lr['roc_auc']:.2f}", help_extra="0.5 = coin toss, 1.0 = perfect ranking.")
        metric(c[1], "Top-10% lift", f"{lr['top_decile_lift']:.1f}×",
               help_extra="Leavers are this many times more common in the highest-risk 10% than on average.")
        metric(c[2], "Leavers found", f"{lr['recall']:.0%}", help_extra="Share of actual leavers flagged at the threshold.")
        metric(c[3], "Base rate", f"{lr['base_rate']:.1%}", help_extra="Share who actually left in the test period.")
        coefs = at.get("coefficients")
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            if isinstance(coefs, list) and coefs:
                cf = pd.DataFrame(coefs)
                cf = cf[cf.feature.str.startswith("num__")]
                if len(cf):
                    cf = cf.reindex(cf.coefficient.abs().sort_values(ascending=False).index).head(8)
                    fig = charts.bar(cf, "label", "coefficient", horizontal=True, height=300, hover_fmt="+.2f",
                                     highlight=cf.loc[cf.coefficient > 0, "label"].tolist(),
                                     title="Strongest numeric drivers (standardised coefficients)")
                    st.plotly_chart(fig, width="stretch")
                    caption("Amber bars raise the risk of leaving; teal bars lower it. Associations, not causes.")
        with c2:
            st.markdown(
                "**Chosen: logistic regression.** Each driver gets a clear direction and size, so HR can see *why* a "
                "group is at risk. Gradient boosting was tried"
                + (f" (AUC {gb['roc_auc']:.2f})" if gb else "")
                + " and was not materially better, so the simpler, explainable model wins.\n\n"
                  "**No leakage:** features use only information available on the prediction date; exit reasons, "
                  "later surveys and gender are excluded.\n\n"
                  "**Honest reading:** it ranks risk better than chance but misses many individual leavers — fit for "
                  "planning at team level, never for decisions about a person.")

    st.divider()
    _model_card("3 · Time to fill", "How long will a new role take to fill, so we open it early enough?", "ttf")
    ev = pd.DataFrame(hf.get("evaluation", {}).get("results", []))
    if len(ev):
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            show(ev[["model", "mae", "n"]].rename(columns={"mae": "avg error (days)", "n": "roles tested"}),
                 {"avg error (days)": "float1", "roles tested": "int"})
        with c2:
            st.markdown("**Chosen: survival curves (Kaplan-Meier).** Roles still open are counted correctly instead "
                        "of being ignored (which would make hiring look faster than it is). Since no point-prediction "
                        "model beat the simple median, planning uses ranges: *open the role P80 days + notice period "
                        "before you need the person*.")

    st.divider()
    st.markdown("### 4 · Scenario explorer")
    st.markdown("Not a trained model on purpose. A planner changes assumptions (attrition ±, hiring pace, delays, pay "
                "inflation) and the app recalculates headcount, cost and turnover cost month by month with plain "
                "arithmetic, so every number can be traced. Tests check the same inputs always give the same output "
                "and that, e.g., higher attrition never *raises* headcount.")

    st.divider()
    st.markdown("### 5 · Text analytics (survey comments)")
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("**Sentiment — VADER + workplace lexicon.** A dictionary-based scorer: fast, offline, explainable. "
                    "General dictionaries miss workplace language ('burnout', 'firefighting' scored neutral), so a "
                    "small documented lexicon was added.\n\n**Limits shown in the app:** about 6 in 10 single-tone "
                    "comments agree with the intended label; sarcasm ('Great, another quarter of doing two jobs') is "
                    "misread — so sentiment is used for trends across groups, not to judge any one comment.")
    with c2:
        st.markdown("**Themes — keyword lexicon (multi-label).** Career growth, workload, manager support, pay, "
                    "recognition, work-life balance, learning, collaboration. Transparent rules; ~97% of comments get "
                    "their intended theme.\n\n**Distinctive terms — TF-IDF.** Highlights words a department uses more "
                    "than others (e.g. Customer Support → 'workload', 'targets').")

    st.divider()
    _model_card("6 · Ask PeopleLens", "Can a manager ask a question in plain English and get a correct, explained "
                "answer?", "ask")
    st.markdown("A deterministic engine, not a chatbot: the question is matched to one of 20 known question types and "
                "the details (department, period) are extracted, then a pre-tested query runs. Unknown questions get "
                "suggestions instead of a guess.")


def _stack() -> None:
    stack = pd.DataFrame([
        ["Language", "Python 3.12–3.13", "Everything", "Standard for analytics; one language end to end"],
        ["Data generation", "NumPy, pandas", "Simulate employees, hiring, surveys", "Fast vectorised maths, seeded"],
        ["Database", "PostgreSQL 16 / SQLite", "Store 16 tables + mart + views", "Production-grade vs zero-setup demo"],
        ["Database access", "SQLAlchemy Core, psycopg 3", "Schema, loading, queries", "Same code on both databases"],
        ["SQL safety", "sqlparse, DB read-only roles", "SQL Explorer", "Only SELECT, enforced by the database"],
        ["Statistics", "SciPy, statsmodels", "Tests, odds ratios, survival, smoothing", "Proper inference & p-values"],
        ["Machine learning", "scikit-learn", "Attrition model, boosting, TF-IDF", "Reliable, well-documented"],
        ["Text", "VADER (vaderSentiment)", "Comment sentiment", "Offline, free, explainable"],
        ["App", "Streamlit", "Web interface, navigation, caching", "Python-only, free hosting"],
        ["Charts", "Plotly", "Interactive charts", "Hover detail, consistent styling"],
        ["Testing & quality", "pytest, Ruff", "102 tests, code style", "Catch regressions before deploy"],
        ["UI checks", "Playwright", "Headless browser tests, screenshots", "Proves pages render without errors"],
        ["Packaging", "Docker, docker-compose", "App + PostgreSQL in one command", "Reproducible setup"],
        ["Hosting", "Streamlit Community Cloud", "Public demo", "Free; rebuilds data on start"],
    ], columns=["Layer", "Tool", "Used for", "Why this tool"])
    _table(stack)
    caption("No paid services or API keys are used anywhere.")


def _decisions() -> None:
    items = [
        ("Simulate behaviour, not random columns",
         "Exits, hires and promotions follow cause-and-effect rules, so analyses find real patterns — and the "
         "'no effect' variables (location, gender) test that the analysis does not invent findings."),
        ("Quality gate before publishing",
         "Bad data never reaches a dashboard; a failed load leaves the previous version live. Running on PostgreSQL "
         "exposed text-length issues SQLite ignored — they became a permanent rule."),
        ("Attrition ÷ average headcount",
         "Dividing by current headcount overstates attrition in shrinking teams and understates it in growing ones."),
        ("Time-based validation only",
         "Shuffled train/test splits leak the future into training and overstate accuracy. Every model is tested on "
         "a later period."),
        ("Baselines first",
         "A model is kept only if it beats the simplest reasonable guess. Time to fill failed that test, so the app "
         "uses ranges instead — reported, not hidden."),
        ("Explainable over marginally better",
         "Logistic regression was chosen over gradient boosting because the accuracy gap was negligible and HR needs "
         "to see the drivers."),
        ("Censoring handled",
         "Open requisitions and recent joiners have incomplete outcomes. Survival methods count them correctly instead "
         "of making hiring look faster or retention look better."),
        ("Rule-based Ask, not an LLM",
         "Guaranteed-correct numbers, visible SQL, no cost, no risk of generated write queries."),
        ("Governance enforced by the database",
         "The Viewer role's connection physically cannot read employee-level tables, so the rule holds even for "
         "hand-written SQL."),
        ("Groups, not individuals",
         "Predictions describe teams; the product refuses firing or disciplinary questions and hides groups under 10."),
    ]
    for title, text in items:
        insight(f"**{title}.** {text}")


def _terms() -> None:
    terms = pd.DataFrame([
        ["Seed", "A starting number for the random generator. Same seed → identical data every run."],
        ["Staging table", "A raw landing table where data sits before it is cleaned and checked."],
        ["Quality gate", "Automatic rules the data must pass before it is published."],
        ["Mart", "A ready-to-analyse table built for reporting (here: one row per employee per month)."],
        ["Baseline", "The simplest reasonable guess (e.g. 'next month = this month') a model must beat."],
        ["Backtest", "Pretend it is an earlier date, forecast, then compare with what actually happened."],
        ["MAE", "Mean absolute error — the average size of the miss, in the original units (heads, days)."],
        ["ROC-AUC", "How well a model ranks people who leave above people who stay: 0.5 = random, 1.0 = perfect."],
        ["Lift", "How much more common leavers are in the model's highest-risk group than on average."],
        ["Leakage", "Accidentally letting the model see information from the future; makes results look too good."],
        ["Censoring", "Outcomes not finished yet (role still open, employee still here) — must not be ignored."],
        ["Kaplan-Meier", "A survival curve: the share still 'open' (or still employed) after each number of days."],
        ["Holm correction", "Adjusts p-values when testing many groups so chance differences aren't called real."],
        ["Odds ratio", "How much a factor multiplies the odds of leaving (below 1 = protective)."],
        ["TF-IDF", "Scores words that are frequent in one group's comments but rare elsewhere."],
        ["eNPS", "Employee Net Promoter Score: % promoters (9–10) minus % detractors (0–6)."],
    ], columns=["Term", "Plain-English meaning"])
    _table(terms)
