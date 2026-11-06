"""Project health rows (kriya/application/readiness.py): the capabilities of a folder that no other KUP answer states, each with a closed state, a closed reason code
and the configuration fields / observations that decided it. They are read from the folder's RESOLVED configuration and read-only observations, never from a claim about
a run. Containment is mandatory with no operator opt-out (the configuration refuses any other value), and the language-server row reports what the product really does
(jdtls has no contained mode, so it is never started)."""
import os
import stat

import pytest

from kriya.application import readiness as health
from kriya.config import AppConfig
from kriya.config.authority import ContainmentOptOutError
from kriya.platform import private_store as ps


def rows(cfg=None, state_directory=None):
    return {r["id"]: r for r in health.capability_rows(AppConfig() if cfg is None else cfg, state_directory=state_directory)}


def configured(**autonomy):
    cfg = AppConfig()
    return cfg.model_copy(update={"autonomy": cfg.autonomy.model_copy(update=autonomy)})


def test_the_packaged_defaults_report_mandatory_containment_verification_and_a_disabled_lookup():
    r = rows()
    assert (r["security"]["state"], r["security"]["reason_code"]) == (health.AVAILABLE, "CONTAINMENT_REQUIRED")
    assert "network denied" in r["security"]["detail"] and "no setting that turns this off" in r["security"]["detail"] and "protected paths" in r["security"]["detail"]
    assert (r["lookup"]["state"], r["lookup"]["reason_code"]) == (health.NOT_REQUIRED, "LOOKUP_DISABLED")
    assert (r["verification"]["state"], r["verification"]["reason_code"]) == (health.AVAILABLE, "RUN_VERIFICATION_ENABLED")
    assert (r["recovery"]["state"], r["recovery"]["reason_code"]) == (health.AVAILABLE, "RETRY_BUDGET_CONFIGURED")
    assert (r["code_intelligence"]["state"], r["code_intelligence"]["reason_code"]) == (health.AVAILABLE, "STRUCTURAL_INDEX")


def test_the_row_for_mandatory_containment_is_grounded_in_a_refusal_the_configuration_really_makes():
    """The security row says there is no opt-out; this is the product refusing one, so the sentence cannot drift from the behaviour."""
    with pytest.raises(ContainmentOptOutError):
        configured(contained_execution_required=False)


def test_every_row_has_a_closed_state_a_reason_code_and_evidence():
    for cfg in (AppConfig(), configured(run_verification_enabled=False), configured(web_lookup_enabled=True), None):
        for row in health.capability_rows(cfg, state_directory=None):
            assert row["state"] in health.STATES and row["reason_code"] and row["detail"] and set(row) == {"id", "label", "state", "reason_code", "detail", "evidence"}
            if cfg is not None:
                assert row["evidence"], row["id"]


def test_switching_run_verification_off_is_unavailable_with_its_consequence_stated():
    row = rows(configured(run_verification_enabled=False))["verification"]
    assert (row["state"], row["reason_code"]) == (health.UNAVAILABLE, "RUN_VERIFICATION_DISABLED") and "ends unsuccessfully rather than unverified" in row["detail"]


def test_an_enabled_lookup_is_available_only_under_a_local_only_egress_policy_and_degraded_otherwise():
    local = rows(configured(web_lookup_enabled=True, egress_policy="local_only"))["lookup"]
    assert (local["state"], local["reason_code"]) == (health.AVAILABLE, "LOOKUP_ENABLED_LOCAL_ONLY")
    assert "sanitized" in local["detail"] and "private repository context is not sent" in local["detail"]
    open_policy = rows(configured(web_lookup_enabled=True, egress_policy="unrestricted"))["lookup"]
    assert (open_policy["state"], open_policy["reason_code"]) == (health.DEGRADED, "LOOKUP_ENABLED_EGRESS_OPEN")


def test_the_language_server_is_never_available_because_it_has_no_contained_mode():
    row = rows()["lsp"]
    assert (row["state"], row["reason_code"]) == (health.UNAVAILABLE, "JDTLS_NOT_CONTAINABLE") and "no contained mode" in row["detail"] and "structural index" in row["detail"]


def test_an_unloaded_configuration_is_unknown_everywhere_never_guessed():
    out = health.capability_rows(None, state_directory="/x")
    assert {r["id"] for r in out} == {"security", "private_state", "lookup", "verification", "recovery", "lsp", "code_intelligence"}
    assert {(r["state"], r["reason_code"]) for r in out} == {(health.UNKNOWN, health.CONFIG_UNAVAILABLE)}


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
class TestPrivateState:
    def test_a_private_own_directory_is_verified(self, tmp_path):
        d = tmp_path / "state"
        d.mkdir()
        os.chmod(d, 0o700)
        assert ps.inspect_private_directory(str(d)) == ps.PRIVATE
        row = rows(state_directory=str(d))["private_state"]
        assert (row["state"], row["reason_code"]) == (health.AVAILABLE, "PRIVATE_STATE_VERIFIED")

    def test_an_open_directory_is_degraded_and_the_observation_changes_nothing(self, tmp_path):
        d = tmp_path / "state"
        d.mkdir()
        os.chmod(d, 0o755)
        row = rows(state_directory=str(d))["private_state"]
        assert (row["state"], row["reason_code"]) == (health.DEGRADED, "STATE_DIRECTORY_NOT_PRIVATE")
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o755, "reading is not tightening"

    def test_a_missing_directory_is_unknown_and_is_not_created(self, tmp_path):
        d = tmp_path / "nope"
        assert rows(state_directory=str(d))["private_state"]["reason_code"] == "STATE_DIRECTORY_NOT_CREATED" and not d.exists()
        assert rows(state_directory=None)["private_state"]["state"] == health.UNKNOWN

    def test_a_symlink_or_a_file_is_unsafe_and_never_followed(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        os.chmod(target, 0o700)
        link = tmp_path / "link"
        link.symlink_to(target)
        plain = tmp_path / "file"
        plain.write_text("x")
        assert ps.inspect_private_directory(str(link)) == ps.UNSAFE and ps.inspect_private_directory(str(plain)) == ps.UNSAFE
        assert rows(state_directory=str(link))["private_state"]["reason_code"] == "STATE_DIRECTORY_UNSAFE"
