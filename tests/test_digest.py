"""Explicit cloud summary behavior without background enrichment."""

from unittest.mock import Mock

import httpx
import pytest

from sticky.core.digest import DigestGenerator
from sticky.core.models import Thought


def test_missing_key_makes_zero_requests(monkeypatch):
    post = Mock(side_effect=AssertionError("No key must mean no request"))
    monkeypatch.setattr(httpx.Client, "post", post)
    with pytest.raises(ValueError, match="API key"):
        DigestGenerator("", "test-model").generate_sync([Thought(content="private note")])
    post.assert_not_called()


def test_explicit_digest_sends_only_selected_notes(monkeypatch):
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": "Meeting moved to Friday [1]."}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(httpx.Client, "post", post)
    result = DigestGenerator("fake-key", "test-model").generate_sync([Thought(content="Meeting moved to Friday")])
    assert result == {"text": "Meeting moved to Friday [1]."}
    body = post.call_args.kwargs["json"]
    assert body["messages"][1]["content"] == "[1] Meeting moved to Friday"
    assert body["model"] == "test-model"
    assert "resurfaced" not in body and "action_items" not in result


@pytest.mark.parametrize("data", [{}, {"choices": []}, {"choices": [{"message": {"content": ""}}]}, {"choices": [{"message": {"content": None}}]}])
def test_invalid_cloud_response_fails_clearly(monkeypatch, data):
    response = Mock()
    response.json.return_value = data
    monkeypatch.setattr(httpx.Client, "post", Mock(return_value=response))
    with pytest.raises(ValueError, match="unavailable"):
        DigestGenerator("fake-key", "test-model").generate_sync([Thought(content="note")])


def test_cloud_failure_does_not_fake_a_summary(monkeypatch):
    monkeypatch.setattr(httpx.Client, "post", Mock(side_effect=httpx.ConnectError("offline")))
    with pytest.raises(ValueError, match="notes remain saved"):
        DigestGenerator("fake-key", "test-model").generate_sync([Thought(content="note")])


def test_empty_period_does_not_call_cloud(monkeypatch):
    post = Mock(side_effect=AssertionError("Empty period must not call cloud"))
    monkeypatch.setattr(httpx.Client, "post", post)
    assert DigestGenerator("fake-key", "test-model").generate_sync([])["text"] == "No thoughts in this period."
    post.assert_not_called()
