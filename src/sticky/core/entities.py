"""Entity resolution for classified thoughts.

Resolves people and project names extracted by the classifier against
existing entities in the database, creating new entities as needed
and maintaining mention links.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sticky.core.db import Database
from sticky.core.models import ClassificationResult, Entity, EntityMention

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class EntityResolver:
    """Resolves entity names from classifications against the database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def resolve_entities(
        self,
        classification: ClassificationResult,
        thought_id: str,
    ) -> list[dict]:
        """Resolve people, projects, and concepts from a classification result.

        For each name in classification.people, resolves as type "person".
        For each name in classification.projects, resolves as type "project".
        For each name in classification.concepts, resolves as type "concept".

        Args:
            classification: The classification result containing entity names.
            thought_id: The ID of the thought these entities were extracted from.

        Returns:
            List of dicts with keys: name, type, is_new, id.
        """
        results: list[dict] = []

        for name in classification.people:
            result = self._resolve_one(name, "person", thought_id)
            if result is not None:
                results.append(result)

        for name in classification.projects:
            result = self._resolve_one(name, "project", thought_id)
            if result is not None:
                results.append(result)

        for name in classification.concepts:
            result = self._resolve_one(name, "concept", thought_id)
            if result is not None:
                results.append(result)

        return results

    def _resolve_one(
        self, name: str, entity_type: str, thought_id: str
    ) -> dict | None:
        """Find an existing entity or create a new one, and create a mention link.

        Args:
            name: The entity name to resolve.
            entity_type: The type of entity ("person" or "project").
            thought_id: The ID of the thought to link.

        Returns:
            Dict with keys: name, type, is_new, id. None if name is empty.
        """
        # Strip whitespace, skip empty names
        name = name.strip()
        if not name:
            return None

        existing = self.db.get_entity_by_name(name)

        if existing is not None:
            # Update existing entity
            self.db.update_entity_seen(existing.id)

            # Create mention link
            mention = EntityMention(
                entity_id=existing.id,
                thought_id=thought_id,
                context=name,
            )
            self.db.insert_entity_mention(mention)

            return {
                "name": name,
                "type": entity_type,
                "is_new": False,
                "id": existing.id,
            }
        else:
            # Create new entity
            entity = Entity.create(name=name, entity_type=entity_type)
            self.db.insert_entity(entity)

            # Create mention link
            mention = EntityMention(
                entity_id=entity.id,
                thought_id=thought_id,
                context=name,
            )
            self.db.insert_entity_mention(mention)

            return {
                "name": name,
                "type": entity_type,
                "is_new": True,
                "id": entity.id,
            }

    def add_alias(self, entity_id: str, alias: str) -> Entity | None:
        """Add an alias to an entity for future matching.

        Args:
            entity_id: The ID of the entity.
            alias: The alias string to add.

        Returns:
            The updated Entity, or None if the entity doesn't exist.
        """
        entity = self.db.get_entity(entity_id)
        if entity is None:
            return None

        alias = alias.strip()
        if not alias:
            return entity

        # Skip duplicates (case-insensitive check)
        if any(a.lower() == alias.lower() for a in entity.aliases):
            return entity

        # Update aliases in the database
        new_aliases = entity.aliases + [alias]
        conn = self.db._get_conn()
        conn.execute(
            "UPDATE entities SET aliases = ? WHERE id = ?",
            (json.dumps(new_aliases), entity_id),
        )
        conn.commit()

        # Return updated entity
        return self.db.get_entity(entity_id)

    def merge_entities(
        self, source_id: str, target_id: str
    ) -> Entity | None:
        """Merge source entity into target entity.

        Moves all mentions from source to target, adds source name as
        an alias on target, and deletes the source entity.

        Args:
            source_id: The ID of the entity to merge away (will be deleted).
            target_id: The ID of the entity to merge into (will be kept).

        Returns:
            The updated target Entity, or None if either entity doesn't exist.
        """
        source = self.db.get_entity(source_id)
        target = self.db.get_entity(target_id)

        if source is None or target is None:
            return None

        conn = self.db._get_conn()

        # Move mentions from source to target.
        # Use INSERT OR REPLACE to handle potential primary key conflicts
        # (same thought linked to both entities).
        rows = conn.execute(
            "SELECT thought_id, context, created_at FROM entity_mentions WHERE entity_id = ?",
            (source_id,),
        ).fetchall()

        for row in rows:
            # Check if target already has a mention for this thought
            existing = conn.execute(
                "SELECT 1 FROM entity_mentions WHERE entity_id = ? AND thought_id = ?",
                (target_id, row["thought_id"]),
            ).fetchone()

            if existing is None:
                conn.execute(
                    "INSERT INTO entity_mentions (entity_id, thought_id, context, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (target_id, row["thought_id"], row["context"], row["created_at"]),
                )

        # Delete source mentions
        conn.execute(
            "DELETE FROM entity_mentions WHERE entity_id = ?",
            (source_id,),
        )

        # Add source name as alias on target
        conn.commit()
        self.add_alias(target_id, source.name)

        # Also add source's aliases to target
        for alias in source.aliases:
            self.add_alias(target_id, alias)

        # Delete source entity
        conn.execute("DELETE FROM entities WHERE id = ?", (source_id,))
        conn.commit()

        return self.db.get_entity(target_id)
