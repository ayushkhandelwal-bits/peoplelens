"""Ask PeopleLens: intent parsing, real-result answers, refusals and safety."""

from __future__ import annotations

from datetime import date

import pytest

from src.analytics.common import Filters, add_months, data_bounds
from src.analytics.metrics import kpi_summary
from src.assistant.answer_generator import EXAMPLES, Assistant
from src.assistant.intent_parser import parse, parse_period

DEPTS = ["Customer Support", "Service Operations West", "Platform Engineering"]
BUS = ["Technology", "Operations", "Commercial", "Corporate"]


@pytest.fixture(scope="module")
def bot(engine):
    return Assistant()


@pytest.mark.parametrize("q,intent", [
    ("Which departments had the highest attrition last quarter?", "attrition"),
    ("How many employees joined in the previous six months?", "joiners"),
    ("What is the median time-to-hire by recruitment source?", "time_to_hire"),
    ("Compare headcount versus plan across departments.", "plan_vs_actual"),
    ("Which tenure cohort has the lowest retention?", "early_attrition"),
    ("What is the estimated turnover cost for the Operations department?", "turnover_cost"),
    ("How has engagement changed over the last four quarters?", "engagement"),
    ("What is the forecast headcount for the next three months?", "forecast"),
    ("Explain the main drivers associated with attrition in this dataset.", "drivers"),
    ("Show the departments where hiring demand exceeds the workforce plan.", "hiring_demand"),
    ("headcount in Bengaluru", "headcount"),
    ("What's our offer acceptance rate?", "funnel"),
])
def test_intents(q, intent):
    pq = parse(q, date(2024, 10, 31), date(2026, 9, 30), DEPTS, BUS)
    assert pq.intent == intent


def test_entities_and_periods():
    pq = parse("Attrition in Customer Support for L2 in Pune in 2025", date(2024, 10, 31), date(2026, 9, 30), DEPTS, BUS)
    assert pq.departments == ["Customer Support"] and pq.levels == ["L2"] and pq.locations == ["Pune"]
    assert (pq.start, pq.end) == (date(2025, 1, 31), date(2025, 12, 31))
    s, e, _ = parse_period("last quarter", date(2026, 9, 30), date(2024, 10, 31))
    assert (s, e) == (date(2026, 7, 31), date(2026, 9, 30))
    s, e, _ = parse_period("previous six months", date(2026, 9, 30), date(2024, 10, 31))
    assert (s, e) == (date(2026, 4, 30), date(2026, 9, 30))
    pq = parse("cost for the Operations department", date(2024, 10, 31), date(2026, 9, 30), DEPTS, BUS)
    assert pq.business_units == ["Operations"]


@pytest.mark.parametrize("q", EXAMPLES)
def test_examples_are_answered_from_data(bot, q):
    a = bot.ask(q)
    assert a.status == "answered", a.text
    assert a.evidence is not None and not a.evidence.table.empty
    assert a.text


def test_answer_numbers_match_central_metrics(bot, engine):
    lo, hi = data_bounds(engine)
    a = bot.ask("What is the attrition rate in the last 12 months?")
    k = kpi_summary(Filters(start=max(lo, add_months(hi, -11)), end=hi), engine)
    assert f"{k.attrition_rate:.1%}" in a.text
    h = bot.ask("How many employees do we have?")
    assert f"{k.active_headcount:,}" in h.text


def test_sql_is_parameterised_not_question_text(bot):
    a = bot.ask("Which departments had the highest attrition last quarter?")
    assert "last quarter" not in a.evidence.sql and ":s" in a.evidence.sql


@pytest.mark.parametrize("q", ["Should we fire EMP00012?", "Which employees should we let go to cut costs?",
                               "Who should I discipline in Customer Support?"])
def test_high_stakes_refused(bot, q):
    assert bot.ask(q).status == "refused"


def test_individual_and_injection_refused(bot):
    assert bot.ask("What is the salary of EMP00045?").status == "refused"
    assert bot.ask("SELECT * FROM employees; DROP TABLE employees").status == "refused"


def test_unsupported_question(bot):
    a = bot.ask("What will the weather be tomorrow?")
    assert a.status == "unsupported" and a.suggestions
