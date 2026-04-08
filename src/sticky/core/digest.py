"""Digest generation with LLM-powered topic grouping and offline fallback.

Generates daily/weekly/monthly digests summarising recently captured thoughts,
extracting action items, people mentioned, and resurfacing older related thoughts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from sticky.core.models import Thought

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DIGEST_SYSTEM_PROMPT = """You generate concise daily digest summaries for a personal knowledge management system.
Given a list of recently captured thoughts, produce a JSON response with these exact keys:

{
  "topics": [
    {"label": "Topic Name", "summary": "Brief summary", "thought_indices": [0, 2, 4]}
  ],
  "action_items": [
    {"content": "Follow up with Sarah", "person": "Sarah"}
  ],
  "people": [
    {"name": "Sarah", "context": "career discussion, auth migration"}
  ]
}

Rules:
- Group related thoughts into 2-5 topics
- thought_indices reference the position in the input thought list (0-indexed)
- Extract action items as brief imperative statements
- List people with brief context
- Return ONLY valid JSON. No markdown. No explanation.
"""


def build_digest_prompt(
    thoughts: list[Thought], resurfaced: Thought | None = None
) -> str:
    """Build the user prompt with numbered thoughts.

    Args:
        thoughts: List of recent thoughts to include in the digest.
        resurfaced: An optional older thought to resurface.

    Returns:
        The formatted user prompt string.
    """
    lines: list[str] = ["Here are the recent thoughts to summarise:\n"]
    for i, t in enumerate(thoughts):
        lines.append(f"[{i}] {t.content}")

    if resurfaced is not None:
        lines.append(
            f"\nFrom your archive: \"{resurfaced.content}\" "
            f"(captured {resurfaced.created_at[:10]})"
        )

    return "\n".join(lines)


def parse_digest_response(
    raw: str, thoughts: list[Thought]
) -> dict[str, Any] | None:
    """Parse LLM JSON response into formatted digest text and source mapping.

    Strips markdown code fences if present. Returns None if the response
    cannot be parsed.

    Args:
        raw: Raw LLM response string.
        thoughts: The original thought list (for index-to-ID mapping).

    Returns:
        Dict with keys: text, source_map, action_items, people — or None on failure.
    """
    text = raw.strip()

    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
    match = fence_pattern.match(text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse digest response as JSON")
        return None

    if not isinstance(data, dict):
        logger.warning("Digest response is not a JSON object")
        return None

    topics = data.get("topics", [])
    action_items = data.get("action_items", [])
    people = data.get("people", [])

    # Build source mapping: topic label -> list of thought IDs
    source_map: dict[str, list[str]] = {}
    for topic in topics:
        label = topic.get("label", "Unknown")
        indices = topic.get("thought_indices", [])
        ids = []
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(thoughts):
                ids.append(thoughts[idx].id)
        source_map[label] = ids

    # Build formatted text
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%B %d, %Y")
    count = len(thoughts)

    lines: list[str] = [
        f"YOUR DAY IN THOUGHTS — {date_str} — {count} thoughts captured",
        "",
        "TOPICS",
    ]

    for topic in topics:
        label = topic.get("label", "Unknown")
        summary = topic.get("summary", "")
        indices = topic.get("thought_indices", [])
        valid_count = sum(
            1 for idx in indices
            if isinstance(idx, int) and 0 <= idx < len(thoughts)
        )
        lines.append(f"  {label} ({valid_count} thoughts)  {summary}")

    if action_items:
        lines.append("")
        lines.append("ACTION ITEMS")
        for item in action_items:
            content = item.get("content", "")
            person = item.get("person")
            if person:
                lines.append(f"  [ ] {content} ({person})")
            else:
                lines.append(f"  [ ] {content}")

    if people:
        lines.append("")
        lines.append("PEOPLE MENTIONED")
        for p in people:
            name = p.get("name", "")
            context = p.get("context", "")
            lines.append(f"  {name} — {context}")

    formatted = "\n".join(lines)

    return {
        "text": formatted,
        "source_map": source_map,
        "action_items": action_items,
        "people": people,
    }


class DigestGenerator:
    """Generates digests from captured thoughts via LLM or offline fallback."""

    def __init__(
        self,
        api_key: str,
        model: str = "anthropic/claude-sonnet-4",
    ) -> None:
        self.api_key = api_key
        self.model = model

    def generate_offline(self, thoughts: list[Thought]) -> dict[str, Any]:
        """Generate a simple offline digest by grouping thoughts by category.

        Used as a fallback when the LLM is unavailable.

        Args:
            thoughts: List of thoughts to summarise.

        Returns:
            Dict with keys: text, source_map, action_items, people.
        """
        if not thoughts:
            return {
                "text": "No thoughts captured in this period.",
                "source_map": {},
                "action_items": [],
                "people": [],
            }

        # Group by category
        groups: dict[str, list[Thought]] = defaultdict(list)
        for t in thoughts:
            cat = t.category or "uncategorized"
            groups[cat].append(t)

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%B %d, %Y")
        count = len(thoughts)

        lines: list[str] = [
            f"YOUR DAY IN THOUGHTS — {date_str} — {count} thoughts captured",
            "(offline digest — LLM unavailable)",
            "",
            "TOPICS",
        ]

        source_map: dict[str, list[str]] = {}

        for cat in sorted(groups.keys()):
            cat_thoughts = groups[cat]
            label = cat.capitalize()
            lines.append(f"  {cat.upper()} ({len(cat_thoughts)} thoughts)")
            for t in cat_thoughts:
                preview = t.content[:80]
                lines.append(f"    - {preview}")
            source_map[label] = [t.id for t in cat_thoughts]

        formatted = "\n".join(lines)

        return {
            "text": formatted,
            "source_map": source_map,
            "action_items": [],
            "people": [],
        }

    async def generate(
        self,
        thoughts: list[Thought],
        resurfaced: Thought | None = None,
    ) -> dict[str, Any]:
        """Generate a digest via the OpenRouter LLM API.

        Falls back to offline generation on any failure.

        Args:
            thoughts: List of recent thoughts to summarise.
            resurfaced: Optional older thought to resurface.

        Returns:
            Dict with keys: text, source_map, action_items, people.
        """
        if not thoughts:
            return self.generate_offline([])

        prompt = build_digest_prompt(thoughts, resurfaced=resurfaced)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": DIGEST_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1500,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    OPENROUTER_URL,
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                data = response.json()

            raw_content = data["choices"][0]["message"]["content"]
            result = parse_digest_response(raw_content, thoughts)

            if result is None:
                logger.warning(
                    "Failed to parse LLM digest response, falling back to offline"
                )
                return self.generate_offline(thoughts)

            # Append resurfaced thought info if provided
            if resurfaced is not None:
                date_part = resurfaced.created_at[:10]
                try:
                    dt = datetime.fromisoformat(date_part)
                    date_label = dt.strftime("%b %d")
                except (ValueError, TypeError):
                    date_label = date_part
                result["text"] += (
                    f"\n\nFROM YOUR ARCHIVE (captured {date_label})"
                    f'\n  "{resurfaced.content}"'
                )

            return result

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "OpenRouter API returned %s: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return self.generate_offline(thoughts)
        except httpx.RequestError as exc:
            logger.warning("OpenRouter request failed: %s", exc)
            return self.generate_offline(thoughts)
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning(
                "Failed to extract content from OpenRouter response: %s", exc
            )
            return self.generate_offline(thoughts)
        except Exception as exc:
            logger.warning("Unexpected error during digest generation: %s", exc)
            return self.generate_offline(thoughts)

    def generate_sync(
        self,
        thoughts: list[Thought],
        resurfaced: Thought | None = None,
    ) -> dict[str, Any]:
        """Synchronous wrapper around generate().

        Uses asyncio.run() when no event loop is running, otherwise
        falls back to a ThreadPoolExecutor.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            return asyncio.run(self.generate(thoughts, resurfaced))
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run, self.generate(thoughts, resurfaced)
                )
                return future.result()

    def find_resurface_candidate(
        self,
        db: Any,
        engine: Any,
        recent_thoughts: list[Thought],
        min_age_days: int = 7,
    ) -> Thought | None:
        """Find the best semantically related older thought to resurface.

        Looks for thoughts older than *min_age_days* that are semantically
        related to the recent thoughts.  If no embedding engine is available
        or there are no old thoughts, returns None.

        Args:
            db: A Database instance.
            engine: An EmbeddingEngine instance (or None for no semantic matching).
            recent_thoughts: List of recent thoughts to compare against.
            min_age_days: Minimum age in days for a thought to be a candidate.

        Returns:
            The best matching older Thought, or None.
        """
        if not recent_thoughts:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
        cutoff_iso = cutoff.isoformat()

        # Fetch old thoughts
        rows = db.execute(
            "SELECT * FROM thoughts WHERE created_at < ? "
            "ORDER BY created_at DESC LIMIT 100",
            (cutoff_iso,),
        ).fetchall()

        if not rows:
            return None

        old_thoughts = [Thought.from_row(dict(row)) for row in rows]

        # If no embedding engine, return the most recent old thought
        if engine is None:
            return old_thoughts[0]

        # Find the old thought most similar to any recent thought
        best_thought: Thought | None = None
        best_score = -1.0

        # Get embeddings for recent thoughts (use first few to limit computation)
        recent_with_embeddings = [
            t for t in recent_thoughts if t.embedding is not None
        ]

        if not recent_with_embeddings:
            # No embeddings on recent thoughts; return most recent old thought
            return old_thoughts[0]

        for old_t in old_thoughts:
            if old_t.embedding is None:
                continue
            for recent_t in recent_with_embeddings[:5]:
                score = engine.cosine_similarity(
                    recent_t.embedding, old_t.embedding
                )
                if score > best_score:
                    best_score = score
                    best_thought = old_t

        return best_thought
