import logging
import os
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ModelCapabilities(BaseModel):
    """Measured local-model protocol capabilities, never inferred from API shape."""

    native_tool_calls: bool = Field(default=True)
    json_mode: bool = Field(default=True)
    reliable_multiline_json: bool = Field(default=False)
    streaming: bool = Field(default=True)
    max_tool_argument_chars: int = Field(default=8192, ge=256)
    preferred_edit_protocol: str = Field(default="small_native_tools")

class LLMConfig(BaseModel):
    provider: str = Field(default="openai")
    model: str = Field(default="llama3")
    api_key: str = Field(default="local-key")
    base_url: str = Field(default="http://localhost:11434/v1")
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=4096)
    extra_body: Dict[str, Any] = Field(default_factory=dict)
    reasoning: bool = Field(default=False)
    context_window: int = Field(default=32768)
    knowledge_cutoff: str = Field(default="2023-12-01")
    knowledge_cutoff_confidence: str = Field(default="estimated")
    # Applied ONLY to Developer generation calls that are directly responding to a
    # real prior Quality Gate failure (the same scope as prior_error_context's
    # fix-analysis instruction) - None (default) means no override, unchanged
    # behavior. A real, cited finding motivated this as opt-in rather than
    # lowering `temperature` globally: code-gen success rate measured dropping
    # ~25.7% going from 0.0->0.2 temperature for only a ~9.6% diversity gain
    # (AAAI-38 adaptive-temperature-sampling study) - the opposite of "add
    # randomness to shake a stuck retry loose." Deliberately NOT changed as the
    # global default: `temperature` defaults to 0.7 in default_config.yaml with
    # its own documented rationale (avoiding MoE repetition loops on longer,
    # from-scratch attempt-1 generations) that this finding doesn't touch or
    # contradict - a retry's typically-shorter, narrower regeneration is a
    # different case, not evidence the global default should change too.
    retry_temperature: Optional[float] = Field(default=None)
    # Applied ONLY to ReviewerAgent.run() calls (kriya/workflow/workflow.py, both the
    # pre-approval and final Review stages) - None (default) means no override, the
    # Reviewer inherits whatever `temperature` the rest of the run uses, unchanged
    # behavior. Added after a live, root-caused incident (2026-08-18, eval harness
    # batch b-10t, django_healthcheck_gap): a Reviewer call at temperature=0.2 (the
    # eval harness's own hardcoded eval-determinism override, not this field) entered
    # a verbatim degenerate repetition loop - one genuine review, then the identical
    # "### Code Review" / "### Merge Readiness" block repeated 250 times until it hit
    # the full max_tokens ceiling (639s wall-clock for what should have been a few
    # hundred tokens). Confirmed via Ollama's own server.log: a real, steady ~26 tok/s
    # generation the whole time, not a hang. This is the exact MoE-repetition-loop
    # failure class `temperature: 0.7`'s own default_config.yaml comment already
    # documents and defends against for the Developer's generation calls - the
    # Reviewer stage had no equivalent protection. A dedicated field (mirroring
    # retry_temperature's shape) rather than routing through agent_llms.reviewer.llm,
    # which would silently require re-specifying model/base_url/max_tokens/etc. too
    # just to change one sampling parameter.
    reviewer_temperature: Optional[float] = Field(default=None)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)

class PluginsConfig(BaseModel):
    directory: str = Field(default="./plugins")
    enabled: List[str] = Field(default_factory=list)

class PathsConfig(BaseModel):
    skills: str = Field(default="./skills")
    memory: str = Field(default="./memory")
    logs: str = Field(default="./logs")


class SkillsConfig(BaseModel):
    load_global: bool = Field(default=True)
    load_cwd: bool = Field(default=True)

class LoggingConfig(BaseModel):
    level: str = Field(default="INFO")
    file: Optional[str] = Field(default="./logs/kriya.log")

class MCPServerConfig(BaseModel):
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)

class EmbeddingConfig(BaseModel):
    model: str = Field(default="nomic-embed-text:latest")
    base_url: str = Field(default="http://localhost:11434/v1")

class AutonomyConfig(BaseModel):
    mode: str = Field(default="human-in-the-loop")
    egress_policy: str = Field(default="local_only")
    sensitive_paths: List[str] = Field(default_factory=lambda: [
        r".*\.env$", r".*secrets.*", r"\.github/workflows/.*", r"Jenkinsfile",
        r".*credentials.*", r".*password.*"
    ])
    risk_threshold_lines: int = Field(default=100)
    sandbox_execution: bool = Field(default=True)
    sandbox_env_allowlist: List[str] = Field(default_factory=lambda: [
        "HOME", "LANG", "LC_ALL", "USER", "SHELL", "TMPDIR", "TEMP", "TMP",
        "JAVA_HOME", "M2_HOME", "GRADLE_HOME", "VIRTUAL_ENV", "PYTHONPATH"
    ])
    sandbox_cpu_seconds: int = Field(default=240)
    sandbox_memory_mb: int = Field(default=4096)
    run_verification_enabled: bool = Field(default=True)
    run_verification_timeout_seconds: int = Field(default=90)
    web_lookup_enabled: bool = Field(default=False)
    # A live-lookup query's CONTENT is already hard-restricted (bare technology-name
    # strings only, enforced in code, never goal/design/code/error text) - this is a
    # separate, additional gate on WHEN it's allowed to fire at all. False means every
    # outbound query needs real-time confirmation (showing the exact terms and target
    # URL) before it leaves the machine; a non-interactive (-y) run with this still
    # False simply never sends the query, rather than the pre-existing behavior of
    # firing it anyway and silently discarding the result. Set True only if you've
    # accepted unattended outbound search as part of your threat model.
    web_lookup_auto_approve: bool = Field(default=False)
    # Off by default for the same reason as web_lookup_enabled above: this activates
    # a genuinely new capability (the Developer's own model gets a bounded native
    # tool-calling loop against the sandbox worktree on a compile failure, before
    # falling back to today's full-regeneration retry) rather than tuning an existing
    # one. Native tool-calling is confirmed reliable only for SMALL tool-call
    # arguments on local models (spikes/tool_call_developer/README.md) - the loop's
    # toolset (kriya/workflow/self_correction.py) is deliberately restricted to
    # small-argument-only actions on files already in the sandbox, never full file
    # content and never a new file, so this stays a narrow, additive recovery path,
    # not a parallel generation architecture.
    self_correction_loop_enabled: bool = Field(default=False)
    self_correction_loop_max_turns: int = Field(default=4)
    # Default 1 = today's exact behavior (a single first attempt, unchanged). A value
    # above 1 tries that many INDEPENDENT full-set candidates for the very first
    # generation attempt only (never on later retries, which already have real error
    # grounding to react to) before falling into the normal retry loop - see
    # kriya/workflow/best_of_n.py. Deliberately sequential, never parallel: a goal
    # binding real fixed resources (an embedded broker's port, an Ignite node's
    # discovery/comm ports) would have two candidates' generated apps port-conflict
    # under real parallel execution, and local model serving typically doesn't
    # meaningfully parallelize multiple requests against one loaded model anyway - so
    # peak resource usage at any moment stays identical to a normal single-attempt
    # run; the only cost is added wall-clock in the bounded worst case. Only takes
    # effect when a real isolated worktree sandbox exists (see best_of_n.py's own
    # guard) - never risks writing a discarded candidate's files into the real project.
    best_of_n_first_attempt: int = Field(default=1)

class SearchConfig(BaseModel):
    # Empty by default - live lookup stays fully inert unless a project explicitly
    # points this at a search backend (e.g. a self-hosted SearXNG instance) AND sets
    # autonomy.web_lookup_enabled: true. Two separate switches on purpose - flipping
    # one alone does nothing, so a config merge/copy-paste can't silently enable
    # outbound search.
    base_url: str = Field(default="")
    # How many candidate results to fetch and try per term before giving up on it.
    # A single top-ranked result for a well-known library is often a marketing/landing
    # page with nothing concrete to extract (confirmed via real testing against a real
    # search backend) - trying several in order meaningfully improves the odds of
    # actually finding something usable, which is the whole point of the feature.
    top_k: int = Field(default=3)

class FallbackModelConfig(BaseModel):
    model: str
    base_url: str = Field(default="http://localhost:11434/v1")
    api_key: str = Field(default="local-key")
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=4096)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    reasoning: bool = Field(default=False)
    context_window: int = Field(default=32768)
    knowledge_cutoff: str = Field(default="2023-12-01")
    knowledge_cutoff_confidence: str = Field(default="estimated")

class AgentModelConfig(BaseModel):
    # llm=None means "use the top-level llm config" (today's single-model behavior) -
    # every field here is opt-in, so a project that never touches agent_llms sees zero
    # behavior change. llm_chain is this role's OWN escalation list, tried in order
    # after llm (or the top-level llm if unset) fails - independent of Developer's
    # quality-gate-driven retry loop, which is untouched by this and keeps using the
    # top-level llm/llm_chain exactly as before.
    llm: Optional[LLMConfig] = Field(default=None)
    llm_chain: List[FallbackModelConfig] = Field(default_factory=list)

class AgentRolesConfig(BaseModel):
    # Developer deliberately has no entry here - it stays on the top-level llm/
    # llm_chain, escalated by the existing quality-gate retry loop (a fundamentally
    # different failure signal - "the generated code didn't compile/pass tests" -
    # than the call-level failures the roles below escalate on).
    planner: AgentModelConfig = Field(default_factory=AgentModelConfig)
    architect: AgentModelConfig = Field(default_factory=AgentModelConfig)
    reviewer: AgentModelConfig = Field(default_factory=AgentModelConfig)
    run_verifier: AgentModelConfig = Field(default_factory=AgentModelConfig)
    skill_gap: AgentModelConfig = Field(default_factory=AgentModelConfig)

class RoutingConfig(BaseModel):
    # On by default (since 2026-08-02) - a first-time kriya repl user typing a
    # plain-English request instead of an exact command was the single biggest
    # first-contact UX gap found in this platform's validation pass, and this
    # feature's own real-world validation (95.6% effective accuracy on a
    # 136-case held-out test set, ask-when-uncertain fallback for anything
    # genuinely ambiguous) was already strong enough to trust as a default.
    # Explicit commands are completely unaffected either way - routing only
    # ever activates when the typed line's first word doesn't already match a
    # real command name (kriya/repl.py::_route_line). The one real cost of
    # this default is a new hard dependency on routing.embed_model being
    # pulled; RoutingModelUnavailable fails loudly with the exact `ollama
    # pull ...` command needed (or `routing.enabled: false` to opt back out)
    # rather than silently degrading. See spikes/version_b_routing/README.md
    # for the full feasibility validation this config reflects.
    enabled: bool = Field(default=True)
    # Deliberately separate from embedding.model, which stays tuned for the RAG
    # code/doc index - a different task (long-form retrieval vs. short natural-
    # language intent phrases). Validated head-to-head on a 136-case held-out test
    # set: the packaged default RAG embedding model (nomic-embed-text) scored 77.2%
    # effective routing accuracy vs 95.6% for embeddinggemma - not a marginal gap,
    # and not something a silent fallback should paper over (see kriya/routing.py,
    # which fails loudly rather than falling back to embedding.model on this
    # model being unavailable).
    embed_model: str = Field(default="embeddinggemma:latest")
    # Below this cosine similarity to every command's exemplar centroid, treat the
    # input as out of scope even if the LLM gate said otherwise - defense in depth.
    reject_threshold: float = Field(default=0.3)
    # If the best and second-best candidate commands are within this similarity
    # margin of each other, ask which one instead of guessing (see
    # kriya.routing.Router.route). Ported from AskWhenUncertainClassifier in the
    # spike, where this was the single biggest lever separating a wrong guess from
    # a safe outcome.
    ask_margin: float = Field(default=0.05)

class KnowledgeConfig(BaseModel):
    training_cutoff: str = Field(default="2023-12-01")  # ISO date
    check_enabled: bool = Field(default=True)
    offline_mode: bool = Field(default=False)

class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    llm_chain: List[FallbackModelConfig] = Field(default_factory=list)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    mcp: Dict[str, MCPServerConfig] = Field(default_factory=dict)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    agent_llms: AgentRolesConfig = Field(default_factory=AgentRolesConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)

def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from a YAML file, merging with default configs."""
    config_dict = {}
    
    # Determine Kriya Installation Directory
    KRIYA_INSTALL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    # Try to load default configuration from package path
    default_path = os.path.join(os.path.dirname(__file__), "default_config.yaml")
    if os.path.exists(default_path):
        try:
            with open(default_path, "r") as f:
                default_data = yaml.safe_load(f)
                if default_data:
                    # Resolve relative paths in default config to Kriya Installation root
                    if "paths" in default_data:
                        for k, v in default_data["paths"].items():
                            if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                                default_data["paths"][k] = os.path.abspath(os.path.join(KRIYA_INSTALL_DIR, v))
                    if "plugins" in default_data and "directory" in default_data["plugins"]:
                        v = default_data["plugins"]["directory"]
                        if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                            default_data["plugins"]["directory"] = os.path.abspath(os.path.join(KRIYA_INSTALL_DIR, v))
                    config_dict.update(default_data)
        except Exception as e:
            logger.warning(f"Failed to load packaged default configuration at '{default_path}', falling back to bare defaults: {e}")
            
    # If no config_path is explicitly provided, look for 'kriya.yaml' or 'kriya.yml' in current directory
    # If not found, look for it in the Kriya Installation Directory
    config_dir = os.getcwd()
    if not config_path:
        for filename in ["kriya.yaml", "kriya.yml"]:
            path = os.path.join(os.getcwd(), filename)
            if os.path.exists(path):
                config_path = path
                config_dir = os.getcwd()
                break
        
        if not config_path:
            # Fall back to Kriya installation directory
            for filename in ["kriya.yaml", "kriya.yml"]:
                path = os.path.join(KRIYA_INSTALL_DIR, filename)
                if os.path.exists(path):
                    config_path = path
                    config_dir = KRIYA_INSTALL_DIR
                    break
    else:
        config_path = os.path.abspath(config_path)
        config_dir = os.path.dirname(config_path)

    # Load user config if specified and exists
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_data = yaml.safe_load(f)
                if user_data:
                    # Resolve relative paths in user config to config_dir
                    if "paths" in user_data:
                        for k, v in user_data["paths"].items():
                            if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                                user_data["paths"][k] = os.path.abspath(os.path.join(config_dir, v))
                    if "plugins" in user_data and "directory" in user_data["plugins"]:
                        v = user_data["plugins"]["directory"]
                        if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                            user_data["plugins"]["directory"] = os.path.abspath(os.path.join(config_dir, v))
                    
                    # Simple deep merge of level-1 dicts
                    for key, val in user_data.items():
                        if isinstance(val, dict) and key in config_dict and isinstance(config_dict[key], dict):
                            config_dict[key].update(val)
                        else:
                            config_dict[key] = val
        except Exception as e:
            raise ValueError(f"Failed to load configuration at {config_path}: {e}") from e
            
    cfg = AppConfig(**config_dict)
    
    # Enforce baseline sensitive paths inheritance
    baseline_sensitive = [
        r".*\.env$", r".*secrets.*", r"\.github/workflows/.*", r"Jenkinsfile", 
        r".*credentials.*", r".*password.*"
    ]
    for pattern in baseline_sensitive:
        if pattern not in cfg.autonomy.sensitive_paths:
            cfg.autonomy.sensitive_paths.append(pattern)
            
    return cfg
