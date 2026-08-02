import logging
import os
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

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

class PluginsConfig(BaseModel):
    directory: str = Field(default="./plugins")
    enabled: List[str] = Field(default_factory=list)

class PathsConfig(BaseModel):
    skills: str = Field(default="./skills")
    memory: str = Field(default="./memory")
    logs: str = Field(default="./logs")

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
