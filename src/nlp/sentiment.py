"""Lexicon-based sentiment with VADER (runs fully offline).

VADER scores short informal text well, but it cannot reliably detect sarcasm,
hedging or comments that praise one thing and criticise another. Scores are
therefore treated as directional signals for *groups* of comments, never as an
objective measure of what an individual employee feels.
"""

from __future__ import annotations

from functools import lru_cache

import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

POS, NEG = 0.05, -0.05


# Small, documented workplace lexicon added to VADER's general-purpose one. VADER
# scores everyday words; without these, workplace complaints such as "burnout" or
# "firefighting" read as neutral. Valences follow VADER's -4..+4 scale.
WORKPLACE_LEXICON = {
    "burnout": -2.6, "firefighting": -1.8, "micromanagement": -2.2, "overworked": -2.2, "understaffed": -1.8,
    "stretched": -1.2, "silos": -1.3, "invisible": -1.4, "sustainable": 1.2, "unsustainable": -1.8,
    "exhausting": -2.0, "unsettling": -1.6, "supportive": 1.9, "flexible": 1.2, "transparent": 1.3,
    "unclear": -1.0, "irrelevant": -1.2, "generic": -0.8, "competitive": 0.9,
}


@lru_cache(maxsize=1)
def _analyzer() -> SentimentIntensityAnalyzer:
    a = SentimentIntensityAnalyzer()
    a.lexicon.update(WORKPLACE_LEXICON)
    return a


def score(text: str) -> float:
    return float(_analyzer().polarity_scores(text or "")["compound"])


def label(compound: float) -> str:
    return "positive" if compound >= POS else "negative" if compound <= NEG else "neutral"


def score_frame(df: pd.DataFrame, text_col: str = "feedback_text") -> pd.DataFrame:
    df = df.copy()
    df["compound"] = df[text_col].map(score)
    df["sentiment"] = df.compound.map(label)
    return df


def evaluate_against_labels(scored: pd.DataFrame, labels: pd.DataFrame) -> dict:
    """Agreement with the generator's intended sentiment (synthetic ground truth).

    Mixed comments are excluded from accuracy (no single correct label) and
    reported separately.
    """
    m = scored.merge(labels, on="feedback_id", how="inner")
    if m.empty:
        return {}
    single = m[m.intended_sentiment.isin(["positive", "negative", "neutral"])]
    acc = float((single.sentiment == single.intended_sentiment).mean())
    cm = pd.crosstab(single.intended_sentiment, single.sentiment)
    sarcasm = m[m.feedback_text.str.contains(r"^(?:Great|Fantastic|Wonderful|Sure|Amazing),", regex=True)]
    return dict(n=int(len(single)), accuracy=acc, confusion=cm,
                mixed_comments=int((m.intended_sentiment == "mixed").sum()),
                mixed_scored_positive=float((m[m.intended_sentiment == "mixed"].sentiment == "positive").mean())
                if (m.intended_sentiment == "mixed").any() else None,
                sarcasm_n=int(len(sarcasm)),
                sarcasm_misread=float((sarcasm.sentiment != "negative").mean()) if len(sarcasm) else None)


def ambiguous_examples(scored: pd.DataFrame, labels: pd.DataFrame | None, n: int = 6) -> pd.DataFrame:
    """Examples where the lexicon score is most likely misleading (text only)."""
    if labels is not None and len(labels):
        m = scored.merge(labels, on="feedback_id")
        wrong = m[(m.intended_sentiment.isin(["positive", "negative"])) & (m.sentiment != m.intended_sentiment)]
        mixed = m[m.intended_sentiment == "mixed"]
        ex = pd.concat([wrong.drop_duplicates("feedback_text").head(n - 2),
                        mixed.drop_duplicates("feedback_text").head(2)])
        return ex[["feedback_text", "compound", "sentiment", "intended_sentiment"]]
    near = scored[scored.compound.abs() < 0.2].drop_duplicates("feedback_text").head(n)
    return near[["feedback_text", "compound", "sentiment"]]
