"""LLM classification and entity extraction via OpenRouter.

Classifies a thought into a category and extracts entities (people, projects)
and action items in a single LLM call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from sticky.core.models import ClassificationResult

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CATEGORIES = ["idea", "project", "person", "meeting", "action", "reference", "journal"]

SYSTEM_PROMPT = """\
You are a classification engine for a personal knowledge system.
Classify the user's thought into exactly ONE category and extract structured data.

Categories: idea, project, person, meeting, action, reference, journal

Respond with ONLY valid JSON — no markdown, no commentary, no extra text.
The JSON must have this exact structure:
{
  "category": "<one of the categories above>",
  "confidence": <float 0.0-1.0>,
  "topics": [<list of topic strings>],
  "people": [<list of person name strings>],
  "projects": [<list of project name strings>],
  "concepts": [<list of concept strings>],
  "actions": [<list of action item strings>],
  "source_url": "<URL or null>"
}

Rules:
- category MUST be one of: idea, project, person, meeting, action, reference, journal
- confidence is how sure you are about the category (0.0 to 1.0)
- topics: short topic labels relevant to the thought
- people: full names or first names of people mentioned
- projects: project names or product names mentioned
- concepts: named methods, frameworks, theories, or techniques (e.g. 'Zettelkasten', 'PARA method', 'gradient descent'). NOT generic topics.
- actions: only EXPLICIT action items or to-dos stated in the thought (e.g. "need to follow up", "agreed to do X"). Do NOT infer unstated tasks. Max 2 actions.
- source_url: if the thought text contains a URL, extract it. Otherwise null.
- If a field has no items, use an empty list []
- Output ONLY the JSON object, nothing else\
"""


def parse_classification_response(raw: str) -> ClassificationResult | None:
    """Parse a raw LLM response string into a ClassificationResult.

    Strips markdown code fences if present. Returns None if the response
    cannot be parsed or is missing required fields.
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
        logger.warning("Failed to parse classification response as JSON")
        return None

    if not isinstance(data, dict):
        logger.warning("Classification response is not a JSON object")
        return None

    # Require category and confidence
    if "category" not in data or "confidence" not in data:
        logger.warning("Classification response missing required fields")
        return None

    try:
        return ClassificationResult(
            category=data["category"],
            confidence=float(data["confidence"]),
            topics=data.get("topics", []),
            people=data.get("people", []),
            projects=data.get("projects", []),
            concepts=data.get("concepts", []),
            actions=data.get("actions", []),
            source_url=data.get("source_url"),
        )
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to construct ClassificationResult: %s", exc)
        return None


class Classifier:
    """Classifies thoughts via the OpenRouter LLM API."""

    def __init__(
        self,
        api_key: str,
        model: str = "anthropic/claude-sonnet-4",
    ) -> None:
        self.api_key = api_key
        self.model = model

    def _build_prompt(self, content: str, template: str | None = None) -> str:
        """Build the user prompt for classification.

        Args:
            content: The thought text to classify.
            template: Optional template hint (e.g. "person", "meeting")
                      to guide classification.

        Returns:
            The formatted user prompt string.
        """
        prompt = f"Classify this thought:\n\n{content}"
        if template:
            prompt += f"\n\nHint: the user tagged this as a '{template}' template."
        return prompt

    async def classify(
        self, content: str, template: str | None = None
    ) -> ClassificationResult | None:
        """Classify a thought via OpenRouter.

        Args:
            content: The thought text to classify.
            template: Optional template hint.

        Returns:
            A ClassificationResult on success, or None on any failure.
        """
        prompt = self._build_prompt(content, template)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    OPENROUTER_URL,
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                data = response.json()

            raw_content = data["choices"][0]["message"]["content"]
            return parse_classification_response(raw_content)

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "OpenRouter API returned %s: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return None
        except httpx.RequestError as exc:
            logger.warning("OpenRouter request failed: %s", exc)
            return None
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("Failed to extract content from OpenRouter response: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Unexpected error during classification: %s", exc)
            return None

    def classify_sync(
        self, content: str, template: str | None = None
    ) -> ClassificationResult | None:
        """Synchronous wrapper around classify().

        Uses asyncio.run() when no event loop is running, otherwise
        falls back to a ThreadPoolExecutor.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            return asyncio.run(self.classify(content, template))
        else:
            # Already in an async context — run in a thread
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.classify(content, template))
                return future.result()
