"""SQLite database layer for sticky.

Provides typed CRUD operations over thoughts, entities, digests,
action items, and configuration, with FTS5 keyword search support.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sticky.core.models import (
    ActionItem,
    Digest,
    Entity,
    EntityMention,
    Thought,
)

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- Main thoughts table
CREATE TABLE IF NOT EXISTS thoughts (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    embedding BLOB,
    category TEXT,
    confidence REAL,
    needs_review INTEGER DEFAULT 0,
    source TEXT DEFAULT 'cli',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- FTS5 for keyword search
CREATE VIRTUAL TABLE IF NOT EXISTS thoughts_fts USING fts5(
    content,
    content='thoughts',
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS thoughts_ai AFTER INSERT ON thoughts BEGIN
    INSERT INTO thoughts_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS thoughts_ad AFTER DELETE ON thoughts BEGIN
    INSERT INTO thoughts_fts(thoughts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS thoughts_au AFTER UPDATE ON thoughts BEGIN
    INSERT INTO thoughts_fts(thoughts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO thoughts_fts(rowid, content) VALUES (new.rowid, new.content);
END;

-- Entities
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases TEXT DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    mention_count INTEGER DEFAULT 1
);

-- Entity-Thought links
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    thought_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    context TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, thought_id)
);

-- Config
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Digests
CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    thought_ids TEXT NOT NULL DEFAULT '[]',
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Action items with carryforward
CREATE TABLE IF NOT EXISTS action_items (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    person TEXT,
    source_thought_id TEXT REFERENCES thoughts(id) ON DELETE SET NULL,
    completed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    expires_at TEXT NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_thoughts_category ON thoughts(category);
CREATE INDEX IF NOT EXISTS idx_thoughts_created_at ON thoughts(created_at);
CREATE INDEX IF NOT EXISTS idx_thoughts_needs_review ON thoughts(needs_review);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_thought ON entity_mentions(thought_id);
CREATE INDEX IF NOT EXISTS idx_action_items_completed ON action_items(completed);
"""


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """SQLite database wrapper with typed methods for sticky data."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    _has_vec: bool | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Return the active connection, creating one if needed."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            # Load sqlite-vec extension for vector search (optional)
            try:
                self._conn.enable_load_extension(True)
                import sqlite_vec
                sqlite_vec.load(self._conn)
                self._conn.enable_load_extension(False)
                Database._has_vec = True
            except (AttributeError, OSError, Exception):
                Database._has_vec = False
        return self._conn

    @property
    def has_vec(self) -> bool:
        """Whether sqlite-vec is available."""
        if Database._has_vec is None:
            self._get_conn()
        return Database._has_vec

    def initialize(self) -> None:
        """Create all tables and indexes if they don't exist."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        # Migration: track embedding model per thought
        try:
            self.execute("ALTER TABLE thoughts ADD COLUMN embedding_model TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: source attribution
        try:
            self.execute("ALTER TABLE thoughts ADD COLUMN source_url TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Create vec0 virtual table for vector search (requires sqlite-vec)
        if self.has_vec:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_thoughts USING vec0(
                    thought_id TEXT PRIMARY KEY,
                    embedding float[384] distance_metric=cosine
                )
                """
            )
            conn.commit()

            # Backfill vec_thoughts from existing embeddings
            existing = self.execute(
                "SELECT id, embedding FROM thoughts WHERE embedding IS NOT NULL"
            ).fetchall()
            for row in existing:
                try:
                    self.execute(
                        "INSERT OR IGNORE INTO vec_thoughts(thought_id, embedding) VALUES (?, ?)",
                        (row["id"], row["embedding"]),
                    )
                except Exception:
                    pass
            conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement and return the cursor."""
        return self._get_conn().execute(sql, params)

    # ------------------------------------------------------------------
    # Thoughts CRUD
    # ------------------------------------------------------------------

    def insert_thought(self, thought: Thought) -> None:
        """Insert a thought into the database."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO thoughts
               (id, content, embedding, embedding_model, source_url, category, confidence,
                needs_review, source, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thought.id,
                thought.content,
                thought.embedding,
                thought.embedding_model,
                thought.source_url,
                thought.category,
                thought.confidence,
                int(thought.needs_review),
                thought.source,
                thought.metadata_json,
                thought.created_at,
                thought.updated_at,
            ),
        )
        # Sync embedding into vec_thoughts for KNN search
        if thought.embedding is not None and self.has_vec:
            conn.execute(
                "INSERT OR REPLACE INTO vec_thoughts(thought_id, embedding) VALUES (?, ?)",
                (thought.id, thought.embedding),
            )
        conn.commit()

    def get_thought(self, thought_id: str) -> Thought | None:
        """Retrieve a thought by ID, or None if not found."""
        row = self.execute(
            "SELECT * FROM thoughts WHERE id = ?", (thought_id,)
        ).fetchone()
        if row is None:
            return None
        return Thought.from_row(dict(row))

    def list_thoughts(
        self,
        limit: int = 20,
        cursor: str | None = None,
        category: str | None = None,
        entity: str | None = None,
        after: str | None = None,
        before: str | None = None,
        needs_review: bool | None = None,
        sort: str = "created_at_desc",
        thread: str | None = None,
    ) -> tuple[list[Thought], int]:
        """List thoughts with filtering, sorting, and cursor pagination.

        Args:
            limit: Maximum number of thoughts to return.
            cursor: Pagination cursor (created_at value to continue from).
            category: Filter by category.
            entity: Filter by entity name (uses subquery on entity_mentions + entities).
            after: Only return thoughts created after this ISO timestamp.
            before: Only return thoughts created before this ISO timestamp.
            needs_review: Filter by needs_review flag.
            sort: Sort order, one of 'created_at_desc' or 'created_at_asc'.
            thread: Filter by thread name stored in metadata.

        Returns:
            Tuple of (list of Thoughts, total count matching filters).
        """
        conditions: list[str] = []
        params: list = []

        if category is not None:
            conditions.append("t.category = ?")
            params.append(category)

        if needs_review is not None:
            conditions.append("t.needs_review = ?")
            params.append(int(needs_review))

        if after is not None:
            conditions.append("t.created_at > ?")
            params.append(after)

        if before is not None:
            conditions.append("t.created_at < ?")
            params.append(before)

        if entity is not None:
            conditions.append(
                """t.id IN (
                    SELECT em.thought_id FROM entity_mentions em
                    JOIN entities e ON e.id = em.entity_id
                    WHERE e.name = ? COLLATE NOCASE
                )"""
            )
            params.append(entity)

        if thread is not None:
            conditions.append("json_extract(t.metadata, '$.thread') = ?")
            params.append(thread)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # Total count
        count_sql = f"SELECT COUNT(*) FROM thoughts t {where}"
        total = self.execute(count_sql, tuple(params)).fetchone()[0]

        # Sort direction
        if sort == "created_at_asc":
            order = "t.created_at ASC"
        else:
            order = "t.created_at DESC"

        # Cursor pagination
        cursor_conditions = list(conditions)
        cursor_params = list(params)
        if cursor is not None:
            if sort == "created_at_asc":
                cursor_conditions.append("t.created_at > ?")
            else:
                cursor_conditions.append("t.created_at < ?")
            cursor_params.append(cursor)

        cursor_where = ""
        if cursor_conditions:
            cursor_where = "WHERE " + " AND ".join(cursor_conditions)

        query_sql = (
            f"SELECT t.* FROM thoughts t {cursor_where} "
            f"ORDER BY {order} LIMIT ?"
        )
        cursor_params.append(limit)

        rows = self.execute(query_sql, tuple(cursor_params)).fetchall()
        thoughts = [Thought.from_row(dict(row)) for row in rows]
        return thoughts, total

    def update_thought(
        self,
        thought_id: str,
        content: str | None = None,
        embedding: bytes | None = None,
        category: str | None = None,
        confidence: float | None = None,
        needs_review: bool | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Update specific fields of a thought."""
        sets: list[str] = []
        params: list = []

        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if embedding is not None:
            sets.append("embedding = ?")
            params.append(embedding)
        if category is not None:
            sets.append("category = ?")
            params.append(category)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(confidence)
        if needs_review is not None:
            sets.append("needs_review = ?")
            params.append(int(needs_review))
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(json.dumps(metadata))

        if not sets:
            return

        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(thought_id)

        conn = self._get_conn()
        conn.execute(
            f"UPDATE thoughts SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        # Sync embedding into vec_thoughts for KNN search
        if embedding is not None and self.has_vec:
            conn.execute(
                "DELETE FROM vec_thoughts WHERE thought_id = ?",
                (thought_id,),
            )
            conn.execute(
                "INSERT INTO vec_thoughts(thought_id, embedding) VALUES (?, ?)",
                (thought_id, embedding),
            )
        conn.commit()

    def delete_thought(self, thought_id: str) -> str | None:
        """Delete a thought by ID. Returns content preview or None if not found."""
        row = self.execute(
            "SELECT content FROM thoughts WHERE id = ?", (thought_id,)
        ).fetchone()
        if row is None:
            return None

        content = row["content"]
        preview = content[:80] if len(content) > 80 else content

        conn = self._get_conn()
        conn.execute("DELETE FROM thoughts WHERE id = ?", (thought_id,))
        if self.has_vec:
            conn.execute("DELETE FROM vec_thoughts WHERE thought_id = ?", (thought_id,))
        conn.commit()
        return preview

    # ------------------------------------------------------------------
    # FTS
    # ------------------------------------------------------------------

    def fts_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search thoughts by keyword using FTS5.

        Args:
            query: The search query string.
            limit: Maximum number of results.

        Returns:
            List of dicts with thought fields and FTS rank.
        """
        rows = self.execute(
            """SELECT t.*, thoughts_fts.rank
               FROM thoughts_fts
               JOIN thoughts t ON t.rowid = thoughts_fts.rowid
               WHERE thoughts_fts MATCH ?
               ORDER BY thoughts_fts.rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def insert_entity(self, entity: Entity) -> None:
        """Insert an entity into the database."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO entities
               (id, name, entity_type, aliases, first_seen, last_seen, mention_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.id,
                entity.name,
                entity.entity_type,
                entity.aliases_json,
                entity.first_seen,
                entity.last_seen,
                entity.mention_count,
            ),
        )
        conn.commit()

    def get_entity(self, entity_id: str) -> Entity | None:
        """Retrieve an entity by ID, or None if not found."""
        row = self.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            return None
        return Entity.from_row(dict(row))

    def get_entity_by_name(self, name: str) -> Entity | None:
        """Retrieve an entity by name (case-insensitive), checking aliases too.

        Checks the entity name first, then scans aliases JSON arrays.
        """
        # Check name directly (case-insensitive)
        row = self.execute(
            "SELECT * FROM entities WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row is not None:
            return Entity.from_row(dict(row))

        # Check aliases: scan all entities and check JSON arrays
        rows = self.execute("SELECT * FROM entities").fetchall()
        name_lower = name.lower()
        for row in rows:
            aliases = json.loads(row["aliases"])
            if any(alias.lower() == name_lower for alias in aliases):
                return Entity.from_row(dict(row))

        return None

    def list_entities(
        self,
        entity_type: str | None = None,
        query: str | None = None,
        limit: int = 20,
        sort: str = "last_seen",
    ) -> tuple[list[Entity], int]:
        """List entities with optional filtering.

        Args:
            entity_type: Filter by entity type.
            query: Filter by name (case-insensitive LIKE match).
            limit: Maximum number of entities to return.
            sort: Sort field, one of 'last_seen', 'mention_count', 'name'.

        Returns:
            Tuple of (list of Entities, total count matching filters).
        """
        conditions: list[str] = []
        params: list = []

        if entity_type is not None:
            conditions.append("entity_type = ?")
            params.append(entity_type)

        if query is not None:
            conditions.append("name LIKE ? COLLATE NOCASE")
            params.append(f"%{query}%")

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # Total count
        total = self.execute(
            f"SELECT COUNT(*) FROM entities {where}", tuple(params)
        ).fetchone()[0]

        # Sort
        sort_map = {
            "last_seen": "last_seen DESC",
            "mention_count": "mention_count DESC",
            "name": "name ASC",
        }
        order = sort_map.get(sort, "last_seen DESC")

        rows = self.execute(
            f"SELECT * FROM entities {where} ORDER BY {order} LIMIT ?",
            (*params, limit),
        ).fetchall()
        entities = [Entity.from_row(dict(row)) for row in rows]
        return entities, total

    def update_entity_seen(self, entity_id: str) -> None:
        """Increment mention_count and update last_seen for an entity."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE entities
               SET mention_count = mention_count + 1, last_seen = ?
               WHERE id = ?""",
            (_now_iso(), entity_id),
        )
        conn.commit()

    def insert_entity_mention(self, mention: EntityMention) -> None:
        """Insert an entity-thought mention link."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO entity_mentions
               (entity_id, thought_id, context, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                mention.entity_id,
                mention.thought_id,
                mention.context,
                mention.created_at,
            ),
        )
        conn.commit()

    def get_thoughts_for_entity(
        self, entity_id: str, limit: int = 10
    ) -> list[Thought]:
        """Get thoughts linked to an entity via entity_mentions."""
        rows = self.execute(
            """SELECT t.* FROM thoughts t
               JOIN entity_mentions em ON em.thought_id = t.id
               WHERE em.entity_id = ?
               ORDER BY t.created_at DESC
               LIMIT ?""",
            (entity_id, limit),
        ).fetchall()
        return [Thought.from_row(dict(row)) for row in rows]

    def delete_mentions_for_thought(self, thought_id: str) -> None:
        """Delete all entity mentions for a given thought."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM entity_mentions WHERE thought_id = ?", (thought_id,)
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Digests
    # ------------------------------------------------------------------

    def insert_digest(self, digest: Digest) -> None:
        """Insert a digest into the database."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO digests
               (id, content, thought_ids, period_start, period_end, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                digest.id,
                digest.content,
                digest.thought_ids_json,
                digest.period_start,
                digest.period_end,
                digest.created_at,
            ),
        )
        conn.commit()

    def list_digests(self, limit: int = 10) -> list[Digest]:
        """List recent digests, ordered by created_at descending."""
        rows = self.execute(
            "SELECT * FROM digests ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Digest.from_row(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Action Items
    # ------------------------------------------------------------------

    def insert_action_item(self, item: ActionItem) -> None:
        """Insert an action item into the database."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO action_items
               (id, content, person, source_thought_id, completed,
                created_at, completed_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.id,
                item.content,
                item.person,
                item.source_thought_id,
                int(item.completed),
                item.created_at,
                item.completed_at,
                item.expires_at,
            ),
        )
        conn.commit()

    def list_action_items(
        self, completed: bool = False, limit: int = 20
    ) -> list[ActionItem]:
        """List action items filtered by completion status."""
        rows = self.execute(
            """SELECT * FROM action_items
               WHERE completed = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (int(completed), limit),
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["completed"] = bool(data["completed"])
            result.append(ActionItem(**data))
        return result

    def complete_action_item(self, item_id: str) -> None:
        """Mark an action item as completed."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE action_items
               SET completed = 1, completed_at = ?
               WHERE id = ?""",
            (_now_iso(), item_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def get_config_value(self, key: str) -> str | None:
        """Get a config value by key, or None if not set."""
        row = self.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return row["value"]

    def set_config_value(self, key: str, value: str) -> None:
        """Set a config key-value pair (upsert)."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO config (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?""",
            (key, value, _now_iso(), value, _now_iso()),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return summary statistics about the database.

        Returns:
            Nested dict with keys: thoughts, entities, digests.
        """
        # -- Thoughts --
        thought_total = self.execute(
            "SELECT COUNT(*) FROM thoughts"
        ).fetchone()[0]

        # By category
        cat_rows = self.execute(
            "SELECT category, COUNT(*) as cnt FROM thoughts "
            "WHERE category IS NOT NULL GROUP BY category"
        ).fetchall()
        by_category = {row["category"]: row["cnt"] for row in cat_rows}

        # Needs review
        needs_review = self.execute(
            "SELECT COUNT(*) FROM thoughts WHERE needs_review = 1"
        ).fetchone()[0]

        # First and last
        first_row = self.execute(
            "SELECT created_at FROM thoughts ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        last_row = self.execute(
            "SELECT created_at FROM thoughts ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        first_thought = first_row["created_at"] if first_row else None
        last_thought = last_row["created_at"] if last_row else None

        # Capture rate: thoughts per day (if we have dates)
        capture_rate = 0.0
        if first_thought and last_thought and thought_total > 0:
            try:
                first_dt = datetime.fromisoformat(first_thought)
                last_dt = datetime.fromisoformat(last_thought)
                days = max((last_dt - first_dt).days, 1)
                capture_rate = round(thought_total / days, 2)
            except (ValueError, TypeError):
                capture_rate = 0.0

        # -- Entities --
        entity_total = self.execute(
            "SELECT COUNT(*) FROM entities"
        ).fetchone()[0]

        type_rows = self.execute(
            "SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type"
        ).fetchall()
        by_type = {row["entity_type"]: row["cnt"] for row in type_rows}

        top_rows = self.execute(
            "SELECT name, mention_count FROM entities "
            "ORDER BY mention_count DESC LIMIT 10"
        ).fetchall()
        top_mentioned = [
            {"name": row["name"], "mention_count": row["mention_count"]}
            for row in top_rows
        ]

        # -- Digests --
        digest_total = self.execute(
            "SELECT COUNT(*) FROM digests"
        ).fetchone()[0]
        last_digest_row = self.execute(
            "SELECT created_at FROM digests ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_generated = (
            last_digest_row["created_at"] if last_digest_row else None
        )

        return {
            "thoughts": {
                "total": thought_total,
                "by_category": by_category,
                "needs_review": needs_review,
                "first": first_thought,
                "last": last_thought,
                "capture_rate": capture_rate,
            },
            "entities": {
                "total": entity_total,
                "by_type": by_type,
                "top_mentioned": top_mentioned,
            },
            "digests": {
                "total": digest_total,
                "last_generated": last_generated,
            },
        }
