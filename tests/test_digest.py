"""Tests for the sticky digest generation module."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from sticky.core.digest import DigestGenerator, build_digest_prompt, parse_digest_response
from sticky.core.models import Thought


# ---------------------------------------------------------------------------
# build_digest_prompt
# ---------------------------------------------------------------------------


def test_build_digest_prompt():
    """Prompt includes numbered thoughts."""
    thoughts = [Thought.create("Sarah leaving"), Thought.create("Auth delayed")]
    prompt = build_digest_prompt(thoughts, resurfaced=None)
    assert "Sarah" in prompt
    assert "[0]" in prompt  # numbered


def test_build_digest_prompt_with_resurface():
    """Prompt includes resurfaced thought when provided."""
    thoughts = [Thought.create("today")]
    old = Thought.create("old thought")
    prompt = build_digest_prompt(thoughts, resurfaced=old)
    assert "old thought" in prompt


def test_build_digest_prompt_multiple_thoughts():
    """All thoughts are numbered sequentially."""
    thoughts = [
        Thought.create("first"),
        Thought.create("second"),
        Thought.create("third"),
    ]
    prompt = build_digest_prompt(thoughts)
    assert "[0]" in prompt
    assert "[1]" in prompt
    assert "[2]" in prompt
    assert "first" in prompt
    assert "second" in prompt
    assert "third" in prompt


# ---------------------------------------------------------------------------
# DigestGenerator — generate_offline
# ---------------------------------------------------------------------------


def test_digest_generator_no_thoughts():
    """Empty thoughts list produces a 'No thoughts' message."""
    gen = DigestGenerator(api_key="test-key", model="test")
    result = gen.generate_offline([])
    assert "No thoughts" in result["text"]


def test_generate_offline_groups_by_category():
    """Offline digest groups thoughts by their category."""
    thoughts = [
        Thought.create("idea 1", category="idea"),
        Thought.create("idea 2", category="idea"),
        Thought.create("meeting 1", category="meeting"),
    ]
    gen = DigestGenerator(api_key="", model="test")
    result = gen.generate_offline(thoughts)
    assert "IDEA" in result["text"]
    assert "MEETING" in result["text"]


def test_generate_offline_uncategorized():
    """Thoughts without a category are grouped under UNCATEGORIZED."""
    thoughts = [
        Thought.create("random note"),
    ]
    gen = DigestGenerator(api_key="", model="test")
    result = gen.generate_offline(thoughts)
    assert "UNCATEGORIZED" in result["text"]


def test_generate_offline_source_map():
    """Offline digest returns source_map mapping category labels to thought IDs."""
    thoughts = [
        Thought.create("idea 1", category="idea"),
        Thought.create("meeting 1", category="meeting"),
    ]
    gen = DigestGenerator(api_key="", model="test")
    result = gen.generate_offline(thoughts)
    assert "source_map" in result
    assert "Idea" in result["source_map"]
    assert "Meeting" in result["source_map"]
    assert thoughts[0].id in result["source_map"]["Idea"]
    assert thoughts[1].id in result["source_map"]["Meeting"]


def test_generate_offline_returns_empty_action_items_and_people():
    """Offline digest returns empty action_items and people lists."""
    thoughts = [Thought.create("test")]
    gen = DigestGenerator(api_key="", model="test")
    result = gen.generate_offline(thoughts)
    assert result["action_items"] == []
    assert result["people"] == []


# ---------------------------------------------------------------------------
# parse_digest_response
# ---------------------------------------------------------------------------


def test_parse_digest_response_valid():
    """Valid LLM JSON response is parsed into formatted text and source map."""
    thoughts = [
        Thought.create("Auth migration delayed"),
        Thought.create("Sarah exploring platform team"),
        Thought.create("Auth needs mobile team input"),
    ]
    raw = json.dumps({
        "topics": [
            {
                "label": "Auth Migration",
                "summary": "Decided to delay by 2 weeks",
                "thought_indices": [0, 2],
            },
            {
                "label": "Career",
                "summary": "Sarah exploring move to platform team",
                "thought_indices": [1],
            },
        ],
        "action_items": [
            {"content": "Follow up with Sarah", "person": "Sarah"}
        ],
        "people": [
            {"name": "Sarah", "context": "career discussion, auth migration"}
        ],
    })
    result = parse_digest_response(raw, thoughts)
    assert result is not None
    assert "Auth Migration" in result["text"]
    assert "Career" in result["text"]
    assert "Follow up with Sarah" in result["text"]
    assert "Sarah" in result["text"]
    # Source map
    assert "Auth Migration" in result["source_map"]
    assert thoughts[0].id in result["source_map"]["Auth Migration"]
    assert thoughts[2].id in result["source_map"]["Auth Migration"]
    assert "Career" in result["source_map"]
    assert thoughts[1].id in result["source_map"]["Career"]
    # Action items and people
    assert len(result["action_items"]) == 1
    assert result["action_items"][0]["content"] == "Follow up with Sarah"
    assert len(result["people"]) == 1
    assert result["people"][0]["name"] == "Sarah"


def test_parse_digest_response_invalid_json():
    """Invalid JSON returns None."""
    result = parse_digest_response("not json at all", [])
    assert result is None


def test_parse_digest_response_strips_markdown_fences():
    """Markdown code fences are stripped before parsing."""
    thoughts = [Thought.create("test thought")]
    raw = '```json\n' + json.dumps({
        "topics": [{"label": "Test", "summary": "A test", "thought_indices": [0]}],
        "action_items": [],
        "people": [],
    }) + '\n```'
    result = parse_digest_response(raw, thoughts)
    assert result is not None
    assert "Test" in result["text"]


def test_parse_digest_response_invalid_indices():
    """Out-of-range thought_indices are silently skipped."""
    thoughts = [Thought.create("only one")]
    raw = json.dumps({
        "topics": [{"label": "Topic", "summary": "Test", "thought_indices": [0, 5, 99]}],
        "action_items": [],
        "people": [],
    })
    result = parse_digest_response(raw, thoughts)
    assert result is not None
    assert "Topic" in result["text"]
    # Source map should only contain the valid index
    assert thoughts[0].id in result["source_map"]["Topic"]
    assert len(result["source_map"]["Topic"]) == 1


# ---------------------------------------------------------------------------
# find_resurface_candidate
# ---------------------------------------------------------------------------


def test_find_resurface_no_old_thoughts(tmp_path):
    """When there are no old thoughts, returns None."""
    from sticky.core.db import Database

    db = Database(tmp_path / "test.db")
    db.initialize()

    # Only recent thoughts exist
    recent = Thought.create("recent thought")
    db.insert_thought(recent)

    gen = DigestGenerator(api_key="", model="test")
    result = gen.find_resurface_candidate(db, None, [recent], min_age_days=7)
    assert result is None

    db.close()


# ---------------------------------------------------------------------------
# DigestGenerator init
# ---------------------------------------------------------------------------


def test_digest_generator_init():
    """DigestGenerator stores api_key and model."""
    gen = DigestGenerator(api_key="sk-test-123", model="openai/gpt-4")
    assert gen.api_key == "sk-test-123"
    assert gen.model == "openai/gpt-4"


def test_digest_generator_default_model():
    """DigestGenerator uses anthropic/claude-sonnet-4 by default."""
    gen = DigestGenerator(api_key="test")
    assert gen.model == "anthropic/claude-sonnet-4"
