"""Interpretable theme detection, keywords and TF-IDF term profiles.

Themes are assigned with a transparent keyword lexicon (multi-label: one
comment can touch several themes). TF-IDF is used descriptively, to surface
the terms that distinguish one group of comments from the rest.
"""

from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from config.settings import get_settings
from src.nlp.text_cleaning import STOPWORDS, clean_text, tokenize

THEME_LEXICON = {
    "Career growth": ["career", "promotion", "promoted", "growth", "grow", "next level", "path", "progression",
                      "opportunit", "same role", "future", "internal openings", "internal moves", "responsibilit"],
    "Manager support": ["manager", "lead", "micromanag", "one-on-one", "feedback", "supportive", "backs the team",
                        "trust"],
    "Workload": ["workload", "firefight", "burnout", "stretched", "too much", "two people", "targets",
                 "pressure", "open positions", "covering", "deadlines", "staffed", "sustainable"],
    "Compensation": ["pay", "salary", "increment", "compensation", "bonus", "market", "benefits", "inflation",
                     "paid", "salary bands"],
    "Recognition": ["recognis", "recogniz", "noticed", "valued", "acknowledg", "shout-out", "invisible",
                    "pizza", "appreciat"],
    "Work-life balance": ["work-life", "balance", "weekend", "flexible", "hybrid", "late-night", "after hours",
                          "switch off", "shift", "leave requests", "family", "commute"],
    "Learning & development": ["training", "course", "learning", "programme", "learnt", "budget",
                               "development"],
    "Team collaboration": ["colleague", "collaborat", "silo", "handoff", "blame", "communication", "team",
                           "help each other", "restructure"],
}
_PATTERNS = {k: re.compile("|".join(re.escape(w) for w in v)) for k, v in THEME_LEXICON.items()}


def detect_themes(text: str) -> list[str]:
    t = clean_text(text)
    themes = [k for k, p in _PATTERNS.items() if p.search(t)]
    # "team" alone is too generic: keep Team collaboration only with a stronger cue
    if "Team collaboration" in themes and not re.search(r"colleague|collaborat|silo|handoff|blame|communication|"
                                                        r"help each other|restructure|great team|love my team", t):
        themes.remove("Team collaboration")
    return themes or ["General / other"]


def theme_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Explode comments into (comment, theme) rows."""
    out = df.copy()
    out["themes"] = out.feedback_text.map(detect_themes)
    return out.explode("themes").rename(columns={"themes": "theme"})


def keyword_frequency(texts: pd.Series, top: int = 25) -> pd.DataFrame:
    c = Counter()
    for t in texts:
        toks = tokenize(t)
        c.update(toks)
        c.update(" ".join(p) for p in zip(toks, toks[1:]))
    return pd.DataFrame(c.most_common(top), columns=["term", "count"])


def distinctive_terms(df: pd.DataFrame, group: str, top: int = 6, min_docs: int | None = None) -> pd.DataFrame:
    """Top TF-IDF terms per group (groups below the privacy threshold are skipped)."""
    min_docs = min_docs or get_settings().min_group_size
    docs = df.groupby(group).feedback_text.apply(lambda s: " ".join(s))
    counts = df.groupby(group).size()
    docs = docs[counts.reindex(docs.index) >= min_docs]
    if len(docs) < 2:
        return pd.DataFrame(columns=[group, "terms"])
    vec = TfidfVectorizer(stop_words=list(STOPWORDS), ngram_range=(1, 2), min_df=1, sublinear_tf=True,
                          preprocessor=clean_text)
    X = vec.fit_transform(docs.values)
    terms = np.array(vec.get_feature_names_out())
    rows = []
    for k, key in enumerate(docs.index):
        row = X[k].toarray().ravel()
        idx = row.argsort()[::-1][:top]
        rows.append({group: key, "terms": ", ".join(terms[idx])})
    return pd.DataFrame(rows)


def evaluate_themes(df: pd.DataFrame, labels: pd.DataFrame) -> dict:
    """Recall of the intended theme(s) and precision of assigned themes vs synthetic labels."""
    m = df.merge(labels, on="feedback_id")
    m = m[~m.intended_theme.isin(["General"])]
    if m.empty:
        return {}
    pred = m.feedback_text.map(detect_themes)
    true = m.intended_theme.str.split(r" \| ")
    hit = [len(set(p) & set(t)) > 0 for p, t in zip(pred, true)]
    prec = [len(set(p) & set(t)) / len(p) if p and p != ["General / other"] else 0 for p, t in zip(pred, true)]
    return dict(n=int(len(m)), recall_any=float(np.mean(hit)), precision=float(np.mean(prec)))
