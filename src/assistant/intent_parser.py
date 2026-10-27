"""Deterministic intent and entity extraction for Ask PeopleLens.

No language model is involved: questions are matched against weighted keyword
patterns per intent, and entities (period, department, business unit,
location, level, breakdown, horizon) are extracted with explicit rules. The
output is a structured, inspectable ``ParsedQuestion``; nothing from the
question text is ever executed as SQL.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18, "twenty-four": 24}

INTENTS: dict[str, list[tuple[str, float]]] = {
    "forecast": [(r"forecast|projection|project(ed)?\b|predict|next \w+ months?|coming months|will .*headcount", 3)],
    "drivers": [(r"driver|factors?|what explains|why (do|are|did) (people|employees)|associated with attrition|"
                 r"predictors?|cause", 3)],
    "hiring_demand": [(r"hiring demand|hiring need|hires? (are )?(needed|required)|demand exceeds|need to hire|"
                       r"requisitions? (to open|needed)", 3.5)],
    "turnover_cost": [(r"turnover cost|cost of (attrition|turnover|exits?)|replacement cost|cost of (people )?leaving",
                       4)],
    "budget_variance": [(r"(cost|spend|budget).*(plan|budget|variance|over|under)|budget variance|"
                         r"(over|under)spend", 2.5)],
    "plan_vs_actual": [(r"\bplan(ned)?\b|\baop\b|versus plan|vs\.? plan|against plan|behind plan|ahead of plan", 2.5)],
    "time_to_hire": [(r"time[- ]to[- ]hire|how (long|quickly) .*hire", 4)],
    "time_to_fill": [(r"time[- ]to[- ]fill|how long .*fill|vacanc(y|ies) .*(days|long)", 4)],
    "source_effectiveness": [(r"(recruitment|sourcing|hiring) (source|channel)|by source|best source|which source|"
                              r"referral|job portal|agency", 3)],
    "funnel": [(r"funnel|conversion|offer acceptance|acceptance rate|offers?|pipeline|candidates?|applications?|"
                r"applicants?", 2)],
    "early_attrition": [(r"early attrition|first (30|60|90) days|within (30|60|90) days|new joiners? (leave|leaving)|"
                         r"\b(30|60|90)[- ]day", 3.5), (r"retention|cohort|retained", 2.5)],
    "engagement": [(r"engage|enps|e-nps|survey|morale|satisfaction|sentiment", 3)],
    "exit_reasons": [(r"exit reasons?|reasons? (for|why) (leaving|exits?)|why .*(leave|leaving|left)", 3)],
    "attrition": [(r"attrition|turnover|churn|leavers?|resign|quit|\bleft\b|exits?\b|leaving", 2)],
    "joiners": [(r"\bjoin(ed|ers?)?\b|new hires?|\bhired\b|onboarded|starters?", 2.2)],
    "headcount": [(r"head ?count|how many (employees|people|staff)|workforce size|size of the workforce|"
                   r"number of employees|\bfte\b|staff count|employees? (do we have|are there)", 2.5)],
    "compensation": [(r"salar(y|ies)|compensation|\bpay\b|payroll|cost per (employee|head)|workforce cost|"
                      r"people cost|manpower cost", 2.4)],
    "performance": [(r"performance|ratings?|high performers?|appraisal", 2.2)],
    "promotion": [(r"promot|internal mobility|transfers?|career moves?", 2.6)],
    "span_of_control": [(r"span of control|direct reports|managers? (have|with)|team size", 3.5)],
}

HIGH_STAKES = re.compile(r"\b(fire|firing|terminate|termination|dismiss|lay ?off|let go|sack|disciplin|"
                         r"punish|demote|who should (we|i) (cut|remove))", re.I)
INDIVIDUAL = re.compile(r"\bEMP\d{3,5}\b|\bemployee (named|called)\b|\b(salary|rating|score) of [A-Z][a-z]+", re.I)

DIM_PATTERNS = {
    "department": r"by department|which departments?|per department|across departments|departments? (with|where|had|have)|"
                  r"department[- ]wise|each department|compare .*departments",
    "location": r"by (location|city|office)|which (locations?|cities|offices?)|per (location|city)|across (locations|cities)",
    "job_level": r"by (job )?level|which levels?|per level|across levels|by grade",
    "business_unit": r"by business unit|which business units?|by bu\b|across business units",
    "source": r"by (recruitment )?source|by channel|which sources?",
    "tenure_band": r"by tenure|which tenure|tenure (band|cohort|group)s?",
    "job_family": r"by (job )?family|which (job )?famil(y|ies)|by role",
}
LOCATION_ALIASES = {"bangalore": "Bengaluru", "bengaluru": "Bengaluru", "mumbai": "Mumbai", "bombay": "Mumbai",
                    "delhi": "Delhi NCR", "ncr": "Delhi NCR", "gurgaon": "Delhi NCR", "gurugram": "Delhi NCR",
                    "noida": "Delhi NCR", "hyderabad": "Hyderabad", "pune": "Pune", "chennai": "Chennai",
                    "madras": "Chennai", "jaipur": "Jaipur"}


@dataclass
class ParsedQuestion:
    text: str
    intent: str | None
    confidence: float
    start: date
    end: date
    period_label: str
    departments: list[str] = field(default_factory=list)  # department names
    business_units: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)  # city names
    levels: list[str] = field(default_factory=list)
    breakdown: str | None = None
    horizon_months: int = 3
    direction: str | None = None  # highest | lowest
    flags: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)


def _num(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def _month_end(ts) -> date:
    return (pd.Timestamp(ts) + pd.offsets.MonthEnd(0)).date()


def parse_period(text: str, data_end: date, data_start: date) -> tuple[date, date, str]:
    t = text.lower()
    end_ts = pd.Timestamp(data_end)
    q_end = (end_ts - pd.offsets.QuarterEnd(0)) if end_ts.is_quarter_end else (end_ts - pd.offsets.QuarterEnd(1))
    m = re.search(r"\b(last|previous|past|prior)\s+(quarter)\b", t)
    if m:
        s = q_end - pd.offsets.MonthEnd(2)
        return _month_end(s), _month_end(q_end), f"last complete quarter ({s:%b}–{q_end:%b %Y})"
    if re.search(r"\b(this|current|latest) quarter\b", t):
        s = q_end - pd.offsets.MonthEnd(2)
        return _month_end(s), _month_end(q_end), f"latest complete quarter ({s:%b}–{q_end:%b %Y})"
    m = re.search(r"\b(last|previous|past|prior)\s+(\d+|[a-z\-]+)\s+(months?|quarters?|years?)\b", t)
    if m and _num(m.group(2)):
        n = _num(m.group(2)) * {"m": 1, "q": 3, "y": 12}[m.group(3)[0]]
        s = end_ts - pd.offsets.MonthEnd(n - 1)
        return _month_end(max(s, pd.Timestamp(data_start))), data_end, f"last {n} months"
    if re.search(r"\b(last|previous|past) (year|12 months)\b", t):
        s = end_ts - pd.offsets.MonthEnd(11)
        return _month_end(s), data_end, "last 12 months"
    if re.search(r"\b(last|previous|past) month\b", t):
        return data_end, data_end, f"{end_ts:%b %Y}"
    m = re.search(r"\bq([1-4])\s*(?:fy)?\s*'?(20\d{2})\b", t) or re.search(r"\b(20\d{2})\s*q([1-4])\b", t)
    if m:
        a, b = m.groups()
        qn, yr = (int(a), int(b)) if len(a) == 1 else (int(b), int(a))
        e = pd.Timestamp(yr, qn * 3, 1) + pd.offsets.MonthEnd(0)
        s = e - pd.offsets.MonthEnd(2)
        return _month_end(max(s, pd.Timestamp(data_start))), _month_end(min(e, end_ts)), f"Q{qn} {yr}"
    m = re.search(r"\bfy\s*'?(\d{2,4})\b", t)
    if m:
        yr = int(m.group(1)) % 100 + 2000
        s, e = pd.Timestamp(yr - 1, 4, 30), pd.Timestamp(yr, 3, 31)
        return _month_end(max(s, pd.Timestamp(data_start))), _month_end(min(e, end_ts)), f"FY{yr % 100:02d} (Apr–Mar)"
    m = re.search(r"\b(?:in|during|for)\s+(20\d{2})\b", t) or re.search(r"\b(20\d{2})\b", t)
    if m:
        yr = int(m.group(1))
        s, e = pd.Timestamp(yr, 1, 31), pd.Timestamp(yr, 12, 31)
        if e >= pd.Timestamp(data_start) and s <= end_ts:
            return (_month_end(max(s, pd.Timestamp(data_start))), _month_end(min(e, end_ts)),
                    f"calendar year {yr}" + (" (to date)" if e > end_ts else ""))
    if re.search(r"\b(ytd|year to date|this year)\b", t):
        s = pd.Timestamp(end_ts.year, 1, 31)
        return _month_end(s), data_end, f"{end_ts.year} year to date"
    if re.search(r"\b(all time|whole period|entire history|overall history)\b", t):
        return data_start, data_end, "full history"
    s = end_ts - pd.offsets.MonthEnd(11)
    return _month_end(max(s, pd.Timestamp(data_start))), data_end, "last 12 months (default)"


def parse(text: str, data_start: date, data_end: date, departments: list[str], business_units: list[str]
          ) -> ParsedQuestion:
    t = " ".join(text.strip().split())
    low = t.lower()
    scores = {}
    for intent, pats in INTENTS.items():
        s = sum(w for p, w in pats if re.search(p, low))
        if s:
            scores[intent] = s
    # disambiguation rules
    if "headcount" in scores and ("plan" in low or "aop" in low):
        scores["plan_vs_actual"] = scores.get("plan_vs_actual", 0) + 2
    if "joiners" in scores and re.search(r"\b(left|leave|retention|early)\b", low):
        scores["joiners"] -= 1.5
    if "joiners" in scores and "headcount" in scores and re.search(r"\b(joined|join|hired|joiners)\b", low):
        scores["headcount"] = 0
    if "attrition" in scores and "turnover_cost" in scores:
        scores["attrition"] = 0
    if "compensation" in scores and "turnover_cost" in scores:
        scores["compensation"] = 0
    if "compensation" in scores and "budget_variance" in scores and re.search(r"plan|budget|variance", low):
        scores["compensation"] = 0
    if "plan_vs_actual" in scores and "hiring_demand" in scores:
        scores["plan_vs_actual"] = 0
    if "plan_vs_actual" in scores and "budget_variance" in scores and re.search(r"\bcost|spend", low):
        scores["plan_vs_actual"] = 0
    if "forecast" in scores and "hiring_demand" in scores:
        scores["forecast"] = 0
    if "early_attrition" in scores and "attrition" in scores:
        scores["attrition"] = 0
    if "drivers" in scores and ("attrition" in scores or re.search(r"leave|exit", low)):
        scores["drivers"] += 2
    if "exit_reasons" in scores and "drivers" in scores and "reason" not in low:
        scores["exit_reasons"] = 0
    if "funnel" in scores and ("time_to_hire" in scores or "source_effectiveness" in scores):
        scores["funnel"] = 0.5
    ranked = sorted(((v, k) for k, v in scores.items() if v > 0), reverse=True)
    intent = ranked[0][1] if ranked else None
    conf = ranked[0][0] / (sum(v for v, _ in ranked) or 1) if ranked else 0.0

    start, end, label = parse_period(low, data_end, data_start)
    pq = ParsedQuestion(text=t, intent=intent, confidence=round(conf, 2), start=start, end=end, period_label=label,
                        alternatives=[k for _, k in ranked[1:3]])

    # departments: exact name, then fuzzy on distinctive tokens
    for name in departments:
        if name.lower() in low:
            pq.departments.append(name)
    if not pq.departments:
        words = re.findall(r"[a-z&]+", low)
        for name in departments:
            key = name.lower().replace("&", "").split()
            distinctive = [k for k in key if k not in {"operations", "management", "and", "department"}]
            if distinctive and all(difflib.get_close_matches(k, words, n=1, cutoff=0.85) for k in distinctive):
                pq.departments.append(name)
    for bu in business_units:
        if re.search(rf"\b{bu.lower()}\b", low) and not any(bu.lower() in d.lower() for d in pq.departments):
            pq.business_units.append(bu)
    for alias, city in LOCATION_ALIASES.items():
        if re.search(rf"\b{alias}\b", low) and city not in pq.locations:
            pq.locations.append(city)
    pq.levels = sorted({f"L{n}" for n in re.findall(r"\b(?:l|level\s*)([1-9])\b", low)})
    for dim, pat in DIM_PATTERNS.items():
        if re.search(pat, low):
            pq.breakdown = dim
            break
    if re.search(r"highest|most|top|worst|largest|biggest|greatest", low):
        pq.direction = "highest"
    elif re.search(r"lowest|least|fewest|best retention|smallest", low):
        pq.direction = "lowest"
    m = re.search(r"next\s+(\d+|[a-z\-]+)\s+(months?|quarters?)", low)
    if m and _num(m.group(1)):
        pq.horizon_months = min(6, _num(m.group(1)) * (3 if m.group(2).startswith("q") else 1))
    if HIGH_STAKES.search(t):
        pq.flags.append("high_stakes")
    if INDIVIDUAL.search(t):
        pq.flags.append("individual")
    return pq
