"""Turns parsed questions into evidence-backed answers.

Answer structure: question understood -> metric definition -> filters applied
-> result -> comparison/trend -> interpretation -> caveat. Every number in an
answer comes from the query result or a saved model artefact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy.engine import Engine

from src.analytics.common import data_bounds, lookups
from src.assistant.intent_parser import ParsedQuestion, parse
from src.assistant.query_templates import HANDLERS, INTENT_LABELS, Context, Evidence
from src.assistant.validation import HIGH_STAKES_MESSAGE, INDIVIDUAL_MESSAGE, check_question
from src.governance.metric_dictionary import METRICS

EXAMPLES = [
    "Which departments had the highest attrition last quarter?",
    "How many employees joined in the previous six months?",
    "What is the median time-to-hire by recruitment source?",
    "Compare headcount versus plan across departments.",
    "Which tenure cohort has the lowest retention?",
    "What is the estimated turnover cost for the Operations department?",
    "How has engagement changed over the last four quarters?",
    "What is the forecast headcount for the next three months?",
    "Explain the main drivers associated with attrition in this dataset.",
    "Show the departments where hiring demand exceeds the workforce plan.",
]


@dataclass
class Answer:
    question: str
    status: str  # answered | unsupported | refused | error
    intent: str | None = None
    intent_label: str | None = None
    parsed: ParsedQuestion | None = None
    filters_text: str = ""
    evidence: Evidence | None = None
    metric_definition: str = ""
    text: str = ""
    suggestions: list[str] = field(default_factory=list)


class Assistant:
    def __init__(self, engine: Engine | None = None):
        from database.connection import get_readonly_engine

        self.engine = engine or get_readonly_engine("analyst")
        self.lookups = lookups(self.engine)
        self.data_start, self.data_end = data_bounds(self.engine)
        self.ctx = Context(self.engine, self.lookups, self.data_start, self.data_end)
        self.departments = list(self.lookups["departments"].values())
        self.business_units = sorted(set(self.lookups["dept_bu"].values()) - {"Executive"})

    def ask(self, question: str) -> Answer:
        check = check_question(question)
        if not check.allowed:
            return Answer(question, "refused", text=check.message, suggestions=EXAMPLES[:3])
        pq = parse(question, self.data_start, self.data_end, self.departments, self.business_units)
        if "high_stakes" in pq.flags:
            return Answer(question, "refused", parsed=pq, text=HIGH_STAKES_MESSAGE, suggestions=EXAMPLES[:2])
        if "individual" in pq.flags:
            return Answer(question, "refused", parsed=pq, text=INDIVIDUAL_MESSAGE, suggestions=EXAMPLES[:3])
        if pq.intent is None or pq.intent not in HANDLERS:
            return Answer(question, "unsupported", parsed=pq,
                          text="I couldn't match that to a supported question. Ask PeopleLens covers headcount, "
                               "joiners, attrition, retention, recruitment, engagement, compensation and cost, plan "
                               "vs actual, performance, promotions, forecasts and attrition drivers.",
                          suggestions=EXAMPLES[:4])
        try:
            ev = HANDLERS[pq.intent](pq, self.ctx)
        except Exception as exc:  # surface, never invent
            return Answer(question, "error", intent=pq.intent, parsed=pq,
                          text=f"The query for this question failed ({type(exc).__name__}: {exc}). No answer was "
                               "generated rather than guessing.", suggestions=EXAMPLES[:3])
        if ev.table is None or (isinstance(ev.table, pd.DataFrame) and ev.table.empty and not ev.headline):
            return Answer(question, "answered", intent=pq.intent, parsed=pq, evidence=ev,
                          text="The data contains no records for this scope, so there is nothing to report.")
        metric = METRICS.get(ev.metric_key) if ev.metric_key else None
        filters = [f"Period: {pq.period_label} ({pq.start:%b %Y} – {pq.end:%b %Y})"]
        if pq.departments:
            filters.append("Department: " + ", ".join(pq.departments))
        if pq.business_units:
            filters.append("Business unit: " + ", ".join(pq.business_units))
        if pq.locations:
            filters.append("Location: " + ", ".join(pq.locations))
        if pq.levels:
            filters.append("Level: " + ", ".join(pq.levels))
        if pq.breakdown:
            filters.append("Breakdown: " + pq.breakdown.replace("_", " "))
        if ev.intent in ("forecast", "drivers", "hiring_demand"):
            filters[0] = f"Horizon: {pq.horizon_months} months" if ev.intent == "forecast" else filters[0]
        text = "\n\n".join(x for x in [ev.headline, ev.comparison, ev.interpretation,
                                       f"*Caveat:* {ev.caveat}" if ev.caveat else ""] if x)
        return Answer(question, "answered", intent=pq.intent, intent_label=INTENT_LABELS.get(pq.intent),
                      parsed=pq, filters_text=" · ".join(filters), evidence=ev,
                      metric_definition=f"**{metric.name}** — {metric.definition} Formula: `{metric.formula}`"
                      if metric else "", text=text)
