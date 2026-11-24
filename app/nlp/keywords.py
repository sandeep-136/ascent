"""Keyword extraction: scored unigrams/bigrams with a stopword list and a
minimum document-frequency sanity rule. Deterministic and explainable -
every keyword can be traced to its occurrences."""

import math
import re
from collections import Counter

STOPWORDS = set("""
a an the and or but if then else when while of at by for with about into through during before after
above below to from up down in out on off over under again further once here there all any both each
few more most other some such no nor not only own same so than too very can will just should now is
am are was were be been being have has had having do does did doing would could ought i you he she it
we they them his her their its our your my me him us this that these those as s t don didn isn aren
we're it's you're they're what's there's also per etc via within across within include includes including
used use uses using new one two may might must shall will would like get gets got make makes made
""".split())

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.#-]*")


def extract_keywords(text: str, limit: int = 15) -> list[dict]:
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    unigrams = Counter(t for t in tokens if t not in STOPWORDS and len(t) > 2)
    bigrams = Counter(
        (tokens[i], tokens[i + 1])
        for i in range(len(tokens) - 1)
        if tokens[i] not in STOPWORDS and tokens[i + 1] not in STOPWORDS
        and len(tokens[i]) > 2 and len(tokens[i + 1]) > 2
    )
    total = max(len(tokens), 1)
    scored = []
    for term, count in unigrams.items():
        scored.append((term, count, math.log1p(count) * (count / total)))
    for (a, b), count in bigrams.items():
        if count >= 2:
            scored.append((f"{a} {b}", count, 1.6 * math.log1p(count) * (count / total)))
    scored.sort(key=lambda x: x[2], reverse=True)
    return [{"term": t, "count": c} for t, c, _ in scored[:limit]]
