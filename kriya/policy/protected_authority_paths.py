"""The ONE canonical set of host paths that hold Kriya AUTHORITY state (D2 EXEC-LAYER rebuild, slice S1; owner rulings 2026-10-24).

Authority state is what Kriya later trusts without re-deriving it: a workload that can read or write it can forge or leak what the control
plane believes. A capability grant (a workspace mount, later an MCP ``additional_*_paths`` entry) authorizes access to files, never to
authority state, so every consumer that exposes host paths to a contained workload asks THIS module which paths are protected instead of
keeping its own list. In S1 the consumer is the OCI backend's workspace mount (``kriya/tools/containment_oci.py``); the global homes below
are consumed by the MCP slice (S5) and are pure data until then.

The set (extending it is an explicit, owner-approved change here, so every consumer follows):

- ``<workspace>/.kriya/control`` - RunRecords, commit evidence, run lock state (``kriya/control/persistence.py``);
- ``<workspace>/.kriya/checkpoints`` - checkpoints (``kriya/control/checkpoint.py``);
- the operator's ``KRIYA_TRUST_FILE`` when set (a file);
- every home in ``AUTHORITY_HOMES`` - a DIRECTORY, because each is a store of per-workspace (or per-identity) records and a protection of
  only the active workspace's file would leave every other workspace's approvals, waivers and records open. Each home is classified from
  its writer and reader (``AuthorityHome.basis``), never from its name or its place under ``~/.kriya``.

NOT authority (``NON_AUTHORITY_HOMES``, each with the reason): the state and log directories. ``~/.kriya`` as a whole is never classified.

``<workspace>/.kriya`` ITSELF is not in this set: it is the STRUCTURAL ANCHOR (``WORKSPACE_ANCHOR_RELPATH``) of a writable workspace mount,
bound read-write onto itself by the OCI backend so it cannot be renamed or replaced (``kriya/tools/containment_oci.py::workspace_anchor_args``).
The semantic set above and the structural anchor are deliberately separate: the anchor is broader than the authority it protects, because
renaming it would defeat the descendants' masks.

Limit, by construction: a path that a per-workspace CONFIGURATION value overrides (for example ``static_analysis.waivers.store``) cannot be
enumerated globally; the set resolves the environment-variable homes of the running process and the active workspace only. A home that
resolves inside the workspace is refused for the authority home (SEC-009) and is otherwise an open characterization item.

Deliberately NOT the authority definition: the candidate-write policy (``kriya/policy/filesystem.py::TRUSTED_CONTROL_DIRECTORIES``) refuses a
candidate's write to ``.git`` and ``.kriya`` as a whole - a different question (what a model may write) from this one (what a container must
not be able to forge or read).

Pure and read-only: ``realpath``/``stat`` only, nothing is created. Paths are canonical (real) paths; relationships between a protected path
and a grant are decided by the caller with ``kriya.platform.filesystem_semantics`` (filesystem identity, not string prefixes). A path that does
not exist yet is still listed: whether a missing protected path can be left unmasked depends on whether the exposing mount is writable, which
only the caller knows.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from kriya.config import environment as env_module
from kriya.config.authority_approval import TrustPathInsideWorkspaceError, validate_trust_path_outside_workspace
from kriya.config.environment import Environment, expand_user, trust_file
from kriya.platform.filesystem_semantics import PathRelation, path_relation

# The workspace-relative authority directories. `kriya/control/persistence.py::_CONTROL_DIR` and `kriya/control/checkpoint.py::CHECKPOINT_DIR`
# own the same values; tests/architecture/test_protected_authority_paths.py pins the three together so a rename in the owner fails a test
# instead of silently unprotecting the old name.
WORKSPACE_ANCHOR_RELPATH = ".kriya"  # the structural anchor: the Kriya-owned metadata root whose rename would defeat the descendants below
WORKSPACE_CONTROL_RELPATH = os.path.join(".kriya", "control")
WORKSPACE_CHECKPOINTS_RELPATH = os.path.join(".kriya", "checkpoints")
WORKSPACE_AUTHORITY_RELPATHS = (WORKSPACE_CONTROL_RELPATH, WORKSPACE_CHECKPOINTS_RELPATH)

LABEL_WORKSPACE_CONTROL = "workspace_control"
LABEL_WORKSPACE_CHECKPOINTS = "workspace_checkpoints"
LABEL_TRUST_FILE = "config_trust_file"

# Authority classes of a home (closed): a store whose records authorize something, and a store whose records are the result truth that later
# runs, reports or releases accept.
DECISION_AUTHORITY = "decision_authority"
RESULT_TRUTH = "result_truth"


@dataclass(frozen=True)
class AuthorityHome:
    label: str
    variable: str  # the environment variable that overrides it (kriya/config/environment.py)
    resolve: Callable[[Optional[Environment]], str]  # the environment owner's function, so the override and the default are the owner's
    authority_class: str
    basis: str  # the writer and reader that make it authority (traced 2026-10-24)


AUTHORITY_HOMES: Tuple[AuthorityHome, ...] = (
    AuthorityHome("authority_home", env_module.AUTHORITY_HOME, env_module.authority_home, DECISION_AUTHORITY,
                  "SEC-009 configuration approvals (kriya/config/authority_approval.py) and the human-decision store - launch records, review "
                  "decisions (kriya/control/human_decision_store.py): read to decide whether a configuration or a launch is authorized"),
    AuthorityHome("mcp_approvals_home", env_module.MCP_APPROVAL_HOME, env_module.mcp_approval_home, DECISION_AUTHORITY,
                  "TOOL-002 MCP invocation approvals (kriya/mcp/invocation_approval.py): read to decide whether a tool call may run"),
    AuthorityHome("static_analysis_waivers_home", env_module.STATIC_ANALYSIS_HOME, env_module.static_analysis_home, DECISION_AUTHORITY,
                  "PRD-031A waivers (kriya/static_analysis/waivers.py): read to decide that a finding no longer blocks"),
    AuthorityHome("qualifications_home", env_module.QUALIFICATION_HOME, env_module.qualification_home, RESULT_TRUTH,
                  "PRD-014 model qualification records (kriya/core/model_qualification.py): read to decide whether a runtime is qualified"),
    AuthorityHome("certifications_home", env_module.CERTIFICATION_HOME, env_module.certification_home, RESULT_TRUTH,
                  "PRD-035/036 live certification records and release streaks (kriya/core/model_certification.py): sealed by an unkeyed digest"),
    AuthorityHome("adjudications_home", env_module.ADJUDICATION_HOME, env_module.adjudication_home, RESULT_TRUTH,
                  "PRD-033 trusted run-adjudication store (kriya/metrics/adjudication.py): the only record of false success / regression "
                  "escape, sealed by an unkeyed digest"),
)

# Durable Kriya locations that are NOT authority, each with the reason. A variable of the environment owner that ends in _HOME, _DIR or
# _FILE must appear here or in AUTHORITY_HOMES (tests/architecture/test_protected_authority_paths.py), so a new home is classified on purpose.
NON_AUTHORITY_HOMES: Dict[str, str] = {
    env_module.STATE_DIR: "run state, indexes, learned-knowledge database and traces: derived or untrusted-fenced content, re-derivable, never a verdict",
    env_module.LOG_DIR: "diagnostic logs: observation only, no reader takes a decision from them",
}

PROTECTED_AUTHORITY_LABELS = frozenset({
    LABEL_WORKSPACE_CONTROL, LABEL_WORKSPACE_CHECKPOINTS, LABEL_TRUST_FILE, *(home.label for home in AUTHORITY_HOMES),
})


class ProtectedAuthorityPathsUnresolvable(RuntimeError):
    """The protected set cannot be established (the SEC-009 authority home resolves inside the workspace, which SEC-009 itself
    refuses). A consumer must refuse to expose host paths rather than guess where the approval store is."""


@dataclass(frozen=True)
class ProtectedAuthorityPath:
    label: str
    host_path: str  # canonical (real) path; it may not exist yet


def _nested_in_another(index: int, found: List[ProtectedAuthorityPath]) -> bool:
    """True when entry ``index`` lies inside (or is the same filesystem object as) an EARLIER-or-parent entry: a mask for it would be a mount
    inside the mask of its parent, whose empty scratch directory has no such path. An identity that cannot be established keeps the entry."""
    me = found[index].host_path
    for other_index, other in enumerate(found):
        if other_index == index:
            continue
        if path_relation(other.host_path, me) is PathRelation.WITHIN:
            if path_relation(me, other.host_path) is not PathRelation.WITHIN or other_index < index:  # strictly inside, or the same object listed first
                return True
    return False


def protected_authority_paths(workspace_root: str, *, environment: Optional[Environment] = None) -> Tuple[ProtectedAuthorityPath, ...]:
    """Every protected authority path for ``workspace_root``, in a fixed order, with no entry nested inside another. Raises
    ``ProtectedAuthorityPathsUnresolvable`` when the SEC-009 authority home cannot be located outside the workspace - a caller must not
    guess where it is."""
    root = os.path.realpath(workspace_root)
    found: List[ProtectedAuthorityPath] = [
        ProtectedAuthorityPath(LABEL_WORKSPACE_CONTROL, os.path.join(root, WORKSPACE_CONTROL_RELPATH)),
        ProtectedAuthorityPath(LABEL_WORKSPACE_CHECKPOINTS, os.path.join(root, WORKSPACE_CHECKPOINTS_RELPATH)),
    ]
    for home in AUTHORITY_HOMES:
        directory = os.path.realpath(home.resolve(environment))  # the owner's function; each owner realpaths exactly this
        if home.variable == env_module.AUTHORITY_HOME:
            try:
                validate_trust_path_outside_workspace(os.path.join(directory, "x.json"), root)
            except TrustPathInsideWorkspaceError as error:
                raise ProtectedAuthorityPathsUnresolvable(str(error)) from error
        found.append(ProtectedAuthorityPath(home.label, directory))
    explicit = trust_file(environment)
    if explicit:
        found.append(ProtectedAuthorityPath(LABEL_TRUST_FILE, os.path.realpath(expand_user(explicit))))
    return tuple(entry for index, entry in enumerate(found) if not _nested_in_another(index, found))
