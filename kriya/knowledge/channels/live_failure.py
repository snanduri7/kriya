"""Live-failure knowledge channel: generalizes the lesson-extraction block that used
to live inline in kriya/workflow/workflow.py into structured, category-tagged facts
instead of a single free-text sentence appended to staged_rules.txt.

Deliberately NOT this module's concern: *when* to call this. The trigger condition
(workflow.py: `state.last_model_override or state.budgets.retry_count >= 2`) is
already battle-tested - an earlier, looser version fired on every trivial
retry-then-succeed run and flooded staged_rules.txt with noise (see the comment
block above that check in workflow.py). This module only decides *what* to extract
once the caller has already decided a genuinely hard-won lesson is worth extracting.
"""
import json
import logging
from typing import Dict, List, NamedTuple, Optional

from kriya.core.llm import LLMClient
from kriya.knowledge.channels.base import KnowledgeChannel
from kriya.knowledge.schema import KNOWLEDGE_CATEGORIES, KnowledgeFact

logger = logging.getLogger(__name__)


class LiveFailureContext(NamedTuple):
    error_context: str
    file_contents: Dict[str, str]  # filepath -> final content, already read by the caller
    model_override: Optional[str] = None
    base_url_override: Optional[str] = None
    api_key_override: Optional[str] = None


def _strip_fences(text: str) -> str:
    text = text.strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _parse_fact_list(text: str) -> list:
    """Recovers a JSON array from a response that's supposed to be one but might have
    prose preamble/postamble or markdown fences around it - the same class of
    robustness DeveloperAgent's own JSON parsing needs, kept as a small local copy
    here rather than importing DeveloperAgent's private helper, so kriya/knowledge/
    doesn't reach into kriya/agents/ internals for a two-branch fallback."""
    cleaned = _strip_fences(text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(cleaned[start:end + 1])
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                pass
        logger.warning(f"Could not recover a fact list from live-failure extraction response: {text[:200]}...")
        return []


class LiveFailureChannel(KnowledgeChannel):

    def __init__(self, llm: LLMClient):
        self.llm = llm

    @property
    def name(self) -> str:
        return "live_failure"

    async def extract(self, context: LiveFailureContext) -> List[KnowledgeFact]:
        error_kind = (
            "runtime verification" if "RUNTIME VERIFICATION" in context.error_context
            else "compilation/test"
        )
        prompt = f"A {error_kind} error occurred:\n{context.error_context}\n\n"
        prompt += "The files were successfully fixed with this final content:\n"
        for filepath, content in context.file_contents.items():
            prompt += f"=== File: {filepath} ===\n{content}\n"
        prompt += (
            "\nExtract the durable, project-specific facts a future generation should know to avoid "
            f"repeating this error. Return a JSON array (at most 5 items) of objects, each with exactly "
            f"these keys:\n"
            f'  "category": one of {KNOWLEDGE_CATEGORIES}\n'
            '  "value": one concise sentence stating the fact\n'
            '  "quote": an exact short quote from the error text or file content above that this fact is '
            'grounded in, or null if there truly is none\n'
            "Only extract facts genuinely evidenced by the text above - do not restate the error itself as "
            "a fact. Return [] if there is nothing durable to extract. Output only the JSON array, nothing else."
        )

        try:
            raw = await self.llm.complete(
                system_prompt=(
                    "You are a senior software engineer. Extract durable, project-specific facts from this "
                    "error resolution so future generations of similar code avoid repeating it. Be precise - "
                    "prefer fewer, exact facts over vague generalities."
                ),
                user_prompt=prompt,
                json_mode=True,
                model_override=context.model_override,
                base_url_override=context.base_url_override,
                api_key_override=context.api_key_override,
            )
        except Exception as e:
            logger.warning(f"Live-failure knowledge extraction LLM call failed (non-fatal): {e}")
            return []

        facts: List[KnowledgeFact] = []
        for item in _parse_fact_list(raw):
            if not isinstance(item, dict):
                continue
            category = item.get("category")
            value = (item.get("value") or "").strip()
            quote = item.get("quote") or None
            if category not in KNOWLEDGE_CATEGORIES or not value:
                continue
            confidence = "llm_from_quote" if quote else "llm_inferred_no_quote"
            facts.append(KnowledgeFact(
                category=category,
                key=value[:60],
                value=value,
                source_channel=self.name,
                extraction_confidence=confidence,
                provenance=quote if quote else "derived from error_context/final file content, no exact quote",
            ))
        return facts
