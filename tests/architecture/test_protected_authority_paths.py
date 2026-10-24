"""D2 EXEC-LAYER S1: ONE canonical set of protected authority paths (kriya/policy/protected_authority_paths.py).

The names of the workspace authority directories are owned by ``kriya/control`` (STATE); the canonical set repeats them because AUTHORITY may not
import that module. These tests pin the copies together, so renaming a control directory fails here instead of silently unprotecting the old
name, pin the OCI backend to the canonical set so it keeps no list of its own, and make a new Kriya home on the environment owner an explicit,
classified decision (authority or not) instead of a silent omission.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

from kriya.control import checkpoint, control_store, persistence
from kriya.policy import protected_authority_paths as canonical
from kriya.repository import commit
from kriya.tools import containment_oci

REPO = Path(__file__).resolve().parents[2]


def test_the_canonical_workspace_relpaths_equal_the_names_their_owners_use():
    assert canonical.WORKSPACE_CONTROL_RELPATH == persistence._CONTROL_DIR  # pylint: disable=protected-access
    assert canonical.WORKSPACE_CHECKPOINTS_RELPATH == checkpoint.CHECKPOINT_DIR
    assert commit._COMMIT_EVIDENCE_RELATIVE_DIR.startswith(canonical.WORKSPACE_CONTROL_RELPATH), (  # pylint: disable=protected-access
        "commit evidence must stay beneath a protected directory")


def test_the_structural_anchor_name_is_the_name_the_control_store_and_the_environment_use():
    from kriya.config.environment import KRIYA_DIRECTORY_NAME
    assert canonical.WORKSPACE_ANCHOR_RELPATH == control_store.CONTROL_DIRECTORY == KRIYA_DIRECTORY_NAME
    assert all(rel.startswith(canonical.WORKSPACE_ANCHOR_RELPATH + os.sep) for rel in canonical.WORKSPACE_AUTHORITY_RELPATHS)
    assert canonical.WORKSPACE_ANCHOR_RELPATH not in canonical.WORKSPACE_AUTHORITY_RELPATHS, (
        "the anchor is structural, not a semantic authority leaf: masking it would hide the ordinary writes beneath it")


def test_the_oci_workspace_mask_uses_the_canonical_relpaths_not_a_list_of_its_own():
    assert containment_oci._AUTHORITY_STATE_RELPATHS is canonical.WORKSPACE_AUTHORITY_RELPATHS  # pylint: disable=protected-access


def test_the_labels_are_a_closed_table_matching_what_the_function_can_return():
    assert canonical.PROTECTED_AUTHORITY_LABELS == {
        canonical.LABEL_WORKSPACE_CONTROL, canonical.LABEL_WORKSPACE_CHECKPOINTS, canonical.LABEL_TRUST_FILE,
        "authority_home", "mcp_approvals_home", "static_analysis_waivers_home", "qualifications_home", "certifications_home",
        "adjudications_home",
    }


def _names_used(relative: str) -> set:
    tree = ast.parse((REPO / relative).read_text(encoding="utf-8"))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def _environment_functions() -> dict:
    tree = ast.parse((REPO / "kriya/config/environment.py").read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _calls_kriya_home(node: ast.FunctionDef) -> bool:
    return any(isinstance(call, ast.Call) and getattr(call.func, "id", None) == "kriya_home" for call in ast.walk(node))


def test_every_home_owner_function_of_the_environment_module_is_classified():
    """A new ``*_home()`` / default-directory owner (any environment function that builds a path under ``~/.kriya``) fails here until it is
    listed as authority (AUTHORITY_HOMES) or as not-authority (NON_AUTHORITY_HOMES) on purpose."""
    owners = {name for name, node in _environment_functions().items() if _calls_kriya_home(node)}
    classified = {home.resolve.__name__ for home in canonical.AUTHORITY_HOMES} | {"default_state_dir", "default_log_dir"}
    assert owners == classified


def test_every_directory_or_file_variable_of_the_closed_vocabulary_is_classified():
    from kriya.config import environment
    located = {name for name in environment.VARIABLES if name.endswith(("_HOME", "_DIR", "_FILE"))
               and name not in ("HOME", "GRADLE_USER_HOME")}  # toolchain variables are the OS/toolchain's, not Kriya state
    classified = {home.variable for home in canonical.AUTHORITY_HOMES} | set(canonical.NON_AUTHORITY_HOMES) | {environment.TRUST_FILE}
    assert located == classified


def test_no_module_other_than_the_environment_owner_builds_a_kriya_home():
    offenders = []
    for path in sorted((REPO / "kriya").rglob("*.py")):
        relative = path.relative_to(REPO).as_posix()
        if relative == "kriya/config/environment.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "kriya_home":
                offenders.append(relative)
    assert offenders == []


def test_each_authority_home_is_classified_with_its_writer_and_reader_and_is_never_the_whole_kriya_home():
    assert {home.authority_class for home in canonical.AUTHORITY_HOMES} <= {canonical.DECISION_AUTHORITY, canonical.RESULT_TRUTH}
    assert all(len(home.basis) > 40 for home in canonical.AUTHORITY_HOMES)
    assert len({home.label for home in canonical.AUTHORITY_HOMES}) == len(canonical.AUTHORITY_HOMES)
    assert all(reason for reason in canonical.NON_AUTHORITY_HOMES.values())
    from kriya.config.environment import kriya_home
    resolved = {os.path.realpath(home.resolve(None)) for home in canonical.AUTHORITY_HOMES}
    assert os.path.realpath(kriya_home()) not in resolved, "~/.kriya as a whole must never be classified as authority"


def test_the_oci_backend_keeps_no_list_of_homes_of_its_own():
    names = _names_used("kriya/tools/containment_oci.py")
    assert not {"authority_home", "mcp_approval_home", "static_analysis_home", "qualification_home",
                "certification_home", "adjudication_home"} & names
