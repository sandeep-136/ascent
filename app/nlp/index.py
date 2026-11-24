"""Hybrid retrieval over document chunks.

Dense: MiniLM embeddings, cosine similarity.
Sparse: TF-IDF over the same chunks (unigrams + bigrams).
Fusion: reciprocal rank fusion (RRF) - a standard, explainable way to combine
two ranked lists without tuning weights.

The sparse index is rebuilt in memory whenever a document is added; dense
vectors live in SQLite as float32 blobs next to their chunk, so a restart
needs no re-embedding.
"""

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

RRF_K = 60


class HybridIndex:
    def __init__(self):
        self._version = -1
        self._doc_ids: list[str] = []
        self._positions: list[int] = []
        self._texts: list[str] = []
        self._dense: np.ndarray | None = None
        self._vectorizer: TfidfVectorizer | None = None
        self._sparse = None

    def rebuild(self, rows: list[dict]):
        """rows: [{'doc_id', 'position', 'text', 'embedding' (bytes)}]"""
        self._doc_ids = [r["doc_id"] for r in rows]
        self._positions = [r["position"] for r in rows]
        self._texts = [r["text"] for r in rows]
        if rows:
            self._dense = np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32).reshape(len(rows), -1)
            self._vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
            self._sparse = self._vectorizer.fit_transform(self._texts)
        else:
            self._dense, self._vectorizer, self._sparse = None, None, None

    @property
    def size(self) -> int:
        return len(self._texts)

    def search(self, query: str, query_embedding: np.ndarray, top_k: int = 6,
               doc_id: str | None = None) -> list[dict]:
        if not self._texts:
            return []
        dense_scores = (self._dense @ query_embedding).ravel()
        sparse_scores = (self._sparse @ self._vectorizer.transform([query]).T).toarray().ravel()

        def ranks(scores):
            order = np.argsort(-scores)
            r = np.empty(len(scores))
            r[order] = np.arange(1, len(scores) + 1)
            return r

        fused = 1 / (RRF_K + ranks(dense_scores)) + 1 / (RRF_K + ranks(sparse_scores))
        order = np.argsort(-fused)
        out = []
        for i in order:
            if doc_id is not None and self._doc_ids[i] != doc_id:
                continue
            out.append({
                "doc_id": self._doc_ids[i],
                "position": self._positions[i],
                "text": self._texts[i],
                "dense_score": round(float(dense_scores[i]), 4),
                "sparse_score": round(float(sparse_scores[i]), 4),
            })
            if len(out) >= top_k:
                break
        return out
