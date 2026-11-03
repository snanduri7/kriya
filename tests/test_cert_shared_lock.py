"""CERT-SHARED-PORT-001: the tests that own a machine-wide resource (demo-v2's fixed stub port 18767) exclude each other across processes."""
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _shared_resource_lock import lock_path, shared_resource_lock

TESTS = Path(__file__).resolve().parent
HOLDER = """
import sys, time
from pathlib import Path
sys.path.insert(0, {tests!r})
from _shared_resource_lock import shared_resource_lock
d = Path({d!r})
with shared_resource_lock("x"):
    (d / {mark!r}).write_text("1")
    for _ in range(300):
        if (d / "release").exists():
            break
        time.sleep(0.1)
"""


def _spawn(tmp_path, mark, env):
    return subprocess.Popen([sys.executable, "-c", HOLDER.format(tests=str(TESTS), d=str(tmp_path), mark=mark)], env=env)


def _wait_for(path, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_a_second_process_blocks_until_the_first_releases(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_CERT_LOCK_DIR", str(tmp_path / "locks"))
    env = dict(__import__("os").environ)
    first = _spawn(tmp_path, "first", env)
    assert _wait_for(tmp_path / "first"), "the first holder never acquired"
    second = _spawn(tmp_path, "second", env)
    time.sleep(1.5)  # a negative can only pass falsely, never fail falsely: the second must NOT have the lock while the first holds it
    assert not (tmp_path / "second").exists()
    (tmp_path / "release").write_text("1")
    assert _wait_for(tmp_path / "second"), "the second process never acquired after the release"
    assert first.wait(timeout=30) == 0 and second.wait(timeout=30) == 0


def test_the_lock_file_follows_the_shared_directory_certify_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_CERT_LOCK_DIR", str(tmp_path / "shared"))
    with shared_resource_lock("demo-v2-stub-port-18767") as held:
        assert held == lock_path("demo-v2-stub-port-18767") == tmp_path / "shared" / "kriya-test-lock-demo-v2-stub-port-18767.lock"
    monkeypatch.delenv("KRIYA_CERT_LOCK_DIR")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "per-process-tmp"))
    assert lock_path("x").parent == Path("/tmp")  # independent of TMPDIR: two sessions with different temp roots still meet at one lock


@pytest.mark.parametrize("module", ["test_demo_v2_owner_state.py", "test_demo_v2_stub.py"])
def test_every_module_that_starts_or_inspects_the_fixed_port_stub_holds_the_lock(module):
    source = (TESTS / module).read_text(encoding="utf-8")
    assert "def _fixed_stub_port_lock" in source and 'shared_resource_lock("demo-v2-stub-port-18767")' in source
    assert "@pytest.fixture(autouse=True)" in source.split("def _fixed_stub_port_lock")[0].rsplit("\n\n", 1)[-1]


def test_no_other_test_module_starts_the_stub_or_runs_the_scenario_03_idle_check_without_the_lock():
    users = sorted(path.name for path in TESTS.glob("test_*.py")
                   if any(token in path.read_text(encoding="utf-8") for token in ("stub_ctl.sh", "--scenario\", \"03\"", "scenario-03", "STUB_PORT")))
    # test_demo_v2_assets.py only drives the REFUSAL path of reset/prepare on a directory demo-v2 did not create: it exits before any stub is started or inspected
    assert users == ["test_cert_shared_lock.py", "test_demo_v2_assets.py", "test_demo_v2_owner_state.py", "test_demo_v2_stub.py"], users
