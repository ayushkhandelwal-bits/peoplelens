"""Text normalisation and tokenisation for survey comments (no external models)."""

from __future__ import annotations

import re

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

# Domain words that appear in most comments and carry no thematic signal.
DOMAIN_STOPWORDS = {"team", "work", "company", "really", "just", "lot", "time", "thanks", "thank", "quarter",
                    "year", "feel", "like", "get", "got", "make", "makes", "people", "good", "great", "bit",
                    "things", "thing", "way", "ok", "okay", "able", "also", "would", "could", "still", "even",
                    "come", "new", "rarely", "hard", "extra", "genuinely", "lot", "doing", "given", "simply"}
STOPWORDS = set(ENGLISH_STOP_WORDS) | DOMAIN_STOPWORDS
_TOKEN = re.compile(r"[a-z][a-z'\-]+")


def clean_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9'\-\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str, remove_stopwords: bool = True) -> list[str]:
    toks = _TOKEN.findall(clean_text(text))
    toks = [t.strip("'-") for t in toks]
    return [t for t in toks if len(t) > 2 and (not remove_stopwords or t not in STOPWORDS)]
