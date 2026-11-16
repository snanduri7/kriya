"""Configuration RESOLUTION (full modularization D3): source -> provenance -> expansion -> field classification -> violations, split
from the schema (kriya/config/config.py keeps the pydantic models, the SEC-009-checked ``load_config`` that constructs ``AppConfig``,
and re-exports every name below under its old path).

Moved here VERBATIM from kriya/config/config.py: ``ConfigResolutionState`` and ``resolve_config_state`` (the one code path load_config(),
``kriya authority inspect|approve`` and the KUP configuration operations share), the runtime-profile preset table and the production seal
it is checked against (``runtime_profile_preset_fields``, ``production_sealed_value_satisfied``/``_requirement``, the production budget
and fixed guarantees), and the removed-field errors. Three things changed, none observable: the working directory is read through
CONTEXT (``current_directory()``, the same ``os.getcwd()``); the paths.state rule and its error are CONFIG's own (below) instead of a lazy
import from STATE's ``core.state_paths`` (which re-exports them: the same objects); the log-directory rule stays a lazy import of
``core.logging_setup`` (CONFIG; lazy because importing ``kriya.core`` imports the provider). ``load_config`` stays with the schema it constructs: here it would close an import cycle
config.config <-> config.resolution, which tests/architecture/test_cycles.py forbids.
"""
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from kriya.config.environment import (
    KRIYA_DIRECTORY_NAME,
    Environment,
    default_state_dir,
    expand_user,
    kriya_install_dir,
)
from kriya.config.environment import state_dir as environment_state_dir
from kriya.context.working_directory import current_directory
from kriya.context.workspace_identity import (
    authority_root,
    belongs_to_root,
    canonical_workspace,
    reaches_root,
    workspace_identity,
)
from kriya.platform.filesystem_semantics import PathIdentity, PathRelation, path_identity, path_relation

# The logger keeps the name the resolution logged under before D3 (kriya.config.config).
logger = logging.getLogger("kriya.config.config")


# ------------------------------------------------------------------ the paths.state rule (moved from kriya/core/state_paths.py, D3)

class StateDirectoryError(ValueError):
    """The state directory is relative (outside config resolution) or invalid."""


class ShadowedConfigurationError(ValueError):
    """A ``kriya.yaml``/``kriya.yml`` sits between the invoked directory and the repository root (D-001). The repository's configuration is the ROOT's, so
    a run started deeper reads that one; a second file it would no longer read may carry the tighter bound, so it is refused, never dropped silently."""


WORKSPACE_STATE_PARENT = KRIYA_DIRECTORY_NAME


def require_workspace_local_state_under_kriya_dir(resolved: str, workspace_root: str, original: str) -> None:
    """A state directory inside the workspace must be beneath
    ``<workspace>/.kriya/`` (e.g. ``.kriya/state``); ``./state`` or ``./logs``
    would put Kriya state among the repository's own files."""
    workspace = os.path.realpath(workspace_root)
    target = os.path.realpath(resolved)
    if path_relation(workspace, target) is not PathRelation.WITHIN:
        return  # outside the workspace: SEC-009 path authority decides
    kriya_dir = os.path.join(workspace, WORKSPACE_STATE_PARENT)
    if path_relation(kriya_dir, target) is PathRelation.WITHIN and path_identity(kriya_dir, target) is not PathIdentity.SAME:
        return
    raise StateDirectoryError(
        f"paths.state {original!r} resolves inside the workspace ({target}) but not beneath "
        f"{kriya_dir}/; use e.g. '.kriya/state', or a directory outside the workspace."
    )


# ------------------------------------------------------------------ the state directory (moved from kriya/core/state_paths.py, S3)

ENV_STATE_DIR = "KRIYA_STATE_DIR"

STATE_DIR_SOURCE_ENV = "env"
STATE_DIR_SOURCE_CONFIG = "config"
STATE_DIR_SOURCE_DEFAULT = "default"

def default_state_directory(environment: Optional[Environment] = None) -> str:
    """``~/.kriya/state``, computed at call time (tests point HOME elsewhere)."""
    return default_state_dir(environment)


def _absolute(value: str, origin: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateDirectoryError(f"{origin} must be a non-empty directory path.")
    expanded = expand_user(value)
    if not os.path.isabs(expanded):
        raise StateDirectoryError(
            f"{origin} must be an absolute path here (got {value!r}); Kriya never resolves "
            "a state directory against the current working directory."
        )
    return os.path.realpath(expanded)


def ambient_state_directory(environment: Optional[Environment] = None) -> str:
    """The state directory for code with NO ``AppConfig`` in scope (a gate cache, a JVM acceptance staging root, a model-runtime record store):
    ``KRIYA_STATE_DIR`` (absolute, ``~`` expanded; a relative value is refused, never resolved against the working directory) else ``~/.kriya/state``.
    WAVE8 OI-02: this is the ONE place the environment/default location is decided; ``resolve_state_directory`` composes it and no other module reads
    ``KRIYA_STATE_DIR`` or the default location itself (tripwire: tests/test_wave8_state_directory_authority.py). Code that HAS a configuration asks
    ``resolve_state_directory(cfg)``, which also honours ``paths.state``. Raises ``StateDirectoryError``. Pure."""
    env_value = environment_state_dir(environment)
    if env_value:
        return _absolute(env_value, ENV_STATE_DIR)
    return os.path.realpath(default_state_directory(environment))


def state_directory_for(configured: Optional[str], environment: Optional[Environment] = None) -> Tuple[str, str]:
    """(directory, source) for a ``paths.state`` VALUE, precedence env > config > default. Pure. The one composition ``resolve_state_directory``
    and the configuration resolution (which has no ``AppConfig`` yet) share."""
    if environment_state_dir(environment):
        return ambient_state_directory(environment), STATE_DIR_SOURCE_ENV
    if configured is not None:
        # load_config() already made this absolute (anchored at the config
        # file's directory); a directly built AppConfig must do the same.
        return _absolute(configured, "paths.state"), STATE_DIR_SOURCE_CONFIG
    return ambient_state_directory(environment), STATE_DIR_SOURCE_DEFAULT


# ------------------------------------------------------------------ project state namespace (C-05 / R-05, slice S3)
#
# Project-derived mutable or semantic state (the code index: ``dependency_graph.db`` with the structural tables, ``vector_index.db``) belongs to ONE
# project: the authority root the configuration was resolved for (``kriya/context/workspace_identity.py``, AUTH-A5-01). That identity is bound to the
# ``AppConfig`` once, by ``load_config`` (or explicitly, ``bind_project_identity``), and carried by it, so the namespace never changes with the
# directory a unit happens to operate in (direct, enforce, Project Setup, resume, Desktop and CLI all load the configuration from the project's
# directory).
#
# CONFIG owns the POLICY, all of it pure: the identity binding, where the state lives, whether a source directory or a recorded store owner is this
# project's, and the closed refusal table. The store MECHANISM (the SQLite stamp, opening and claiming a store file) is RETRIEVAL's,
# ``kriya/memory/project_store.py``, which asks the verdicts below; CONFIG never imports it and never opens a store.
#
# Location: the packaged default of ``paths.memory`` is ``<state>/projects/<identity>/memory``; an explicit ``paths.memory`` is used as written.
# Either way every store FILE carries the identity it was built for, and a reader is handed a store only when that stamp is this project's. There is
# no fallback to an unkeyed store: absence, a foreign stamp and an unattributable legacy store are all "no index for this project".
#
# Ownership is decided by the canonical repository/root authority (D-001, ``authority_root``), NEVER by filesystem containment: a directory is this
# project's source only when its own authority root IS the project's. An independent repository nested inside the project, a link that resolves into
# another project and a Kriya-managed sandbox (its own authority root) are all not this project's source here; the run that owns a sandbox resolves
# it to the run's workspace through STATE's lease (``control.run_coordinator.candidate_workspace_root``) before it asks.

PROJECT_STATE_PARENT = "projects"
PROJECT_MEMORY_DIRECTORY = "memory"

PROJECT_IDENTITY_MISSING = "PROJECT_IDENTITY_MISSING"
PROJECT_IDENTITY_MISMATCH = "PROJECT_IDENTITY_MISMATCH"
PROJECT_STORE_FOREIGN = "PROJECT_STORE_FOREIGN"
PROJECT_STORE_UNATTRIBUTED = "PROJECT_STORE_UNATTRIBUTED"
PROJECT_STORE_UNREADABLE = "PROJECT_STORE_UNREADABLE"
PROJECT_SOURCE_FOREIGN = "PROJECT_SOURCE_FOREIGN"

# The closed table of project-state refusals; the message is rendered from it only (tripwire: tests/test_s3_project_state_namespace.py).
PROJECT_STATE_REFUSALS = {
    PROJECT_IDENTITY_MISSING: (
        "this configuration carries no project identity, so no project-derived store may be opened with it. A configuration gets its identity "
        "from load_config(), or explicitly from bind_project_identity(cfg, <workspace>); Kriya never selects a default or shared store instead."
    ),
    PROJECT_IDENTITY_MISMATCH: (
        "this configuration is bound to the project at {root!r}, but the run is for the project at {run!r}. A run never uses another project's "
        "configuration identity, and never rebinds one: load the configuration from the run's own project directory. Nothing was read, "
        "written or sent to a model."
    ),
    PROJECT_STORE_FOREIGN: (
        "the store at {path!r} was built for project {actual!r}, not this project ({expected!r}). It is never read or rewritten for another "
        "project; point paths.memory at a directory of this project's own, or remove the setting to use the per-project default."
    ),
    PROJECT_STORE_UNATTRIBUTED: (
        "the store at {path!r} records no project and its location does not establish that it is this project's ({root!r}), so Kriya cannot "
        "tell whose it is. It is ignored, never adopted. Move or delete it and run `kriya analyze` to rebuild this project's index."
    ),
    PROJECT_STORE_UNREADABLE: "the store at {path!r} could not be inspected for its project ({detail}); it is treated as not this project's.",
    PROJECT_SOURCE_FOREIGN: (
        "the directory {source!r} is not part of this project ({root!r}): its repository root is not this project's, so its content is never "
        "written into this project's stores. Run Kriya from inside that directory's own project to index it."
    ),
}


class ProjectStateError(RuntimeError):
    """A project-derived store may not be used for this project (``reason_code`` is one of ``PROJECT_STATE_REFUSALS``)."""

    def __init__(self, reason_code: str, **details: object) -> None:
        self.reason_code = reason_code
        self.details = details
        super().__init__(PROJECT_STATE_REFUSALS[reason_code].format(**details))


def project_state_root(state_directory: str, identity: str) -> str:
    """``<state>/projects/<identity>``: the one directory a project's Kriya-located derived state lives under. Pure."""
    if not isinstance(identity, str) or not identity:
        raise ProjectStateError(PROJECT_IDENTITY_MISSING)
    return os.path.join(state_directory, PROJECT_STATE_PARENT, identity)


# The identity form of a path under a project's state root: ``<kriya-project-state>/<path below <state>/projects/<identity>>``. The default
# ``paths.memory`` resolves to an absolute path that names the state directory and the project's identity (a hash of where the workspace is); the SAME
# configuration for a project somewhere else, or with the state directory somewhere else, is the same semantic configuration ("the per-project
# default"), so a configuration identity (kriya/control/checkpoint.py::canonical_identity_config) must not hash either location - exactly as it does
# not hash the checkout root (``<kriya-install>``). Which project a record belongs to is bound separately, by its workspace ownership stamp.
PROJECT_STATE_IDENTITY_TOKEN = "<kriya-project-state>"
_PROJECT_IDENTITY_COMPONENT = re.compile(r"[0-9a-f]{64}")


def project_state_identity_former(configured_state: Any, environment: Optional[Environment] = None) -> Callable[[Any], Optional[str]]:
    """For the configuration whose ``paths.state`` VALUE is ``configured_state``: a function giving the identity form of a path that lies under
    ``<state>/projects/<identity>/`` (the root itself is the bare token), or None for every other value - a non-path, a relative path, a path
    anywhere else, a state directory that cannot be resolved. Pure."""
    try:
        parent: Optional[str] = os.path.realpath(os.path.join(state_directory_for(configured_state, environment)[0], PROJECT_STATE_PARENT))
    except StateDirectoryError:
        parent = None

    def form(path: Any) -> Optional[str]:
        if parent is None or not isinstance(path, str) or not os.path.isabs(path):
            return None
        candidate = os.path.normpath(path)
        if path_relation(parent, candidate) is not PathRelation.WITHIN:
            return None
        identity, _, below = os.path.relpath(os.path.realpath(candidate), parent).partition(os.sep)
        if not _PROJECT_IDENTITY_COMPONENT.fullmatch(identity):
            return None
        return f"{PROJECT_STATE_IDENTITY_TOKEN}/{below}" if below else PROJECT_STATE_IDENTITY_TOKEN

    return form


def default_project_memory_directory(configured_state: Optional[str], workspace_root: str,
                                     environment: Optional[Environment] = None) -> str:
    """Where an UNCONFIGURED ``paths.memory`` resolves for the project at ``workspace_root`` (an authority root)."""
    state_directory = state_directory_for(configured_state, environment)[0]
    return os.path.join(project_state_root(state_directory, workspace_identity(workspace_root)), PROJECT_MEMORY_DIRECTORY)


PROJECT_SKILLS_DIRECTORY = "skills"


def default_project_skills_directory(configured_state: Optional[str], workspace_root: str,
                                     environment: Optional[Environment] = None) -> str:
    """Where an UNCONFIGURED ``paths.skills`` resolves for the project at ``workspace_root``: the project's skill OVERLAY, the root its runs and
    its ``kriya skills`` commands write. The shipped library (``<install>/skills``) is a second, read-only root, never this one."""
    state_directory = state_directory_for(configured_state, environment)[0]
    return os.path.join(project_state_root(state_directory, workspace_identity(workspace_root)), PROJECT_SKILLS_DIRECTORY)


def is_project_skill_overlay(cfg) -> bool:
    """Whether ``cfg.paths.skills`` is the bound project's own overlay (the per-project default). False for an explicit skills directory, for a
    configuration with no project identity and for a state directory that cannot be resolved. Pure."""
    identity = getattr(cfg, "_project_identity", None)
    skills = getattr(getattr(cfg, "paths", None), "skills", None)
    if not isinstance(identity, str) or not identity or not isinstance(skills, str) or not os.path.isabs(skills):
        return False
    try:
        state_directory = state_directory_for(getattr(cfg.paths, "state", None))[0]
    except StateDirectoryError:
        return False
    overlay = os.path.join(project_state_root(state_directory, identity), PROJECT_SKILLS_DIRECTORY)
    return os.path.realpath(skills) == os.path.realpath(overlay)


# Stores that are GLOBAL by declaration: they hold nothing derived from a project and carry no project's authority, so every project of this
# state directory shares them. One today: the release cache (``knowledge_cache.db``: public registry release dates keyed by ecosystem, package and
# version, fetched from fixed registry hosts, refreshed by TTL). Learned knowledge is NOT one: ``kriya learn`` takes a local file or free text and
# its rows record no scope or approval, so nothing proves a row is meant for every project; it is a project store (kriya/memory/project_store.py)
# until a governed promotion exists (S4).
GLOBAL_STATE_PARENT = "global"


def global_store_directory(configured_state: Optional[str], environment: Optional[Environment] = None) -> str:
    """``<state>/global``: the one named location of the declared global stores. Never a project's directory and never ``paths.memory``. Pure."""
    return os.path.join(state_directory_for(configured_state, environment)[0], GLOBAL_STATE_PARENT)


def bind_project_identity(cfg, workspace_path: str) -> None:
    """Bind ``cfg`` to the project ``workspace_path`` belongs to (its authority root). ``load_config`` does this for the directory the configuration
    was resolved in; a caller that deliberately points a configuration at ANOTHER project (a certification fixture) does it explicitly."""
    cfg._project_root = authority_root(workspace_path)
    cfg._project_identity = workspace_identity(workspace_path)


def project_identity(cfg) -> str:
    """The identity ``cfg`` was bound to. Missing: ``ProjectStateError(PROJECT_IDENTITY_MISSING)`` - never a default."""
    identity = getattr(cfg, "_project_identity", None)
    if not isinstance(identity, str) or not identity:
        raise ProjectStateError(PROJECT_IDENTITY_MISSING)
    return identity


def _bound_project(cfg) -> Tuple[str, str]:
    """(identity, root) of the project ``cfg`` is bound to; a configuration missing either proves nothing (``PROJECT_IDENTITY_MISSING``)."""
    identity = project_identity(cfg)
    root = getattr(cfg, "_project_root", None)
    if not isinstance(root, str) or not root:
        raise ProjectStateError(PROJECT_IDENTITY_MISSING)
    return identity, root


def bound_project_root(cfg) -> str:
    """The authority root of the project ``cfg`` is bound to: the ONE workspace root a consumer that acts for that project (the MCP manager:
    capability scope, containment mount, invocation approval) derives from the configuration, never from the process working directory. A
    configuration that carries no project (or none at all) proves nothing: ``ProjectStateError(PROJECT_IDENTITY_MISSING)``, never a default."""
    return _bound_project(cfg)[1]


def require_run_project(cfg, run_workspace: str) -> None:
    """The RUN-ENTRY rule (owner decision 2026-11-16, option 1). ``run_workspace`` is the workspace of the run that holds the lease (STATE's
    ``RunContext.workspace_path``: the run's own workspace, never a candidate sandbox and never a unit's directory). A configuration that carries
    no identity is bound to that run's project, once; one that is already bound is accepted only when it is bound to that same project and is
    otherwise refused (``PROJECT_IDENTITY_MISMATCH``) - it is never rebound. This is the boundary of a run, not a default: the store API below a
    run still refuses a configuration with no identity."""
    bound = getattr(cfg, "_project_identity", None)
    if not isinstance(bound, str) or not bound:
        bind_project_identity(cfg, run_workspace)
        return
    if bound != workspace_identity(run_workspace):
        raise ProjectStateError(PROJECT_IDENTITY_MISMATCH, root=getattr(cfg, "_project_root", None), run=run_workspace)


def _belongs_to(root: str, directory: object) -> bool:
    """Whether the canonical authority (D-001, ``belongs_to_root``) says ``directory`` is part of the project rooted at ``root``. Aliases (a link,
    a case or normalization variant) resolve to what they name before the question is asked. A directory that cannot be resolved is not the
    project's."""
    if not isinstance(directory, str) or not directory:
        return False
    try:
        return belongs_to_root(root, directory)
    except (OSError, ValueError):
        return False


def require_project_source(cfg, source_root: str) -> None:
    """Raise ``ProjectStateError`` unless ``source_root`` is part of the project ``cfg`` is bound to, by the canonical root authority: the project
    root itself, an ordinary directory of it, or an alias of either. This holds for a project that is a repository and for one that is not (a
    greenfield directory: its ordinary subdirectories are the project's). Lying beneath the project root proves nothing - an independent
    repository nested in it, a link into another project and a Kriya-managed sandbox are their own roots and are refused
    (``PROJECT_SOURCE_FOREIGN``). Pure."""
    _identity, root = _bound_project(cfg)
    if not _belongs_to(root, source_root):
        raise ProjectStateError(PROJECT_SOURCE_FOREIGN, source=source_root, root=root)


def foreign_root_within(source_root: str) -> Callable[[str], bool]:
    """For a walk of ``source_root``: a predicate over a directory's path RELATIVE to it, true when that directory is not part of the same root
    as ``source_root`` by the canonical authority (``reaches_root``: an independent repository or submodule nested in it, a Kriya-owned
    directory - in a repository and outside one alike). A walk that fills a project store prunes such a directory: its files are another
    root's, and lying beneath the source does not make them this project's. An ordinary subdirectory is kept. Pure."""
    canonical = canonical_workspace(source_root)

    def foreign(relative_directory: str) -> bool:
        return not reaches_root(canonical, os.path.normpath(os.path.join(canonical, relative_directory)))

    return foreign


def _proven_by_location(cfg, path: str, identity: str) -> bool:
    """An unstamped store is this project's only when WHERE it is proves it: its directory belongs to this project by the canonical authority (only
    this project's configuration places a store in its own repository), or it lies under this identity's project state root (a path derived from
    the identity itself)."""
    target = os.path.realpath(path)
    root = getattr(cfg, "_project_root", None)
    if isinstance(root, str) and root and _belongs_to(root, os.path.dirname(target)):
        return True
    try:
        state_root = os.path.realpath(project_state_root(state_directory_for(getattr(cfg.paths, "state", None))[0], identity))
    except StateDirectoryError:
        return False
    return path_relation(state_root, target) is PathRelation.WITHIN


def require_store_owner(cfg, path: str, recorded_owner: Optional[str]) -> None:
    """The ownership VERDICT for an existing store at ``path`` whose stamp reads ``recorded_owner`` (None: it carries none). Raises
    ``ProjectStateError`` unless the store is this project's: another identity is ``PROJECT_STORE_FOREIGN``; no stamp is accepted only where the
    location proves the project (``PROJECT_STORE_UNATTRIBUTED`` otherwise). Pure: the caller (RETRIEVAL) read the stamp."""
    identity, root = _bound_project(cfg)
    if recorded_owner is None:
        if not _proven_by_location(cfg, path, identity):
            raise ProjectStateError(PROJECT_STORE_UNATTRIBUTED, path=path, root=root)
        return
    if recorded_owner != identity:
        raise ProjectStateError(PROJECT_STORE_FOREIGN, path=path, actual=recorded_owner, expected=identity)


# ------------------------------------------------------------------ removed fields (moved from kriya/config/config.py, D3)

REMOVED_PATHS_LOGS_MESSAGE = (
    "paths.logs was removed; use logging.directory for logs or paths.state for trace state."
)
REMOVED_LOGGING_FILE_MESSAGE = (
    "logging.file was removed; use logging.directory to configure the log directory "
    "(or logging.file_enabled: false to turn the application log off)."
)


class RemovedConfigFieldError(ValueError):
    """A configuration names a field Kriya has removed; never reinterpreted."""


# ------------------------------------------------------------------ runtime-profile presets and the production seal (moved, D3)

# A production run must have a finite outer deadline.  One hour is deliberately
# generous enough for local-model generation while still preventing an
# unbounded run.  The reserve and per-file estimates remain independently
# configurable only outside the sealed profile.
PRODUCTION_GENERATION_TIME_BUDGET_SECONDS = 3600

# These guarantees have no weakening configuration knob: generation already
# refuses to run in the application workspace when isolated-worktree creation
# fails (workflow.py), and checkpoints/traces are persistent workflow artifacts.
# Declaring them is not evidence that the deployment can honor them:
# `kriya doctor --production` (kriya/production_doctor.py) verifies each one
# against the real environment and derives `runtime.fixed_guarantees` from those
# checks, so these identifiers stay the shared vocabulary, not a PASS.
PRODUCTION_FIXED_RUNTIME_GUARANTEES = frozenset({
    "candidate_isolation_fail_closed",
    "checkpoint_persistence",
    "trace_persistence",
    "no_uncontained_host_fallback",
})


def _production_budget_is_at_least_as_strict(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and 0 < value <= PRODUCTION_GENERATION_TIME_BUDGET_SECONDS
    )


# Sealed leaves for which a strictly safer explicit value is accepted in place
# of the preset value. Every other sealed leaf already carries its strictest
# setting, so it must equal the preset exactly.
_PRODUCTION_STRICTER_ACCEPTED = {
    ("autonomy", "generation_time_budget_seconds"): (
        _production_budget_is_at_least_as_strict,
        f"{PRODUCTION_GENERATION_TIME_BUDGET_SECONDS!r} or a smaller positive integer",
    ),
}


def production_sealed_value_satisfied(key: Tuple[str, str], value: Any) -> bool:
    """Whether ``value`` meets the production seal for ``key``: the preset
    value itself, or - for the leaves in _PRODUCTION_STRICTER_ACCEPTED - a
    value at least as strict."""
    stricter = _PRODUCTION_STRICTER_ACCEPTED.get(key)
    if stricter is not None:
        return stricter[0](value)
    return value == runtime_profile_preset_fields("production")[key]


def production_sealed_requirement(key: Tuple[str, str]) -> str:
    stricter = _PRODUCTION_STRICTER_ACCEPTED.get(key)
    return stricter[1] if stricter is not None else repr(runtime_profile_preset_fields("production")[key])


def runtime_profile_preset_fields(profile: Optional[str]) -> Dict[Any, Any]:
    """The exact (top_key, leaf_key) -> value mapping a runtime_profile
    preset expands to - see AppConfig's own docstring for what each preset
    means. Extracted as its own function so it can be tested directly
    without going through load_config() (whose SEC-009 P1 authority
    resolution denies a repository-sourced runtime_profile outright,
    independent of what it would have expanded to - see
    tests/test_sec009_config_authority.py) or through AppConfig's
    constructor (which does not itself apply this expansion - only
    load_config() does, deliberately, post-merge/pre-authority)."""
    if profile == "legacy":
        return {
            ("engineering_triage", "shadow_mode"): True,
            ("process_profiles", "enabled"): False,
            ("workflow_controller", "enabled"): False,
            ("workflow_controller", "mode"): "shadow",
        }
    if profile == "validated":
        return {
            ("engineering_triage", "shadow_mode"): False,
            ("process_profiles", "enabled"): True,
            ("workflow_controller", "enabled"): True,
            ("workflow_controller", "mode"): "shadow",
        }
    if profile == "hardened":
        return {
            ("engineering_triage", "shadow_mode"): False,
            ("process_profiles", "enabled"): True,
            ("workflow_controller", "enabled"): True,
            ("workflow_controller", "mode"): "enforce",
        }
    if profile == "production":
        return {
            ("engineering_triage", "shadow_mode"): False,
            ("process_profiles", "enabled"): True,
            ("workflow_controller", "enabled"): True,
            ("workflow_controller", "mode"): "enforce",
            ("execution_policy", "enabled"): True,
            ("execution_policy", "mode"): "enforce",
            ("autonomy", "egress_policy"): "local_only",
            ("autonomy", "generation_time_budget_seconds"): PRODUCTION_GENERATION_TIME_BUDGET_SECONDS,
            ("autonomy", "containment_backend"): "oci",
            ("autonomy", "contained_execution_required"): True,
            ("autonomy", "mcp_contained_execution_required"): True,
            # Production always captures the pristine full-suite baseline and
            # fails closed if it is indeterminate - stricter than PRD-024's
            # risk-derived `auto`, which skips trivial changes.
            ("autonomy", "brownfield_full_regression_baseline_policy"): "required",
            # PRD-012 deny-by-default: an arbitrary shell command gets no
            # network (package managers stay registry-scoped), and
            # KnowledgeGuard never sends goal-named library names to public
            # registries (cached release metadata only).
            ("autonomy", "shell_network"): "denied",
            ("knowledge", "offline_mode"): True,
            # PRD-020: an original requirement with no verifier verdict, or
            # one the verifier cannot confirm from code and no deterministic
            # evidence closed, never lets a production run succeed.
            ("autonomy", "requirement_unknown_policy"): "block",
            ("autonomy", "requirement_unverified_policy"): "block",
        }
    return {}


# ------------------------------------------------------------------ resolution (moved, D3)

@dataclass
class ConfigResolutionState:
    """Everything load_config() resolves BEFORE authority is checked -
    extracted into its own function (resolve_config_state()) so both
    load_config() and `kriya authority inspect`/`approve` (kriya/cli.py)
    share exactly one code path for what counts as a violation and what the
    live effective value of each field is. approve() in particular must see
    EXACTLY what load_config() would compute - a second, slightly-different
    re-implementation here would be a real correctness risk, not just
    duplication."""
    config_dict: Dict[str, Any]
    workspace_root: str
    violations: List[Any]  # List[ConfigAuthorityViolation] - Any to avoid a module-level authority.py import
    # GUI M6 (execution preflight): which file, if any, supplied the user layer and how it was found, as resolved here - reported, never re-derived.
    config_path: Optional[str] = None
    config_source: Optional[str] = None


_CONFIG_FILE_NAMES = ("kriya.yaml", "kriya.yml")


def _refuse_shadowed_configuration(invoked_directory: str, root: str) -> None:
    """Every directory from the invoked one up to (not including) the authority root: a configuration file in one of them is not read, so it is refused."""
    directory = invoked_directory
    while path_identity(directory, root) is not PathIdentity.SAME and path_relation(root, directory) is PathRelation.WITHIN:
        for filename in _CONFIG_FILE_NAMES:
            if os.path.lexists(os.path.join(directory, filename)):
                raise ShadowedConfigurationError(
                    f"{os.path.join(directory, filename)} is not this repository's configuration: the configuration of a repository is the one at its root "
                    f"({root}), read from every directory inside it. Merge it into {os.path.join(root, _CONFIG_FILE_NAMES[0])}, remove it, or pass it with --config."
                )
        parent = os.path.dirname(directory)
        if parent == directory:
            return
        directory = parent


def resolve_config_state(config_path: Optional[str] = None) -> ConfigResolutionState:
    """Source -> provenance -> expansion -> field classification -> violation
    list. Never raises for a violation (that's load_config()'s job, after
    consulting SEC-009 P2 approval) and never constructs AppConfig - pure
    resolution, safe to call from `kriya authority inspect`/`approve` even
    when the config would ultimately be denied.
    """
    from kriya.config.authority import (
        CONTAINMENT_MANDATE_FIELDS,
        ConfigSource,
        FieldClassification,
        FieldPath,
        agent_role_field_classification,
        compute_violations,
        explicit_config_source,
        parse_autonomy_mode,
        parse_containment_mandate,
        parse_egress_policy,
        path_field_classification,
        value_gated_field_classification,
    )

    config_dict: Dict[str, Any] = {}
    user_data: Dict[str, Any] = {}
    # provenance[(top_key, leaf_key)] = ConfigSource that set that field.
    # top_key is None for scalar/list top-level fields (runtime_profile,
    # llm_chain). Granularity mirrors the merge loop below exactly - this is
    # the actual unit a source can independently set.
    provenance: Dict[FieldPath, ConfigSource] = {}
    # Runtime-resolved overrides for fields whose classification depends on a
    # resolved value, not just the field name (paths.* containment check).
    classification_overrides: Dict[FieldPath, FieldClassification] = {}

    # D-001: the workspace SEC-009 judges (trust-path containment, an explicit --config's provenance, the paths.state rule), the approval identity and
    # the directory ``kriya.yaml`` is discovered in are the repository's authority root - the same for the root and every subdirectory of one repository.
    invoked_directory = os.path.realpath(current_directory())
    workspace_root = authority_root(invoked_directory)

    # Determine Kriya Installation Directory
    KRIYA_INSTALL_DIR = kriya_install_dir()

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
                                default_data["paths"][k] = os.path.realpath(os.path.join(KRIYA_INSTALL_DIR, v))
                    if "plugins" in default_data and "directory" in default_data["plugins"]:
                        v = default_data["plugins"]["directory"]
                        if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                            default_data["plugins"]["directory"] = os.path.realpath(os.path.join(KRIYA_INSTALL_DIR, v))
                    config_dict.update(default_data)
                    for top_key, val in default_data.items():
                        if isinstance(val, dict):
                            for leaf_key in val:
                                provenance[(top_key, leaf_key)] = ConfigSource.PACKAGED_DEFAULT
                        else:
                            provenance[(None, top_key)] = ConfigSource.PACKAGED_DEFAULT
        except Exception as e:
            logger.warning(f"Failed to load packaged default configuration at '{default_path}', falling back to bare defaults: {e}")

    # If no config_path is explicitly provided, look for 'kriya.yaml' or 'kriya.yml' in current directory
    # If not found, look for it in the Kriya Installation Directory
    config_dir = workspace_root
    source: Optional["ConfigSource"] = None
    if not config_path:
        _refuse_shadowed_configuration(invoked_directory, workspace_root)
        for filename in ["kriya.yaml", "kriya.yml"]:
            path = os.path.join(workspace_root, filename)
            if os.path.exists(path):
                config_path = path
                config_dir = workspace_root
                source = ConfigSource.AUTO_DISCOVERED_CWD
                break

        if not config_path:
            # Fall back to Kriya installation directory
            for filename in ["kriya.yaml", "kriya.yml"]:
                path = os.path.join(KRIYA_INSTALL_DIR, filename)
                if os.path.exists(path):
                    config_path = path
                    config_dir = KRIYA_INSTALL_DIR
                    source = ConfigSource.AUTO_DISCOVERED_INSTALL_DIR
                    break
    else:
        # realpath (not just abspath) so a symlinked --config path is
        # classified and resolved by its REAL target, not its apparent
        # location - closes the symlinked-config gap (SEC-009 P1).
        config_path = os.path.realpath(config_path)
        config_dir = os.path.dirname(config_path)
        source = explicit_config_source(config_path, workspace_root)

    # Load user config if specified and exists
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_data = yaml.safe_load(f) or {}
                if user_data:
                    # Resolve relative paths in user config to config_dir (realpath -
                    # resolves symlinks in the resulting path, not just abspath).
                    if isinstance(user_data.get("paths"), dict) and "logs" in user_data["paths"]:
                        raise RemovedConfigFieldError(f"{config_path}: {REMOVED_PATHS_LOGS_MESSAGE}")
                    if isinstance(user_data.get("logging"), dict) and "file" in user_data["logging"]:
                        raise RemovedConfigFieldError(f"{config_path}: {REMOVED_LOGGING_FILE_MESSAGE}")
                    if "paths" in user_data:
                        for k, v in user_data["paths"].items():
                            if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                                user_data["paths"][k] = os.path.realpath(os.path.join(config_dir, v))
                        # paths.state: ANY relative value (not only "./"-
                        # prefixed) anchors to config_dir, never the CWD; "~"
                        # expands; resolved once so SEC-009 classifies exactly
                        # the directory kriya/core/state_paths.py opens.
                        state_value = user_data["paths"].get("state")
                        if isinstance(state_value, str):
                            expanded = expand_user(state_value)
                            user_data["paths"]["state"] = os.path.realpath(
                                expanded if os.path.isabs(expanded) else os.path.join(config_dir, expanded)
                            )
                            # Inside the workspace, state lives only beneath
                            # <workspace>/.kriya/ (never a repository-visible
                            # ./state or ./logs); outside it, SEC-009 below.
                            require_workspace_local_state_under_kriya_dir(
                                user_data["paths"]["state"], workspace_root, state_value,
                            )
                    if "plugins" in user_data and "directory" in user_data["plugins"]:
                        v = user_data["plugins"]["directory"]
                        if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                            user_data["plugins"]["directory"] = os.path.realpath(os.path.join(config_dir, v))

                    # logging.directory - canonicalized ONCE here (expand ~,
                    # realpath) so the value SEC-009 digests is exactly the
                    # directory kriya/core/logging_setup.py opens. A relative
                    # value is a typed error: it is never anchored to the CWD
                    # or to config_dir.
                    if isinstance(user_data.get("logging"), dict) and user_data["logging"].get("directory") is not None:
                        from kriya.core.logging_setup import canonical_log_directory
                        user_data["logging"]["directory"] = canonical_log_directory(
                            user_data["logging"]["directory"], "logging.directory"
                        )

                    # TOOL-003 P1: MCP capability filesystem paths - resolved
                    # and escape-checked HERE, anchored to config_dir (same
                    # anchor and same realpath-based idiom as paths.*/
                    # logging.directory above, for the identical reason: the value
                    # SEC-009 authority resolution digests below and the
                    # value kriya/mcp/capability.py's resolve_mcp_capability_
                    # profile() eventually binds to a real MCPClient must be
                    # the SAME single resolved value, computed ONCE, never
                    # independently re-resolved later against a possibly-
                    # different CWD (the exact classify-here/execute-there
                    # split the SEC-009 bypass-closure fix of 2026-09-12 exists to
                    # prevent).
                    # A `capabilities` key is unconditionally backfilled to
                    # `{}` for every configured server (even one that never
                    # mentions `capabilities` at all) so an omitted key and
                    # an explicit empty `capabilities: {}` always produce the
                    # IDENTICAL effective dict from this point forward -
                    # otherwise the two would digest differently under
                    # SEC-009 P2 (build_security_field_records() digests this
                    # exact dict) even though pydantic resolves both to the
                    # same default MCPCapabilityConfig(), causing a spurious
                    # "approval invalidated" the moment an operator adds a
                    # no-op explicit empty block.
                    # PRD-031A: static-analysis rule packs and the waiver
                    # store are resolved against config_dir ONCE here (never
                    # the process CWD), realpath'd, like every other path
                    # field - the value SEC-009 digests is the value used.
                    # static_analysis.* is SECURITY_AUTHORITY as a whole, so
                    # a path may name a location outside config_dir (a rule
                    # pack outside the workspace is the recommended layout).
                    _static = user_data.get("static_analysis")
                    if isinstance(_static, dict):
                        def _anchor(raw: Any) -> Any:
                            if not isinstance(raw, str) or not raw:
                                return raw
                            expanded = expand_user(raw)
                            return os.path.realpath(
                                expanded if os.path.isabs(expanded) else os.path.join(config_dir, expanded)
                            )

                        _waivers = _static.get("waivers")
                        if isinstance(_waivers, dict) and _waivers.get("store"):
                            _waivers["store"] = _anchor(_waivers["store"])
                        _providers = _static.get("providers")
                        if isinstance(_providers, dict):
                            for _provider_cfg in _providers.values():
                                packs = _provider_cfg.get("rule_packs") if isinstance(_provider_cfg, dict) else None
                                if isinstance(packs, list):
                                    _provider_cfg["rule_packs"] = [
                                        {**pack, "path": _anchor(pack.get("path"))} if isinstance(pack, dict)
                                        else _anchor(pack)
                                        for pack in packs
                                    ]
                    if isinstance(user_data.get("mcp"), dict):
                        for _server_name, _server_cfg in user_data["mcp"].items():
                            if not isinstance(_server_cfg, dict):
                                continue
                            caps = _server_cfg.setdefault("capabilities", {})
                            if not isinstance(caps, dict):
                                continue
                            for _path_field in ("additional_read_paths", "additional_write_paths"):
                                raw_paths = caps.get(_path_field)
                                if not isinstance(raw_paths, list):
                                    continue
                                resolved_paths = []
                                for raw in raw_paths:
                                    if not isinstance(raw, str):
                                        resolved_paths.append(raw)
                                        continue
                                    if os.path.isabs(raw):
                                        # Absolute -> an explicit host path.
                                        # Unambiguous by construction - no
                                        # anchor, no escape check applies (an
                                        # absolute path was never workspace-
                                        # relative to begin with).
                                        resolved_paths.append(os.path.realpath(raw))
                                        continue
                                    # Relative -> workspace-relative-only:
                                    # resolved against config_dir and
                                    # canonicalized (realpath follows both
                                    # `..` traversal and any intermediate
                                    # symlink to its real target) - REJECTED
                                    # if the real target does not stay inside
                                    # the real config_dir. A relative-looking
                                    # path must never be able to smuggle an
                                    # outside-anchor target past someone
                                    # reading the config, and must never
                                    # silently receive host-path authority it
                                    # did not explicitly ask for by being
                                    # written as an absolute path.
                                    real_root = os.path.realpath(config_dir)
                                    real_target = os.path.realpath(os.path.join(real_root, raw))
                                    if path_relation(real_root, real_target) is not PathRelation.WITHIN:
                                        raise ValueError(
                                            f"mcp.{_server_name}.capabilities.{_path_field} entry "
                                            f"{raw!r} is a relative path that resolves outside "
                                            f"{real_root!r} - a relative capability path must stay "
                                            f"within the config's own directory; use an absolute "
                                            f"path to explicitly authorize a host path outside it."
                                        )
                                    resolved_paths.append(real_target)
                                caps[_path_field] = resolved_paths

                    # paths.{skills,memory,logs} classification depends on the
                    # resolved value (in-workspace vs. escaping) - resolve
                    # non-relative values too (an absolute path or a
                    # symlinked one) so containment is checked against the
                    # real target in every case, not just the "./"-prefixed
                    # relative-path branch above. Anchored to config_dir (the
                    # directory the SETTING config file lives in), not
                    # workspace_root/CWD - see path_field_classification()'s
                    # own docstring for why: an explicit --config living
                    # outside CWD with an ordinary relative `./skills` value
                    # is not an authority escape, just a config that lives
                    # somewhere else.
                    if isinstance(user_data.get("paths"), dict):
                        for k, v in user_data["paths"].items():
                            if k in ("skills", "memory", "state") and isinstance(v, str):
                                resolved = v if os.path.isabs(v) else os.path.join(config_dir, v)
                                classification_overrides[("paths", k)] = path_field_classification(
                                    k, resolved, config_dir
                                )
                            elif k == "state" and v is None:
                                # null = the canonical default location; grants nothing
                                classification_overrides[("paths", k)] = FieldClassification.REPOSITORY_SAFE

                    # agent_llms.<role> is one atomic merge unit (see
                    # agent_role_field_classification()'s docstring) -
                    # REPOSITORY_SAFE unless it redirects a network
                    # destination (base_url) somewhere within it.
                    if isinstance(user_data.get("agent_llms"), dict):
                        for role, role_val in user_data["agent_llms"].items():
                            classification_overrides[("agent_llms", role)] = agent_role_field_classification(role_val)

                    # S3: a value-gated field (self_correction_loop_enabled,
                    # web_lookup_auto_approve) is REPOSITORY_SAFE only at its
                    # restricting value; any enabling value needs authority.
                    # S3b: a containment resource bound only at a positive
                    # value at most its packaged ceiling; a raise needs authority.
                    for _top_key, _section in user_data.items():
                        if isinstance(_section, dict):
                            for _leaf_key, _value in _section.items():
                                _gated = value_gated_field_classification(_top_key, _leaf_key, _value)
                                if _gated is not None:
                                    classification_overrides[(_top_key, _leaf_key)] = _gated

                    # logging.directory: null (the canonical default) grants
                    # nothing; any directory is a filesystem write target and
                    # falls to its static SECURITY_AUTHORITY entry.
                    if isinstance(user_data.get("logging"), dict) and "directory" in user_data["logging"]:
                        if user_data["logging"]["directory"] is None:
                            classification_overrides[("logging", "directory")] = FieldClassification.REPOSITORY_SAFE

                    # Simple deep merge of level-1 dicts, tracking provenance
                    # at the exact same granularity the merge itself uses.
                    for key, val in user_data.items():
                        if isinstance(val, dict) and key in config_dict and isinstance(config_dict[key], dict):
                            config_dict[key].update(val)
                            for leaf_key in val:
                                provenance[(key, leaf_key)] = source
                        else:
                            config_dict[key] = val
                            if isinstance(val, dict):
                                for leaf_key in val:
                                    provenance[(key, leaf_key)] = source
                            else:
                                provenance[(None, key)] = source
        except Exception as e:
            from kriya.core.logging_setup import LogDirectoryError
            if isinstance(e, (LogDirectoryError, StateDirectoryError, RemovedConfigFieldError)):
                raise  # already typed and names the field
            raise ValueError(f"Failed to load configuration at {config_path}: {e}") from e

    # C-05 (S3): an UNCONFIGURED paths.memory is this project's own directory under the state directory, never one directory every project shares
    # (the packaged "./memory" used to resolve to <install>/memory for all of them). An explicit paths.memory is kept as written; either way each
    # store file is bound to the project by its recorded identity (the project-state namespace above).
    _paths = config_dict.get("paths")
    _user_paths = user_data.get("paths")
    if isinstance(_paths, dict) and not (isinstance(_user_paths, dict) and "memory" in _user_paths):
        _paths["memory"] = default_project_memory_directory(_paths.get("state"), workspace_root)
    # The same for an UNCONFIGURED paths.skills: the project's own skill overlay, never the shipped library every project reads (the packaged
    # "./skills" used to resolve to <install>/skills, so one project's scaffolds and learned skills were every project's). The shipped library
    # stays a separate read-only root (skills.load_global). An explicit paths.skills is kept as written.
    if isinstance(_paths, dict) and not (isinstance(_user_paths, dict) and "skills" in _user_paths):
        _paths["skills"] = default_project_skills_directory(_paths.get("state"), workspace_root)

    # --- runtime_profile expansion (dict-level, BEFORE AppConfig/authority) ---
    # Applied before AppConfig construction so authority resolution below
    # inspects the FULLY EXPANDED effective configuration - a repository
    # setting runtime_profile cannot launder a security-sensitive field
    # change through preset expansion and have it slip past classification
    # simply because the raw `runtime_profile` scalar was the only thing
    # checked. Derived fields inherit RUNTIME_PROFILE_OVERRIDE provenance
    # (itself denied for any repository-equivalent source) whenever the
    # runtime_profile value itself did not come from a trusted source.
    runtime_profile = config_dict.get("runtime_profile")
    if runtime_profile is not None:
        preset_fields = runtime_profile_preset_fields(runtime_profile)
        if runtime_profile == "production":
            contradictory = []
            for top, leaf in preset_fields:
                explicit_section = user_data.get(top)
                if (
                    isinstance(explicit_section, dict)
                    and leaf in explicit_section
                    and not production_sealed_value_satisfied((top, leaf), explicit_section[leaf])
                ):
                    contradictory.append(
                        f"{top}.{leaf}={explicit_section[leaf]!r} "
                        f"(production requires {production_sealed_requirement((top, leaf))})"
                    )
            if contradictory:
                raise ValueError(
                    "runtime_profile 'production' rejects contradictory overrides: "
                    + "; ".join(sorted(contradictory))
                )
        else:
            # Preserve the original all-or-nothing behavior of the three older
            # presets exactly. Production is more precise because its sealed
            # surface spans only selected leaves in larger sections such as
            # autonomy, where unrelated settings remain legitimate.
            conflicting = sorted(
                key for key in ("engineering_triage", "process_profiles", "workflow_controller")
                if key in user_data
            )
            if conflicting:
                raise ValueError(
                    f"runtime_profile cannot be combined with explicit {conflicting!r}; choose the preset "
                    "or configure the individual subsystems"
                )

        rp_source = provenance.get((None, "runtime_profile"), ConfigSource.PACKAGED_DEFAULT)
        derived_source = (
            ConfigSource.RUNTIME_PROFILE_OVERRIDE if rp_source not in (
                ConfigSource.PACKAGED_DEFAULT, ConfigSource.PLATFORM_FLOOR
            ) else rp_source
        )

        for (top, leaf), value in preset_fields.items():
            if runtime_profile == "production":
                explicit_section = user_data.get(top)
                if (
                    isinstance(explicit_section, dict)
                    and leaf in explicit_section
                    and explicit_section[leaf] != value
                ):
                    # Past the contradiction check above, a differing explicit
                    # value is a stricter one (e.g. a shorter deadline): keep it,
                    # with its own provenance, rather than loosening it back to
                    # the preset.
                    continue
            config_dict.setdefault(top, {})[leaf] = value
            provenance[(top, leaf)] = derived_source

    # S16a (OI-130): autonomy.mode is a closed vocabulary. The EFFECTIVE value (whichever layer set it: packaged default,
    # workspace or install-dir kriya.yaml, explicit --config) is validated BEFORE classification, so a malformed mode is a
    # typed AutonomyModeError for load_config, `kriya authority inspect|approve` and the desktop's configuration operations
    # alike: there is nothing to approve, and an approval of it (a trust file, a stale local store) is never consulted.
    autonomy_section = config_dict.get("autonomy")
    if isinstance(autonomy_section, dict) and "mode" in autonomy_section:
        parse_autonomy_mode(autonomy_section["mode"])
    # F5 (Wave-1 closure delta): autonomy.egress_policy is a closed vocabulary too, owned by the same module (parse_egress_policy; AutonomyConfig's validator, assignment and
    # model_copy call the very same function). The effective value is checked at the same point as the mode, so a mistyped restriction ("LOCAL_ONLY") is a typed
    # EgressPolicyError for `kriya authority inspect|approve` and the desktop's configuration operations as it already is for load_config - never listed as a field to
    # approve: an approval cannot make it valid.
    if isinstance(autonomy_section, dict) and "egress_policy" in autonomy_section:
        parse_egress_policy(autonomy_section["egress_policy"])
    # S7 (packaged-default migration): the containment mandate has NO operator opt-out. An explicit false (any layer) is a typed ContainmentOptOutError for load_config,
    # `kriya authority inspect|approve` and the desktop's configuration operations - never listed as a field to approve (an approval cannot make host execution valid),
    # never coerced to true. An explicit true and the packaged default are accepted; the production profile writes true.
    if isinstance(autonomy_section, dict):
        for mandate_field in CONTAINMENT_MANDATE_FIELDS:
            if mandate_field in autonomy_section:
                parse_containment_mandate(mandate_field, autonomy_section[mandate_field])

    violations = compute_violations(provenance, classification_overrides)
    return ConfigResolutionState(config_dict=config_dict, workspace_root=workspace_root, violations=violations,
                                 config_path=config_path if config_path and os.path.exists(config_path) else None,
                                 config_source=source.value if source is not None and config_path and os.path.exists(config_path) else None)
