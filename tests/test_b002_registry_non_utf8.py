"""RDCG B-002 (owner decision 2026-10-28): a contract registry whose bytes are not UTF-8 is the existing typed corrupt-registry refusal, exactly like bytes that are not JSON.

MEASURED before the fix (WAVE2_MALFORMED_STATE_CHARACTERIZATION_2026-10-28): ``load_contract_registry`` caught ``OSError`` only around ``handle.read()``, so the ``UnicodeDecodeError`` escaped
(1) ``derive_contract_transition`` -> ``commit_terminal_candidate`` -> the attempt loop's broad ``except Exception``, which recorded a failed ATTEMPT and entered Developer repair until the retry budgets
ran out (``quality_gates_exhausted``), and (2) ``contract_registry_fingerprint`` (``OSError``/typed errors only) -> ``workflow.py``'s broad catch, which dropped every resume fingerprint of every
checkpoint ("it will not be resumable"). ASCII garbage in the same file already produced the typed ``contract_registry_blocked`` stop and an UNAVAILABLE fingerprint.

The exact number of scripted Developer calls is NOT an invariant here; the end-to-end test pins that the invalid-UTF-8 run behaves like its ASCII-garbage control."""
import json
import logging
import os

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
    requested_file,
    run_direct,
)

from kriya.control.contracts import CONTRACT_REGISTRY_CORRUPT, ContractRegistryCorruptError
from kriya.control.persistence import contract_registry_path, load_contract_registry, scan_run_records
from kriya.workflow.resume_fingerprints import (
    FingerprintStatus,
    compare_resume_fingerprints,
    contract_registry_fingerprint,
    fingerprint_block,
)

ASCII_GARBAGE = b"{{ not json"
INVALID_UTF8 = {
    "lone_invalid_start_byte": b'{"schema_version": 1}\xff\xfe',
    "truncated_multibyte_sequence": b'{"contracts": {}, "note": "\xe2\x82',
    "utf16_with_bom": b'\xff\xfe{\x00}\x00',
}
DESIGN = "Design: sub in calc.py, a constant in helper.py.\n```json\n" + json.dumps({"files": ["calc.py", "helper.py"]}) + "\n```\n"
RESUME_WARNING = "Could not compute resume fingerprints"
ATTEMPT_VERDICT = "OVERALL ATTEMPT - Attempt"


def _write_registry(workspace, data: bytes) -> str:
    path = contract_registry_path(str(workspace))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _bytes_of(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


@pytest.mark.parametrize("data", [ASCII_GARBAGE, *INVALID_UTF8.values()], ids=["ascii_garbage_control", *INVALID_UTF8])
def test_unreadable_registry_bytes_are_the_typed_corrupt_refusal_and_left_untouched(tmp_path, data):
    path = _write_registry(tmp_path, data)
    with pytest.raises(ContractRegistryCorruptError) as exc_info:
        load_contract_registry(str(tmp_path))
    assert exc_info.value.reason_code == CONTRACT_REGISTRY_CORRUPT
    assert exc_info.value.path == path
    assert _bytes_of(path) == data


@pytest.mark.parametrize("data", INVALID_UTF8.values(), ids=list(INVALID_UTF8))
def test_the_encoding_cause_is_named_in_the_detail_not_in_a_new_code(tmp_path, data):
    _write_registry(tmp_path, data)
    with pytest.raises(ContractRegistryCorruptError) as exc_info:
        load_contract_registry(str(tmp_path))
    assert "UTF-8" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)
    assert exc_info.value.reason_code == CONTRACT_REGISTRY_CORRUPT


def test_an_absent_registry_is_still_the_valid_empty_registry(tmp_path):
    assert load_contract_registry(str(tmp_path)).all_records() == ()
    assert contract_registry_fingerprint(str(tmp_path)).available


@pytest.mark.parametrize("data", [ASCII_GARBAGE, *INVALID_UTF8.values()], ids=["ascii_garbage_control", *INVALID_UTF8])
def test_the_resume_fingerprint_is_typed_unavailable_never_raised_or_dropped(tmp_path, data):
    _write_registry(tmp_path, data)
    fingerprint = contract_registry_fingerprint(str(tmp_path))
    assert not fingerprint.available
    assert fingerprint.basis.startswith("contract registry unreadable")


@pytest.mark.parametrize("data", INVALID_UTF8.values(), ids=list(INVALID_UTF8))
def test_a_checkpoint_saved_under_a_valid_registry_is_unverified_not_resumable_under_unreadable_bytes(tmp_path, data):
    saved = tmp_path / "saved"
    saved.mkdir()
    stored = fingerprint_block({"contract_registry": contract_registry_fingerprint(str(saved))})
    now = tmp_path / "now"
    now.mkdir()
    _write_registry(now, data)
    comparisons = {c.name: c for c in compare_resume_fingerprints(stored, {"contract_registry": contract_registry_fingerprint(str(now))}, ["plan"])}
    assert comparisons["contract_registry"].status is FingerprintStatus.UNVERIFIED
    assert comparisons["contract_registry"].invalidates
    assert "unavailable now: contract registry unreadable" in comparisons["contract_registry"].reason


def _scripted_run(root, name: str, registry_bytes: bytes):
    base = os.path.join(root, name)
    os.makedirs(base)
    workspace = git_workspace(base, {"calc.py": CALC, "helper.py": "X = 1\n", "test_calc.py": TEST_SUB})
    _write_registry(workspace, registry_bytes)

    def responder(role, request):
        if role == "architect":
            return DESIGN
        if role == "developer":
            return "X = 2\n" if requested_file(request) == "helper.py" else CALC_WITH_SUB
        return benign_roles(role, request)

    runtime = ChaosRuntime(responder)
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    with open(os.path.join(workspace, "calc.py"), encoding="utf-8") as handle:
        untouched = handle.read() == CALC
    return {
        "quality_gates_passed": result.get("quality_gates_passed"),
        "failure_category": result.get("failure_category"),
        "developer_calls": runtime.roles.count("developer"),
        "workspace_untouched": untouched,
        "run_record": [(r.lifecycle_state.value, r.commit_result) for r in scan_run_records(str(workspace)).records],
        "registry_bytes_untouched": _bytes_of(contract_registry_path(str(workspace))) == registry_bytes,
    }


@pytest.mark.parametrize("data", INVALID_UTF8.values(), ids=list(INVALID_UTF8))
def test_an_invalid_utf8_registry_stops_a_run_like_malformed_json_without_repair_or_lost_resumability(tmp_path, caplog, data):
    """The reproducer of the measured symptom: the real generation pipeline over a registry file the Developer cannot have caused."""
    with caplog.at_level(logging.INFO):
        control = _scripted_run(str(tmp_path), "ascii", ASCII_GARBAGE)
        control_resume_warnings = [r for r in caplog.records if RESUME_WARNING in r.getMessage()]
        control_attempts = [r for r in caplog.records if ATTEMPT_VERDICT in r.getMessage()]
        caplog.clear()
        observed = _scripted_run(str(tmp_path), "utf8", data)
        observed_resume_warnings = [r for r in caplog.records if RESUME_WARNING in r.getMessage()]
        observed_attempts = [r for r in caplog.records if ATTEMPT_VERDICT in r.getMessage()]
    assert control["failure_category"] == "contract_registry_blocked"
    assert observed == control
    assert observed["failure_category"] != "quality_gates_exhausted"
    assert observed["run_record"] == [("FAILURE", "NOT_COMMITTED")]
    assert observed["workspace_untouched"] and observed["registry_bytes_untouched"]
    assert control_resume_warnings == [] and observed_resume_warnings == []
    # the decode error enters no attempt/repair cycle of its own: the run ends in the same number of attempts as its malformed-JSON control
    assert len(observed_attempts) == len(control_attempts)
