"""SEC-001 foundation: ProcessController as the common execution boundary -
sync/async equivalence, containment composition, and fail-closed semantics
for both lifecycles. Real subprocess execution throughout (no mocked
subprocess primitives) - these prove the actual OS-level behavior, not
just that the right Python calls were made."""
import asyncio

import pytest

from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    ContainmentSetupError,
    NetworkAuthority,
    NullContainmentBackend,
    DummyContainmentBackend,
    TrustClass,
)
from kriya.tools.process import ProcessController


# --- happy path: containment-aware calls behave identically to today's
# raw env/preexec_fn calls (existing behavior preserved). network=UNRESTRICTED
# throughout this block - ShellTool's own real profile shape (the foundation
# gate's "not requiring OS isolation" case) - since NullContainmentBackend
# now refuses anything stricter (SEC-001 foundation gate, 2026-09-11). ---

def test_run_with_null_backend_profile_matches_plain_run():
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".",
        network=NetworkAuthority.UNRESTRICTED,
    )
    res = controller.run(
        ["echo", "hello"], cwd=".", timeout=5,
        containment_profile=profile, containment_backend=NullContainmentBackend(),
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "hello"


@pytest.mark.asyncio
async def test_run_async_with_null_backend_profile_matches_plain_run():
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".",
        network=NetworkAuthority.UNRESTRICTED,
    )
    res = await controller.run_async(
        ["echo", "hello"], cwd=".", timeout=5,
        containment_profile=profile, containment_backend=NullContainmentBackend(),
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "hello"


def test_run_env_allowlist_via_profile_matches_direct_env(monkeypatch):
    monkeypatch.setenv("KRIYA_TEST_SECRET_PC", "super-secret")
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".", env_allowlist=["PATH"],
        network=NetworkAuthority.UNRESTRICTED,
    )
    res = controller.run(
        ["/bin/sh", "-c", "echo $KRIYA_TEST_SECRET_PC"], cwd=".", timeout=5,
        containment_profile=profile, containment_backend=NullContainmentBackend(),
    )
    assert res.stdout.strip() == ""


# --- fail-closed: backend unavailable/misconfigured blocks execution,
# never silently runs uncontained (both sync and async) ---

def test_run_backend_unavailable_blocks_execution_sync(tmp_path):
    sentinel = tmp_path / "should_not_exist.txt"
    controller = ProcessController()
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".")
    failing_backend = DummyContainmentBackend(should_fail=True)
    with pytest.raises(BackendUnavailableError):
        controller.run(
            ["touch", str(sentinel)], cwd=".", timeout=5,
            containment_profile=profile, containment_backend=failing_backend,
        )
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_run_async_backend_unavailable_blocks_execution(tmp_path):
    sentinel = tmp_path / "should_not_exist_async.txt"
    controller = ProcessController()
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".")
    failing_backend = DummyContainmentBackend(should_fail=True)
    with pytest.raises(BackendUnavailableError):
        await controller.run_async(
            ["touch", str(sentinel)], cwd=".", timeout=5,
            containment_profile=profile, containment_backend=failing_backend,
        )
    assert not sentinel.exists()


def test_run_null_backend_blocks_network_restricted_profile_end_to_end(tmp_path):
    """Foundation gate: proving the NullContainmentBackend network-authority
    check (kriya/tools/containment.py) actually blocks the real command at
    the ProcessController boundary, not just at the backend's own unit
    level - a profile requiring real network isolation must never reach
    subprocess.Popen under the null backend."""
    from kriya.tools.containment import NetworkAuthority, NullContainmentBackend

    sentinel = tmp_path / "should_not_exist_network_gate.txt"
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".",
        network=NetworkAuthority.DENIED,
    )
    with pytest.raises(BackendUnavailableError):
        controller.run(
            ["touch", str(sentinel)], cwd=".", timeout=5,
            containment_profile=profile, containment_backend=NullContainmentBackend(),
        )
    assert not sentinel.exists()


def test_run_resource_setup_failure_blocks_execution(tmp_path):
    """A preexec_fn that genuinely fails to apply a resource limit must
    block the command from starting at all - the SEC-001 fail-closed
    correction, exercised through the real subprocess.Popen path (not
    mocked)."""
    sentinel = tmp_path / "should_not_exist_rlimit.txt"

    def broken_preexec() -> None:
        raise RuntimeError("resource limit deliberately broken for this test")

    controller = ProcessController()
    with pytest.raises(ContainmentSetupError):
        controller.run(
            ["touch", str(sentinel)], cwd=".", timeout=5, preexec_fn=broken_preexec,
        )
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_run_async_resource_setup_failure_blocks_execution(tmp_path):
    sentinel = tmp_path / "should_not_exist_rlimit_async.txt"

    def broken_preexec() -> None:
        raise RuntimeError("resource limit deliberately broken for this test")

    controller = ProcessController()
    with pytest.raises(ContainmentSetupError):
        await controller.run_async(
            ["touch", str(sentinel)], cwd=".", timeout=5, preexec_fn=broken_preexec,
        )
    assert not sentinel.exists()


def test_containment_profile_without_backend_raises_value_error():
    controller = ProcessController()
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".")
    with pytest.raises(ValueError):
        controller.run(["echo", "x"], cwd=".", timeout=5, containment_profile=profile)


def test_mixing_raw_env_and_containment_profile_raises_value_error():
    controller = ProcessController()
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=".")
    with pytest.raises(ValueError):
        controller.run(
            ["echo", "x"], cwd=".", timeout=5, env={"PATH": "/usr/bin"},
            containment_profile=profile, containment_backend=NullContainmentBackend(),
        )


# --- sync/async equivalence: timeout + process-tree termination, no
# duplicated security semantics (Invariant 14) ---

def test_run_sync_timeout_kills_process_tree():
    controller = ProcessController(reap_timeout=3)
    res = controller.run(["sleep", "30"], cwd=".", timeout=1)
    assert res.timeout is True
    assert res.returncode == -1


@pytest.mark.asyncio
async def test_run_async_timeout_kills_process_tree():
    controller = ProcessController(reap_timeout=3)
    res = await controller.run_async(["sleep", "30"], cwd=".", timeout=1)
    assert res.timeout is True
    assert res.returncode == -1


@pytest.mark.asyncio
async def test_run_async_timeout_leaves_no_surviving_process():
    """Independent, non-self-reported confirmation - checks real OS process
    state after termination via `pgrep`, not just that the call returned
    (same discipline Demo 04 already established for AuthorizedFileWriter).
    A distinctive sleep duration (37s - never used by any other test in
    this suite) keeps this check specific without relying on shell argv
    surviving exec() (confirmed empirically it does not: `sh -c "sleep N
    # marker"` execs directly into `sleep N`, dropping the trailing
    comment from the process's own argv)."""
    controller = ProcessController(reap_timeout=3)

    await controller.run_async(["sleep", "37"], cwd=".", timeout=1)
    await asyncio.sleep(0.3)

    check = await asyncio.create_subprocess_exec(
        "pgrep", "-f", "sleep 37", stdout=asyncio.subprocess.PIPE,
    )
    out, _ = await check.communicate()
    remaining_pids = [p for p in out.decode().split() if p.strip()]
    assert remaining_pids == []
