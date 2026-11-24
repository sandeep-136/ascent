"""Sentence splitting and chunking.

Documents are split into overlapping chunks of ~900 characters on sentence
boundaries. Chunk boundaries follow sentences so a retrieved passage is
always readable and citable on its own.
"""

import re

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def split_sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        out.extend(s for s in _SENT_SPLIT.split(para) if s.strip())
    return out


def chunk_text(text: str, target: int = 900, overlap_sentences: int = 1) -> list[dict]:
    """Return [{'position': i, 'text': chunk}] covering the whole document."""
    sentences = split_sentences(text)
    chunks, buf, size = [], [], 0
    for sent in sentences:
        if buf and size + len(sent) > target:
            chunks.append(" ".join(buf))
            buf = buf[-overlap_sentences:] if overlap_sentences else []
            size = sum(len(s) + 1 for s in buf)
        buf.append(sent)
        size += len(sent) + 1
    if buf:
        chunks.append(" ".join(buf))
    return [{"position": i, "text": c} for i, c in enumerate(chunks)]
