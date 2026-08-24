"""MA4.14: policy telemetry (kriya/policy/telemetry.py). Two things matter
equally: real secret shapes actually get redacted, AND legitimate
non-secret data that merely LOOKS long/random (a SHA-256 hash, a commit
SHA) is left alone - the same false-positive discipline MA4.12's injection
detector established, applied to redaction instead of detection."""

import json

import pytest

from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.policy.telemetry import PolicyDecisionRecord, build_decision_record, scrub_potential_secrets


# --- scrub_potential_secrets: real secret shapes get redacted ---

def test_scrubs_key_value_password():
    assert "hunter2" not in scrub_potential_secrets("--password=hunter2")
    assert "***REDACTED***" in scrub_potential_secrets("--password=hunter2")


def test_scrubs_key_value_api_key_with_colon():
    scrubbed = scrub_potential_secrets("api_key: sk_live_abcdef1234567890")
    assert "sk_live_abcdef1234567890" not in scrubbed


def test_scrubs_bearer_token():
    scrubbed = scrub_potential_secrets("Authorization header: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def")
    assert "eyJhbGciOiJIUzI1NiJ9" not in scrubbed
    assert "Bearer ***REDACTED***" in scrubbed


def test_scrubs_aws_access_key_id_shape():
    scrubbed = scrub_potential_secrets("found AKIAABCDEFGHIJKLMNOP in the diff")
    assert "AKIAABCDEFGHIJKLMNOP" not in scrubbed


def test_scrubs_openai_style_key_shape():
    scrubbed = scrub_potential_secrets("set OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx1234")
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in scrubbed


def test_scrubs_github_token_shape():
    scrubbed = scrub_potential_secrets("token: ghp_abcdefghijklmnopqrstuvwxyz012345")
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in scrubbed


def test_multiple_secrets_in_one_string_all_scrubbed():
    text = "--password=hunter2 --api-key=sk-abcdefghijklmnopqrstuvwx1234"
    scrubbed = scrub_potential_secrets(text)
    assert "hunter2" not in scrubbed
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in scrubbed


def test_scrub_never_mutates_the_input():
    original = "--password=hunter2"
    scrub_potential_secrets(original)
    assert original == "--password=hunter2"


# --- False-positive guard: legitimate non-secret data left alone ---

def test_sha256_hash_is_not_redacted():
    """The exact shape kriya/policy/approved_sources.py's manifest hashes
    are - long hex, no credential-shaped key name or known token prefix."""
    sha256_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    scrubbed = scrub_potential_secrets(f"approved manifest entry hash={sha256_hash}")
    assert sha256_hash in scrubbed


def test_git_commit_sha_is_not_redacted():
    scrubbed = scrub_potential_secrets("git commit 1c2cfe2a4b9e8f0d3c5a7b6e9f1d2c3b4a5e6f7d")
    assert "1c2cfe2a4b9e8f0d3c5a7b6e9f1d2c3b4a5e6f7d" in scrubbed


def test_ordinary_file_path_is_not_redacted():
    scrubbed = scrub_potential_secrets("/Users/dev/project/src/main.py")
    assert scrubbed == "/Users/dev/project/src/main.py"


def test_ordinary_command_is_not_redacted():
    scrubbed = scrub_potential_secrets("mvn clean compile -Dmaven.compiler.showWarnings=true")
    assert scrubbed == "mvn clean compile -Dmaven.compiler.showWarnings=true"


# --- PolicyDecisionRecord construction ---

def _request(**kwargs):
    defaults = dict(action_type=ActionType.RUN_COMMAND)
    defaults.update(kwargs)
    return ActionRequest(**defaults)


def _result(**kwargs):
    defaults = dict(decision=PolicyDecision.DENY, reason_code="SOME_REASON", explanation="some explanation")
    defaults.update(kwargs)
    return PolicyResult(**defaults)


def test_build_decision_record_captures_the_core_fields():
    request = _request(command=("git", "push", "--force"))
    result = _result(decision=PolicyDecision.DENY, reason_code="FORCE_PUSH_DENIED", matched_rule="force_push")
    record = build_decision_record(request, result, enforced=True)

    assert record.action_type == "run_command"
    assert record.decision == "deny"
    assert record.reason_code == "FORCE_PUSH_DENIED"
    assert record.matched_rule == "force_push"
    assert record.enforced is True
    assert record.command_summary == "git push --force"


def test_build_decision_record_never_carries_metadata():
    """metadata is never included at all, not even scrubbed - see module
    docstring for why."""
    request = _request(metadata={"secret": "should-never-appear", "version": "1.2.3"})
    record = build_decision_record(request, _result())
    record_text = json.dumps(record.to_dict())
    assert "should-never-appear" not in record_text
    assert "1.2.3" not in record_text
    assert "metadata" not in record.to_dict()


def test_build_decision_record_scrubs_target_and_network_target():
    request = ActionRequest(
        action_type=ActionType.NETWORK_ACCESS,
        target="config",
        network_target="https://example.com/webhook?api_key=sk-abcdefghijklmnopqrstuvwx1234",
    )
    record = build_decision_record(request, _result())
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in record.network_target_summary


def test_build_decision_record_caps_summary_length():
    long_command = ("echo",) + tuple("x" * 1000 for _ in range(1))
    request = _request(command=long_command)
    record = build_decision_record(request, _result())
    assert len(record.command_summary) <= 300


def test_to_dict_is_json_serializable_and_stable():
    request = _request(target="src/main.py", action_type=ActionType.READ_FILE)
    record = build_decision_record(request, _result(decision=PolicyDecision.ALLOW, reason_code="OK"))
    as_json = record.to_json()
    parsed = json.loads(as_json)
    assert parsed["action_type"] == "read_file"
    assert parsed["decision"] == "allow"


def test_record_is_frozen():
    import dataclasses
    request = _request()
    record = build_decision_record(request, _result())
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.decision = "allow"
