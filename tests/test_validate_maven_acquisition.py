"""SEC-001 live-validation follow-up (2026-09-11): deterministic coverage
for `PolymorphicValidator._run_maven_cmd`'s bounded acquisition/offline
retry state machine - kriya/tools/validate.py. Complements the real-Docker
integration coverage in tests/test_validate_oci.py (which proves the fix
actually resolves lifecycle plugins offline) with fast, mocked tests that
pin the EXACT call sequence/bounding, since that's easier to verify
precisely without a real Maven Central round-trip for every case.

No Docker required - `_run_cmd_with_timeout` is mocked directly."""
from unittest.mock import patch

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import NetworkAuthority
from kriya.tools.validate import PolymorphicValidator

_MISSING_DEP_STDERR = (
    "[ERROR] Failed to execute goal: Could not resolve dependencies\n"
    "[ERROR] dependency: org.junit.jupiter:junit-jupiter:jar:5.10.2 (test)\n"
    "[ERROR] \tCannot access central (https://repo.maven.apache.org/maven2) in offline mode "
    "and the artifact org.junit.jupiter:junit-jupiter:jar:5.10.2 has not been downloaded from it before.\n"
)
_DIFFERENT_MISSING_DEP_STDERR = (
    "[ERROR] Failed to execute goal: Could not resolve dependencies\n"
    "[ERROR] dependency: org.apache.maven.plugins:maven-surefire-plugin:jar:3.2.5 (test)\n"
    "[ERROR] \tCannot access central (https://repo.maven.apache.org/maven2) in offline mode "
    "and the artifact org.apache.maven.plugins:maven-surefire-plugin:jar:3.2.5 has not been downloaded from it before.\n"
)
_ORDINARY_FAILURE_STDERR = "[ERROR] COMPILATION ERROR :\n[ERROR] cannot find symbol\n"


def _contained_validator(tmp_path) -> PolymorphicValidator:
    (tmp_path / "pom.xml").write_text("<project></project>")
    return PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )


def test_run_maven_cmd_host_mode_is_exact_passthrough(tmp_path):
    """contained_execution_required=False: byte-for-byte unchanged - no
    -o/-Dmaven.repo.local flags added, exactly one call, exact original
    goals."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig())
    with patch.object(
        validator, "_run_cmd_with_timeout", return_value={"returncode": 0, "stdout": "", "stderr": ""},
    ) as mock_run:
        result = validator._run_maven_cmd(["validate"], cwd=str(tmp_path), timeout=120)

    assert result["returncode"] == 0
    mock_run.assert_called_once_with(["mvn", "validate"], cwd=str(tmp_path), timeout=120)


def test_run_maven_cmd_offline_success_never_acquires(tmp_path):
    """A cache that's already warm (or a goal needing nothing new) must
    succeed on the first offline attempt with NO acquisition call at
    all - proves ordinary/already-satisfied dependency resolution stays
    cheap and never unnecessarily re-acquires."""
    validator = _contained_validator(tmp_path)
    with patch.object(
        validator, "_run_cmd_with_timeout", return_value={"returncode": 0, "stdout": "BUILD SUCCESS", "stderr": ""},
    ) as mock_run:
        result = validator._run_maven_cmd(["validate"], cwd=str(tmp_path), timeout=120)

    assert result["returncode"] == 0
    assert mock_run.call_count == 1
    assert mock_run.call_args.kwargs["network"] == NetworkAuthority.DENIED


def test_run_maven_cmd_ordinary_failure_never_triggers_acquisition(tmp_path):
    """A genuine code-level failure (not a missing-dependency signature)
    must return immediately - no acquisition call, no retry. Acquisition
    is reserved for a real MISSING_DEPENDENCY classification only."""
    validator = _contained_validator(tmp_path)
    with patch.object(
        validator, "_run_cmd_with_timeout",
        return_value={"returncode": 1, "stdout": "", "stderr": _ORDINARY_FAILURE_STDERR},
    ) as mock_run:
        result = validator._run_maven_cmd(["compile"], cwd=str(tmp_path), timeout=120)

    assert result["returncode"] == 1
    assert mock_run.call_count == 1


def test_run_maven_cmd_bounded_reacquisition_then_offline_success(tmp_path):
    """First offline attempt misses a dependency -> exactly ONE
    acquisition (network=UNRESTRICTED, the SAME goals) -> exactly one
    more offline attempt (network=DENIED), which succeeds. Exactly 3
    calls total, never more."""
    validator = _contained_validator(tmp_path)
    responses = [
        {"returncode": 1, "stdout": "", "stderr": _MISSING_DEP_STDERR},  # first offline
        {"returncode": 0, "stdout": "BUILD SUCCESS (acquisition)", "stderr": ""},  # acquisition
        {"returncode": 0, "stdout": "BUILD SUCCESS", "stderr": ""},  # second offline
    ]
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=responses) as mock_run:
        result = validator._run_maven_cmd(["test"], cwd=str(tmp_path), timeout=120)

    assert result["returncode"] == 0
    assert result["stdout"] == "BUILD SUCCESS"  # the OFFLINE retry's result, not the acquisition's
    assert mock_run.call_count == 3
    networks_used = [c.kwargs["network"] for c in mock_run.call_args_list]
    assert networks_used == [NetworkAuthority.DENIED, NetworkAuthority.UNRESTRICTED, NetworkAuthority.DENIED]
    # Same real goal used for acquisition as for the authoritative attempts
    # - never a static plugin list, never a different, narrower proxy goal.
    for call in mock_run.call_args_list:
        assert call.args[0][-1] == "test"


def test_run_maven_cmd_acquisition_result_never_returned_as_pass(tmp_path):
    """The acquisition call (network=UNRESTRICTED) reporting success must
    NEVER be mistaken for Quality Gate PASS evidence - only a SUBSEQUENT
    offline (network=DENIED) attempt's own result is ever returned. Here
    the acquisition step (with network) would "pass", but the real
    offline retry afterward still fails - the function must return the
    OFFLINE failure, not the acquisition's success."""
    validator = _contained_validator(tmp_path)
    responses = [
        {"returncode": 1, "stdout": "", "stderr": _MISSING_DEP_STDERR},  # first offline: miss
        {"returncode": 0, "stdout": "BUILD SUCCESS (acquisition only)", "stderr": ""},  # acquisition: "passes"
        {"returncode": 1, "stdout": "", "stderr": _ORDINARY_FAILURE_STDERR},  # offline retry: a REAL code failure
    ]
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=responses) as mock_run:
        result = validator._run_maven_cmd(["test"], cwd=str(tmp_path), timeout=120)

    assert result["returncode"] == 1
    assert "BUILD SUCCESS (acquisition only)" not in result["stdout"]
    assert mock_run.call_count == 3


def test_run_maven_cmd_repeated_identical_missing_artifact_terminates_deterministically(tmp_path):
    """Offline still reports the SAME missing artifact after the one
    bounded reacquisition - must terminate deterministically (a
    distinguishing MAVEN_ACQUISITION_INCOMPLETE marker on the returned
    result) rather than looping - exactly 3 calls, never a third
    acquisition."""
    validator = _contained_validator(tmp_path)
    responses = [
        {"returncode": 1, "stdout": "", "stderr": _MISSING_DEP_STDERR},  # first offline
        {"returncode": 0, "stdout": "BUILD SUCCESS (acquisition)", "stderr": ""},  # acquisition (ineffective)
        {"returncode": 1, "stdout": "", "stderr": _MISSING_DEP_STDERR},  # second offline: SAME artifact again
    ]
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=responses) as mock_run:
        result = validator._run_maven_cmd(["test"], cwd=str(tmp_path), timeout=120)

    assert result["returncode"] == 1
    assert mock_run.call_count == 3  # no third acquisition, no loop
    assert "MAVEN_ACQUISITION_INCOMPLETE:" in result["stderr"]


def test_run_maven_cmd_different_missing_artifact_after_reacquisition_not_flagged_incomplete(tmp_path):
    """If the SECOND offline attempt names a DIFFERENT missing artifact
    than the first, that's still an ordinary (if unlucky) missing-
    dependency result, not "repeated identical evidence" - no
    MAVEN_ACQUISITION_INCOMPLETE marker, and still exactly 3 calls (this
    method's own bound is "at most one acquisition per call", not "keep
    acquiring until nothing is missing")."""
    validator = _contained_validator(tmp_path)
    responses = [
        {"returncode": 1, "stdout": "", "stderr": _MISSING_DEP_STDERR},
        {"returncode": 0, "stdout": "BUILD SUCCESS (acquisition)", "stderr": ""},
        {"returncode": 1, "stdout": "", "stderr": _DIFFERENT_MISSING_DEP_STDERR},
    ]
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=responses) as mock_run:
        result = validator._run_maven_cmd(["test"], cwd=str(tmp_path), timeout=120)

    assert result["returncode"] == 1
    assert mock_run.call_count == 3
    assert "MAVEN_ACQUISITION_INCOMPLETE:" not in result["stderr"]
