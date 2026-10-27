"""Quarterly engagement pulse survey and free-text feedback.

Scores are noisy proxies of the latent engagement used by the attrition
hazard, so survey results carry real but imperfect signal about later exits.
Disengaged employees are slightly less likely to respond (non-response bias,
documented as a limitation). Comments are stored separately from responses and
carry only department, period and tenure band (no employee identifier).
"""

from __future__ import annotations

import numpy as np

from src.data_generation.feedback_text import make_comment
from src.data_generation.generate_compensation import comp_ratio
from src.data_generation.generate_lifecycle import engagement_latent
from src.data_generation.state import WorkforceState, from_ordinal


def tenure_band(days: int) -> str:
    months = days / 30.44
    if months < 6:
        return "<6 months"
    if months < 12:
        return "6-12 months"
    if months < 36:
        return "1-3 years"
    if months < 60:
        return "3-5 years"
    return "5+ years"


def run_survey(state: WorkforceState, ms: int, me: int) -> None:
    rng, a, cfg = state.rng, state.arrays, state.cfg
    day = min(ms + 14, me)
    d = from_ordinal(day)
    period = f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    n = state.n
    # Population: employed on the survey date (includes people who leave later this month).
    employed = a["active"][:n] | (a["exit"][:n] > day)
    idx = np.flatnonzero(employed & (a["join"][:n] <= day - 30) & (a["level"][:n] <= 8))
    if len(idx) == 0:
        return
    cr = comp_ratio(state, idx, day)
    eng = engagement_latent(state, idx, day, cr)
    p_resp = np.clip(cfg.survey_response_rate * (1 + 0.12 * eng), 0.2, 0.95)
    respond = rng.random(len(idx)) < p_resp
    for k in np.flatnonzero(respond):
        i = int(idx[k])
        e = float(eng[k])
        mgr = int(a["manager"][i])
        mq = float(a["mgr_quality"][mgr]) if mgr >= 0 else 0.0
        recent_mgr = bool(a["last_mgr_change"][i] and day - a["last_mgr_change"][i] < 182)
        recent_promo = (day - a["last_promo"][i]) < 365
        stale = (day - a["last_promo"][i]) > 3 * 365
        dept = state.departments[int(a["dept"][i])]

        def clip(x):
            return float(np.clip(round(x, 2), 1.0, 5.0))

        scores = dict(
            engagement=clip(3.55 + 0.45 * e + rng.normal(0, 0.38)),
            job_satisfaction=clip(3.5 + 0.40 * e + rng.normal(0, 0.45)),
            manager_support=clip(3.6 + 0.25 * e + 0.45 * mq - 0.35 * recent_mgr + rng.normal(0, 0.45)),
            work_life_balance=clip(3.45 + 0.25 * e + dept.wlb + rng.normal(0, 0.5)),
            career_growth=clip(3.3 + 0.30 * e + 0.35 * recent_promo - 0.3 * stale
                               + 0.6 * float(np.clip(cr[k] - 1, -0.3, 0.2)) + rng.normal(0, 0.5)),
        )
        enps = int(np.clip(round(7.2 + 1.9 * e + rng.normal(0, 1.4)), 0, 10))
        state.surveys.append(dict(employee_idx=i, survey_day=day, survey_period=period,
                                  dept=int(a["dept"][i]), enps=enps, **scores))
        extremity = abs(scores["engagement"] - 3.5)
        if rng.random() < cfg.feedback_comment_rate * (0.8 + 0.5 * extremity):
            text, theme, sentiment = make_comment(rng, scores, bool(cr[k] < 0.9))
            fid = len(state.feedback) + 1
            state.feedback.append(dict(feedback_id=fid, survey_day=day, survey_period=period,
                                       dept=int(a["dept"][i]),
                                       tenure_band=tenure_band(day - int(a["join"][i])),
                                       feedback_text=text))
            state.feedback_labels.append(dict(feedback_id=fid, intended_theme=theme,
                                              intended_sentiment=sentiment))
