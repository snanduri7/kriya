"""CONTAINMENT-PROXY-START-TIMEOUT-ORPHAN-001 (R8-A, owner ruling 2026-11-12 after Scenario-A run 8).

MEASURED in run 8 (Docker Desktop backend log): the registry proxy was created in 127 ms, its start request was HELD 31.1 s, Kriya's 20 s client
call gave up, Kriya removed the network, the held start was then served against the missing network, and the container stayed behind in state
Created. Pre-fix characterization (passes on ff23469): ~/kriya-evidence/product-72h-confidence/run8-gaps-2026-11-12/r8a/.

The rule now: create, then ONE start with the same 20 s client limit; a client-side timeout of that start is indeterminate, so the same container
is observed under the launch's own deadline (ProcessController's timeout - no new one); on every failure the container is removed before its network.
"""
import shutil
import time

import pytest
from _r8a_fake_docker import FakeDocker, install, registry_profile

from kriya.tools import containment, containment_oci, process
from kriya.tools.containment import (
    PROXY_START_FAILED,
    PROXY_START_REASON_CODES,
    PROXY_START_TIMEOUT,
    BackendUnavailableError,
    ContainmentSetupError,
    PreparedContainment,
    ProxyStartError,
    current_launch_deadline,
    launch_deadline,
)
from kriya.tools.containment_oci import OCIContainmentBackend

DOCKER = "/usr/local/bin/docker"
COMMAND = ["mvn", "-q", "compile"]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: DOCKER)
    path = tmp_path / "ws"
    path.mkdir()
    return str(path)


def _prepare(fake, workspace, *, budget=None):
    """One registry-scoped preparation; ``budget`` is the launch timeout ProcessController would have set as the deadline."""
    with launch_deadline(None if budget is None else fake.now + budget):
        return OCIContainmentBackend()._prepare_registry_scoped(DOCKER, registry_profile(workspace), COMMAND)


def _starts(fake):
    return [args for _, args in fake.calls if args[0] in ("start", "run")]


def _order(fake, *verbs):
    """Indexes of the first call of each verb, to assert an order."""
    seen = fake.verbs()
    return [seen.index(verb) for verb in verbs]


# ------------------------------------------------------------------------------------------------------------------ the run-8 shape, fixed

def test_run8_shape_a_start_held_past_the_client_limit_is_observed_and_the_launch_continues(workspace, monkeypatch):
    fake = FakeDocker(start_hold=31.1)
    install(monkeypatch, fake)

    prepared = _prepare(fake, workspace, budget=180)

    assert fake.verbs()[:3] == ["network create", "create", "start"]
    assert len(_starts(fake)) == 1 and _starts(fake)[0][0] == "start"          # the same start; never a second one, never `run`
    assert fake.at("start") == [0.0] and 20.0 in fake.at("inspect")              # the client limit is still 20 s; observation begins at it
    assert "network rm" not in fake.verbs() and "rm" not in fake.verbs()        # the network is kept while the start is unresolved
    assert fake.proxy()["status"] == "running" and len(fake.networks) == 1
    assert "exec" in fake.verbs()                                                # the existing readiness probe ran after it
    assert 11.0 <= prepared.setup_wait_seconds <= 12.0                           # 31.1 s held - 20 s client call, in 0.5 s observations
    prepared.cleanup()
    assert fake.proxy() is None and not fake.networks and not fake.containers


def test_the_observed_wait_comes_off_the_same_launch_timeout_and_no_new_deadline_exists(workspace, monkeypatch):
    """Deadline authority: the only bound is the caller's launch timeout. A start held longer than it ends typed, at that deadline."""
    fake = FakeDocker(start_hold=500.0)
    install(monkeypatch, fake)

    with pytest.raises(ProxyStartError) as refused:
        _prepare(fake, workspace, budget=60)

    assert refused.value.reason_code == PROXY_START_TIMEOUT and refused.value.cleanup_failures == ()
    assert 60.0 <= fake.now - 1000.0 <= 61.0                                    # stopped at the launch deadline, not at a constant of its own
    assert len(_starts(fake)) == 1
    rm_proxy, rm_network = _order(fake, "rm", "network rm")
    assert rm_proxy < rm_network                                                 # container first, then its network
    assert not fake.containers and not fake.networks
    fake.advance(500.0)                                                          # the backend serves the held start later: nothing to serve it to
    assert not fake.containers and not fake.networks


def test_without_a_launch_deadline_an_indeterminate_start_waits_for_nothing(workspace, monkeypatch):
    fake = FakeDocker(start_hold=31.1)
    install(monkeypatch, fake)

    with pytest.raises(ProxyStartError) as refused:
        _prepare(fake, workspace, budget=None)

    assert refused.value.reason_code == PROXY_START_TIMEOUT and "no deadline" in str(refused.value)
    assert fake.now - 1000.0 == 20.0 and fake.verbs().count("inspect") == 1     # one observation, no sleep
    assert _order(fake, "rm", "network rm") == sorted(_order(fake, "rm", "network rm"))
    assert not fake.containers and not fake.networks


def test_a_container_that_reaches_a_failed_state_after_the_timeout_is_a_typed_start_failure(workspace, monkeypatch):
    fake = FakeDocker(start_hold=25.0, start_outcome="exited")
    install(monkeypatch, fake)

    with pytest.raises(ProxyStartError) as refused:
        _prepare(fake, workspace, budget=180)

    assert refused.value.reason_code == PROXY_START_FAILED
    assert "reached state 'exited'" in str(refused.value) and "exit code 139" in str(refused.value)
    assert 25.0 <= fake.now - 1000.0 <= 26.0 and len(_starts(fake)) == 1
    rm_proxy, rm_network = _order(fake, "rm", "network rm")
    assert rm_proxy < rm_network and not fake.containers and not fake.networks


# ------------------------------------------------------------------------------------------------------------------------ negative controls

def test_an_ordinary_start_is_unchanged_no_observation_no_wait(workspace, monkeypatch):
    fake = FakeDocker(start_hold=0.4)
    install(monkeypatch, fake)

    prepared = _prepare(fake, workspace, budget=180)

    # create, start, the readiness probe, the proxy IP - and nothing else (no state inspect, no removal)
    assert fake.verbs() == ["network create", "create", "start", "exec", "inspect"]
    assert "IPAddress" in " ".join(fake.calls[-1][1])
    assert prepared.setup_wait_seconds == 0.0
    create = next(args for _, args in fake.calls if args[0] == "create")
    assert create[1:3] == ("--name", next(iter(fake.containers)))
    assert ("--cap-drop", "ALL") == create[create.index("--cap-drop"):create.index("--cap-drop") + 2]
    assert "no-new-privileges" in create and "kriya-acq-proxy-base:1" in create and "http_access deny all" in create[-1]


def test_a_real_docker_start_error_fails_at_once_and_is_cleaned_up_in_order(workspace, monkeypatch):
    fake = FakeDocker(start_error="Error response from daemon: driver failed programming external connectivity")
    install(monkeypatch, fake)

    with pytest.raises(ProxyStartError) as refused:
        _prepare(fake, workspace, budget=180)

    assert refused.value.reason_code == PROXY_START_FAILED and "driver failed" in str(refused.value)
    assert fake.now == 1000.0 and "inspect" not in fake.verbs()                 # a refusal is not indeterminate: nothing is observed
    assert fake.verbs() == ["network create", "create", "start", "rm", "network rm"]
    assert not fake.containers and not fake.networks


def test_a_proxy_that_starts_but_never_becomes_ready_still_fails_closed(workspace, monkeypatch):
    fake = FakeDocker(start_hold=0.4, ready=False)
    install(monkeypatch, fake)

    with pytest.raises(BackendUnavailableError, match="did not become ready within 15s"):
        _prepare(fake, workspace, budget=180)

    assert not fake.containers and not fake.networks


def test_a_teardown_that_cannot_remove_the_container_says_so_and_keeps_the_network(workspace, monkeypatch):
    """Truthful cleanup: the network is never removed under a container that may still be starting, and the leftover is named."""
    fake = FakeDocker(start_hold=500.0, rm_container_fails=True)
    install(monkeypatch, fake)

    with pytest.raises(ProxyStartError) as refused:
        _prepare(fake, workspace, budget=60)

    assert refused.value.reason_code == PROXY_START_TIMEOUT
    assert "CLEANUP INCOMPLETE" in str(refused.value) and len(refused.value.cleanup_failures) == 2
    assert "removal of container is already in progress" in refused.value.cleanup_failures[0]
    assert "kept" in refused.value.cleanup_failures[1]
    assert "network rm" not in fake.verbs() and len(fake.networks) == 1 and fake.proxy() is not None


def test_a_proxy_start_failure_is_a_containment_setup_failure_never_a_candidate_failure(workspace, monkeypatch):
    from kriya.workflow.recovery_coordinator import classify_attempt_exception

    fake = FakeDocker(start_hold=500.0)
    install(monkeypatch, fake)
    with pytest.raises(ProxyStartError) as refused:
        _prepare(fake, workspace, budget=30)

    assert isinstance(refused.value, ContainmentSetupError)
    classified = classify_attempt_exception(refused.value, None, last_attempt_mode="full_set", ground_scope_denial=lambda exc, ctx: None)
    assert classified.containment_setup_failure is True
    assert classified.failure.type == "containment_setup_failed"
    assert classified.failure.message.startswith("CONTAINMENT_SETUP_FAILED: CONTAINMENT_PROXY_START_TIMEOUT:")
    assert classified.failure.diagnostics == {"reason_code": PROXY_START_TIMEOUT}


def test_the_proxy_start_reason_codes_are_a_closed_table():
    assert PROXY_START_REASON_CODES == frozenset({"CONTAINMENT_PROXY_START_FAILED", "CONTAINMENT_PROXY_START_TIMEOUT"})
    assert containment_oci._PROXY_CLIENT_CALL_SECONDS == 20                      # the limit of the former `docker run -d`, not raised


# ------------------------------------------------------------------------------------- the launch deadline: ProcessController is its only owner

class _Backend:
    name = "fake"

    def __init__(self, waited):
        self.waited = waited
        self.seen = []

    def prepare(self, profile, command):
        deadline = current_launch_deadline()
        self.seen.append(None if deadline is None else deadline - time.monotonic())
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, setup_wait_seconds=self.waited)


class _Process:
    returncode = 0

    def __init__(self):
        self.timeouts = []

    def communicate(self, input=None, timeout=None):  # noqa: A002 - subprocess.Popen's own parameter name
        self.timeouts.append(timeout)
        return "", ""


@pytest.mark.parametrize("waited, expected", [(0.0, 90), (11.5, 78.5), (200.0, 0.0)])
def test_run_sets_its_timeout_as_the_launch_deadline_and_takes_the_wait_off_the_command(tmp_path, monkeypatch, waited, expected):
    backend, spawned = _Backend(waited), _Process()
    monkeypatch.setattr(process, "_spawn_popen", lambda *a, **k: spawned)

    result = process.ProcessController().run(["true"], cwd=str(tmp_path), timeout=90,
                                             containment_profile=registry_profile(str(tmp_path)), containment_backend=backend)

    assert result.returncode == 0
    assert 89.0 < backend.seen[0] <= 90.0                                        # the deadline preparation saw = this launch's own timeout
    assert spawned.timeouts == [expected]                                        # never the full timeout again after a wait
    assert current_launch_deadline() is None                                     # and it does not outlive the preparation


@pytest.mark.asyncio
async def test_run_async_applies_the_same_deadline_and_deduction(tmp_path, monkeypatch):
    backend = _Backend(11.5)
    waits = []

    class _AsyncProcess:
        returncode = 0

        async def communicate(self, input=None):  # noqa: A002
            return b"", b""

    async def spawn(*args, **kwargs):
        return _AsyncProcess()

    real_wait_for = process.asyncio.wait_for

    async def wait_for(awaitable, timeout=None):
        waits.append(timeout)
        return await real_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(process, "spawn_subprocess_exec_fail_closed", spawn)
    monkeypatch.setattr(process.asyncio, "wait_for", wait_for)

    await process.ProcessController().run_async(["true"], cwd=str(tmp_path), timeout=90,
                                                containment_profile=registry_profile(str(tmp_path)), containment_backend=backend)

    assert 89.0 < backend.seen[0] <= 90.0 and waits == [78.5]


def test_a_launch_without_a_timeout_has_no_launch_deadline(tmp_path):
    """start_managed() (a service the caller tears down) passes no timeout: preparation sees no deadline and waits for nothing."""
    backend = _Backend(0.0)

    process._prepare_env_and_preexec(command=["true"], containment_profile=registry_profile(str(tmp_path)), containment_backend=backend,
                                     env=None, preexec_fn=None)

    assert backend.seen == [None]
    assert containment._LAUNCH_DEADLINE.get() is None
