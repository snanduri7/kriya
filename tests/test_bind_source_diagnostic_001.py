"""DEMO-RESET-REUSES-DOCKER-BIND-ANCESTOR-PATH-001, the product half (owner-approved, OBSERVATION ONLY): when a container backend refuses to start a container with `bind source path does not exist: <path>`, Kriya records
the typed fact "the backend reported the source missing" together with whether the HOST has it, captured when the backend returned and before any Kriya cleanup.

What is pinned: the classification (host-present vs host-absent), that only a run whose target NEVER started can carry it (a started candidate printing the same text cannot forge it), that it reaches the result and the
gate's failure text, and that the verdict stays exactly what it was - the fail-closed verification-infrastructure failure, with NO second gate invocation, NO wait and NO path substitution. The cause of such a
disagreement (the campaign diagnosis) is deliberately not asserted by the product. Since BIND-SOURCE-RELAUNCH-001 the validator's LAUNCH seam relaunches the same contained command once for the host-present
code (tests/test_bind_source_relaunch_001.py); that happens below the seam these gate tests script, and the diagnostic owner itself still retries nothing.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from kriya.tools import validate as validate_module
from kriya.tools.containment import (
    BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST,
    BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST,
    INFRASTRUCTURE_DIAGNOSTIC_CODES,
    LAUNCHER_REACHED_MARKER,
    TARGET_NOT_STARTED_MARKER,
    ContainmentProfile,
    FiniteLaunchStage,
    NetworkAuthority,
    PreparedContainment,
    TrustClass,
    diagnose_bind_source_missing,
    infrastructure_diagnostic_for,
)
from kriya.tools.dependency_execution import MAVEN_INVOCATION_FAILED
from kriya.tools.process import ProcessController
from kriya.tools.validate import PolymorphicValidator

SIGNATURE = 'docker: Error response from daemon: invalid mount config for type "bind": bind source path does not exist: {path}.\nSee \'docker run --help\'.'


def _stderr(path: str, *, vm_prefix: bool = True) -> str:
    return SIGNATURE.format(path=("/host_mnt" + path) if vm_prefix else path)


# ------------------------------------------------------------------------------------------------------------------------------------- the classifier

def test_a_source_that_exists_on_the_host_is_reported_as_missing_by_the_backend_but_present(tmp_path):
    source = tmp_path / "state" / "gate-views" / "view-0123456789abcdef0000061d000001"
    source.mkdir(parents=True)
    diagnostic = diagnose_bind_source_missing(_stderr(str(source)), backend="oci")
    assert diagnostic is not None and diagnostic["reason_code"] == BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST
    assert diagnostic["backend"] == "oci" and diagnostic["reported_source"] == "/host_mnt" + str(source) and diagnostic["host_source"] == str(source)
    facts = diagnostic["host_source_facts"]
    assert facts["exists"] is True and facts["kind"].startswith("d") and facts["is_symlink"] is False and facts["listed_in_parent"] is True and isinstance(facts["inode"], int)
    assert diagnostic["host_parent_facts"]["exists"] is True and diagnostic["host_grandparent_facts"]["exists"] is True
    assert "exists on the host" in diagnostic["meaning"] and "captured_at" in diagnostic


def test_a_source_that_is_absent_on_the_host_is_reported_as_absent_a_kriya_lifecycle_suspect(tmp_path):
    missing = tmp_path / "never-created"
    diagnostic = diagnose_bind_source_missing(_stderr(str(missing)), backend="oci")
    assert diagnostic is not None and diagnostic["reason_code"] == BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST
    assert diagnostic["host_source_facts"]["exists"] is False and "absent on the host" in diagnostic["meaning"]


def test_the_host_spelling_without_the_vm_prefix_and_a_symlinked_source_are_classified_from_the_host_facts(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    plain = diagnose_bind_source_missing(_stderr(str(real), vm_prefix=False), backend=None)
    assert plain["reason_code"] == BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST and plain["backend"] is None
    linked = diagnose_bind_source_missing(_stderr(str(link)), backend="oci")
    assert linked["reason_code"] == BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST and linked["host_source_facts"]["is_symlink"] is True  # the symlink status is recorded, not hidden


@pytest.mark.parametrize("text", [
    "", "docker: Error response from daemon: Minimum memory limit allowed is 6MB\n", "bind source path does not exist\n",
    'docker: Error response from daemon: invalid mount config for type "bind": source path must be a directory\n',
])
def test_any_other_stderr_has_no_diagnostic(text):
    assert diagnose_bind_source_missing(text, backend="oci") is None


def test_the_diagnostic_codes_are_a_closed_table():
    assert INFRASTRUCTURE_DIAGNOSTIC_CODES == {BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST, BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST}


def test_only_a_run_whose_target_never_started_and_did_not_time_out_can_carry_it(tmp_path):
    text = _stderr(str(tmp_path))
    assert infrastructure_diagnostic_for(text, target_started=False, timed_out=False, backend="oci")["reason_code"] == BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST
    assert infrastructure_diagnostic_for(text, target_started=True, timed_out=False, backend="oci") is None   # a started target printing this text is the target's output, never the backend's
    assert infrastructure_diagnostic_for(text, target_started=False, timed_out=True, backend="oci") is None


# --------------------------------------------------------------------------------------------------------- the controller (the facts are captured before cleanup)

_REFUSAL = "import sys; sys.stderr.write(sys.argv[1]); sys.exit(125)"
_LAUNCHER = ("import os, sys\nnonce, argv = sys.argv[1], sys.argv[2:]\nsys.stderr.write('\\n" + LAUNCHER_REACHED_MARKER + "' + nonce + '\\n')\nsys.stderr.flush()\nos.execvp(argv[0], argv)\n")


class _Backend:
    """A scripted container backend: `refuse` = it refuses to start the container (no launcher ever runs); otherwise the real launcher contract runs the target."""
    name = "scripted-oci"

    def __init__(self, refuse_with: str = "", *, cleanup_log=None):
        self.refuse_with, self.cleanup_log = refuse_with, cleanup_log if cleanup_log is not None else []

    def prepare(self, profile, command):  # noqa: ARG002 - the Protocol's signature
        stage = FiniteLaunchStage(prefix=(sys.executable, "-c", _LAUNCHER, "n1"), marker=f"{TARGET_NOT_STARTED_MARKER}n1", reached=f"{LAUNCHER_REACHED_MARKER}n1")
        if self.refuse_with:
            return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=stage, command_prefix=(sys.executable, "-c", _REFUSAL, self.refuse_with),
                                       cleanup=lambda: self.cleanup_log.append("cleanup"))
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=stage)


def _profile(tmp_path):
    return ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path), network=NetworkAuthority.DENIED)


def test_the_controller_attaches_the_diagnostic_with_host_facts_and_changes_nothing_else(tmp_path):
    source = tmp_path / "view-x"
    source.mkdir()
    result = ProcessController().run(["mvn", "validate"], cwd=str(tmp_path), timeout=30, containment_profile=_profile(tmp_path), containment_backend=_Backend(_stderr(str(source))))
    assert result.target_started is False and result.returncode == 125            # the verdict inputs are exactly what they were
    assert "bind source path does not exist" in result.stderr                      # the backend's own text is kept verbatim
    diagnostic = result.infrastructure_diagnostic
    assert diagnostic["reason_code"] == BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST and diagnostic["backend"] == "scripted-oci"
    assert result.to_dict()["infrastructure_diagnostic"] == diagnostic and result.to_dict()["target_started"] is False


def test_the_async_controller_attaches_it_too(tmp_path):
    result = asyncio.run(ProcessController().run_async(["mvn", "validate"], cwd=str(tmp_path), timeout=30, containment_profile=_profile(tmp_path),
                                                       containment_backend=_Backend(_stderr(str(tmp_path / "gone")))))
    assert result.target_started is False and result.infrastructure_diagnostic["reason_code"] == BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST


def test_a_started_candidate_that_prints_the_same_text_cannot_forge_the_diagnostic(tmp_path):
    forged = ["python3", "-c", "import sys; sys.stderr.write(sys.argv[1]); sys.exit(1)", _stderr(str(tmp_path))]
    result = ProcessController().run(forged, cwd=str(tmp_path), timeout=30, containment_profile=_profile(tmp_path), containment_backend=_Backend())
    assert result.target_started is True and result.infrastructure_diagnostic is None and "infrastructure_diagnostic" not in result.to_dict()


def test_an_ordinary_result_has_no_diagnostic_key(tmp_path):
    result = ProcessController().run(["python3", "-c", "print('ok')"], cwd=str(tmp_path), timeout=30, containment_profile=_profile(tmp_path), containment_backend=_Backend())
    assert result.infrastructure_diagnostic is None and "infrastructure_diagnostic" not in result.to_dict()


# ------------------------------------------------------------------------------------------------------ the gate: still fail-closed, one invocation, no wait

def _validator(tmp_path, monkeypatch, outcome):
    (tmp_path / "pom.xml").write_text('<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion></project>\n')
    validator = PolymorphicValidator(str(tmp_path))
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return dict(outcome)

    monkeypatch.setattr(validator, "_run_maven_cmd", runner)
    monkeypatch.setattr(validate_module.time, "sleep", lambda *_a, **_k: pytest.fail("the gate must not wait for the backend"), raising=False)
    return validator, calls


@pytest.mark.parametrize("present", [True, False], ids=["host-present", "host-absent"])
def test_the_pom_validate_gate_stays_an_environment_failure_with_the_typed_note_and_no_retry(tmp_path, monkeypatch, present):
    source = tmp_path / "view-x"
    if present:
        source.mkdir()
    diagnostic = diagnose_bind_source_missing(_stderr(str(source)), backend="oci")
    validator, calls = _validator(tmp_path, monkeypatch, {"returncode": 125, "stdout": "", "stderr": _stderr(str(source)), "timeout": False, "target_started": False, "infrastructure_diagnostic": diagnostic})
    result = validator.run_pom_validate()
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_INVOCATION_FAILED  # fail-closed and NEVER a candidate (POM) failure, whichever way the host facts fell
    assert diagnostic["reason_code"] in result["output"] and "bind source path does not exist" in result["output"]
    assert result["infrastructure_diagnostic"] == diagnostic
    assert len(calls) == 1                                                                           # no automatic retry, no second gate invocation


def test_an_ordinary_never_started_gate_has_no_diagnostic_note(tmp_path, monkeypatch):
    validator, calls = _validator(tmp_path, monkeypatch, {"returncode": 127, "stdout": "", "stderr": "exec: mvn: not found", "timeout": False, "target_started": False})
    result = validator.run_pom_validate()
    assert result["environment_reason_code"] == MAVEN_INVOCATION_FAILED and "infrastructure_diagnostic" not in result and "BACKEND_BIND_SOURCE" not in result["output"] and len(calls) == 1


def test_the_execution_evidence_carries_the_diagnostic(tmp_path):
    diagnostic = diagnose_bind_source_missing(_stderr(str(tmp_path)), backend="oci")
    assert validate_module.execution_evidence({"infrastructure_diagnostic": diagnostic})["infrastructure_diagnostic"] == diagnostic
