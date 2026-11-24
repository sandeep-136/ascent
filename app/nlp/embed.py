"""Dense embeddings: all-MiniLM-L6-v2 (384-dim) running as a quantized ONNX
model. No torch dependency, fast on CPU, small enough for a free-tier deploy.
The model files live in model/ and are baked into the Docker image."""

import os
import threading

import numpy as np

_lock = threading.Lock()
_session = None
_tokenizer = None

MODEL_DIR = os.environ.get("EMBEDDING_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "model"))


def _load():
    global _session, _tokenizer
    if _session is None:
        with _lock:
            if _session is None:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                _tokenizer = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
                _tokenizer.enable_truncation(max_length=256)
                _tokenizer.enable_padding()
                _session = ort.InferenceSession(
                    os.path.join(MODEL_DIR, "model.onnx"), providers=["CPUExecutionProvider"]
                )
    return _session, _tokenizer


def embed_texts(texts: list[str]) -> np.ndarray:
    """L2-normalized 384-dim embeddings, one per text (cosine = dot)."""
    session, tokenizer = _load()
    enc = tokenizer.encode_batch(texts)
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    types = np.array([e.type_ids for e in enc], dtype=np.int64)
    (last_hidden,) = session.run(
        None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
    )
    m = mask[..., None].astype(np.float32)
    pooled = (last_hidden * m).sum(axis=1) / m.sum(axis=1).clip(min=1e-9)
    return pooled / np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
