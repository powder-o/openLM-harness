"""Local sentence embeddings (L2-normalized float32). STUDY_EMBED_FAKE=1 uses a hashing embedder."""
from __future__ import annotations

import hashlib
import re
import threading

import numpy as np

from .. import config

FAKE_DIM = 384
_model = None
_model_name: str | None = None
_lock = threading.Lock()


def model_name() -> str:
    return "fake-hash-384" if config.fake_embed() else config.get_settings()["embed_model"]


def _fake_vec(text: str) -> np.ndarray:
    v = np.zeros(FAKE_DIM, dtype=np.float32)
    words = re.findall(r"\w+", text.lower())
    for tok in words + [f"{a}_{b}" for a, b in zip(words, words[1:])]:
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % FAKE_DIM] += 1.0
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _get_model():
    global _model, _model_name
    name = config.get_settings()["embed_model"]
    with _lock:
        if _model is None or _model_name != name:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(name)
            _model_name = name
    return _model


def dim() -> int:
    if config.fake_embed():
        return FAKE_DIM
    return int(_get_model().get_sentence_embedding_dimension())


def embed_texts(texts: list[str], batch_size: int = 32) -> np.ndarray:
    if not texts:
        return np.zeros((0, dim()), dtype=np.float32)
    if config.fake_embed():
        return np.stack([_fake_vec(t) for t in texts])
    arr = _get_model().encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True
    )
    return np.asarray(arr, dtype=np.float32)


def embed_query(text: str) -> np.ndarray:
    name = model_name().lower()
    if "bge" in name and "-en" in name:
        text = "Represent this sentence for searching relevant passages: " + text
    return embed_texts([text])[0]


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def from_blob(blob: bytes | None) -> np.ndarray | None:
    return None if blob is None else np.frombuffer(blob, dtype="<f4")


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))  # inputs are normalized
