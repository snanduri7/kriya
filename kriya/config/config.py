import logging
import os
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

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
    # Planner responses are execution metadata, not implementation bodies.
    # Keep their output budget independent from Developer generation.
    planner_max_tokens: int = Field(default=1600, ge=256)
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
    # Gates on SpecComplianceAgent (kriya/agents/agent.py): unlike compile/test/
    # run-verification, checks whether the goal's LITERALLY named requirements
    # (an exact field/method/class name, exact type, exact constant) actually
    # appear in the generated code - closes a gap compile/test/LSP grounding
    # structurally can't (syntactically valid, semantically non-compliant code
    # passes every other gate). Runs once, only after every other gate already
    # passed. Default False (opt-in), unlike run_verification_enabled's
    # default True - this is a genuinely NEW unconditional agent call, and
    # run_verification_enabled's own introduction required ~110 explicit
    # `cfg.autonomy.run_verification_enabled = False` opt-outs across
    # tests/test_workflow.py just to keep its shared llm.complete mock
    # side_effect sequencing intact; repeating that blast radius for a new,
    # separately-optional gate isn't warranted. Same "new capability, off
    # until proven" default already used for web_lookup_enabled and
    # self_correction_loop_enabled above.
    spec_compliance_enabled: bool = Field(default=False)
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
    # tool-calling loop against the sandbox worktree on a compile OR run-verification
    # failure, before falling back to today's full-regeneration retry) rather than
    # tuning an existing one. Native tool-calling is confirmed reliable only for
    # SMALL tool-call arguments on local models (spikes/tool_call_developer/README.md)
    # - the loop's toolset (kriya/workflow/self_correction.py) is deliberately
    # restricted to small-argument-only actions (including, since 2026-08-22, 4
    # read-only "ground truth" lookups - a project's real declared dependencies, an
    # external dependency's real public API via javap against the resolved
    # classpath, a Maven Central coordinate lookup, and a real compiled-output
    # listing - closing the gap where a fix needs grounding in something that isn't
    # any one file's content), never full file content and never a new file, so this
    # stays a narrow, additive recovery path, not a parallel generation architecture.
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
    # Optional end-to-end generation deadline. None preserves unbounded normal
    # CLI behavior; eval/demo harnesses with an outer timeout should set this to
    # the same or a slightly smaller value so Kriya can stop cleanly instead of
    # starting a model pass the harness will kill mid-generation.
    generation_time_budget_seconds: Optional[int] = Field(default=None, ge=1)
    generation_gate_reserve_seconds: int = Field(default=120, ge=0)
    generation_seconds_per_file_estimate: int = Field(default=90, ge=1)
    # Closes a real, confirmed-in-code gap (2026-08-23): index_repository() -
    # the only thing that ever populates dependency_graph.db's files/symbols
    # tables and vector_index.db's code embeddings - is called EXCLUSIVELY
    # from the `kriya analyze` CLI command, never from run_generation_workflow()
    # itself. A repo never explicitly `kriya analyze`-d therefore has an
    # empty persisted graph for the entire life of every `generate`/`fix`
    # call against it - already known to silently degrade the duplicate-type
    # and cross-package-mismatch Quality Gates to their in-memory-only
    # fallback (see docs/design.md §7.45's follow-up), and, for a genuinely
    # pre-existing repo Kriya never wrote itself, there's no established_files-
    # style fallback covering that gap at all. When True, run_generation_workflow()
    # checks once, before state.generation_started_monotonic starts (i.e. this
    # cost is structurally excluded from generation_time_budget_seconds, not
    # counted against it) whether dependency_graph.db has any indexed files
    # for this workspace; if not, it runs a real, one-time index_repository()
    # pass (never with changed=True - that flag scopes to files `git diff`
    # reports as modified/staged/untracked, which would silently index NOTHING
    # for a fully-committed pre-existing repo, exactly the case this exists to
    # cover) before proceeding. A repeat call against an already-indexed
    # workspace (milestone 2+ in a sequence, or a second `fix` call) is a
    # cheap no-op row check, not a re-index - the same file-level mtime cache
    # index_repository() already has makes this safe to leave enabled across
    # a whole milestone sequence. Any failure (embedding endpoint down, model
    # not pulled) is caught and logged as a warning - generation proceeds
    # exactly as it does today with an empty graph, never blocked by this.
    # Defaults False here (same "new capability, off until proven" rollout
    # already used for spec_compliance_enabled above - this is the first time
    # `generate`/`fix` would trigger real, uncontrolled embedding-endpoint
    # traffic implicitly rather than only on an explicit `kriya analyze`) -
    # candidate to flip True in default_config.yaml once live-validated, same
    # two-step rollout spec_compliance_enabled already went through.
    auto_index_missing_dependency_graph: bool = Field(default=False)

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
    # Extra identifiers the project owner explicitly declares public. Unknown
    # terms never receive unattended auto-approval.
    public_terms: List[str] = Field(default_factory=list)

class FallbackModelConfig(BaseModel):
    model: str
    base_url: str = Field(default="http://localhost:11434/v1")
    api_key: str = Field(default="local-key")
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=4096)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    reasoning: bool = Field(default=False)
    # Mirrors LLMConfig.extra_body (below) - a fallback model can need request
    # shape the primary model doesn't (e.g. qwen3.8:27b's reasoning_effort
    # string, distinct from the `reasoning` bool above, which only gates this
    # client's own <think>-stripping/token-floor logic). Before this field
    # existed, every call site that escalates to a fallback model (call_with_
    # escalation, attribution's triage tier, self_correction's tool loop, the
    # Developer retry loop's targeted/fallback-targeted/full-set paths, lesson
    # extraction) still unconditionally used the PRIMARY model's own extra_body
    # regardless of which model was actually being called - harmless when the
    # fallback ignores unknown fields, but silently wrong whenever it doesn't.
    extra_body: Dict[str, Any] = Field(default_factory=dict)
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
    spec_compliance: AgentModelConfig = Field(default_factory=AgentModelConfig)

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

class EngineeringTriageConfig(BaseModel):
    """MA1 of the control-plane implementation plan (kriya/workflow/triage.py) -
    kind/risk_class/execution_weight classification for a generation request.
    Two independent switches, same "flipping one alone does nothing" pattern
    already used for autonomy.web_lookup_enabled + search.base_url: `enabled`
    turns classification ON (so it actually runs and gets logged), `shadow_mode`
    keeps its result from affecting anything current Kriya does. MA1 requires
    shadow_mode to stay True regardless of `enabled` - nothing reads
    EngineeringRoute for a real decision until MA2. Both default False here at
    the pydantic-model level (the safe bare-AppConfig() fallback, same
    convention as spec_compliance_enabled/auto_index_missing_dependency_graph
    above) - default_config.yaml is what actually turns shadow classification
    on for real usage once MA1.3 wires it in."""

    enabled: bool = Field(default=False)
    shadow_mode: bool = Field(default=True)

class ProcessProfilesConfig(BaseModel):
    """MA2 of the control-plane implementation plan - whether a resolved
    ProcessProfile (kriya/workflow/process_profile.py) actually gets to
    change run_generation_workflow()'s behavior, and which behaviors
    specifically. Deliberately separate from EngineeringTriageConfig above:
    `engineering_triage.enabled` controls whether classification runs and
    is observable at all (MA1's scope, unchanged by this); `enabled` here
    plus each per-capability `enforce_*` flag controls whether MA2's actual
    behavioral changes are live - "safe incremental activation" per the
    control-plane plan, so MA2.5's approval gating can be validated live
    without MA2.6's context/verification changes also being active, and
    vice versa. All default False - a new capability stays off until it's
    been live-validated, same convention as spec_compliance_enabled/
    auto_index_missing_dependency_graph (kriya/config/config.py's
    AutonomyConfig, above)."""

    enabled: bool = Field(default=False)
    enforce_approval: bool = Field(default=False)
    enforce_context_depth: bool = Field(default=False)
    # MA2.6b explicit decision (control-plane implementation plan): "ProcessProfile
    # may increase safety/process cost, but it may not reduce Kriya's existing
    # verification baseline." MA2 ships verification depth as telemetry-only -
    # verification_tier is recorded, but PolymorphicValidator/Quality Gates run
    # IDENTICALLY regardless of execution_weight (the full regression suite stays
    # unconditional for LIGHT too, exactly as it is today). Rejected here, not just
    # left unread, so a misconfiguration can never quietly believe reduced-LIGHT-
    # verification is active when it isn't - see the validator below.
    enforce_verification_depth: bool = Field(default=False)

    @field_validator("enforce_verification_depth")
    @classmethod
    def _not_yet_implemented(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "process_profiles.enforce_verification_depth is not implemented yet - MA2 "
                "ships verification depth as telemetry-only by explicit design decision "
                "('a triage misclassification cannot reduce regression-test coverage in "
                "MA2'). Setting this to true would silently do nothing rather than actually "
                "changing verification behavior, which is exactly the quiet misconfiguration "
                "this validator exists to prevent. Leave it false until a future milestone "
                "implements real, deterministically-safeguarded behavioral enforcement."
            )
        return v

class ExecutionPolicyConfig(BaseModel):
    """MA4.15 of the control-plane implementation plan - whether
    kriya/policy/execution.py::ExecutionPolicy's real decisions ever get to
    influence Kriya's actual behavior, beyond being computed and logged.

    `enabled` defaults to True, unlike engineering_triage.enabled/
    process_profiles.enabled above (both default False at the pydantic-model
    level - "a new capability stays off until it's been live-validated").
    ExecutionPolicy's audit-only consultation is NOT a new, unvalidated
    capability the way those were when their own config sections were
    introduced: every MA4.3-4.14 real call site (kriya/core/llm.py,
    kriya/tools/validate.py, kriya/workflow/edit_safety.py, kriya/tools/
    web.py, kriya/workflow/worktree.py, kriya/workflow/workflow.py) has
    already been calling ExecutionPolicy.evaluate() unconditionally, safely,
    and exception-guarded for this task's entire duration - defaulting
    `enabled` to False now would be a real regression (telemetry that's
    already flowing today would silently stop), not a safe-activation
    default. `enabled` only gates WorkflowEngine's own two call sites today
    (_audit_approval_rules, _authorize_action's Stage 2A caller) - it is not
    yet threaded through every real call site (see kriya/policy/execution.py
    and kriya/workflow/workflow.py's own comments for the honestly-tracked
    boundary on that).

    `mode` is the actual audit-vs-enforce gate, and is where this session's
    binding constraint lives in code: MA4 was to roll out in AUDIT mode
    only, "before any future ENFORCE mode is considered" - not "before it
    is implemented," which MA4.13 already did (WorkflowEngine.
    _authorize_action's enforce=True branch is real, tested code). The
    validator below is what actually keeps that promise: "enforce" is
    accepted as a syntactically valid value (so a project can express
    intent and see a clear, deliberate rejection) but is REJECTED at
    validation time, exactly mirroring ProcessProfilesConfig.
    enforce_verification_depth's own precedent immediately above - fail
    loud at config-load time, never silently do nothing. Lifting this
    restriction is a distinct, later, explicit decision, not something
    accidentally reachable by editing a YAML file today."""

    enabled: bool = Field(default=True)
    mode: str = Field(default="audit")

    @field_validator("mode")
    @classmethod
    def _mode_must_be_audit_for_now(cls, v: str) -> str:
        if v not in ("audit", "enforce"):
            raise ValueError(f"execution_policy.mode must be 'audit' or 'enforce', got {v!r}")
        if v == "enforce":
            raise ValueError(
                "execution_policy.mode: 'enforce' is not enabled yet. MA4's rollout "
                "requires an explicit AUDIT-only period before ENFORCE mode is ever "
                "turned on for real - setting this to 'enforce' would silently do "
                "nothing useful without that separate, deliberate decision having been "
                "made yet. Leave this as 'audit' until a future milestone lifts this "
                "restriction."
            )
        return v

class WorkflowControllerConfig(BaseModel):
    """MA6.13/6.14 of the MA6 structured-execution implementation plan
    (kriya/workflow/workflow_controller.py) - mirrors ExecutionPolicyConfig's
    own audit/enforce precedent immediately above: `enabled` defaults False
    (a new, not-yet-broadly-validated capability stays off, same "ship the
    mechanism, default it off" pattern as engineering_triage/process_profiles
    when THEY were introduced). `mode` is the real gate - "shadow" builds a
    real EngineeringPlan and runs SubtaskExecutor against it for every
    subtask, but never lets the result affect real files or the run's
    actual outcome (kriya/workflow/workflow_controller.py's
    _run_structured_shadow) - the existing, unmodified run_generation_workflow()
    still owns the real outcome unconditionally in this mode.

    TRIED AND REVERTED, 2026-08-24: `enabled` was briefly flipped to
    True-by-default the same day, reasoned as "zero risk" because shadow
    mode is provably non-mutating (never affects real generation output).
    That reasoning covered CORRECTNESS but missed RESOURCE REACHABILITY:
    shadow mode still makes its own real Planner/Architect/SubtaskExecutor
    LLM calls through the real WorkflowEngine, independent of whatever
    run_generation_workflow() itself does. A widespread test pattern in
    this suite (tests/test_file_goal.py and others) mocks ONLY
    WorkflowEngine.run_generation_workflow (not Kernel/LLMClient), which
    was previously sufficient to guarantee zero real network calls end to
    end - once workflow_controller.enabled defaulted True, that same
    pattern silently let shadow's own agent calls reach a REAL, unmocked
    LLMClient, hanging/failing against a live network endpoint the test
    never expected to hit. Reverted same-day (never reached origin).
    Re-attempting this default flip needs a real test-suite audit first
    (every run_generation_workflow-only mock site, not just an explicit
    assertion about the default value) - not something to redo casually.

    "enforce" (MA7.8, 2026-08-24) is now real, allowed code - lifting the
    prior rejection was its own deliberate decision, confirmed with the
    user directly (mirroring how MA7.3 handled the analogous
    execution_policy.mode restriction: asked first, never silently
    lifted). SubtaskExecutor (MA6.5) still deliberately stops at "get file
    content or a tool result" - "enforce" does NOT port compile/test
    verification or approval gating into new machinery; instead
    WorkflowController._run_structured_enforce reuses the existing,
    mature run_generation_workflow() itself, once per subtask, the same
    real pattern kriya/workflow/milestones.py::run_milestones() already
    uses for milestones - see that method's own docstring for exactly
    what it does and its honest remaining scope boundaries (TOOL-tagged
    subtasks are refused outright, not silently skipped). `enabled`/`mode`
    both still default to False/"shadow" - "enforce" only ever runs for a
    project that explicitly opts in."""

    enabled: bool = Field(default=False)
    mode: str = Field(default="shadow")

    @field_validator("mode")
    @classmethod
    def _mode_must_be_valid(cls, v: str) -> str:
        if v not in ("shadow", "enforce"):
            raise ValueError(f"workflow_controller.mode must be 'shadow' or 'enforce', got {v!r}")
        return v

_VALID_RUNTIME_PROFILES = (None, "legacy", "validated", "hardened")


class AppConfig(BaseModel):
    """runtime_profile (2026-08-25, external review P2) - a named
    preset in place of remembering which combination of independent
    toggles (engineering_triage.shadow_mode, process_profiles.enabled,
    workflow_controller.enabled/mode) "hardened" actually means. Deliberately
    NOT a new independent config surface of its own: load_config() applies
    it as a straightforward, unconditional override of those existing
    fields AFTER the normal default+user merge. `legacy`, `validated`, and
    `hardened` are coherent presets; a user config must pick a profile OR
    hand-tune the individual fields, never mix both. None (the
    default) changes nothing - every field keeps behaving exactly as it
    always has, matching every existing kriya.yaml unchanged.

    Deliberately does NOT touch execution_policy.mode - that field's own
    validator has always hard-rejected "enforce" as a distinct, separate,
    later decision (kriya/config/config.py's own ExecutionPolicyConfig
    docstring), and this preset does not silently reach around that
    restriction. The narrow, always-on hard-invariant enforcement
    (kriya/policy/enforcement.py, MA7.3) and control-plane persistence
    already happen unconditionally whenever workflow_controller.enabled is
    true - there is no separate, real toggle for either one to include
    here, despite how the original review phrased the preset's contents."""

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
    engineering_triage: EngineeringTriageConfig = Field(default_factory=EngineeringTriageConfig)
    process_profiles: ProcessProfilesConfig = Field(default_factory=ProcessProfilesConfig)
    execution_policy: ExecutionPolicyConfig = Field(default_factory=ExecutionPolicyConfig)
    workflow_controller: WorkflowControllerConfig = Field(default_factory=WorkflowControllerConfig)
    runtime_profile: Optional[str] = Field(default=None)

    @field_validator("runtime_profile")
    @classmethod
    def _runtime_profile_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v not in _VALID_RUNTIME_PROFILES:
            raise ValueError(f"runtime_profile must be one of {_VALID_RUNTIME_PROFILES!r}, got {v!r}")
        return v

def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from a YAML file, merging with default configs."""
    config_dict = {}
    user_data: Dict[str, Any] = {}
    
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
                user_data = yaml.safe_load(f) or {}
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

    if cfg.runtime_profile is not None:
        conflicting = sorted(
            key for key in ("engineering_triage", "process_profiles", "workflow_controller")
            if key in user_data
        )
        if conflicting:
            raise ValueError(
                f"runtime_profile cannot be combined with explicit {conflicting!r}; choose the preset "
                "or configure the individual subsystems"
            )

    # runtime_profile="hardened" (2026-08-25, external review P2) - see
    # AppConfig's own docstring for exactly what this does and does not
    # cover. Applied AFTER the normal default+user merge/validation, as an
    # unconditional override - deliberately not a config_dict-level merge
    # trick, so it behaves identically regardless of how the user's own
    # kriya.yaml happened to set these same fields.
    if cfg.runtime_profile == "legacy":
        cfg.engineering_triage.shadow_mode = True
        cfg.process_profiles.enabled = False
        cfg.workflow_controller.enabled = False
        cfg.workflow_controller.mode = "shadow"
    elif cfg.runtime_profile == "validated":
        cfg.engineering_triage.shadow_mode = False
        cfg.process_profiles.enabled = True
        cfg.workflow_controller.enabled = True
        cfg.workflow_controller.mode = "shadow"
    elif cfg.runtime_profile == "hardened":
        cfg.engineering_triage.shadow_mode = False
        cfg.process_profiles.enabled = True
        cfg.workflow_controller.enabled = True
        cfg.workflow_controller.mode = "enforce"

    # Enforce baseline sensitive paths inheritance
    baseline_sensitive = [
        r".*\.env$", r".*secrets.*", r"\.github/workflows/.*", r"Jenkinsfile", 
        r".*credentials.*", r".*password.*"
    ]
    for pattern in baseline_sensitive:
        if pattern not in cfg.autonomy.sensitive_paths:
            cfg.autonomy.sensitive_paths.append(pattern)
            
    return cfg
