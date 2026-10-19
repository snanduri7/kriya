"""Import facades (full modularization, slice A2; DEPENDENCY-RULES.md §4.3, §4.9).

A facade keeps an old import path alive while its symbols live in their
owner. A file listed in ``FACADES`` contains ONLY a module docstring that
names its removal slice and ``from <owner> import X as X`` lines from its
declared owner module; every re-exported name is the owner's object. Any
other statement fails. Every pure re-export module of kriya/ must be listed
(a facade cannot appear silently), and P1 empties ``FACADES`` (an entry may
only stay with a written justification, DEPENDENCY-RULES §4.9).

``PARTIAL_FACADES`` (added in C2, because the §4.3 rule cannot express it): a
module that keeps real logic of its own AND re-exports names that moved to an
owner (``workflow/triage.py`` keeps ``EngineeringTriageService``, its moved
vocabulary lives in ``policy/risk.py``). For such a module the moved names, and
only those, must be top-level ``from <owner> import X as X`` lines, each the
owner's object, never also defined locally; the source names its removal slice.
P1 empties it together with ``FACADES``. A module whose moved names come from SEVERAL owner modules (M1:
``workflow/edit_safety.py`` re-exports from ``repository.atomic_write`` and ``repository.commit``) maps to a tuple of
``PartialFacade``, one per owner; each owner's import lines are policed against that owner's own name list.
"""
from __future__ import annotations

import ast
import importlib
from typing import Dict, List, Mapping, NamedTuple, Tuple, Union

import pytest

from . import module_map as mm
from .test_import_direction import DONE_SLICES, SLICES


class Facade(NamedTuple):
    owner: str          # dotted owner module every line imports from
    removal_slice: str  # the slice that deletes the facade
    reason: str


FACADES: Dict[str, Facade] = {
    "kriya/build_info.py": Facade("kriya.foundation.build_info", "P1",
                                  "build identity moved to FOUNDATION in A1; old import path kept"),
    "kriya/core/tomlcompat.py": Facade("kriya.foundation.tomlcompat", "P1",
                                       "tomllib shim moved to FOUNDATION in A1; old import path kept"),
    "kriya/workflow/runtime_command_approval.py": Facade(
        "kriya.policy.approval", "P1",
        "C3: runtime-command subject/fingerprint/decision owned by AUTHORITY; 9 test files import the old path"),
    "kriya/core/trace.py": Facade("kriya.control.trace", "P1",
                                  "D2: the run trace store (TraceLogger) owned by STATE; 17 test files and fixtures import the old path"),
}


class PartialFacade(NamedTuple):
    owner: str               # dotted owner module the moved names come from
    names: Tuple[str, ...]   # exactly the names re-exported for the old import path
    removal_slice: str
    reason: str


PARTIAL_FACADES: Dict[str, Union[PartialFacade, Tuple[PartialFacade, ...]]] = {
    "kriya/config/config.py": PartialFacade(
        "kriya.config.resolution",
        ("ConfigResolutionState", "PRODUCTION_FIXED_RUNTIME_GUARANTEES", "PRODUCTION_GENERATION_TIME_BUDGET_SECONDS", "REMOVED_LOGGING_FILE_MESSAGE",
         "REMOVED_PATHS_LOGS_MESSAGE", "RemovedConfigFieldError", "production_sealed_requirement", "production_sealed_value_satisfied",
         "resolve_config_state", "runtime_profile_preset_fields"), "P1",
        "D3: configuration resolution split from the schema; the schema and load_config stay; 1 patch target "
        "(kriya.config.config.resolve_config_state, test_state_location) and ~20 importers use the old path"),
    "kriya/core/state_paths.py": PartialFacade(
        "kriya.config.resolution", ("StateDirectoryError", "WORKSPACE_STATE_PARENT", "require_workspace_local_state_under_kriya_dir"), "P1",
        "D3: the paths.state rule and its error owned by CONFIG (resolution needs them without importing STATE); the state directory, "
        "trace database and legacy migration stay"),
    "kriya/workflow/triage.py": PartialFacade(
        "kriya.policy.risk",
        ("ChangeKind", "EngineeringRoute", "ExecutionWeight", "ImpactVector", "RiskClass", "determine_execution_weight"), "P1",
        "C2: risk/route vocabulary owned by AUTHORITY; EngineeringTriageService stays (kernel); 69 test files import the old path"),
    "kriya/workflow/process_profile.py": PartialFacade(
        "kriya.policy.risk", ("ContextDepth", "ExecutionWeight", "ProcessProfile", "VerificationTier"), "P1",
        "C2: profile vocabulary owned by AUTHORITY; the profile table and process_profile_for stay"),
    "kriya/control/launch_approval.py": PartialFacade(
        "kriya.policy.approval",
        ("KIND_CHANGE", "KIND_RUNTIME", "KINDS", "REFUSAL_CODES", "REVIEW_SECTION_HEADER", "RUNTIME_COMMAND_REVIEW", "WORKING_DIRECTORY_KIND",
         "ApprovalRefused", "RuntimeCommandReviewPort", "_sha", "candidate_digest", "split_reason"), "P1",
        "C3: review kinds/codes, ApprovalRefused, candidate digest and split_reason owned by AUTHORITY's approval module (plus the review "
        "port the record and wait take as a parameter); the review record, wait and decide stay"),
    "kriya/workflow/checkpoint.py": PartialFacade(
        "kriya.control.checkpoint",
        ("CHECKPOINT_DIR", "OBSERVATIONAL_CONFIG_SECTIONS", "checkpoint_path", "compute_config_fingerprint", "compute_registry_hash",
         "delete_checkpoint", "find_latest_checkpoint", "list_checkpoint_run_references", "list_checkpoints", "load_checkpoint", "new_run_id",
         "save_checkpoint", "without_observational_config"), "P1",
        "D2: the checkpoint store owned by STATE; the git/working-tree fingerprints (M2) and the resume validation (kernel) stay; "
        "24 test files and the engine (save_checkpoint/delete_checkpoint, patched on workflow.workflow) import the old path"),
    "kriya/workflow/contract_lifecycle.py": PartialFacade(
        "kriya.control.persistence",
        ("TRANSITION_ALREADY_APPLIED", "TRANSITION_DISCARDED", "TRANSITION_NEEDS_REVIEW", "TRANSITION_PROMOTED", "complete_contract_transition"),
        "P1", "D2: recovery's half of the contract-registry transaction (a read/settle of STATE's registry files) owned by STATE, so "
        "control.recovery no longer imports the kernel; derive_contract_transition stays; test_prd029_contract_lifecycle uses the old path"),
    "kriya/core/llm.py": PartialFacade(
        "kriya.policy.egress", ("is_local_url",), "P1",
        "C3: the local/private-address definition owned by AUTHORITY (policy.execution no longer imports the provider back); "
        "8 production modules (cli, kup.context_ops, production_doctor, core.model_runtime, core.model_qualification, memory.embedding, workflow.attempt, workflow.egress_authority) and the regression index use the old path"),
    "kriya/workflow/edit_safety.py": (
        PartialFacade(
            "kriya.repository.atomic_write", ("_execution_policy", "atomic_write_file"), "P1",
            "M1: the atomic write of one file owned by REPOSITORY; the audit policy instance stays reachable for the 4 "
            "monkeypatches of its evaluate (test_edit_safety_policy_audit); the edit engine stays (GENERATION)"),
        PartialFacade(
            "kriya.repository.commit",
            ("BatchCommitError", "BatchCommitResult", "CandidateMaterializationError", "CommitEvidence", "CommitState",
             "FileRevisionConflict", "StagedFileWrite", "UncertainCommitError", "candidate_digest", "candidate_digest_of_entries",
             "commit_evidence_dir", "commit_revision_grounded_batch", "commit_revision_grounded_file", "commit_state_for_transaction",
             "content_revision", "find_uncertain_commit_evidence", "list_commit_evidence", "load_commit_evidence", "read_file_revision",
             "settle_recovered_commit_evidence", "stage_file_prefix"), "P1",
            "M1: the revision-grounded commit and its evidence owned by REPOSITORY; ~40 test files and 20 kernel modules import the "
            "old path; the privates (_persist_commit_evidence, _stage_content, _atomic_write_bytes) are deliberately NOT re-exported so a "
            "patch on the facade fails loudly instead of silently missing the mechanism (6 patch sites retargeted)"),
    ),
    "kriya/workflow/file_integrity.py": PartialFacade(
        "kriya.repository.snapshot",
        ("CRLF", "LF", "MIXED", "MIXED_NEWLINE_UNSUPPORTED", "NO_NEWLINE", "SOURCE_CHANGED_SINCE_AUTHORIZATION", "SYMLINK_TARGET_UNSUPPORTED",
         "UNSUPPORTED_TEXT_ENCODING", "UTF8_BOM", "FileIntegrityError", "FileSnapshot", "_content_identity", "_stat_identity",
         "display_text", "file_raw_digest", "load_snapshot", "newline_style", "raw_digest", "read_shown_text", "require_unchanged",
         "untracked_repository_paths"), "P1",
        "M1: snapshots and raw-byte revision identity owned by REPOSITORY; the edit engine (GENERATION) and VerificationTreeBinding "
        "(AUTHORITY, which looks up the identities and untracked_repository_paths here) stay"),
    "kriya/workflow/workflow_controller.py": (
        PartialFacade(
            "kriya.workflow.plan_revision", ("PLAN_SCOPE_REVISION_REQUIRED",), "P1",
            "E11: the plan revisions and plan-graph queries owned by plan_revision; the controller keeps a forwarder for "
            "each function and re-exports the scope-revision reason code (the enforce loop and tests read it)"),
        PartialFacade(
            "kriya.workflow.owner_recovery", ("ArtifactOwnerResolutionBasis", "OwnerResolution", "_GATE_FAILURE_TYPES"), "P1",
            "E11: the owner recovery and the integration obligations owned by owner_recovery; the controller keeps a "
            "forwarder for each function and re-exports the two resolution types and the gate-failure set (tests import "
            "them from the controller)"),
        PartialFacade(
            "kriya.workflow.planning_request",
            ("AUTHORITATIVE_PLANNER_SYSTEM_PROMPT", "BOUNDED_SUBTASK_DESIGN", "PLANNED_FILE_SOURCE_REASON",
             "_JAVA_CONSTRUCTOR_INSTANTIATION_RE", "_JAVA_TYPE_MENTION_RE", "_PRESERVED_REFERENCE_CONFLICT_RE",
             "_QUOTED_TOKEN_RE", "_REQUIRES_PROVIDES_ERROR_KEYWORDS", "_SUBTASK_MENTION_RE"), "P1",
            "E11: the planning request and the plan checks owned by planning_request; the controller keeps a forwarder "
            "for each function (three are patched on it, ownership_findings imports one at call time) and re-exports "
            "the system prompt, the bounded-subtask design and planned-source reason the enforce loop reads, and the "
            "patterns"),
    ),
    "kriya/workflow/attempt.py": (
        PartialFacade(
            "kriya.workflow.generation_service", ("PLANNED_SOURCE_HEADER", "_PLANNED_SOURCE_UNBOUNDED_TOKENS", "_chain_binding"),
            "P1", "E6: the Developer request assembly and generation owned by generation_service; attempt.py keeps forwarders "
            "for the patched/called names and re-exports these three (the planned-source header and default budget that "
            "tests and the forwarder's default read, the pure chain-binding lookup attempt.py's own retry and "
            "edit-capability code still calls)"),
        PartialFacade(
            "kriya.workflow.fallback_decision", ("_fallback_rejection",), "P1",
            "E6: the llm_chain fallback decisions owned by fallback_decision; the pure rejection record is re-exported "
            "(the other four names are forwarders that inject attempt.py's collaborators)"),
        PartialFacade(
            "kriya.workflow.repair_orchestrator",
            ("RetryContextPreparation", "_DETERMINISTIC_VERDICT_REASON_CODES", "_GOAL_CALL_NAME", "_GOAL_MEMBER_HINTS_MAX",
             "_classify_retry_target_source_origin", "_compute_retry_evidence_fingerprint", "_goal_named_member_candidates",
             "_goal_named_members_of", "_preserve_member_exact_precision", "_target_source_record"), "P1",
            "E7: the retry/repair preparation and the coordinated repair owned by repair_orchestrator; attempt.py keeps 11 "
            "forwarders that inject its collaborators and re-exports these ten pure names (the preparation's result type, "
            "the deterministic-verdict codes, the goal-member pattern and cap, the target-source record, the evidence "
            "fingerprint, the goal-named candidates edit_feasibility imports from attempt, the member-exact merge "
            "generation_service receives through GenerationDeps)"),
        PartialFacade(
            "kriya.workflow.gate_orchestrator",
            ("SPEC_COMPLIANCE_MODEL_ADVISORY", "SPEC_MODEL_INDETERMINATE", "SPEC_MODEL_REPORTED_MISSING",
             "_PROCESS_BOUNDARY_RECURRENCE_ESCALATION", "_TEST_VERIFIERS"), "P1",
            "E8: the candidate-gate orchestration owned by gate_orchestrator; attempt.py keeps 11 forwarders that inject "
            "its collaborators (GateDeps) and re-exports the goal-spec gate's three advisory statuses. E14: +10 forwarders "
            "(the gate seams OI-54/OI-50 placed here) and the re-exports of their two constants (GateDeps binds the "
            "recurrence escalation text by attempt.py's name; the test-verifier set moved with its only reader)"),
        PartialFacade(
            "kriya.workflow.process_boundary",
            ("_INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT", "_NONZERO_PROCESS_EXIT_RE", "_TERMINATING_CASE_MARKERS"), "P1",
            "E14 (OI-57): the process-boundary test-candidate facts and the dependency-graph readers owned by "
            "process_boundary; attempt.py keeps 14 forwarders (ProcessBoundaryDeps) and re-exports the three constants "
            "the moved bodies read (tests/test_e14_characterization.py reads the initial constraint from attempt)"),
        PartialFacade(
            "kriya.workflow.runtime_verification",
            ("MANAGED_SERVICE_APPROVAL_UNSUPPORTED", "_DISTRUST_DISCLOSURE_NOTE", "_MANAGED_SERVICE_FORBIDDEN_LEADING_EXECUTABLES",
             "_MANAGED_SERVICE_PROBE_KINDS", "_MANAGED_SERVICE_READINESS_KINDS", "_MANAGED_SERVICE_SHELL_MARKERS",
             "_RUNTIME_VERIFICATION_SYNTHETIC_INPUT", "_SHELL_INTERPRETER_EXECUTABLES", "_SUPPORTED_RUNTIME_EXECUTION_MODES"),
            "P1", "E9: runtime and managed-service verification owned by runtime_verification; attempt.py keeps 26 forwarders "
            "that inject its collaborators (RuntimeDeps) and re-exports these nine constants (test_workflow, "
            "test_runtime_stdin_rerun_binding and test_managed_service_hitl_fail_closed read the synthetic input and the "
            "managed-service reason code from attempt)"),
        PartialFacade(
            "kriya.workflow.spec_compliance", ("_REQUIREMENT_PROVENANCE_STOPWORDS", "_REQUIREMENT_WORD_RE"), "P1",
            "E10: spec compliance, requirement verdicts and the contract-change classification owned by "
            "spec_compliance; attempt.py keeps 18 forwarders (GateDeps hands the gates eleven of them, terminal_gate_service "
            "imports one at call time) and re-exports the two requirement-provenance word primitives"),
        PartialFacade(
            "kriya.workflow.candidate_transaction", ("MAX_SYMBOL_LOCI_PER_FILE", "_RESPONSE_VALIDITY_FAILURES"), "P1",
            "E10: the candidate staging transaction owned by candidate_transaction; attempt.py keeps 18 "
            "forwarders that inject its collaborators (CandidateDeps) and re-exports the edit-capability locus bound "
            "(test_context_edit_protocol_large_file_001 reads it from attempt) and the response-validity failure family"),
    ),
    "kriya/cli.py": (
        PartialFacade(
            "kriya.cli_commands.common", ("_user_error_text",), "P1",
            "E14: the operator-error text shared by kriya/cli.py and the moved command groups, owned by "
            "cli_commands.common (no command module may import kriya.cli: kriya.cli <-> kriya.repl is a cycle)"),
        PartialFacade(
            "kriya.cli_commands.static_analysis",
            ("_static_analysis_workspace", "static_analysis_group", "static_analysis_revoke", "static_analysis_scan",
             "static_analysis_status", "static_analysis_waive", "static_analysis_waivers"), "P1",
            "E14: the `kriya static-analysis` group owned by cli_commands.static_analysis, registered on main by "
            "kriya/cli.py; every moved name re-exported (the same object)"),
    ),
    "kriya/workflow/terminal_commit.py": PartialFacade(
        "kriya.repository.commit", ("CandidateMaterializationError",), "P1",
        "M1: the materialization error owned by repository.commit (static_analysis.service imports it there, which removes the "
        "static_analysis.service <-> workflow.terminal_commit cycle); the terminal commit seam stays (kernel)"),
}


def partial_facade_entries() -> List[Tuple[str, PartialFacade]]:
    """(path, facade) per owner: a path with several owners contributes one entry per owner."""
    entries = []
    for path, value in sorted(PARTIAL_FACADES.items()):
        entries.extend((path, facade) for facade in ((value,) if isinstance(value, PartialFacade) else value))
    return entries


def partial_facade_problems(path: str, tree: ast.Module, source: str, facade: PartialFacade) -> List[str]:
    problems = []
    reexported: Dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == facade.owner:
            for alias in node.names:
                if alias.name not in facade.names:
                    problems.append(f"line {node.lineno}: {alias.name} is imported from the owner but not listed")
                elif alias.asname != alias.name:
                    problems.append(f"line {node.lineno}: {alias.name} is not re-exported as itself")
                else:
                    reexported[alias.name] = reexported.get(alias.name, 0) + 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in facade.names:
            problems.append(f"line {node.lineno}: {node.name} is defined here although it moved to {facade.owner}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            problems.extend(f"line {node.lineno}: {t.id} is rebound here" for t in targets
                            if isinstance(t, ast.Name) and t.id in facade.names)
    problems.extend(f"{name} is not re-exported exactly once" for name in facade.names if reexported.get(name) != 1)
    if f"Removed in {facade.removal_slice}" not in source:
        problems.append(f"the source does not name removal slice {facade.removal_slice}")
    return [f"{path}: {problem}" for problem in problems]


def _docstring(tree: ast.Module) -> str:
    return ast.get_docstring(tree) or ""


def facade_problems(path: str, tree: ast.Module, facade: Facade) -> List[str]:
    problems = []
    body = list(tree.body)
    if not (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        problems.append("no module docstring")
    else:
        body = body[1:]
    if facade.removal_slice not in _docstring(tree):
        problems.append(f"docstring does not name removal slice {facade.removal_slice}")
    if not body:
        problems.append("re-exports nothing")
    for node in body:
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or node.module != facade.owner:
            problems.append(f"line {node.lineno}: not `from {facade.owner} import X as X`")
            continue
        problems.extend(f"line {node.lineno}: {alias.name} is not re-exported as itself"
                        for alias in node.names if alias.asname != alias.name)
    return [f"{path}: {problem}" for problem in problems]


def pure_reexport_modules(trees: Mapping[str, ast.Module]) -> List[str]:
    found = []
    for path, tree in trees.items():
        body = [node for node in tree.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
        if body and all(isinstance(node, ast.ImportFrom) and all(a.asname == a.name for a in node.names)
                        for node in body):
            found.append(path)
    return sorted(found)


def test_the_a1_facades_are_listed():
    assert {"kriya/build_info.py", "kriya/core/tomlcompat.py"} <= set(FACADES)


@pytest.mark.parametrize("path", sorted(FACADES))
def test_a_facade_contains_only_reexports_from_its_owner(path):
    facade = FACADES[path]
    assert facade_problems(path, mm.trees()[path], facade) == []
    assert facade.removal_slice in SLICES and facade.removal_slice not in DONE_SLICES and facade.reason


@pytest.mark.parametrize("path", sorted(FACADES))
def test_every_reexported_name_is_the_owners_object(path):
    facade_module = importlib.import_module(mm.module_name(path))
    owner = importlib.import_module(FACADES[path].owner)
    names = [alias.name for node in mm.trees()[path].body if isinstance(node, ast.ImportFrom) for alias in node.names]
    assert names and all(getattr(facade_module, name) is getattr(owner, name) for name in names)


def test_no_unlisted_facade():
    assert pure_reexport_modules(mm.trees()) == sorted(FACADES), "list a new re-export facade in FACADES"


def test_the_facade_check_detects_every_forbidden_form():
    facade = Facade("kriya.foundation.x", "P1", "r")
    planted = mm.parse_sources({
        "good.py": '"""Facade; removed in P1."""\nfrom kriya.foundation.x import a as a\n',
        "logic.py": '"""Facade; removed in P1."""\nfrom kriya.foundation.x import a as a\nb = a + 1\n',
        "alias.py": '"""Facade; removed in P1."""\nfrom kriya.foundation.x import a\n',
        "other.py": '"""Facade; removed in P1."""\nfrom kriya.elsewhere import a as a\n',
        "noslice.py": '"""Facade."""\nfrom kriya.foundation.x import a as a\n',
    })
    assert facade_problems("good.py", planted["good.py"], facade) == []
    assert facade_problems("logic.py", planted["logic.py"], facade) == [
        "logic.py: line 3: not `from kriya.foundation.x import X as X`"]
    assert facade_problems("alias.py", planted["alias.py"], facade) == [
        "alias.py: line 2: a is not re-exported as itself"]
    assert facade_problems("other.py", planted["other.py"], facade) == [
        "other.py: line 2: not `from kriya.foundation.x import X as X`"]
    assert facade_problems("noslice.py", planted["noslice.py"], facade) == [
        "noslice.py: docstring does not name removal slice P1"]
    assert pure_reexport_modules(planted) == ["good.py", "noslice.py", "other.py"]


@pytest.mark.parametrize("path,facade", partial_facade_entries(), ids=[f"{p}<-{f.owner}" for p, f in partial_facade_entries()])
def test_a_partial_facade_reexports_exactly_its_moved_names_from_the_owner(path, facade):
    source = (mm.REPO / path).read_text(encoding="utf-8")
    assert partial_facade_problems(path, mm.trees()[path], source, facade) == []
    assert facade.removal_slice in SLICES and facade.removal_slice not in DONE_SLICES and facade.reason
    module, owner = importlib.import_module(mm.module_name(path)), importlib.import_module(facade.owner)
    assert all(getattr(module, name) is getattr(owner, name) for name in facade.names)
    assert path not in FACADES and path not in pure_reexport_modules(mm.trees())   # it keeps real logic: not a pure facade


def test_the_partial_facade_check_detects_every_forbidden_form():
    facade = PartialFacade("kriya.foundation.x", ("a",), "P1", "r")
    sources = {
        "good.py": "# Removed in P1\nfrom kriya.foundation.x import a as a\ndef logic():\n    return a\n",
        "alias.py": "# Removed in P1\nfrom kriya.foundation.x import a\n",
        "extra.py": "# Removed in P1\nfrom kriya.foundation.x import a as a\nfrom kriya.foundation.x import b as b\n",
        "redefined.py": "# Removed in P1\nfrom kriya.foundation.x import a as a\nclass a:\n    pass\n",
        "rebound.py": "# Removed in P1\nfrom kriya.foundation.x import a as a\na = 1\n",
        "missing.py": "# Removed in P1\nimport os\n",
        "noslice.py": "from kriya.foundation.x import a as a\n",
    }
    trees = mm.parse_sources(sources)
    found = {name: partial_facade_problems(name, trees[name], sources[name], facade) for name in sources}
    assert found == {
        "good.py": [],
        "alias.py": ["alias.py: line 2: a is not re-exported as itself", "alias.py: a is not re-exported exactly once"],
        "extra.py": ["extra.py: line 3: b is imported from the owner but not listed"],
        "redefined.py": ["redefined.py: line 3: a is defined here although it moved to kriya.foundation.x"],
        "rebound.py": ["rebound.py: line 3: a is rebound here"],
        "missing.py": ["missing.py: a is not re-exported exactly once"],
        "noslice.py": ["noslice.py: the source does not name removal slice P1"],
    }


def test_the_partial_facade_check_polices_every_owner_of_a_multi_owner_facade():
    owners = (PartialFacade("kriya.foundation.x", ("a",), "P1", "r"), PartialFacade("kriya.foundation.y", ("b",), "P1", "r"))
    sources = {
        "good.py": "# Removed in P1\nfrom kriya.foundation.x import a as a\nfrom kriya.foundation.y import b as b\n",
        "wrong_owner.py": "# Removed in P1\nfrom kriya.foundation.x import a as a\nfrom kriya.foundation.y import b as b\n"
                          "from kriya.foundation.y import a as a\n",
        "second_missing.py": "# Removed in P1\nfrom kriya.foundation.x import a as a\n",
    }
    trees = mm.parse_sources(sources)
    found = {name: [problem for facade in owners for problem in partial_facade_problems(name, trees[name], sources[name], facade)]
             for name in sources}
    assert found == {
        "good.py": [],
        "wrong_owner.py": ["wrong_owner.py: line 4: a is imported from the owner but not listed"],
        "second_missing.py": ["second_missing.py: b is not re-exported exactly once"],
    }
    assert [facade.owner for _path, facade in partial_facade_entries() if _path == "kriya/workflow/edit_safety.py"] == [
        "kriya.repository.atomic_write", "kriya.repository.commit"]
