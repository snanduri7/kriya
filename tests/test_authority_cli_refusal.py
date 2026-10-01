"""AUTHORITY-INSPECT-TRACEBACK-001 (Backlog 6.6): a SEC-009 approval store
configured inside the workspace (KRIYA_AUTHORITY_HOME) is refused - that
refusal is correct and stays - but the CLI reports it as a typed operator
error (TRUST_PATH_INSIDE_WORKSPACE, the offending path, a remediation) with
a non-zero exit, never a Python traceback. `doctor --production --json`
stays machine-readable and carries the same reason code. An unexpected
internal exception on the same path is not swallowed by that handling.
"""
import contextlib
import json
import os

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import authority_approval as aa

CODE = "TRUST_PATH_INSIDE_WORKSPACE"


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@pytest.fixture(name="workspace")
def _workspace(tmp_path, monkeypatch):
    """A workspace whose config sets a SECURITY_AUTHORITY field (so every
    authority command reaches the approval store), with the store inside it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "kriya.yaml").write_text(yaml.safe_dump({
        "llm": {"base_url": "http://127.0.0.1:11434/v1"}, "logging": {"file_enabled": False},
    }))
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(ws / ".authority"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    return ws


def _invoke(ws, *args):
    with _cwd(ws):
        return CliRunner().invoke(main, ["--config", "kriya.yaml", *args])


def _assert_typed_refusal(result, ws):
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # an exit, not an escaped error
    assert "Traceback" not in result.output
    assert f"[{CODE}]" in result.output
    assert "Remediation: Point KRIYA_AUTHORITY_HOME" in result.output and str(ws) in result.output


@pytest.mark.parametrize("command", [
    ["authority", "inspect"], ["authority", "approve", "--confirm"], ["authority", "revoke"],
])
def test_an_in_workspace_approval_store_is_a_typed_refusal_for_every_authority_command(workspace, command):
    result = _invoke(workspace, *command)
    _assert_typed_refusal(result, workspace)
    assert not (workspace / ".authority").exists()  # still refused: nothing was written


def test_a_command_that_loads_the_config_reports_the_same_typed_refusal(workspace):
    result = _invoke(workspace, "config")
    _assert_typed_refusal(result, workspace)
    assert f"Error loading configuration: [{CODE}]" in result.output


def test_doctor_production_json_stays_parseable_and_carries_the_reason_code(workspace):
    result = _invoke(workspace, "doctor", "--production", "--json")
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["production_ready"] is False
    (check,) = report["checks"]
    assert check["status"] == "FAIL" and check["evidence"]["reason_code"] == CODE
    assert check["remediation"].startswith("Point KRIYA_AUTHORITY_HOME")


def test_the_error_type_carries_its_own_code_path_and_remediation(tmp_path):
    with pytest.raises(aa.TrustPathInsideWorkspaceError) as refusal:
        aa.validate_trust_path_outside_workspace(str(tmp_path / "store" / "x.json"), str(tmp_path))
    assert refusal.value.reason_code == CODE
    assert refusal.value.trust_path == str(tmp_path / "store" / "x.json")
    assert refusal.value.workspace_root == str(tmp_path)
    assert "resolves inside the workspace root" in str(refusal.value)


def test_an_approval_store_outside_the_workspace_is_used_normally(workspace, tmp_path, monkeypatch):
    """Control: the same command succeeds once the store is outside."""
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "outside"))
    result = _invoke(workspace, "authority", "inspect")
    assert result.exit_code == 0 and "No local approval on file" in result.output


def test_an_unexpected_internal_error_on_the_same_path_is_not_hidden(workspace, monkeypatch):
    """Only the typed operator errors are rendered; a coding error still
    escapes with its traceback."""
    def broken(_workspace_root):
        raise RuntimeError("internal defect")

    monkeypatch.setattr(aa, "default_local_approval_path", broken)
    result = _invoke(workspace, "authority", "inspect")
    assert isinstance(result.exception, RuntimeError) and result.exit_code != 0
