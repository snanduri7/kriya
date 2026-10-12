"""BACKEND-READINESS-V2, S3_A: the container's own host name is not failure identity.

Measured (sealed store of the S3_A run, three full-suite gates of the same tree family): the same 16 tests error with
``UnknownHost <12-hex container id>: <id>: Temporary failure in name resolution`` and the id is different on every contained
run, so the PRE/POST comparison called all 16 CHANGED_FAILURE and blocked each candidate with an unactionable
regression_test failure (no file, low confidence). The output below reproduces the measured Results blocks."""
import pytest

from kriya.workflow.validation_baseline import (
    DeltaClassification,
    ValidationBaseline,
    ValidationInvocation,
    build_validation_outcome,
    classify_baseline_delta,
    normalize_failure_text,
)

CLASS = "StringSubstitutorWithInterpolatorStringLookupTest"
DNS = ("testDnsLookup", "testDnsLookupAddress", "testLocalHostLookup_Address")


def _output(host: str, extra_failure: str = "", reason: str = "Temporary failure in name resolution") -> str:
    entries = "".join(f"[ERROR]   {CLASS}.{t}:147 » UnknownHost {host}: {host}: {reason}\n" for t in DNS)
    total = 1889
    errors = len(DNS) + (1 if extra_failure else 0)
    return (f"[INFO] Running tests\n[ERROR] Tests run: {total}, Failures: 0, Errors: {errors}, Skipped: 4\n[INFO]\n"
            f"[ERROR] Results:\n[ERROR] Errors: \n{entries}{extra_failure}"
            f"[ERROR] Tests run: {total}, Failures: 0, Errors: {errors}, Skipped: 4\n[INFO]\n")


def _raw(output: str) -> dict:
    return {"success": False, "output": output}


def _delta(pre_output: str, post_output: str):
    baseline = ValidationBaseline(workspace_revision="r", run_id="x", invocation=ValidationInvocation("mvn test", "full"),
                                  captured_at=0.0, outcome=build_validation_outcome(_raw(pre_output)))
    return classify_baseline_delta(baseline, build_validation_outcome(_raw(post_output)))


def test_the_same_pre_existing_errors_under_a_new_container_id_are_not_a_change():
    delta = _delta(_output("9018ce6664da"), _output("09f035760c7f"))
    assert delta.blocking is False and delta.level2_available is True
    assert set(delta.level2.values()) == {DeltaClassification.PRE_EXISTING_FAILURE} and len(delta.level2) == len(DNS)
    assert delta.level1.classification == DeltaClassification.PRE_EXISTING_FAILURE


def test_negative_controls_a_real_change_is_still_blocking():
    # a different reason on the same tests is a changed failure
    changed = _delta(_output("9018ce6664da"), _output("09f035760c7f", reason="Connection refused"))
    assert changed.blocking is True and DeltaClassification.CHANGED_FAILURE in set(changed.level2.values())
    # a NEW failing test beside the unchanged ones blocks, whatever the host id
    extra = f"[ERROR]   {CLASS}.testBrandNew:9 » IllegalState boom\n"
    new = _delta(_output("9018ce6664da"), _output("09f035760c7f", extra_failure=extra))
    assert new.blocking is True and new.level1.classification == DeltaClassification.CHANGED_FAILURE


def test_only_a_repeated_container_id_host_is_normalised():
    same = normalize_failure_text("UnknownHost 9018ce6664da: 9018ce6664da: Temporary failure")
    assert same == normalize_failure_text("UnknownHost 09f035760c7f: 09f035760c7f: Temporary failure")
    full = normalize_failure_text("java.net.UnknownHostException: 9018ce6664da: 9018ce6664da: Temporary failure")
    assert full == normalize_failure_text("java.net.UnknownHostException: 09f035760c7f: 09f035760c7f: Temporary failure")
    # any other host name is material: the test now resolves a different thing
    assert normalize_failure_text("UnknownHost foo.invalid: foo.invalid: Name or service not known") != \
        normalize_failure_text("UnknownHost bar.invalid: bar.invalid: Name or service not known")
    # a host appearing once, or two different ids, is not the repeated local-name form
    assert normalize_failure_text("UnknownHost 9018ce6664da: Temporary") != normalize_failure_text("UnknownHost 09f035760c7f: Temporary")
    assert normalize_failure_text("UnknownHost 9018ce6664da: 09f035760c7f: x") != \
        normalize_failure_text("UnknownHost 09f035760c7f: 09f035760c7f: x")
    # a 12-hex token elsewhere in a message is untouched
    assert normalize_failure_text("expected 9018ce6664da but was 09f035760c7f") != \
        normalize_failure_text("expected 09f035760c7f but was 09f035760c7f")


@pytest.mark.parametrize("text", ["UnknownHost 9018ce6664da: 9018ce6664da: x", "UnknownHostException: 9018ce6664da: 9018ce6664da: x"])
def test_the_normalised_text_names_the_failure_kind_and_reason_still(text):
    out = normalize_failure_text(text)
    assert out.startswith("UnknownHost") and out.endswith(": x") and "9018ce6664da" not in out


def test_a_result_recorded_under_the_old_comparison_rules_is_not_the_same_environment():
    """The fingerprint rules changed, so a stored full-suite result from before must not be reused or compared as the same
    run: the comparison version is part of the verification-policy identity."""
    from unittest.mock import patch

    from _strict_doubles import strict_config

    from kriya.workflow import baseline_policy, validation_baseline
    from kriya.workflow.validation_baseline import BASELINE_COMPARISON_VERSION

    autonomy = strict_config().autonomy
    current = baseline_policy.verification_policy_identity(autonomy)
    assert current["comparison_version"] == BASELINE_COMPARISON_VERSION >= 3
    with patch.object(validation_baseline, "BASELINE_COMPARISON_VERSION", 2):
        assert baseline_policy.verification_policy_identity(autonomy) != current
