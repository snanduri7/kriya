"""CERT-GIT-MAINTENANCE-CLASS-001: the mechanical tripwire (tests/_git_quiescence.py) that closes "a test snapshots a repository while git's detached auto-maintenance may run".

What this file proves (owner ruling 2026-11-04, points 3 and 9):
  * the population is derived from what runs: a SYNTHETIC helper nobody listed anywhere is caught;
  * it catches the real helpers that were the defect - the three fixed in 1de8732 and the two found by hosted run 41840628981 - when the guard is taken out (the mutation);
  * its inference ("an unguarded commit-class git starts maintenance") agrees with git's own trace2 on this machine, both ways;
  * the REAL conftest fixture fails a test end to end, honours the guard, and its report mode records instead of failing;
  * the exemption marker cannot be used outside this file.
These tests commit an UNGUARDED repository on purpose, so they carry ``git_quiescence_exempt`` (the tripwire's own tests only)."""
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import _git_quiescence as q
import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
pytestmark = pytest.mark.git_quiescence_exempt

IDENTITY = ["-c", "user.email=t@t", "-c", "user.name=t"]


@pytest.fixture(autouse=True)
def _fresh_record():
    q.forget_everything()
    q.begin_test("tripwire-under-test")
    yield
    q.forget_everything()


def _commit(repo: Path, *, guarded: bool, via_C: bool = False) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "f.txt").write_text("x\n")
    guard = ["-c", "maintenance.auto=false", "-c", "gc.auto=0"] if guarded else []
    prefix = ["git", "-C", str(repo)] if via_C else ["git"]
    cwd = None if via_C else repo
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run([*prefix, *guard, *IDENTITY, "commit", "-qm", "c"], cwd=cwd, check=True, capture_output=True)


def _snapshot_walk(repo: Path) -> int:
    """A plain full-tree snapshot, .git included: the oi142 / s19e / logging_location shape."""
    return sum(len(names) for _d, _dirs, names in os.walk(repo))


# ------------------------------------------------------------------------------------------------------------------ the rules

def test_the_subcommand_is_found_past_global_options():
    assert q.subcommand_of(["git", "-c", "a=b", "-C", "/x", "commit", "-m", "y"]) == "commit"
    assert q.subcommand_of(["git", "--git-dir", "/x/.git", "fetch"]) == "fetch"
    assert q.subcommand_of(["git", "-c", "maintenance.auto=false"]) is None


@pytest.mark.parametrize("argv, env, guarded", [
    (["git", "-c", "maintenance.auto=false", "commit"], {}, True),
    (["git", "commit"], {}, False),
    (["git", "-c", "gc.auto=0", "commit"], {}, False),                    # gc.auto alone does not stop `maintenance run --auto`
    (["git", "commit"], {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "false"}, True),
    (["git", "commit"], {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "true"}, False),
    (["git", "commit"], {"GIT_CONFIG_PARAMETERS": "'maintenance.auto'='false'"}, True),
])
def test_what_counts_as_the_maintenance_guard(argv, env, guarded):
    assert q.is_guarded(argv, env) is guarded


# ------------------------------------------------------------------------------------------------------------------ the tripwire on a new helper nobody listed

def test_a_new_unguarded_helper_whose_repository_is_then_snapshotted_is_caught(tmp_path):
    def brand_new_fixture_helper(root):                      # defined here, listed nowhere: the population is mechanical
        _commit(root, guarded=False)
        return root
    repo = brand_new_fixture_helper(tmp_path / "r")
    _snapshot_walk(repo)
    found = q.violations_of_current_test()
    assert len(found) == 1 and found[0].repository == os.path.realpath(repo) and found[0].commit.subcommand == "commit"
    assert "test_git_quiescence_tripwire.py:_commit" in found[0].commit.helper.replace("tests/", "") and not found[0].commit.product
    assert "os.scandir" in found[0].observer and "maintenance.auto=false" in found[0].render()


def test_the_same_helper_with_the_guard_is_not_flagged(tmp_path):
    repo = tmp_path / "r"
    _commit(repo, guarded=True)
    _snapshot_walk(repo)
    assert q.violations_of_current_test() == []


def test_a_commit_made_through_dash_C_is_attributed_to_that_repository(tmp_path):
    repo = tmp_path / "r"
    _commit(repo, guarded=False, via_C=True)
    _snapshot_walk(repo)
    assert [v.repository for v in q.violations_of_current_test()] == [os.path.realpath(repo)]


def test_a_test_that_never_observes_the_repository_is_not_flagged(tmp_path):
    _commit(tmp_path / "r", guarded=False)
    assert q.violations_of_current_test() == []


def test_a_walk_that_prunes_dot_git_is_not_an_observer(tmp_path):
    repo = tmp_path / "r"
    _commit(repo, guarded=False)
    for _d, dirs, _names in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
    assert q.violations_of_current_test() == []


def test_observing_another_repository_is_not_flagged(tmp_path):
    _commit(tmp_path / "committed", guarded=False)
    _commit(tmp_path / "other", guarded=True)
    _snapshot_walk(tmp_path / "other")
    assert q.violations_of_current_test() == []


def test_listing_dot_git_objects_directly_is_an_observer(tmp_path):
    repo = tmp_path / "r"
    _commit(repo, guarded=False)
    os.listdir(repo / ".git" / "objects")
    assert [v.repository for v in q.violations_of_current_test()] == [os.path.realpath(repo)]


def test_a_commit_launched_by_product_code_is_labelled_as_product(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "f").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    code = compile("import subprocess\ndef product_commit(cwd, identity):\n    subprocess.run(['git', *identity, 'commit', '-qm', 'c'], cwd=cwd, check=True, capture_output=True)\n",
                   str(ROOT / "kriya" / "fake_product_module.py"), "exec")
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102 - a frame whose file is under kriya/ is how "product code launched it" is observed
    namespace["product_commit"](str(repo), IDENTITY)
    _snapshot_walk(repo)
    [found] = q.violations_of_current_test()
    assert found.commit.product and "PRODUCT code" in found.render()


# ------------------------------------------------------------------------------------------------------------------ agreement with git itself

@pytest.mark.parametrize("guarded", [False, True], ids=["unguarded", "guarded"])
def test_the_inference_agrees_with_gits_own_trace2(tmp_path, monkeypatch, guarded):
    """The tripwire never asks git; this asks git. An unguarded commit starts `git maintenance` and the tripwire records it; a guarded one starts nothing and it records nothing."""
    trace = tmp_path / "trace2.jsonl"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    repo = tmp_path / "r"
    _commit(repo, guarded=guarded)
    spawned = [json.loads(line) for line in trace.read_text().splitlines() if '"child_start"' in line]
    started_maintenance = any("maintenance" in event.get("argv", []) for event in spawned)
    _snapshot_walk(repo)
    assert started_maintenance is (not guarded)
    assert bool(q.violations_of_current_test()) is started_maintenance


# ------------------------------------------------------------------------------------------------------------------ the mutation: take the guard out of every real helper

def _real_helper_builders():
    def make_base(root):
        from _named_test_oracle_harness import make_base as build
        return build(root / "named")

    def maven_workspace(root):
        from _b2c_fixtures import maven_workspace as build
        (root / "b2c").mkdir()
        return build(root / "b2c")

    def kup_repo(root):
        from test_kup_workspace import _repo as build
        return build(root / "kup")

    def s19e_repository(root):
        from test_s19e_honest_refusal import _repository as build
        (root / "s19e").mkdir()
        return build(root / "s19e", commit=True)

    def logging_location_workspace(root):
        from test_logging_location import _git_workspace as build
        return build(root / "logging")
    def through_a_wrapper(module, name):
        """The census-flagged helpers that are a ``git(cwd, *args)`` wrapper: init + add + commit through THEIR wrapper, so their embedded guard is what is measured."""
        def build(root):
            import importlib
            wrapper = getattr(importlib.import_module(module), name)
            repo = root / "w"
            repo.mkdir(parents=True)
            (repo / "f.txt").write_text("x\n")
            wrapper(repo, "init", "-q")
            wrapper(repo, "add", "-A")
            wrapper(repo, "commit", "-qm", "c")
            return repo
        return build

    def workflow_controller_enforce(root):
        from test_workflow_controller_enforce import _init_git_repo as build
        (root / "wce").mkdir()
        build(root / "wce")
        return root / "wce"
    wrappers = {f"{module}.{name}": through_a_wrapper(module, name) for module, name in (
        ("test_repair_003_p1_2_suite_preservation_attribution", "_git"), ("test_s18c_read_root_identity", "git"), ("_chaos_harness", "git"),
        ("test_w33_u3_scoped_snapshot_projection", "_git"), ("test_backend_final_closure_005_partial_no_change", "_git"),
        ("test_verification_contract_003_baseline", "_git"), ("test_enforce_verified_no_change", "_git"), ("test_r5_r6_bound_tree_copy", "_git"),
        ("test_taf4_snapshot_fallback", "_git"))}
    return {**wrappers, "test_workflow_controller_enforce._init_git_repo": workflow_controller_enforce,
            "named_test_oracle_harness.make_base": make_base, "b2c_fixtures.maven_workspace": maven_workspace, "kup_workspace._repo": kup_repo,
            "s19e_honest_refusal._repository": s19e_repository, "logging_location._git_workspace": logging_location_workspace}


REAL_HELPERS = _real_helper_builders()


def _strip_the_guard(monkeypatch):
    """The mutation that restores the defect: every `-c maintenance.auto=false` / `-c gc.auto=0` pair is removed from every git argv before the process starts."""
    original = subprocess.Popen.__init__

    def mutated(self, args, *rest, **kwargs):
        if isinstance(args, (list, tuple)) and args and os.path.basename(str(args[0])) == "git":
            kept, skip = [], False
            for index, token in enumerate(args):
                if skip:
                    skip = False
                    continue
                if token == "-c" and index + 1 < len(args) and str(args[index + 1]) in ("maintenance.auto=false", "gc.auto=0"):
                    skip = True
                    continue
                kept.append(token)
            args = kept
        original(self, args, *rest, **kwargs)
    monkeypatch.setattr(subprocess.Popen, "__init__", mutated)


@pytest.mark.parametrize("helper", sorted(REAL_HELPERS))
def test_every_real_helper_is_clean_as_written_and_caught_when_its_guard_is_removed(tmp_path, monkeypatch, helper):
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "t")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "t@t")
    (tmp_path / "as-written").mkdir()
    (tmp_path / "mutated").mkdir()
    built = REAL_HELPERS[helper](tmp_path / "as-written")
    _snapshot_walk(Path(str(built)))
    assert q.violations_of_current_test() == [], f"{helper} commits without the guard"
    q.forget_everything()
    q.begin_test("mutated")
    _strip_the_guard(monkeypatch)
    mutated = REAL_HELPERS[helper](tmp_path / "mutated")
    _snapshot_walk(Path(str(mutated)))
    found = q.violations_of_current_test()
    assert found, f"{helper}: with the guard removed the tripwire did not fire - the mutation survived"
    assert os.path.realpath(str(mutated)) == found[0].repository or os.path.realpath(str(mutated)).startswith(found[0].repository)


# ------------------------------------------------------------------------------------------------------------------ the REAL fixture, end to end

CONFTEST = textwrap.dedent('''
    import importlib.util, sys
    sys.path.insert(0, {tests!r})
    spec = importlib.util.spec_from_file_location("kriya_tests_conftest", {conftest!r})
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _git_maintenance_cannot_race_a_repository_snapshot = module._git_maintenance_cannot_race_a_repository_snapshot
''')

TESTFILE = textwrap.dedent('''
    import os, subprocess
    import pytest

    def _repo(path, guard):
        path.mkdir()
        (path / "f").write_text("x")
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        flags = ["-c", "maintenance.auto=false", "-c", "gc.auto=0"] if guard else []
        subprocess.run(["git", *flags, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"], cwd=path, check=True, capture_output=True)

    def _snapshot(path):
        return [names for _d, _dirs, names in os.walk(path)]

    def test_new_unguarded_helper(tmp_path):
        _repo(tmp_path / "r", guard=False)
        _snapshot(tmp_path / "r")

    def test_guarded_helper(tmp_path):
        _repo(tmp_path / "r", guard=True)
        _snapshot(tmp_path / "r")

    def test_unguarded_but_never_snapshotted(tmp_path):
        _repo(tmp_path / "r", guard=False)

    @pytest.mark.git_quiescence_exempt
    def test_exempt(tmp_path):
        _repo(tmp_path / "r", guard=False)
        _snapshot(tmp_path / "r")
''')


def _run_the_real_fixture(tmp_path, extra_env=None):
    (tmp_path / "conftest.py").write_text(CONFTEST.format(tests=str(TESTS), conftest=str(TESTS / "conftest.py")))
    (tmp_path / "test_synthetic.py").write_text(TESTFILE)
    env = {**os.environ, "PYTHONPATH": str(ROOT), **(extra_env or {})}
    env.pop("KRIYA_GIT_QUIESCENCE", None)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "-c", os.devnull, "--rootdir", str(tmp_path), "--override-ini=markers=git_quiescence_exempt: the tripwire's own tests only",
                           "--import-mode=importlib", str(tmp_path / "test_synthetic.py")], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)


def test_the_real_fixture_fails_only_the_unguarded_snapshotting_test(tmp_path):
    done = _run_the_real_fixture(tmp_path)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "ERROR test_synthetic.py::test_new_unguarded_helper" in out, out
    assert "git auto-maintenance can race this test's repository snapshot" in out and "test_synthetic.py:_repo" in out, out
    for passed in ("test_guarded_helper", "test_unguarded_but_never_snapshotted", "test_exempt"):
        assert f"ERROR test_synthetic.py::{passed}" not in out and f"FAILED test_synthetic.py::{passed}" not in out, out
    assert re.search(r"\b1 error\b", out) and "4 passed" in out, out   # the body of the unguarded test passes; its TEARDOWN errors


def test_report_mode_records_every_commit_and_overlap_without_failing(tmp_path):
    destination = tmp_path / "overlaps.jsonl"
    done = _run_the_real_fixture(tmp_path, {"KRIYA_GIT_QUIESCENCE": f"report:{destination}"})
    assert done.returncode == 0, done.stdout + done.stderr
    records = [json.loads(line) for line in destination.read_text().splitlines()]
    commits = [r for r in records if r["kind"] == "commit"]
    overlaps = [r for r in records if r["kind"] == "overlap"]
    assert len(commits) == 4 and sum(1 for r in commits if r["guarded"]) == 1, records
    assert sorted(r["test"].split("::")[-1] for r in overlaps) == ["test_exempt", "test_new_unguarded_helper"], records


# ------------------------------------------------------------------------------------------------------------------ the exemption cannot spread

def test_the_exemption_marker_is_used_by_no_other_test_file():
    users = sorted(path.name for path in TESTS.glob("**/*.py")
                   if "git_quiescence_exempt" in path.read_text(encoding="utf-8", errors="replace") and path.name not in {"test_git_quiescence_tripwire.py", "conftest.py", "_git_quiescence.py"})
    assert users == [], f"only the tripwire's own tests may be exempt from it: {users}"


# ------------------------------------------------------------------------------------------------------------------ judge(): fixture-origin always fails; product-origin only with an honest acknowledgement

def _violation(product: bool) -> q.Violation:
    return q.Violation("t::x", "/r", q.UnguardedCommit("/r", "commit", "tests/x.py:h:1", product), "os.scandir(/r/.git) at tests/x.py:t:2", 2)


def test_a_fixture_origin_overlap_always_fails_even_with_the_product_marker():
    assert q.judge([_violation(False)], product_marker=False)
    assert q.judge([_violation(False)], product_marker=True)          # the marker never excuses a fixture's own commit


def test_a_product_origin_overlap_fails_unless_the_test_acknowledges_it():
    assert q.judge([_violation(True)], product_marker=False)
    assert q.judge([_violation(True)], product_marker=True) == []


def test_a_product_marker_with_no_product_overlap_behind_it_is_itself_a_failure():
    assert "stale" in " ".join(q.judge([], product_marker=True))
    assert "stale" in " ".join(q.judge([_violation(False)], product_marker=True))


def test_only_the_product_origin_tests_carry_the_product_marker():
    """Every use of the marker is backed, at run time, by a product-origin overlap (judge() fails a stale one); here: no other kind of file may use it."""
    users = sorted(path.name for path in TESTS.glob("**/*.py")
                   if "mark.git_quiescence_product_commit" in path.read_text(encoding="utf-8", errors="replace") and path.name not in {"test_git_quiescence_tripwire.py", "conftest.py", "_git_quiescence.py"})
    assert users == ["test_file_integrity_contract_001b.py"], users


# ------------------------------------------------------------------------------------------------------------------ attribution names the TEST helper, never the stdlib frame that did the listing

def _named_snapshot_helper(repo: Path) -> None:
    for _directory, _dirs, _names in os.walk(repo):
        pass


def test_an_os_walk_inside_a_named_helper_is_attributed_to_that_helper_not_to_the_stdlib(tmp_path):
    _commit(tmp_path / "repo", guarded=False)
    q.begin_test("attribution")
    _named_snapshot_helper(tmp_path / "repo")
    found = q.violations_of_current_test()
    assert found, "an unguarded commit and a walk of the repository must be an overlap"
    assert "_named_snapshot_helper" in found[0].observer and "<frozen" not in found[0].observer and "os.py" not in found[0].observer, found[0].observer


# ------------------------------------------------------------------------------------------------------------------ the shell hole: git launched by a script is invisible to the audit hook

#: tracked shell scripts under these roots that run a commit-class git without the guard, MEASURED 2026-11-05 (whole-suite trace2: 11 repositories created by
#: demo-v2/scripts/reset_demo.sh and scenario02_run.sh were seen by git's own trace2 and by no Python audit event). The only test that runs them and lists the
#: result is tests/test_demo_v2_owner_state.py, whose snapshot walks the work area with `.git` EXCLUDED BY NAME before reading a byte. A RATCHET, not an exemption list:
#: a NEW unguarded line anywhere under these roots fails, and a line that gains the guard (or disappears) must be removed from here.
SHELL_LAUNCHED_UNGUARDED = {          # file -> number of unguarded commit-class lines (a SECOND line in a listed file fails too)
    "demo-v2/scripts/config_approval_smoke.sh": 1,
    "demo-v2/scripts/product_ux_smoke.sh": 1,
    "demo-v2/scripts/reset_demo.sh": 1,
    "demo-v2/scripts/scenario02_run.sh": 1,
    "demo-v2/scripts/ux_smoke.sh": 1,
}
_SHELL_ROOTS = ("demo-v2/", "scripts/", "tests/")
_COMMIT_CLASS = q.COMMIT_CLASS


def _scripts_with_an_unguarded_commit_class_git(texts):
    """{file: number of unguarded commit-class git lines}"""
    found = {}
    for name, text in texts.items():
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if any(not guarded for _sub, guarded, _dir in q.commit_class_in_text(line)):
                found[name] = found.get(name, 0) + 1
    return found


@pytest.mark.parametrize("line, flagged", [
    ('git -C "$WS" -c user.email=a@b -c user.name="Kriya demo" commit -q -m x', True),
    ("git commit -qm x", True),
    ("git fetch --no-tags origin x", True),
    ("git -c maintenance.auto=false -c gc.auto=0 commit -qm x", False),
    ('git merge-base --is-ancestor "$A" HEAD', False),
    ('fail "tag missing (git fetch --tags)"', False),
    ("# git commit -qm x", False),
    ("git status --porcelain", False),
])
def test_the_shell_scan_flags_exactly_the_unguarded_commit_class_lines(line, flagged):
    assert (_scripts_with_an_unguarded_commit_class_git({"s.sh": line}) == {"s.sh": 1}) is flagged


def test_a_second_unguarded_line_in_an_already_listed_script_is_a_new_failure():
    two = "git commit -qm a\ngit -c maintenance.auto=false commit -qm b\ngit -C d commit -qm c\n"
    assert _scripts_with_an_unguarded_commit_class_git({"s.sh": two}) == {"s.sh": 2}


def test_a_shell_script_that_runs_a_commit_class_git_without_the_guard_is_a_known_residual_or_a_failure():
    root = TESTS.parent
    tracked = [name for name in subprocess.run(["git", "ls-files", "-z", "--", "*.sh"], cwd=root, check=True, capture_output=True, text=True).stdout.split("\0")
               if name.startswith(_SHELL_ROOTS)]
    unguarded = _scripts_with_an_unguarded_commit_class_git({name: (root / name).read_text(encoding="utf-8", errors="replace") for name in tracked})
    assert unguarded == SHELL_LAUNCHED_UNGUARDED, f"unguarded commit-class lines per script: found {unguarded}, ratchet {SHELL_LAUNCHED_UNGUARDED} (a script that gained the guard must be removed from the ratchet)"


# ------------------------------------------------------------------------------------------------------------------ the documented residual: an observer that never LISTS the directories (a bare stat)

#: the TIMES (``getmtime`` / ``st_mtime`` / ``st_ctime``) of the `.git` directory ITSELF or of `.git/objects` - what maintenance moves (not a file inside: `.git/index`, `.git/config`, `.git/HEAD` are never touched by it; not `st_dev`).
#: The audit hook cannot see a stat; this static scan is what covers it. MEASURED 2026-11-05: no test file does it.
_BARE_STAT_OF_GIT_DIR = re.compile(r"""(?:getmtime|getctime)\([^\n]*?(?:["']\.git["']|["']objects["']|/\s*\.git)\s*\)|\bstat\([^\n]*?(?:["']\.git["']|["']objects["'])\s*\)+\.st_[mc]time""")


def test_no_test_stats_the_git_directory_or_its_objects_directory_directly():
    found = []
    for path in sorted(TESTS.glob("**/*.py")):
        if path.name in {"test_git_quiescence_tripwire.py", "_git_quiescence.py"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _BARE_STAT_OF_GIT_DIR.search(line):
                found.append(f"{path.name}:{number}: {line.strip()[:110]}")
    assert found == [], "a stat of .git / .git/objects is invisible to the audit hook; list it (so the tripwire sees it) or guard its fixture:\n" + "\n".join(found)


@pytest.mark.parametrize("line, flagged", [
    ('mtime = os.stat(os.path.join(repo, ".git", "objects")).st_mtime', True),
    ('before = os.path.getmtime(repo / ".git")', True),
    ("os.stat(folder / '.git' / 'index').st_mtime_ns", False),
    ('os.stat(root / ".git" / "config")', False),
    ('os.path.isdir(os.path.join(ws, ".git"))', False),
])
def test_the_bare_stat_scan_flags_the_directory_itself_and_not_the_files_in_it(line, flagged):
    assert bool(_BARE_STAT_OF_GIT_DIR.search(line)) is flagged


# ------------------------------------------------------------------------------------------------------------------ every other way Python can start a git commit: shell text, shell=True, os.system, posix_spawn

def _launch_unguarded_and_snapshot(tmp_path, launch) -> bool:
    repo = tmp_path / "r"
    repo.mkdir(parents=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    q.begin_test("route")
    launch(repo)
    _snapshot_walk(repo)
    return bool(q.violations_of_current_test())


_GUARD_TEXT = "-c maintenance.auto=false -c gc.auto=0"
_ID = "-c user.email=t@t -c user.name=t"
ROUTES = {
    "shell=True string": lambda repo, guard: subprocess.run(f"git {guard} {_ID} commit -qm c", shell=True, cwd=repo, check=True, capture_output=True),
    "sh -c": lambda repo, guard: subprocess.run(["sh", "-c", f"git {guard} {_ID} commit -qm c"], cwd=repo, check=True, capture_output=True),
    "bash -c with a pipeline and a cd": lambda repo, guard: subprocess.run(["bash", "-c", f"cd {repo} && true | git {guard} {_ID} commit -qm c"], cwd=repo, check=True, capture_output=True),
    "bash -c with git -C": lambda repo, guard: subprocess.run(["bash", "-c", f"git -C {repo} {guard} {_ID} commit -qm c"], cwd="/", check=True, capture_output=True),
    "os.system": lambda repo, guard: os.system(f"cd {repo} && git {guard} {_ID} commit -qm c >/dev/null 2>&1"),
    "os.posix_spawn": lambda repo, guard: os.waitpid(os.posix_spawn(shutil.which("git"), ["git", "-C", str(repo), *guard.split(), *_ID.split(), "commit", "-qm", "c"], os.environ), 0),
}


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_every_python_route_to_an_unguarded_commit_is_seen_and_every_guarded_one_is_not(tmp_path, monkeypatch, route):
    if route == "os.posix_spawn":
        monkeypatch.setenv("PATH", os.environ["PATH"])
    unguarded = _launch_unguarded_and_snapshot(tmp_path / "u", lambda repo: ROUTES[route](repo, ""))
    q.forget_everything()
    guarded = _launch_unguarded_and_snapshot(tmp_path / "g", lambda repo: ROUTES[route](repo, _GUARD_TEXT))
    assert unguarded is True, f"{route}: an unguarded commit launched this way was not seen"
    assert guarded is False, f"{route}: the guard on the command was not recognised"


def test_a_guarded_first_command_does_not_excuse_an_unguarded_second_one_in_the_same_shell_text(tmp_path):
    flagged = _launch_unguarded_and_snapshot(tmp_path, lambda repo: subprocess.run(
        ["sh", "-c", f"git {_GUARD_TEXT} {_ID} commit -qm a --allow-empty; git {_ID} commit -qm b --allow-empty"], cwd=repo, check=True, capture_output=True))
    assert flagged is True


# ------------------------------------------------------------------------------------------------------------------ the documented residual: an EXTERNAL process that copies or compares a repository

_EXTERNAL_COPIER = re.compile(r"""\[\s*["'](?:tar|rsync|ditto|cpio|zip)["']|\[\s*["']cp["']\s*,\s*["']-[a-zA-Z]*[rRa]|\[\s*["']diff["']\s*,\s*["']-[a-zA-Z]*r|(?:subprocess\.\w+|os\.system)\(\s*f?["'](?:tar|rsync|ditto|cp -[a-zA-Z]*[rRa]|diff -[a-zA-Z]*r)\b""")


def test_no_test_launches_an_external_copier_or_comparer_of_a_repository():
    """tar / rsync / ditto / cp -R / diff -r read .git in a process the audit hook cannot see. MEASURED 2026-11-05: none exists; a new one fails here."""
    found = []
    for path in sorted(TESTS.glob("**/*.py")):
        if path.name in {"test_git_quiescence_tripwire.py", "_git_quiescence.py"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _EXTERNAL_COPIER.search(line):
                found.append(f"{path.name}:{number}: {line.strip()[:110]}")
    assert found == [], "an external process reading a repository is invisible to the audit hook; copy/compare it from Python (so the tripwire lists it) or guard its fixture:\n" + "\n".join(found)


@pytest.mark.parametrize("line, flagged", [
    ('subprocess.run(["tar", "cf", "x.tar", str(repo)])', True),
    ('subprocess.run(["rsync", "-a", src, dst])', True),
    ('subprocess.run(["cp", "-R", src, dst])', True),
    ('subprocess.run(["diff", "-r", a, b])', True),
    ('subprocess.run(["git", "diff", "--stat"])', False),
    ('subprocess.run(["cp", "a.txt", "b.txt"])', False),
])
def test_the_external_copier_scan_has_negative_controls(line, flagged):
    assert bool(_EXTERNAL_COPIER.search(line)) is flagged
