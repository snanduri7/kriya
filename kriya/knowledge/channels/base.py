"""Common interface every knowledge-extraction channel implements, so the schema/
rubric/staging core (kriya/knowledge/) never has to know how a given channel produces
facts. Adding a new source of knowledge later (doc ingestion, a package registry,
`kriya learn`) means writing one new module implementing this interface - nothing
else in kriya/knowledge/ changes.
"""
from abc import ABC, abstractmethod
from typing import Any, List

from kriya.knowledge.schema import KnowledgeFact


class KnowledgeChannel(ABC):
    """Base class every knowledge-extraction channel must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this channel, used as KnowledgeFact.source_channel."""
        pass

    @abstractmethod
    async def extract(self, context: Any) -> List[KnowledgeFact]:
        """Produce zero or more KnowledgeFacts from whatever `context` this channel
        needs (a RepositoryModel + Skill for repo_manifest, error/file state for
        live_failure, etc). Async on every implementation - even purely mechanical
        channels like repo_manifest - so callers can `await channel.extract(...)`
        uniformly without needing to know which channels happen to make an LLM call
        internally. Must not raise on ordinary "nothing found" cases - return an
        empty list instead."""
        pass
