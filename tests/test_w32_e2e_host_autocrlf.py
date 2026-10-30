"""W3-2 permanent end-to-end regression (KCEP-7/8) for GIT-AUTOCRLF-HOST-CONFIG-CANDIDATE-BYTES-001.

The original MEASURED defect, through the real Kriya path (``run_generation_workflow`` with a scripted model, real git, real
sandbox, real gates, real terminal commit): under host ``core.autocrlf=true`` a GREEN run committed CRLF bytes over an LF workspace
(``calc.py``: 0 -> 6 CRLF lines; the ``false`` control stayed 0; the untouched ``test_calc.py`` stayed 0). The deterministic
equivalent of the live observation is this scripted run; the unit-level mechanisms are in tests/test_w32_host_git_authority.py.

Every fixture's exact bytes are asserted immediately before the run, so a workspace that was already normalized cannot make it green,
and the intentional-CRLF twin proves the invariant is BYTE STABILITY and not 'LF everywhere'.
"""
import hashlib

import pytest
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)

from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.checkpoint import compute_workspace_content_hash
from tests.test_w32_host_git_authority import Host


def _developer_always_answers(answer: str):
    return lambda role, request: answer if role == "developer" else benign_roles(role, request)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _config(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    cfg = chaos_config()
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    return cfg


@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_a_green_run_leaves_the_exact_lf_bytes_whatever_the_hosts_autocrlf(autocrlf, tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    cfg = _config(tmp_path, monkeypatch)
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    host.configure(autocrlf=autocrlf)                              # the host setting is in force for the whole run, not the fixture
    known = {"calc.py": CALC.encode(), "test_calc.py": TEST_SUB.encode()}
    before = {name: (workspace / name).read_bytes() for name in known}
    assert before == known and all(b"\r" not in data for data in before.values()), "the fixture was not exact LF"
    hash_before = compute_workspace_content_hash(str(workspace))

    with RuntimeRegistration(ChaosRuntime(_developer_always_answers(CALC_WITH_SUB))):
        result = run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)

    assert result.get("quality_gates_passed") is True, result.get("status")
    after = {name: (workspace / name).read_bytes() for name in known}
    assert after["calc.py"] == CALC_WITH_SUB.encode(), "the final workspace is not exactly the candidate the model produced"
    assert b"\r" not in after["calc.py"], "host autocrlf rewrote the candidate (the original defect)"
    assert after["test_calc.py"] == known["test_calc.py"], "an unrelated file changed"
    # the evidence identity of the final tree is the same under either host setting, and it moved because the candidate was applied
    hash_after = {}
    for setting in ("false", "true"):
        host.configure(autocrlf=setting)
        hash_after[setting] = compute_workspace_content_hash(str(workspace))
    assert hash_after["false"] == hash_after["true"] and hash_after["true"] is not None
    assert hash_after["true"] != hash_before


def test_positive_control_an_intentionally_crlf_workspace_stays_crlf_end_to_end(tmp_path, monkeypatch):
    """The overcorrection control: an implementation that 'fixed' the defect by forcing LF everywhere fails here."""
    host = Host(tmp_path, monkeypatch)
    cfg = _config(tmp_path, monkeypatch)
    crlf_calc, crlf_test = CALC.replace("\n", "\r\n"), TEST_SUB.replace("\n", "\r\n")
    workspace = git_workspace(tmp_path, {"calc.py": crlf_calc, "test_calc.py": crlf_test})
    host.configure(autocrlf="true")
    before = {name: (workspace / name).read_bytes() for name in ("calc.py", "test_calc.py")}
    assert before == {"calc.py": crlf_calc.encode(), "test_calc.py": crlf_test.encode()}, "the fixture was not exact CRLF"

    with RuntimeRegistration(ChaosRuntime(_developer_always_answers(CALC_WITH_SUB))):
        result = run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)

    assert result.get("quality_gates_passed") is True, result.get("status")
    calc = (workspace / "calc.py").read_bytes()
    assert calc.count(b"\r\n") == calc.count(b"\n") > 0, "the CRLF convention of the authoritative file was lost"
    assert calc.replace(b"\r\n", b"\n") == CALC_WITH_SUB.encode()
    assert (workspace / "test_calc.py").read_bytes() == before["test_calc.py"]

