"""Grounded Q&A: retrieve, build a cited-passage prompt, call the LLM,
parse and validate citations. Citations that point outside the retrieved
passage set are stripped rather than shown - a wrong citation is worse than
none."""

import re

from ..llm.client import complete
from .prompts import GROUNDED_QA_SYSTEM, GROUNDED_QA_USER, format_passages

_CITE = re.compile(r"\[(\d{1,2})\]")


def build_grounded_prompt(question: str, chunks: list[dict]) -> tuple[str, str]:
    return GROUNDED_QA_SYSTEM, GROUNDED_QA_USER.format(
        passages=format_passages(chunks), question=question
    )


def parse_citations(answer: str, n_passages: int) -> tuple[str, list[int], list[str]]:
    """Return (clean_answer, valid_citation_numbers, uncited_sentences).

    Valid citations are 1-based passage numbers within range. Uncited
    sentences are reported so the UI can mark them honestly.
    """
    def _filter(match):
        n = int(match.group(1))
        return match.group(0) if 1 <= n <= n_passages else ""

    clean = _CITE.sub(_filter, answer)
    cited = sorted({int(m.group(1)) for m in _CITE.finditer(clean)})
    uncited = []
    for sent in re.split(r"(?<=[.!?])\s+", clean):
        s = sent.strip()
        if len(s) >= 40 and not _CITE.search(s) and not s.startswith("The document does not say"):
            uncited.append(s)
    return clean, cited, uncited


def answer_question(question: str, chunks: list[dict], **llm_kwargs) -> dict:
    system, user = build_grounded_prompt(question, chunks)
    raw = complete(system, user, **llm_kwargs)
    clean, cited, uncited = parse_citations(raw, len(chunks))
    sources = [
        {"number": n, "doc_id": chunks[n - 1]["doc_id"], "position": chunks[n - 1]["position"],
         "text": chunks[n - 1]["text"]}
        for n in cited
    ]
    return {"answer": clean, "sources": sources, "uncited_sentences": uncited}
