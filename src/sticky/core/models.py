"""Pydantic data models for thoughts, entities, digests, and actions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from ulid import ULID


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_ulid() -> str:
    """Generate a new ULID string."""
    return str(ULID())


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------


class ThoughtCreate(BaseModel):
    """Input model for creating a thought."""

    content: str
    template: str | None = None
    source: str = "cli"


# ---------------------------------------------------------------------------
# Core Models
# ---------------------------------------------------------------------------


class Thought(BaseModel):
    """A captured thought."""

    id: str = Field(default_factory=_new_ulid)
    content: str
    embedding: bytes | None = None
    embedding_model: str | None = None
    source_url: str | None = None
    category: str | None = None
    confidence: float | None = None
    needs_review: bool = False
    source: str = "cli"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    @classmethod
    def create(cls, content: str, **kwargs: Any) -> Thought:
        """Create a new Thought with the given content."""
        return cls(content=content, **kwargs)

    @property
    def metadata_json(self) -> str:
        """Return metadata as a JSON string."""
        return json.dumps(self.metadata)

    @classmethod
    def from_row(cls, row: dict) -> Thought:
        """Create a Thought from a SQLite row dict.

        Parses metadata from JSON string and converts needs_review int to bool.
        """
        data = dict(row)
        # SQLite stores booleans as integers
        if "needs_review" in data:
            data["needs_review"] = bool(data["needs_review"])
        # Parse metadata from JSON string
        if isinstance(data.get("metadata"), str):
            data["metadata"] = json.loads(data["metadata"])
        return cls(**data)

    def to_display(self, score: float | None = None) -> dict:
        """Return a display-friendly dict, excluding embedding.

        Optionally includes a similarity score.
        """
        d = self.model_dump(exclude={"embedding"})
        d["source_url"] = self.source_url
        if score is not None:
            d["score"] = score
        return d


class Entity(BaseModel):
    """A tracked person, project, or concept."""

    id: str = Field(default_factory=_new_ulid)
    name: str
    entity_type: str  # person | project | concept
    aliases: list[str] = Field(default_factory=list)
    first_seen: str = Field(default_factory=_now_iso)
    last_seen: str = Field(default_factory=_now_iso)
    mention_count: int = 1

    @classmethod
    def create(cls, name: str, entity_type: str, **kwargs: Any) -> Entity:
        """Create a new Entity."""
        return cls(name=name, entity_type=entity_type, **kwargs)

    @property
    def aliases_json(self) -> str:
        """Return aliases as a JSON string."""
        return json.dumps(self.aliases)

    @classmethod
    def from_row(cls, row: dict) -> Entity:
        """Create an Entity from a SQLite row dict.

        Parses aliases from JSON string.
        """
        data = dict(row)
        if isinstance(data.get("aliases"), str):
            data["aliases"] = json.loads(data["aliases"])
        return cls(**data)


class EntityMention(BaseModel):
    """Links an entity to a thought."""

    entity_id: str
    thought_id: str
    context: str | None = None
    created_at: str = Field(default_factory=_now_iso)


class Digest(BaseModel):
    """A generated digest summarizing thoughts over a period."""

    id: str = Field(default_factory=_new_ulid)
    content: str
    thought_ids: list[str] = Field(default_factory=list)
    period_start: str
    period_end: str
    created_at: str = Field(default_factory=_now_iso)

    @property
    def thought_ids_json(self) -> str:
        """Return thought_ids as a JSON string."""
        return json.dumps(self.thought_ids)

    @classmethod
    def from_row(cls, row: dict) -> Digest:
        """Create a Digest from a SQLite row dict.

        Parses thought_ids from JSON string.
        """
        data = dict(row)
        if isinstance(data.get("thought_ids"), str):
            data["thought_ids"] = json.loads(data["thought_ids"])
        return cls(**data)


class ActionItem(BaseModel):
    """A tracked action item with carryforward support."""

    id: str = Field(default_factory=_new_ulid)
    content: str
    person: str | None = None
    source_thought_id: str | None = None
    completed: bool = False
    created_at: str = Field(default_factory=_now_iso)
    completed_at: str | None = None
    expires_at: str | None = None

    def model_post_init(self, __context: Any) -> None:
        """Set expires_at to 14 days after created_at if not provided."""
        if self.expires_at is None:
            created = datetime.fromisoformat(self.created_at)
            self.expires_at = (created + timedelta(days=14)).isoformat()


# ---------------------------------------------------------------------------
# Result Models
# ---------------------------------------------------------------------------


class ClassificationResult(BaseModel):
    """LLM classification output for a thought."""

    category: str
    confidence: float
    topics: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    source_url: str | None = None


class SearchResult(BaseModel):
    """A search result pairing a thought with a relevance score."""

    thought: Thought
    score: float
    match_type: str = "hybrid"  # vector | fts | hybrid
