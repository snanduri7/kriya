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

import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


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
    ("autonomy", "web_lookup_enabled"), ("autonomy", "web_lookup_auto_approve"),
    ("autonomy", "self_correction_loop_enabled"), ("autonomy", "self_correction_loop_max_turns"),
    ("autonomy", "best_of_n_first_attempt"),
    ("autonomy", "shell_command_timeout_seconds"),
    ("autonomy", "run_verification_enabled"), ("autonomy", "run_verification_timeout_seconds"),
    ("autonomy", "sandbox_cpu_seconds"), ("autonomy", "sandbox_memory_mb"),
    ("autonomy", "acquisition_cpu_seconds"), ("autonomy", "acquisition_memory_mb"),
    ("skills", "load_global"), ("skills", "load_cwd"),
    ("logging", "level"), ("logging", "file"),
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
    # paths.{skills,memory,logs} are NOT listed here even though they can be
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
    ("autonomy", "sandbox_execution"),
    # widens what ambient host env a repo's OWN sandboxed build/test command sees
    ("autonomy", "sandbox_env_allowlist"),
    ("autonomy", "egress_policy"), ("autonomy", "acquisition_registry_hosts"),
    ("autonomy", "risk_threshold_lines"),
    # "human-in-the-loop" -> a more permissive mode removes the approval gate
    ("autonomy", "mode"),
    ("llm", "base_url"), ("embedding", "base_url"), ("search", "base_url"),
    (None, "llm_chain"),  # each entry carries its own base_url
    ("workflow_controller", "enabled"), ("workflow_controller", "mode"),
    ("engineering_triage", "shadow_mode"),
    # paths escaping the workspace (see containment check note above)
    ("paths", "skills"), ("paths", "memory"), ("paths", "logs"),
})

# NOTE: there is currently no AppConfig field for an "LSP executable path" -
# kriya/workflow/lsp_integration.py::find_jdtls() locates jdtls via its own
# discovery logic (kriya/tools/lsp.py), never from a kriya.yaml-settable
# field. The "LSP executable configuration" P1 requirement therefore has no
# corresponding attack surface to classify today; recorded here rather than
# fabricating a field, and called out explicitly in the P1 evidence doc.

# mcp.<any-server-name>.* is handled specially in classify_field() below
# (dynamic keys - can't be enumerated by name), always SECURITY_AUTHORITY.


def classify_field(top_key: Optional[str], leaf_key: str) -> FieldClassification:
    """Classify a single (top_key, leaf_key) field. Unknown/unclassified
    fields fail closed as SECURITY_AUTHORITY - never REPOSITORY_SAFE by
    omission ("no permissive default")."""
    if top_key == "mcp":
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
    if top_key == "mcp":
        return True
    key = (top_key, leaf_key)
    return key in _REPOSITORY_SAFE_FIELDS or key in _PLATFORM_POLICY_FIELDS or key in _SECURITY_AUTHORITY_FIELDS


def path_field_classification(leaf_key: str, resolved_value: str, container_root: str) -> FieldClassification:
    """paths.{skills,memory,logs} - REPOSITORY_SAFE only if the value's real
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
    real_value = os.path.realpath(resolved_value)
    real_root = os.path.realpath(container_root)
    if real_value == real_root or real_value.startswith(real_root + os.sep):
        return FieldClassification.REPOSITORY_SAFE
    return FieldClassification.SECURITY_AUTHORITY


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
    if isinstance(llm_val, dict) and "base_url" in llm_val:
        return FieldClassification.SECURITY_AUTHORITY
    for chain_entry in role_value.get("llm_chain") or []:
        if isinstance(chain_entry, dict) and "base_url" in chain_entry:
            return FieldClassification.SECURITY_AUTHORITY
    return FieldClassification.REPOSITORY_SAFE


def explicit_config_source(resolved_config_path: str, workspace_root: str) -> ConfigSource:
    """Classify an explicit --config path's provenance using realpath
    (resolves symlinks - a plain abspath comparison would not), so a symlink
    inside the workspace pointing at a target outside it is correctly
    treated as an escape, and a symlink whose real target stays inside the
    workspace is correctly treated as in-workspace."""
    real_path = os.path.realpath(resolved_config_path)
    real_workspace = os.path.realpath(workspace_root)
    if real_path == real_workspace or real_path.startswith(real_workspace + os.sep):
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
    to set a field that is not REPOSITORY_SAFE. Never includes the actual
    configured value - only field path, provenance, classification, and a
    fixed, non-value-bearing reason string, per SEC-009 P1's denial-semantics
    requirement (no secret values in the error)."""

    def __init__(self, violations: List[ConfigAuthorityViolation]) -> None:
        self.violations = violations
        lines = [
            "Configuration-authority denied - repository-controlled configuration "
            "cannot grant itself Kriya control-plane authority (SEC-009 P1). "
            f"{len(violations)} field(s) rejected:",
        ]
        for v in sorted(violations, key=lambda x: x.field_path):
            lines.append(
                f"  - {v.field_path}: source={v.source.value}, "
                f"classification={v.classification.value} - {v.reason}"
            )
        super().__init__("\n".join(lines))


def resolve_authority(
    provenance: Dict[FieldPath, ConfigSource],
    classification_overrides: Optional[Dict[FieldPath, FieldClassification]] = None,
) -> None:
    """Walk every tracked field's provenance and raise ConfigAuthorityError
    if any repository-equivalent source set a non-REPOSITORY_SAFE field.
    Collects ALL violations before raising (a single deterministic error
    listing everything denied, not just the first hit).

    `classification_overrides` lets the caller supply a runtime-resolved
    classification for fields whose safety depends on a resolved value, not
    just the field name - specifically paths.{skills,memory,logs}, whose
    classification depends on whether the resolved realpath stays inside the
    workspace root (see path_field_classification()). Static fields are
    classified via classify_field() as usual.
    """
    overrides = classification_overrides or {}
    violations: List[ConfigAuthorityViolation] = []
    for (top_key, leaf_key), source in provenance.items():
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
                "authorized source; P1 has no approval mechanism, so repository-equivalent "
                "provenance is always denied for this field"
            )
        violations.append(ConfigAuthorityViolation(field_path, source, classification, reason))
    if violations:
        raise ConfigAuthorityError(violations)
