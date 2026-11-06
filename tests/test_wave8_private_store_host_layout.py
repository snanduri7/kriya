"""WAVE8 OI-134 / OI-154: the private-store protection against REAL host layouts (owner instruction 2026-11-06, item 4).

tests/test_wave8_private_stores.py proves the rules by faking ``_current_uid`` (every path then looks foreign). That cannot show the case that matters on a
real host: the user's own files and directories are accepted while an artifact genuinely owned by another account (a ``sudo``-created ``~/.kriya`` or state
directory) is refused. Here nothing about identity is faked: the host-identity port answers with this process's real uid, and the foreign artifacts are
directories/files that really belong to root on this host (``/var/empty``, ``/etc/hosts``), found by stat, never created or chmodded by the tests. No second
OS user, no sudo. A test is skipped (never passed vacuously) when this host has no such artifact or the suite itself runs as root.
"""
import os
import stat
import subprocess

import pytest

from kriya.config import AppConfig
from kriya.control import control_store
from kriya.platform import private_store as ps
from kriya.platform.private_store import PrivateStoreUnsafeError
from kriya.repository.atomic_write import atomic_write_file

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX ownership/mode semantics")

FOREIGN_DIRECTORY = "/var/empty"
FOREIGN_FILE = "/etc/hosts"


def fingerprint(path):
    info = os.lstat(path)
    return (info.st_uid, stat.S_IMODE(info.st_mode), info.st_ino)


def require_foreign(path, is_dir):
    try:
        info = os.lstat(path)
    except OSError:
        pytest.skip(f"{path} does not exist on this host")
    kind_ok = stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode)
    if not kind_ok or info.st_uid == os.getuid():
        pytest.skip(f"{path} is not another account's {'directory' if is_dir else 'file'} for this user")


@pytest.fixture
def repo(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    return workspace


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A user-owned HOME with Kriya's default layout; no environment override of the state or authority homes."""
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("KRIYA_STATE_DIR", raising=False)
    monkeypatch.delenv("KRIYA_AUTHORITY_HOME", raising=False)
    return path


# ---------------------------------------------------------------------------- the normal current-user layout stays usable

def test_a_normal_current_user_kriya_home_is_tightened_and_stays_usable_across_runs(home, repo):
    kriya_home = home / ".kriya"
    state = kriya_home / "state"
    state.mkdir(parents=True)
    legacy = state / "traces.db"
    legacy.write_text("legacy-history")
    for directory in (kriya_home, state):
        os.chmod(directory, 0o755)
    os.chmod(legacy, 0o644)

    control_store.ensure_private_roots(str(repo), AppConfig())             # run 1: the owner's own legacy layout is adopted, not refused
    assert stat.S_IMODE(os.lstat(kriya_home).st_mode) == 0o700 and stat.S_IMODE(os.lstat(state).st_mode) == 0o700

    atomic_write_file(str(legacy), "run-1", private=True)                  # and it is usable: the private write replaces the legacy file
    assert legacy.read_text() == "run-1" and stat.S_IMODE(os.lstat(legacy).st_mode) == 0o600

    before = {str(p): fingerprint(p) for p in (kriya_home, state, legacy)}
    control_store.ensure_private_roots(str(repo), AppConfig())             # run 2: idempotent, still accepted, nothing disturbed
    atomic_write_file(str(legacy), "run-2", private=True)
    assert {str(p): fingerprint(p)[:2] for p in (kriya_home, state, legacy)} == {k: v[:2] for k, v in before.items()}
    assert legacy.read_text() == "run-2"


def test_a_fresh_host_with_no_kriya_home_gets_private_roots_and_is_usable(home, repo):
    control_store.ensure_private_roots(str(repo), AppConfig())
    created = [home / ".kriya", home / ".kriya" / "state"]            # Kriya's home is created as the (private) parent of its own state directory
    assert all(p.is_dir() and not p.is_symlink() for p in created)
    for directory in created:
        assert stat.S_IMODE(os.lstat(directory).st_mode) == 0o700 and os.lstat(directory).st_uid == os.getuid()
    atomic_write_file(str(created[1] / "traces.db"), "fresh", private=True)
    assert (created[1] / "traces.db").read_text() == "fresh"


# ---------------------------------------------------------------------------- a REALLY foreign artifact is refused, by ownership, untouched

@pytest.mark.parametrize("variable", ["KRIYA_STATE_DIR", "KRIYA_AUTHORITY_HOME"])
def test_a_directory_owned_by_another_account_stops_the_run_and_is_never_touched(home, repo, tmp_path, monkeypatch, variable):
    require_foreign(FOREIGN_DIRECTORY, is_dir=True)
    other = {"KRIYA_STATE_DIR": "KRIYA_AUTHORITY_HOME", "KRIYA_AUTHORITY_HOME": "KRIYA_STATE_DIR"}[variable]
    own = tmp_path / "own"
    own.mkdir()
    monkeypatch.setenv(other, str(own))                                    # the other root is the user's own: only THIS one can be the cause
    monkeypatch.setenv(variable, FOREIGN_DIRECTORY)
    before = fingerprint(FOREIGN_DIRECTORY)
    with pytest.raises(PrivateStoreUnsafeError, match="owned by uid .* not by this user"):    # the ownership refusal, not a failed chmod
        control_store.ensure_private_roots(str(repo), AppConfig())
    assert fingerprint(FOREIGN_DIRECTORY) == before
    assert not (repo / ".kriya").exists(), "the run stopped before the workspace control roots were created"


def test_a_file_owned_by_another_account_is_neither_adopted_nor_replaced(tmp_path):
    require_foreign(FOREIGN_FILE, is_dir=False)
    before = fingerprint(FOREIGN_FILE)
    with pytest.raises(PrivateStoreUnsafeError, match="owned by uid .* not by this user"):
        ps.ensure_private_file(FOREIGN_FILE)
    with pytest.raises(PrivateStoreUnsafeError, match="not this user's own regular file"):
        ps.require_own_replaceable(FOREIGN_FILE)
    assert fingerprint(FOREIGN_FILE) == before


# ---------------------------------------------------------------------------- a symlinked private path is refused, and no chmod follows the link

def test_a_symlinked_kriya_home_is_refused_and_the_link_target_is_never_chmodded(home, repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chmod(elsewhere, 0o755)
    (home / ".kriya").symlink_to(elsewhere)                                # a dotfile-manager style link: the default layout lives behind it
    with pytest.raises(PrivateStoreUnsafeError, match="not a real directory"):
        control_store.ensure_private_roots(str(repo), AppConfig())
    assert stat.S_IMODE(os.lstat(elsewhere).st_mode) == 0o755, "a chmod must never follow the link"
    assert os.path.islink(home / ".kriya")
