"""Resume fingerprints and the reused-artifact dependency matrix (PRD-008).

A checkpoint may be resumed only as far as the state it lets the new run
reuse is still valid. Two tables decide that, and nothing else does:

* ``ARTIFACT_DEPENDENCIES`` names every artifact a resume can reuse, the
  stage that produced it, and the fingerprints it depends on;
* ``reused_artifacts_for_checkpoint()`` names the artifacts a given
  checkpoint actually hands to the resumed run (by value, never by key
  presence: ``_save_stage_checkpoint`` always writes the baseline keys, often
  as ``None``).

Each fingerprint then compares to exactly one status:

* ``NOT_APPLICABLE`` - no reused artifact depends on it. Only the matrix can
  say this; a caller omitting a current value never can.
* ``MATCH`` - stored and current values are both available and equal.
* ``CHANGED`` - both available, different.
* ``UNVERIFIED`` - either side is missing or UNAVAILABLE, or the two were
  computed on different bases. Never treated as a match.

A CHANGED or UNVERIFIED fingerprint invalidates every stage whose artifacts
depend on it (``invalidated_stages_for``). ``kriya_runtime`` is a dependency
of every artifact, so a different Kriya build reuses nothing.

Configuration is split by one explicit ownership table
(``CONFIG_FIELD_OWNERS``): model identity, containment and verification-policy
fields belong to their own fingerprints; every other field, including any
field added later, stays in ``config`` (fail closed).
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple

UNAVAILABLE = "UNAVAILABLE"
RESUME_FINGERPRINT_SCHEMA_VERSION = 1
CHECKPOINT_KEY = "resume_fingerprints"


class FingerprintStatus(str, Enum):
    MATCH = "MATCH"
    CHANGED = "CHANGED"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Fingerprint:
    """``value`` None means UNAVAILABLE; ``basis`` says how the value was
    derived (or why it could not be), and two values are comparable only on
    the same basis."""

    value: Optional[str]
    basis: str

    @property
    def available(self) -> bool:
        return self.value is not None

    @classmethod
    def unavailable(cls, basis: str) -> "Fingerprint":
        return cls(None, basis)

    def to_dict(self) -> Dict[str, str]:
        return {"value": self.value if self.value is not None else UNAVAILABLE, "basis": self.basis}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Fingerprint"]:
        if not isinstance(data, dict):
            return None
        value, basis = data.get("value"), data.get("basis")
        if not isinstance(value, str) or not isinstance(basis, str):
            return None
        return cls(None if value == UNAVAILABLE else value, basis)


FINGERPRINT_NAMES: Tuple[str, ...] = (
    "workspace", "config", "goal", "approved_plan",
    "input_obligation_ledger", "effective_obligation_ledger",
    "skills", "model_runtime", "containment", "toolchain",
    "verification_policy", "authority_context", "kriya_runtime",
)

# ---------------------------------------------------------------- matrix

STAGE_ORDER: Tuple[str, ...] = ("context", "planning", "model_protocol", "candidate", "verification")

_PLANNING_DEPENDENCIES = frozenset({
    "workspace", "config", "goal", "skills", "approved_plan",
    "input_obligation_ledger", "authority_context", "kriya_runtime",
})

ARTIFACT_STAGE: Dict[str, str] = {
    "knowledge_clearance": "context",
    "plan": "planning",
    "design": "planning",
    "model_protocol_state": "model_protocol",
    "candidate": "candidate",
    "validation_baselines": "verification",
    "candidate_gate_outcomes": "verification",
}

ARTIFACT_DEPENDENCIES: Dict[str, FrozenSet[str]] = {
    # A resumed checkpoint skips the knowledge-gap gate (workflow.py).
    # The knowledge-gap check also reads the skills directory (a library with
    # no covering skill is a gap), so a skill change re-opens the gate.
    "knowledge_clearance": frozenset({"workspace", "config", "goal", "skills", "kriya_runtime"}),
    "plan": _PLANNING_DEPENDENCIES,
    "design": _PLANNING_DEPENDENCIES,
    # Model-protocol state (negotiated capabilities, tool protocol). No
    # checkpoint reuses any today: model_hops are trace-only.
    "model_protocol_state": frozenset({"model_runtime", "kriya_runtime"}),
    "candidate": _PLANNING_DEPENDENCIES | {"effective_obligation_ledger"},
    "validation_baselines": frozenset({
        "workspace", "config", "containment", "toolchain", "verification_policy", "kriya_runtime",
    }),
    "candidate_gate_outcomes": _PLANNING_DEPENDENCIES | {
        "effective_obligation_ledger", "containment", "toolchain", "verification_policy",
    },
}

CANDIDATE_CHECKPOINT_STAGES = frozenset({"candidate_gates_passed", "developer_success"})


def invalidated_stages_for(fingerprint: str) -> Tuple[str, ...]:
    """Every stage owning an artifact that depends on ``fingerprint``."""
    stages = {
        ARTIFACT_STAGE[artifact]
        for artifact, dependencies in ARTIFACT_DEPENDENCIES.items()
        if fingerprint in dependencies
    }
    return tuple(stage for stage in STAGE_ORDER if stage in stages)


def reused_artifacts_for_checkpoint(checkpoint: Mapping[str, Any]) -> FrozenSet[str]:
    """What run_generation_workflow() would take from this checkpoint."""
    reused = {"knowledge_clearance"}
    if checkpoint.get("plan"):
        reused.add("plan")
    if checkpoint.get("design") or checkpoint.get("architect_files"):
        reused.add("design")
    if (
        checkpoint.get("validation_baseline_targeted") is not None
        or checkpoint.get("validation_baseline_full_regression") is not None
    ):
        reused.add("validation_baselines")
    if checkpoint.get("stage") in CANDIDATE_CHECKPOINT_STAGES:
        reused.add("candidate")
        if checkpoint.get("gate_outcomes") is not None:
            reused.add("candidate_gate_outcomes")
    return frozenset(reused)


# ---------------------------------------------------------------- candidate + ledger snapshots

# Checkpoint keys written with a candidate (workflow.py _save_stage_checkpoint).
CANDIDATE_HASH_KEY = "candidate_snapshot_hash"
EFFECTIVE_LEDGER_KEY = "effective_obligation_ledger_snapshot"


def candidate_snapshot_digest(final_files: Mapping[str, str]) -> str:
    """Digest of a checkpointed candidate (relpath -> exact text)."""
    return _digest(sorted(final_files.items()))


def candidate_integrity_problem(checkpoint: Mapping[str, Any]) -> Optional[str]:
    """Why a checkpoint's candidate cannot be reused, or None. The save side
    stores no digest when any written file could not be captured exactly
    (unreadable, deleted, not UTF-8), so an incomplete candidate is never
    rebuilt as if it were whole."""
    final_files = checkpoint.get("final_files")
    if not isinstance(final_files, dict) or not all(
        isinstance(path, str) and isinstance(content, str) for path, content in final_files.items()
    ):
        return "checkpoint holds no well-formed candidate files"
    stored = checkpoint.get(CANDIDATE_HASH_KEY)
    if not isinstance(stored, str):
        return "checkpoint recorded no candidate digest (legacy, or a file could not be captured exactly)"
    if candidate_snapshot_digest(final_files) != stored:
        return "checkpointed candidate files do not match their recorded digest"
    return None


def restore_effective_ledger(checkpoint: Mapping[str, Any]) -> Tuple[Any, Optional[str]]:
    """(ledger, problem): the effective obligation ledger saved with a
    candidate, or None and why not. A missing snapshot is never an empty
    ledger."""
    from kriya.workflow.obligations import ObligationLedger

    snapshot = checkpoint.get(EFFECTIVE_LEDGER_KEY)
    if snapshot is None:
        return None, "checkpoint holds no effective obligation ledger snapshot"
    try:
        return ObligationLedger.from_snapshot(snapshot), None
    except ValueError as error:
        return None, str(error)


# ---------------------------------------------------------------- invalidation

# Each stage's artifacts are derived from the ones before it on this chain,
# so a resume keeps a prefix of it. model_protocol is not on it: nothing
# downstream is derived from negotiated model state, so a model change
# never discards a plan or candidate (it invalidates model_protocol only).
DERIVATION_CHAIN: Tuple[str, ...] = ("context", "planning", "candidate", "verification")

_ARTIFACT_KEYS: Dict[str, Tuple[str, ...]] = {
    "plan": ("plan",),
    "design": ("design", "architect_files"),
    "candidate": ("final_files", "original_files", CANDIDATE_HASH_KEY, EFFECTIVE_LEDGER_KEY, "model_hops"),
    "candidate_gate_outcomes": ("gate_outcomes",),
    "validation_baselines": ("validation_baseline_targeted", "validation_baseline_full_regression"),
    "model_protocol_state": (),
}


@dataclass(frozen=True)
class ResumePlan:
    """What a resumed run actually reuses from one checkpoint. ``state`` is
    the checkpoint with every discarded artifact removed (None: nothing is
    reused, the run starts fresh). The reuse flags live here, never in the
    checkpoint file, so a checkpoint cannot grant itself a gate skip."""

    checkpoint_id: str
    checkpoint_stage: Optional[str]
    offered: FrozenSet[str]
    reused: FrozenSet[str]
    invalidated_stages: Tuple[str, ...]
    truncated_at: Optional[str]
    state: Optional[Dict[str, Any]]

    @property
    def resumes(self) -> bool:
        return self.state is not None

    @property
    def reuse_candidate(self) -> bool:
        return "candidate" in self.reused

    @property
    def skip_candidate_gates(self) -> bool:
        return "candidate_gate_outcomes" in self.reused

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_stage": self.checkpoint_stage,
            "reused": sorted(self.reused),
            "discarded": sorted(self.offered - self.reused),
            "invalidated_stages": list(self.invalidated_stages),
            "truncated_at": self.truncated_at,
            "reuse_candidate": self.reuse_candidate,
            "skip_candidate_gates": self.skip_candidate_gates,
        }


def build_resume_plan(
    checkpoint_id: str, checkpoint: Mapping[str, Any], invalidated_stages: Iterable[str],
) -> ResumePlan:
    """Keep the longest prefix of DERIVATION_CHAIN no invalidated stage
    touches; drop every artifact from the first invalidated stage on. Pure."""
    invalidated = tuple(stage for stage in STAGE_ORDER if stage in set(invalidated_stages))
    offered = reused_artifacts_for_checkpoint(checkpoint)
    truncated_at = next((stage for stage in DERIVATION_CHAIN if stage in invalidated), None)
    kept_stages = (
        set(DERIVATION_CHAIN) if truncated_at is None
        else set(DERIVATION_CHAIN[:DERIVATION_CHAIN.index(truncated_at)])
    )
    reused = frozenset(
        artifact for artifact in offered
        if ARTIFACT_STAGE[artifact] in kept_stages and ARTIFACT_STAGE[artifact] not in invalidated
    )
    if "knowledge_clearance" not in reused:
        return ResumePlan(
            checkpoint_id, checkpoint.get("stage"), offered, frozenset(), invalidated, truncated_at, None,
        )
    state = copy.deepcopy(dict(checkpoint))
    for artifact in offered - reused:
        for key in _ARTIFACT_KEYS[artifact]:
            state[key] = None
    if "candidate" not in reused:
        # The stage label must not keep claiming a candidate that is gone.
        state["stage"] = "design" if "design" in reused else "plan" if "plan" in reused else "context"
    state["resumed_checkpoint_stage"] = checkpoint.get("stage")
    return ResumePlan(checkpoint_id, checkpoint.get("stage"), offered, reused, invalidated, truncated_at, state)


# ---------------------------------------------------------------- comparison

@dataclass(frozen=True)
class FingerprintComparison:
    name: str
    status: FingerprintStatus
    stored: Optional[str]
    current: Optional[str]
    reason: str

    @property
    def invalidates(self) -> bool:
        return self.status in (FingerprintStatus.CHANGED, FingerprintStatus.UNVERIFIED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status.value,
            "stored": self.stored, "current": self.current, "reason": self.reason,
        }


def fingerprint_block(fingerprints: Mapping[str, Fingerprint]) -> Dict[str, Any]:
    """The checkpoint entry (``checkpoint[CHECKPOINT_KEY]``)."""
    return {
        "schema_version": RESUME_FINGERPRINT_SCHEMA_VERSION,
        "fingerprints": {name: fingerprints[name].to_dict() for name in sorted(fingerprints)},
    }


def parse_fingerprint_block(block: Any) -> Tuple[Dict[str, Fingerprint], Optional[str]]:
    """(fingerprints, problem). An absent, malformed or unknown-version block
    yields no stored values, so every applicable fingerprint is UNVERIFIED."""
    if block is None:
        return {}, "checkpoint predates resume fingerprints (PRD-008)"
    if not isinstance(block, dict) or block.get("schema_version") != RESUME_FINGERPRINT_SCHEMA_VERSION:
        return {}, "checkpoint resume fingerprints are malformed or an unsupported version"
    raw = block.get("fingerprints")
    if not isinstance(raw, dict):
        return {}, "checkpoint resume fingerprints are malformed"
    parsed: Dict[str, Fingerprint] = {}
    for name, value in raw.items():
        fingerprint = Fingerprint.from_dict(value)
        if fingerprint is not None:
            parsed[name] = fingerprint
    return parsed, None


def compare_resume_fingerprints(
    stored_block: Any,
    current: Mapping[str, Fingerprint],
    reused_artifacts: Iterable[str],
) -> Tuple[FingerprintComparison, ...]:
    reused = frozenset(reused_artifacts)
    unknown = reused - set(ARTIFACT_DEPENDENCIES)
    if unknown:
        raise ValueError(f"unknown reused artifacts: {sorted(unknown)}")
    stored_map, block_problem = parse_fingerprint_block(stored_block)
    comparisons = []
    for name in FINGERPRINT_NAMES:
        if not any(name in ARTIFACT_DEPENDENCIES[artifact] for artifact in reused):
            comparisons.append(FingerprintComparison(
                name, FingerprintStatus.NOT_APPLICABLE, None, None,
                "no reused artifact depends on it",
            ))
            continue
        stored = stored_map.get(name)
        now = current.get(name)
        stored_value = stored.value if stored is not None else None
        current_value = now.value if now is not None else None
        if stored is None:
            status, reason = FingerprintStatus.UNVERIFIED, block_problem or "checkpoint stored no value"
        elif not stored.available:
            status, reason = FingerprintStatus.UNVERIFIED, f"unavailable when the checkpoint was saved: {stored.basis}"
        elif now is None:
            status, reason = FingerprintStatus.UNVERIFIED, "no current value was computed"
        elif not now.available:
            status, reason = FingerprintStatus.UNVERIFIED, f"unavailable now: {now.basis}"
        elif stored.basis != now.basis:
            status, reason = FingerprintStatus.UNVERIFIED, (
                f"computed on different bases (checkpoint={stored.basis!r} current={now.basis!r})"
            )
        elif stored.value == now.value:
            status, reason = FingerprintStatus.MATCH, "unchanged"
        else:
            status, reason = FingerprintStatus.CHANGED, "changed since the checkpoint was saved"
        comparisons.append(FingerprintComparison(name, status, stored_value, current_value, reason))
    return tuple(comparisons)


# ---------------------------------------------------------------- config ownership

_MODEL_IDENTITY_KEYS = ("provider", "model", "base_url", "api_key")
_AGENT_ROLES = ("architect", "planner", "reviewer", "run_verifier", "skill_gap", "spec_compliance")
# Identity leaves only. Per-role and fallback knobs (temperature, max_tokens,
# context_window, ...) stay in `config`; `llm_chain` is a list and stays in
# `config` whole (over-invalidating is the safe direction).
MODEL_RUNTIME_CONFIG_FIELDS: Tuple[Tuple[str, ...], ...] = (
    *(("llm", key) for key in _MODEL_IDENTITY_KEYS),
    *(("agent_llms", role, "llm", key) for role in _AGENT_ROLES for key in _MODEL_IDENTITY_KEYS),
)
CONTAINMENT_CONFIG_FIELDS: Tuple[Tuple[str, ...], ...] = (
    ("autonomy", "containment_backend"),
    ("autonomy", "contained_execution_required"),
    ("autonomy", "mcp_contained_execution_required"),
    ("autonomy", "sandbox_execution"),
    ("autonomy", "sandbox_cpu_seconds"),
    ("autonomy", "sandbox_memory_mb"),
    ("autonomy", "sandbox_env_allowlist"),
    ("autonomy", "acquisition_cpu_seconds"),
    ("autonomy", "acquisition_memory_mb"),
    ("autonomy", "acquisition_registry_hosts"),
    ("mcp_lifecycle",),
)
VERIFICATION_POLICY_CONFIG_FIELDS: Tuple[Tuple[str, ...], ...] = (
    ("autonomy", "run_verification_enabled"),
    ("autonomy", "run_verification_timeout_seconds"),
    ("autonomy", "brownfield_baseline_target_test"),
    ("autonomy", "brownfield_full_regression_baseline_policy"),
    ("autonomy", "spec_compliance_enabled"),
    ("process_profiles", "enforce_verification_depth"),
)
CONFIG_FIELD_OWNERS: Dict[Tuple[str, ...], str] = {
    **{path: "model_runtime" for path in MODEL_RUNTIME_CONFIG_FIELDS},
    **{path: "containment" for path in CONTAINMENT_CONFIG_FIELDS},
    **{path: "verification_policy" for path in VERIFICATION_POLICY_CONFIG_FIELDS},
}

_ABSENT = object()


def split_config_by_owner(config_dump: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{"config": remainder, owner: {"a.b": value}} - each leaf in one bucket."""
    remainder = copy.deepcopy(dict(config_dump))
    owned: Dict[str, Dict[str, Any]] = {
        "config": remainder, "model_runtime": {}, "containment": {}, "verification_policy": {},
    }
    for path, owner in CONFIG_FIELD_OWNERS.items():
        parent: Any = remainder
        for key in path[:-1]:
            parent = parent.get(key) if isinstance(parent, dict) else None
        value = parent.pop(path[-1], _ABSENT) if isinstance(parent, dict) else _ABSENT
        owned[owner][".".join(path)] = None if value is _ABSENT else value
    return owned


# ---------------------------------------------------------------- computation

def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def workspace_fingerprint(workspace_path: str) -> Fingerprint:
    from kriya.workflow.checkpoint import compute_base_commit, compute_workspace_content_hash

    content = compute_workspace_content_hash(workspace_path)
    if content is None:
        return Fingerprint.unavailable(
            "workspace content identity could not be computed (not a git repository, or git unavailable)"
        )
    return Fingerprint(
        _digest({"content": content, "base": compute_base_commit(workspace_path)}),
        "git-worktree-content+HEAD",
    )


def ledger_fingerprint(ledger: Any) -> Fingerprint:
    """A run with no caller ledger starts from an empty one (workflow.py), so
    ``None`` fingerprints exactly like an empty ledger."""
    if ledger is None:
        from kriya.workflow.obligations import ObligationLedger
        ledger = ObligationLedger()
    try:
        revision, content = ledger.fingerprint()
    except Exception as error:
        return Fingerprint.unavailable(f"obligation ledger fingerprint failed: {type(error).__name__}")
    return Fingerprint(f"{revision}:{content}", "obligation-ledger")


_SKILL_IGNORED_DIRS = frozenset({"__pycache__", ".git"})
# Staged rules are proposals, never loaded as skills until `kriya skills
# approve`; Finder/Explorer metadata changes behind anyone's back. Other
# dotfiles (e.g. .skill_conflicts.json) do affect behaviour and are hashed.
_SKILL_IGNORED_FILES = frozenset({"staged_rules.txt", ".DS_Store", "Thumbs.db", "desktop.ini"})


def skills_fingerprint(skill_source_dirs: Optional[Sequence[str]]) -> Fingerprint:
    if skill_source_dirs is None:
        return Fingerprint.unavailable("skill sources unknown (skill engine exposes no source directories)")
    sources = []
    try:
        for directory in skill_source_dirs:
            entry: Dict[str, Any] = {"dir": os.path.realpath(directory), "files": []}
            if os.path.isdir(directory):
                for root, dirnames, filenames in os.walk(directory):
                    dirnames[:] = sorted(d for d in dirnames if d not in _SKILL_IGNORED_DIRS)
                    for filename in sorted(filenames):
                        if filename in _SKILL_IGNORED_FILES or filename.endswith(".pyc"):
                            continue
                        path = os.path.join(root, filename)
                        with open(path, "rb") as handle:
                            digest = hashlib.sha256(handle.read()).hexdigest()
                        entry["files"].append([os.path.relpath(path, directory), digest])
            else:
                entry["absent"] = True
            sources.append(entry)
    except OSError as error:
        return Fingerprint.unavailable(f"skill sources unreadable: {error}")
    return Fingerprint(_digest(sources), "skill-source-content")


@functools.lru_cache(maxsize=1)
def kriya_runtime_fingerprint() -> Fingerprint:
    """The running Kriya implementation: every package source file (not
    bytecode, which Python writes at import time) plus the distribution
    version. Computed once per process - it identifies the code that was
    imported, so a later on-disk edit must not change it mid-run."""
    import kriya

    package_dir = os.path.dirname(os.path.abspath(kriya.__file__))
    files = []
    try:
        for root, dirnames, filenames in os.walk(package_dir):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
            for filename in sorted(filenames):
                # Source and package data only: bytecode is written at import
                # time and dotfiles (.DS_Store) change outside Kriya, either of
                # which would make every cross-process resume read CHANGED.
                if filename.startswith(".") or filename.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(root, filename)
                with open(path, "rb") as handle:
                    files.append([os.path.relpath(path, package_dir), hashlib.sha256(handle.read()).hexdigest()])
    except OSError as error:
        return Fingerprint.unavailable(f"Kriya package sources unreadable: {error}")
    try:
        from importlib.metadata import version
        dist_version: Optional[str] = version("kriya")
    except Exception:
        dist_version = None
    return Fingerprint(_digest({"version": dist_version, "files": files}), "kriya-package-source")


def authority_context_fingerprint(
    *,
    allowed_write_relpaths: Optional[Iterable[str]],
    authorized_semantic_regions: Optional[Iterable[Any]],
    write_scope_mode: Any,
    protected_source_file: Optional[str],
) -> Fingerprint:
    """Proposal scope, mutation envelope and D1 authority inputs. A region's
    ``source`` is audit-only provenance and deliberately excluded."""
    regions = sorted(
        (
            [
                getattr(region, "relpath", None),
                getattr(getattr(region, "region_type", None), "value", None),
                getattr(region, "member_key", None),
                getattr(region, "successor_key", None),
            ]
            for region in (authorized_semantic_regions or [])
        ),
        key=lambda region: json.dumps(region),
    )
    return Fingerprint(_digest({
        "allowed_write_relpaths": (
            sorted(allowed_write_relpaths) if allowed_write_relpaths is not None else None
        ),
        "authorized_semantic_regions": regions,
        "write_scope_mode": getattr(write_scope_mode, "value", write_scope_mode),
        "protected_source_file": protected_source_file,
    }), "authority-context")


def compute_resume_fingerprints(
    *,
    workspace_path: str,
    config_dump: Mapping[str, Any],
    goal_inputs: Mapping[str, Any],
    approved_plan_inputs: Mapping[str, Any],
    input_obligation_ledger: Any,
    effective_obligation_ledger: Any,
    skill_source_dirs: Optional[Sequence[str]],
    authority_inputs: Mapping[str, Any],
    verification_inputs: Mapping[str, Any],
    workspace: Optional[Fingerprint] = None,
    input_obligation_fingerprint: Optional[Fingerprint] = None,
    effective_obligation_fingerprint: Optional[Fingerprint] = None,
    toolchain: Optional[Fingerprint] = None,
    model_runtime: Optional[Fingerprint] = None,
) -> Dict[str, Fingerprint]:
    """Every fingerprint in FINGERPRINT_NAMES. ``workspace`` and
    ``input_obligation_fingerprint`` may be passed precomputed: a run fixes
    both at entry (the caller's ledger is the same object the run then grows
    into its effective ledger, so recomputing it later would lose the
    input/effective distinction)."""
    owned = split_config_by_owner(config_dump)
    return {
        "workspace": workspace if workspace is not None else workspace_fingerprint(workspace_path),
        "config": Fingerprint(_digest(owned["config"]), "config-excluding-owned-fields"),
        "goal": Fingerprint(_digest(dict(goal_inputs)), "goal-inputs"),
        "approved_plan": Fingerprint(_digest(dict(approved_plan_inputs)), "approved-plan-inputs"),
        "input_obligation_ledger": (
            input_obligation_fingerprint if input_obligation_fingerprint is not None
            else ledger_fingerprint(input_obligation_ledger)
        ),
        "effective_obligation_ledger": (
            effective_obligation_fingerprint if effective_obligation_fingerprint is not None
            else ledger_fingerprint(effective_obligation_ledger)
        ),
        "skills": skills_fingerprint(skill_source_dirs),
        "model_runtime": (
            model_runtime if model_runtime is not None
            else Fingerprint.unavailable("no model runtime identity was supplied")
        ),
        "containment": Fingerprint(_digest(owned["containment"]), "containment-config"),
        "toolchain": toolchain if toolchain is not None else toolchain_fingerprint(),
        "verification_policy": Fingerprint(
            _digest({"config": owned["verification_policy"], "run": dict(verification_inputs)}),
            "verification-policy",
        ),
        "authority_context": authority_context_fingerprint(**authority_inputs),
        "kriya_runtime": kriya_runtime_fingerprint(),
    }


def model_runtime_resume_fingerprint(config: Any) -> Fingerprint:
    """PRD-013: the exact runtimes of every model a production role can call
    (the Developer's llm + llm_chain, each agent_llms binding), plus the
    config fields this owner owns. Available only when EVERY runtime is
    exact; otherwise UNAVAILABLE, which never matches."""
    from kriya.core.model_qualification import role_models
    from kriya.core.model_runtime import resolve_configured_model_runtime

    models = list(dict.fromkeys(m for chain in role_models(config).values() for m in chain))
    digests = {}
    for model in models:
        fingerprint = resolve_configured_model_runtime(config, model)
        if not fingerprint.exact:
            return Fingerprint.unavailable(
                f"model runtime identity of {model!r} is not exact "
                f"({'; '.join(fingerprint.probe_errors) or ', '.join(fingerprint.missing_components)})"
            )
        digests[model.casefold()] = fingerprint.digest
    owned = split_config_by_owner(config.model_dump())["model_runtime"]
    return Fingerprint(_digest({"runtimes": digests, "config": owned}), "model-runtime")


def _declaration_mutable(write_scope_mode: Any, allowed_write_relpaths: Any, structured_plan: Any) -> bool:
    from kriya.workflow.toolchain import toolchain_declaration_mutable

    return toolchain_declaration_mutable(write_scope_mode, allowed_write_relpaths, structured_plan)


def toolchain_fingerprint(
    workspace_path: Optional[str] = None, autonomy_cfg: Any = None, *, goal: Optional[str] = None,
    candidate_files: Optional[Mapping[str, str]] = None, declaration_mutable: bool = False,
) -> Fingerprint:
    """The toolchain identity every reuse decision compares (direct resume
    and milestone VERIFIED_NO_CHANGE alike).

    Bound only where PRD-011 makes it provable: with contained execution
    required, the verification toolchain PolymorphicValidator selects - the
    repository's baseline declaration, a goal-stated JDK exactly as the
    attempt applies it, and, for a checkpointed candidate
    (``candidate_files``), the candidate's own toolchain declaration, i.e.
    the target of an authorized toolchain migration - plus that image's
    immutable local content digest. Deterministic from those inputs and
    side-effect free (inspects, never pulls or runs), so a checkpoint and
    its resume compute it identically. The same content digest always
    attests the same runtime, so observed versions add nothing. Host
    execution, an unresolvable or conflicting requirement, or an absent image
    is UNAVAILABLE, which every reuse decision treats as UNVERIFIED."""
    if workspace_path is None or getattr(autonomy_cfg, "contained_execution_required", False) is not True:
        return Fingerprint.unavailable("host toolchain has no attested identity (containment not required)")
    import shutil
    import tempfile

    from kriya.tools.containment import ContainmentSetupError
    from kriya.tools.containment_oci import local_image_content_digest
    from kriya.tools.toolchain_identity import ALL_TOOLCHAIN_DECLARATION_FILES
    from kriya.tools.validate import PolymorphicValidator

    declarations = {
        path: text for path, text in (candidate_files or {}).items() if path in ALL_TOOLCHAIN_DECLARATION_FILES
    }
    overlay = None
    try:
        stack = PolymorphicValidator(workspace_path).stack
        if declarations:
            # The candidate's toolchain declaration over the baseline's.
            overlay = tempfile.mkdtemp(prefix="kriya-toolchain-")
            for name in ALL_TOOLCHAIN_DECLARATION_FILES:
                source = os.path.join(workspace_path, name)
                if name in declarations:
                    with open(os.path.join(overlay, name), "w", encoding="utf-8") as stream:
                        stream.write(declarations[name])
                elif os.path.isfile(source):
                    shutil.copyfile(source, os.path.join(overlay, name))
        from kriya.tools.toolchain_identity import resolve_toolchain_selection

        override = None
        if goal and stack == "java":
            from kriya.workflow.toolchain import _resolve_java_home_override

            override = _resolve_java_home_override(goal)
        identity = resolve_toolchain_selection(
            overlay or workspace_path, stack, baseline_path=workspace_path if overlay else None,
            java_home_override=override, declaration_mutable=declaration_mutable,
        )
    except ContainmentSetupError as exc:
        return Fingerprint.unavailable(f"toolchain requirement unresolvable: {exc}")
    finally:
        if overlay is not None:
            shutil.rmtree(overlay, ignore_errors=True)
    if identity is None:
        return Fingerprint.unavailable(f"no versioned toolchain profile for stack {stack!r}")
    digest = local_image_content_digest(identity.containment_image)
    if digest is None:
        return Fingerprint.unavailable(f"toolchain image {identity.containment_image!r} is not present locally")
    declared = identity.to_dict()
    for evidence_only in ("image_digest", "observed_runtime_version", "observed_build_tool_version"):
        declared.pop(evidence_only, None)
    return Fingerprint(_digest({"declared": declared, "image_digest": digest}), "contained-toolchain-image")


def generation_resume_fingerprints(
    config: Any,
    workspace_path: str,
    *,
    goal: str,
    error_context: Optional[str] = None,
    supplementary_context: str = "",
    recovery_contract_block: str = "",
    execution_scope: str = "",
    grounding_goal: str = "",
    established_files: Optional[Iterable[str]] = None,
    predetermined_plan: Optional[str] = None,
    predetermined_design: Optional[str] = None,
    predetermined_architect_files: Optional[Iterable[str]] = None,
    structured_plan: Any = None,
    current_subtask_id: Optional[str] = None,
    completed_subtask_ids: Optional[Iterable[str]] = None,
    obligation_ledger: Any = None,
    effective_obligation_ledger: Any = None,
    skill_engine_override: Any = None,
    allowed_write_relpaths: Optional[Iterable[str]] = None,
    authorized_semantic_regions: Optional[Iterable[Any]] = None,
    write_scope_mode: Any = None,
    protected_source_file: Optional[str] = None,
    required_verification: Any = None,
    runtime_verification_required: Optional[bool] = None,
    strict_spec_compliance: bool = False,
    strict_dependency_index: bool = False,
    workspace: Optional[Fingerprint] = None,
    input_obligation_fingerprint: Optional[Fingerprint] = None,
    effective_obligation_fingerprint: Optional[Fingerprint] = None,
    candidate_files: Optional[Mapping[str, str]] = None,
) -> Dict[str, Fingerprint]:
    """The fingerprints of one run_generation_workflow() call, from its own
    arguments (same names, same defaults). The workflow uses this both to
    validate a checkpoint and to save one, so the two cannot diverge.
    ``effective_obligation_ledger`` None means the run's starting ledger."""
    if skill_engine_override is None:
        from kriya.skills.skill import SkillEngine

        skill_dirs = SkillEngine.from_config(config, workspace_path=workspace_path).skills_dirs
    else:
        skill_dirs = getattr(skill_engine_override, "skills_dirs", None)
    return compute_resume_fingerprints(
        workspace_path=workspace_path,
        workspace=workspace,
        input_obligation_fingerprint=input_obligation_fingerprint,
        effective_obligation_fingerprint=effective_obligation_fingerprint,
        config_dump=config.model_dump(),
        goal_inputs={
            "goal": goal,
            "error_context": error_context or "",
            "supplementary_context": supplementary_context,
            "recovery_contract_block": recovery_contract_block,
            "execution_scope": execution_scope,
            "grounding_goal": grounding_goal,
            "established_files": sorted(established_files or []),
        },
        approved_plan_inputs={
            "predetermined_plan": predetermined_plan,
            "predetermined_design": predetermined_design,
            "predetermined_architect_files": (
                list(predetermined_architect_files) if predetermined_architect_files is not None else None
            ),
            "structured_plan": structured_plan.content_hash() if structured_plan is not None else None,
            "current_subtask_id": current_subtask_id,
            "completed_subtask_ids": sorted(completed_subtask_ids or ()),
        },
        input_obligation_ledger=obligation_ledger,
        effective_obligation_ledger=(
            effective_obligation_ledger if effective_obligation_ledger is not None else obligation_ledger
        ),
        skill_source_dirs=skill_dirs,
        model_runtime=model_runtime_resume_fingerprint(config),
        toolchain=toolchain_fingerprint(
            workspace_path, config.autonomy, goal=goal, candidate_files=candidate_files,
            declaration_mutable=_declaration_mutable(write_scope_mode, allowed_write_relpaths, structured_plan),
        ),
        authority_inputs={
            "allowed_write_relpaths": allowed_write_relpaths,
            "authorized_semantic_regions": authorized_semantic_regions,
            "write_scope_mode": write_scope_mode,
            "protected_source_file": protected_source_file,
        },
        verification_inputs={
            "required_verification": required_verification,
            "runtime_verification_required": runtime_verification_required,
            "strict_spec_compliance": strict_spec_compliance,
            "strict_dependency_index": strict_dependency_index,
        },
    )


__all__ = [
    "ARTIFACT_DEPENDENCIES", "ARTIFACT_STAGE", "CANDIDATE_CHECKPOINT_STAGES", "CANDIDATE_HASH_KEY",
    "CHECKPOINT_KEY", "CONFIG_FIELD_OWNERS", "DERIVATION_CHAIN", "EFFECTIVE_LEDGER_KEY", "FINGERPRINT_NAMES",
    "ResumePlan", "build_resume_plan", "candidate_integrity_problem", "candidate_snapshot_digest",
    "restore_effective_ledger", "Fingerprint", "FingerprintComparison",
    "FingerprintStatus", "RESUME_FINGERPRINT_SCHEMA_VERSION", "STAGE_ORDER", "UNAVAILABLE",
    "authority_context_fingerprint", "compare_resume_fingerprints", "compute_resume_fingerprints",
    "fingerprint_block", "generation_resume_fingerprints", "invalidated_stages_for", "kriya_runtime_fingerprint", "ledger_fingerprint",
    "parse_fingerprint_block", "reused_artifacts_for_checkpoint", "skills_fingerprint",
    "split_config_by_owner", "toolchain_fingerprint", "workspace_fingerprint",
]
