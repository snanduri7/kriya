"""RDCG B-003(a) (owner decision 2026-10-28): an SEC-009 approval store (local per-workspace store or an operator/CI trust file) whose bytes are not UTF-8 is the existing typed
``ApprovalArtifactError`` refusal, exactly like bytes that are not JSON - and it never grants authority.

MEASURED before the fix (WAVE2_MALFORMED_STATE_CHARACTERIZATION_2026-10-28): ``load_approval_artifact`` caught ``(OSError, json.JSONDecodeError)`` only, so ``_load_artifact_fail_closed``
(``ApprovalArtifactError`` only) let the ``UnicodeDecodeError`` through ``resolve_with_approval`` and out of ``load_config()`` - every ``kriya`` command run in a folder with a pending
security-authority field died with a raw traceback instead of the typed ``ConfigAuthorityError`` that ASCII garbage in the same file produced. Nothing was approved either way (fail closed,
untyped). ``kriya authority inspect|revoke|approve`` already handled it; ``application/configuration.py`` caught ``ApprovalArtifactError`` only. Approval policy is unchanged."""
import json
import os

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityError
from kriya.config.authority_approval import ApprovalArtifactError, load_approval_artifact
from kriya.config.config import load_config, resolve_config_state
from kriya.context.workspace_identity import canonical_workspace
from kriya.kup.cli_ops import run_kup_operation

ASCII_GARBAGE = b"{{ not json"
INVALID_UTF8 = {
    "lone_invalid_start_byte": b'{"schema_version": 1}\xff\xfe',
    "truncated_multibyte_sequence": b'{"schema_version": 1, "note": "\xe2\x82',
    "utf16_with_bom": b'\xff\xfe{\x00}\x00',
}
UNREADABLE = {"ascii_garbage_control": ASCII_GARBAGE, "empty_control": b"", **INVALID_UTF8}
PROTECTED = {"mcp": {"hostile": {"command": "/bin/sh"}}}


@pytest.fixture(autouse=True)
def isolated_authority_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "kriya.yaml").write_text(yaml.safe_dump(PROTECTED))
    monkeypatch.chdir(folder)
    return folder


def _local_store(workspace) -> str:
    return aa.default_local_approval_path(canonical_workspace(str(workspace)))


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _bytes_of(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _approve_current_configuration() -> None:
    state = resolve_config_state()
    artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
    aa.save_approval_artifact(_local_store(state.workspace_root), artifact)


def test_controls_no_store_denies_and_a_valid_store_still_approves(ws):
    with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
        load_config()
    _approve_current_configuration()
    assert load_config().mcp["hostile"].command == "/bin/sh"


def test_an_absent_store_is_still_no_approval_not_an_error(tmp_path):
    assert load_approval_artifact(str(tmp_path / "missing.json")) is None


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_the_loader_raises_the_typed_artifact_error_and_leaves_the_bytes(tmp_path, data):
    path = _write(str(tmp_path / "store.json"), data)
    with pytest.raises(ApprovalArtifactError):
        load_approval_artifact(path)
    assert _bytes_of(path) == data


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_store_file_that_cannot_be_opened_is_the_typed_artifact_error_too(tmp_path):
    path = _write(str(tmp_path / "store.json"), b"{}")
    os.chmod(path, 0)
    try:
        with pytest.raises(ApprovalArtifactError) as exc_info:
            load_approval_artifact(path)
    finally:
        os.chmod(path, 0o600)
    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.parametrize("data", INVALID_UTF8.values(), ids=list(INVALID_UTF8))
def test_the_encoding_cause_is_kept_in_the_detail_not_in_a_new_type(tmp_path, data):
    path = _write(str(tmp_path / "store.json"), data)
    with pytest.raises(ApprovalArtifactError) as exc_info:
        load_approval_artifact(path)
    assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)
    assert "utf-8" in str(exc_info.value)


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_an_unreadable_local_store_never_grants_and_load_config_stays_the_typed_denial(ws, data):
    path = _write(_local_store(ws), data)
    with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
        load_config()
    assert _bytes_of(path) == data


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_an_unreadable_trust_file_never_grants_and_load_config_stays_the_typed_denial(ws, tmp_path, monkeypatch, data):
    trust = _write(str(tmp_path / "operator" / "trust.json"), data)
    monkeypatch.setenv("KRIYA_TRUST_FILE", trust)
    with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
        load_config()
    assert _bytes_of(trust) == data


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_an_unreadable_local_store_is_reported_to_the_application_as_store_unreadable_and_can_be_replaced(ws, data):
    """application/configuration.py receives the typed failure: required + store_unreadable (replaceable like ``kriya authority approve``), never a raw error."""
    _write(_local_store(ws), data)
    status = run_kup_operation(AppConfig(), {"operation": "configuration.approval_status", "workspace": str(ws)})
    assert status["data"]["status"] == "required" and status["data"]["reason"] == "store_unreadable" and status["data"]["can_approve"] is True
    approved = run_kup_operation(AppConfig(), {"operation": "configuration.approve", "workspace": str(ws), "fingerprint": status["data"]["fingerprint"]})
    assert approved["data"]["outcome"] == "approved"
    assert run_kup_operation(AppConfig(), {"operation": "configuration.approval_status", "workspace": str(ws)})["data"]["status"] == "approved"


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_authority_inspect_reports_the_invalid_store_without_a_traceback(ws, data):
    _write(_local_store(ws), data)
    result = CliRunner().invoke(main, ["authority", "inspect"])
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.exit_code == 0 and "present but invalid" in result.output
    assert json.dumps(PROTECTED) not in result.output
