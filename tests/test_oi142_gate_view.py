"""OI-142 (owner decisions Q4 2026-10-20 and 2026-10-23): a verification gate executes in a FRESH Kriya-owned view of the bound tree, never in the
shared persistent worktree, so ignored/generated residue cannot decide a verdict.

MEASURED before the fix (closure/OI142_CHARACTERIZATION.md): seven forms planted in GIT-IGNORED paths forged a later pytest-gate PASS (and the
mirror, a forged FAIL) through the real `PolymorphicValidator.run_tests` with the tree binding bound and `git status --porcelain` clean: a timestamp
`.pyc`, an unchecked-hash `.pyc`, a sourceless `.pyc`, an ignored `tests/conftest.py`, an ignored `tests/pytest.ini` that collects nothing (exit 5 counts as
success), ignored generated source and an ignored symlink module.

These tests drive the REAL validator against REAL Git repositories (no stand-in for the gate): the reviewed `calc.f()` returns 1 and the tracked test
asserts 2, so the honest verdict is FAIL and a True is a forged PASS; the `correct` rows invert it (honest PASS, a forged FAIL is the mirror)."""
import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
from importlib._bootstrap_external import _code_to_hash_pyc, _code_to_timestamp_pyc
from pathlib import Path

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools import gate_view, venv_integrity
from kriya.tools.gate_view import (
    GATE_VIEW_IDENTITY_MISMATCH,
    GATE_VIEW_PATH_CONFUSION,
    GATE_VIEW_REASON_CODES,
    GATE_VIEW_UNAVAILABLE,
    GateViewError,
    build_gate_view,
)
from kriya.tools.validate import RUNTIME_VERIFICATION_GATE, PolymorphicValidator
from kriya.workflow.file_integrity import VerificationGateCreatedFiles, VerificationTreeBinding, VerificationTreeMutated
from kriya.workflow.worktree import repository_content_paths

CALC1 = "def f():\n    return 1\n"
CALC2 = "def f():\n    return 2\n"  # same size as CALC1
CALC_HELPER = "import helper\n\ndef f():\n    return helper.V\n"
CALC_LIB = "from lib import helper\n\ndef f():\n    return helper.V\n"
TEST2 = "import calc\n\ndef test_f():\n    assert calc.f() == 2\n"


def _state_root():
    from kriya.core.state_paths import ENV_STATE_DIR

    return os.environ[ENV_STATE_DIR]


def _views():
    return gate_view.views_directory(_state_root())


def _build(root, **kwargs):
    return build_gate_view(str(root), state_root=_state_root(), **kwargs)


def _git(root, *args):
    subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=x", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path, calc=CALC1, test=TEST2, extra=None, name="cand"):
    root = tmp_path / name
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(calc)
    (root / "tests" / "test_calc.py").write_text(test)
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n")
    for relpath, text in (extra or {}).items():
        (root / relpath).parent.mkdir(parents=True, exist_ok=True)
        (root / relpath).write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "reviewed")
    return root


# --- the planted forms: every one is written to a git-IGNORED path --------------------------------------------------------------

def plant_timestamp_pyc(root, forged=CALC2):
    src = root / "calc.py"
    code = compile(forged, str(src), "exec", dont_inherit=True)
    pyc = Path(importlib.util.cache_from_source(str(src)))
    pyc.parent.mkdir(parents=True, exist_ok=True)
    pyc.write_bytes(_code_to_timestamp_pyc(code, int(src.stat().st_mtime), src.stat().st_size))


def plant_unchecked_hash_pyc(root, forged=CALC2):
    src = root / "calc.py"
    code = compile(forged, str(src), "exec", dont_inherit=True)
    pyc = Path(importlib.util.cache_from_source(str(src)))
    pyc.parent.mkdir(parents=True, exist_ok=True)
    pyc.write_bytes(_code_to_hash_pyc(code, importlib.util.source_hash(src.read_bytes()), checked=False))


def plant_sourceless_pyc(root):
    (root / "helper.pyc").write_bytes(_code_to_timestamp_pyc(compile("V = 2\n", "helper.py", "exec"), 0, 0))


def plant_conftest(root, body="import calc\ncalc.f = lambda: 2\n"):
    (root / "tests" / ".gitignore").write_text("*\n")
    (root / "tests" / "conftest.py").write_text(body)


def plant_ini_that_collects_nothing(root):
    (root / "tests" / ".gitignore").write_text("*\n")
    (root / "tests" / "pytest.ini").write_text("[pytest]\naddopts = -k nomatch_zzz\n")


def plant_generated_source(root):
    (root / "lib").mkdir(exist_ok=True)
    (root / "lib" / ".gitignore").write_text("*\n")
    (root / "lib" / "helper.py").write_text("V = 2\n")


def plant_symlink_module(root, outside):
    (root / "lib").mkdir(exist_ok=True)
    (root / "lib" / ".gitignore").write_text("*\n")
    os.symlink(outside, root / "lib" / "helper.py")


def _gate(root, *, target=None, bound=True, candidate_paths=("calc.py",), validator=None):
    """One REAL test gate on ``root``: the validator binds the tree exactly as the gate orchestrator does (content + candidate paths)."""
    validator = validator or PolymorphicValidator(str(root), original_workspace_path=str(root))
    if bound:
        validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), list(candidate_paths))
    result = validator.run_tests(target) if target else validator.run_tests()
    return result["success"], validator


@pytest.fixture
def outside_helper(tmp_path):
    path = tmp_path / "outside" / "evil_helper.py"
    path.parent.mkdir()
    path.write_text("V = 2\n")
    return path


# (label, reviewed calc, planter(root, outside), target): the reviewed candidate is WRONG (f()==1, test asserts 2), so a True verdict is forged
FORGED_PASS = [
    ("timestamp .pyc keyed to the reviewed source", CALC1, lambda r, o: plant_timestamp_pyc(r), None),
    ("unchecked-hash .pyc carrying the reviewed source hash", CALC1, lambda r, o: plant_unchecked_hash_pyc(r), None),
    ("sourceless ignored helper.pyc", CALC_HELPER, lambda r, o: plant_sourceless_pyc(r), None),
    ("ignored tests/conftest.py", CALC1, lambda r, o: plant_conftest(r), None),
    ("ignored tests/pytest.ini collecting nothing (targeted gate)", CALC1, lambda r, o: plant_ini_that_collects_nothing(r), "tests/test_calc.py"),
    ("ignored generated source lib/helper.py", CALC_LIB, lambda r, o: plant_generated_source(r), None),
    ("ignored symlink lib/helper.py -> a file outside the tree", CALC_LIB, lambda r, o: plant_symlink_module(r, o), None),
]


def test_the_honest_wrong_candidate_fails_and_the_honest_right_one_passes(tmp_path):
    """Controls: the fixture's honest verdicts. Without them every other test here could pass vacuously (a fix that fails everything)."""
    assert _gate(_repo(tmp_path, calc=CALC1, name="wrong"))[0] is False
    assert _gate(_repo(tmp_path, calc=CALC2, name="right"))[0] is True


@pytest.mark.parametrize("label,calc,planter,target", FORGED_PASS, ids=[row[0] for row in FORGED_PASS])
def test_ignored_residue_cannot_forge_a_pass_and_nothing_in_git_or_the_binding_shows_it(tmp_path, outside_helper, label, calc, planter, target):
    root = _repo(tmp_path, calc=calc)
    planter(root, outside_helper)
    planted = sorted(p for p in root.rglob("*") if ".git" not in p.parts and (p.is_file() or p.is_symlink()))
    success, validator = _gate(root, target=target)
    assert success is False, f"{label}: a planted ignored file decided the verdict"
    # the contamination was invisible to the binding and to git: this is exactly why a bigger binding is not the fix
    assert validator.tree_binding.changes() == [] and validator.tree_binding.created() == []
    assert subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip() == ""
    # not destructive: the owner's tree keeps every planted file (no purge, no git clean of the worktree)
    assert sorted(p for p in root.rglob("*") if ".git" not in p.parts and (p.is_file() or p.is_symlink()) and "__pycache__" not in p.parts
                  and ".kriya" not in p.parts and ".pytest_cache" not in p.parts) == \
        [p for p in planted if "__pycache__" not in p.parts]


@pytest.mark.parametrize("planter", [lambda r: plant_timestamp_pyc(r, CALC1), lambda r: plant_conftest(r, "import calc\ncalc.f = lambda: 1\n")],
                         ids=["timestamp-pyc", "conftest"])
def test_the_mirror_a_correct_candidate_cannot_be_forced_to_fail_by_ignored_residue(tmp_path, planter):
    root = _repo(tmp_path, calc=CALC2)
    planter(root)
    assert _gate(root)[0] is True


def test_the_gate_still_passes_a_correct_candidate_with_untracked_work_and_an_ignored_candidate_path(tmp_path):
    """The view holds the BOUND set - tracked + untracked-not-ignored + the candidate paths - so an honest candidate with untracked WIP, or one whose
    candidate file lives under an ignored path, is not rejected (a view of the tracked files only would fail these)."""
    root = _repo(tmp_path, calc="from gen import value\nfrom wip import bonus\n\ndef f():\n    return value() + bonus()\n", test=TEST2,
                 extra={".gitignore": "gen/\n__pycache__/\n"})
    (root / "gen").mkdir()
    (root / "gen" / "__init__.py").write_text("def value():\n    return 2\n")  # ignored, but the candidate wrote it
    (root / "wip.py").write_text("def bonus():\n    return 0\n")  # untracked, NOT ignored: repository content the candidate depends on
    assert _gate(root, candidate_paths=("calc.py", "gen/__init__.py"))[0] is True


# --- prior-gate residue cannot reach the next gate ---------------------------------------------------------------------------------

PLANT_PYC_TEST = '''import importlib.util, os
from importlib._bootstrap_external import _code_to_timestamp_pyc
def test_side_effect_plants_ignored_pyc():
    root = os.getcwd(); src = os.path.join(root, "calc.py"); st = os.stat(src)
    code = compile("def f():\\n    return 2\\n", src, "exec", dont_inherit=True)
    pyc = importlib.util.cache_from_source(src); os.makedirs(os.path.dirname(pyc), exist_ok=True)
    open(pyc, "wb").write(_code_to_timestamp_pyc(code, int(st.st_mtime), st.st_size))
'''
PLANT_CONFTEST_TEST = '''import os
def test_side_effect_plants_ignored_conftest():
    d = os.path.join(os.getcwd(), "tests")
    open(os.path.join(d, ".gitignore"), "w").write("*\\n")
    open(os.path.join(d, "conftest.py"), "w").write("import calc\\ncalc.f = lambda: 2\\n")
'''


@pytest.mark.parametrize("source,a_outcome", [(PLANT_PYC_TEST, True), (PLANT_CONFTEST_TEST, "stopped")], ids=["pyc", "conftest"])
def test_residue_written_by_one_gate_cannot_affect_the_next_gate_of_the_same_validator(tmp_path, source, a_outcome):
    """The real scenario: gate A is a TRACKED, reviewed, PASSING test that (as candidate code does) writes ignored residue while it runs; gate B runs the
    reviewed tests. One validator, one binding bound once on the worktree, both gates through the real wrapper.

    A's `.pyc` is ignored output (accepted, as before). A's `tests/.gitignore` + `conftest.py` is the self-hiding trick that used to make the planted
    conftest invisible; the view check applies the REPOSITORY's ignore rules (not rules the gate wrote), so A is now a typed created-files stop. Either
    way B must not consume anything A left."""
    root = _repo(tmp_path, extra={"tests/test_plant.py": source})
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    try:
        first = _gate(root, target="tests/test_plant.py", validator=validator, bound=False)[0]
    except VerificationGateCreatedFiles:
        first = "stopped"
    second, _ = _gate(root, target="tests/test_calc.py", validator=validator, bound=False)
    assert first == a_outcome
    assert second is False    # B must not consume what A left behind
    assert not (root / "tests" / "conftest.py").exists()  # and A's residue never reached the worktree at all


@pytest.fixture
def state_inside_a_git_work_tree(tmp_path, monkeypatch):
    """The state root is itself inside a Git work tree, so a view directory looks like a work tree: a nested gate that asked for its own view WOULD get one."""
    from kriya.core.state_paths import ENV_STATE_DIR

    holder = tmp_path / "state-repo"
    holder.mkdir()
    _git(holder, "init", "-q")
    monkeypatch.setenv(ENV_STATE_DIR, str(holder / "state"))
    return holder


def test_a_gate_nested_in_a_gate_shares_the_view_even_when_the_view_would_look_like_a_work_tree(tmp_path, state_inside_a_git_work_tree):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    from kriya.tools.validate import _verification_gate

    roots = []

    def inner(self):
        roots.append(self.workspace_path)
        return {"success": True}

    def outer(self):
        roots.append(self.workspace_path)
        _verification_gate("compile")(inner)(self)
        with self.verification_session("tests"):
            roots.append(self.workspace_path)
        return {"success": True}

    _verification_gate("tests")(outer)(validator)
    assert len(set(roots)) == 1 and roots[0] != str(root) and validator.workspace_path == str(root)
    assert gate_view._ACTIVE_ROOTS == set()


def test_every_outermost_gate_gets_its_own_fresh_view_and_a_nested_gate_shares_it(tmp_path):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    seen = []
    nested_seen = []

    class Probe(PolymorphicValidator):
        pass

    from kriya.tools.validate import _verification_gate

    def inner(self):
        nested_seen.append(self.workspace_path)
        return {"success": True}

    def outer(self):
        seen.append(self.workspace_path)
        Path(self.workspace_path, "scratch.txt").write_text("written by gate one")
        _verification_gate("compile")(inner)(self)
        return {"success": True}

    def second(self):
        seen.append(self.workspace_path)
        seen.append(Path(self.workspace_path, "scratch.txt").exists())
        return {"success": True}

    _verification_gate("tests")(outer)(validator)
    _verification_gate("tests")(second)(validator)
    first_view, second_view = seen[0], seen[1]
    assert first_view != str(root) and second_view != str(root)
    # the directory NAME is stable per tree (failure text names it; the retry loop compares failures between attempts) but the view is rebuilt: below,
    # nothing the first gate wrote is there for the second
    assert nested_seen == [first_view]                     # a gate nested in a gate runs in that gate's view
    assert seen[2] is False                                # what the first gate wrote died with its view
    assert validator.workspace_path == str(root)           # the execution root is restored
    assert not os.path.exists(first_view) and not os.path.exists(second_view)
    assert not (root / "scratch.txt").exists()


def test_the_execution_root_is_restored_and_the_view_removed_when_the_gate_raises(tmp_path):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    from kriya.tools.validate import _verification_gate

    seen = []

    def boom(self):
        seen.append(self.workspace_path)
        raise RuntimeError("the gate blew up")

    with pytest.raises(RuntimeError, match="blew up"):
        _verification_gate("tests")(boom)(validator)
    assert validator.workspace_path == str(root) and not os.path.exists(seen[0])


# --- baseline / PRE / candidate symmetry -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("label,calc,planter,target", FORGED_PASS, ids=[row[0] for row in FORGED_PASS])
def test_a_validator_with_no_binding_baseline_and_pre_gates_gets_the_same_view_policy(tmp_path, outside_helper, label, calc, planter, target):
    root = _repo(tmp_path, calc=calc)
    planter(root, outside_helper)
    success, _ = _gate(root, target=target, bound=False)  # baseline / PRE gates are constructed with no tree binding
    assert success is False, label


def test_the_same_candidate_gets_the_same_verdict_bound_unbound_and_with_a_separate_original_workspace(tmp_path):
    root = _repo(tmp_path, calc=CALC2)
    verdicts = {_gate(root)[0], _gate(root, bound=False)[0],
                _gate(root, validator=PolymorphicValidator(str(root), original_workspace_path=str(tmp_path / "elsewhere")))[0]}
    assert verdicts == {True}


# --- a view that cannot be built or proven refuses the gate; it never falls back to the worktree -------------------------------------

def test_a_view_that_cannot_be_built_refuses_the_gate_and_never_runs_in_place(tmp_path, monkeypatch):
    root = _repo(tmp_path, calc=CALC2)
    ran = []
    monkeypatch.setattr("kriya.tools.validate.ProcessController.run", lambda *a, **k: ran.append(1))

    def refuse(*_a, **_k):
        raise GateViewError(GATE_VIEW_UNAVAILABLE, "no space left on device")

    monkeypatch.setattr("kriya.tools.validate.build_gate_view", refuse)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    with pytest.raises(GateViewError) as refused:
        validator.run_tests()
    assert refused.value.reason_code == GATE_VIEW_UNAVAILABLE and ran == []
    assert validator.workspace_path == str(root)


def test_an_unwritable_state_directory_is_a_typed_refusal(tmp_path, monkeypatch):
    root = _repo(tmp_path, calc=CALC2)
    blocker = tmp_path / "state-is-a-file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(gate_view, "views_directory", lambda _state_root: str(blocker / "gate-views"))
    with pytest.raises(GateViewError) as refused:
        _build(root)
    assert refused.value.reason_code == GATE_VIEW_UNAVAILABLE


def test_a_bound_file_that_changed_before_it_was_copied_is_refused(tmp_path):
    """TOCTOU between the binding check and the copy: the view must hold the BOUND bytes or the gate is refused."""
    root = _repo(tmp_path)
    binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    (root / "calc.py").write_text(CALC2)  # swapped after the binding, before the view is built
    with pytest.raises(GateViewError) as refused:
        _build(root, manifest=binding.view_manifest(), extra_paths=binding.view_untracked())
    assert refused.value.reason_code == GATE_VIEW_IDENTITY_MISMATCH and "calc.py" in str(refused.value)
    assert os.listdir(_views()) == []  # the half-built view was removed


def test_a_command_for_the_view_that_names_the_worktree_is_refused_before_it_runs(tmp_path, monkeypatch):
    """Path confusion: a view is built but the command's cwd/argv still names the worktree - it would execute the worktree's bytes while the verdict is
    attributed to the view."""
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    ran = []
    monkeypatch.setattr("kriya.tools.validate.ProcessController.run", lambda *a, **k: ran.append(a) or None)
    from kriya.tools.validate import _verification_gate

    def body(self):
        self._run_cmd_with_timeout([sys.executable, str(root / "calc.py")], cwd=self.workspace_path)
        return {"success": True}

    with pytest.raises(GateViewError) as refused:
        _verification_gate("tests")(body)(validator)
    assert refused.value.reason_code == GATE_VIEW_PATH_CONFUSION and ran == []

    def body_cwd(self):
        self._run_cmd_with_timeout([sys.executable, "-c", "pass"], cwd=str(root))
        return {"success": True}

    with pytest.raises(GateViewError) as refused:
        _verification_gate("tests")(body_cwd)(validator)
    assert refused.value.reason_code == GATE_VIEW_PATH_CONFUSION and ran == []


def test_the_project_venv_is_the_one_path_into_the_origin_a_view_command_may_name(tmp_path):
    root = _repo(tmp_path)
    view = _build(root, environment_roots=(str(root / ".kriya" / "venv"),))
    try:
        gate_view.guard_against_origin(view, [str(root / ".kriya" / "venv" / "bin" / "python"), "-m", "pytest"], view.root)
        with pytest.raises(GateViewError):
            gate_view.guard_against_origin(view, [str(root / ".kriya" / "other" / "python")], view.root)
    finally:
        view.close()


def test_a_symlink_alias_of_the_origin_is_the_origin_and_is_refused(tmp_path):
    root = _repo(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(root)
    view = _build(root, environment_roots=(str(root / ".kriya" / "venv"),))
    try:
        with pytest.raises(GateViewError):
            gate_view.guard_against_origin(view, [str(alias / "calc.py")], view.root)
    finally:
        view.close()


def test_a_path_whose_relation_to_the_origin_cannot_be_decided_is_refused_not_allowed(tmp_path, monkeypatch):
    """Fail closed: an undecidable relation is not evidence that the path stays out of the shared worktree, and it is no evidence of an environment root either."""
    root = _repo(tmp_path)
    view = _build(root, environment_roots=(str(root / ".kriya" / "venv"),))
    try:
        monkeypatch.setattr(gate_view, "path_relation", lambda _root, _target: gate_view.PathRelation.UNKNOWN)
        assert view.refers_to_origin(str(root / "calc.py")) is True
        assert view.refers_to_origin(str(root / ".kriya" / "venv" / "bin" / "python")) is True
    finally:
        view.close()


def test_the_closed_reason_code_table_is_exactly_these_three():
    assert GATE_VIEW_REASON_CODES == {"GATE_VIEW_UNAVAILABLE", "GATE_VIEW_IDENTITY_MISMATCH", "GATE_VIEW_PATH_CONFUSION"}
    assert issubclass(GateViewError, gate_view.ContainmentSetupError)  # the run's existing "refused, not run uncontained" handling applies


def test_a_directory_that_is_not_a_git_work_tree_has_no_bound_set_and_runs_in_place(tmp_path):
    """The one documented exemption: no repository, no content to be a view of (and no binding can exist). It is decided before anything runs."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "calc.py").write_text(CALC2)
    validator = PolymorphicValidator(str(plain))
    from kriya.tools.validate import _verification_gate

    seen = []
    _verification_gate("tests")(lambda self: seen.append(self.workspace_path) or {"success": True})(validator)
    assert seen == [str(plain)]


def test_contained_execution_is_viewed_like_any_other_gate(tmp_path):
    """D2 S3 (owner decision 2026-10-24; this test used to pin the OPPOSITE, owner O4's "the OCI backend was not run for this change"): a
    contained gate runs in the exact-bound view, so the workspace the OCI backend mounts is the Kriya-owned view and never the candidate worktree
    (whose ignored residue the verdict would otherwise describe). The real-OCI controls are tests/test_oi142_gate_view_oci.py."""
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root), autonomy_cfg=AutonomyConfig(contained_execution_required=True))
    from kriya.tools.validate import _verification_gate

    seen = []
    _verification_gate("tests")(lambda self: seen.append(self.workspace_path) or {"success": True})(validator)
    assert len(seen) == 1 and seen[0] != str(root) and os.path.basename(seen[0]).startswith("view-")
    assert validator.workspace_path == str(root)  # restored after the gate


# --- what the view holds -----------------------------------------------------------------------------------------------------------

def test_the_view_holds_exactly_the_bound_set_byte_for_byte_and_nothing_else(tmp_path):
    root = _repo(tmp_path, extra={"scripts/run.sh": "#!/bin/sh\necho hi\n"})
    (root / "scripts" / "run.sh").chmod(0o755)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "exec bit")
    os.symlink("calc.py", root / "alias.py")
    _git(root, "add", "alias.py")
    _git(root, "commit", "-qm", "link")
    (root / "untracked.txt").write_text("not ignored\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "calc.cpython-314.pyc").write_bytes(b"residue")
    (root / ".kriya").mkdir()
    (root / ".kriya" / "state.json").write_text("{}")
    view = _build(root)
    try:
        listed = sorted(os.path.relpath(os.path.join(d, n), view.root) for d, _dirs, names in os.walk(view.root) for n in names)
        assert listed == sorted([".gitignore", "alias.py", "calc.py", "scripts/run.sh", "tests/test_calc.py", "untracked.txt"])
        assert os.path.islink(os.path.join(view.root, "alias.py")) and os.readlink(os.path.join(view.root, "alias.py")) == "calc.py"
        assert os.stat(os.path.join(view.root, "scripts", "run.sh")).st_mode & stat.S_IXUSR
        for relpath in ("calc.py", "tests/test_calc.py"):
            assert Path(view.root, relpath).read_bytes() == (root / relpath).read_bytes()
            assert os.stat(os.path.join(view.root, relpath)).st_ino != os.stat(root / relpath).st_ino  # never a hard link: a gate cannot rewrite the worktree's bytes
    finally:
        view.close()
    assert not os.path.exists(view.root)


def test_a_candidate_path_that_git_ignores_is_in_the_view_through_the_binding(tmp_path):
    root = _repo(tmp_path, extra={".gitignore": "gen/\n"})
    (root / "gen").mkdir()
    (root / "gen" / "made.py").write_text("X = 1\n")
    binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["gen/made.py"])
    view = _build(root, manifest=binding.view_manifest(), extra_paths=binding.view_untracked())
    try:
        assert Path(view.root, "gen", "made.py").read_text() == "X = 1\n"
    finally:
        view.close()


def test_a_tracked_symlink_is_kept_as_a_link_and_never_followed_into_outside_bytes(tmp_path, outside_helper):
    root = _repo(tmp_path)
    os.symlink(str(outside_helper), root / "outside_link.py")
    _git(root, "add", "outside_link.py")
    _git(root, "commit", "-qm", "link to outside")
    view = _build(root)
    try:
        assert os.path.islink(os.path.join(view.root, "outside_link.py"))
        assert os.readlink(os.path.join(view.root, "outside_link.py")) == str(outside_helper)
    finally:
        view.close()


def test_the_view_lives_under_the_state_root_never_inside_the_worktree(tmp_path):
    root = _repo(tmp_path)
    view = _build(root)
    try:
        assert os.path.dirname(view.root) == _views()
        assert not view.root.startswith(str(root.resolve()) + os.sep)
    finally:
        view.close()


# --- the 001/001B contract still holds for what runs in the view --------------------------------------------------------------------

def _view_gate(root, body, *, gate="tests", bound=True):
    from kriya.tools.validate import _verification_gate

    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    if bound:
        validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    return _verification_gate(gate)(body)(validator), validator


def test_a_gate_that_rewrites_a_bound_file_in_the_view_is_the_tree_mutated_stop(tmp_path):
    root = _repo(tmp_path)

    def body(self):
        Path(self.workspace_path, "calc.py").write_text(CALC2)  # a formatter bound to the build, a fixture, the app under test
        return {"success": True}

    with pytest.raises(VerificationTreeMutated) as stopped:
        _view_gate(root, body)
    assert "calc.py" in stopped.value.failure.diagnostics["changed_paths"]
    assert (root / "calc.py").read_text() == CALC1  # and the worktree never saw it


def test_a_gate_that_creates_repository_content_in_the_view_is_the_created_files_stop(tmp_path):
    root = _repo(tmp_path)

    def body(self):
        Path(self.workspace_path, "stray.yml").write_text("x: 1\n")
        return {"success": True}

    with pytest.raises(VerificationGateCreatedFiles) as stopped:
        _view_gate(root, body)
    assert [entry["path"] for entry in stopped.value.failure.diagnostics["created"]] == ["stray.yml"]


def test_ignored_output_in_the_view_is_not_a_violation_and_never_reaches_the_worktree(tmp_path):
    root = _repo(tmp_path)

    def body(self):
        (Path(self.workspace_path) / "__pycache__").mkdir()
        Path(self.workspace_path, "__pycache__", "x.pyc").write_bytes(b"ignored build output")
        return {"success": True}

    result, _ = _view_gate(root, body)
    assert result["success"] is True and not (root / "__pycache__").exists()


def test_the_runtime_gate_returns_the_state_the_application_created_and_it_dies_with_the_view(tmp_path):
    root = _repo(tmp_path)

    def body(self):
        Path(self.workspace_path, "app-state.db").write_text("runtime state")
        return {"success": True}

    result, _ = _view_gate(root, body, gate=RUNTIME_VERIFICATION_GATE)
    assert result["runtime_artifacts"] == ["app-state.db"] and not (root / "app-state.db").exists()


def test_a_write_to_the_worktree_by_absolute_path_is_still_caught_by_the_worktree_backstop(tmp_path):
    root = _repo(tmp_path)
    absolute = str(root / "calc.py")

    def body(self):
        Path(absolute).write_text(CALC2)  # reaches the SHARED tree around the view
        return {"success": True}

    with pytest.raises(VerificationTreeMutated):
        _view_gate(root, body)


def test_a_gate_result_without_a_binding_carries_no_runtime_artifacts_key(tmp_path):
    """Result contract (rule 2): the key exists only where a check produced it, exactly as before the views."""
    root = _repo(tmp_path)
    result, _ = _view_gate(root, lambda self: {"success": True}, gate=RUNTIME_VERIFICATION_GATE, bound=False)
    assert "runtime_artifacts" not in result


# --- declared artifacts cross the view boundary only through an explicit, narrow door ------------------------------------------------

def test_the_acceptance_oracle_works_through_the_view_and_ignored_pycs_cannot_forge_it(tmp_path):
    from kriya.workflow import acceptance_oracle as ao
    from kriya.workflow.requirements import derive_requirements

    goal = "Add a double(x) function to calc/__init__.py so that double(5) returns 10.\n"
    acceptance = ("import pytest\n\nfrom calc import double\n\n\n@pytest.mark.kriya_requirement(\"REQ-1\")\n"
                  "def test_double_doubles():\n    assert double(5) == 10\n")
    wrong, right = "def double(x):\n    return x + 2\n", "def double(x):\n    return 2 * x\n"

    def run(init, plant=False):
        root = tmp_path / ("c%d" % len(list(tmp_path.iterdir())))
        (root / "calc").mkdir(parents=True)
        (root / "calc" / "__init__.py").write_text(init)
        (root / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "reviewed")
        if plant:
            src = root / "calc" / "__init__.py"
            code = compile(right, str(src), "exec", dont_inherit=True)
            pyc = Path(importlib.util.cache_from_source(str(src)))
            pyc.parent.mkdir(parents=True, exist_ok=True)
            pyc.write_bytes(_code_to_timestamp_pyc(code, int(src.stat().st_mtime), src.stat().st_size))
        path = tmp_path / ("acceptance-%s.py" % root.name)
        path.write_text(acceptance)
        artifact = ao.load_acceptance(str(path), derive_requirements(goal), str(tmp_path / ("state-" + root.name)))
        validator = PolymorphicValidator(str(root), original_workspace_path=str(root), autonomy_cfg=AutonomyConfig())
        validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc/__init__.py"])
        outcome = ao.run_acceptance(artifact, str(root), candidate_paths=["calc/__init__.py"], validator_factory=lambda: validator)
        return ao.judge_acceptance(artifact, outcome)["REQ-1"].code, outcome, root

    honest_wrong, _, _ = run(wrong)
    forged, _, _ = run(wrong, plant=True)
    honest_right, outcome, root = run(right)
    assert honest_wrong == forged == "ACCEPTANCE_VIOLATED"       # the planted pyc cannot turn a violated requirement into a passed one
    assert honest_right == "ACCEPTANCE_PASSED"                   # and a correct candidate is still PASSED through the view (observations came back)
    assert outcome.observations is not None and outcome.integrity_problem is None
    assert not list((root / ".kriya").glob("acceptance-runs/*"))  # the staging was Kriya's, in the worktree, and is cleaned as before


def test_the_runner_contract_digest_is_unchanged_by_this_fix():
    """Owner O5: RUNNER_CONTRACT_DIGEST (runner source + ini) must not move; widening it would be a STOP."""
    from kriya.workflow.acceptance_oracle import RUNNER_CONTRACT_DIGEST

    assert RUNNER_CONTRACT_DIGEST == "806f453647de48b218b4a1ec7e46a9e2a99817203986a0959d94f8d09b7db259"


def test_a_declared_directory_is_staged_in_and_only_new_regular_files_come_back(tmp_path):
    root = _repo(tmp_path)
    (root / ".kriya" / "acceptance-runs" / "abc").mkdir(parents=True)
    (root / ".kriya" / "acceptance-runs" / "abc" / "runner.py").write_text("staged input")
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    from kriya.tools.validate import _verification_gate

    outside = tmp_path / "outside.txt"
    outside.write_text("do not copy")

    def body(self):
        staged = self._stage_declared_directory(".kriya/acceptance-runs/abc")
        here = Path(self.workspace_path, ".kriya", "acceptance-runs", "abc")
        assert (here / "runner.py").read_text() == "staged input"
        (here / "runner.py").write_text("tampered")        # a staged input is never replaced from the view
        (here / "observations.json").write_text("{}")
        os.symlink(str(outside), here / "link.json")       # a link is never a declared output
        Path(self.workspace_path, "elsewhere.json").write_text("{}")  # outside the declared directory: not a declared output
        self._collect_declared_outputs(".kriya/acceptance-runs/abc", staged)
        return {"success": True}

    _verification_gate("acceptance")(body)(validator)
    staged_dir = root / ".kriya" / "acceptance-runs" / "abc"
    assert sorted(p.name for p in staged_dir.iterdir()) == ["observations.json", "runner.py"]
    assert (staged_dir / "runner.py").read_text() == "staged input"
    assert not (root / "elsewhere.json").exists()


def test_only_a_kriya_staging_directory_may_be_imported_into_a_view(tmp_path):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    from kriya.tools.validate import _verification_gate

    def body(self):
        for bad in ("calc", "../x", "/etc", ".kriya/../calc"):
            with pytest.raises(GateViewError):
                self._stage_declared_directory(bad)
        return {"success": True}

    assert _verification_gate("acceptance")(body)(validator)["success"] is True


# --- execution-environment inputs: the venv (owner O4) -------------------------------------------------------------------------------

def _fake_venv(tmp_path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "lib" / "site-packages" / "pkg").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    (venv / "lib" / "site-packages" / "pkg" / "__init__.py").write_text("X = 1\n")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return venv


def test_a_sealed_venv_verifies_clean_and_any_change_to_it_is_an_unexpected_mutation(tmp_path):
    venv = _fake_venv(tmp_path)
    assert venv_integrity.mismatches(str(venv)) == ["<unsealed>"]  # never sealed: no content to trust
    venv_integrity.seal(str(venv))
    assert venv_integrity.mismatches(str(venv)) == []
    module = venv / "lib" / "site-packages" / "pkg" / "__init__.py"
    module.write_text("X = 2\n")
    assert venv_integrity.mismatches(str(venv)) == ["lib/site-packages/pkg/__init__.py"]
    module.write_text("X = 1\n")
    assert venv_integrity.mismatches(str(venv)) == []
    (venv / "lib" / "site-packages" / "evil.pth").write_text("import os\n")   # runs at every interpreter start
    assert venv_integrity.mismatches(str(venv)) == ["lib/site-packages/evil.pth"]
    (venv / "lib" / "site-packages" / "evil.pth").unlink()
    (venv / "pyvenv.cfg").unlink()
    assert venv_integrity.mismatches(str(venv)) == ["pyvenv.cfg"]


def test_bytecode_in_the_venv_is_deleted_before_every_reuse_so_a_planted_pyc_never_loads(tmp_path):
    venv = _fake_venv(tmp_path)
    venv_integrity.seal(str(venv))
    planted = venv / "lib" / "site-packages" / "pkg" / "__pycache__" / "__init__.cpython-314.pyc"
    planted.parent.mkdir()
    planted.write_bytes(b"forged bytecode")
    assert venv_integrity.mismatches(str(venv)) == []   # bytecode is a disposable cache, not sealed content
    assert not planted.parent.exists()                  # ...and it is gone before the venv is used


def test_a_changed_venv_is_recreated_not_reused(tmp_path, monkeypatch):
    """Through the validator: `_ensure_project_venv` re-verifies a memoized venv before handing it out and rebuilds a changed one."""
    root = _repo(tmp_path, extra={"requirements.txt": "left-pad-nonexistent==1\n"})
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    venv_dir = root / ".kriya" / "venv"
    created = []

    def fake_run(cmd, cwd, timeout=300, **_kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            venv = Path(cmd[3])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").write_text("#!/bin/sh\n")
            created.append(1)
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}

    monkeypatch.setattr(validator, "_run_cmd_with_timeout", fake_run)
    python, error = validator._ensure_project_venv(["-r", str(root / "requirements.txt")])
    assert error is None and python == str(venv_dir / "bin" / "python") and created == [1]
    assert venv_integrity.mismatches(str(venv_dir)) == []          # sealed after the install
    again, _ = validator._ensure_project_venv(["-r", str(root / "requirements.txt")])
    assert again == python and created == [1] and validator.venv_install_attempts == 1   # reused as sealed: no second install
    (venv_dir / "bin" / "sitecustomize.py").write_text("print('planted by a gate')\n")
    rebuilt, _ = validator._ensure_project_venv(["-r", str(root / "requirements.txt")])
    assert created == [1, 1] and validator.venv_recreations == 1 and not (venv_dir / "bin" / "sitecustomize.py").exists()
    assert rebuilt == python


def test_the_venv_a_view_gate_uses_is_the_origin_trees_not_the_views(tmp_path, monkeypatch):
    """Execution-environment input: the interpreter for a gate that runs in a view lives under the ORIGIN's `.kriya/venv`, so one install serves
    every gate and the view does not carry (or lose) it."""
    root = _repo(tmp_path, extra={"requirements.txt": "left-pad-nonexistent==1\n"})
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    created = []

    def fake_run(cmd, cwd, timeout=300, **_kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            (Path(cmd[3]) / "bin").mkdir(parents=True)
            (Path(cmd[3]) / "bin" / "python").write_text("#!/bin/sh\n")
            created.append(cmd[3])
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}

    monkeypatch.setattr(validator, "_run_cmd_with_timeout", fake_run)
    from kriya.tools.validate import _verification_gate

    pythons = []
    for _ in range(2):  # two outermost gates of one validator
        _verification_gate("tests")(lambda self: pythons.append(self._resolve_python_interpreter()[0]) or {"success": True})(validator)
    assert pythons == [str(root / ".kriya" / "venv" / "bin" / "python")] * 2 and created == [str(root / ".kriya" / "venv")]
    assert validator.venv_install_attempts == 1


def test_the_dependency_cache_key_is_the_origin_trees_in_and_out_of_a_view(tmp_path):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root))
    from kriya.tools.validate import _verification_gate

    outside = validator._dependency_cache_dir("maven")
    inside = []
    _verification_gate("tests")(lambda self: inside.append(self._dependency_cache_dir("maven")) or {"success": True})(validator)
    assert inside == [outside]  # a view-keyed cache would be a cold cache (network acquisition) on every gate


# --- the same checks hold for the managed Python runtime step (a runtime gate in a session) -----------------------------------------------

def test_a_verification_session_shares_one_view_between_a_build_step_and_the_run_that_consumes_it(tmp_path):
    """O1 (finite_command): Kriya's own build preparation and the reviewed run are one verification - the product of the build exists only in that view
    and is consumed by the run, never left behind as residue in the worktree for any later gate."""
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    with validator.verification_session(RUNTIME_VERIFICATION_GATE):
        Path(validator.workspace_path, "app.txt").write_text("built in the session")
        result = validator.run_app_sequence([[sys.executable, "-c", "print(open('app.txt').read())"]], timeout=30)
    assert "built in the session" in result["output"]
    assert not (root / "app.txt").exists() and validator.workspace_path == str(root)
    after = validator.run_app_sequence([[sys.executable, "-c", "import os; print(os.path.exists('app.txt'))"]], timeout=30)
    assert "False" in after["output"]  # a later gate does not see it


def test_a_session_nested_in_a_gate_or_a_gate_nested_in_a_session_does_not_open_a_second_view(tmp_path):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    with validator.verification_session("tests"):
        outer = validator.workspace_path
        with validator.verification_session("tests"):
            assert validator.workspace_path == outer
        assert validator.workspace_path == outer
    assert validator.workspace_path == str(root)


def test_session_closes_the_view_and_restores_the_root_when_the_body_raises(tmp_path):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    seen = []
    with pytest.raises(RuntimeError):
        with validator.verification_session("tests"):
            seen.append(validator.workspace_path)
            raise RuntimeError("prepare failed")
    assert validator.workspace_path == str(root) and not os.path.exists(seen[0])


def test_view_contents_are_hash_identical_to_the_bound_identities(tmp_path):
    root = _repo(tmp_path)
    binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    view = _build(root, manifest=binding.view_manifest(), extra_paths=binding.view_untracked())
    try:
        for relpath in ("calc.py", "tests/test_calc.py", ".gitignore"):
            mode = f"{stat.S_IMODE(os.lstat(os.path.join(view.root, relpath)).st_mode):o}"
            digest = hashlib.sha256(Path(view.root, relpath).read_bytes()).hexdigest()
            assert binding.view_manifest()[relpath] == f"{mode}:{digest}"
    finally:
        view.close()


# --- O1: a Gradle runtime command that consumes build/classes rebuilds it from the bound source (D3 generalised) -------------------------

def _gradle_repo(tmp_path):
    return _repo(tmp_path, extra={"build.gradle": "plugins { id 'java' }\n", "src/main/java/App.java": "public class App { }\n"})


def test_a_jvm_launched_on_gradle_build_classes_is_prepared_from_current_source_and_a_gradle_invocation_is_not(tmp_path, monkeypatch):
    root = _gradle_repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    java_on_classes = ["java", "-cp", "build/classes/java/main", "App"]
    assert validator._runtime_needs_gradle_classes(java_on_classes) is True
    assert validator._runtime_needs_gradle_classes(["java", "-cp", "target/classes", "App"]) is False   # not Gradle output
    assert validator._runtime_needs_gradle_classes(["gradle", "run"]) is False                          # builds what it needs itself
    assert validator._runtime_needs_gradle_classes(["python3", "main.py"]) is False
    calls = []
    monkeypatch.setattr(validator, "run_compile_check", lambda files, deadline=None: calls.append(list(files)) or {"success": True, "output": ""})
    not_ready, prerequisite = validator._prepare_runtime([java_on_classes])
    assert not_ready is None and prerequisite == "gradle compileJava (current source): PASSED" and calls == [["src/main/java/App.java"]]
    assert validator._prepare_runtime([["gradle", "run"]]) == (None, None) and len(calls) == 1   # no preparation for a self-building command


def test_a_failed_gradle_prerequisite_build_runs_nothing_and_says_so(tmp_path, monkeypatch):
    root = _gradle_repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    monkeypatch.setattr(validator, "run_compile_check", lambda files, deadline=None: {"success": False, "output": "compileJava FAILED"})
    not_ready, prerequisite = validator._prepare_runtime([["java", "-cp", "build/classes/java/main", "App"]])
    assert not_ready["success"] is False and not_ready["prerequisite_failed"] is True and "gradle compileJava" in not_ready["output"]
    assert "compileJava FAILED" in not_ready["output"] and prerequisite == "gradle compileJava (current source): FAILED"


def test_a_bound_file_changed_in_the_worktree_between_gates_stops_the_next_gate_before_it_runs(tmp_path):
    """The check before the gate (is the worktree still the bound tree?) precedes the view: the view must never be built from a tree that is not the bound one."""
    root = _repo(tmp_path)
    ran = []
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    (root / "calc.py").write_text(CALC2)  # between verification steps
    from kriya.tools.validate import _verification_gate

    with pytest.raises(VerificationTreeMutated) as stopped:
        _verification_gate("tests")(lambda self: ran.append(1) or {"success": True})(validator)
    assert stopped.value.failure.diagnostics["phase"] == "before" and ran == []
    assert validator.workspace_path == str(root)


# =====================================================================================================================================
# Owner review 2026-10-23: proofs required before the commit
# =====================================================================================================================================

# --- architecture: every way into command execution inside the validator starts at a gate ---------------------------------------------

def _validator_model():
    import ast
    import inspect

    from kriya.tools import validate as validate_module

    source = inspect.getsource(validate_module)
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PolymorphicValidator")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}

    def gate_name(node):
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", None) == "_verification_gate":
                return ast.get_source_segment(source, decorator.args[0])
        return None

    calls = {}
    seeds = set()
    for name, node in methods.items():
        called = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) and inner.value.id == "self" and inner.attr in methods:
                called.add(inner.attr)
            if isinstance(inner, ast.Call):
                func = inner.func
                spawns = isinstance(func, ast.Attribute) and func.attr == "_run_cmd_with_timeout" or \
                    isinstance(func, ast.Name) and func.id == "ProcessController" or \
                    isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call) and getattr(func.value.func, "id", None) == "ProcessController"
                hands_over_to_adapter = isinstance(func, ast.Attribute) and func.attr in ("compile", "run_tests") and inner.args \
                    and isinstance(inner.args[0], ast.Name) and inner.args[0].id == "self"
                if spawns or hands_over_to_adapter:
                    seeds.add(name)
        calls[name] = called
    callers = {name: {other for other, called in calls.items() if name in called} for name in methods}
    return methods, calls, callers, seeds, gate_name


def test_the_validator_has_exactly_these_verification_gate_entries():
    methods, _calls, _callers, _seeds, gate_name = _validator_model()
    assert {name: gate_name(node) for name, node in methods.items() if gate_name(node)} == {
        "run_pom_validate": '"pom_validate"', "inspect_external_class": '"classpath_inspection"', "run_compile_check": '"compile"',
        "run_acceptance": "ACCEPTANCE_GATE", "run_tests": '"tests"', "run_app": "RUNTIME_VERIFICATION_GATE",
        "run_app_sequence": "RUNTIME_VERIFICATION_GATE",
        "contained_maven_build": "RUNTIME_VERIFICATION_GATE"}  # D2 S4: the contained `mvn package` a finite command's artifact needs, inside the preparation's session


def test_every_validator_method_that_can_start_a_process_is_reachable_only_through_a_gate():
    """No alternate shared-worktree execution path inside the seam: a method that spawns (or hands the validator to a build adapter, which spawns) is either a
    gate or is called ONLY by gated methods; the one exception is `_run_gradle_cmd`, which the Gradle adapter calls from its own compile/test methods (checked
    below), and those are only ever invoked from gated validator methods."""
    methods, _calls, callers, seeds, gate_name = _validator_model()

    def gated(name, seen=()):
        if gate_name(methods[name]):
            return True
        parents = callers[name] - set(seen) - {name}
        return bool(parents) and all(gated(parent, (*seen, name)) for parent in parents)

    # `_run_cmd_with_timeout` is the spawn primitive itself: what matters is that each of its callers is a gate or called only by gates
    ungated = sorted(name for name in seeds - {"_run_cmd_with_timeout"} if not gated(name))
    assert ungated == ["_run_gradle_cmd"]
    import inspect

    from kriya.capabilities import gradle

    source = inspect.getsource(gradle)
    import ast

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and "_run_gradle_cmd(" in (ast.get_source_segment(source, node) or ""):
            assert node.name in ("compile", "run_tests"), node.name  # only the adapter methods the gated validator methods call


def test_no_module_outside_the_validator_runs_a_gate_command_against_the_worktree_except_the_named_residuals():
    """The residual execution sites outside this seam are exactly these (each a named closure item); a new one fails here until classified."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "kriya"
    pattern = re.compile(r"\._run_cmd_with_timeout\(|\._run_maven_cmd\(|\._run_gradle_cmd\(")
    outside = sorted(str(path.relative_to(root.parent)) for path in root.rglob("*.py")
                     if "kriya/tools/validate.py" not in str(path) and "kriya/capabilities/" not in str(path) and pattern.search(path.read_text()))
    assert outside == ["kriya/workflow/authority_bundle.py"]  # runs in a Kriya-made export (X5 residual), never the worktree


def _recorded_cwds(monkeypatch):
    cwds = []

    class Done:
        def to_dict(self):
            return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}

    def record(self, cmd, cwd=None, **_kwargs):
        cwds.append(cwd)
        return Done()

    monkeypatch.setattr("kriya.tools.validate.ProcessController.run", record)
    return cwds


def test_each_python_gate_entry_executes_in_a_view_and_never_in_the_worktree(tmp_path, monkeypatch):
    root = _repo(tmp_path, calc=CALC2)
    cwds = _recorded_cwds(monkeypatch)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    validator.tree_binding = VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["calc.py"])
    entries = {
        "compile": lambda: validator.run_compile_check(["calc.py"]),
        "tests": lambda: validator.run_tests(),
        "run_app": lambda: validator.run_app([sys.executable, "-c", "pass"]),
        "run_app_sequence": lambda: validator.run_app_sequence([[sys.executable, "-c", "pass"]]),
    }
    for name, enter in entries.items():
        before = len(cwds)
        try:
            enter()
        except Exception:  # pylint: disable=broad-except
            pass  # the recorded fake answers every command; only where it ran matters
        assert len(cwds) > before or name == "compile", f"{name} spawned nothing"  # the Python compile check compiles in-process
        for cwd in cwds[before:]:
            assert os.path.realpath(cwd).startswith(os.path.realpath(_views()) + os.sep), (name, cwd)
            assert not os.path.realpath(cwd).startswith(str(root.resolve())), (name, cwd)


def test_the_java_gate_entries_execute_in_a_view_and_never_in_the_worktree(tmp_path, monkeypatch):
    root = _repo(tmp_path, extra={"pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n",
                                  "src/main/java/App.java": "public class App { }\n"})
    cwds = _recorded_cwds(monkeypatch)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    for enter in (lambda: validator.run_pom_validate(), lambda: validator.run_compile_check(["src/main/java/App.java"]),
                  lambda: validator.run_tests(), lambda: validator.inspect_external_class("java.lang.String")):
        try:
            enter()
        except Exception:  # pylint: disable=broad-except
            pass
    assert cwds, "nothing was spawned"
    for cwd in cwds:
        assert os.path.realpath(cwd).startswith(os.path.realpath(_views()) + os.sep), cwd


# --- cleanup: every way out of a gate removes the view, and a failed removal never rewrites the gate's truth -------------------------------

@pytest.mark.parametrize("outcome", ["passes", "fails", "times_out", "raises", "interrupted"])
def test_the_view_is_removed_and_the_root_restored_on_every_way_out_of_a_gate(tmp_path, outcome):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    from kriya.tools.validate import _verification_gate

    seen = []

    def body(self):
        seen.append(self.workspace_path)
        if outcome == "raises":
            raise RuntimeError("boom")
        if outcome == "interrupted":
            raise KeyboardInterrupt
        return {"success": outcome == "passes", "timed_out": outcome == "times_out"}

    gate = _verification_gate("tests")(body)
    if outcome in ("raises", "interrupted"):
        with pytest.raises((RuntimeError, KeyboardInterrupt)):
            gate(validator)
    else:
        assert gate(validator)["success"] is (outcome == "passes")
    assert validator.workspace_path == str(root) and not os.path.exists(seen[0])
    assert os.listdir(_views()) == []


@pytest.mark.parametrize("gate_outcome", ["returns", "raises"])
def test_a_view_that_cannot_be_removed_never_changes_what_the_gate_reported(tmp_path, monkeypatch, gate_outcome):
    root = _repo(tmp_path)
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    from kriya.tools.validate import _verification_gate

    def failing_rmtree(*_a, **_k):
        raise OSError(16, "Resource busy")

    monkeypatch.setattr(gate_view.shutil, "rmtree", failing_rmtree)

    def body(self):
        if gate_outcome == "raises":
            raise RuntimeError("the gate's own error")
        return {"success": True, "marker": "the gate's own result"}

    gate = _verification_gate("tests")(body)
    if gate_outcome == "raises":
        with pytest.raises(RuntimeError, match="the gate's own error"):
            gate(validator)
    else:
        assert gate(validator) == {"success": True, "marker": "the gate's own result"}
    assert validator.workspace_path == str(root)  # the root is restored even though the directory is left behind


# --- only declared outputs leave a view ----------------------------------------------------------------------------------------------

def _tree_listing(root):
    return sorted(os.path.relpath(os.path.join(d, n), root) for d, ds, ns in os.walk(root)
                  for n in ns + [x for x in ds if os.path.islink(os.path.join(d, x))] if ".git" not in os.path.relpath(d, root).split(os.sep))


def test_nothing_a_gate_writes_in_its_view_reaches_the_worktree_except_declared_outputs(tmp_path):
    root = _repo(tmp_path)
    before = _tree_listing(root)
    from kriya.tools.validate import _verification_gate

    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))

    def body(self):
        base = Path(self.workspace_path)
        (base / "__pycache__").mkdir()
        (base / "__pycache__" / "x.pyc").write_bytes(b"x")
        (base / "tests" / ".gitignore").write_text("*\n")
        (base / "tests" / "conftest.py").write_text("x = 1\n")
        (base / ".kriya").mkdir()
        (base / ".kriya" / "note").write_text("x")
        return {"success": True}

    _verification_gate("tests")(body)(validator)
    assert _tree_listing(root) == before and not (root / ".kriya").exists()


def test_a_gate_written_ignore_file_cannot_hide_what_it_created(tmp_path):
    """The self-hiding trick: the gate writes `sub/.gitignore` (`*`) and `sub/stray.txt`. The REPOSITORY's rules decide what is ignored - a rule the gate wrote
    in its own view must not redefine it - so both files are repository content the gate created without authority."""
    root = _repo(tmp_path)

    def body(self):
        (Path(self.workspace_path) / "sub").mkdir()
        Path(self.workspace_path, "sub", ".gitignore").write_text("*\n")
        Path(self.workspace_path, "sub", "stray.txt").write_text("hidden?\n")
        return {"success": True}

    with pytest.raises(VerificationGateCreatedFiles) as stopped:
        _view_gate(root, body)
    assert [entry["path"] for entry in stopped.value.failure.diagnostics["created"]] == ["sub/.gitignore", "sub/stray.txt"]


# --- the golden normalizer removes the random name only; a view and the worktree stay distinguishable ----------------------------------

def test_the_golden_normalizer_keeps_a_view_distinct_from_the_worktree():
    sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
    import _kernel_golden as kg

    norm = kg.Normalizer({"/ws": "workspace", "/state": "state_dir"})
    view = norm.text("/state/gate-views/view-ab12cd34")
    worktree = norm.text("/ws/.kriya/worktree")
    assert view == "<state_dir:1>/gate-views/<view>"
    assert worktree == "<workspace:1>/.kriya/worktree" and view != worktree
    assert "gate-views" in view and "worktree" in worktree


def test_every_application_maven_and_managed_service_spawn_in_the_engine_goldens_runs_in_a_view_and_none_runs_in_the_worktree():
    """The managed-service launch was the one named residual (outside the validator seam, on the worktree); D2 S4.1 closed it: it runs in the view too."""
    import json

    golden = Path(__file__).resolve().parent / "golden" / "runtime"
    view_rows, worktree_rows = [], []
    for path in sorted(golden.glob("engine_*.json")):
        for row in json.loads(path.read_text())["trace"]:
            if row.get("spawn") in ("app", "maven") or "start_managed" in row:
                assert row["cwd"] == "<state_dir:1>/gate-views/<view>", (path.name, row["cwd"])
                view_rows.append((path.name, "start_managed" in row))
            elif "cwd" in row and row["cwd"] == "<workspace:1>/.kriya/worktree":
                worktree_rows.append((path.name, "start_managed" in row))
    assert worktree_rows == []
    assert ("engine_managed_service_approved.json", True) in view_rows, "the managed-service launch is in the goldens, in a view"


# --- a view's directory name is stable per tree (failure text names it; retry detection compares failures across attempts) ----------------

def test_the_view_directory_is_the_same_for_every_gate_on_one_tree_and_different_for_another_tree(tmp_path):
    first, second = _repo(tmp_path, name="one"), _repo(tmp_path, name="two")
    seen = []
    for root in (first, first, second):
        view = _build(root)
        seen.append(view.root)
        view.close()
    assert seen[0] == seen[1] and seen[0] != seen[2]


def test_two_views_of_one_tree_open_at_once_get_distinct_directories_and_a_leftover_is_replaced(tmp_path):
    root = _repo(tmp_path)
    opened = _build(root)
    sibling = _build(root)
    try:
        assert opened.root != sibling.root and os.path.isdir(opened.root) and os.path.isdir(sibling.root)
    finally:
        opened.close()
        sibling.close()
    leftover = _build(root)
    Path(leftover.root, "residue.txt").write_text("left by a failed cleanup")
    gate_view._ACTIVE_ROOTS.discard(leftover.root)  # as if the process that built it had died before cleaning up
    again = _build(root)
    try:
        assert again.root == leftover.root and not Path(again.root, "residue.txt").exists()
    finally:
        again.close()


def test_an_acceptance_run_that_rewrites_the_candidate_while_it_runs_is_an_integrity_stop_even_without_a_tree_binding(tmp_path):
    """The oracle re-reads candidate_root after the run; the run changed THE VIEW, so the validator's report of what the run rewrote is what keeps this check
    meaningful (a validator without a tree binding, as the closers' own factories build for the named-test oracle and the bundle)."""
    from kriya.workflow import acceptance_oracle as ao
    from kriya.workflow.requirements import derive_requirements

    goal = "Add a double(x) function to calc/__init__.py so that double(5) returns 10.\n"
    acceptance = ("import pytest\n\nfrom calc import double\n\n\n@pytest.mark.kriya_requirement(\"REQ-1\")\n"
                  "def test_double_doubles():\n    open('calc/__init__.py', 'a').write('# rewritten while the acceptance file ran\\n')\n"
                  "    assert double(5) == 10\n")
    root = tmp_path / "cand"
    (root / "calc").mkdir(parents=True)
    (root / "calc" / "__init__.py").write_text("def double(x):\n    return 2 * x\n")
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "reviewed")
    path = tmp_path / "acceptance.py"
    path.write_text(acceptance)
    artifact = ao.load_acceptance(str(path), derive_requirements(goal), str(tmp_path / "state"))
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root), autonomy_cfg=AutonomyConfig())
    outcome = ao.run_acceptance(artifact, str(root), candidate_paths=["calc/__init__.py"], validator_factory=lambda: validator)
    assert outcome.integrity_problem is not None and outcome.integrity_problem[0] == ao.ACCEPTANCE_CANDIDATE_CHANGED_DURING_RUN
    assert (root / "calc" / "__init__.py").read_text() == "def double(x):\n    return 2 * x\n"  # and the shared tree never saw the rewrite
