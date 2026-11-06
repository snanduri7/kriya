"""SECURITY DEMONSTRATION (kriya/policy/security_demonstration.py): the protections shown by running them. Every row is the canonical owner's own decision about a situation built in a
disposable folder; these tests pin the expected decisions (so a weakened protection turns a BLOCKED row into ALLOWED and fails here), that nothing leaks (no path, no decoy content, no
secret), that the disposable folder is gone, and that the rows are deterministic."""
import glob
import json
import os
import tempfile

from kriya.policy import security_demonstration as demo

EXPECTED = {
    "write_inside_scope": (demo.ALLOWED, "PATH_WITHIN_WORKSPACE_ALLOWED", "filesystem.within_workspace_allowed"),
    "write_outside_scope": (demo.BLOCKED, "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS", "filesystem.authorized_writer.outside_scope"),
    "write_through_symlink": (demo.BLOCKED, "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS", "filesystem.authorized_writer.outside_scope"),
    "write_control_state": (demo.BLOCKED, "TRUSTED_CONTROL_PATH_DENIED", "filesystem.authorized_writer.trusted_control_path"),
    "write_credential_file": (demo.BLOCKED, "SENSITIVE_PATH_DENIED", "filesystem.sensitive_path_denied"),
    "read_through_hard_link": (demo.BLOCKED, "HARD_LINK_OUTSIDE_AUTHORIZED_READABLE_ROOTS", "filesystem.authorized_reader.hard_link_outside_scope"),
    "read_outside_scope": (demo.BLOCKED, "PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS", "filesystem.authorized_reader.outside_scope"),
    "redact_secret": (demo.REDACTED, "SECRET_REDACTED", "kriya.foundation.redaction.redact_text"),
}


def test_each_protection_gives_the_owners_typed_decision_in_presentation_order():
    rows = demo.demonstrate()
    assert [r["id"] for r in rows] == list(EXPECTED)
    for row in rows:
        assert (row["outcome"], row["reason_code"], row["authority"]) == EXPECTED[row["id"]], row["id"]
        assert set(row) == {"id", "protection", "requested", "outcome", "reason_code", "authority", "explanation"} and row["outcome"] in demo.OUTCOMES
        assert row["protection"] and row["requested"] and row["explanation"]


def test_the_demonstration_is_not_a_blanket_refusal_one_row_is_allowed():
    outcomes = {r["id"]: r["outcome"] for r in demo.demonstrate()}
    assert outcomes["write_inside_scope"] == demo.ALLOWED and sum(1 for o in outcomes.values() if o == demo.BLOCKED) == 6


def test_nothing_leaks_no_path_no_decoy_content_no_secret():
    text = json.dumps(demo.demonstrate())
    assert tempfile.gettempdir() not in text and "kriya-security-demo-" not in text and "/private/" not in text and "/Users/" not in text
    assert "DECOY" not in text and "ghp_" not in text and "a1B2c3D4" not in text
    assert "(40 characters)" in text or "characters)" in text      # only the length of the masked string is reported


def test_the_disposable_folder_is_removed_and_the_calls_are_deterministic():
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "kriya-security-demo-*")))
    first, second = demo.demonstrate(), demo.demonstrate()
    assert first == second
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "kriya-security-demo-*"))) == before


def test_the_rows_report_the_owners_real_answer_not_a_canned_one(monkeypatch):
    """With the owner's primary scope check defeated, the 'outside the project' row does not keep its canned reason: it shows whatever the owner then answers (a second layer still
    refuses it, by a different rule), and with BOTH layers defeated the row stops being BLOCKED. The text is derived from the decision, never asserted."""
    import kriya.policy.filesystem as fs
    expected = EXPECTED["write_outside_scope"]
    monkeypatch.setattr(fs, "is_within_scope", lambda scope, target: True)
    rows = {r["id"]: r for r in demo.demonstrate()}
    assert (rows["write_outside_scope"]["outcome"], rows["write_outside_scope"]["reason_code"], rows["write_outside_scope"]["authority"]) != expected
    from kriya.policy.execution import ExecutionPolicy
    from kriya.policy.model import PolicyDecision, PolicyResult
    monkeypatch.setattr(ExecutionPolicy, "evaluate", lambda self, request: PolicyResult(decision=PolicyDecision.ALLOW, reason_code="X", explanation="x", matched_rule="x"), raising=False)
    weakened = {r["id"]: r for r in demo.demonstrate()}
    assert weakened["write_outside_scope"]["outcome"] != demo.BLOCKED or weakened["write_outside_scope"]["authority"] != expected[2]
