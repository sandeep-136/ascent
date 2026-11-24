"""Extractive summarization: sentences scored by normalized term frequency
against the document, with a lead-position prior. Returns real sentences from
the document in original order - this is the always-available baseline the
LLM summaries are compared against."""

import math
import re
from collections import Counter

from .chunk import split_sentences
from .keywords import STOPWORDS

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.#-]*")


def extractive_summary(text: str, sentences: int = 5) -> list[str]:
    sents = split_sentences(text)
    if len(sents) <= sentences:
        return sents
    freq = Counter(
        t.lower() for s in sents for t in set(_TOKEN.findall(s))
        if t.lower() not in STOPWORDS and len(t) > 2
    )
    if not freq:
        return sents[:sentences]
    max_f = max(freq.values())
    scored = []
    for i, s in enumerate(sents):
        terms = [t.lower() for t in _TOKEN.findall(s) if t.lower() in freq]
        if not terms:
            continue
        score = sum(freq[t] / max_f for t in terms) / math.sqrt(len(terms))
        score *= 1.0 + 0.15 * math.exp(-i / 10.0)  # mild lead bias
        scored.append((i, s, score))
    top = sorted(scored, key=lambda x: x[2], reverse=True)[:sentences]
    return [s for _, s, _ in sorted(top)]
