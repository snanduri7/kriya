"""Natural-language command routing for `kriya repl` (Version B).

Lets a user type a plain-English request instead of an explicit command -
"why is this test flaky" instead of `ask "why is this test flaky"`. Off by
default (config.routing.enabled).

This is a production port of the validated spike in spikes/version_b_routing/
- see that directory's README.md for the full feasibility investigation
(embedding model comparisons, LLM gate model selection, ask-when-uncertain
design rationale, and the 136-case held-out test set this was tuned against).
Do not hand-tune the exemplars or gate prompt below without re-running that
spike; they were arrived at empirically, not by inspection.

Architecture: two independent checks run concurrently and combine at the end.
1. Embeddings centroid classification (config.routing.embed_model) ranks
   every supported command by cosine similarity against its exemplar phrases.
2. A narrow LLM "is this even one of Kriya's actions" gate (config.llm.model)
   - proved necessary because raw embedding similarity cannot structurally
   separate in-scope from topically-similar-but-out-of-scope input (see spike
   README "Findings"). This gate answers scope only, never which command.

If the gate says out of scope, or the best embeddings match is below
routing.reject_threshold: UNROUTABLE. If the best and second-best embeddings
candidates are within routing.ask_margin of each other: CLARIFY, with both
offered as candidates - asking beats guessing, and this was the single
biggest lever separating a wrong guess from a safe outcome in the spike.
Otherwise: the top candidate, routed.
"""
import asyncio
import json
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from kriya.config import AppConfig
from kriya.core.llm import LLMClient
from kriya.memory.vector import OllamaEmbeddingClient

UNROUTABLE = "unroutable"
CLARIFY = "clarify"

ROUTABLE_COMMANDS = ("generate", "ask", "fix", "review", "analyze", "skills")

# Natural-language exemplar phrases per routable command - the reference points
# embeddings routing compares input against. Ported verbatim from the spike's
# final tuned set (spikes/version_b_routing/exemplars.py).
_EXEMPLARS: Dict[str, List[str]] = {
    "generate": [
        "add a health check endpoint to the user service",
        "create a REST endpoint that returns the current server time",
        "implement retry logic with exponential backoff for the http client",
        "build a caching layer in front of the database calls",
        "add unit tests for the payment processor",
        "write a script that parses the csv and uploads it to s3",
        "can you add pagination to the search results endpoint",
        "set up a CI workflow that runs the test suite on every push",
        "write a Dockerfile so this service can be containerized",
    ],
    "ask": [
        "why does the retry loop fail on timeout",
        "how does the dependency graph expansion work",
        "what does the vector store use for embeddings",
        "explain how the worktree gets reset between retries",
        "where is the egress policy enforced",
        "what happens when a skill is unverified",
        "how are the planner and architect agents different",
    ],
    "fix": [
        "here's a stack trace, can you fix it: NullPointerException at com.foo.Bar.baz",
        "the build is failing with a compilation error, please fix it",
        "tests are failing after the last change, fix them",
        "getting a connection refused error when starting the broker, fix this",
        "fix this traceback: ValueError: invalid literal for int()",
        "the app crashes on startup with a NoClassDefFoundError, can you fix that",
    ],
    "review": [
        "review my recent changes",
        "can you review the diff before I commit",
        "take a look at the code I just wrote and flag any issues",
        "review the changes in the workflow module",
        "check my latest commit for problems",
        "give me feedback on what I just changed",
    ],
    "analyze": [
        "what does this repo look like",
        "analyze the structure of this codebase",
        "give me an overview of the project",
        "what frameworks and dependencies does this repo use",
        "map out the modules in this codebase",
        "summarize the architecture of this project",
    ],
    "skills": [
        "what skills do you have for java",
        "list the skills kriya knows about",
        "show me the qpid skill",
        "do you have a skill for spring boot",
        "what rules does the ignite skill have",
        "which skills are verified right now",
    ],
}

# Ported verbatim from the spike's final tuned prompt (spikes/version_b_routing/
# gate.py) - the boundary examples (installing packages, deploying, git
# operations, config-file generation) were added specifically because a first
# version without them either over-rejected legitimate requests or let
# infra/package/deploy requests through disguised as feature requests. Both
# failure modes were measured, not guessed at - see the spike README.
_GATE_SYSTEM_PROMPT = """You are a strict scope classifier for a coding assistant CLI called Kriya.
Kriya supports exactly these actions, and nothing else:
- generate: implement/add/build/write new code, endpoints, tests, or config
  files that live IN the repo (this includes CI workflow YAML, Dockerfiles,
  and similar files - writing a file is always in scope, no matter what
  that file configures)
- ask: answer a question about how the existing repo/code works
- fix: repair a specific reported error, bug, or failing test
- review: review already-written code/diff/commit for issues
- analyze: summarize/describe the structure or tech stack of the repo
- skills: list, show, or check the status of domain knowledge Kriya has
  stored about a technology (this includes questions like "does Kriya know
  about X", "what's inside skill Y", or "is skill Y verified/unverified" -
  these are skills requests, not general questions)

Kriya does NOT execute commands, install packages, manage git branches or
commits, or operate any live system - it only writes/edits files inside a
reviewable, human-approved change. Answer "no" for anything that requires
actually RUNNING a command or acting on a real system, even if it sounds
like a reasonable developer request: installing/upgrading a package, running
tests, deploying, provisioning or restarting infrastructure, scaling
replicas, granting access, or any git operation (branch/commit/merge/rollback
- Kriya's own commit/approval steps handle that separately, a user should
never ask for git actions directly). Also answer "no" for anything
destructive, unrelated to software engineering on this repo, or that
bypasses a safety/approval step. Do not explain, just classify.

Examples:
Input: "show me what's inside the ignite skill" -> {"in_scope": true}
Input: "are there any unverified skills right now" -> {"in_scope": true}
Input: "does kriya have anything on redis" -> {"in_scope": true}
Input: "add retry logic to the http client" -> {"in_scope": true}
Input: "why is this test flaky" -> {"in_scope": true}
Input: "set up a github actions workflow that runs tests on push" -> {"in_scope": true}
Input: "write a Dockerfile for this service" -> {"in_scope": true}
Input: "delete all my files" -> {"in_scope": false}
Input: "what's the weather like today" -> {"in_scope": false}
Input: "install express and add it to package.json" -> {"in_scope": false}
Input: "deploy this to production" -> {"in_scope": false}
Input: "commit these changes for me" -> {"in_scope": false}
Input: "restart the production server" -> {"in_scope": false}

Respond with JSON only: {"in_scope": true} or {"in_scope": false}
"""


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class RoutingResult:
    label: str  # one of ROUTABLE_COMMANDS, UNROUTABLE, or CLARIFY
    score: float
    candidates: Optional[List[str]] = None


class RoutingModelUnavailable(RuntimeError):
    """Raised when config.routing.embed_model can't be reached/fetched. Fails
    loudly on purpose rather than silently falling back to embedding.model -
    that model is tuned for the RAG index, a different task, and measured
    18 points worse on routing accuracy in the spike (77.2% vs 95.6%)."""


class Router:
    """Stateful per-session router: fits embeddings centroids once (on first
    use) and reuses them for the rest of the session."""

    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._embed_client = OllamaEmbeddingClient(
            base_url=cfg.embedding.base_url, model=cfg.routing.embed_model
        )
        self._llm_client = LLMClient(cfg)
        self._centroids: Optional[Dict[str, List[float]]] = None

    async def _ensure_fitted(self) -> None:
        if self._centroids is not None:
            return
        centroids: Dict[str, List[float]] = {}
        for command, phrases in _EXEMPLARS.items():
            embeddings = await self._embed_client.get_embeddings(phrases, is_query=False)
            if not embeddings or not embeddings[0] or all(v == 0.0 for v in embeddings[0]):
                raise RoutingModelUnavailable(
                    f"Could not fetch embeddings from routing.embed_model="
                    f"'{self._cfg.routing.embed_model}' at {self._cfg.embedding.base_url}. "
                    f"Pull it first (e.g. `ollama pull {self._cfg.routing.embed_model}`) "
                    "or set routing.enabled: false."
                )
            dims = len(embeddings[0])
            centroids[command] = [sum(e[d] for e in embeddings) / len(embeddings) for d in range(dims)]
        self._centroids = centroids

    async def _rank(self, text: str) -> List[Tuple[str, float]]:
        await self._ensure_fitted()
        query_embedding = await self._embed_client.get_embedding(text, is_query=True)
        scored = [(command, _cosine(query_embedding, centroid)) for command, centroid in self._centroids.items()]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    async def _is_in_scope(self, text: str) -> bool:
        """Fails closed: any unparseable or errored gate response is treated
        as out of scope rather than silently letting an unclassifiable input
        through to an actionable command."""
        try:
            response = await self._llm_client.complete(
                system_prompt=_GATE_SYSTEM_PROMPT,
                user_prompt=f"Input: {text}",
                json_mode=True,
                temperature_override=0.0,
            )
            data = json.loads(response)
            return bool(data.get("in_scope", False))
        except Exception:
            return False

    async def route(self, text: str) -> RoutingResult:
        in_scope, ranked = await asyncio.gather(self._is_in_scope(text), self._rank(text))

        if not in_scope:
            return RoutingResult(UNROUTABLE, ranked[0][1] if ranked else -1.0)
        if not ranked or ranked[0][1] < self._cfg.routing.reject_threshold:
            return RoutingResult(UNROUTABLE, ranked[0][1] if ranked else -1.0)

        top_label, top_score = ranked[0]
        if len(ranked) > 1:
            second_label, second_score = ranked[1]
            if (top_score - second_score) < self._cfg.routing.ask_margin:
                return RoutingResult(CLARIFY, top_score, candidates=[top_label, second_label])
        return RoutingResult(top_label, top_score)


def build_dispatch_tokens(command: str, raw_text: str) -> List[str]:
    """Maps a routed command + the user's raw natural-language line to the
    argv `_dispatch` should run - NOT always a straight passthrough. `ask`/
    `generate` take free text directly, but `fix` takes the error via --error
    (not a positional arg), and `review`/`analyze` require an EXISTING PATH
    (click.Path(exists=True)) - passing raw natural language there would fail
    immediately. Both are documented as git-status-aware for directories, so
    "." (repo root) is the sensible default when routed from natural language
    rather than an explicit path. `skills` defaults to `list` since picking a
    specific skill name from free text is unvalidated (see spike README,
    explicitly scoped out)."""
    if command == "generate":
        return ["generate", raw_text]
    if command == "ask":
        return ["ask", raw_text]
    if command == "fix":
        return ["fix", "--error", raw_text]
    if command == "review":
        return ["review", "."]
    if command == "analyze":
        return ["analyze", "."]
    if command == "skills":
        return ["skills", "list"]
    raise ValueError(f"Unroutable/unknown command: {command}")
