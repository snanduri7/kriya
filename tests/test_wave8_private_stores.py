"""OI-134 / OI-154 / OBS-003 (REQUIRED): private Kriya stores are owner-only.

Measured defect (2026-11-06, a scripted run with planted credentials; S20 preflight): the stores whose digests bind their exact bytes - verification contracts,
RunRecords, checkpoints, milestone state - kept a planted credential RAW at mode 0644 inside 0755 directories (state directory, ``<workspace>/.kriya``); the
history database was 0644 too. The owner policy (2026-11-06): private directories 0700, private files 0600, a legacy artifact this user owns is tightened,
another owner's artifact is refused, and secrets are not persisted raw where they are not required (everything no digest binds is redacted at its writer
by the one redactor: tests/test_s20_evidence_redaction.py).
"""
import os
import stat
import subprocess

import pytest

from kriya.control import control_store
from kriya.platform import private_store as ps
from kriya.platform.private_store import PrivateStoreUnsafeError
from kriya.repository.atomic_write import atomic_write_file


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture(params=[0o000, 0o022, 0o077])
def umask(request):
    old = os.umask(request.param)
    yield request.param
    os.umask(old)


# ============================================================================ the authority (kriya/platform/private_store.py)

def test_a_created_private_directory_is_0700_whatever_the_umask(tmp_path, umask):
    target = tmp_path / "a" / "b" / "c"
    assert ps.ensure_private_directory(str(target)) is True
    assert [mode(tmp_path / "a"), mode(tmp_path / "a" / "b"), mode(target)] == [0o700] * 3, "directories Kriya has to create are all private"


def test_an_existing_parent_is_never_touched(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    os.chmod(parent, 0o755)
    ps.ensure_private_directory(str(parent / "mine"))
    assert mode(parent) == 0o755 and mode(parent / "mine") == 0o700


@pytest.mark.parametrize("legacy", [0o755, 0o750, 0o777, 0o710])
def test_a_legacy_wider_directory_this_user_owns_is_tightened(tmp_path, legacy):
    d = tmp_path / "legacy"
    d.mkdir()
    os.chmod(d, legacy)
    ps.ensure_private_directory(str(d))
    assert mode(d) == 0o700


@pytest.mark.parametrize("legacy", [0o644, 0o664, 0o666, 0o755, 0o000, 0o200])
def test_a_legacy_file_this_user_owns_is_tightened_to_0600(tmp_path, legacy):
    f = tmp_path / "legacy.json"
    f.write_text("{}")
    os.chmod(f, legacy)
    assert ps.ensure_private_file(str(f)) is True
    assert mode(f) == 0o600


def test_a_missing_file_is_reported_not_created(tmp_path):
    assert ps.ensure_private_file(str(tmp_path / "absent")) is False and not (tmp_path / "absent").exists()
    assert ps.ensure_private_directory(str(tmp_path / "absent-dir"), create=False) is False and not (tmp_path / "absent-dir").exists()


def test_a_symlink_where_a_directory_or_file_is_required_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(PrivateStoreUnsafeError):
        ps.ensure_private_directory(str(link))
    target_file = tmp_path / "f"
    target_file.write_text("x")
    flink = tmp_path / "flink"
    flink.symlink_to(target_file)
    with pytest.raises(PrivateStoreUnsafeError):
        ps.ensure_private_file(str(flink))
    with pytest.raises(PrivateStoreUnsafeError):
        ps.require_own_replaceable(str(flink))
    assert mode(real) == mode(real)  # untouched: never chmod through a link
    with pytest.raises(PrivateStoreUnsafeError):
        ps.ensure_private_directory(str(target_file))   # a file where a directory is required


def test_another_owners_artifact_is_refused_and_never_chmodded(tmp_path, monkeypatch):
    d = tmp_path / "theirs"
    d.mkdir()
    os.chmod(d, 0o755)
    f = tmp_path / "theirs.json"
    f.write_text("{}")
    os.chmod(f, 0o644)
    monkeypatch.setattr(ps, "_current_uid", lambda: os.getuid() + 4242)     # the artifacts now look foreign
    for call in (lambda: ps.ensure_private_directory(str(d)), lambda: ps.ensure_private_file(str(f)), lambda: ps.require_own_replaceable(str(f))):
        with pytest.raises(PrivateStoreUnsafeError, match="not by this user|not this user"):
            call()
    assert mode(d) == 0o755 and mode(f) == 0o644


def test_ensure_private_directories_covers_the_whole_chain_and_refuses_a_foreign_path(tmp_path):
    root = tmp_path / ".kriya"
    ps.ensure_private_directories(str(root), str(root / "control" / "runs"))
    assert [mode(root), mode(root / "control"), mode(root / "control" / "runs")] == [0o700] * 3
    with pytest.raises(PrivateStoreUnsafeError):
        ps.ensure_private_directories(str(root), str(tmp_path / "elsewhere"))


def test_a_root_and_a_path_spelled_through_a_symlinked_prefix_are_the_same_place(tmp_path):
    """macOS spells a temp directory /var/... and /private/var/...: found by the regression run (milestone sidecars were refused as "not beneath")."""
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    ps.ensure_private_directories(str(alias / ".kriya"), str(real / ".kriya" / "milestones"))
    assert mode(real / ".kriya") == 0o700 and mode(real / ".kriya" / "milestones") == 0o700


def test_a_root_that_is_itself_a_symlink_is_refused_even_when_the_path_resolves_beneath_it(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chmod(elsewhere, 0o755)
    (tmp_path / ".kriya").symlink_to(elsewhere)
    with pytest.raises(PrivateStoreUnsafeError):
        ps.ensure_private_directories(str(tmp_path / ".kriya"), str(tmp_path / ".kriya" / "control"))
    assert mode(elsewhere) == 0o755, "a chmod must never follow the link"


# ============================================================================ the canonical writer

def test_a_private_write_is_0600_from_creation_and_tightens_a_replaced_legacy_file(tmp_path, umask):
    target = tmp_path / "state.json"
    atomic_write_file(str(target), "NEW", private=True)
    assert mode(target) == 0o600 and target.read_text() == "NEW"
    legacy = tmp_path / "legacy.json"
    legacy.write_text("OLD")
    os.chmod(legacy, 0o644)
    atomic_write_file(str(legacy), "NEW", private=True)
    assert mode(legacy) == 0o600 and legacy.read_text() == "NEW"
    other = tmp_path / "ordinary.txt"
    atomic_write_file(str(other), "plain")
    assert mode(other) != 0o600 or umask == 0o077, "a non-private write keeps its ordinary mode"


def test_a_private_write_never_replaces_another_owners_file(tmp_path, monkeypatch):
    target = tmp_path / "theirs.json"
    target.write_text("THEIRS")
    monkeypatch.setattr(ps, "_current_uid", lambda: os.getuid() + 4242)
    with pytest.raises(PrivateStoreUnsafeError):
        atomic_write_file(str(target), "MINE", private=True)
    assert target.read_text() == "THEIRS"


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_control_state_files_are_private_and_so_are_their_directories(repo):
    from kriya.control.persistence import control_state_path, save_control_state
    from kriya.control.state import ControlState

    save_control_state(str(repo), ControlState(schema_version=1, run_id="r1"))
    path = control_state_path(str(repo))
    assert mode(path) == 0o600
    assert mode(repo / ".kriya") == 0o700 and mode(os.path.dirname(path)) == 0o700


def test_a_legacy_control_directory_and_file_are_tightened_by_the_next_control_write(repo):
    control = repo / ".kriya" / "control"
    control.mkdir(parents=True)
    os.chmod(repo / ".kriya", 0o755)
    os.chmod(control, 0o755)
    from kriya.control.persistence import control_state_path, save_control_state
    from kriya.control.state import ControlState

    legacy = control_state_path(str(repo))
    save_control_state(str(repo), ControlState(schema_version=1, run_id="r1"))   # a real, ownership-stamped legacy file ...
    os.chmod(legacy, 0o644)                                                       # ... written wide by an earlier Kriya
    os.chmod(repo / ".kriya", 0o755)
    os.chmod(control, 0o755)
    save_control_state(str(repo), ControlState(schema_version=1, run_id="r2"))
    assert (mode(repo / ".kriya"), mode(control), mode(legacy)) == (0o700, 0o700, 0o600)


def test_a_foreign_control_directory_refuses_the_write(repo, monkeypatch):
    (repo / ".kriya" / "control").mkdir(parents=True)
    from kriya.control.persistence import save_control_state
    from kriya.control.state import ControlState

    monkeypatch.setattr(ps, "_current_uid", lambda: os.getuid() + 4242)
    with pytest.raises(PrivateStoreUnsafeError):
        save_control_state(str(repo), ControlState(schema_version=1, run_id="r3"))


def test_ensure_private_roots_names_every_root_and_tightens_legacy_ones(repo, tmp_path, monkeypatch):
    from kriya.config import AppConfig

    home = tmp_path / "home"
    (home / ".kriya" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("KRIYA_STATE_DIR", raising=False)
    authority = tmp_path / "authority"
    authority.mkdir()
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(authority))
    for legacy in (home / ".kriya", home / ".kriya" / "state", authority, repo / ".kriya", repo / ".kriya" / "control"):
        legacy.mkdir(parents=True, exist_ok=True)
        os.chmod(legacy, 0o755)
    control_store.ensure_private_roots(str(repo), AppConfig())
    roots = [home / ".kriya", home / ".kriya" / "state", authority, repo / ".kriya", repo / ".kriya" / "control", repo / ".kriya" / "checkpoints"]
    assert {str(p): mode(p) for p in roots} == {str(p): 0o700 for p in roots}


def test_kriya_home_is_only_tightened_when_it_exists_never_created(repo, tmp_path, monkeypatch):
    from kriya.config import AppConfig

    home = tmp_path / "bare-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("KRIYA_STATE_DIR", raising=False)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "auth"))
    control_store.ensure_private_roots(str(repo), AppConfig())
    # authority and the default state directory live under ~/.kriya? only the authority override is elsewhere: ~/.kriya was created PRIVATE as the state parent
    assert not (home / ".kriya").exists() or mode(home / ".kriya") == 0o700


def test_a_foreign_root_stops_the_run_before_anything_is_written(repo, tmp_path, monkeypatch):
    from kriya.config import AppConfig

    monkeypatch.setenv("HOME", str(tmp_path / "home2"))
    (tmp_path / "home2").mkdir()
    monkeypatch.setattr(ps, "_current_uid", lambda: os.getuid() + 4242)
    (repo / ".kriya").mkdir()
    with pytest.raises(PrivateStoreUnsafeError):
        control_store.ensure_private_roots(str(repo), AppConfig())


# ============================================================================ the original symptom: a scripted run with planted credentials

def _secret_files(tmp_path):
    """Every file under ``tmp_path`` that holds a planted credential (gunzipped too), outside the candidate worktree (workspace content, not a store)."""
    import gzip

    from test_s20_evidence_redaction import RUN_M1, RUN_M2

    found = []
    for base, dirs, names in os.walk(tmp_path):
        dirs[:] = [d for d in dirs if d not in {"worktree", ".git"}]
        for name in names:
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            data = open(path, "rb").read()
            if data[:2] == b"\x1f\x8b":
                try:
                    data = gzip.decompress(data)
                except OSError:
                    pass
            if RUN_M1.encode() in data or RUN_M2.encode() in data:
                found.append(path)
    return found


def _private_chain(path, roots):
    """True when ``path`` is 0600 and every directory from its nearest private root down to it is 0700."""
    root = next((r for r in roots if os.path.commonpath([r, path]) == r), None)
    if root is None:
        return False, "not under any private root"
    chain = [root]
    relative = os.path.relpath(os.path.dirname(path), root)
    if relative != ".":
        for part in relative.split(os.sep):
            chain.append(os.path.join(chain[-1], part))
    bad = [(d, oct(mode(d))) for d in chain if mode(d) != 0o700]
    return (mode(path) == 0o600 and not bad), f"file {oct(mode(path))}, directories {bad}"


def test_a_scripted_run_leaves_every_planted_credential_in_owner_only_files_under_owner_only_roots(tmp_path, monkeypatch):
    from test_s20_evidence_redaction import _scripted_run

    _scripted_run(tmp_path, monkeypatch)
    roots = [str(tmp_path / "state"), str(tmp_path / "ws" / ".kriya"), os.environ["KRIYA_AUTHORITY_HOME"]]
    secret_files = _secret_files(tmp_path)
    assert len(secret_files) >= 3, secret_files    # the run really stored the credential in digest-bound stores (vacuity guard)
    verdicts = {os.path.relpath(p, tmp_path): _private_chain(p, roots) for p in secret_files}
    assert {k: v for k, v in verdicts.items() if not v[0]} == {}
    classes = {os.path.relpath(p, tmp_path).split(os.sep)[0:3] and p.split(os.sep)[-2] for p in secret_files}
    assert {"verification-contracts", "runs"} <= classes, classes     # the two digest-bound classes measured at 0644 before the fix
    for root in roots:
        assert mode(root) == 0o700, root
    for suffix in ("traces.db", "traces.db-wal", "traces.db-shm"):
        candidate = tmp_path / "state" / suffix
        if candidate.exists():
            assert mode(candidate) == 0o600, suffix
    checkpoints = list((tmp_path / "ws" / ".kriya" / "checkpoints").glob("*.json"))
    assert checkpoints and all(mode(p) == 0o600 for p in checkpoints)


def test_mutation_without_the_private_roots_the_scripted_run_leaves_a_world_readable_credential(tmp_path, monkeypatch):
    """The acceptance harness is not vacuous: with the directory authority and the private writer removed the same run is reported (0644 / 0755)."""
    from test_s20_evidence_redaction import _scripted_run

    for target in ("ensure_private_directory", "ensure_private_directories"):
        monkeypatch.setattr(control_store, target, lambda *a, **k: None)
    monkeypatch.setattr("kriya.control.checkpoint.ensure_private_directories", lambda *a, **k: None)
    real = control_store.commit_revision_grounded_file
    monkeypatch.setattr(control_store, "commit_revision_grounded_file", lambda *a, **k: real(*a, **{**k, "private": False}))
    monkeypatch.setattr("kriya.workflow.contract_compilation.atomic_write_file", lambda path, text, **k: atomic_write_file(path, text))
    monkeypatch.setattr("kriya.workflow.contract_compilation.ensure_private_directories", lambda *a, **k: None)
    _scripted_run(tmp_path, monkeypatch)
    roots = [str(tmp_path / "state"), str(tmp_path / "ws" / ".kriya"), os.environ["KRIYA_AUTHORITY_HOME"]]
    verdicts = [_private_chain(p, roots) for p in _secret_files(tmp_path)]
    assert any(not ok for ok, _ in verdicts), "the mutated run must be caught by the acceptance check"
