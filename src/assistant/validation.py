"""Input validation and policy checks for Ask PeopleLens questions."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LEN = 300
SQL_LIKE = re.compile(r"\b(select|insert|update|delete|drop|alter|union|pragma)\b.*\b(from|into|table|set|all)\b|;|--",
                      re.I)


@dataclass
class PolicyResult:
    allowed: bool
    message: str = ""


def check_question(text: str) -> PolicyResult:
    if not text or not text.strip():
        return PolicyResult(False, "Type a question about the synthetic workforce data.")
    if len(text) > MAX_LEN:
        return PolicyResult(False, f"Please keep questions under {MAX_LEN} characters.")
    if SQL_LIKE.search(text):
        return PolicyResult(False, "Ask PeopleLens does not execute SQL typed into the question box. Use the SQL "
                                   "Explorer in Workforce Explorer for read-only queries.")
    return PolicyResult(True)


HIGH_STAKES_MESSAGE = (
    "PeopleLens does not recommend firing, disciplining or other employment decisions about individuals. "
    "Predictive scores describe groups of similar employees in a synthetic dataset and are meant to prompt "
    "supportive conversations and better planning, not decisions about a person. You could ask instead: "
    "\"Which departments have the highest voluntary attrition?\" or \"What are the main drivers associated "
    "with attrition?\"")

INDIVIDUAL_MESSAGE = (
    "Ask PeopleLens answers with aggregated results and does not return individual employees' salaries, ratings "
    "or risk scores. Analysts can browse synthetic IDs in the Workforce Explorer record view; everyone else sees "
    "aggregates only.")
