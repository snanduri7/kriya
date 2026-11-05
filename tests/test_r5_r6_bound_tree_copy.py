"""R5/R6 (Wave-2 Step 2): every authority/baseline copy of the project is EXACTLY the canonical bound tree.

MEASURED at f3b7d39 and again at 039c0ca (WAVE2_FINAL_CLOSURE_2026-10-28/passE_copy_sites.py): four copy sites - the authority-bundle export, the JVM acceptance
copy, the baseline-authority export (workflow.py) and the baseline replay copy (deterministic_failure_diagnostic / regression_attribution) - filtered the project by
DIRECTORY NAME (``build``, ``target``, ...), dropped five legitimate tracked sources (``src/main/java/com/acme/build/Gen.java``, ``.../target/T.java``,
``build/scripts/make.py``, ``docs/target/readme.md``, ``tools/build/Helper.java``), and the walkers that digest the copy (``authority_bundle._tree_digest``,
``acceptance_jvm._main_digest`` / ``_main_classes``) used the same blacklist, so an edit under ``build/`` did not change the authority digest.

The canonical bound set is Git's own view (``kriya.repository.snapshot.visible_repository_paths``: tracked + untracked non-ignored, ``.kriya`` never); one function,
``kriya.tools.gate_view.copy_bound_tree``, materialises it for all four sites and proves each copy. Host-side only: no container is involved.
"""
import ast
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _b2c_fixtures import ACCEPTANCE, EXACT_GOAL, base_revision, candidate, maven_workspace

from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError, copy_bound_tree
from kriya.repository.snapshot import visible_repository_paths
from kriya.tools.containment import ContainmentSetupError
from kriya.workflow import acceptance_jvm as jvm
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow import authority_bundle as ab
from kriya.workflow import deterministic_failure_diagnostic as dfd
from kriya.workflow.requirements import derive_requirements

REPO_ROOT = Path(__file__).resolve().parent.parent

# legitimate project content whose path merely contains an output-like directory name
LEGIT = (
    "src/main/java/com/acme/App.java", "src/main/java/com/acme/build/Gen.java", "src/main/java/com/acme/target/T.java",
    "build/scripts/make.py", "docs/target/readme.md", "tools/build/Helper.java", "target/generated/G.java",   # tracked AND not git-ignored
)
UNTRACKED_VISIBLE = ("build/notes.txt",)                      # untracked, not ignored: Git's own view includes it
IGNORED = ("target/classes/A.class", "__pycache__/x.pyc", ".gradle/cache.bin", "app.log")
GITIGNORE = "/target/classes/\n__pycache__/\n.gradle/\n*.log\n"


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _write(root, relpath, text):
    path = Path(root) / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _files(root):
    return {os.path.relpath(os.path.join(current, name), root).replace(os.sep, "/") for current, _dirs, names in os.walk(root) for name in names}


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    for relpath in LEGIT:
        _write(root, relpath, f"// {relpath}\n")
    _write(root, ".gitignore", GITIGNORE)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    for relpath in UNTRACKED_VISIBLE:
        _write(root, relpath, "untracked but visible\n")
    for relpath in IGNORED:
        _write(root, relpath, "generated")
    return root


def _bound(root):
    return {path for path in visible_repository_paths(str(root)) if os.path.lexists(os.path.join(root, path))}


# ------------------------------------------------------------------------------------------------------------- the one materialisation

def test_the_copy_is_exactly_the_canonical_bound_tree_whatever_the_directories_are_called(tmp_path):
    repo = _repo(tmp_path)
    dest = tmp_path / "dest"
    returned = copy_bound_tree(str(repo), str(dest))
    bound = _bound(repo)
    assert set(LEGIT) | set(UNTRACKED_VISIBLE) | {".gitignore"} == bound          # the premise: the canonical set holds every legitimate file and no generated one
    assert _files(dest) == bound and set(returned) == bound                          # nothing dropped, nothing added, .git never copied
    for relpath in LEGIT + UNTRACKED_VISIBLE:                                        # 1, 2, 6: legitimate content under build/ and target/ - tracked and untracked
        assert (dest / relpath).read_text() == (repo / relpath).read_text()
    assert not any((dest / relpath).exists() for relpath in IGNORED)                 # 4, 5: ignored / generated content stays out
    assert not (dest / ".git").exists()


def test_a_tracked_path_deleted_in_the_origin_is_not_copied_and_does_not_fail(tmp_path):
    repo = _repo(tmp_path)
    (repo / "build/scripts/make.py").unlink()
    dest = tmp_path / "dest"
    returned = copy_bound_tree(str(repo), str(dest))
    assert "build/scripts/make.py" not in returned and not (dest / "build/scripts/make.py").exists()
    assert "src/main/java/com/acme/build/Gen.java" in returned


def test_an_origin_git_cannot_list_is_a_typed_refusal_never_a_name_filtered_approximation(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, "src/main/java/com/acme/build/Gen.java", "// gen\n")
    with pytest.raises(BoundTreeError) as refused:
        copy_bound_tree(str(plain), str(tmp_path / "dest"))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert not (tmp_path / "dest" / "src").exists()


def test_a_destination_that_cannot_be_written_is_the_same_typed_refusal(tmp_path):
    repo = _repo(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the copy's directory should be")
    with pytest.raises(BoundTreeError) as refused:
        copy_bound_tree(str(repo), str(blocker))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE


# ------------------------------------------------------------------------------------------------------------- site 1: the authority bundle

def _authority_copy(repo, tmp_path, name):
    """Run the production ``run_authority_bundle`` as far as the copy: the factory records what it was handed and refuses, which is the run's own typed stop."""
    seen = {}

    def factory(root):
        seen["files"] = _files(root)
        raise ContainmentSetupError("stop after the copy")

    run = ab.run_authority_bundle(None, str(repo), state_root=str(tmp_path / f"state-{name}"), validator_factory=factory)
    return run, seen


def test_the_authority_bundle_runs_on_exactly_the_bound_tree_and_its_digest_covers_it(tmp_path):
    repo = _repo(tmp_path)
    run, seen = _authority_copy(repo, tmp_path, "a")
    assert run.reason_code == ab.AUTHORITY_EXECUTION_UNAVAILABLE
    assert seen["files"] == _bound(repo)
    base = run.candidate_digest
    # 3: a change to ANY legitimate bound file changes the digest, including those under build/ and target/
    for relpath in LEGIT + UNTRACKED_VISIBLE:
        original = (repo / relpath).read_text()
        (repo / relpath).write_text(original + "// changed\n")
        assert _authority_copy(repo, tmp_path, "b")[0].candidate_digest != base, relpath
        (repo / relpath).write_text(original)
    # 4, 5: generated / ignored content is not part of the bound tree, so it does not move the digest
    for relpath in IGNORED:
        (repo / relpath).write_text("a different generation")
        assert _authority_copy(repo, tmp_path, "c")[0].candidate_digest == base, relpath


def test_the_authority_digest_notices_a_bound_file_that_changes_or_disappears_after_the_copy(tmp_path):
    repo = _repo(tmp_path)
    export = tmp_path / "export"
    bound = copy_bound_tree(str(repo), str(export))
    base = ab._tree_digest(str(export), bound)      # pylint: disable=protected-access
    (export / "build/scripts/make.py").write_text("TAMPERED")
    assert ab._tree_digest(str(export), bound) != base      # pylint: disable=protected-access
    (export / "build/scripts/make.py").write_text("// build/scripts/make.py\n")
    assert ab._tree_digest(str(export), bound) == base      # pylint: disable=protected-access
    (export / "target/generated/G.java").unlink()
    assert ab._tree_digest(str(export), bound) != base      # pylint: disable=protected-access
    (export / "target/generated/G.java").write_text("// target/generated/G.java\n")
    _write(export, "target/classes/Generated.class", "output the run produced beside the bound set")
    assert ab._tree_digest(str(export), bound) == base      # pylint: disable=protected-access
    (export / "identical.txt").write_text((export / "docs/target/readme.md").read_text())
    (export / "docs/target/readme.md").unlink()             # a bound file replaced by a link to IDENTICAL bytes is a different tree entry all the same
    (export / "docs/target/readme.md").symlink_to(export / "identical.txt")
    assert ab._tree_digest(str(export), bound) != base      # pylint: disable=protected-access


def test_an_authority_run_over_a_tree_git_cannot_list_stops_unavailable_before_any_validator(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, "build/Util.java", "// util\n")
    calls = []
    run = ab.run_authority_bundle(None, str(plain), state_root=str(tmp_path / "state"), validator_factory=lambda root: calls.append(root))
    assert run.reason_code == ab.AUTHORITY_EXECUTION_UNAVAILABLE and "bound tree" in run.reason and calls == []
    assert run.verdict == ab.VERDICT_INDETERMINATE and run.candidate_digest is None       # an environment outcome: never PASS or FAIL


# ------------------------------------------------------------------------------------------------------------- site 2: JVM acceptance

JVM_LEGIT = ("src/main/java/demo/build/Gen.java", "src/main/java/demo/target/T.java")


def _jvm_candidate(tmp_path):
    ws = maven_workspace(tmp_path / "ws")
    cand = candidate(ws, tmp_path / "cand", None)
    for relpath in JVM_LEGIT:
        _write(cand, relpath, f"package demo.{relpath.split('/')[-2]};\npublic class {Path(relpath).stem} {{ }}\n")
    _write(cand, "build/scripts/make.py", "# make\n")
    _write(cand, "target/generated/G.java", "// authoritative\n")
    _write(cand, ".gitignore", GITIGNORE)
    for relpath in IGNORED:
        _write(cand, relpath, "generated")
    return ws, cand


def _jvm_copy(ws, cand, tmp_path):
    path = tmp_path / "acceptance.java"
    path.write_text(ACCEPTANCE)
    artifact = ao.load_acceptance(str(path), derive_requirements(EXACT_GOAL), str(tmp_path / "artifacts"))
    seen = {}

    def factory(root):
        seen["files"] = _files(root)
        return SimpleNamespace(stack="python")           # not a JVM validator: the run refuses right after the copy and its digests are taken

    run = jvm.run_java_acceptance(artifact, str(cand), candidate_paths=[], base_revision=base_revision(ws), validator_factory=factory)
    return run, seen


def test_the_jvm_acceptance_copy_is_the_bound_tree_and_its_main_classes_and_digest_follow_it(tmp_path):
    ws, cand = _jvm_candidate(tmp_path)
    run, seen = _jvm_copy(ws, cand, tmp_path)
    assert run.refusal is not None and run.refusal.reason_code == "ACCEPTANCE_RUNNER_UNSUPPORTED" and "files" in seen
    assert seen["files"] == _bound(cand)
    assert {"build/scripts/make.py", "target/generated/G.java"} <= seen["files"] and not (seen["files"] & set(IGNORED))
    assert run.candidate_classes == ["demo.Calc", "demo.build.Gen", "demo.target.T"]          # a package named build/target is a package
    base = run.candidate_digest
    for relpath in JVM_LEGIT:                       # 3: an edit to a bound main source under build/ or target/ changes the digest
        original = (cand / relpath).read_text()
        (cand / relpath).write_text(original + "// changed\n")
        assert _jvm_copy(ws, cand, tmp_path)[0].candidate_digest != base, relpath
        (cand / relpath).write_text(original)
    for relpath in IGNORED:                         # 4, 5
        (cand / relpath).write_text("another generation")
        assert _jvm_copy(ws, cand, tmp_path)[0].candidate_digest == base, relpath


def test_a_jvm_acceptance_whose_bound_tree_cannot_be_copied_is_a_typed_refusal_not_acceptance_evidence(tmp_path):
    ws, cand = _jvm_candidate(tmp_path)

    def refuse(_origin, _destination):
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, "git ls-files failed")

    factory_calls = []
    with patch.object(jvm, "copy_bound_tree", refuse):
        path = tmp_path / "acceptance.java"
        path.write_text(ACCEPTANCE)
        artifact = ao.load_acceptance(str(path), derive_requirements(EXACT_GOAL), str(tmp_path / "artifacts"))
        run = jvm.run_java_acceptance(artifact, str(cand), candidate_paths=[], base_revision=base_revision(ws), validator_factory=lambda root: factory_calls.append(root))
    assert run.refusal is not None and run.refusal.reason_code == jvm.ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE and "bound tree" in str(run.refusal)
    assert factory_calls == [] and run.candidate_classes is None and not run.result and run.report is None       # nothing was run, nothing measured


# ------------------------------------------------------------------------------------------------------------- site 3: the baseline-authority export

def test_the_baseline_authority_export_is_the_bound_tree_plus_the_repositorys_own_git_metadata(tmp_path):
    from kriya.config.config import AutonomyConfig
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow import workflow as wf

    repo = _repo(tmp_path)
    seen = {}

    def run_tests(self, **_kwargs):
        seen["files"] = _files(self.workspace_path)
        seen["git"] = os.path.isdir(os.path.join(self.workspace_path, ".git"))
        return {"success": True, "output": ""}

    def fake_baseline(*_args, judge_suite=None, **_kwargs):
        try:
            judge_suite()
        except Exception:                                   # pylint: disable=broad-exception-caught  # the stub result ends the run; only the export matters here
            pass
        return SimpleNamespace(unavailable_authorities=lambda: [])

    config = AutonomyConfig(contained_execution_required=True, containment_backend="oci")
    with patch.object(PolymorphicValidator, "run_tests", run_tests), patch("kriya.workflow.contract_baseline.run_baseline_authorities", fake_baseline):
        wf.run_contract_baseline(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), str(repo), autonomy_cfg=config, config=SimpleNamespace(kriya=SimpleNamespace()))
    assert "files" in seen, "the export was never built"
    assert seen["git"] is True                              # the documented exception: the base revision is read from the tree's own .git
    assert {path for path in seen["files"] if not path.startswith(".git/")} == _bound(repo)


# ------------------------------------------------------------------------------------------------------------- site 4: the baseline replay copy (R6)

def test_the_baseline_replay_copy_is_the_bound_tree(tmp_path):
    repo = _repo(tmp_path)
    dest = tmp_path / "replay"
    dest.mkdir()
    dfd._copy_baseline_workspace(str(repo), str(dest))      # pylint: disable=protected-access
    assert _files(dest) == _bound(repo)
    assert (dest / "src/main/java/com/acme/build/Gen.java").exists() and not (dest / "target/classes/A.class").exists()


def test_a_baseline_replay_of_a_tree_git_cannot_list_is_no_conclusion_never_a_verdict(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, "build/Util.java", "// util\n")
    store = dfd.DeterministicFailureDiagnosticStore()
    signature = ("compile", "same")
    with pytest.raises(BoundTreeError):
        dfd._copy_baseline_workspace(str(plain), str(tmp_path / "dest"))        # pylint: disable=protected-access
    outcome = dfd.evaluate_candidate_independent_failure(
        store=store, fail_type="compile", current_failure_signature=signature, current_error_text="error", previous_failure_signature=signature,
        workspace_changed=True, has_implicated_files=False, authoritative_workspace_path=str(plain), known_files=[], autonomy_cfg=None, subtask_id="s1")
    assert outcome is None and store.get("s1", signature) is None       # nothing recorded, so the existing retry behaviour is untouched


# ------------------------------------------------------------------------------------------------------------- 7: nothing reapplies the old blacklist

_SITES = (
    ("kriya/workflow/authority_bundle.py", ("run_authority_bundle", "_tree_digest")),
    ("kriya/workflow/acceptance_jvm.py", ("run_java_acceptance", "_main_digest", "_main_classes")),
    ("kriya/workflow/workflow.py", ("exported",)),
    ("kriya/workflow/deterministic_failure_diagnostic.py", ("_copy_baseline_workspace",)),
)
_OUTPUT_NAMES = {"target", "build", ".gradle", "node_modules", "dist", "__pycache__", ".pytest_cache", ".venv", "venv"}
_RETIRED_NAMES = {"_COPY_IGNORE", "_BASELINE_COPY_EXCLUDED_DIRS"}


def _function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


@pytest.mark.parametrize("relpath, functions", _SITES)
def test_no_site_can_reapply_a_directory_name_filter(relpath, functions):
    tree = ast.parse((REPO_ROOT / relpath).read_text(encoding="utf-8"))
    defined = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)}
    assert not defined & _RETIRED_NAMES, (relpath, "the blacklist constant is back")
    for name in functions:
        body = _function(tree, name)
        calls = {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "") for node in ast.walk(body) if isinstance(node, ast.Call)}
        literals = {node.value for node in ast.walk(body) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        assert not calls & {"ignore_patterns", "walk"}, (relpath, name, "a directory walk or ignore pattern decides what is measured")
        assert not literals & _OUTPUT_NAMES, (relpath, name, "an output-directory name decides what is measured")


@pytest.mark.parametrize("relpath, function", [("kriya/workflow/authority_bundle.py", "run_authority_bundle"), ("kriya/workflow/acceptance_jvm.py", "run_java_acceptance"),
                                              ("kriya/workflow/workflow.py", "exported"), ("kriya/workflow/deterministic_failure_diagnostic.py", "_copy_baseline_workspace")])
def test_every_site_materialises_the_tree_through_the_one_function(relpath, function):
    tree = ast.parse((REPO_ROOT / relpath).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(_function(tree, function)) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "copy_bound_tree"]
    assert len(calls) == 1, (relpath, function)
