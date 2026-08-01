"""Classifiers for the Version B routing spike.

Every classifier's `predict(text)` returns a dict:
    {"label": str, "score": float, "diagnostic": str, "candidates": Optional[List[str]]}

`label` is one of the six commands, UNROUTABLE, or CLARIFY. `candidates` is
only set when label == CLARIFY - the 2+ commands the system couldn't
distinguish between, meant to be surfaced to the user as a disambiguating
question ("did you mean X or Y?") instead of guessing.
"""
import math
from typing import Dict, List, Optional

from kriya.core.llm import LLMClient
from kriya.memory.vector import OllamaEmbeddingClient

UNROUTABLE = "unroutable"
CLARIFY = "clarify"


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _result(label: str, score: float, diagnostic: str = "", candidates: Optional[List[str]] = None) -> Dict:
    return {"label": label, "score": score, "diagnostic": diagnostic, "candidates": candidates}


class ExemplarClassifier:
    def __init__(self, embed_client: OllamaEmbeddingClient, threshold: float = 0.3):
        self._embed_client = embed_client
        self._threshold = threshold
        self._labels: List[str] = []
        self._phrases: List[str] = []
        self._embeddings: List[List[float]] = []

    async def fit(self, exemplars: Dict[str, List[str]]) -> None:
        self._labels = []
        self._phrases = []
        for command, phrases in exemplars.items():
            for phrase in phrases:
                self._labels.append(command)
                self._phrases.append(phrase)
        self._embeddings = await self._embed_client.get_embeddings(self._phrases, is_query=False)

    async def predict(self, text: str) -> Dict:
        query_embedding = await self._embed_client.get_embedding(text, is_query=True)
        best_score = -1.0
        best_index = -1
        for i, emb in enumerate(self._embeddings):
            score = _cosine(query_embedding, emb)
            if score > best_score:
                best_score = score
                best_index = i
        if best_index == -1 or best_score < self._threshold:
            return _result(UNROUTABLE, best_score)
        return _result(self._labels[best_index], best_score, self._phrases[best_index])


class CentroidClassifier:
    """Compares against one averaged embedding per command (the centroid of
    all its exemplars) instead of every individual exemplar."""

    def __init__(self, embed_client: OllamaEmbeddingClient, threshold: float = 0.3):
        self._embed_client = embed_client
        self._threshold = threshold
        self._centroids: Dict[str, List[float]] = {}

    async def fit(self, exemplars: Dict[str, List[str]]) -> None:
        self._centroids = {}
        for command, phrases in exemplars.items():
            embeddings = await self._embed_client.get_embeddings(phrases, is_query=False)
            dims = len(embeddings[0])
            centroid = [sum(emb[d] for emb in embeddings) / len(embeddings) for d in range(dims)]
            self._centroids[command] = centroid

    async def rank(self, text: str) -> List[tuple]:
        """Returns every (command, score) pair, sorted best-first."""
        query_embedding = await self._embed_client.get_embedding(text, is_query=True)
        scored = [(command, _cosine(query_embedding, centroid)) for command, centroid in self._centroids.items()]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    async def predict(self, text: str) -> Dict:
        ranked = await self.rank(text)
        if not ranked or ranked[0][1] < self._threshold:
            return _result(UNROUTABLE, ranked[0][1] if ranked else -1.0)
        return _result(ranked[0][0], ranked[0][1])


class AskWhenUncertainClassifier:
    """Wraps a CentroidClassifier. Instead of always committing to the
    top-ranked command, this checks the margin between the best and
    second-best candidate: if they're too close to call, returns CLARIFY
    with both as candidates rather than silently picking one. Mirrors how a
    real conversational assistant handles ambiguity - ask, don't guess."""

    def __init__(self, inner: CentroidClassifier, reject_threshold: float = 0.3, margin: float = 0.05):
        self._inner = inner
        self._reject_threshold = reject_threshold
        self._margin = margin

    async def fit(self, exemplars: Dict[str, List[str]]) -> None:
        await self._inner.fit(exemplars)

    async def predict(self, text: str) -> Dict:
        ranked = await self._inner.rank(text)
        if not ranked or ranked[0][1] < self._reject_threshold:
            return _result(UNROUTABLE, ranked[0][1] if ranked else -1.0)
        top_label, top_score = ranked[0]
        if len(ranked) > 1:
            second_label, second_score = ranked[1]
            if (top_score - second_score) < self._margin:
                return _result(CLARIFY, top_score, candidates=[top_label, second_label])
        return _result(top_label, top_score)


class HybridGateClassifier:
    """Runs the LLM in-scope/out-of-scope gate (gate.py) first. Only when the
    gate says the input IS one of Kriya's six actions does it fall through to
    `inner` (an embeddings classifier, typically AskWhenUncertainClassifier)
    to decide WHICH one."""

    def __init__(self, llm_client: LLMClient, inner, gate_model_override: Optional[str] = None):
        self._llm_client = llm_client
        self._inner = inner
        self._gate_model_override = gate_model_override

    async def fit(self, exemplars: Dict[str, List[str]]) -> None:
        await self._inner.fit(exemplars)

    async def predict(self, text: str) -> Dict:
        from gate import is_in_scope

        in_scope = await is_in_scope(self._llm_client, text, model_override=self._gate_model_override)
        if not in_scope:
            return _result(UNROUTABLE, -1.0, "[llm-gate: out of scope]")
        return await self._inner.predict(text)
