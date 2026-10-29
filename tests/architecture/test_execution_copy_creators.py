"""D2 S3 defect fix: a CLOSED inventory of every production function that creates a directory (copy, export, scratch) Kriya may run a validator or tool over, and
what each is. A git-less copy of a project is ownable only through its CREATOR's ``declare_execution_copy``; this fails when a creator loses its declaration, when
the declaration is called anywhere else, when a downstream ``execution_copy=`` keyword reappears, and when a new copy-making function is added unclassified.
"""
from __future__ import annotations

import ast
from typing import Dict, Set, Tuple

from .execution_sites import REPO, ROOTS

CREATES = {"copytree", "mkdtemp", "TemporaryDirectory", "export_base", "copy_bound_tree"}   # R5/R6: copy_bound_tree is the one copy of the bound tree (kriya/tools/gate_view.py)

# (file, function) -> (kind, why)
CREATOR_DECLARES = "CREATOR_DECLARES"        # makes a git-less copy bound to the run's original checkout: MUST call declare_execution_copy after its factory returns
REPLAY_NO_METADATA = "REPLAY_NO_METADATA"    # git-less copy whose original IS the copy: no Git metadata is ever bound, nothing to own (pinned by test_execution_copy_creator_provenance)
KEEPS_GIT = "KEEPS_GIT"                      # a copy that keeps its .git: a Git work tree, not git-less
OWN_REPOSITORY = "OWN_REPOSITORY"            # builds its own repository (it has a .git)
VALIDATOR_OWNED = "VALIDATOR_OWNED"          # a tree the validator itself creates and registers (view, tooling directory) or a probe in a neutral directory
NOT_A_VALIDATOR_TREE = "NOT_A_VALIDATOR_TREE"  # scratch/export/probe directories no validator executes a project in
SITES: Dict[Tuple[str, str], str] = {
    ("kriya/workflow/acceptance_jvm.py", "run_java_acceptance"): CREATOR_DECLARES,
    ("kriya/workflow/authority_bundle.py", "run_authority_bundle"): CREATOR_DECLARES,
    ("kriya/workflow/named_test_oracle.py", "judge_named_tests"): CREATOR_DECLARES,             # copytree-free: export_base + TemporaryDirectory
    ("kriya/workflow/deterministic_failure_diagnostic.py", "replay_deterministic_verification_against_baseline"): REPLAY_NO_METADATA,
    ("kriya/workflow/deterministic_failure_diagnostic.py", "_copy_baseline_workspace"): NOT_A_VALIDATOR_TREE,          # R6: fills the directory its two replay callers created (classified above/below)
    ("kriya/workflow/regression_attribution.py", "_replay_test_ids_against_workspace"): REPLAY_NO_METADATA,
    ("kriya/workflow/workflow.py", "exported"): KEEPS_GIT,                                          # the baseline-authority export keeps .git (ignore list below)
    ("kriya/workflow/worktree.py", "_create_scoped_snapshot_sandbox"): OWN_REPOSITORY,
    ("kriya/tools/validate.py", "_acquire_maven_tooling"): VALIDATOR_OWNED,
    ("kriya/tools/validate.py", "_stage_declared_directory"): VALIDATOR_OWNED,
    ("kriya/tools/validate.py", "check_java_toolchain"): NOT_A_VALIDATOR_TREE,
    ("kriya/core/model_qualification.py", "case_anchored_edit_protocol"): NOT_A_VALIDATOR_TREE,
    ("kriya/production_doctor.py", "_check_candidate_isolation"): NOT_A_VALIDATOR_TREE,
    ("kriya/production_doctor.py", "_check_no_host_fallback"): NOT_A_VALIDATOR_TREE,
    ("kriya/production_doctor.py", "probe_oci_containment"): NOT_A_VALIDATOR_TREE,
    ("kriya/static_analysis/adapters/semgrep.py", "_run_probe"): NOT_A_VALIDATOR_TREE,
    ("kriya/static_analysis/operator_scan.py", "run_operator_scan"): NOT_A_VALIDATOR_TREE,
    ("kriya/static_analysis/service.py", "_evaluate"): NOT_A_VALIDATOR_TREE,
    ("kriya/tools/containment_oci.py", "authority_state_mask_args"): NOT_A_VALIDATOR_TREE,
    ("kriya/tools/dependency_execution.py", "seed_gradle_distribution_from_host"): NOT_A_VALIDATOR_TREE,
    ("kriya/tools/dependency_cache.py", "create_ephemeral_cache"): NOT_A_VALIDATOR_TREE,  # R3: a dependency directory mounted into a container, never a tree a project is executed in
    ("kriya/tools/git_metadata.py", "export_git_metadata"): NOT_A_VALIDATOR_TREE,
    ("kriya/skills/run_shadow.py", "create"): NOT_A_VALIDATOR_TREE,        # the run-local copy of the skills text under the state directory; nothing runs in it
    ("kriya/workflow/context_certification.py", "run_certification"): NOT_A_VALIDATOR_TREE,
    ("kriya/workflow/resume_fingerprints.py", "toolchain_fingerprint"): NOT_A_VALIDATOR_TREE,        # a validator is built only to read its stack; nothing runs
}


def _parse(relpath: str) -> ast.AST:
    return ast.parse((REPO / relpath).read_text(encoding="utf-8"))


def _functions(tree: ast.AST) -> Dict[str, ast.AST]:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _call_name(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")


def _scan() -> Set[Tuple[str, str]]:
    found = set()
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _call_name(node) in CREATES:
                    owner = node
                    while owner in parents and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        owner = parents[owner]
                    found.add((path.relative_to(REPO).as_posix(), getattr(owner, "name", "<module>")))
    return found


def test_every_function_that_creates_a_directory_is_classified():
    scanned = _scan()
    assert scanned == set(SITES), (sorted(scanned - set(SITES)), sorted(set(SITES) - scanned))


def test_every_creator_of_a_git_less_execution_copy_declares_it_after_its_factory_returned():
    for (relpath, function), kind in SITES.items():
        if kind != CREATOR_DECLARES:
            continue
        node = _functions(_parse(relpath))[function]
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        factories = [c.lineno for c in calls if _call_name(c) in {"validator_factory", "base_validator"}]
        declares = [c.lineno for c in calls if _call_name(c) == "declare_execution_copy"]
        assert factories and declares, (relpath, function, "a creator must build its validator through a factory and declare the copy")
        assert min(declares) > min(factories), (relpath, function, "the declaration belongs after the factory returned the validator")


def test_only_the_creators_declare_and_no_downstream_keyword_exists():
    callers, keywords = [], []
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_name(node) == "declare_execution_copy":
                    owner = node
                    while owner in parents and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        owner = parents[owner]
                    callers.append((path.relative_to(REPO).as_posix(), getattr(owner, "name", "<module>")))
                if any(keyword.arg == "execution_copy" for keyword in node.keywords):
                    keywords.append(path.relative_to(REPO).as_posix())
    creators = sorted(key for key, kind in SITES.items() if kind == CREATOR_DECLARES)
    assert sorted(callers) == creators, (sorted(callers), creators)
    assert keywords == [], "a downstream factory must not declare ownership"


def test_the_classified_non_declaring_copies_really_are_what_they_are_classified_as():
    exported = _functions(_parse("kriya/workflow/workflow.py"))["exported"]
    calls = [c for c in ast.walk(exported) if isinstance(c, ast.Call)]
    assert not [c for c in calls if _call_name(c) == "ignore_patterns"], "the baseline export is the bound tree, not a name-filtered copy (R5)"
    assert [c for c in calls if _call_name(c) == "copy_bound_tree"], "the baseline export copies the bound tree through the one function"
    assert [c for c in calls if _call_name(c) == "copytree" and any(isinstance(n, ast.Name) and n.id == "git_path" for a in c.args for n in ast.walk(a))], \
        "the baseline export must keep its .git (run_java_acceptance reads the base revision from the tree it is given)"
    for relpath, function in (("kriya/workflow/deterministic_failure_diagnostic.py", "replay_deterministic_verification_against_baseline"),
                              ("kriya/workflow/regression_attribution.py", "_replay_test_ids_against_workspace")):
        node = _functions(_parse(relpath))[function]
        constructed = [c for c in ast.walk(node) if isinstance(c, ast.Call) and _call_name(c) == "PolymorphicValidator"]
        assert constructed, (relpath, function)
        for call in constructed:
            keywords = {k.arg: k.value for k in call.keywords}
            assert isinstance(keywords["original_workspace_path"], ast.Name) and keywords["original_workspace_path"].id == "temp_dir" and call.args[0].id == "temp_dir", \
                (relpath, "its original workspace must BE the copy, so no Git metadata can be bound to it")
