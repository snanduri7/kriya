"""W3-3 unit C1: the migration scans and the contract reference scan decide source membership from the bound tree on Git-backed roots, not from a directory name.

MEASURED at 3fd1641 (probes/probe_c1_git_backed_migration_scans.py, PREFIX -> POSTFIX), all on Git-backed fixtures: ``migration._scan_java_files`` pruned directories NAMED
``build/target/dist/...`` so (a) ``resolve_migration_resolution`` dropped a tracked Gson consumer under ``.../build/`` and answered NOT_APPLICABLE - the goal's migration
obligation silently vanished; (b) ``find_migration_incomplete`` read a leftover Gson consumer under ``.../build/`` as "migrated"; (c) ``contract_lifecycle._reference_scan`` -
whose result decides the ``CONTRACT_CONSUMER_VERIFICATION_MISSING`` refusal - returned no consumer for a tracked caller under ``build/`` or ``dist/`` and DID return one for a
git-IGNORED ``out/``. The membership is now ``worktree.candidate_member_paths`` (the D-001 bound tree); the parked root classes (non-Git workspace, scoped-snapshot sandbox)
keep the legacy walk unchanged; a Git-backed root Git cannot list is the typed ``BoundTreeError`` and never "no consumer" / "no migration" / ``[]``.

The end-to-end typed stop of the enforce route is proved through the real controller (test_taf3_enforce_terminal_refusal's harness); the terminal gate is proved on the service.
"""
import ast
import subprocess
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_taf3_enforce_terminal_refusal as taf3
from test_taf3_enforce_terminal_refusal import _enforce, _engine

from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.workflow import contract_lifecycle as cl
from kriya.workflow import migration as mg
from kriya.workflow.authority_bundle import AUTHORITY_EXECUTION_UNAVAILABLE
from kriya.workflow.migration import (
    MigrationObligation,
    MigrationResolution,
    MigrationResolutionStatus,
    find_migration_incomplete,
    resolve_migration_resolution,
)
from kriya.workflow.terminal_gate_service import TerminalGateService
from kriya.workflow.workflow import _migration_identities
from kriya.workflow.worktree import _create_scoped_snapshot_sandbox, candidate_member_paths

REPO_ROOT = Path(__file__).resolve().parents[1]
_POM_BOTH = (
    "<project><dependencies>\n"
    "<dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId><version>2.11.0</version></dependency>\n"
    "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId><version>2.17.2</version></dependency>\n"
    "</dependencies></project>\n"
)
_POM_JACKSON_ONLY = (
    "<project><dependencies>\n"
    "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId><version>2.17.2</version></dependency>\n"
    "</dependencies></project>\n"
)
_GSON = "package com.example;\nimport com.google.gson.Gson;\npublic class JsonService { Gson g; }\n"
_JACKSON = "package com.example;\nimport com.fasterxml.jackson.databind.ObjectMapper;\npublic class JsonService { ObjectMapper m; }\n"
_GOAL = "Replace the existing JSON serialization library with the JSON library already approved for this repository."
_DIR = "src/main/java/com/example"
_NAMED = ["build", "target", "dist", "node_modules"]


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _write(root, files):
    for relative, content in files.items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _repo(path, files):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "Test")
    _write(path, files)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _obligation(consumers=(f"{_DIR}/util/JsonService.java",)):
    return MigrationObligation(
        migration_kind="dependency_or_technology_replacement", source_identity="gson", target_identity="jackson-databind",
        source_artifacts=["com.google.code.gson:gson"], target_artifacts=["com.fasterxml.jackson.core:jackson-databind"],
        grounded_consumers=list(consumers))


def _unlistable(monkeypatch):
    def cannot_list(*_args, **_kwargs):
        raise RuntimeError("git ls-files failed: injected")
    monkeypatch.setattr("kriya.repository.bound_tree.visible_repository_paths", cannot_list)


# ---- the original symptom: a directory NAME decided membership -------------------------------------------------------------------------------------

@pytest.mark.parametrize("segment", ["util"] + _NAMED)
def test_resolution_finds_a_tracked_consumer_whatever_its_directory_is_named(tmp_path, segment):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_BOTH, f"{_DIR}/{segment}/JsonService.java": _GSON})
    resolution = resolve_migration_resolution(_GOAL, str(root))
    assert resolution.status == MigrationResolutionStatus.RESOLVED
    assert resolution.obligation.grounded_consumers == [f"{_DIR}/{segment}/JsonService.java"]


def test_resolution_does_not_see_a_git_ignored_output_as_a_consumer(tmp_path):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_BOTH, ".gitignore": "out/\n", f"{_DIR}/util/Other.java": "package com.example; public class Other {}\n",
                                     "out/Gen.java": _GSON})
    assert resolve_migration_resolution(_GOAL, str(root)).status == MigrationResolutionStatus.NOT_APPLICABLE


def test_resolution_sees_an_untracked_visible_consumer(tmp_path):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_BOTH, f"{_DIR}/util/Other.java": "package com.example; public class Other {}\n"})
    _write(root, {f"{_DIR}/build/JsonService.java": _GSON})
    resolution = resolve_migration_resolution(_GOAL, str(root))
    assert resolution.status == MigrationResolutionStatus.RESOLVED and resolution.obligation.grounded_consumers == [f"{_DIR}/build/JsonService.java"]


@pytest.mark.parametrize("segment", ["util", "build", "target"])
def test_a_leftover_source_consumer_is_found_whatever_its_directory_is_named(tmp_path, segment):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_JACKSON_ONLY, f"{_DIR}/util/JsonService.java": _JACKSON, f"{_DIR}/{segment}/Legacy.java": _GSON})
    gap = find_migration_incomplete(_obligation(), str(root))
    assert gap is not None and gap["reason_codes"] == ["SOURCE_USAGE_REMAINS"]
    assert gap["source_usage_files"] == [f"{_DIR}/{segment}/Legacy.java"]


def test_a_fully_migrated_tracked_tree_is_still_none_and_a_deleted_consumer_is_not_migrated(tmp_path):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_JACKSON_ONLY, f"{_DIR}/util/JsonService.java": _JACKSON, f"{_DIR}/build/Migrated.java": _JACKSON})
    assert find_migration_incomplete(_obligation(), str(root)) is None
    (root / _DIR / "util" / "JsonService.java").unlink()            # tracked, deleted from the work tree: not a member, so the grounded consumer is unmigrated
    gap = find_migration_incomplete(_obligation(), str(root))
    assert gap is not None and gap["grounded_consumers"] == [f"{_DIR}/util/JsonService.java"]


def test_the_candidate_worktree_of_a_run_is_scanned_by_the_bound_tree(tmp_path):
    """find_migration_incomplete runs on the candidate worktree (a linked git worktree), not on the workspace."""
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_JACKSON_ONLY, f"{_DIR}/util/JsonService.java": _JACKSON})
    candidate = tmp_path / "candidate"
    _git(root, "worktree", "add", "-q", "--detach", str(candidate))
    _write(candidate, {f"{_DIR}/build/Legacy.java": _GSON})        # written by the candidate: untracked, visible
    gap = find_migration_incomplete(_obligation(), str(candidate))
    assert gap is not None and gap["source_usage_files"] == [f"{_DIR}/build/Legacy.java"]


@pytest.mark.parametrize("segment", ["util", "build", "dist"])
def test_the_reference_scan_finds_a_tracked_consumer_whatever_its_directory_is_named(tmp_path, segment):
    root = _repo(tmp_path / "repo", {"src/Api.java": "class Api { void foo() {} }\n", f"src/{segment}/Caller.java": "class Caller { void x() { foo(); } }\n"})
    assert cl._reference_scan(str(root), {}, ["foo"], "src/Api.java") == {f"src/{segment}/Caller.java": ["foo"]}


def test_the_reference_scan_ignores_a_git_ignored_output_and_keeps_its_overlay_semantics(tmp_path):
    root = _repo(tmp_path / "repo", {".gitignore": "out/\n", "src/Api.java": "class Api { void foo() {} }\n", "src/Old.java": "class Old { void x() { foo(); } }\n",
                                     "out/Stale.java": "class S { void x() { foo(); } }\n", "notes.txt": "call foo( here\n"})
    overlay = {"src/Old.java": "class Old { }\n", "src/New.java": "class New { void x() { foo(); } }\n", "src/Api.java": "class Api { void foo() { foo(); } }\n"}
    # the overlay replaces a tracked file (Old no longer calls foo), adds a file the tree does not have, and the owner is never its own consumer; any member file counts
    assert cl._reference_scan(str(root), overlay, ["foo"], "src/Api.java") == {"notes.txt": ["foo"], "src/New.java": ["foo"]}


_OWNER_V1 = "public class Owner {\n    public int total() {\n        return 1;\n    }\n}\n"
_OWNER_V2 = "public class Owner {\n    public long total() {\n        return 1L;\n    }\n}\n"
_CALLER = "public class Caller {\n    int use(Owner o) {\n        return o.total();\n    }\n}\n"


def _derive_transition(root, verified):
    authorization = SimpleNamespace(authorization_id="auth-total", provenance=SimpleNamespace(value="direct"), affected_owner="src/Owner.java",
                                    affected_symbol="total", allowed_change_category=SimpleNamespace(value="modify"))
    return cl.derive_contract_transition(
        workspace_path=str(root), registry=None, original_contents={"src/Owner.java": _OWNER_V1}, final_contents={"src/Owner.java": _OWNER_V2},
        authorizations=[authorization], transaction_id="tx1", candidate_hash="h", downstream_verified=verified)


@pytest.mark.parametrize("segment", ["util", "build", "target"])
def test_a_consumer_under_a_named_directory_makes_the_contract_transition_refuse_without_downstream_verification(tmp_path, segment):
    """The consequence the scan carries: invalidated consumers + no downstream verification = CONTRACT_CONSUMER_VERIFICATION_MISSING. A consumer the scan could not see
    (a tracked caller under build/) used to let the same transition through."""
    root = _repo(tmp_path / "repo", {"src/Owner.java": _OWNER_V1, f"src/{segment}/Caller.java": _CALLER})
    with pytest.raises(cl.ContractTransitionRefused) as raised:
        _derive_transition(root, False)
    assert raised.value.reason_code == cl.CONTRACT_CONSUMER_VERIFICATION_MISSING and f"src/{segment}/Caller.java" in raised.value.detail
    transition = _derive_transition(root, True)
    assert [item["consumer"] for item in transition.invalidated_consumers] == [f"src/{segment}/Caller.java"]


# ---- the parked root classes keep the legacy walk, unchanged (PARKED, not endorsed) ----------------------------------------------------------------

def test_a_pre_git_workspace_keeps_the_legacy_walk(tmp_path):
    _write(tmp_path, {"pom.xml": _POM_BOTH, f"{_DIR}/util/Seen.java": "package com.example; public class Seen {}\n", f"{_DIR}/build/JsonService.java": _GSON})
    assert sorted(candidate_member_paths(str(tmp_path), mg._IGNORED_DIRS)) == ["pom.xml", f"{_DIR}/util/Seen.java"]            # PARKED: the basename prune stands
    assert resolve_migration_resolution(_GOAL, str(tmp_path)).status == MigrationResolutionStatus.NOT_APPLICABLE
    assert list(mg._scan_java_files(str(tmp_path))) == [f"{_DIR}/util/Seen.java"]


def test_the_scoped_snapshot_sandbox_keeps_the_legacy_walk(tmp_path):
    workspace = tmp_path / "ws"
    _write(workspace, {f"{_DIR}/util/Seen.java": "package com.example; public class Seen {}\n", f"{_DIR}/build/Hidden.java": _GSON})
    sandbox = workspace / ".kriya" / "scoped-worktree"
    _create_scoped_snapshot_sandbox(str(workspace), str(sandbox))
    assert (sandbox / ".git").is_dir()
    assert list(mg._scan_java_files(str(sandbox))) == [f"{_DIR}/util/Seen.java"]                                               # PARKED (C2)


# ---- a Git-backed root Git cannot list is a typed stop, never "no consumer", "no migration" or [] ----------------------------------------------

SCANS = [
    ("_scan_java_files", lambda root: mg._scan_java_files(root)),
    ("resolve_migration_resolution", lambda root: resolve_migration_resolution(_GOAL, root)),
    ("find_migration_incomplete", lambda root: find_migration_incomplete(_obligation(), root)),
    ("_reference_scan", lambda root: cl._reference_scan(root, {}, ["foo"], "src/Api.java")),
    ("_migration_identities", lambda root: _migration_identities(_GOAL, root)),
]


@pytest.mark.parametrize("scan", [s[1] for s in SCANS], ids=[s[0] for s in SCANS])
def test_an_unlistable_git_backed_root_is_the_typed_error(tmp_path, monkeypatch, scan):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_BOTH, f"{_DIR}/util/JsonService.java": _GSON, "src/Api.java": "class Api { void foo() {} }\n"})
    scan(str(root))                                                                           # matched control: listable, no error
    _unlistable(monkeypatch)
    with pytest.raises(BoundTreeError) as raised:
        scan(str(root))
    assert raised.value.reason_code == BOUND_TREE_UNAVAILABLE


@pytest.mark.parametrize("scan", [s[1] for s in SCANS], ids=[s[0] for s in SCANS])
def test_a_contradicting_git_authority_is_the_typed_error(tmp_path, scan):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_BOTH, f"{_DIR}/util/JsonService.java": _GSON, "src/Api.java": "class Api { void foo() {} }\n"})
    (root / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
    with pytest.raises(BoundTreeError):
        scan(str(root))


def test_the_migration_identities_of_a_listable_root_are_unchanged(tmp_path):
    root = _repo(tmp_path / "repo", {"pom.xml": _POM_BOTH, f"{_DIR}/build/JsonService.java": _GSON})
    assert _migration_identities(_GOAL, str(root)) == [("gson", "jackson-databind")]
    assert _migration_identities("Add a displayName field to Customer", str(root)) == []                    # no replacement intent: still no migration
    assert _migration_identities(_GOAL, str(tmp_path / "missing")) == []                                    # a non-listing defect (no such directory) is still recorded as none


# ---- the terminal gate: the typed error leaves it, an ordinary validator defect is still the indeterminate gap ------------------------------------

def _terminal_gate(validator):
    service = object.__new__(TerminalGateService)
    service._validators = SimpleNamespace(find_migration_incomplete=validator)
    resolution = MigrationResolution(MigrationResolutionStatus.RESOLVED, obligation=_obligation())
    return service, SimpleNamespace(migration_resolution=resolution, obligation_ledger=None, candidate_root="/unused")


def test_the_terminal_gate_lets_the_membership_error_out():
    def validator(*_args, **_kwargs):
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, "injected at the terminal gate")
    service, request = _terminal_gate(validator)
    with pytest.raises(BoundTreeError, match="injected at the terminal gate"):
        service._migration_gap(request)


def test_the_terminal_gate_still_reports_an_ordinary_validator_defect_as_an_indeterminate_gap():
    def validator(*_args, **_kwargs):
        raise RuntimeError("validator broke")
    service, request = _terminal_gate(validator)
    assert "MIGRATION FINAL-STATE CHECK INDETERMINATE" in service._migration_gap(request)


# ---- end to end through the real enforce controller ---------------------------------------------------------------------------------------------

def _enforce_repo(tmp_path):
    repo = taf3._repo(tmp_path, tmp_path / "FILTER_RAN")
    _write(repo, {"pom.xml": _POM_BOTH, f"{_DIR}/build/JsonService.java": _GSON})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "java")
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["contract_compilation", "baseline_resolution"])
async def test_a_membership_failure_in_a_migration_scan_ends_the_enforce_run_typed(tmp_path, monkeypatch, site):
    """Two real scans precede generation: the contract compilation's ``_migration_identities`` and the enforce loop's one-time baseline resolution. The listing is made to
    fail ONLY under one of them at a time, so each site's own re-raise is what the run's outcome proves: the old ``except Exception`` of either turned the error into ``[]`` /
    an INDETERMINATE resolution and the run went on to generate. The membership error now ends the run as the existing AUTHORITY_EXECUTION_UNAVAILABLE outcome, nothing applied."""
    from _strict_doubles import strict_config
    monkeypatch.setattr(taf3, "GOAL", _GOAL)
    repo = _enforce_repo(tmp_path)
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    import kriya.repository.bound_tree as bound_tree
    listing = bound_tree.visible_repository_paths
    failed_under = []

    def cannot_list_under_the_site(*args, **kwargs):
        stack = traceback.extract_stack()
        names = {frame.name for frame in stack}
        under_contract_compilation = "_migration_identities" in names
        if "_scan_java_files" in names and under_contract_compilation == (site == "contract_compilation"):
            failed_under.append(site)
            raise RuntimeError("git ls-files failed: injected at the migration scan")
        return listing(*args, **kwargs)
    monkeypatch.setattr(bound_tree, "visible_repository_paths", cannot_list_under_the_site)
    result = await _enforce(repo, _engine(config, bind_filter=False))
    legacy = result.legacy_result
    assert failed_under, f"the migration scan under {site} was never reached (the run ended somewhere else)"
    assert legacy["status"] == "failure" and legacy["quality_gates_passed"] is False and legacy["files"] == [], legacy
    assert legacy["reason_codes"] == [AUTHORITY_EXECUTION_UNAVAILABLE] and legacy["failure_category"] == "verification_authority_unavailable", legacy
    assert "injected at the migration scan" in legacy["membership_authority"]["detail"]
    assert (repo / "app.py").read_text() == "original\n"


@pytest.mark.asyncio
async def test_the_matched_control_resolves_the_migration_and_is_not_the_typed_stop(tmp_path, monkeypatch):
    from _strict_doubles import strict_config
    monkeypatch.setattr(taf3, "GOAL", _GOAL)
    repo = _enforce_repo(tmp_path)
    result = await _enforce(repo, _engine(strict_config(autonomy={"spec_compliance_enabled": True}), bind_filter=False))
    assert AUTHORITY_EXECUTION_UNAVAILABLE not in (result.legacy_result.get("reason_codes") or []), result.legacy_result


# ---- structural guards ----------------------------------------------------------------------------------------------------------------------------

def _function(path, name):
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def _calls(node, name):
    return [sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and getattr(sub.func, "attr", getattr(sub.func, "id", None)) == name]


@pytest.mark.parametrize("path, function, call", [
    ("kriya/workflow/workflow.py", "_migration_identities", "resolve_migration_resolution"),
    ("kriya/workflow/enforce_loop.py", "run", "resolve_migration_resolution"),
    ("kriya/workflow/terminal_gate_service.py", "_migration_gap", "find_migration_incomplete"),
])
def test_the_membership_error_is_re_raised_ahead_of_every_broad_catch(path, function, call):
    node = _function(path, function)
    guarded = [try_node for try_node in ast.walk(node) if isinstance(try_node, ast.Try) and _calls(ast.Module(body=try_node.body, type_ignores=[]), call)]
    assert guarded, f"{function} no longer calls {call} inside a try"
    for try_node in guarded:
        types = [ast.unparse(handler.type) if handler.type is not None else "" for handler in try_node.handlers]
        assert types.index("BoundTreeError") < types.index("Exception"), types
        handler = try_node.handlers[types.index("BoundTreeError")]
        assert any(isinstance(sub, ast.Raise) and sub.exc is None for sub in ast.walk(handler))


def test_no_scan_walks_the_tree_by_directory_name_except_the_one_parked_branch():
    assert not _calls(_function("kriya/workflow/migration.py", "_scan_java_files"), "walk")
    assert not _calls(_function("kriya/workflow/contract_lifecycle.py", "_reference_scan"), "walk")
    assert len(_calls(_function("kriya/workflow/worktree.py", "candidate_member_paths"), "walk")) == 1
