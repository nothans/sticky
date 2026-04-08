"""Tests for the sticky LLM classification module."""

import json

import pytest

from sticky.core.classify import Classifier, parse_classification_response


# ---------------------------------------------------------------------------
# parse_classification_response
# ---------------------------------------------------------------------------


def test_parse_valid_response():
    """Parse a valid JSON classification response."""
    raw = json.dumps(
        {
            "category": "person",
            "confidence": 0.87,
            "topics": ["career"],
            "people": ["Sarah"],
            "projects": [],
            "actions": ["follow up"],
        }
    )
    result = parse_classification_response(raw)
    assert result is not None
    assert result.category == "person"
    assert result.confidence == 0.87
    assert "Sarah" in result.people
    assert result.actions == ["follow up"]


def test_parse_invalid_response_returns_none():
    """Invalid JSON returns None."""
    result = parse_classification_response("not json at all")
    assert result is None


def test_parse_missing_fields_uses_defaults():
    """Missing list fields should default to empty lists."""
    raw = json.dumps({"category": "idea", "confidence": 0.5})
    result = parse_classification_response(raw)
    assert result is not None
    assert result.category == "idea"
    assert result.confidence == 0.5
    assert result.topics == []
    assert result.people == []
    assert result.projects == []
    assert result.actions == []


def test_parse_strips_markdown_fences():
    """Markdown code fences around JSON should be stripped."""
    raw = '```json\n{"category": "idea", "confidence": 0.7}\n```'
    result = parse_classification_response(raw)
    assert result is not None
    assert result.category == "idea"
    assert result.confidence == 0.7


def test_parse_strips_markdown_fences_no_lang():
    """Markdown code fences without language tag should be stripped."""
    raw = '```\n{"category": "action", "confidence": 0.6}\n```'
    result = parse_classification_response(raw)
    assert result is not None
    assert result.category == "action"


def test_parse_missing_required_field_returns_none():
    """Missing required field (category) returns None."""
    raw = json.dumps({"confidence": 0.5, "topics": []})
    result = parse_classification_response(raw)
    assert result is None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def test_classifier_builds_prompt():
    """_build_prompt includes content and optional template hint."""
    c = Classifier(api_key="test", model="test")
    prompt = c._build_prompt("Sarah leaving", template="person")
    assert "Sarah" in prompt
    assert "person" in prompt.lower()


def test_classifier_builds_prompt_no_template():
    """_build_prompt works without a template."""
    c = Classifier(api_key="test", model="test")
    prompt = c._build_prompt("Just a random thought")
    assert "Just a random thought" in prompt


def test_classifier_init_stores_config():
    """Classifier stores api_key and model."""
    c = Classifier(api_key="sk-test-123", model="openai/gpt-4")
    assert c.api_key == "sk-test-123"
    assert c.model == "openai/gpt-4"


def test_classifier_default_model():
    """Classifier uses anthropic/claude-sonnet-4 by default."""
    c = Classifier(api_key="test")
    assert c.model == "anthropic/claude-sonnet-4"
