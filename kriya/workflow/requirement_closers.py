"""The PRD-020 / VERIFICATION-CONTRACT-003 requirement closers (full modularization W4, E4).

Moved verbatim out of ``kriya/workflow/workflow.py``: the ``close_requirements_*`` family the direct/milestone
pre-apply boundary and the enforce terminal gate (``terminal_gate_service``) run on a candidate, and the helpers only
they use (the run's mutation record, the oracle base revision, the reference/candidate test files, the frozen and
documentation referents of a sealed contract).

``kriya.workflow.workflow`` keeps every public name as a forwarder with the identical signature (tests and
``terminal_gate_service`` call those names, and tests patch ``_run_committed_paths``, ``compute_workspace_content_hash``
and ``logger`` there): the forwarder builds ONE ``CloserDeps`` from the facade's names at call time and calls the body
here, so a patch on the facade still reaches the moved code. A body that needs a collaborator takes ``deps`` as its
first parameter and binds the names it used under their old names in a prologue; a body that needs none is re-exported
by the facade unchanged. Collaborators that sit in the kernel's all-import cycle (``suite_attribution``,
``checkpoint``, ``baseline_policy``) or are not on their module's public surface (``authority_bundle``) arrive through
``CloserDeps`` (the two lazily imported modules as loaders called where the body imported them), so this module joins
neither the cycle nor the import-direction allowlist. It never imports the engine facade, the controller, the attempt
module or the CLI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance_oracle import bound_acceptance
from kriya.workflow.validation_baseline import _capture_single_baseline


@dataclass(frozen=True)
class CloserDeps:
    """What the closers use from the facade, resolved by ``kriya.workflow.workflow._closer_deps`` at call time."""

    logger: Any                                          # kriya.workflow.workflow.logger
    run_committed_paths: Callable[[str], Tuple[Optional[str], Optional[str], List[str]]]  # workflow._run_committed_paths
    compute_workspace_content_hash: Callable[[str], Optional[str]]   # kriya.workflow.checkpoint (all-import cycle)
    captured_baseline: Callable[..., Any]                # kriya.workflow.suite_attribution (all-import cycle)
    attribute_suite_result: Callable[..., Any]
    post_environment_identity: Callable[..., Any]
    load_baseline_environment_identity: Callable[[], Callable[..., Any]]   # lazy kriya.workflow.baseline_policy import
    load_authority_bundle: Callable[[], Tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]]  # lazy AUTHORITY


def _candidate_test_files(candidate_root: str) -> List[str]:
    """The runnable test files of the candidate tree (a requirement's own
    words may name them)."""
    from kriya.workflow.file_resolution import is_runnable_test_file

    test_files: List[str] = []
    for root, dirs, files in os.walk(candidate_root):
        dirs[:] = [d for d in dirs if d not in {".git", ".kriya", "node_modules", ".venv", "venv",
                                                "__pycache__", "target", "build", "dist"}]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), candidate_root)
            if is_runnable_test_file(rel):
                test_files.append(rel)
    return test_files


def named_preserved_test_files(contract: Any) -> List[str]:
    """PLAN-NAMED-TEST-IMMUTABILITY-001: the existing test files a PURE regression-preservation statement names ("Every
    existing test in tests/test_a.py must keep passing unchanged"), read from the sealed contract's named-tests binding
    (the compiler decided them from the goal's own words and the base tree). Only an entry whose single required claim is
    REGRESSION_PRESERVATION counts: a statement that also asks for a test to be added (TEST_ADDITION) or for behaviour
    (BEHAVIOR) is left to the terminal closure, which refuses a candidate-written named test whatever else it claims
    (``close_unverified_requirements_with_named_tests``). A planning input; never a model input."""
    from kriya.workflow.contract_compilation import CLOSER_NAMED_TESTS
    from kriya.workflow.requirements import REGRESSION_PRESERVATION

    if contract is None:
        return []
    pure = {entry.requirement_id for entry in getattr(contract, "entries", None) or ()
            if tuple(entry.required_claims) == (REGRESSION_PRESERVATION,)}
    return sorted({path for rid, binding in contract.binding_closers(CLOSER_NAMED_TESTS) if rid in pure
                   for path in binding.detail.get("tests") or ()})


def immutable_test_files(deps: CloserDeps, contract: Any, workspace_path: str) -> Optional[List[str]]:
    """PLAN-TEST-IMMUTABILITY-SCOPE-001: the existing test files a plan may not edit or delete, else None. Two sources:
    the tracked runnable test files of the workspace's HEAD when the sealed verification contract carries a
    TEST_IMMUTABILITY claim (the goal says existing tests must stay as they are), and - PLAN-NAMED-TEST-IMMUTABILITY-001 -
    the test files a pure regression-preservation statement names (``named_preserved_test_files``). A planning-time input
    only: the terminal gate still judges the candidate's test tree against the same base."""
    logger = deps.logger
    from kriya.repository.git import GIT_READ_FAILURES
    from kriya.workflow.file_resolution import is_runnable_test_file
    from kriya.workflow.named_test_oracle import BaseTree
    from kriya.workflow.requirements import TEST_IMMUTABILITY_CLAIM

    frozen = set(named_preserved_test_files(contract))
    entries = getattr(contract, "entries", None) or ()
    if any(TEST_IMMUTABILITY_CLAIM in getattr(entry, "required_claims", ()) for entry in entries):
        head = _workspace_head(workspace_path)
        if head:
            try:
                frozen.update(path for path in BaseTree(workspace_path, head).paths if is_runnable_test_file(path))
            except GIT_READ_FAILURES as exc:  # unreadable base: only the named files constrain planning (the terminal gate still refuses)
                logger.info(f"Immutable test files unavailable for planning: {type(exc).__name__}: {exc}")
    return sorted(frozen) or None


def _reference_test_files(deps: CloserDeps, candidate_root: str, workspace_path: str) -> Optional[List[str]]:
    """The test files that existed before this run: the real workspace's
    (untouched until apply) and the run base's. A statement naming one of
    them claims regression preservation even when the candidate deleted it.
    None when that cannot be established (an in-place candidate with no
    readable base): the caller then assumes the statement claims both."""
    logger = deps.logger
    from kriya.repository.git import GIT_READ_FAILURES
    from kriya.workflow.file_resolution import is_runnable_test_file
    from kriya.workflow.named_test_oracle import BaseTree

    in_place = os.path.realpath(candidate_root) == os.path.realpath(workspace_path)
    files = [] if in_place else _candidate_test_files(workspace_path)
    base_revision = _oracle_base_revision(deps, candidate_root, workspace_path)
    if base_revision:
        try:
            files += [path for path in BaseTree(candidate_root, base_revision).paths if is_runnable_test_file(path)]
        except GIT_READ_FAILURES as exc:  # unreadable base (git error, I/O): unknown, both claims required
            logger.info(f"Acceptance: base test files unavailable: {type(exc).__name__}: {exc}")
            return None
    elif in_place:
        return None
    return sorted(set(files))


def close_requirements_with_acceptance_tests(
    deps: CloserDeps,
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    acceptance: Any, modified: Iterable[str], revision: Any, toolchain_declaration_mutable: bool,
    tree_binding: Any = None, approval: Any = None, contract: Any = None, companion: Any = None,
    source: str = "requirement_closure.acceptance",
) -> List[Dict[str, Any]]:
    """FS-1C2 B2-a: judges the BEHAVIOR claims the operator's acceptance file
    (bound before generation; ``acceptance`` None = none was given) covers,
    by running it once, under Kriya's own runner, on the candidate at
    ``candidate_root`` - see kriya/workflow/acceptance_oracle.py. Shared by the
    direct/milestone pre-apply boundary and enforce's terminal gate, before
    the named-test closure. With no artifact it only supersedes an earlier
    acceptance judgment of the same candidate (never runs anything)."""
    from kriya.workflow.acceptance_jvm import run_java_acceptance
    from kriya.workflow.acceptance_oracle import close_requirements_with_acceptance, run_acceptance

    modified = list(modified)
    test_files = _candidate_test_files(candidate_root)
    reference = _reference_test_files(deps, candidate_root, workspace_path)

    def validator() -> PolymorphicValidator:
        built = PolymorphicValidator(
            candidate_root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg,
            toolchain_declaration_mutable=toolchain_declaration_mutable,
        )
        built.tree_binding = tree_binding
        return built

    def export_validator(root: str) -> PolymorphicValidator:
        # B2-c: a Kriya-owned copy of the candidate; its build configuration is
        # the authorized base's (trust surface checked first), so no toolchain
        # declaration may differ there.
        return PolymorphicValidator(root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg, execution_copy=True)

    base_revision = _oracle_base_revision(deps, candidate_root, workspace_path) if acceptance is not None else None

    def execute(artifact: Any) -> Any:
        if artifact.language == "java":
            return run_java_acceptance(artifact, candidate_root, candidate_paths=modified,
                                       base_revision=base_revision, validator_factory=export_validator)
        return run_acceptance(artifact, candidate_root, candidate_paths=modified, validator_factory=validator)

    from kriya.workflow.contract_closers import contract_claims_map

    return close_requirements_with_acceptance(
        ledger, requirement_set, acceptance,
        test_files=None if reference is None else sorted(set(test_files) | set(reference)),
        execute=execute, source=source, revision=revision,
        approval=approval, base_revision=base_revision, contract_claims=contract_claims_map(contract),
        companion_digests=[companion.digest] if companion is not None else (),
    )


def close_requirements_with_derived_examples(
    deps: CloserDeps,
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    engine: Any, modified: Iterable[str], revision: Any, toolchain_declaration_mutable: bool,
    tree_binding: Any = None, contract: Any = None,
) -> List[Dict[str, Any]]:
    """VERIFICATION-CONTRACT-003: the goal's compiled example module (bound by
    the contract compilation before any model call) judges the EXACT
    behaviour statements it covers, through the same B2-a boundary as the
    operator's file; the operator's artifact (if any) is its companion, so
    neither supersedes the other's records."""
    from kriya.workflow.example_oracle import bound_derived_examples

    examples = bound_derived_examples(engine)
    if examples is None:
        return []
    return close_requirements_with_acceptance_tests(deps,
        autonomy_cfg, ledger, requirement_set, candidate_root, workspace_path, acceptance=examples,
        modified=modified, revision=revision, toolchain_declaration_mutable=toolchain_declaration_mutable,
        tree_binding=tree_binding, approval=None, contract=contract, companion=bound_acceptance(engine),
        source="requirement_closure.derived_examples",
    )


def _tracked_workspace_paths(workspace_path: str) -> List[str]:
    """The paths git tracks in the workspace (the referent set of a mutation-scope
    statement at admission); empty when git cannot answer."""
    from kriya.repository.git import GIT_READ_FAILURES
    from kriya.workflow.worktree import git_read_lines

    try:
        return [path for path in git_read_lines(workspace_path, "ls-files") if path]
    except GIT_READ_FAILURES:
        return []


def _workspace_file_reader(workspace_path: str) -> Callable[[str], Optional[bytes]]:
    """A reader of tracked files for the contract compiler's documentation
    referent inspection; None for an unreadable path (fail closed there)."""

    def read(path: str) -> Optional[bytes]:
        try:
            with open(os.path.join(workspace_path, path), "rb") as handle:
                return handle.read()
        except OSError:
            return None

    return read


def _workspace_head(workspace_path: str) -> Optional[str]:
    from kriya.repository.git import GIT_READ_FAILURES
    from kriya.workflow.worktree import git_read_lines

    try:
        heads = git_read_lines(workspace_path, "rev-parse", "HEAD")
    except GIT_READ_FAILURES:
        return None
    return heads[0] if heads else None


def frozen_file_paths(contract: Any) -> Dict[str, List[str]]:
    """OD-3 (BACKEND-FINAL-CLOSURE-005): requirement id -> the tracked files its
    FILE_IMMUTABILITY claim freezes, read from the sealed contract's binding
    (the compiler decided them from the goal's words and the base tree). A
    planning constraint and the terminal closer's referent; never a model input."""
    from kriya.workflow.requirements import FILE_IMMUTABILITY

    if contract is None:
        return {}
    return {rid: list(binding.detail.get("frozen_paths") or ())
            for rid, binding in contract.binding_closers(FILE_IMMUTABILITY)}


def documentation_referent_paths(contract: Any) -> List[str]:
    """PLAN-DOCUMENTATION-UNIT-VERIFICATION-001: the tracked documentation
    files a sealed contract judges deterministically (the referents of its
    DOCUMENTATION claims bound to the documentation-entries predicate) - a
    planning input: a unit that edits only these declares no verification
    of its own."""
    from kriya.workflow.contract_compilation import CLOSER_DOCUMENTATION_LIST_ENTRIES

    if contract is None:
        return []
    paths: List[str] = []
    for _rid, binding in contract.binding_closers(CLOSER_DOCUMENTATION_LIST_ENTRIES):
        # Only the files whose named list section the sealed predicate actually judges (its ``headings`` are
        # "<path>: <heading>"): a docs-directory referent's ``paths`` lists every file under docs/, code included
        # (docs/conf.py) - independent review of the repair (finding 1) - and those are never exempt.
        for entry in binding.detail.get("headings") or ():
            path = str(entry).split(": ", 1)[0]
            if path and path not in paths:
                paths.append(path)
    return paths


def close_requirements_by_file_immutability(
    deps: CloserDeps,
    ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any, contract: Any = None,
) -> List[Dict[str, Any]]:
    """OD-3: decides every "do not modify <named file>" requirement from the
    run's own mutation record (mutation_scope_evidence) - a frozen file the
    candidate changed, deleted or renamed is VIOLATED, every frozen file
    byte-identical and present closes it. Shared by the pre-apply boundary
    and enforce's terminal gate; a goal without such a requirement costs
    nothing."""
    from kriya.workflow.requirements import close_file_immutability_requirements

    frozen = frozen_file_paths(contract)
    if not frozen:
        return []
    _tracked, evidence = mutation_scope_evidence(deps, candidate_root, workspace_path, candidate_paths=candidate_paths)
    present = [path for paths in frozen.values() for path in paths if os.path.isfile(os.path.join(candidate_root, path))]
    return close_file_immutability_requirements(
        ledger, requirement_set, frozen_paths_by_requirement=frozen, present_paths=present, scope_evidence=evidence,
        source="requirement_closure.file_immutability", revision=revision,
    )


def close_requirements_by_test_immutability(
    deps: CloserDeps,
    ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any, contract: Any = None,
) -> List[Dict[str, Any]]:
    """REQUIREMENT-CLOSURE-PLAIN-GOAL-001: decides every "do not change any
    existing test" requirement from the run's own mutation record
    (mutation_scope_evidence) against the tests that existed before the run
    (_reference_test_files). Shared by the pre-apply boundary and enforce's
    terminal gate; a goal without such a requirement costs nothing. With the
    sealed contract (VERIFICATION-CONTRACT-003) every requirement claiming
    TEST_IMMUTABILITY is decided, compound statements as their claim."""
    from kriya.workflow.contract_closers import contract_claims_map
    from kriya.workflow.requirements import (
        TEST_IMMUTABILITY_CLAIM,
        close_test_immutability_requirements,
        is_test_immutability_requirement,
    )

    claims = contract_claims_map(contract)
    if not any(TEST_IMMUTABILITY_CLAIM in (claims.get(r.id) or ()) or (not claims and is_test_immutability_requirement(r.text))
               for r in requirement_set.requirements):
        return []
    _tracked, evidence = mutation_scope_evidence(deps, candidate_root, workspace_path, candidate_paths=candidate_paths)
    return close_test_immutability_requirements(
        ledger, requirement_set, reference_test_files=_reference_test_files(deps, candidate_root, workspace_path),
        present_test_files=_candidate_test_files(candidate_root), scope_evidence=evidence,
        source="requirement_closure.test_immutability", revision=revision, contract_claims=claims,
    )


def close_requirements_with_authority_bundle_evidence(
    deps: CloserDeps,
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, contract: Any, candidate_root: str, workspace_path: str, *,
    engine: Any, revision: Any, toolchain_declaration_mutable: bool, tree_binding: Any = None,
) -> List[Dict[str, Any]]:
    """VERIFICATION-CONTRACT-003 (D2): the operator's sealed external oracle
    (kriya/workflow/authority_bundle.py) judges the requirements the sealed
    contract bound to it, run once on a Kriya-owned copy of the candidate
    through the production containment boundary (the validator the gates
    use, with the run's toolchain authority)."""
    from kriya.core.state_paths import resolve_state_directory
    bound_authority_bundle, close_requirements_with_authority_bundle, run_authority_bundle = (
        deps.load_authority_bundle())

    bundle = bound_authority_bundle(engine)
    if bundle is None or contract is None:
        return []
    config = getattr(getattr(engine, "kernel", None), "config", None)
    state_root = resolve_state_directory(config)[0] if config is not None else None
    if state_root is None:
        return []

    def validator_for(root: str) -> PolymorphicValidator:
        built = PolymorphicValidator(root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg,
                                     toolchain_declaration_mutable=toolchain_declaration_mutable, execution_copy=True)
        built.tree_binding = tree_binding
        return built

    return close_requirements_with_authority_bundle(
        ledger, requirement_set, bundle, contract,
        execute=lambda: run_authority_bundle(bundle, candidate_root, state_root=state_root, validator_factory=validator_for),
        source="requirement_closure.external_acceptance_command", revision=revision,
        base_revision=_oracle_base_revision(deps, candidate_root, workspace_path),
    )


def close_requirements_by_api_preservation(
    deps: CloserDeps,
    ledger: Any, requirement_set: Any, contract: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any,
) -> List[Dict[str, Any]]:
    """VERIFICATION-CONTRACT-003: the Python public-API predicate
    (kriya/workflow/api_preservation.py) for every requirement the sealed
    contract bound to it: base revision versus the candidate, one comparison."""
    from kriya.workflow.api_preservation import close_api_preservation_requirements, compare_public_api
    from kriya.workflow.contract_closers import contract_claims_map
    from kriya.workflow.contract_compilation import CLOSER_API_PRESERVATION

    bindings = contract.binding_closers(CLOSER_API_PRESERVATION) if contract is not None else []
    if not bindings:
        return []
    candidate_files = sorted(set(_tracked_workspace_paths(candidate_root)) | set(candidate_paths))
    # The contract sealed which predicate applies (the project language at compile time); never re-derived here.
    language = str(bindings[0][1].detail.get("language") or "python")
    comparison = compare_public_api(candidate_root, _oracle_base_revision(deps, candidate_root, workspace_path),
                                    candidate_files=candidate_files, read_candidate=_workspace_file_reader(candidate_root),
                                    language=language)
    return close_api_preservation_requirements(
        ledger, requirement_set, contract_claims=contract_claims_map(contract), comparison=comparison,
        source="requirement_closure.api_preservation", revision=revision,
    )


def close_requirements_by_test_addition(
    deps: CloserDeps,
    ledger: Any, requirement_set: Any, contract: Any, candidate_root: str, workspace_path: str, *,
    base_test_identities: Optional[Iterable[str]] = None, candidate_test_identities: Optional[Iterable[str]] = None,
    revision: Any,
) -> List[Dict[str, Any]]:
    """VERIFICATION-CONTRACT-003: "add a test for it" from the candidate's own
    test files versus the pre-run set, or its structured test identities
    versus the base's (kriya/workflow/contract_closers.py)."""
    from kriya.workflow.contract_closers import close_test_addition_requirements
    from kriya.workflow.contract_compilation import CLOSER_TEST_ADDITION

    if contract is None or not contract.binding_closers(CLOSER_TEST_ADDITION):
        return []
    return close_test_addition_requirements(
        ledger, requirement_set, contract, reference_test_files=_reference_test_files(deps, candidate_root, workspace_path),
        candidate_test_files=_candidate_test_files(candidate_root), base_test_identities=base_test_identities,
        candidate_test_identities=candidate_test_identities, source="requirement_closure.test_addition",
        revision=revision, read_candidate=_workspace_file_reader(candidate_root),
    )


def close_requirements_by_documentation(
    ledger: Any, requirement_set: Any, contract: Any, candidate_root: str, *, revision: Any,
) -> List[Dict[str, Any]]:
    """VERIFICATION-CONTRACT-003: a conditional documentation request whose
    referent the candidate still lacks (kriya/workflow/contract_closers.py)."""
    from kriya.workflow.contract_closers import close_documentation_requirements
    from kriya.workflow.contract_compilation import (
        CLOSER_DOCUMENTATION_LIST_ENTRIES,
        CLOSER_DOCUMENTATION_NOT_APPLICABLE,
    )

    if contract is None or not (contract.binding_closers(CLOSER_DOCUMENTATION_NOT_APPLICABLE)
                                or contract.binding_closers(CLOSER_DOCUMENTATION_LIST_ENTRIES)):
        return []
    tracked = set(_tracked_workspace_paths(candidate_root))
    for root, dirs, files in os.walk(candidate_root):  # untracked additions count as present too
        dirs[:] = [d for d in dirs if d not in {".git", ".kriya", "node_modules", "__pycache__"}]
        tracked.update(os.path.relpath(os.path.join(root, name), candidate_root) for name in files)
    return close_documentation_requirements(
        ledger, requirement_set, contract, candidate_tracked_paths=tracked,
        read_candidate=_workspace_file_reader(candidate_root), source="requirement_closure.documentation",
        revision=revision,
    )


SCOPE_SUITE_PRESERVATION = "suite_preservation"


# SUITE-PRESERVATION-DIRECT-BASELINE-POLICY-001 (2026-10-10): who owns the PRE-mutation baseline a suite-preservation
# closure may judge through - an explicit caller input, never inferred from a missing baseline. Closed set.
SUITE_BASELINE_FROM_RUN = "run"            # the direct terminal gate: the run's baseline policy owns it; none means none


SUITE_BASELINE_LAZY_CAPTURE = "lazy_capture"  # the enforce terminal gate: captured lazily on the untouched workspace


_SUITE_BASELINE_SOURCES = frozenset({SUITE_BASELINE_FROM_RUN, SUITE_BASELINE_LAZY_CAPTURE})


def close_requirements_by_suite_preservation(
    deps: CloserDeps,
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    revision: Any, toolchain_declaration_mutable: bool, baseline_source: str, candidate_paths: Iterable[str] = (),
    java_home_override: Optional[str] = None, tree_binding: Any = None, contract: Any = None,
    goal: str = "", suite_baseline: Any = None, stability_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    baseline_suite_run: Optional[Callable[[Optional[Tuple[str, ...]]], Dict[str, Any]]] = None,
    run_baseline_policy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """REQUIREMENT-CLOSURE-PLAIN-GOAL-001: closes every UNVERIFIED whole-suite
    preservation requirement ("every existing test must keep passing") from
    the candidate's own full test run under the production test gate, with
    the run's toolchain authority (as the named-test closer): COMPLETE
    structured evidence, tests executed, the suite green. The suite runs
    once, only when such a requirement is open.

    SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001: a failing candidate
    suite is judged against the frozen PRE-mutation baseline by the one
    attribution owner (kriya/workflow/suite_attribution.py, the decision the
    terminal regression check and the candidate gates make). The direct
    boundary passes the run's captured full-regression baseline, its
    stability cache and its baseline replay (``suite_baseline``,
    ``stability_cache``, ``baseline_suite_run``: nothing runs twice); the
    enforce terminal gate passes none, and the baseline is then captured
    lazily on the untouched real workspace (immutable until commit,
    PRD-004) through the same capture the run start uses - once per
    closure, only when the candidate suite failed, never for a green suite
    - under the same validator and toolchain identity as the candidate run.
    An indeterminate run baseline, a baseline that cannot be captured or an
    attribution that cannot be made is reported as unavailable, and the
    closer fails closed (REGRESSION_UNATTRIBUTED).

    SUITE-PRESERVATION-DIRECT-BASELINE-POLICY-001 (review F3, 2026-10-10):
    ``baseline_source`` is the caller's explicit statement of who owns the
    baseline. ``SUITE_BASELINE_FROM_RUN`` (the direct terminal gate): the
    run's effective policy (``run_baseline_policy``, recorded at run start)
    decided whether a baseline exists; when it captured none (``disabled``,
    or ``auto`` resolved to not required) a failing suite is unattributable
    by policy - no lazy capture, no attribution authority the operator did
    not grant, the typed REGRESSION_UNATTRIBUTED refusal. ``SUITE_BASELINE_
    LAZY_CAPTURE`` (the enforce terminal gate) keeps the lazy capture above.
    Any other value is refused before anything runs: the distinction is
    never inferred from ``suite_baseline is None``."""
    logger = deps.logger
    compute_workspace_content_hash = deps.compute_workspace_content_hash
    captured_baseline = deps.captured_baseline
    attribute_suite_result = deps.attribute_suite_result
    post_environment_identity = deps.post_environment_identity
    if baseline_source not in _SUITE_BASELINE_SOURCES:
        raise ValueError(f"close_requirements_by_suite_preservation: unknown baseline_source {baseline_source!r} "
                         f"(expected one of {sorted(_SUITE_BASELINE_SOURCES)})")
    baseline_environment_identity = deps.load_baseline_environment_identity()
    from kriya.workflow.contract_closers import contract_claims_map
    from kriya.workflow.requirements import (
        SUITE_PRESERVATION,
        close_suite_preservation_requirements,
        is_suite_preservation_requirement,
        test_immutability_evidence,
    )

    candidate_paths = list(candidate_paths)
    cache: Dict[str, Dict[str, Any]] = stability_cache if stability_cache is not None else {}

    def untouched_workspace_run(target_test: Optional[Tuple[str, ...]]) -> Dict[str, Any]:
        # The run start's own baseline call (run_generation_workflow's baseline_suite_run): the same validator and
        # config on the untouched workspace, for the capture and for the owner's bounded stability replay.
        return PolymorphicValidator(
            workspace_path, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg,
        ).run_tests(target_test=target_test)

    baseline_run = baseline_suite_run or untouched_workspace_run

    def attribute_suite(result: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
        # The closer asks exactly once per closure attempt (one candidate run, one decision for every pending
        # requirement), so the lazy capture below runs at most once per closure.
        baseline = captured_baseline(suite_baseline)
        if baseline is None and suite_baseline is not None:
            return None, ("the run's PRE-mutation baseline is indeterminate: "
                          f"{getattr(suite_baseline, 'indeterminate_reason', None)}")
        if baseline is None and baseline_source == SUITE_BASELINE_FROM_RUN:
            # The run's policy captured no baseline; the direct closure owns no capture of its own.
            return None, (f"the run's full-regression baseline policy ({run_baseline_policy or 'not required'}) "
                          "captured no PRE-mutation baseline, and the direct terminal closure may not capture one")
        if baseline is None:
            try:
                environment = baseline_environment_identity(workspace_path, autonomy_cfg, goal=goal)
            except Exception as exc:
                logger.warning(f"Suite-preservation baseline environment identity unavailable: {exc}")
                environment = None
            lazily_captured = _capture_single_baseline(
                run_id=f"{SCOPE_SUITE_PRESERVATION}:{revision}", target_test=None, run_validator=baseline_run,
                compute_revision=lambda: compute_workspace_content_hash(workspace_path),
                environment_identity=environment)
            baseline = captured_baseline(lazily_captured)
            if baseline is None:
                return None, ("the untouched workspace's PRE-mutation baseline could not be captured: "
                              f"{lazily_captured.indeterminate_reason}")
        from kriya.repository.git import RepositoryContentFilterRefused
        try:
            return attribute_suite_result(
                scope=SCOPE_SUITE_PRESERVATION, baseline=baseline, result=result,
                post_environment=post_environment_identity(
                    baseline=baseline, workspace_path=workspace_path, worktree_path=candidate_root,
                    autonomy_cfg=autonomy_cfg, goal=goal, written_files=candidate_paths),
                stability_cache=cache, replay=baseline_run, workspace_path=workspace_path,
            ), None
        except RepositoryContentFilterRefused:
            raise  # a typed S19 refusal (the stability measurement's revision inspection) reaches the caller as itself
        except Exception as exc:
            logger.warning(f"Suite-preservation baseline attribution unavailable: {type(exc).__name__}: {exc}")
            return None, f"the PRE/POST attribution could not be made ({type(exc).__name__}: {exc})"

    suite_ids = None
    if contract is not None:
        suite_ids = sorted({rid for rid, _b in contract.binding_closers(SUITE_PRESERVATION)})
    immutability = None
    if (suite_ids if suite_ids is not None else
            any(is_suite_preservation_requirement(r.text) for r in requirement_set.requirements)):
        _tracked, evidence = mutation_scope_evidence(deps, candidate_root, workspace_path, candidate_paths=candidate_paths)
        immutability = test_immutability_evidence(
            _reference_test_files(deps, candidate_root, workspace_path), _candidate_test_files(candidate_root), evidence)

    def run_suite() -> Dict[str, Any]:
        validator = PolymorphicValidator(
            candidate_root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg,
            toolchain_declaration_mutable=toolchain_declaration_mutable,
        )
        validator.tree_binding = tree_binding
        validator.java_home_override = java_home_override
        return validator.run_tests()

    return close_suite_preservation_requirements(
        ledger, requirement_set, test_files=_candidate_test_files(candidate_root), run_suite=run_suite,
        source="requirement_closure.suite_preservation", revision=revision, test_immutability=immutability,
        contract_claims=contract_claims_map(contract), suite_requirement_ids=suite_ids,
        attribute_suite=attribute_suite,
    )


def close_requirements_with_named_tests(
    deps: CloserDeps,
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    modified: Iterable[str], revision: Any, toolchain_declaration_mutable: bool,
    java_home_override: Optional[str] = None, tree_binding: Any = None, contract: Any = None,
) -> List[Dict[str, Any]]:
    """PRD-020: runs the tests an UNVERIFIED requirement's own text names, on
    the candidate at ``candidate_root`` (the one the verifier just judged),
    and records closure evidence when they execute and pass - see
    requirements.close_unverified_requirements_with_named_tests. Shared by
    the direct/milestone pre-apply boundary and enforce's terminal gate.

    D8: the validator is built only when an UNVERIFIED requirement names an
    existing test (otherwise there is nothing to close), and with the run's
    own toolchain authority (``toolchain_declaration_mutable``,
    derived by the caller from its write scope and approved plan exactly as
    its own gates were): this step verifies the already-authorized
    candidate; it never re-decides a toolchain change. Without that
    authority a changed declaration still fails closed here."""
    from kriya.workflow.named_test_oracle import judge_named_tests
    from kriya.workflow.requirements import (
        RequirementOutcome,
        close_unverified_requirements_with_named_tests,
        named_existing_tests,
        requirement_outcomes,
    )

    test_files = _candidate_test_files(candidate_root)
    outcomes = requirement_outcomes(ledger, requirement_set)
    if not any(outcomes.get(requirement.id) is RequirementOutcome.UNVERIFIED
               and named_existing_tests(requirement.text, test_files)
               for requirement in requirement_set.requirements):
        return []  # D8: nothing to close, so no validator and no toolchain resolution
    def candidate_validator() -> PolymorphicValidator:
        validator = PolymorphicValidator(
            candidate_root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg,
            toolchain_declaration_mutable=toolchain_declaration_mutable,
        )
        validator.java_home_override = java_home_override
        validator.tree_binding = tree_binding
        return validator

    def base_validator(root: str) -> PolymorphicValidator:
        # The base revision declares its own toolchain; nothing is changed there.
        validator = PolymorphicValidator(root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg, execution_copy=True)
        validator.java_home_override = java_home_override
        return validator

    from kriya.workflow.contract_closers import contract_claims_map

    modified = list(modified)
    base_revision = _oracle_base_revision(deps, candidate_root, workspace_path)
    return close_unverified_requirements_with_named_tests(
        ledger, requirement_set, test_files=test_files, modified=modified,
        judge=lambda named: judge_named_tests(
            named, candidate_root=candidate_root, base_revision=base_revision, modified=modified,
            candidate_validator=candidate_validator, base_validator=base_validator),
        source="requirement_closure.named_test_run", revision=revision, contract_claims=contract_claims_map(contract),
    )


def _oracle_base_revision(deps: CloserDeps, candidate_root: str, workspace_path: str) -> Optional[str]:
    """FS-1C0: the revision a named test must be authored at - the owning
    run's base (so an earlier unit's committed change is still the run's
    own), else the candidate's HEAD; None when neither can be read."""
    logger = deps.logger
    _run_committed_paths = deps.run_committed_paths
    from kriya.repository.git import GIT_READ_FAILURES
    from kriya.workflow.worktree import git_read_lines

    try:
        base = _run_committed_paths(workspace_path)[1]
        if base:
            return base
        heads = git_read_lines(candidate_root, "rev-parse", "HEAD")
    except (*COMMITTED_HISTORY_READ_FAILURES, *GIT_READ_FAILURES) as exc:  # no readable base: the oracle refuses (UNVERIFIED)
        logger.info(f"Named-test oracle base revision unavailable: {exc}")
        return None
    return heads[0] if heads else None


# Reading the owning run's committed history is reading a persisted record: I/O, JSON/schema (ValueError) or a missing field (KeyError).
COMMITTED_HISTORY_READ_FAILURES = (OSError, ValueError, KeyError)


def mutation_scope_evidence(
    deps: CloserDeps,
    candidate_root: str, workspace_path: str, *, candidate_paths: Iterable[str],
) -> Tuple[List[str], Dict[str, Any]]:
    """PRD-020: what the run actually changed, for mutation-scope requirements
    - (paths tracked at the run's base, evidence).

    ``actual_paths`` = the candidate's own changes (``candidate_paths`` that
    git reports changed in ``candidate_root`` against the base) plus the
    run's committed path history (earlier milestones). ``foreign_paths`` =
    anything else git reports changed or untracked (``.kriya/`` and ignored
    files excluded): present, but not attributable to the run.

    COUNTER-EVIDENCE IS MONOTONIC. The committed history is read first and is a fact
    about the run on its own. When a later git read fails (or the candidate is not at the run's base) the evidence is
    ``unavailable`` - never complete - but it keeps what was already established as ``known_changed_paths``: a consumer may
    conclude VIOLATED from a known offending path, and may never conclude SATISFIED from an incomplete record
    (``requirements.scope_evidence_view`` is the one reader of this shape). Only when the history itself cannot be read is
    nothing known."""
    _run_committed_paths = deps.run_committed_paths
    from kriya.repository.git import (
        GIT_READ_FAILURES,
        RepositoryContentFilterRefused,
        refuse_repository_content_filters,
    )
    from kriya.workflow.worktree import git_read_lines as _git_lines

    try:
        run_id, base, committed = _run_committed_paths(workspace_path)
    except COMMITTED_HISTORY_READ_FAILURES as exc:
        return [], {"unavailable": f"{type(exc).__name__}: {exc}"}
    known = {"run_id": run_id, "base_revision": base, "committed_history": sorted(committed), "known_changed_paths": sorted(committed)}
    try:
        heads = _git_lines(candidate_root, "rev-parse", "HEAD")
        if not heads:
            return [], {**known, "unavailable": "git printed no HEAD revision for the candidate"}
        head = heads[0]
        if base and head != base:
            return [], {**known, "unavailable": f"candidate is at {head[:12]}, not the run base {base[:12]}"}
        base = base or head
        tracked = _git_lines(candidate_root, "ls-tree", "-r", "--name-only", base)
        # S19 P1c (8(a)): `diff <base>` compares the WORK TREE and runs a clean filter on a stat-dirty file (MEASURED);
        # the folder it runs in is inspected immediately before it, and a refusal is typed, never "unavailable".
        refuse_repository_content_filters(candidate_root)
        changed = {
            path for path in (
                _git_lines(candidate_root, "diff", "--name-only", "--no-renames", "--relative", base)
                + _git_lines(candidate_root, "ls-files", "--others", "--exclude-standard")
            ) if not (path == ".kriya" or path.startswith(".kriya/"))
        }
    except RepositoryContentFilterRefused:
        raise
    except GIT_READ_FAILURES as exc:
        return [], {**known, "unavailable": f"{type(exc).__name__}: {exc}"}
    actual = (set(candidate_paths) & changed) | set(committed)
    return tracked, {
        "run_id": run_id, "base_revision": base, "candidate_revision": head,
        "actual_paths": sorted(actual), "committed_history": sorted(committed),
        "foreign_paths": sorted(changed - actual),
    }


def close_requirements_by_mutation_scope(
    deps: CloserDeps,
    ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any,
) -> List[Dict[str, Any]]:
    """PRD-020: decides every "do not modify any other file" requirement from
    the run's own mutation record (requirements.close_mutation_scope_requirements).
    Shared by the direct/milestone pre-apply boundary and enforce's terminal
    gate; a goal without such a requirement costs nothing."""
    from kriya.workflow.requirements import close_mutation_scope_requirements, is_mutation_scope_requirement

    if not any(is_mutation_scope_requirement(r.text) for r in requirement_set.requirements):
        return []
    tracked, evidence = mutation_scope_evidence(deps, candidate_root, workspace_path, candidate_paths=candidate_paths)
    return close_mutation_scope_requirements(
        ledger, requirement_set, tracked_paths=tracked, scope_evidence=evidence,
        source="requirement_closure.mutation_scope", revision=revision,
    )


__all__ = ["CloserDeps", "SCOPE_SUITE_PRESERVATION", "SUITE_BASELINE_FROM_RUN", "SUITE_BASELINE_LAZY_CAPTURE",
           "close_requirements_by_api_preservation", "close_requirements_by_documentation",
           "close_requirements_by_file_immutability", "close_requirements_by_mutation_scope",
           "close_requirements_by_suite_preservation", "close_requirements_by_test_addition",
           "close_requirements_by_test_immutability", "close_requirements_with_acceptance_tests",
           "close_requirements_with_authority_bundle_evidence", "close_requirements_with_derived_examples",
           "close_requirements_with_named_tests", "documentation_referent_paths", "frozen_file_paths",
           "immutable_test_files", "mutation_scope_evidence", "named_preserved_test_files"]
