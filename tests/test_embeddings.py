"""Tests for embedding engine."""

import struct

from sticky.core.embeddings import EmbeddingEngine


def test_embed_produces_correct_dimensions():
    engine = EmbeddingEngine()
    vec = engine.embed("hello world")
    assert len(vec) == 384 * 4  # 384 float32s = 1536 bytes


def test_embed_different_texts_differ():
    engine = EmbeddingEngine()
    v1 = engine.embed("machine learning is great")
    v2 = engine.embed("I love cooking pasta")
    assert v1 != v2


def test_cosine_similarity_same_text():
    engine = EmbeddingEngine()
    v1 = engine.embed("hello world")
    sim = engine.cosine_similarity(v1, v1)
    assert abs(sim - 1.0) < 0.01


def test_cosine_similarity_similar_texts():
    engine = EmbeddingEngine()
    v1 = engine.embed("career changes and job transitions")
    v2 = engine.embed("Sarah is thinking about leaving her position")
    sim = engine.cosine_similarity(v1, v2)
    assert sim > 0.15  # Should be somewhat similar


def test_bytes_to_floats_roundtrip():
    engine = EmbeddingEngine()
    vec_bytes = engine.embed("test")
    floats = engine.bytes_to_floats(vec_bytes)
    assert len(floats) == 384
    assert all(isinstance(f, float) for f in floats)


def test_embed_batch():
    engine = EmbeddingEngine()
    results = engine.embed_batch(["hello", "world"])
    assert len(results) == 2
    assert len(results[0]) == 384 * 4
    assert results[0] != results[1]


def test_singleton():
    from sticky.core.embeddings import get_embedding_engine
    e1 = get_embedding_engine()
    e2 = get_embedding_engine()
    assert e1 is e2
