"""kriya/workflow/failure_grounding.py - first direct test coverage for
detect_process_termination_signature()/_build_test_quality_gate_failure()
(PRV-06, 2026-08-28)."""

from kriya.workflow.failure_grounding import (
    _build_test_quality_gate_failure,
    detect_process_termination_signature,
)


# --- detect_process_termination_signature ---

def test_detects_surefire_booter_fork_exception():
    output = (
        "[ERROR] org.apache.maven.surefire.booter.SurefireBooterForkException: "
        "The forked VM terminated without properly saying goodbye. VM crash or System.exit called?"
    )
    assert detect_process_termination_signature(output) == "SurefireBooterForkException"


def test_detects_forked_vm_terminated_phrase():
    output = "[ERROR] The forked VM terminated without properly saying goodbye. VM crash or System.exit called?"
    signature = detect_process_termination_signature(output)
    assert signature in ("forked VM terminated", "VM crash or System.exit called?")


def test_ordinary_assertion_failure_has_no_signature():
    output = "org.opentest4j.AssertionFailedError: expected: <HELLO> but was: <hello>"
    assert detect_process_termination_signature(output) is None


def test_ordinary_compile_error_has_no_signature():
    output = "[ERROR] cannot find symbol\n  symbol: class Foo\n  location: class Bar"
    assert detect_process_termination_signature(output) is None


def test_empty_output_has_no_signature():
    assert detect_process_termination_signature("") is None
    assert detect_process_termination_signature(None) is None


# --- _build_test_quality_gate_failure ---

def test_process_termination_output_upgrades_type_and_message():
    failure = _build_test_quality_gate_failure(
        "test", "TEST FAILURE:\n...raw...",
        "org.apache.maven.surefire.booter.SurefireBooterForkException: "
        "The forked VM terminated without properly saying goodbye. VM crash or System.exit called?",
        "/tmp/work", ["AppTest.java"], 3,
    )
    assert failure.type == "test_process_terminated"
    assert failure.message.startswith("TEST_PROCESS_TERMINATED (evidence: 'SurefireBooterForkException'):")
    assert "structural" in failure.message
    assert "single-file edit" in failure.message
    assert "cannot resolve this" in failure.message


def test_ordinary_test_output_keeps_generic_type_and_message():
    failure = _build_test_quality_gate_failure(
        "test", "TEST FAILURE:\n...raw...",
        "expected <HELLO> but was <hello>",
        "/tmp/work", ["AppTest.java"], 3,
    )
    assert failure.type == "test"
    assert failure.message == "TEST FAILURE:\n...raw..."


def test_targeted_test_type_also_upgrades_on_process_termination():
    failure = _build_test_quality_gate_failure(
        "targeted_test", "TARGETED TEST FAILURE:\n...raw...",
        "forked VM terminated without properly saying goodbye",
        "/tmp/work", ["AppTest.java"], 5,
    )
    assert failure.type == "test_process_terminated"


def test_raw_output_field_stays_the_pure_tool_output_not_the_guidance():
    raw = "SurefireBooterForkException: VM crash or System.exit called?"
    failure = _build_test_quality_gate_failure(
        "test", "TEST FAILURE:\n" + raw, raw, "/tmp/work", ["AppTest.java"], 1,
    )
    assert failure.raw_output == raw


def test_surefire_crashed_test_targets_only_incompatible_test_strategy():
    raw = (
        "SurefireBooterForkException: VM crash or System.exit called?\n"
        "Crashed tests:\n"
        "[ERROR] com.example.AppTest\n"
        "at com.example.App.main(App.java:12)"
    )
    failure = _build_test_quality_gate_failure(
        "test", "TEST FAILURE", raw, "/tmp/work",
        ["src/main/java/com/example/App.java", "src/test/java/com/example/AppTest.java"], 1,
    )

    assert failure.type == "verification_strategy_incompatible"
    assert failure.likely_files == ["src/test/java/com/example/AppTest.java"]
    assert failure.diagnostics["reason_code"] == "VERIFICATION_STRATEGY_INCOMPATIBLE"
    assert "Do not change or remove the product's required process-exit behavior" in failure.message
    assert "SecurityManager" in failure.message
