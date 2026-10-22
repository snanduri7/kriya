"""F5 (Wave-1 closure delta): an invalid ``autonomy.egress_policy`` never reaches approval.

MEASURED at 6cde784: with ``autonomy: {egress_policy: LOCAL_ONLY}`` in the workspace kriya.yaml, ``kriya authority inspect`` listed the field as a pending security-authority
field ("Run 'kriya authority approve' to grant it") although the value can never load - the vocabulary check ran only when AppConfig was constructed, AFTER the approval
step. The malformed ``autonomy.mode`` was already refused typed at the shared resolution point (S16a). Now the effective egress value is checked at that same point, by the ONE
validator (``kriya.config.authority.parse_egress_policy``), so ``load_config``, ``kriya authority inspect|approve`` and the desktop's configuration operations all refuse it before
classification: there is nothing to approve, and an approval (even a stale digest-valid one) of the invalid value is never consulted.
"""
import os
import re
import subprocess

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityViolation, ConfigSource, EgressPolicyError, FieldClassification
from kriya.config.config import load_config, resolve_config_state

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALID = ("local_only", "unrestricted")
INVALID = ["LOCAL_ONLY", "Local_Only", "local-only", " local_only", "local_only ", "localonly", "open", "none", "UNRESTRICTED", "", None, 0, 1, True, False, ["local_only"], {"local_only": True}]


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


def _workspace(tmp_path, monkeypatch, egress, name="ws"):
    ws = tmp_path / name
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": {"egress_policy": egress}}))
    monkeypatch.chdir(ws)
    return ws


def _approve(ws, value):
    """A digest-valid SEC-009 approval of exactly ``autonomy.egress_policy = value``, built the way ``kriya authority approve`` builds one (a stale approval)."""
    violation = ConfigAuthorityViolation("autonomy.egress_policy", ConfigSource.AUTO_DISCOVERED_CWD, FieldClassification.SECURITY_AUTHORITY,
                                         "classification security_authority requires a trusted or explicitly authorized source")
    artifact = aa.build_approval_artifact([violation], {"autonomy": {"egress_policy": value}}, os.path.realpath(str(ws)))
    target = aa.default_local_approval_path(os.path.realpath(str(ws)))
    aa.save_approval_artifact(target, artifact)
    return target


def _ids(values):
    return [repr(v) for v in values]


# ------------------------------------------------------------------------------------------------------- valid values keep their governed behaviour

def test_local_only_is_valid_vocabulary_and_resolves(tmp_path, monkeypatch):
    """No vocabulary error. (Setting any security-authority field in a repository kriya.yaml is still a pending approval whatever its value - unchanged.)"""
    _workspace(tmp_path, monkeypatch, "local_only")
    assert [v.field_path for v in resolve_config_state().violations] == ["autonomy.egress_policy"]


def test_unrestricted_is_still_a_pending_security_authority_field_not_an_error(tmp_path, monkeypatch):
    """The widening value is valid vocabulary: it is offered for approval exactly as before (and refused at load until approved)."""
    _workspace(tmp_path, monkeypatch, "unrestricted")
    state = resolve_config_state()
    assert [v.field_path for v in state.violations] == ["autonomy.egress_policy"]
    result = CliRunner().invoke(main, ["authority", "inspect"], catch_exceptions=False)
    assert result.exit_code == 0 and "autonomy.egress_policy" in result.output and "pending approval" in result.output, result.output


# ------------------------------------------------------------------------------------------------------- an invalid value is refused BEFORE approval

@pytest.mark.parametrize("value", INVALID, ids=_ids(INVALID))
def test_resolution_refuses_an_invalid_egress_policy_before_any_authority_comparison(tmp_path, monkeypatch, value):
    _workspace(tmp_path, monkeypatch, value)
    with pytest.raises(EgressPolicyError, match=r"autonomy\.egress_policy must be 'local_only' or 'unrestricted'"):
        resolve_config_state()
    with pytest.raises(EgressPolicyError):
        load_config()


@pytest.mark.parametrize("inside", [True, False], ids=["inside", "outside"])
@pytest.mark.parametrize("value", ["LOCAL_ONLY", None, 1], ids=_ids(["LOCAL_ONLY", None, 1]))
def test_an_explicit_config_is_refused_the_same_way(tmp_path, monkeypatch, value, inside):
    ws = _workspace(tmp_path, monkeypatch, "local_only")
    path = (ws if inside else tmp_path) / "explicit.yaml"
    path.write_text(yaml.safe_dump({"autonomy": {"egress_policy": value}}))
    with pytest.raises(EgressPolicyError):
        resolve_config_state(str(path))
    with pytest.raises(EgressPolicyError):
        load_config(str(path))


@pytest.mark.parametrize("value", ["LOCAL_ONLY", "local-only", None], ids=_ids(["LOCAL_ONLY", "local-only", None]))
def test_an_approval_does_not_make_an_invalid_egress_policy_valid(tmp_path, monkeypatch, value):
    """A digest-valid durable approval of the invalid value is never consulted: the value is a validation error first."""
    ws = _workspace(tmp_path, monkeypatch, value)
    _approve(ws, value)
    with pytest.raises(EgressPolicyError):
        load_config()


@pytest.mark.parametrize("value", ["LOCAL_ONLY", "local-only", ""], ids=_ids(["LOCAL_ONLY", "local-only", ""]))
def test_the_cli_refuses_typed_offers_nothing_and_approves_nothing(tmp_path, monkeypatch, value):
    ws = _workspace(tmp_path, monkeypatch, value)
    _approve(ws, value)                                                                        # even a pre-existing approval of it
    store = aa.default_local_approval_path(os.path.realpath(str(ws)))
    before = open(store, "rb").read()
    for argv in (["config"], ["authority", "inspect"], ["authority", "approve", "--confirm"]):
        result = CliRunner().invoke(main, argv, catch_exceptions=False)
        assert result.exit_code == 1, (argv, result.exit_code, result.output)
        assert "autonomy.egress_policy must be 'local_only' or 'unrestricted'" in result.output, (argv, result.output)
        assert "pending approval" not in result.output and "kriya authority approve" not in result.output, (argv, result.output)   # never offered
    assert open(store, "rb").read() == before                                                  # `approve` wrote nothing


def test_the_desktop_configuration_paths_refuse_typed(tmp_path, monkeypatch):
    from kriya.config.service import CONFIG_LOAD_FAILED, ConfigService
    from kriya.kup.cli_ops import run_kup_operation

    ws = _workspace(tmp_path, monkeypatch, "LOCAL_ONLY")
    _approve(ws, "LOCAL_ONLY")
    resolved = ConfigService().resolve_here()
    assert resolved.app is None and resolved.failure is not None
    assert resolved.failure["code"] == CONFIG_LOAD_FAILED and "autonomy.egress_policy must be" in resolved.failure["message"], resolved.failure
    status = run_kup_operation(None, {"operation": "configuration.approval_status", "workspace": str(ws)})
    assert status["error"]["code"] == CONFIG_LOAD_FAILED and "autonomy.egress_policy must be" in status["error"]["message"], status
    approve = run_kup_operation(None, {"operation": "configuration.approve", "workspace": str(ws), "fingerprint": "sha256:" + "0" * 64})
    assert approve["error"]["code"] == CONFIG_LOAD_FAILED and "autonomy.egress_policy must be" in approve["error"]["message"], approve


# ------------------------------------------------------------------------------------------------------- one vocabulary owner

def test_the_resolution_and_cli_reuse_the_one_validator_and_define_no_vocabulary_of_their_own():
    """The owner (kriya/config/authority.py) lists the members; nothing else that handles the field does, and AutonomyConfig's validator, assignment and model_copy call the very
    same function the resolution does."""
    for relative in ("kriya/config/resolution.py", "kriya/cli_commands/authority.py", "kriya/config/authority_approval.py", "kriya/config/service.py", "kriya/config/config.py"):
        text = open(os.path.join(REPO, relative), encoding="utf-8").read()
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        # a second vocabulary would list the members together (a tuple / set / `in (...)` check); the production preset's single literal is not one
        assert not re.search(r"[\"']local_only[\"']\s*,\s*[\"']unrestricted[\"']|[\"']unrestricted[\"']\s*,\s*[\"']local_only[\"']", code), f"{relative} defines its own egress vocabulary"
    assert "parse_egress_policy(autonomy_section[\"egress_policy\"])" in open(os.path.join(REPO, "kriya/config/resolution.py"), encoding="utf-8").read()
    assert "return parse_egress_policy(value)" in open(os.path.join(REPO, "kriya/config/config.py"), encoding="utf-8").read()
