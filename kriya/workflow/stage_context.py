"""The explicit dependencies and the shared per-unit context of the kernel's extracted run stages (full modularization
W4, E2/E3).

``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``) builds one ``StageDeps`` per use, from
its own module-level names at CALL time, and hands it to the owner modules (``run_lifecycle``, ``outcome``, the
design stages ``stage_knowledge_guard``/``stage_analysis``/``stage_planning``/``stage_architecture`` driven by
``work_unit_runner``, and the stage modules later W4 slices add). An owner module never imports
``kriya.workflow.workflow`` (nor ``attempt``, ``workflow_controller`` or ``kriya.cli``): every collaborator a test
patches on the facade (``kriya.workflow.workflow.logger``, ``RepositoryAnalyzer``, ``retrieve_graph_context``,
``include_response_construction_owners``, ...) reaches the moved code through this object, so the patch keeps
intercepting it. Collaborators whose own imports are allowlisted for ``workflow.workflow`` only (non-public
PROVIDER/EXECUTION/KNOWLEDGE/SKILLS/TOOLBOX/AUTHORITY/RETRIEVAL modules) or that sit in the kernel's all-import cycle
(``kriya.workflow.state``, ``egress_authority``, ``context_budget``, ``planner_repair``, ``skill_extraction``,
``ownership_findings``, ``graph_retrieval``, ``kriya.skills.skill``, ``kriya.memory.*``, ...) are injected here
rather than imported by the owner modules, so extracting a stage adds neither an allowlist entry nor a member to that
cycle.

The E3 design-stage collaborators default to ``NOT_INJECTED`` values that fail on any use
(``StageCollaboratorMissing``): a ``StageDeps`` built for the lifecycle/outcome stages only (E2's
``WorkflowEngine._stage_deps``, a test double) can never silently run a design stage with a missing collaborator;
``WorkflowEngine._work_unit_deps`` injects every one of them (pinned by ``tests/test_work_unit_stages.py``). The E4
review, approval/apply and lesson collaborators (``stage_review``, ``stage_apply``) are a second fail-closed group
(``late_stage_fields``), injected by ``WorkflowEngine._late_stage_deps``.

``StageContext`` is ONE work unit's run-local names shared by the design stages: the run's inputs (the
``run_generation_workflow`` arguments the stages read, the run state and trace identity the engine opened) and what
each stage produces for the next stage and for the engine's Developer loop. It holds exactly the locals the stages
shared when they were one method, under the same names. E4 adds what the engine's Developer loop hands the late
stages (the sandbox path, the effective obligation ledger, the grounding goal, the work unit).

``RunState`` is the run's ``kriya.workflow.state.GenerationState``. It is spelled ``Any`` here on purpose: an
import of ``kriya.workflow.state`` (even under ``TYPE_CHECKING``, which the architecture scan counts as a
top-level edge) would put every owner module into the 67-module all-import strongly connected component that
``state.py`` belongs to.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, fields
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

RunState = Any  # kriya.workflow.state.GenerationState (see the module docstring for why it is not imported)


def _wall_clock() -> float:
    # Looked up on the time module at every call, exactly as the inline ``time.time()`` it replaces.
    return time.time()


def _monotonic() -> float:
    return time.monotonic()


class StageCollaboratorMissing(RuntimeError):
    """A design stage used a ``StageDeps`` collaborator its builder never injected."""


class _NotInjected:
    """The default of an E3/E4 collaborator field: every use (call, attribute, iteration, test) raises."""

    __slots__ = ("name", "group")

    def __init__(self, name: str, group: str = "design") -> None:
        self.name = name
        self.group = group      # "design": an E3 design-stage field; "late": an E4 review/apply/lesson field

    def __repr__(self) -> str:
        return f"<not injected: {self.name}>"

    def _missing(self, *_args: Any, **_kwargs: Any) -> Any:
        raise StageCollaboratorMissing(f"StageDeps.{self.name} was not injected")

    __call__ = _missing
    __iter__ = _missing
    __contains__ = _missing
    __bool__ = _missing

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("__"):   # protocol probes (copy, pickle, inspect) see an ordinary missing attribute
            raise AttributeError(attr)
        raise StageCollaboratorMissing(f"StageDeps.{self.name} was not injected")


def _injected(name: str) -> Any:
    return field(default=_NotInjected(name))


def _late(name: str) -> Any:
    return field(default=_NotInjected(name, "late"))


@dataclass(frozen=True)
class StageDeps:
    """What an extracted run stage may use, resolved by the engine when it builds this object."""

    logger: logging.Logger                                       # kriya.workflow.workflow.logger (3 test patches)
    config: Any                                                  # the kernel's AppConfig
    new_state: Callable[..., RunState]                           # GenerationState
    egress_authority_details: Callable[[Any], Dict[str, Any]]    # PRD-012 outbound authority, for the run event
    primary_model_runtime_details: Callable[[Any], Awaitable[Dict[str, Any]]]   # PRD-013/014 runtime + qualification
    role_independence_details: Callable[[Any], Dict[str, Any]]   # PRD-018 role -> exact runtime
    trace_events: Callable[[RunState], List[Dict[str, Any]]]     # the engine's _trace_run_events (drains + metrics)
    static_analysis_fields: Callable[[Any], Dict[str, Any]]      # PRD-031A result fields
    required_verification_evidence: Callable[..., List[Dict[str, Any]]]   # workflow._build_required_verification_evidence
    deterministic_gate_evidence: Callable[[List[Dict[str, Any]], Optional[int]], List[Dict[str, Any]]]
    wall_clock: Callable[[], float] = field(default=_wall_clock)
    monotonic: Callable[[], float] = field(default=_monotonic)

    # ---- E3 design stages (stages 0-3); WorkflowEngine._work_unit_deps injects every field below.
    # The engine's own collaborators (were ``self.<name>`` in run_generation_workflow).
    registry: Any = _injected("registry")                        # kernel.registry (the Planner's tool catalog)
    planner: Any = _injected("planner")
    architect: Any = _injected("architect")
    skill_gap_agent: Any = _injected("skill_gap_agent")
    llm: Any = _injected("llm")                                  # the CI-6 localization decision's model client
    engineering_triage: Any = _injected("engineering_triage")
    approve_web_lookup: Any = _injected("approve_web_lookup")    # WorkflowEngine._approve_web_lookup
    authorize_action: Any = _injected("authorize_action")        # WorkflowEngine._authorize_action
    # Names tests patch on kriya.workflow.workflow (resolved there at call time).
    RepositoryAnalyzer: Any = _injected("RepositoryAnalyzer")
    retrieve_graph_context: Any = _injected("retrieve_graph_context")
    include_response_construction_owners: Any = _injected("include_response_construction_owners")
    # kriya.workflow.workflow's own helpers (imported from there by the controller and milestones).
    unresolved_knowledge_report: Any = _injected("unresolved_knowledge_report")
    _log_phase_banner: Any = _injected("_log_phase_banner")
    # Lazily imported by the stage bodies before E3 (kriya.tools.knowledge, kriya.skills.skill, kriya.tools.web,
    # kriya.memory.*): non-public or all-import-cycle modules, resolved by the engine when it builds this object.
    KnowledgeGuard: Any = _injected("KnowledgeGuard")
    extract_library_versions: Any = _injected("extract_library_versions")
    SkillEngine: Any = _injected("SkillEngine")
    is_accidental_shared_skills_write: Any = _injected("is_accidental_shared_skills_write")
    fact_match: Any = _injected("fact_match")
    mentions_term: Any = _injected("mentions_term")
    is_version_supported: Any = _injected("is_version_supported")
    find_conflict_resolution: Any = _injected("find_conflict_resolution")
    record_conflict_resolution: Any = _injected("record_conflict_resolution")
    fetch_url_text: Any = _injected("fetch_url_text")
    configured_client: Any = _injected("configured_client")
    run_deadline: Any = _injected("run_deadline")
    LocalVectorStore: Any = _injected("LocalVectorStore")
    # All-import-cycle KERNEL modules (context_budget, planner_repair, skill_extraction, ownership_findings,
    # localization_decision, goal_facts) and non-public PROVIDER/RETRIEVAL/AUTHORITY names.
    CandidatePrompts: Any = _injected("CandidatePrompts")
    RetrievalLimits: Any = _injected("RetrievalLimits")
    _reserve_graph_context_budget: Any = _injected("_reserve_graph_context_budget")
    agent_request_capacity: Any = _injected("agent_request_capacity")
    allocation_window: Any = _injected("allocation_window")
    candidate_model: Any = _injected("candidate_model")
    fit_planner_request: Any = _injected("fit_planner_request")
    retrieval_limits_for: Any = _injected("retrieval_limits_for")
    STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS: Any = _injected("STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS")
    build_structured_plan_repair_prompt: Any = _injected("build_structured_plan_repair_prompt")
    classify_structured_plan_parse_issue: Any = _injected("classify_structured_plan_parse_issue")
    planner_outcome_for_completeness: Any = _injected("planner_outcome_for_completeness")
    planner_response_model: Any = _injected("planner_response_model")
    record_planner_outcome: Any = _injected("record_planner_outcome")
    _filter_misattributed_extraction: Any = _injected("_filter_misattributed_extraction")
    _sanitize_for_flat_file_line: Any = _injected("_sanitize_for_flat_file_line")
    _scoped_skill_gap_description: Any = _injected("_scoped_skill_gap_description")
    _skill_staleness_warning: Any = _injected("_skill_staleness_warning")
    _split_rules_by_verification: Any = _injected("_split_rules_by_verification")
    _stage_skill_conflicts: Any = _injected("_stage_skill_conflicts")
    _write_skill_extraction: Any = _injected("_write_skill_extraction")
    decide_localization: Any = _injected("decide_localization")
    developer_goal_facts: Any = _injected("developer_goal_facts")
    find_ownership_findings: Any = _injected("find_ownership_findings")
    findings_prompt_block: Any = _injected("findings_prompt_block")
    grounded_owner_candidates: Any = _injected("grounded_owner_candidates")
    owner_candidates_prompt_block: Any = _injected("owner_candidates_prompt_block")
    parse_ownership_justifications: Any = _injected("parse_ownership_justifications")
    settle_findings: Any = _injected("settle_findings")
    CALLABLE_KINDS: Any = _injected("CALLABLE_KINDS")
    InferenceDeadlineError: Any = _injected("InferenceDeadlineError")
    ActionRequest: Any = _injected("ActionRequest")
    ActionType: Any = _injected("ActionType")
    PolicyDecision: Any = _injected("PolicyDecision")
    PolicyDeniedError: Any = _injected("PolicyDeniedError")

    # ---- E4 review, approval/apply and lesson stages (stages 5-7); WorkflowEngine._late_stage_deps injects every
    # field below (and the E3 fields those stages use: llm, _log_phase_banner, InferenceDeadlineError).
    # The engine's own collaborators (were ``self.<name>`` in run_generation_workflow).
    reviewer: Any = _late("reviewer")
    developer: Any = _late("developer")
    audit_approval_rules: Any = _late("audit_approval_rules")              # WorkflowEngine._audit_approval_rules
    keep_full_suite_evidence: Any = _late("keep_full_suite_evidence")      # sets WorkflowEngine._prior_full_suite_evidence
    # Function-local imports of all-import-cycle modules in the moved bodies, as loaders called where they imported.
    load_full_suite_evidence_for_reuse: Any = _late("load_full_suite_evidence_for_reuse")   # kriya.workflow.baseline_policy
    load_lesson_extraction: Any = _late("load_lesson_extraction")          # kriya.knowledge.staging + live-failure channel
    # Names tests patch on kriya.workflow.workflow (resolved there at call time).
    delete_checkpoint: Any = _late("delete_checkpoint")
    commit_terminal_candidate: Any = _late("commit_terminal_candidate")
    derive_contract_transition: Any = _late("derive_contract_transition")
    _direct_terminal_writes: Any = _late("_direct_terminal_writes")
    # kriya.workflow.workflow's own helpers (the FINAL_REVIEW_* mapping keeps its provider imports on the facade).
    _final_review_error_code: Any = _late("_final_review_error_code")
    _record_review_fit: Any = _late("_record_review_fit")
    _review_refit_recorder: Any = _late("_review_refit_recorder")
    _record_approval_decision: Any = _late("_record_approval_decision")
    _restore_original_bytes: Any = _late("_restore_original_bytes")
    _terminal_contract_authorizations: Any = _late("_terminal_contract_authorizations")
    _raise_contract_registry_stop: Any = _late("_raise_contract_registry_stop")
    _raise_terminal_commit_stop: Any = _late("_raise_terminal_commit_stop")
    # All-import-cycle KERNEL modules (context_budget, review_context, ownership_findings) and non-public
    # AUTHORITY/EXECUTION/PROVIDER names (control.launch_approval, static_analysis.service, core.token_budget).
    review_requests: Any = _late("review_requests")
    build_reviewer_verified_evidence: Any = _late("build_reviewer_verified_evidence")
    build_candidate_diff_context: Any = _late("build_candidate_diff_context")
    ownership_review_evidence: Any = _late("ownership_review_evidence")
    REVIEW_SECTION_HEADER: Any = _late("REVIEW_SECTION_HEADER")
    banner: Any = _late("banner")
    commit_guard: Any = _late("commit_guard")
    ContextBudgetUnsatisfiableError: Any = _late("ContextBudgetUnsatisfiableError")


def design_stage_fields() -> Tuple[str, ...]:
    """The E3 collaborator field names (those with a design-stage ``NOT_INJECTED`` default)."""
    return tuple(f.name for f in fields(StageDeps) if isinstance(f.default, _NotInjected) and f.default.group == "design")


def late_stage_fields() -> Tuple[str, ...]:
    """The E4 collaborator field names (the review, approval/apply and lesson stages)."""
    return tuple(f.name for f in fields(StageDeps) if isinstance(f.default, _NotInjected) and f.default.group == "late")


def not_injected(deps: StageDeps, names: Optional[Tuple[str, ...]] = None) -> List[str]:
    """The collaborators of ``deps`` (the design-stage ones unless ``names`` is given) still at their fail-closed
    default."""
    return [name for name in (design_stage_fields() if names is None else names)
            if isinstance(getattr(deps, name), _NotInjected)]


@dataclass(kw_only=True)
class StageContext:
    """One work unit's names shared by the design stages (stages 0-3), under their run_generation_workflow names."""

    # ---- inputs: run_generation_workflow's arguments the stages read
    goal: str
    workspace_path: str
    step_callback: Optional[Callable[[str, str], None]] = None
    approval_callback: Optional[Callable[..., Any]] = None
    stream_callback: Optional[Callable[[str, str], None]] = None
    knowledge_risk_confirmed: bool = False
    skill_gap_callback: Optional[Callable[..., Any]] = None
    skill_conflict_callback: Optional[Callable[..., Any]] = None
    web_lookup_callback: Optional[Callable[..., Any]] = None
    web_lookup_query_callback: Optional[Callable[..., Any]] = None
    supplementary_context: str = ""
    reference_context: str = ""
    resolved_knowledge_coordinates: Optional[List[str]] = None
    skill_engine_override: Any = None
    predetermined_plan: Optional[str] = None
    predetermined_design: Optional[str] = None
    predetermined_architect_files: Optional[List[str]] = None
    allowed_write_relpaths: Optional[List[str]] = None
    write_scope_mode: Any = None
    structured_plan: Any = None
    current_subtask_id: Optional[str] = None
    milestone_group_id: Optional[str] = None
    milestone_index: Optional[int] = None
    milestone_total: Optional[int] = None
    enforce_run_id: Optional[str] = None
    # ---- inputs: what the engine opened before stage 0
    state: RunState = None
    trace_id: str = ""
    start_time: float = 0.0
    run_id: Optional[str] = None
    resume_state: Optional[Dict[str, Any]] = None
    requirement_set: Any = None
    engineering_route: Any = None          # re-bound by the architecture stage (post-Architect triage)
    control: Any = None                    # re-bound by the architecture stage (post-Architect triage)
    record_requirement_lineage: Optional[Callable[[str, Any], None]] = None
    # set by the runner between analysis and planning (the engine's checkpoint saver, opened at that point)
    save_stage_checkpoint: Optional[Callable[..., None]] = None
    # ---- stage 0 (KnowledgeGuard)
    knowledge_config: Any = None
    guard: Any = None
    all_gap_report: Any = None
    # ---- stage 1 (analysis, skills, graph RAG, learned reference)
    active_skills: List[str] = field(default_factory=list)
    retrieved_chunks: List[Any] = field(default_factory=list)
    skill_staleness_warnings: List[str] = field(default_factory=list)
    repo_model: Any = None
    repo_context: str = ""
    repo_slug: str = ""
    se: Any = None
    skills_dir: Any = None
    convention_prompt: str = ""
    unresolved_skill_gap_names: List[str] = field(default_factory=list)
    unverified_relevant: List[Any] = field(default_factory=list)
    missing_skill_candidates: List[str] = field(default_factory=list)
    matched_files: List[str] = field(default_factory=list)
    related_files: List[str] = field(default_factory=list)
    graph_rag_context: str = ""
    graph_retrieval_result: Any = None
    retrieval_member_hints: Dict[str, List[str]] = field(default_factory=dict)
    retrieval_symbol_loci: Dict[str, List[Tuple[int, str]]] = field(default_factory=dict)
    verified_grounding: Dict[str, List[str]] = field(default_factory=dict)
    hypothesis_candidates: Dict[str, List[str]] = field(default_factory=dict)
    skills_prompt: str = ""                # extended by the architecture stage (ownership findings, Stage 2B)
    learned_rag_context: str = ""
    # ---- stage 2 (plan)
    plan: Any = None
    plan_prompt: str = ""
    owner_candidates: List[Any] = field(default_factory=list)
    owner_block: str = ""
    ownership_findings: List[Any] = field(default_factory=list)   # settled by the architecture stage
    # ---- stage 3 (architecture, 2A, 2B) and the active-skill snapshot
    design: Any = None
    architect_files: Any = None
    active_skill_rules_snapshot: Dict[str, List[str]] = field(default_factory=dict)
    active_skill_manifest: List[Any] = field(default_factory=list)
    # ---- set by the engine before its Developer loop, read by the E4 review, approval/apply and lesson stages
    worktree_path: str = ""
    resolved_obligation_ledger: Any = None
    grounding_goal: str = ""
    work_unit: Any = None


__all__ = ["RunState", "StageCollaboratorMissing", "StageContext", "StageDeps", "design_stage_fields",
           "late_stage_fields", "not_injected"]
