"""Embedding engine using sentence-transformers."""

from __future__ import annotations

import struct
from functools import lru_cache

import numpy as np


class EmbeddingEngine:
    """Manages sentence-transformer model for embedding generation."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def dimensions(self) -> int:
        return 384

    def embed(self, text: str) -> bytes:
        """Generate embedding as bytes (float32 array)."""
        vec = self.model.encode(text, normalize_embeddings=True)
        return struct.pack(f"{len(vec)}f", *vec)

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        """Generate embeddings for multiple texts."""
        vecs = self.model.encode(texts, normalize_embeddings=True)
        return [struct.pack(f"{len(v)}f", *v) for v in vecs]

    @staticmethod
    def bytes_to_floats(data: bytes) -> list[float]:
        """Convert embedding bytes back to float list."""
        n = len(data) // 4
        return list(struct.unpack(f"{n}f", data))

    @staticmethod
    def cosine_similarity(a: bytes, b: bytes) -> float:
        """Compute cosine similarity between two embedding byte arrays."""
        n_a = len(a) // 4
        n_b = len(b) // 4
        va = np.array(struct.unpack(f"{n_a}f", a), dtype=np.float32)
        vb = np.array(struct.unpack(f"{n_b}f", b), dtype=np.float32)
        dot = np.dot(va, vb)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        if norm == 0:
            return 0.0
        return float(dot / norm)


_engine: EmbeddingEngine | None = None


def get_embedding_engine(model_name: str = "all-MiniLM-L6-v2") -> EmbeddingEngine:
    """Get or create the global embedding engine singleton."""
    global _engine
    if _engine is None or _engine.model_name != model_name:
        _engine = EmbeddingEngine(model_name)
    return _engine
