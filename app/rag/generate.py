"""LLM generation features (summaries, flashcards, quiz) with robust output
parsing and honest fallbacks: without an LLM key, summaries fall back to the
extractive engine and the other panels report themselves unconfigured."""

import json
import re

from ..llm.client import complete
from ..nlp.summarize import extractive_summary
from .prompts import (
    FLASHCARD_SYSTEM,
    QUIZ_SYSTEM,
    SUMMARY_STYLES,
    SUMMARY_SYSTEM,
    format_passages,
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(raw: str) -> dict | None:
    m = _JSON_BLOCK.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def summarize(chunks: list[dict], style: str, document_text: str, **llm_kwargs) -> dict:
    style = style if style in SUMMARY_STYLES else "brief"
    try:
        raw = complete(SUMMARY_SYSTEM.format(style=SUMMARY_STYLES[style]),
                       format_passages(chunks), **llm_kwargs)
        return {"style": style, "engine": "llm_grounded", "text": raw}
    except Exception:
        sentences = extractive_summary(document_text, sentences={"brief": 3, "detailed": 8, "bullets": 6}[style])
        return {"style": style, "engine": "extractive_fallback",
                "text": "\n".join(f"- {s}" for s in sentences) if style == "bullets" else " ".join(sentences),
                "note": "LLM not configured; this is the extractive baseline (real sentences from the document)."}


def flashcards(chunks: list[dict], n: int = 8, **llm_kwargs) -> dict:
    raw = complete(FLASHCARD_SYSTEM.format(n=n), format_passages(chunks), **llm_kwargs)
    data = _parse_json(raw)
    cards = []
    for c in (data or {}).get("cards", []):
        try:
            src = int(c.get("source", 0))
            if c.get("front") and c.get("back") and 1 <= src <= len(chunks):
                cards.append({"front": str(c["front"]), "back": str(c["back"]),
                              "source": {"number": src, "position": chunks[src - 1]["position"],
                                         "text": chunks[src - 1]["text"]}})
        except (TypeError, ValueError):
            continue
    return {"cards": cards, "requested": n, "produced": len(cards)}


def quiz(chunks: list[dict], n: int = 5, **llm_kwargs) -> dict:
    raw = complete(QUIZ_SYSTEM.format(n=n), format_passages(chunks), **llm_kwargs)
    data = _parse_json(raw)
    questions = []
    for q in (data or {}).get("questions", []):
        try:
            opts = [str(o) for o in q["options"]]
            ai = int(q["answer_index"])
            src = int(q.get("source", 0))
            if q.get("question") and len(opts) == 4 and 0 <= ai < 4 and 1 <= src <= len(chunks):
                questions.append({"question": str(q["question"]), "options": opts,
                                  "answer_index": ai,
                                  "source": {"number": src, "position": chunks[src - 1]["position"],
                                             "text": chunks[src - 1]["text"]}})
        except (KeyError, TypeError, ValueError):
            continue
    return {"questions": questions, "requested": n, "produced": len(questions)}
