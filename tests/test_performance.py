"""Warm local-operation smoke timings; cold startup is measured separately."""

import time

import httpx
import pytest

from sticky.core.service import StickyService


@pytest.fixture
def service(tmp_data_dir, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Capture and search must remain local")
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    svc = StickyService(data_dir=tmp_data_dir)
    svc.initialize()
    yield svc
    svc.db.close()


def test_warm_capture_under_3s(service):
    service.capture("Warm-up note to load the local embedding model")
    start = time.monotonic()
    receipt = service.capture("Measuring local capture latency")
    elapsed = time.monotonic() - start
    assert receipt["id"]
    assert elapsed < 3.0, f"Warm capture took {elapsed:.3f}s (target: 2s; smoke ceiling: 3s)"


def test_warm_search_under_1s_over_100_records(service):
    for index in range(100):
        service.capture(f"Software engineering thought {index}: architecture and distributed systems principle {index}")
    assert service.stats()["thoughts"]["total"] == 100
    start = time.monotonic()
    results = service.search("architecture distributed systems", limit=10)
    elapsed = time.monotonic() - start
    assert len(results) == 10
    assert elapsed < 1.0, f"Warm hybrid search took {elapsed:.3f}s (target: 500ms; smoke ceiling: 1s)"
