"""SEC-009 P1: configuration-authority resolution.

Invariant this module enforces: repository content may configure safe
engineering preferences, but it may not grant itself process, plugin,
filesystem, network, policy, containment, or other Kriya control-plane
authority merely by being auto-discovered or explicitly pointed at via
`--config`. Precedence (which value wins a merge) and authority (whether the
source of a value has the right to set it at all) are separate questions -
this module answers the second one, deliberately kept out of
`kriya/policy/` (ExecutionPolicy reasons about already-resolved runtime
actions; this reasons about where a config VALUE came from, before any
`AppConfig` is ever constructed).

P1 has no approval/trust mechanism yet (that is P2). The only rule here is:

    trusted source (packaged default / platform floor) + any classification
        -> AUTHORIZED
    any other source ("repository-equivalent" - auto-discovered, explicit
    --config inside OR outside the workspace, a runtime_profile preset
    expanded from a repository-controlled value) + REPOSITORY_SAFE
        -> AUTHORIZED
    that same repository-equivalent source + anything else (SECURITY_AUTHORITY,
    PLATFORM_POLICY, USER_AUTHORITY, or a field with no explicit
    classification at all) -> DENIED

Unknown/unclassified fields are denied, not allowed - "no permissive
default" (see _REPOSITORY_SAFE_FIELDS: only fields explicitly listed there
are ever authorized from repository-equivalent provenance).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from kriya.foundation.errors import KriyaError
from kriya.platform.filesystem_semantics import PathRelation, path_relation


class ConfigSource(str, Enum):
    PACKAGED_DEFAULT = "packaged_default"
    AUTO_DISCOVERED_CWD = "auto_discovered_cwd"
    # Left OUT of _TRUSTED_SOURCES deliberately - an editable/dev install's
    # KRIYA_INSTALL_DIR is itself an ordinary git checkout; nothing available
    # to this module establishes it is genuinely operator-controlled and
    # distinct from arbitrary repository content under every supported
    # deployment shape. Evidence was insufficient to grant it
    # SECURITY_AUTHORITY-bypassing trust in P1 - see module docstring in
    # kriya/config/config.py's load_config() and the SEC-009 P1 evidence
    # doc for the full account. Treated exactly like repository provenance
    # here; P2 should revisit with real packaging evidence.
    AUTO_DISCOVERED_INSTALL_DIR = "auto_discovered_install_dir"
    EXPLICIT_CONFIG_PATH_INSIDE_WORKSPACE = "explicit_config_path_inside_workspace"
    # Also NOT trusted in P1 - naming an external file with --config is not
    # itself evidence of operator review of that file's security-relevant
    # fields. No mechanism exists yet to establish "this external path is a
    # trusted operator/org config" (that is what P2's trust manifest is for).
    EXPLICIT_CONFIG_PATH_OUTSIDE_WORKSPACE = "explicit_config_path_outside_workspace"
    # A derived-field marker: fields expanded from a `runtime_profile` preset
    # inherit this source PROVIDED the runtime_profile value itself came from
    # a repository-equivalent source. Authority resolution runs on the fully
    # expanded configuration precisely so this catches the "runtime_profile:
    # legacy" laundering path, not just the raw runtime_profile field.
    RUNTIME_PROFILE_OVERRIDE = "runtime_profile_override"
    PLATFORM_FLOOR = "platform_floor"


# Sources whose values are always authorized, regardless of classification.
_TRUSTED_SOURCES = frozenset({ConfigSource.PACKAGED_DEFAULT, ConfigSource.PLATFORM_FLOOR})


class FieldClassification(str, Enum):
    REPOSITORY_SAFE = "repository_safe"
    USER_AUTHORITY = "user_authority"
    SECURITY_AUTHORITY = "security_authority"
    PLATFORM_POLICY = "platform_policy"


# A field is identified by (top_level_key, leaf_key). top_level_key is None
# for a scalar/list top-level field with no leaf structure of its own
# (runtime_profile, llm_chain). This granularity mirrors load_config()'s own
# "simple deep merge of level-1 dicts" exactly - it is the actual unit a
# repository-sourced value can independently override.
FieldPath = Tuple[Optional[str], str]


# ---------------------------------------------------------------------------
# Explicit REPOSITORY_SAFE allowlist. Deny-by-default: a field not listed
# here is never authorized from repository-equivalent provenance, no matter
# how it would ideally be classified - "no permissive default" per the
# SEC-009 P1 task. Comment inline wherever the reasoning isn't obvious from
# the field name alone.
# ---------------------------------------------------------------------------
_REPOSITORY_SAFE_FIELDS: frozenset = frozenset({
    ("llm", "provider"), ("llm", "model"), ("llm", "api_key"),
    ("llm", "temperature"), ("llm", "max_tokens"), ("llm", "planner_max_tokens"),
    ("llm", "reasoning"), ("llm", "context_window"),
    ("llm", "knowledge_cutoff"), ("llm", "knowledge_cutoff_confidence"),
    ("llm", "retry_temperature"), ("llm", "reviewer_temperature"),
    ("llm", "extra_body"), ("llm", "capabilities"),
    # llm.base_url is SECURITY_AUTHORITY (below) - picking a model/tuning
    # sampling never grants new capability, but redirecting the endpoint does.
    ("autonomy", "generation_time_budget_seconds"),
    ("autonomy", "generation_gate_reserve_seconds"),
    ("autonomy", "generation_seconds_per_file_estimate"),
    ("autonomy", "spec_compliance_enabled"),
    ("autonomy", "max_consecutive_no_progress_attempts"),
    ("autonomy", "auto_index_missing_dependency_graph"),
    # web_lookup_enabled alone sends nothing unattended: search.base_url is
    # SECURITY_AUTHORITY and every query is confirmed by a person unless
    # web_lookup_auto_approve (value-gated below, S3) is authorized.
    ("autonomy", "web_lookup_enabled"),
    # S3 (OI-99): self_correction_loop_enabled and web_lookup_auto_approve are
    # NOT here - see _VALUE_GATED_FIELDS. The turn bound stays: it only
    # matters once enabling the loop has been authorized.
    ("autonomy", "self_correction_loop_max_turns"),
    # DEV-INV-001 (2026-09-19): strictly read-only (unlike the self-correction
    # loop, which edits and compiles - S3 corrected the old "same risk shape"
    # claim): controls only whether/how many bounded, already-governed read-only
    # lookups the Developer may request; every lookup still passes through
    # AuthorizedFileReader's own real containment/sensitive-path enforcement
    # regardless of this flag's value, and grants no new execution/network/
    # filesystem/policy authority of its own).
    ("autonomy", "developer_investigation_enabled"), ("autonomy", "developer_investigation_max_turns"),
    ("autonomy", "best_of_n_first_attempt"),
    # S3b (OI-132): shell_command_timeout_seconds and run_verification_timeout_seconds are NOT here - see
    # _VALUE_BOUNDED_FIELDS (a repository may lower them, never raise them).
    ("autonomy", "run_verification_enabled"),
    # VAL-001 G1-R3 (2026-09-18): found never classified at all when af23008
    # introduced it - fell through to the fail-closed SECURITY_AUTHORITY
    # default, denying it from ANY non-packaged-default source including an
    # operator's own explicit --config. A closed, three-value enum
    # ("auto"/"required"/"disabled", enforced by AppConfig's own
    # field_validator - not merely documented - so this premise is true in
    # code, not just in this comment) controlling only WHICH ALREADY-
    # EXISTING repository test(s) get compared PRE/POST and how a failure
    # is attributed (pre-existing vs. new) - never new execution/network/
    # filesystem/policy authority, exactly the same risk shape as
    # run_verification_enabled/spec_compliance_enabled immediately above.
    # Deliberately NOT extended to brownfield_baseline_target_test (the
    # sibling field) - that one carries an attacker-controlled arbitrary
    # path string (or list of them) that becomes literal subprocess argv
    # content, resolved relative to workspace CWD with no path-containment
    # check, and pytest genuinely IMPORTS whatever it collects - a real,
    # if narrow, traversal-to-arbitrary-import risk this closed-enum field
    # does not share. See docs/assurance/VAL_001_GRAPHIFY_G1.md's own G1-R3
    # section for the full writeup - that field stays unclassified
    # (fail-closed, approval-gated per workspace) on purpose.
    ("autonomy", "brownfield_full_regression_baseline_policy"),
    # S3b (OI-132): sandbox_* / acquisition_* resource bounds are value-bounded, see _VALUE_BOUNDED_FIELDS.
    ("skills", "load_global"), ("skills", "load_cwd"),
    ("logging", "level"),
    # Turning log files off removes a capability; logging.directory is
    # value-sensitive - see _SECURITY_AUTHORITY_FIELDS below. (The retired
    # per-file path setting is rejected by config.py before classification.)
    ("logging", "file_enabled"), ("logging", "run_file_enabled"),
    ("embedding", "model"),
    ("routing", "enabled"), ("routing", "embed_model"),
    ("routing", "reject_threshold"), ("routing", "ask_margin"),
    ("knowledge", "training_cutoff"), ("knowledge", "check_enabled"),
    ("knowledge", "offline_mode"), ("knowledge", "release_cache_ttl_days"),
    ("search", "top_k"), ("search", "public_terms"),
    ("engineering_triage", "enabled"),
    # process_profiles fields only ever ADD approval/review requirements
    # (verified against kriya/workflow/workflow.py:2517-2523 - enforce_approval
    # is OR'd into need_human_approval, never subtracted from it); a
    # repository setting these can make Kriya more cautious, never less.
    ("process_profiles", "enabled"), ("process_profiles", "enforce_approval"),
    ("process_profiles", "enforce_context_depth"), ("process_profiles", "enforce_verification_depth"),
    # paths.{skills,memory,state} are NOT listed here even though they can be
    # safe - their classification depends on a resolved value (in-workspace
    # vs. escaping), not the field name alone, so config.py always supplies
    # an explicit classification_overrides entry for them via
    # path_field_classification(). Deliberately absent from this static
    # table so that if that override were ever skipped by a bug, the static
    # fallback (_SECURITY_AUTHORITY_FIELDS below) fails CLOSED, not open.
})

# Kept only for accurate, honest ConfigAuthorityError labeling - the P1 deny
# decision is identical whether a field is SECURITY_AUTHORITY, PLATFORM_POLICY
# or simply absent from every table below (all three are denied for
# repository-equivalent provenance). Do not add to _REPOSITORY_SAFE_FIELDS
# based on this list; it exists for clearer error messages only.
_PLATFORM_POLICY_FIELDS: frozenset = frozenset({
    (None, "runtime_profile"),
    ("execution_policy", "mode"),
})

_SECURITY_AUTHORITY_FIELDS: frozenset = frozenset({
    # Direct execution
    ("plugins", "directory"), ("plugins", "enabled"),
    # Security boundary
    ("execution_policy", "enabled"),
    ("autonomy", "containment_backend"), ("autonomy", "contained_execution_required"),
    # OD-1 (BACKEND-FINAL-CLOSURE-005): what a contained build sees as .git is
    # a security boundary; a repository can neither widen nor silently disable it.
    ("autonomy", "git_metadata_export"),
    # TOOL-003 P2 (2026-09-13): the MCP analogue immediately above - a
    # repository must never be able to disable MCP containment (nor
    # silently enable it in a way the operator did not choose) without
    # explicit SEC-009 approval, same as contained_execution_required.
    ("autonomy", "mcp_contained_execution_required"),
    # CORR-018 (2026-09-13): the semantic-region-authority analogue of the
    # two containment flags above - a repository must never be able to
    # silently disable an operator's own strict semantic-mutation
    # enforcement.
    ("autonomy", "semantic_region_enforcement_required"),
    ("autonomy", "sandbox_execution"),
    # PRD-012: "unrestricted" grants arbitrary shell commands network access.
    ("autonomy", "shell_network"),
    # FILE-INTEGRITY-CONTRACT-001: which Developer response protocol the model
    # is asked for; it changes the qualified inference behaviour, so only the
    # operator chooses it.
    ("autonomy", "developer_response_protocol"),
    # PRD-020: "record" lets an unverified/unknown original requirement pass.
    ("autonomy", "requirement_unknown_policy"), ("autonomy", "requirement_unverified_policy"),
    # PRD-023: "human" opens a human authorization path for contract changes.
    ("autonomy", "contract_change_escalation"),
    # widens what ambient host env a repo's OWN sandboxed build/test command sees
    ("autonomy", "sandbox_env_allowlist"),
    ("autonomy", "egress_policy"), ("autonomy", "acquisition_registry_hosts"),
    ("autonomy", "risk_threshold_lines"),
    # "human-in-the-loop" -> a more permissive mode removes the approval gate
    ("autonomy", "mode"),
    # S3 (OI-99): enabling values only (see _VALUE_GATED_FIELDS; the restricting
    # value is REPOSITORY_SAFE through value_gated_field_classification()).
    ("autonomy", "self_correction_loop_enabled"), ("autonomy", "web_lookup_auto_approve"),
    ("llm", "base_url"), ("embedding", "base_url"), ("search", "base_url"),
    # INF-001: which runtime adapter serves the model - which native identity
    # endpoints are probed and whether a context window is sent.
    ("llm", "inference_runtime"),
    # PROVIDER-CONTRACT-001: transport timeouts are resource bounds.
    ("llm", "transport"),
    # PRD-016: which larger context windows a request may be sent with, and
    # the hard context/output ceilings - resource authority over the host.
    ("llm", "context_policy"),
    # PRD-018: which roles must run on a runtime distinct from the
    # Developer's - a repository must not be able to relax the operator's.
    ("model_policy", "independent_roles"),
    # PRD-019: which runtime each role is routed to, and the evidence tables.
    ("model_policy", "routing"),
    (None, "llm_chain"),  # each entry carries its own base_url
    ("workflow_controller", "enabled"), ("workflow_controller", "mode"),
    ("engineering_triage", "shadow_mode"),
    # paths escaping the workspace (see containment check note above)
    ("paths", "skills"), ("paths", "memory"), ("paths", "state"),
    # logging.directory (PRD-010 logging closure): any configured directory
    # is a filesystem write target; config.py overrides only an explicit
    # null to REPOSITORY_SAFE.
    ("logging", "directory"),
    # SEC-004 (2026-09-13): every mcp_lifecycle.* bound is security-
    # relevant - a repository weakening a timeout/output-limit/resource
    # ceiling reopens exactly the hang/leak/resource-exhaustion classes
    # SEC-004 exists to close. Only the packaged default (PACKAGED_DEFAULT,
    # trusted) may set these; no runtime value-based override exists here
    # (unlike paths.*/logging.directory) - EVERY repository-equivalent source
    # is denied outright, regardless of the value, since there is no
    # "safe" direction to weaken any of these bounds toward.
    ("mcp_lifecycle", "startup_timeout_seconds"),
    ("mcp_lifecycle", "request_timeout_seconds"),
    ("mcp_lifecycle", "shutdown_grace_seconds"),
    ("mcp_lifecycle", "force_kill_reap_seconds"),
    ("mcp_lifecycle", "max_stdout_line_bytes"),
    ("mcp_lifecycle", "max_stderr_buffer_bytes"),
    ("mcp_lifecycle", "cpu_seconds"),
    ("mcp_lifecycle", "memory_mb"),
    # TOOL-003 P2 (2026-09-13): the container-cleanup bound is exactly as
    # security-relevant as every other mcp_lifecycle.* bound above (the
    # same "no safe direction to weaken toward" reasoning) - a repository
    # widening it could mask an orphaned-container leak behind a longer
    # grace window.
    ("mcp_lifecycle", "container_cleanup_timeout_seconds"),
    # S3b (OI-132): the containment resource bounds of the sandbox a repository's own code runs in - the
    # mcp_lifecycle precedent above. Static class = SECURITY_AUTHORITY (a skipped value override fails CLOSED); a
    # positive value at most the packaged ceiling is REPOSITORY_SAFE through value_gated_field_classification().
    ("autonomy", "sandbox_cpu_seconds"), ("autonomy", "sandbox_memory_mb"),
    ("autonomy", "acquisition_cpu_seconds"), ("autonomy", "acquisition_memory_mb"),
    ("autonomy", "shell_command_timeout_seconds"), ("autonomy", "run_verification_timeout_seconds"),
})

# NOTE: there is currently no AppConfig field for an "LSP executable path" -
# kriya/workflow/lsp_integration.py::find_jdtls() locates jdtls via its own
# discovery logic (kriya/tools/lsp.py), never from a kriya.yaml-settable
# field. The "LSP executable configuration" P1 requirement therefore has no
# corresponding attack surface to classify today; recorded here rather than
# fabricating a field, and called out explicitly in the P1 evidence doc.

# mcp.<any-server-name>.* is handled specially in classify_field() below
# (dynamic keys - can't be enumerated by name), always SECURITY_AUTHORITY.
# So is static_analysis.* (PRD-031A): the gate, its policy, rule packs,
# waiver store and provider settings (dynamic provider keys) - a repository
# can neither disable nor soften it.
# So is model_qualification.* (QUAL-CONFIG-001): it decides whether a model
# may run in production, so a repository can never change it.
# So is evidence.* (LR-R1-M1): a repository must never turn off, reduce or
# widen the audit trail of what is done to it.
_BLANKET_SECURITY_TOP_KEYS = ("mcp", "static_analysis", "model_qualification", "evidence")


def classify_field(top_key: Optional[str], leaf_key: str) -> FieldClassification:
    """Classify a single (top_key, leaf_key) field. Unknown/unclassified
    fields fail closed as SECURITY_AUTHORITY - never REPOSITORY_SAFE by
    omission ("no permissive default")."""
    if top_key in _BLANKET_SECURITY_TOP_KEYS:
        return FieldClassification.SECURITY_AUTHORITY
    key = (top_key, leaf_key)
    if key in _REPOSITORY_SAFE_FIELDS:
        return FieldClassification.REPOSITORY_SAFE
    if key in _PLATFORM_POLICY_FIELDS:
        return FieldClassification.PLATFORM_POLICY
    if key in _SECURITY_AUTHORITY_FIELDS:
        return FieldClassification.SECURITY_AUTHORITY
    return FieldClassification.SECURITY_AUTHORITY


def is_known_field(top_key: Optional[str], leaf_key: str) -> bool:
    """True only if this field has an explicit entry in one of the
    classification tables (or is a dynamic mcp.<name> entry). Used purely to
    distinguish a genuinely unclassified/future field in error reporting -
    the deny decision itself does not depend on this."""
    if top_key in _BLANKET_SECURITY_TOP_KEYS:
        return True
    key = (top_key, leaf_key)
    return key in _REPOSITORY_SAFE_FIELDS or key in _PLATFORM_POLICY_FIELDS or key in _SECURITY_AUTHORITY_FIELDS


def path_field_classification(leaf_key: str, resolved_value: str, container_root: str) -> FieldClassification:
    """paths.{skills,memory,state} - REPOSITORY_SAFE only if the value's real
    target stays inside `container_root`; SECURITY_AUTHORITY (escape)
    otherwise. `container_root` is the directory the SETTING config file
    itself lives in (config_dir), not necessarily the process's CWD - for an
    auto-discovered kriya.yaml these are the same directory by construction,
    but for an explicit --config living elsewhere, its own relative paths
    (the existing, pre-SEC-009 convention) are already resolved against its
    own directory, and containment must be checked against that same
    anchor - checking against CWD instead would deny an ordinary relative
    `paths.skills: ./skills` in a config file that simply doesn't live in
    the process's CWD, which is not an authority escape, just an operator's
    config living somewhere else. Never applied to plugins.directory - that
    field is unconditionally SECURITY_AUTHORITY regardless of containment,
    because the malicious payload it would load is typically INSIDE the
    same repository/workspace that is under attack."""
    # PLAT-PATH-CONTAINMENT-001: filesystem identity, never a lexical prefix (case/normalization aliases).
    if path_relation(container_root, resolved_value) is PathRelation.WITHIN:
        return FieldClassification.REPOSITORY_SAFE
    return FieldClassification.SECURITY_AUTHORITY


# S3 (OI-99, OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001): fields that ENABLE an
# authority-sensitive behaviour, mapped to their one restricting value. A
# repository may set the restricting value (it grants nothing - `kriya init`
# writes it); any other value is SECURITY_AUTHORITY and needs the human
# authority path (P2 approval / trust file).
#   self_correction_loop_enabled: a model edit loop whose validator compiles
#     model-written files (the controller's owner-recovery path runs it in the
#     plan worktree, outside the per-attempt source review);
#   web_lookup_auto_approve: removes the per-query human confirmation of an
#     outbound live-lookup search.
_VALUE_GATED_FIELDS: Dict[FieldPath, Any] = {
    ("autonomy", "self_correction_loop_enabled"): False,
    ("autonomy", "web_lookup_auto_approve"): False,
}


# S3b (OI-132): containment resource bounds of the sandbox a repository's own code runs in, mapped to their packaged
# ceiling (the AutonomyConfig default; default_config.yaml does not set them - both pinned by
# tests/test_s3b_resource_bound_value_gating.py). A repository may TIGHTEN a bound (a positive int at most the ceiling
# grants nothing); a raise needs the human authority path, like every mcp_lifecycle.* bound. Exactly a positive int:
# -1 is RLIM_INFINITY to setrlimit on Linux (a raise disguised as a lower number), 0 is not a bound, and a bool, string
# or float (which pydantic would coerce) is not judged - all fail closed to SECURITY_AUTHORITY.
#   sandbox_cpu_seconds / sandbox_memory_mb: target / generated code execution (RLIMIT_CPU, RLIMIT_AS or JVM bounds,
#     OCI --memory); acquisition_*: dependency acquisition; shell_command_timeout_seconds: ShellTool wall clock;
#     run_verification_timeout_seconds: the Runtime Verification Gate's wall clock (sibling audit: the same class as
#     the shell timeout - the OCI backend honours cpu_seconds only as a rate cap, so the wall clock IS its time bound).
_VALUE_BOUNDED_FIELDS: Dict[FieldPath, int] = {
    ("autonomy", "sandbox_cpu_seconds"): 240,
    ("autonomy", "sandbox_memory_mb"): 4096,
    ("autonomy", "acquisition_cpu_seconds"): 300,
    ("autonomy", "acquisition_memory_mb"): 2048,
    ("autonomy", "shell_command_timeout_seconds"): 300,
    ("autonomy", "run_verification_timeout_seconds"): 90,
}


def value_gated_field_classification(top_key: Optional[str], leaf_key: str, value: Any) -> Optional[FieldClassification]:
    """REPOSITORY_SAFE for a value-gated field set to exactly its restricting value (the bool ``False``, not a string
    or a number that would coerce to it) and for a value-bounded field set to a positive int at most its packaged
    ceiling (S3b), SECURITY_AUTHORITY for any other value, None for a field that is neither (its static classification
    applies)."""
    key = (top_key, leaf_key)
    if key in _VALUE_BOUNDED_FIELDS:
        if type(value) is int and 1 <= value <= _VALUE_BOUNDED_FIELDS[key]:
            return FieldClassification.REPOSITORY_SAFE
        return FieldClassification.SECURITY_AUTHORITY
    if key not in _VALUE_GATED_FIELDS:
        return None
    restricting = _VALUE_GATED_FIELDS[key]
    if value is restricting:
        return FieldClassification.REPOSITORY_SAFE
    return FieldClassification.SECURITY_AUTHORITY


# S16a (OI-130, AUTONOMY-MODE-FAIL-OPEN): autonomy.mode is a CLOSED vocabulary, owned here. Derived from every production
# comparison site (they all compare against "human-in-the-loop"; every other mode takes their else branch), the schema
# default, the packaged default, `kriya init`, KUP/GUI (display only) and the documentation ("guardrails": approval only
# on a sensitive path, the risk threshold or a process profile). No other value is a mode: before S16a any other string
# was accepted and silently behaved as guardrails, so a typo of human-in-the-loop turned every human-in-the-loop guarantee
# off. One parser (parse_autonomy_mode) is called at every ingress - configuration resolution (every YAML layer, before
# classification, so nothing invalid can be approved), AutonomyConfig construction, attribute assignment and model_copy
# updates. No normalization: case or whitespace variants are refused, never repaired.
AUTONOMY_MODE_HUMAN_IN_THE_LOOP = "human-in-the-loop"
AUTONOMY_MODE_GUARDRAILS = "guardrails"
AUTONOMY_MODES: Tuple[str, ...] = (AUTONOMY_MODE_GUARDRAILS, AUTONOMY_MODE_HUMAN_IN_THE_LOOP)
AUTONOMY_MODE_DEFAULT = AUTONOMY_MODE_HUMAN_IN_THE_LOOP  # an absent key; the schema and the packaged default carry it
AUTONOMY_MODE_FIELD = "autonomy.mode"

AUTONOMY_MODE_NULL = "AUTONOMY_MODE_NULL"
AUTONOMY_MODE_NOT_A_STRING = "AUTONOMY_MODE_NOT_A_STRING"
AUTONOMY_MODE_EMPTY = "AUTONOMY_MODE_EMPTY"
AUTONOMY_MODE_NOT_CANONICAL = "AUTONOMY_MODE_NOT_CANONICAL"
AUTONOMY_MODE_UNKNOWN = "AUTONOMY_MODE_UNKNOWN"
# The closed reason-code table (a code outside it is refused at construction). The texts never contain the value.
AUTONOMY_MODE_REASONS: Dict[str, str] = {
    AUTONOMY_MODE_NULL: "is null (remove the key for the default {default!r})",
    AUTONOMY_MODE_NOT_A_STRING: "is a {kind}, not a string",
    AUTONOMY_MODE_EMPTY: "is empty",
    AUTONOMY_MODE_NOT_CANONICAL: "differs from {canonical!r} only in letter case or surrounding whitespace; write it exactly",
    AUTONOMY_MODE_UNKNOWN: "is not a Kriya autonomy mode",
}


class AutonomyModeError(KriyaError):
    """A malformed ``autonomy.mode``: a typed configuration error raised before any authority comparison, workflow or tool
    execution. Deliberately NOT a ValueError, so pydantic propagates it unchanged from a validator. Never echoes the value."""

    def __init__(self, reason_code: str, *, kind: Optional[str] = None, canonical: Optional[str] = None) -> None:
        if reason_code not in AUTONOMY_MODE_REASONS:
            raise ValueError(f"unknown autonomy.mode reason code {reason_code!r}")
        detail = AUTONOMY_MODE_REASONS[reason_code].format(kind=kind, canonical=canonical, default=AUTONOMY_MODE_DEFAULT)
        allowed = ", ".join(repr(mode) for mode in AUTONOMY_MODES)
        super().__init__(reason_code, f"{reason_code}: {AUTONOMY_MODE_FIELD} {detail}. Allowed values (exact, case-sensitive): {allowed}.")
        self.field = AUTONOMY_MODE_FIELD
        self.allowed = AUTONOMY_MODES


def parse_autonomy_mode(value: Any) -> str:
    """The one validator of ``autonomy.mode``: the value itself when it is exactly one of AUTONOMY_MODES, else
    AutonomyModeError. A str subclass, a bool or any other type is not a mode; nothing is stripped or lower-cased."""
    if value is None:
        raise AutonomyModeError(AUTONOMY_MODE_NULL)
    if type(value) is not str:
        raise AutonomyModeError(AUTONOMY_MODE_NOT_A_STRING, kind=type(value).__name__)
    if value in AUTONOMY_MODES:
        return value
    stripped = value.strip()
    if not stripped:
        raise AutonomyModeError(AUTONOMY_MODE_EMPTY)
    for mode in AUTONOMY_MODES:
        if stripped.casefold() == mode:
            raise AutonomyModeError(AUTONOMY_MODE_NOT_CANONICAL, canonical=mode)
    raise AutonomyModeError(AUTONOMY_MODE_UNKNOWN)


EGRESS_POLICIES = ("local_only", "unrestricted")


class EgressPolicyError(ValueError):
    """A malformed ``autonomy.egress_policy``. A ValueError (pydantic turns it into a ValidationError inside a validator, and the assignment / model_copy paths keep raising a
    ValueError), named so the `kriya authority` CLI can report it as the operator error it is instead of a traceback."""


def parse_egress_policy(value: Any) -> str:
    """The ONE check of ``autonomy.egress_policy`` (the AutonomyConfig validator, assignment and model_copy AND the authority resolution all call it): exactly a member of
    EGRESS_POLICIES. Every consumer compares against the literal "local_only", so any other value is read as unrestricted."""
    if type(value) is not str or value not in EGRESS_POLICIES:
        raise EgressPolicyError(f"autonomy.egress_policy must be 'local_only' or 'unrestricted', got {value!r}")
    return value


def autonomy_mode_of(config: Any) -> str:
    """OI-135 (S18): the ONE fail-closed reader every autonomy-mode consumer calls. Re-checks the value at read time with
    parse_autonomy_mode, so an instance that bypassed validation (pydantic ``model_construct``, ``object.__setattr__``)
    is a typed AutonomyModeError, never a permissive reading. A configuration with no readable mode is AUTONOMY_MODE_NULL."""
    return parse_autonomy_mode(getattr(getattr(config, "autonomy", None), "mode", None))


def is_human_in_the_loop(mode: Any) -> bool:
    """OI-135 (S18): whether ``mode`` (a value read from a configuration) is human-in-the-loop - after re-checking it with
    parse_autonomy_mode, so anything outside the closed vocabulary raises AutonomyModeError instead of silently taking a
    consumer's non-human-in-the-loop (permissive) branch. Every consumer compares through this, never against the literal."""
    return parse_autonomy_mode(mode) == AUTONOMY_MODE_HUMAN_IN_THE_LOOP


def agent_role_field_classification(role_value: object) -> FieldClassification:
    """agent_llms.<role> (planner/architect/reviewer/run_verifier/skill_gap/
    spec_compliance) is one atomic merge unit (AgentModelConfig) - the merge
    never goes deeper than this leaf, so provenance can't distinguish
    "just picked a model name" from "also redirected the network
    destination" by field path alone. REPOSITORY_SAFE unless the role value
    sets a base_url anywhere within it (its own `llm.base_url`, or any
    `llm_chain` entry's base_url) - that is the same SECURITY_AUTHORITY
    concern as the top-level llm.base_url, just nested one level deeper."""
    if not isinstance(role_value, dict):
        return FieldClassification.SECURITY_AUTHORITY
    llm_val = role_value.get("llm")
    authority_keys = ("base_url", "inference_runtime")
    if isinstance(llm_val, dict) and any(key in llm_val for key in authority_keys):
        return FieldClassification.SECURITY_AUTHORITY
    for chain_entry in role_value.get("llm_chain") or []:
        if isinstance(chain_entry, dict) and any(key in chain_entry for key in authority_keys):
            return FieldClassification.SECURITY_AUTHORITY
    return FieldClassification.REPOSITORY_SAFE


def explicit_config_source(resolved_config_path: str, workspace_root: str) -> ConfigSource:
    """Classify an explicit --config path's provenance using realpath
    (resolves symlinks - a plain abspath comparison would not), so a symlink
    inside the workspace pointing at a target outside it is correctly
    treated as an escape, and a symlink whose real target stays inside the
    workspace is correctly treated as in-workspace."""
    if path_relation(workspace_root, resolved_config_path) is PathRelation.WITHIN:
        return ConfigSource.EXPLICIT_CONFIG_PATH_INSIDE_WORKSPACE
    return ConfigSource.EXPLICIT_CONFIG_PATH_OUTSIDE_WORKSPACE


@dataclass(frozen=True)
class ConfigAuthorityViolation:
    field_path: str
    source: ConfigSource
    classification: FieldClassification
    reason: str


class ConfigAuthorityError(ValueError):
    """Raised by load_config() when a repository-equivalent source attempts
    to set a field that is not REPOSITORY_SAFE and no valid SEC-009 P2
    approval covers it. Never includes the actual configured value - only
    field path, provenance, classification, and a fixed, non-value-bearing
    reason string (no secret values in the error)."""

    def __init__(self, violations: List[ConfigAuthorityViolation]) -> None:
        self.violations = violations
        lines = [
            "Configuration-authority denied (SEC-009) - repository-controlled configuration "
            "cannot grant itself Kriya control-plane authority without explicit approval. "
            f"{len(violations)} field(s) rejected:",
        ]
        for v in sorted(violations, key=lambda x: x.field_path):
            lines.append(
                f"  - {v.field_path}: source={v.source.value}, "
                f"classification={v.classification.value} - {v.reason}"
            )
        lines.append(
            "Run 'kriya authority inspect' to see pending security-authority fields, or "
            "'kriya authority approve' to grant explicit, digest-bound approval."
        )
        super().__init__("\n".join(lines))


def compute_violations(
    provenance: Dict[FieldPath, ConfigSource],
    classification_overrides: Optional[Dict[FieldPath, FieldClassification]] = None,
) -> List[ConfigAuthorityViolation]:
    """Pure - walks every tracked field's provenance and returns every
    repository-equivalent-sourced, non-REPOSITORY_SAFE field as a
    ConfigAuthorityViolation. Never raises; this is P1's classification
    logic exactly as it was (still the sole authority for WHAT counts as a
    violation) - SEC-009 P2 adds an approval-covered check on TOP of this
    list (kriya/config/authority_approval.py), it never changes what this
    function considers a violation in the first place.

    `classification_overrides` lets the caller supply a runtime-resolved
    classification for fields whose safety depends on a resolved value, not
    just the field name - specifically paths.{skills,memory,state} and
    agent_llms.<role>, whose classification depends on a resolved value
    (see path_field_classification()/agent_role_field_classification()).
    Static fields are classified via classify_field() as usual.
    """
    overrides = classification_overrides or {}
    violations: List[ConfigAuthorityViolation] = []
    for (top_key, leaf_key), source in sorted(provenance.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
        if source in _TRUSTED_SOURCES:
            continue
        field_key = (top_key, leaf_key)
        classification = overrides.get(field_key) or classify_field(top_key, leaf_key)
        if classification == FieldClassification.REPOSITORY_SAFE:
            continue
        field_path = leaf_key if top_key is None else f"{top_key}.{leaf_key}"
        if not is_known_field(top_key, leaf_key):
            reason = (
                "field has no explicit REPOSITORY_SAFE classification (unknown/future field) - "
                "fails closed by default, no permissive default"
            )
        else:
            reason = (
                f"classification {classification.value} requires a trusted or explicitly "
                "authorized source - see 'kriya authority inspect'/'kriya authority approve'"
            )
        violations.append(ConfigAuthorityViolation(field_path, source, classification, reason))
    return violations


def resolve_authority(
    provenance: Dict[FieldPath, ConfigSource],
    classification_overrides: Optional[Dict[FieldPath, FieldClassification]] = None,
) -> None:
    """P1 behavior, preserved exactly: raises ConfigAuthorityError if
    compute_violations() finds anything, with no approval mechanism
    consulted at all. Not called by load_config() directly anymore (P2
    inserts an approval-coverage check between computing violations and
    raising) - kept as a thin wrapper so any caller wanting P1's original,
    approval-blind all-or-nothing behavior still has it available."""
    violations = compute_violations(provenance, classification_overrides)
    if violations:
        raise ConfigAuthorityError(violations)


def get_field_value(config_dict: Dict[str, Any], top_key: Optional[str], leaf_key: str) -> Any:
    """The effective value load_config() resolved for a given field path,
    read from the fully merged+expanded config_dict - used by SEC-009 P2 to
    build an approval artifact's security subset. Never used for the P1
    error message (which never includes values)."""
    if top_key is None:
        return config_dict.get(leaf_key)
    section = config_dict.get(top_key)
    if isinstance(section, dict):
        return section.get(leaf_key)
    return None
