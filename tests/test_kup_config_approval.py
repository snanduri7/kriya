"""GUI configuration-approval bridge: ``configuration.approval_status`` and ``configuration.approve``.

The desktop is ONE MORE CLIENT of SEC-009. These tests pin that it does not add, widen or reinterpret authority:

* equivalence with ``kriya authority approve|inspect`` in BOTH directions (an approval made through KUP is current for the CLI, and the other way round,
  and both are invalidated by the same change);
* the approval is bound to the exact configuration the person reviewed (TOCTOU: a changed configuration is ``APPROVAL_STALE`` and nothing is written);
* no configuration VALUE ever reaches the response;
* the request grammar cannot carry fields, values, paths, digests or store locations;
* the approval is durable in Kriya's own store (not in any client) and a restarted client sees it.

Every test points KRIYA_AUTHORITY_HOME at a tmp directory: nothing here reads or writes the real ~/.kriya/authority.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.config import authority_approval as aa
from kriya.control.workspace_identity import canonical_workspace
from kriya.kup import policy, serve
from kriya.kup.cli_ops import run_kup_operation

SECRET = "tok-9f8e7d6c5b4a-do-not-leak"
CREDENTIAL_URL = "http://operator:hunter2-pass@127.0.0.1:11434/v1"
PROTECTED = {
    "llm": {"base_url": CREDENTIAL_URL},
    "mcp": {"hostile": {"command": "/usr/local/bin/hostile-mcp", "env": {"API_TOKEN": SECRET}}},
}
VALUE_STRINGS = (SECRET, "hunter2-pass", "operator:", "/usr/local/bin/hostile-mcp", "127.0.0.1:11434")


@pytest.fixture(autouse=True)
def authority_home(tmp_path, monkeypatch):
    home = tmp_path / "_authority_home"
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(home))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    return home


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "kriya.yaml").write_text(yaml.safe_dump(PROTECTED))
    return folder


def _status(path):
    return run_kup_operation(AppConfig(), {"operation": "configuration.approval_status", "workspace": str(path)})


def _approve(path, fingerprint):
    return run_kup_operation(AppConfig(), {"operation": "configuration.approve", "workspace": str(path), "fingerprint": fingerprint})


def _artifact_path(path):
    return aa.default_local_approval_path(canonical_workspace(str(path)))


def _cli(path, *args):
    """The real CLI, run FROM the folder (the working directory decides discovery, exactly as for a terminal user)."""
    previous = os.getcwd()
    os.chdir(path)
    try:
        return CliRunner().invoke(main, ["authority", *args])
    finally:
        os.chdir(previous)


def _write_config(path, data):
    (path / "kriya.yaml").write_text(yaml.safe_dump(data))


def _tree(path):
    return sorted((os.path.relpath(os.path.join(d, n), path), os.stat(os.path.join(d, n)).st_mtime_ns) for d, _dirs, names in os.walk(path) for n in names)


# --------------------------------------------------------------------- status

def test_status_when_nothing_needs_approval(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    env = _status(plain)
    assert env["error"] is None and env["operation"] == "configuration.approval_status"
    data = env["data"]
    assert data["status"] == "not_required" and data["required"] is False and data["can_approve"] is False
    assert data["protected_fields"] == [] and data["fingerprint"] is None and data["reason"] is None


def test_status_lists_protected_field_names_and_is_read_only(ws, authority_home):
    before, cwd = _tree(ws), os.getcwd()
    env = _status(ws)
    data = env["data"]
    assert data["status"] == "required" and data["required"] is True and data["can_approve"] is True and data["reason"] == "no_approval"
    names = [f["field"] for f in data["protected_fields"]]
    assert names == ["llm.base_url", "mcp.hostile"], "exactly what `kriya authority inspect` lists, by name"
    assert all(set(f) == {"field", "classification", "source"} for f in data["protected_fields"])
    assert data["fingerprint"].startswith("sha256:") and len(data["fingerprint"]) == 71
    assert data["workspace"] == canonical_workspace(str(ws)) and data["values_shown"] is False
    assert os.getcwd() == cwd, "the process working directory is restored"
    assert _tree(ws) == before, "a status read writes nothing into the folder"
    assert not authority_home.exists(), "a status read writes no approval"


def test_status_never_sends_a_configuration_value(ws):
    text = json.dumps(_status(ws))
    for secret in VALUE_STRINGS:
        assert secret not in text, f"{secret!r} reached the response"
    # also after approval, and in the approve response
    data = _status(ws)["data"]
    text = json.dumps(_approve(ws, data["fingerprint"])) + json.dumps(_status(ws))
    for secret in VALUE_STRINGS:
        assert secret not in text, f"{secret!r} reached the response"


def test_status_is_deterministic_for_one_configuration_and_changes_with_any_value(ws):
    first = _status(ws)["data"]["fingerprint"]
    assert _status(ws)["data"]["fingerprint"] == first
    _write_config(ws, {**PROTECTED, "llm": {"base_url": "http://127.0.0.1:9/v1"}})
    assert _status(ws)["data"]["fingerprint"] != first


def test_status_for_a_malformed_configuration_is_typed(ws):
    (ws / "kriya.yaml").write_text("llm: [unclosed\n  - : :\n")
    env = _status(ws)
    assert env["data"] is None and env["error"]["code"] == policy.CONFIG_LOAD_FAILED
    assert "Traceback" not in json.dumps(env)


def test_status_for_a_folder_that_is_not_there_or_not_a_folder_is_typed(tmp_path):
    missing = _status(tmp_path / "nope")
    assert missing["error"]["code"] == policy.WORKSPACE_UNAVAILABLE and missing["error"]["reason"] == "not_found"
    (tmp_path / "f").write_text("x")
    assert _status(tmp_path / "f")["error"]["reason"] == "not_a_directory"
    relative = run_kup_operation(AppConfig(), {"operation": "configuration.approval_status", "workspace": "relative/path"})
    assert relative["error"]["code"] == policy.INVALID_REQUEST


# -------------------------------------------------------------------- approve

def test_approve_records_the_approval_and_the_run_is_no_longer_refused(ws, authority_home):
    reviewed = _status(ws)["data"]
    env = _approve(ws, reviewed["fingerprint"])
    assert env["error"] is None and env["data"]["outcome"] == "approved"
    assert env["data"]["approved_fields"] == ["llm.base_url", "mcp.hostile"] and len(env["data"]["approval_digest"]) == 16
    after = _status(ws)["data"]
    assert after["status"] == "approved" and after["required"] is True and after["can_approve"] is False
    # readiness, read the way the desktop reads it, no longer carries the blocker
    pre = run_kup_operation(AppConfig(), {"operation": "execution.preflight", "workspace": str(ws)})["data"]
    assert pre["configuration"]["authority"]["status"] == "approved"
    assert "CONFIG_AUTHORITY_REFUSED" not in [b["code"] for b in pre["blockers"]]
    assert os.path.isfile(_artifact_path(ws)) and str(ws) not in os.path.realpath(str(authority_home)), "the store is outside the folder"


def test_a_kup_approval_is_current_for_the_cli_and_covers_the_same_fields(ws):
    fingerprint = _status(ws)["data"]["fingerprint"]
    assert _approve(ws, fingerprint)["error"] is None
    inspected = _cli(ws, "inspect")
    assert "is CURRENT and covers all pending fields" in inspected.output, inspected.output
    artifact = aa.load_approval_artifact(_artifact_path(ws))
    assert artifact is not None and aa.verify_artifact_integrity(artifact)
    assert [r.field_path for r in artifact.records] == ["llm.base_url", "mcp.hostile"]
    assert artifact.records[1].env_keys == ("API_TOKEN",), "env key NAMES only; the value is digested with the artifact's salt"
    assert SECRET not in json.dumps(artifact.to_dict())


def test_a_cli_approval_is_seen_as_approved_by_kup(ws):
    assert _status(ws)["data"]["status"] == "required"
    result = _cli(ws, "approve", "--confirm")
    assert result.exit_code == 0, result.output
    data = _status(ws)["data"]
    assert data["status"] == "approved" and data["reason"] is None


def test_the_artifact_kup_writes_has_the_same_shape_as_the_cli_artifact(ws, tmp_path):
    cli_copy = tmp_path / "cli-artifact.json"
    other = tmp_path / "other"
    other.mkdir()
    (other / "kriya.yaml").write_text((ws / "kriya.yaml").read_text())
    assert _cli(other, "approve", "--confirm", "--out", str(cli_copy)).exit_code == 0
    via_cli = json.loads(cli_copy.read_text())
    assert _approve(ws, _status(ws)["data"]["fingerprint"])["error"] is None
    via_kup = json.loads(open(_artifact_path(ws)).read())
    assert set(via_kup) == set(via_cli)
    assert via_kup["schema_version"] == via_cli["schema_version"] and via_kup["authority_schema_version"] == via_cli["authority_schema_version"]
    strip = lambda a: [{k: v for k, v in r.items() if k != "value_digest"} for r in a["security_subset"]["records"]]  # noqa: E731 - digests carry the per-artifact salt
    assert strip(via_kup) == strip(via_cli)
    assert via_kup["salt"] != via_cli["salt"], "each artifact has its own random salt, as before"


def test_a_changed_configuration_after_approval_requires_approval_again(ws):
    assert _approve(ws, _status(ws)["data"]["fingerprint"])["error"] is None
    assert _status(ws)["data"]["status"] == "approved"
    _write_config(ws, {**PROTECTED, "llm": {"base_url": "http://127.0.0.1:9999/v1"}})
    data = _status(ws)["data"]
    assert data["status"] == "required" and data["reason"] == "stale_approval" and data["can_approve"] is True
    pre = run_kup_operation(AppConfig(), {"operation": "execution.preflight", "workspace": str(ws)})["data"]
    assert "CONFIG_AUTHORITY_REFUSED" in [b["code"] for b in pre["blockers"]]
    assert "does NOT cover the current configuration" in _cli(ws, "inspect").output, "the CLI reaches the same verdict"


def test_adding_or_removing_a_protected_field_invalidates_the_approval(ws):
    assert _approve(ws, _status(ws)["data"]["fingerprint"])["error"] is None
    _write_config(ws, {**PROTECTED, "mcp": {**PROTECTED["mcp"], "second": {"command": "/bin/true"}}})
    assert _status(ws)["data"]["status"] == "required"
    _write_config(ws, {"llm": PROTECTED["llm"]})
    assert _status(ws)["data"]["status"] == "required"


def test_approve_twice_is_idempotent_and_rewrites_nothing(ws):
    fingerprint = _status(ws)["data"]["fingerprint"]
    assert _approve(ws, fingerprint)["data"]["outcome"] == "approved"
    path = _artifact_path(ws)
    bytes_then, mtime_then = open(path, "rb").read(), os.stat(path).st_mtime_ns
    again = _approve(ws, fingerprint)
    assert again["error"] is None and again["data"]["outcome"] == "already_approved" and again["data"]["approval_digest"] is None
    assert open(path, "rb").read() == bytes_then and os.stat(path).st_mtime_ns == mtime_then


# ----------------------------------------------------------- TOCTOU / staleness

def test_approving_a_configuration_that_changed_after_review_is_refused_and_writes_nothing(ws, authority_home):
    reviewed = _status(ws)["data"]["fingerprint"]
    _write_config(ws, {**PROTECTED, "llm": {"base_url": "http://127.0.0.1:1/v1"}})  # the race: B appears between Review (A) and Approve
    env = _approve(ws, reviewed)
    assert env["data"] is None and env["error"]["code"] == policy.APPROVAL_STALE and env["error"]["reason"] == "configuration_changed"
    assert not authority_home.exists(), "NOTHING was approved: B was not approved silently"
    assert _status(ws)["data"]["status"] == "required"
    # reviewing again gives the new fingerprint, and that one is approvable
    fresh = _status(ws)["data"]["fingerprint"]
    assert fresh != reviewed
    assert _approve(ws, fresh)["data"]["outcome"] == "approved"


def test_a_change_that_leaves_nothing_to_approve_is_also_stale(ws):
    reviewed = _status(ws)["data"]["fingerprint"]
    _write_config(ws, {"llm": {"temperature": 0.1}})
    env = _approve(ws, reviewed)
    assert env["error"]["code"] == policy.APPROVAL_STALE


def test_an_env_secret_change_is_a_different_fingerprint(ws):
    reviewed = _status(ws)["data"]["fingerprint"]
    rotated = {**PROTECTED, "mcp": {"hostile": {**PROTECTED["mcp"]["hostile"], "env": {"API_TOKEN": SECRET + "-rotated"}}}}
    _write_config(ws, rotated)
    assert _approve(ws, reviewed)["error"]["code"] == policy.APPROVAL_STALE, "a rotated secret must not ride an approval given for the old one"


def test_a_review_taken_before_a_service_restart_is_refused_as_stale(ws, monkeypatch):
    reviewed = _status(ws)["data"]["fingerprint"]
    monkeypatch.setattr(aa, "_REVIEW_FINGERPRINT_SALT", "a-different-process-salt")  # what a restarted Kriya service has
    assert _approve(ws, reviewed)["error"]["code"] == policy.APPROVAL_STALE


def test_a_fingerprint_from_another_folder_cannot_approve_this_one(ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _write_config(other, {"mcp": {"unrelated": {"command": "/bin/true"}}})
    foreign = _status(other)["data"]["fingerprint"]
    assert _approve(ws, foreign)["error"]["code"] == policy.APPROVAL_STALE
    assert _status(ws)["data"]["status"] == "required"


# ------------------------------------------------------------ request grammar

@pytest.mark.parametrize("bad", [None, "", "sha256:", "sha256:xyz", "SHA256:" + "a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63, "sha256:" + "a" * 65, "*", "all", 7, ["sha256:" + "a" * 64]])
def test_a_malformed_fingerprint_is_refused_before_anything_is_resolved(ws, authority_home, bad):
    env = run_kup_operation(AppConfig(), {"operation": "configuration.approve", "workspace": str(ws), "fingerprint": bad})
    assert env["error"]["code"] == policy.INVALID_REQUEST and env["error"]["reason"] == "invalid_fingerprint"
    assert not authority_home.exists()


def test_the_wire_grammar_carries_only_workspace_and_fingerprint():
    assert serve.ARGUMENTS["configuration.approve"] == frozenset({"workspace", "fingerprint"})
    assert serve.ARGUMENTS["configuration.approval_status"] == frozenset({"workspace"})
    ok = serve.parse_request(json.dumps({"id": "q1", "schema_version": 1, "operation": "configuration.approve",
                                         "arguments": {"workspace": "/w", "fingerprint": "sha256:" + "a" * 64}}).encode())
    assert "refusal" not in ok
    for extra in ("fields", "values", "protected_fields", "path", "store", "digest", "set_digest", "salt", "trust_file", "out", "confirm", "config", "approved_by", "workspace_id"):
        refused = serve.parse_request(json.dumps({"id": "q1", "schema_version": 1, "operation": "configuration.approve",
                                                  "arguments": {"workspace": "/w", "fingerprint": "sha256:" + "a" * 64, extra: "x"}}).encode())
        assert refused["refusal"]["error"]["code"] == policy.INVALID_REQUEST, extra
    nested = serve.parse_request(json.dumps({"id": "q1", "schema_version": 1, "operation": "configuration.approve",
                                             "arguments": {"workspace": "/w", "fingerprint": {"records": []}}}).encode())
    assert nested["refusal"]["error"]["code"] == policy.INVALID_REQUEST, "structured content cannot be submitted"


@pytest.mark.parametrize("workspace", ["*", "~", "..", "relative", "/tmp/../etc", "", "/does/not/exist"])
def test_a_wildcard_or_arbitrary_path_cannot_be_approved(authority_home, workspace):
    env = run_kup_operation(AppConfig(), {"operation": "configuration.approve", "workspace": workspace, "fingerprint": "sha256:" + "a" * 64})
    # "/tmp/../etc" is a real absolute folder: it is refused because no fingerprint Kriya gave matches it, not because of how it is spelled
    assert env["data"] is None and env["error"]["code"] in (policy.INVALID_REQUEST, policy.WORKSPACE_UNAVAILABLE, policy.APPROVAL_STALE)
    assert not authority_home.exists()


# ------------------------------------------------------- unavailable conditions

def test_a_trust_file_in_effect_cannot_be_approved_around(ws, tmp_path, monkeypatch, authority_home):
    trust = tmp_path / "ci-trust.json"
    monkeypatch.setenv("KRIYA_TRUST_FILE", str(trust))
    data = _status(ws)["data"]
    assert data["status"] == "unavailable" and data["can_approve"] is False and data["reason"] == "trust_file_in_effect" and data["blocking_reason"]
    env = _approve(ws, data["fingerprint"])
    assert env["error"]["code"] == policy.APPROVAL_UNAVAILABLE and env["error"]["reason"] == "trust_file_in_effect"
    assert not authority_home.exists()


def test_a_store_that_would_be_inside_the_folder_is_refused(ws, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(ws / ".authority"))
    data = _status(ws)["data"]
    assert data["status"] == "unavailable" and data["reason"] == "store_inside_workspace" and data["can_approve"] is False
    env = _approve(ws, data["fingerprint"])
    assert env["error"]["code"] == policy.APPROVAL_UNAVAILABLE and env["error"]["reason"] == "store_inside_workspace"
    assert not (ws / ".authority").exists(), "an approval is never written into the folder"


def test_an_unreadable_store_is_reported_and_can_be_replaced_like_the_cli_does(ws, authority_home):
    path = _artifact_path(ws)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{ not json")
    data = _status(ws)["data"]
    assert data["status"] == "required" and data["reason"] == "store_unreadable" and data["can_approve"] is True
    assert _approve(ws, data["fingerprint"])["data"]["outcome"] == "approved"
    assert _status(ws)["data"]["status"] == "approved"


def test_a_store_that_cannot_be_written_is_a_typed_failure_and_nothing_is_claimed(ws, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the store directory should be")
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(blocker / "authority"))
    data = _status(ws)["data"]
    env = _approve(ws, data["fingerprint"])
    assert env["error"]["code"] == policy.APPROVAL_UNAVAILABLE and env["error"]["reason"] == "store_unwritable"
    assert _status(ws)["data"]["status"] == "required"


def test_an_unexpected_internal_fault_is_not_swallowed_as_a_refusal(ws, monkeypatch):
    """The broad catch around configuration resolution must only translate resolution failures: a coding error AFTER it (here, in the approval write) surfaces."""
    monkeypatch.setattr("kriya.kup.authority_ops.grant_local_approval", lambda *a, **k: (_ for _ in ()).throw(TypeError("coding error")))
    fingerprint = _status(ws)["data"]["fingerprint"]
    with pytest.raises(TypeError):
        _approve(ws, fingerprint)


# ----------------------------------------------------------------- durability

def test_the_approval_is_durable_in_kriyas_store_and_survives_a_new_process(ws, authority_home):
    assert _approve(ws, _status(ws)["data"]["fingerprint"])["error"] is None
    code = ("import json,sys; from kriya.config import AppConfig; from kriya.kup.cli_ops import run_kup_operation;"
            "print(json.dumps(run_kup_operation(AppConfig(), {'operation':'configuration.approval_status','workspace':sys.argv[1]})['data']['status']))")
    out = subprocess.run([sys.executable, "-c", code, str(ws)], capture_output=True, text=True, env={**os.environ, "KRIYA_AUTHORITY_HOME": str(authority_home)}, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == "approved"
    inspected = subprocess.run([sys.executable, "-m", "kriya.cli", "authority", "inspect"], cwd=ws, capture_output=True, text=True,
                               env={**os.environ, "KRIYA_AUTHORITY_HOME": str(authority_home)})
    assert "CURRENT" in inspected.stdout + inspected.stderr, inspected.stdout + inspected.stderr


def test_revoking_through_the_cli_is_seen_immediately(ws):
    assert _approve(ws, _status(ws)["data"]["fingerprint"])["error"] is None
    assert _cli(ws, "revoke").exit_code == 0
    assert _status(ws)["data"]["status"] == "required"


# ------------------------------------------------------------- the service path

def test_the_service_answers_both_operations_even_when_its_own_configuration_is_denied(ws, tmp_path):
    """`kriya authority` is reachable while a configuration is denied; so are these two (they use no cockpit configuration)."""
    denied = tmp_path / "denied.yaml"
    denied.write_text(yaml.safe_dump({"mcp": {"x": {"command": "/bin/true"}}}))
    home = tmp_path / "service-home"
    home.mkdir()
    previous = os.getcwd()
    os.chdir(home)
    try:
        def ask(operation, **arguments):
            parsed = serve.parse_request(json.dumps({"id": "q1", "schema_version": 1, "operation": operation, "arguments": arguments}).encode())
            return json.loads(serve.answer(parsed, str(denied), None))["envelope"]
        other = ask("execution.context", workspace=str(ws))
        assert other["error"]["code"] == policy.CONFIG_AUTHORITY_REFUSED, "control: the service's own configuration IS denied"
        status = ask("configuration.approval_status", workspace=str(ws))
        assert status["error"] is None and status["data"]["status"] == "required"
        assert ask("configuration.approve", workspace=str(ws), fingerprint=status["data"]["fingerprint"])["data"]["outcome"] == "approved"
    finally:
        os.chdir(previous)
