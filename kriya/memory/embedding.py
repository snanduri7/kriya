"""EMBEDDING-CONTRACT-001: the embedding side of the provider contract.

Measured on Ollama 0.34.4 with nomic-embed-text (2026-10-01): `/api/embed`
with `truncate: false` refuses an over-long input (HTTP 400 "the input length
exceeds the context length"); the same input to `/api/embed` without it, or to
`/v1/embeddings`, returns 200 from a silently truncated prompt
(prompt_eval_count = 2048, the served context). So:

- E1: a failed or malformed embedding is a typed error, never a substitute
  (zero) vector. Every response is validated (count, dimension, finite values,
  non-zero norm).
- E2: one endpoint, `/api/embed` with `truncate: false`; the provider's refusal
  is authoritative. Over-long text is segmented deterministically at line
  boundaries (bounded), each segment keeping its parent chunk, segment index
  and source span; a local estimate that let a refused input through is
  recorded as an `embedding_admission_miss`.
- E3: an EmbeddingFingerprint (model digest, adapter, dimension, served
  context, prefix policy, preprocessing and segmentation versions) identifies
  vectors; a mismatch is never queried as current.
- E4: `trust_env=False`, Kriya-owned timeouts, at most one retry and only for
  a transient transport failure or a 5xx; never for an over-long input, a
  malformed response, an identity mismatch, a security refusal or an
  exhausted run deadline (which stays InferenceDeadlineError).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

ADAPTER_ID = "ollama-native-embed/1"
PREPROCESSING_VERSION = "1"  # chunk header + model prefix policy, below
SEGMENTATION_VERSION = "1"  # line-boundary halving, below
BATCH_SIZE = 32
# A conservative local admission estimate (an optimization only; the provider
# decides): code averages well above 2 bytes per token.
ESTIMATE_BYTES_PER_TOKEN = 2.0
MAX_SEGMENT_DEPTH = 12  # 2**12 segments per chunk at most: bounded progress
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0)

EMBEDDING_UNAVAILABLE = "EMBEDDING_UNAVAILABLE"
EMBEDDING_MALFORMED_RESPONSE = "EMBEDDING_MALFORMED_RESPONSE"
EMBEDDING_INPUT_TOO_LONG = "EMBEDDING_INPUT_TOO_LONG"
EMBEDDING_IDENTITY_CHANGED = "EMBEDDING_IDENTITY_CHANGED"


class EmbeddingError(Exception):
    """A typed embedding failure; ``reason_code`` names it."""

    reason_code = EMBEDDING_UNAVAILABLE

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(f"{self.reason_code}: {message}")
        self.details = details or {}


class EmbeddingUnavailableError(EmbeddingError):
    reason_code = EMBEDDING_UNAVAILABLE


class EmbeddingMalformedResponseError(EmbeddingError):
    reason_code = EMBEDDING_MALFORMED_RESPONSE


class EmbeddingInputTooLongError(EmbeddingError):
    reason_code = EMBEDDING_INPUT_TOO_LONG


class EmbeddingIdentityChangedError(EmbeddingError):
    reason_code = EMBEDDING_IDENTITY_CHANGED


def prefix_policy(model: str) -> str:
    """The query/document prefix the model expects (nomic's task prefixes)."""
    return "nomic-search-v1" if "nomic" in model.lower() else "none"


def apply_prefix(model: str, text: str, is_query: bool) -> str:
    if prefix_policy(model) == "nomic-search-v1":
        prefix = "search_query: " if is_query else "search_document: "
        return text if text.startswith(prefix) else prefix + text
    return text


@dataclass(frozen=True)
class EmbeddingFingerprint:
    """The semantic identity of a set of vectors (E3)."""

    model: str
    model_digest: str
    adapter: str
    dimension: int
    served_context: int
    prefix_policy: str
    preprocessing_version: str
    segmentation_version: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "digest": self.digest}


def native_root(base_url: str) -> str:
    """The one Ollama endpoint root: an OpenAI-style ``/v1`` suffix in the
    configured URL names the same server; there is no second endpoint."""
    root = base_url.rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def _validate(vectors: Any, expected: int, dimension: Optional[int]) -> List[List[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected:
        got = len(vectors) if isinstance(vectors, list) else type(vectors).__name__
        raise EmbeddingMalformedResponseError(f"expected {expected} embeddings, got {got}")
    out = []
    for index, vector in enumerate(vectors):
        if not isinstance(vector, list) or not vector:
            raise EmbeddingMalformedResponseError(f"embedding {index} is not a non-empty list")
        dimension = dimension if dimension is not None else len(vector)  # one dimension per response
        if len(vector) != dimension:
            raise EmbeddingMalformedResponseError(
                f"embedding {index} has dimension {len(vector)}, expected {dimension}")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
            raise EmbeddingMalformedResponseError(f"embedding {index} has a non-finite value")
        if math.fsum(float(v) * float(v) for v in vector) <= 0.0:
            raise EmbeddingMalformedResponseError(f"embedding {index} has zero norm")
        out.append([float(v) for v in vector])
    return out


def run_deadline(config: Any) -> Optional[float]:
    """The active run's root generation deadline (PROVIDER-CONTRACT-001A F-1),
    narrowed by an active local inference-deadline cap; None outside a run (an
    offline ``analyze`` uses its own transport timeouts)."""
    from kriya.control.run_coordinator import claim_run_generation_clock
    from kriya.core.llm import _INFERENCE_DEADLINE

    budget = getattr(getattr(config, "autonomy", None), "generation_time_budget_seconds", None)
    started = claim_run_generation_clock()
    deadline = started + budget if started is not None and budget is not None else None
    cap = _INFERENCE_DEADLINE.get()
    if cap is not None:
        deadline = cap if deadline is None else min(deadline, cap)
    return deadline


def _deadline_error(reason_code: str, deadline: float) -> Exception:
    from kriya.core.llm import DEADLINE_SOURCE_RUN_BUDGET, InferenceDeadlineError

    remaining_ms = int((deadline - time.monotonic()) * 1000)
    return InferenceDeadlineError(reason_code, {
        "deadline_source": DEADLINE_SOURCE_RUN_BUDGET, "remaining_budget_at_dispatch_ms": remaining_ms,
        "operation": "embedding",
    })


class OllamaEmbeddingClient:
    """The native Ollama embedding client (`/api/embed`, `truncate: false`).

    ``egress_policy`` is required (autonomy.egress_policy): embedding requests
    carry repository code and goal text, so they obey the same local_only
    boundary as LLMClient (PRD-012). ``timeout`` is Kriya-owned
    (``llm.transport``); ``trust_env`` is always off."""

    def __init__(self, base_url: str, model: str, *, egress_policy: str,
                 timeout: Optional[httpx.Timeout] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.root = native_root(base_url)
        self.model = model
        self.egress_policy = egress_policy
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.dimension: Optional[int] = None
        self.admission_misses = 0
        self._fingerprint: Optional[EmbeddingFingerprint] = None

    @property
    def detected_dimensions(self) -> Optional[int]:
        """Compatibility name: the dimension a successful call measured."""
        return self.dimension

    def _enforce_egress(self) -> None:
        """Raised before any request and never retried: a refused endpoint is
        a security boundary, not an outage."""
        if self.egress_policy == "local_only":
            from kriya.core.llm import EgressViolationError, is_local_url

            if not is_local_url(self.base_url):
                raise EgressViolationError(
                    f"Egress violation: embedding request to external endpoint '{self.base_url}' "
                    "blocked under 'local_only' policy."
                )

    async def _post(self, client: httpx.AsyncClient, path: str, body: Dict[str, Any],
                    deadline: Optional[float]) -> httpx.Response:
        """One request, retried at most once for a transient failure."""
        from kriya.core.llm import INFERENCE_DEADLINE_EXCEEDED, INFERENCE_DEADLINE_EXHAUSTED

        for attempt in (1, 2):
            timeout: Optional[float] = None
            if deadline is not None:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise _deadline_error(INFERENCE_DEADLINE_EXHAUSTED, deadline)
            try:
                request = client.post(f"{self.root}{path}", json=body)
                response = await (asyncio.wait_for(request, timeout) if timeout is not None else request)
            except asyncio.TimeoutError as error:
                raise _deadline_error(INFERENCE_DEADLINE_EXCEEDED, deadline) from error
            except httpx.TransportError as error:
                if attempt == 1:
                    logger.warning("Embedding request failed (%s); retrying once.", type(error).__name__)
                    continue
                raise EmbeddingUnavailableError(f"{type(error).__name__}: {error}") from error
            if response.status_code >= 500 and attempt == 1:
                logger.warning("Embedding endpoint returned HTTP %s; retrying once.", response.status_code)
                continue
            return response
        raise AssertionError("unreachable")

    async def embed(self, texts: Sequence[str], *, is_query: bool = False,
                    deadline: Optional[float] = None) -> List[List[float]]:
        """One vector per text, validated; raises a typed error otherwise."""
        self._enforce_egress()
        if not texts:
            return []
        vectors: List[List[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            for start in range(0, len(texts), BATCH_SIZE):
                batch = [apply_prefix(self.model, text, is_query) for text in texts[start:start + BATCH_SIZE]]
                response = await self._post(
                    client, "/api/embed", {"model": self.model, "input": batch, "truncate": False}, deadline)
                if response.status_code == 400 and "context length" in response.text:
                    raise EmbeddingInputTooLongError(
                        response.text.strip()[:200], details={"batch_size": len(batch)})
                if response.status_code != 200:
                    raise EmbeddingUnavailableError(
                        f"HTTP {response.status_code}: {response.text.strip()[:200]}")
                try:
                    payload = response.json()
                except ValueError as error:
                    raise EmbeddingMalformedResponseError("response is not JSON") from error
                vectors.extend(_validate(payload.get("embeddings") if isinstance(payload, dict) else None,
                                         len(batch), self.dimension))
                self.dimension = len(vectors[0])
        return vectors

    async def get_embedding(self, text: str, client: Optional[httpx.AsyncClient] = None,
                            is_query: bool = False, deadline: Optional[float] = None) -> List[float]:
        """The vector of one text (``client`` is accepted for compatibility)."""
        del client
        return (await self.embed([text], is_query=is_query, deadline=deadline))[0]

    async def get_embeddings(self, texts: List[str], is_query: bool = False,
                             deadline: Optional[float] = None) -> List[List[float]]:
        return await self.embed(texts, is_query=is_query, deadline=deadline)

    async def fingerprint(self) -> EmbeddingFingerprint:
        """The served model's identity: digest (/api/tags), served context
        (/api/show) and the dimension of a real probe embedding."""
        if self._fingerprint is not None:
            return self._fingerprint
        self._enforce_egress()
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            try:
                tags = (await client.get(f"{self.root}/api/tags")).json()
                show = (await self._post(client, "/api/show", {"model": self.model}, None)).json()
            except (httpx.HTTPError, ValueError) as error:
                raise EmbeddingUnavailableError(f"model identity unavailable: {error}") from error
        digest = next((m.get("digest", "") for m in tags.get("models", []) if m.get("name") == self.model), "")
        if not digest:
            raise EmbeddingUnavailableError(f"model {self.model!r} is not served")
        info = show.get("model_info", {}) if isinstance(show, dict) else {}
        context = next((int(v) for k, v in info.items() if k.endswith(".context_length")), 0)
        if context <= 0:
            raise EmbeddingUnavailableError(f"served context of {self.model!r} is unknown")
        dimension = len(await self.get_embedding("kriya embedding identity probe"))
        self._fingerprint = EmbeddingFingerprint(
            model=self.model, model_digest=digest, adapter=ADAPTER_ID, dimension=dimension,
            served_context=context, prefix_policy=prefix_policy(self.model),
            preprocessing_version=PREPROCESSING_VERSION, segmentation_version=SEGMENTATION_VERSION,
        )
        return self._fingerprint


def configured_client(config: Any) -> "OllamaEmbeddingClient":
    """The production embedding client: the configured endpoint and model,
    the egress policy, and Kriya-owned timeouts (llm.transport,
    SECURITY_AUTHORITY) - one transport authority for every call site."""
    from kriya.core.llm import transport_timeout

    return OllamaEmbeddingClient(
        base_url=config.embedding.base_url, model=config.embedding.model,
        egress_policy=config.autonomy.egress_policy, timeout=transport_timeout(config),
    )


@dataclass(frozen=True)
class EmbeddedSegment:
    """One stored vector: a whole chunk (segment 0 of 1), or one segment of an
    over-long chunk. ``start_line``/``end_line`` are the parent chunk's source
    span (chunk text carries header lines that are not source lines, so a
    segment is identified by its index, never by a guessed sub-span)."""

    parent_chunk: int
    segment_index: int
    start_line: int
    end_line: int
    text: str
    vector: List[float]


def _halves(text: str) -> Optional[Tuple[str, str]]:
    """Two halves at the middle line boundary; a single line splits by
    characters. None when nothing is left to split."""
    lines = text.split("\n")
    if len(lines) > 1:
        middle = len(lines) // 2
        return "\n".join(lines[:middle]), "\n".join(lines[middle:])
    if len(text) > 1:
        return text[:len(text) // 2], text[len(text) // 2:]
    return None


async def _segment(client: OllamaEmbeddingClient, text: str, limit_bytes: float,
                   deadline: Optional[float]) -> List[Tuple[str, List[float]]]:
    """(text, vector) pieces of one chunk: whole when the provider accepts it,
    else halved at line boundaries until every piece is accepted (bounded).
    Every piece is estimated first; only an estimated fit is sent."""
    pieces: List[Tuple[str, List[float]]] = []
    pending = [(text, 0, len(text.encode("utf-8")) <= limit_bytes)]
    while pending:
        piece, depth, fits = pending.pop(0)
        try:
            if not fits:
                raise EmbeddingInputTooLongError("estimated over the served context")
            pieces.append((piece, (await client.embed([piece], deadline=deadline))[0]))
        except EmbeddingInputTooLongError:
            if fits:
                client.admission_misses += 1
                logger.info("embedding_admission_miss: a piece accepted by the local estimate was "
                            "refused by the provider as over its served context")
            halves = _halves(piece) if depth < MAX_SEGMENT_DEPTH else None
            if halves is None:
                raise
            pending[0:0] = [(half, depth + 1, len(half.encode("utf-8")) <= limit_bytes) for half in halves]
    return pieces


async def embed_chunks(client: OllamaEmbeddingClient, chunks: Sequence[Dict[str, Any]],
                       served_context: int, deadline: Optional[float] = None) -> List[EmbeddedSegment]:
    """Every chunk's vectors, in order, batched; a batch the provider refuses
    as too long is redone chunk by chunk, segmenting the refused ones. All or
    nothing for the caller: any other failure raises."""
    limit_bytes = served_context * ESTIMATE_BYTES_PER_TOKEN
    out: List[EmbeddedSegment] = []
    for base in range(0, len(chunks), BATCH_SIZE):
        group = list(chunks[base:base + BATCH_SIZE])
        fits = [len(c["text"].encode("utf-8")) <= limit_bytes for c in group]
        vectors: Optional[List[List[float]]] = None
        if all(fits):
            try:
                vectors = await client.embed([c["text"] for c in group], deadline=deadline)
            except EmbeddingInputTooLongError:
                vectors = None
        for offset, chunk in enumerate(group):
            start = int(chunk.get("start", 1))
            end = int(chunk.get("end", start))
            pieces = ([(chunk["text"], vectors[offset])] if vectors is not None
                      else await _segment(client, chunk["text"], limit_bytes, deadline))
            out.extend(EmbeddedSegment(base + offset, index, start, end, text, vector)
                       for index, (text, vector) in enumerate(pieces))
    return out
