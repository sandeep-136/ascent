"""Document structure and readability: heading detection plus simple,
honest readability statistics (no black-box scores)."""

import re

from .chunk import split_sentences

_HEADING = re.compile(r"^(#{1,4}\s+.+|[A-Z][A-Za-z0-9 ,/&()'-]{2,60}:$|[A-Z][A-Z0-9 ,/&()'-]{3,60}$)")
_WORD = re.compile(r"[A-Za-z]+(?:'[a-z]+)?")
_SYLL = re.compile(r"[aeiouy]+", re.IGNORECASE)


def detect_headings(text: str) -> list[str]:
    headings = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and _HEADING.match(line) and len(line) <= 70:
            headings.append(line.lstrip("#").strip().rstrip(":"))
    return headings[:30]


def readability(text: str) -> dict:
    sentences = split_sentences(text)
    words = _WORD.findall(text)
    syllables = sum(max(1, len(_SYLL.findall(w))) for w in words)
    n_sent, n_words = max(len(sentences), 1), max(len(words), 1)
    # Flesch Reading Ease, reported as a number with its scale, not a verdict.
    flesch = 206.835 - 1.015 * (n_words / n_sent) - 84.6 * (syllables / n_words)
    return {
        "sentences": len(sentences),
        "words": len(words),
        "avg_sentence_words": round(n_words / n_sent, 1),
        "flesch_reading_ease": round(flesch, 1),
        "reading_time_min": round(n_words / 220, 1),
    }
