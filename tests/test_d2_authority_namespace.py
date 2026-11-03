"""D2 EXEC-LAYER S1: the authority namespace of a mounted workspace (the structural ``.kriya`` anchor and the protected leaves).

MEASURED on real Docker (2026-10-24, before this slice): with the workspace mounted read-write a contained workload could rename ``.kriya`` aside and
put a forged ``.kriya/control`` at the original host-visible name, and could create a missing ``.kriya/control`` itself. A mask protects its leaf's
CONTENTS only; the owner-chosen anchor is ``<workspace>/.kriya`` bound READ-WRITE onto itself (a mount point cannot be renamed, removed or replaced from
inside the container) with an empty read-only mask over each protected leaf. The namespace is created on the HOST by the OCI backend before the
container starts, so no consumer can omit it, and nothing is created when the backend is ``none`` (the unchanged default).

A. host-side creation (``kriya.policy.authority_namespace``); B. the backend's arguments and refusals (no Docker); C. defaults unchanged;
D. the same attacks against the REAL OCI runtime through every workspace-mount path, each with a negative control that removes the protection.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
from _plugin_test_support import load_core_tools_module

from kriya.config import AppConfig
from kriya.config.config import AutonomyConfig
from kriya.platform.host_identity import HostIdentity
from kriya.platform.services import platform_services
from kriya.policy import authority_namespace
from kriya.policy.authority_namespace import AuthorityNamespaceUnsafeError, create_workspace_authority_namespace
from kriya.tools import containment_oci
from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    NetworkAuthority,
    NullContainmentBackend,
    TrustClass,
)
from kriya.tools.containment_oci import (
    OCIContainmentBackend,
    authority_state_mask_args,
    ensure_authority_namespace,
    workspace_anchor_args,
)
from kriya.tools.process import ProcessController
from kriya.tools.validate import PolymorphicValidator

ShellTool = load_core_tools_module().ShellTool
LEAVES = (".kriya/control", ".kriya/checkpoints")


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


# ------------------------------------------------------------------ A. host-side creation

@pytest.mark.parametrize("umask", [0o000, 0o022, 0o277])
def test_a_fresh_workspace_gets_the_anchor_and_both_leaves_with_exactly_0700_whatever_the_umask(tmp_path, umask):
    """``mkdir`` can only LOSE bits to the umask, so the mode is pinned with ``chmod`` afterwards; 0o277 would leave a directory unwritable."""
    previous = os.umask(umask)
    try:
        anchor = create_workspace_authority_namespace(str(tmp_path))
    finally:
        os.umask(previous)
    assert anchor == str(tmp_path / ".kriya")
    for relative in (".kriya", *LEAVES):
        assert _mode(tmp_path / relative) == 0o700, relative


def test_an_existing_namespace_is_verified_never_chmodded_and_its_content_is_left_alone(tmp_path):
    (tmp_path / ".kriya" / "control").mkdir(parents=True)
    (tmp_path / ".kriya" / "control" / "record.json").write_text("REAL")
    os.chmod(tmp_path / ".kriya", 0o755)
    os.chmod(tmp_path / ".kriya" / "control", 0o750)
    create_workspace_authority_namespace(str(tmp_path))
    create_workspace_authority_namespace(str(tmp_path))  # idempotent
    assert _mode(tmp_path / ".kriya") == 0o755 and _mode(tmp_path / ".kriya" / "control") == 0o750
    assert (tmp_path / ".kriya" / "control" / "record.json").read_text() == "REAL"
    assert os.listdir(tmp_path / ".kriya" / "checkpoints") == []  # created, and nothing is written into it


@pytest.mark.parametrize("shape", ["symlink", "file"])
@pytest.mark.parametrize("relative", [".kriya", ".kriya/control", ".kriya/checkpoints"])
def test_a_symlink_or_file_in_the_namespace_is_refused_never_followed(tmp_path, relative, shape):
    target = tmp_path / "outside"
    target.mkdir()
    victim = tmp_path / relative
    victim.parent.mkdir(parents=True, exist_ok=True)
    if shape == "symlink":
        victim.symlink_to(target)
    else:
        victim.write_text("not a directory")
    with pytest.raises(AuthorityNamespaceUnsafeError, match="not a real directory"):
        create_workspace_authority_namespace(str(tmp_path))
    assert os.listdir(target) == []  # the symlink target was never touched


def test_a_directory_owned_by_another_user_is_refused(tmp_path, monkeypatch):
    (tmp_path / ".kriya").mkdir()
    real = platform_services().host_identity
    monkeypatch.setattr(real, "current", lambda: HostIdentity(uid=os.getuid() + 1, gid=os.getgid()), raising=False)
    with pytest.raises(AuthorityNamespaceUnsafeError, match="owned by uid"):
        create_workspace_authority_namespace(str(tmp_path))


def test_a_workspace_that_does_not_exist_is_refused_not_created(tmp_path):
    with pytest.raises(AuthorityNamespaceUnsafeError, match="cannot create"):
        create_workspace_authority_namespace(str(tmp_path / "missing"))
    assert not (tmp_path / "missing").exists()


def test_the_creation_helper_uses_the_platform_identity_port_not_an_os_call():
    source = Path(authority_namespace.__file__).read_text(encoding="utf-8")
    assert "geteuid" not in source and "getuid" not in source


# ------------------------------------------------------------------ B. the backend's arguments and refusals (no Docker)

def _mounts(args):
    """The ``--mount type=bind,src=...,dst=...[,readonly]`` arguments, parsed, in order."""
    parsed = []
    for i, token in enumerate(args):
        if token == "--mount" and i + 1 < len(args):
            fields = args[i + 1].split(",")
            parsed.append({"src": next(f[4:] for f in fields if f.startswith("src=")), "dst": next(f[4:] for f in fields if f.startswith("dst=")),
                           "readonly": "readonly" in fields})
    return parsed


def test_a_writable_workspace_gets_the_anchor_first_then_a_read_only_mask_over_each_leaf(tmp_path):
    create_workspace_authority_namespace(str(tmp_path))
    args, cleanup = authority_state_mask_args(str(tmp_path), workspace_writable=True)
    mounts = _mounts(args)
    assert len(mounts) == 3
    anchor = mounts[0]
    assert anchor["dst"] == "/kriya/workspace/.kriya" and os.path.samefile(anchor["src"], tmp_path / ".kriya") and not anchor["readonly"]
    assert [m["dst"] for m in mounts[1:]] == [f"/kriya/workspace/{leaf}" for leaf in LEAVES] and all(m["readonly"] for m in mounts[1:])
    assert cleanup is not None
    scratch = [m["src"] for m in mounts[1:]]
    assert all(os.path.isdir(path) and os.listdir(path) == [] for path in scratch)  # an EMPTY scratch directory per mask
    assert len(set(scratch)) == 2  # each mask has its own
    cleanup()
    assert not any(os.path.exists(path) for path in scratch)


def test_a_read_only_workspace_gets_no_anchor_and_a_mask_only_over_a_leaf_that_exists(tmp_path):
    (tmp_path / ".kriya" / "control").mkdir(parents=True)
    args, cleanup = authority_state_mask_args(str(tmp_path), workspace_writable=False)
    mounts = _mounts(args)
    assert [m["dst"] for m in mounts] == ["/kriya/workspace/.kriya/control"] and mounts[0]["readonly"]
    assert cleanup is not None
    cleanup()
    nothing, no_cleanup = authority_state_mask_args(str(tmp_path / "elsewhere"), workspace_writable=False)
    assert nothing == [] and no_cleanup is None


def test_ensure_authority_namespace_creates_for_a_writable_mount_and_nothing_for_a_read_only_one(tmp_path):
    ensure_authority_namespace(str(tmp_path / "ro"), workspace_writable=False)  # a read-only mount creates nothing (the path need not exist)
    assert not (tmp_path / "ro").exists()
    ensure_authority_namespace(str(tmp_path), workspace_writable=True)
    assert sorted(os.listdir(tmp_path / ".kriya")) == ["checkpoints", "control"]


@pytest.mark.parametrize("shape", ["symlink", "file"])
def test_a_hostile_namespace_is_a_typed_backend_refusal_before_anything_starts(tmp_path, shape):
    (tmp_path / "elsewhere").mkdir()
    if shape == "symlink":
        (tmp_path / ".kriya").symlink_to(tmp_path / "elsewhere")
    else:
        (tmp_path / ".kriya").write_text("x")
    with pytest.raises(BackendUnavailableError, match="AUTHORITY_NAMESPACE_UNAVAILABLE"):
        ensure_authority_namespace(str(tmp_path), workspace_writable=True)


@pytest.mark.parametrize("missing", ["anchor", "leaf"])
def test_the_anchor_and_masks_re_verify_at_use_and_never_skip_a_missing_directory(tmp_path, missing):
    """Defence in depth against a swap between creation and mounting: building the arguments for a writable mount refuses a missing anchor or leaf."""
    (tmp_path / ".kriya" / "control").mkdir(parents=True)
    if missing == "anchor":
        shutil.rmtree(tmp_path / ".kriya")
        with pytest.raises(BackendUnavailableError, match="AUTHORITY_ANCHOR_UNAVAILABLE"):
            workspace_anchor_args(str(tmp_path))
    else:  # .kriya and control exist, checkpoints does not
        with pytest.raises(BackendUnavailableError, match="AUTHORITY_LEAF_UNAVAILABLE"):
            authority_state_mask_args(str(tmp_path), workspace_writable=True)


def _mask_scratch_dirs() -> set:
    return {path.name for path in Path(tempfile.gettempdir()).glob("kriya-authority-mask-*")}


def test_a_refusal_after_the_first_leaf_leaks_no_scratch_directory(tmp_path):
    """The masks' scratch directories are created only after EVERY protected directory has been verified: a refusal on the second leaf must not
    leave the first leaf's scratch directory behind (a defect of my own first draft, found while planning the mutants)."""
    (tmp_path / ".kriya" / "control").mkdir(parents=True)  # .kriya and control exist, checkpoints does not
    before = _mask_scratch_dirs()
    with pytest.raises(BackendUnavailableError, match="AUTHORITY_LEAF_UNAVAILABLE"):
        authority_state_mask_args(str(tmp_path), workspace_writable=True)
    assert _mask_scratch_dirs() == before


# ------------------------------------------------------------------ C. defaults unchanged

def test_the_null_backend_creates_nothing_in_the_workspace(tmp_path):
    profile = ContainmentProfile(trust_class=TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE, workspace_path=str(tmp_path))
    NullContainmentBackend().prepare(profile, ["true"])
    assert os.listdir(tmp_path) == []


# ------------------------------------------------------------------ D. the real OCI runtime


def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:  # pylint: disable=broad-except
        return False


NEEDS_DOCKER = pytest.mark.skipif(not _docker_reachable(), reason="docker CLI/daemon not available")

_ATTACK = r'''
import json, os, ctypes
K = "/kriya/workspace/.kriya"; WS = "/kriya/workspace"
libc = ctypes.CDLL(None, use_errno=True)
out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "SUCCEEDED"
    except BaseException as error:
        out[name] = type(error).__name__ + ":" + str(getattr(error, "errno", ""))
attempt("rename", lambda: os.rename(K, WS + "/.kriya.moved"))
attempt("rmdir", lambda: os.rmdir(K))
def replace():
    os.makedirs(WS + "/decoy/control", exist_ok=True)
    open(WS + "/decoy/control/secret.txt", "w").write("FORGED")
    os.rename(WS + "/decoy", K)
attempt("replace_with_directory", replace)
def umount():
    if libc.umount2(K.encode(), 2) != 0:
        raise OSError(ctypes.get_errno(), "umount")
attempt("umount", umount)
def ordinary():
    os.makedirs(K + "/test-reports", exist_ok=True)
    open(K + "/test-reports/report.xml", "w").write("ordinary")
attempt("ordinary_write", ordinary)
for leaf in ("control", "checkpoints"):
    def forge(leaf=leaf):
        os.makedirs(K + "/" + leaf, exist_ok=True)
        open(K + "/" + leaf + "/forged.json", "w").write("FORGED")
    attempt("forge_" + leaf, forge)
    attempt("rename_" + leaf, lambda leaf=leaf: os.rename(K + "/" + leaf, K + "/" + leaf + ".moved"))
    attempt("rmdir_" + leaf, lambda leaf=leaf: os.rmdir(K + "/" + leaf))
print(json.dumps(out))
'''
_OCI_CFG = dict(containment_backend="oci", contained_execution_required=True)


def _write_attack(workspace) -> None:
    (workspace / "attack.py").write_text(_ATTACK, encoding="utf-8")


def _report(stdout: str) -> dict:
    return json.loads(stdout.strip().splitlines()[-1])


def _assert_workspace_untouched(ws, *, real_leaf_content=True) -> None:
    # (the attack's own ordinary ``decoy/`` files are legitimate workspace writes; what must not exist is a moved or forged authority directory)
    assert not (ws / ".kriya.moved").exists()
    assert not (ws / ".kriya" / "control" / "secret.txt").exists() or (ws / ".kriya" / "control" / "secret.txt").read_text() == "REAL-CONTROL"
    for leaf in ("control", "checkpoints"):
        assert not (ws / ".kriya" / leaf / "forged.json").exists(), leaf
        assert not (ws / ".kriya" / f"{leaf}.moved").exists(), leaf
    if real_leaf_content:
        assert (ws / ".kriya" / "control" / "secret.txt").read_text() == "REAL-CONTROL"
        assert (ws / ".kriya" / "checkpoints" / "cp.json").read_text() == "REAL-CHECKPOINT"


def _existing_workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".kriya" / "control").mkdir(parents=True)
    (ws / ".kriya" / "checkpoints").mkdir()
    (ws / ".kriya" / "control" / "secret.txt").write_text("REAL-CONTROL")
    (ws / ".kriya" / "checkpoints" / "cp.json").write_text("REAL-CHECKPOINT")
    _write_attack(ws)
    return ws


def _validator_run(ws, command, *, network_profile=False):
    """The validator's production path: its profile builder -> OCIContainmentBackend.prepare -> a real run."""
    validator = PolymorphicValidator(str(ws), autonomy_cfg=AutonomyConfig(**_OCI_CFG))
    profile, backend = validator.build_containment_profile_and_backend(
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY if network_profile else NetworkAuthority.DENIED)
    result = ProcessController().run(command, cwd=str(ws), timeout=180, containment_profile=profile, containment_backend=backend)
    assert result.returncode == 0, result.stderr
    return _report(result.stdout)


def _assert_protected(report) -> None:
    for attempt in ("rename", "rmdir", "replace_with_directory"):
        assert report[attempt] == "OSError:16", (attempt, report)  # EBUSY: the anchor is a mount point
    assert report["umount"] != "SUCCEEDED", report
    for leaf in ("control", "checkpoints"):
        assert report[f"forge_{leaf}"] != "SUCCEEDED", report  # the read-only mask over the leaf
        assert report[f"rename_{leaf}"] == "OSError:16" and report[f"rmdir_{leaf}"] == "OSError:16", report  # a mount point: EBUSY
    assert report["ordinary_write"] == "SUCCEEDED", report


@NEEDS_DOCKER
def test_real_oci_the_anchor_and_masks_block_every_attack_and_ordinary_writes_still_land(tmp_path):
    ws = _existing_workspace(tmp_path)
    _assert_protected(_validator_run(ws, ["python3", "attack.py"]))
    assert (ws / ".kriya" / "test-reports" / "report.xml").read_text() == "ordinary"  # an ordinary write beneath the anchor reached the host
    _assert_workspace_untouched(ws)


@NEEDS_DOCKER
def test_real_oci_a_child_and_a_grandchild_have_the_same_boundary(tmp_path):
    ws = _existing_workspace(tmp_path)
    nested = "import subprocess; r = subprocess.run(['sh', '-c', 'python3 attack.py'], capture_output=True, text=True); print(r.stdout)"
    _assert_protected(_validator_run(ws, ["python3", "-c", nested]))  # python -> sh -> python
    _assert_workspace_untouched(ws)


@NEEDS_DOCKER
def test_real_oci_negative_control_without_the_anchor_the_original_reproducer_forges_host_authority(tmp_path, monkeypatch, acl_protected):
    """The pre-S1 mount shape for the structure (leaf masks only): the workload renames ``.kriya`` aside and recreates a forged control directory at the
    original host-visible name. If this stops reproducing, the positive test above no longer proves the anchor does anything."""
    ws = _existing_workspace(tmp_path)
    acl_protected(ws / ".kriya.moved")  # the attack renames .kriya aside; Docker Desktop leaves deny-delete ACLs on the moved mask targets
    monkeypatch.setattr(containment_oci, "workspace_anchor_args", lambda workspace_host: [])
    report = _validator_run(ws, ["python3", "attack.py"])
    assert report["rename"] == "SUCCEEDED" and (ws / ".kriya.moved").is_dir()
    assert report["replace_with_directory"] == "SUCCEEDED"
    # a forged control directory now sits at the original host-visible name (the attack then goes on to rename it aside, so match where it ended up)
    forged = list((ws / ".kriya").glob("control*/secret.txt"))
    assert forged and forged[0].read_text() == "FORGED", sorted(str(path) for path in (ws / ".kriya").rglob("*"))


@NEEDS_DOCKER
def test_real_oci_on_a_fresh_workspace_the_backend_creates_the_namespace_and_the_workload_cannot_forge_replace_or_remove_it(tmp_path):
    ws = tmp_path / "fresh"
    ws.mkdir()
    _write_attack(ws)
    assert not (ws / ".kriya").exists()  # nothing of Kriya's exists: the host side must create every protected directory
    report = _validator_run(ws, ["python3", "attack.py"])
    assert sorted(os.listdir(ws / ".kriya")) == ["checkpoints", "control", "test-reports"]  # created on the host before any container existed
    _assert_protected(report)
    for leaf in ("control", "checkpoints"):
        assert os.listdir(ws / ".kriya" / leaf) == [], leaf  # nothing reached the host
    _assert_workspace_untouched(ws, real_leaf_content=False)


@NEEDS_DOCKER
def test_real_oci_negative_control_without_the_namespace_protection_the_workload_creates_and_forges_it(tmp_path, monkeypatch):
    """The pre-S1 shape on a fresh workspace (nothing created, no anchor, no masks): the workload creates ``.kriya/control`` itself and the forged file
    is on the HOST afterwards."""
    ws = tmp_path / "fresh"
    ws.mkdir()
    _write_attack(ws)
    monkeypatch.setattr(containment_oci, "ensure_authority_namespace", lambda *a, **k: None)
    monkeypatch.setattr(containment_oci, "authority_state_mask_args", lambda *a, **k: ([], None))
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(ws), network=NetworkAuthority.DENIED)
    result = ProcessController().run(["python3", "attack.py"], cwd=str(ws), timeout=180, containment_profile=profile,
                                     containment_backend=OCIContainmentBackend())
    assert result.returncode == 0, result.stderr
    assert _report(result.stdout)["forge_control"] == "SUCCEEDED"
    assert list((ws / ".kriya").glob("control*/forged.json")), sorted(str(p) for p in (ws / ".kriya").rglob("*"))


@NEEDS_DOCKER
@pytest.mark.asyncio
async def test_real_oci_the_shell_tool_the_first_attempt_missed_is_protected_end_to_end(tmp_path, monkeypatch):
    """ShellTool builds its own profile from the operator's cwd. With containment required it must get the same protection with no caller change."""
    ws = tmp_path / "shell"
    ws.mkdir()
    _write_attack(ws)
    monkeypatch.chdir(ws)
    cfg = AppConfig()
    cfg.autonomy.containment_backend = "oci"
    cfg.autonomy.contained_execution_required = True
    result = await ShellTool(autonomy_cfg=cfg.autonomy).execute(command="python3 attack.py")
    _assert_protected(_report(result["stdout"]))
    assert sorted(os.listdir(ws / ".kriya")) == ["checkpoints", "control", "test-reports"]
    for leaf in ("control", "checkpoints"):
        assert os.listdir(ws / ".kriya" / leaf) == [], leaf
    _assert_workspace_untouched(ws, real_leaf_content=False)


@NEEDS_DOCKER
def test_real_oci_the_registry_scoped_mount_path_has_the_same_protection(tmp_path):
    """The second workspace-binding function (dependency acquisition through the registry proxy) always mounts the workspace writable."""
    ws = _existing_workspace(tmp_path)
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(ws),
                                 network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, network_destinations=("repo.maven.apache.org",))
    result = ProcessController().run(["python3", "attack.py"], cwd=str(ws), timeout=240, containment_profile=profile,
                                     containment_backend=OCIContainmentBackend())
    assert result.returncode == 0, result.stderr
    _assert_protected(_report(result.stdout))
    _assert_workspace_untouched(ws)


@NEEDS_DOCKER
def test_real_oci_a_read_only_workspace_hides_the_leaf_contents(tmp_path):
    ws = _existing_workspace(tmp_path)
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(ws), network=NetworkAuthority.DENIED,
                                 workspace_write=False)
    script = "import os; print(sorted(os.listdir('/kriya/workspace/.kriya/control')), sorted(os.listdir('/kriya/workspace/.kriya')))"
    result = ProcessController().run(["python3", "-c", script], cwd=str(ws), timeout=120, containment_profile=profile,
                                     containment_backend=OCIContainmentBackend())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[] ['checkpoints', 'control']"  # the real leaf content (secret.txt) is hidden; nothing was created
    assert sorted(os.listdir(ws / ".kriya")) == ["checkpoints", "control"]


@NEEDS_DOCKER
def test_real_oci_the_mask_scratch_directories_are_removed_after_the_run(tmp_path):
    ws = _existing_workspace(tmp_path)
    before = _mask_scratch_dirs()
    validator = PolymorphicValidator(str(ws), autonomy_cfg=AutonomyConfig(**_OCI_CFG))
    profile, backend = validator.build_containment_profile_and_backend()
    result = ProcessController().run(["python3", "-c", "print('ok')"], cwd=str(ws), timeout=120, containment_profile=profile, containment_backend=backend)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr
    assert _mask_scratch_dirs() == before  # removed by the same teardown that removes the container


@NEEDS_DOCKER
def test_real_oci_the_registry_scoped_path_also_removes_its_mask_scratch_directories(tmp_path):
    """The second workspace-binding function has its own teardown (it also removes the proxy and the network): the mask scratch must go with it."""
    ws = _existing_workspace(tmp_path)
    before = _mask_scratch_dirs()
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(ws),
                                 network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, network_destinations=("repo.maven.apache.org",))
    result = ProcessController().run(["python3", "-c", "print('ok')"], cwd=str(ws), timeout=240, containment_profile=profile,
                                     containment_backend=OCIContainmentBackend())
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr
    assert _mask_scratch_dirs() == before
