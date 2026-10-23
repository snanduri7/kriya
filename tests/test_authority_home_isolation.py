"""D2 prerequisite: no test touches the real ~/.kriya/authority. tests/conftest.py gives every test its own authority home through
KRIYA_AUTHORITY_HOME; a CLI/waiter subprocess a test starts inherits it through the environment."""
import os
import subprocess
import sys

import _authority_isolation as isolation

from kriya.config import authority_approval
from kriya.config.environment import authority_home, kriya_home

_PRINT_HOME = "from kriya.config.environment import authority_home; print(authority_home())"


def _real_default_home():
    # the home a test would hit with no isolation; resolved from the account database, not from $HOME/KRIYA_* variables
    import pwd

    return os.path.realpath(os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".kriya", "authority"))


def test_the_authority_home_of_a_test_is_inside_the_test_sandbox(tmp_path_factory):
    home = os.path.realpath(authority_home())
    assert home != _real_default_home()
    assert os.path.commonpath([home, os.path.realpath(str(tmp_path_factory.getbasetemp()))]) == os.path.realpath(
        str(tmp_path_factory.getbasetemp())), f"{home} is outside the pytest base temp"
    assert os.path.realpath(authority_home()) != os.path.realpath(kriya_home("authority"))


def test_each_test_gets_its_own_authority_home_and_it_stays_empty_until_written(tmp_path):
    home = authority_home()
    assert not os.path.exists(home) or os.listdir(home) == []


def test_a_real_approval_write_lands_in_the_sandbox_and_not_in_the_real_home(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    real_before = sorted(os.listdir(_real_default_home())) if os.path.isdir(_real_default_home()) else None
    _artifact, path = authority_approval.grant_local_approval([], {}, str(workspace))
    assert os.path.isfile(path)
    assert os.path.realpath(path).startswith(os.path.realpath(authority_home()) + os.sep)
    assert not os.path.realpath(path).startswith(_real_default_home() + os.sep)
    real_after = sorted(os.listdir(_real_default_home())) if os.path.isdir(_real_default_home()) else None
    assert real_after == real_before, "the real authority home changed during the test"


def test_a_child_process_inherits_the_isolated_authority_home():
    out = subprocess.run([sys.executable, "-I", "-c", _PRINT_HOME], capture_output=True, text=True, check=True,
                         env=dict(os.environ)).stdout.strip()
    assert out == authority_home()
    assert os.path.realpath(out) != _real_default_home()


# ---- the bootstrap correction: the sandbox is installed by a SESSION fixture, so monkeypatch.undo() can only fall back to it ----

def test_monkeypatch_undo_restores_the_session_sandbox_and_never_the_real_home(monkeypatch, tmp_path_factory, tmp_path):
    """The M1a incident: a test called monkeypatch.undo(), which reverted the per-test isolation to the OPERATOR's value and the rest of the test wrote
    into the real ~/.kriya/authority. Now what undo() restores is the session sandbox."""
    per_test = os.path.realpath(authority_home())
    monkeypatch.undo()
    after_undo = os.path.realpath(authority_home())
    assert after_undo != per_test  # the undo really reverted the per-test fixture
    assert after_undo != _real_default_home() and after_undo != isolation.session_real_home()  # not the account default, not what the operator configured
    base = os.path.realpath(str(tmp_path_factory.getbasetemp()))
    assert os.path.commonpath([after_undo, base]) == base and os.path.basename(after_undo).startswith("kriya-authority-session")
    # ... and a real write after the undo lands in the sandbox, in this process and in a child process
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _artifact, path = authority_approval.grant_local_approval([], {}, str(workspace))
    assert os.path.realpath(path).startswith(after_undo + os.sep)
    child = subprocess.run([sys.executable, "-I", "-c", _PRINT_HOME], capture_output=True, text=True, check=True, env=dict(os.environ)).stdout.strip()
    assert os.path.realpath(child) == after_undo


def test_the_snapshot_guard_reports_added_removed_and_modified_entries_and_never_writes(tmp_path):
    home = tmp_path / "home"
    (home / "sub").mkdir(parents=True)
    (home / "a.json").write_text("one")
    (home / "sub" / "b.json").write_text("two")
    before = isolation.snapshot(str(home))
    stat_before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in (home, home / "a.json", home / "sub" / "b.json")}
    assert isolation.changes(before, isolation.snapshot(str(home))) == ()
    assert {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in stat_before} == stat_before  # reading it changed nothing
    assert sorted(os.listdir(home)) == ["a.json", "sub"]
    (home / "c.json").write_text("new")
    (home / "a.json").write_text("one, edited")
    (home / "sub" / "b.json").unlink()
    found = isolation.changes(before, isolation.snapshot(str(home)))
    assert "added: c.json" in found and "removed: " + os.path.join("sub", "b.json") in found and "modified: a.json" in found


def test_the_snapshot_guard_distinguishes_a_missing_home_from_a_created_one(tmp_path):
    home = tmp_path / "not-yet"
    assert isolation.snapshot(str(home)) is None and isolation.changes(None, None) == ()
    home.mkdir()
    assert isolation.changes(None, isolation.snapshot(str(home)))
    assert isolation.changes(isolation.snapshot(str(home)), None) == ("the home was removed",)


def _inner_session(tmp_path, body: str, *, real_home):
    """A real pytest session (a child process) that registers the SAME session fixture; its 'operator-configured' authority home is ``real_home``."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})\n"
        "from _authority_isolation import _authority_home_isolation  # noqa: F401\n"
        "\n\ndef pytest_sessionfinish(session):  # after the session fixtures were torn down\n"
        "    import os\n    print('AUTHORITY_AT_FINISH=' + os.environ.get('KRIYA_AUTHORITY_HOME', '<unset>'))\n")
    (inner / "test_inner.py").write_text(body)
    env = {**os.environ, "KRIYA_AUTHORITY_HOME": str(real_home), "INNER_TARGET": str(real_home)}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "-c", "/dev/null", str(inner)],
                          cwd=inner, env=env, capture_output=True, text=True, timeout=120)


def test_the_session_guard_fails_the_session_when_the_real_home_changes_and_deletes_or_restores_nothing(tmp_path):
    real = tmp_path / "pretend-real-home"
    real.mkdir()
    (real / "approval.json").write_text("operator data")
    body = ("import os\n\n\ndef test_writes_outside_its_sandbox():\n"
            "    open(os.path.join(os.environ['INNER_TARGET'], 'stray.json'), 'w').write('x')\n")
    outcome = _inner_session(tmp_path, body, real_home=real)
    assert outcome.returncode != 0 and "real authority home" in outcome.stdout + outcome.stderr and "stray.json" in outcome.stdout + outcome.stderr
    assert (real / "stray.json").read_text() == "x" and (real / "approval.json").read_text() == "operator data"  # the guard reports, never repairs


def test_the_session_guard_stays_quiet_when_a_test_undoes_its_monkeypatch_and_writes_through_the_authority_home(tmp_path):
    real = tmp_path / "pretend-real-home"
    real.mkdir()
    (real / "approval.json").write_text("operator data")
    body = ("import os\n\n\ndef test_undo_then_write(monkeypatch, tmp_path):\n"
            "    monkeypatch.setenv('KRIYA_AUTHORITY_HOME', str(tmp_path / 'per-test'))\n"
            "    monkeypatch.undo()\n"
            "    home = os.environ['KRIYA_AUTHORITY_HOME']\n"
            "    assert home != os.environ['INNER_TARGET'] and 'kriya-authority-session' in home\n"
            "    os.makedirs(home, exist_ok=True)\n"
            "    open(os.path.join(home, 'written.json'), 'w').write('x')\n")
    outcome = _inner_session(tmp_path, body, real_home=real)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert sorted(os.listdir(real)) == ["approval.json"]
    assert f"AUTHORITY_AT_FINISH={real}" in outcome.stdout  # the operator's environment is put back exactly as it was
