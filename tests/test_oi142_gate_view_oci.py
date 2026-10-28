"""OI-142 under the REAL OCI runtime (owner batch 2026-10-24, item 2): contained production verification runs against the exact bound gate view, not
directly against the candidate worktree.

MEASURED (d2 batch): before this change ``_open_gate_view`` returned ``None`` whenever execution was contained, so the OCI workspace mount WAS the
shared, persistent worktree and every forgery that tests/test_oi142_gate_view.py plants in a git-ignored path (a ``.pyc``, a sourceless ``.pyc``, an
ignored ``conftest.py`` / ``pytest.ini``, ignored generated source, an ignored symlink) decided a contained verdict - the default production mode.
These tests re-run the ORIGINAL scenarios (imported from that file, not re-invented) through the validator's production path with contained OCI
execution, against real Git repositories, with the honest PASS/FAIL controls that keep them from passing vacuously.

The view is the existing owner's (kriya/tools/gate_view.py): nothing here builds a second one. The one addition under containment is that the
origin tree's project venv - an execution-environment input hoisted outside every view - is bind-mounted into the view at ``.kriya/venv``
(read-only for every gate; writable only for the steps that create and populate it).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_oi142_gate_view import (
    CALC1,
    CALC2,
    CALC_HELPER,
    CALC_LIB,
    FORGED_PASS,
    PLANT_CONFTEST_TEST,
    PLANT_PYC_TEST,
    TEST2,
    _repo,
    plant_conftest,
    plant_ini_that_collects_nothing,
    plant_timestamp_pyc,
)

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.file_integrity import VerificationGateCreatedFiles, VerificationTreeBinding
from kriya.workflow.worktree import repository_content_paths

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _validator(root, **kwargs):
    """Contained, OCI - stated explicitly (the packaged defaults still mean host execution until the default migration). A guard so these tests
    can never silently run in the host shape."""
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root),
                                     autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"), **kwargs)
    assert validator.autonomy_cfg.contained_execution_required is True and validator.autonomy_cfg.containment_backend == "oci"
    return validator


def _git_status_without_kriya(root) -> str:
    """``git status --porcelain`` minus Kriya's own ``.kriya/`` (the contained gate's pytest venv lives in the origin's ``.kriya``; a real Kriya
    worktree excludes it, these throwaway test repositories do not)."""
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, capture_output=True, text=True).stdout
    return "\n".join(line for line in out.splitlines() if ".kriya/" not in line)


def _gate(root, *, target=None, bound=True, candidate_paths=("calc.py",), validator=None):
    validator = validator or _validator(root)
    if bound:
        validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), list(candidate_paths))
    result = validator.run_tests(target) if target else validator.run_tests()
    return result["success"], validator, result


# ----------------------------------------------------------------------------------------------- honest controls


def test_the_honest_wrong_candidate_fails_and_the_honest_right_one_passes_under_oci(tmp_path):
    """Without these two every other test here could pass vacuously (a mount that fails everything)."""
    assert _gate(_repo(tmp_path, calc=CALC1, name="wrong"))[0] is False
    assert _gate(_repo(tmp_path, calc=CALC2, name="right"))[0] is True


@pytest.fixture
def outside_helper(tmp_path):
    path = tmp_path / "outside" / "evil_helper.py"
    path.parent.mkdir()
    path.write_text("V = 2\n")
    return path


# ----------------------------------------------------------------------------------------------- the original forgeries


@pytest.mark.parametrize("label,calc,planter,target", FORGED_PASS, ids=[row[0] for row in FORGED_PASS])
def test_under_oci_ignored_residue_cannot_forge_a_pass(tmp_path, outside_helper, label, calc, planter, target):
    """pytest.ini that collects nothing, ignored generated source, timestamp/unchecked-hash/sourceless ``.pyc``, ignored ``conftest.py`` and the ignored
    symlink - each planted in a git-ignored path of the worktree the reviewed (WRONG) candidate sits in. The honest verdict is FAIL."""
    root = _repo(tmp_path, calc=calc)
    planter(root, outside_helper)
    planted = sorted(p for p in root.rglob("*") if ".git" not in p.parts and (p.is_file() or p.is_symlink()) and "__pycache__" not in p.parts)
    success, validator, _result = _gate(root, target=target)
    assert success is False, f"{label}: a planted ignored file decided the contained verdict"
    assert validator.tree_binding.changes() == [] and validator.tree_binding.created() == []
    assert _git_status_without_kriya(root) == ""
    # not destructive: the owner's tree keeps every planted file (no purge, no git clean of the worktree)
    assert sorted(p for p in root.rglob("*") if ".git" not in p.parts and (p.is_file() or p.is_symlink()) and "__pycache__" not in p.parts
                  and ".kriya" not in p.parts and ".pytest_cache" not in p.parts) == [p for p in planted if ".kriya" not in p.parts]


@pytest.mark.parametrize("planter", [lambda r: plant_timestamp_pyc(r, CALC1), lambda r: plant_conftest(r, "import calc\ncalc.f = lambda: 1\n")],
                         ids=["timestamp-pyc", "conftest"])
def test_under_oci_the_mirror_a_correct_candidate_cannot_be_forced_to_fail(tmp_path, planter):
    root = _repo(tmp_path, calc=CALC2)
    planter(root)
    assert _gate(root)[0] is True


def test_under_oci_the_ini_that_collects_nothing_is_not_an_exit_5_pass(tmp_path):
    """The case that started OI-142: an ignored ``tests/pytest.ini`` with ``-k nomatch`` makes pytest collect nothing (exit 5, which counted as success)."""
    root = _repo(tmp_path, calc=CALC1)
    plant_ini_that_collects_nothing(root)
    assert _gate(root, target="tests/test_calc.py")[0] is False


def test_under_oci_the_container_sees_the_bound_set_and_not_the_worktrees_ignored_residue(tmp_path, outside_helper):
    """The mechanism, observed from INSIDE the container: what a command lists in its workspace during a gate."""
    root = _repo(tmp_path, calc=CALC_LIB)
    (root / "lib").mkdir(exist_ok=True)
    (root / "lib" / ".gitignore").write_text("*\n")
    (root / "lib" / "helper.py").write_text("V = 2\n")
    os.symlink(outside_helper, root / "lib" / "linked.py")
    plant_timestamp_pyc(root, CALC1)
    (root / "helper.pyc").write_bytes(b"sourceless")
    validator = _validator(root)
    validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    listing = []
    with validator.verification_session("tests"):
        assert validator.workspace_path != str(root)  # the gate runs in the view...
        result = validator._run_cmd_with_timeout(
            ["python3", "-c", "import os\nfor d, ds, fs in os.walk('.'):\n    ds[:] = [x for x in ds if x != '.kriya']\n"
             "    [print(os.path.join(d, f)) for f in fs]"], cwd=validator.workspace_path)
        listing = sorted(result["stdout"].split())
    assert result["returncode"] == 0, result["stderr"]
    assert "./calc.py" in listing and "./tests/test_calc.py" in listing  # ...which holds exactly the bound content,
    assert not any("helper" in name or name.endswith(".pyc") or "linked" in name for name in listing), listing  # ...and none of the planted residue
    assert validator.workspace_path == str(root)  # restored afterwards


# ----------------------------------------------------------------------------------------------- prior-gate residue


@pytest.mark.parametrize("source,a_outcome", [(PLANT_PYC_TEST, True), (PLANT_CONFTEST_TEST, "stopped")], ids=["pyc", "conftest"])
def test_under_oci_residue_written_by_one_gate_cannot_affect_the_next_gate(tmp_path, source, a_outcome):
    root = _repo(tmp_path, extra={"tests/test_plant.py": source})
    validator = _validator(root)
    validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    try:
        first = _gate(root, target="tests/test_plant.py", validator=validator, bound=False)[0]
    except VerificationGateCreatedFiles:
        first = "stopped"
    second = _gate(root, target="tests/test_calc.py", validator=validator, bound=False)[0]
    assert first == a_outcome
    assert second is False  # B must not consume what A left behind
    assert not (root / "tests" / "conftest.py").exists()  # and A's residue never reached the worktree at all


# ----------------------------------------------------------------------------------------------- intended inputs and outputs still work


def test_under_oci_untracked_work_and_an_ignored_candidate_path_are_in_the_view(tmp_path):
    """The view holds the BOUND set, so an honest candidate with untracked WIP or a candidate file under an ignored path still passes."""
    root = _repo(tmp_path, calc="from gen import value\nfrom wip import bonus\n\ndef f():\n    return value() + bonus()\n", test=TEST2,
                 extra={".gitignore": "gen/\n__pycache__/\n"})
    (root / "gen").mkdir()
    (root / "gen" / "__init__.py").write_text("def value():\n    return 2\n")
    (root / "wip.py").write_text("def bonus():\n    return 0\n")
    assert _gate(root, candidate_paths=("calc.py", "gen/__init__.py"))[0] is True


def test_under_oci_the_test_execution_report_and_the_declared_outputs_still_work(tmp_path):
    """The pytest report is written beneath ``.kriya/test-reports`` inside the container's workspace (the view) and read back by the host from there."""
    root = _repo(tmp_path, calc=CALC2)
    success, _validator_, result = _gate(root)
    assert success is True
    assert result["test_execution"]["completeness"] == "COMPLETE" and result["test_execution"]["status_counts"] == {"passed": 1}
    assert not (root / ".kriya" / "test-reports").exists()  # the report died with the view; it never reached the worktree


def test_under_oci_the_project_venv_lives_in_the_origin_for_its_scope_and_is_gone_after_it(tmp_path):
    """R3: the venv is hoisted OUT of the view (it lives in the origin, so the gates of one scope share it) but it has the lifetime of the verification scope that
    built it: gates inside one session share it, and nothing of it outlives the session (a later attempt builds its own)."""
    root = _repo(tmp_path, calc=CALC2, extra={"requirements.txt": ""})
    validator = _validator(root)
    venv_python = root / ".kriya" / "venv" / "bin" / "python"
    with validator.verification_session("tests"):
        assert _gate(root, validator=validator)[0] is True
        assert os.path.lexists(venv_python), "the venv must be created in the ORIGIN tree, not inside the throwaway view"
        first_attempts = validator.venv_install_attempts
        assert _gate(root, validator=validator, bound=False)[0] is True
        assert validator.venv_install_attempts == first_attempts  # reused inside the scope (the install cache), not rebuilt per view
    assert not os.path.lexists(root / ".kriya" / "venv"), "the venv outlived the scope that built it"


def test_under_oci_a_gate_cannot_modify_the_project_venv(tmp_path):
    """The venv is mounted READ-ONLY for every gate: candidate code that tries to plant something in it (a ``.pth`` that runs on every interpreter
    start) fails, and the origin venv is byte-identical afterwards. (R3: both gates run inside one session, so the venv the first gate built is the one the second
    gate's candidate code tries to poison - without it the before/after comparison would be of two empty listings.)"""
    plant = (
        "import os\n\ndef test_try_to_poison_the_venv():\n"
        "    target = '.kriya/venv/lib/evil.pth'\n"
        "    try:\n        open(target, 'w').write('import os')\n        os.makedirs('.kriya/venv/bin/x', exist_ok=True)\n"
        "    except OSError as error:\n        assert 'Read-only' in str(error), error\n    else:\n        raise AssertionError('the venv was writable')\n"
    )
    root = _repo(tmp_path, calc=CALC2, extra={"requirements.txt": "", "tests/test_poison.py": plant})
    validator = _validator(root)
    venv = root / ".kriya" / "venv"
    with validator.verification_session("tests"):
        _gate(root, validator=validator)  # creates and populates the venv
        before = sorted(str(p.relative_to(venv)) for p in venv.rglob("*") if "__pycache__" not in p.parts)
        assert before, "premise: the venv exists and is populated while the scope is open"
        success, _v, result = _gate(root, target="tests/test_poison.py", validator=validator, bound=False)
        assert success is True, result["output"]
        after = sorted(str(p.relative_to(venv)) for p in venv.rglob("*") if "__pycache__" not in p.parts)
        assert before == after and not (venv / "lib" / "evil.pth").exists()


def test_under_oci_the_view_is_removed_and_the_worktree_is_untouched_after_a_contained_gate(tmp_path):
    from test_oi142_gate_view import _views

    root = _repo(tmp_path, calc=CALC2)
    before = sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if ".git" not in p.parts)
    _gate(root)
    assert not os.path.isdir(_views()) or os.listdir(_views()) == []  # the view was discarded
    after = sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if ".git" not in p.parts and ".kriya" not in p.parts
                   and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts)
    assert [p for p in before if ".kriya" not in p] == after


def test_under_oci_helper_module_candidates_still_resolve_in_the_view(tmp_path):
    """A tracked helper module (CALC_HELPER imports ``helper``) is part of the bound set and imports normally; only the sourceless ignored one is absent."""
    root = _repo(tmp_path, calc=CALC_HELPER, extra={"helper.py": "V = 2\n"})
    assert _gate(root, candidate_paths=("calc.py", "helper.py"))[0] is True


# ----------------------------------------------------------------------------------------------- Maven: the build output lives in the view


_POM = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <modelVersion>4.0.0</modelVersion>\n'
    "  <groupId>demo</groupId><artifactId>oci-view</artifactId><version>1.0.0</version>\n"
    "  <properties><maven.compiler.release>17</maven.compiler.release><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>\n"
    "</project>\n"
)
_APP = 'package demo;\npublic class App { public static void main(String[] a) { System.out.println("hi"); } }\n'


def test_under_oci_a_maven_compile_gate_builds_in_the_view_and_leaves_nothing_in_the_worktree(tmp_path):
    """Intended build output still works: the compile succeeds through the two-phase contained Maven path with the view as the workspace mount,
    ``target/`` exists only in the (discarded) view, and the worktree is exactly as it was."""
    root = tmp_path / "mvn"
    (root / "src/main/java/demo").mkdir(parents=True)
    (root / "pom.xml").write_text(_POM)
    (root / "src/main/java/demo/App.java").write_text(_APP)
    (root / ".gitignore").write_text("target/\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=x", *args], cwd=root, check=True, capture_output=True)
    validator = _validator(root)
    validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["src/main/java/demo/App.java"])
    result = validator.run_compile_check(["src/main/java/demo/App.java"])
    assert result["success"], result.get("output")
    assert not (root / "target").exists()  # the build output died with the view
    assert _git_status_without_kriya(root) == ""
