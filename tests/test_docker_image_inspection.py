"""DOCTOR-IMAGE-INSPECT-001: a failed `docker image inspect` keeps its real
cause; only the running engine's own answer establishes that an image is
absent.

Every stderr below was captured from Docker 27.5.1 / Docker Desktop on
2026-09-29 (handover evidence: graphify-canonical-qwen38/evidence/
docker_idle_repro/). Every failure exits 1, so the class comes from stderr.
Docker Desktop's resource saver stops the engine VM after 5 idle minutes and
then answers every image inspect from an API cache with a JSON-wrapped 404,
for present and missing images alike; `docker image ls` wakes the engine."""
import subprocess

import pytest

from kriya.tools import containment_oci
from kriya.tools.containment_oci import ImageInspectStatus, classify_inspect_failure, inspect_local_image

IMAGE = "debian:bookworm-slim"
DIGEST = "sha256:32d322b19846336d25f755f73618a448e3621982d52c48064e95af8b3dcbc2d9"
ENGINE_NOT_FOUND = f"Error response from daemon: No such image: {IMAGE}"
CACHED_NOT_FOUND = 'Error response from daemon: {"message":"No such image: ' + IMAGE + '"}'
DAEMON_DOWN = ("Cannot connect to the Docker daemon at unix:///Users/me/.docker/run/docker.sock. "
               "Is the docker daemon running?")
PERMISSION = ("permission denied while trying to connect to the Docker daemon socket at unix:///tmp/x.sock: "
              'Get "http://%2Ftmp%2Fx.sock/v1.47/images/debian:bookworm-slim/json": dial unix /tmp/x.sock: '
              "connect: permission denied")
BAD_REFERENCE = ("Error response from daemon: invalid reference format: repository name (library/Bad) "
                 "must be lowercase")
BAD_CONTEXT = ('Failed to initialize: unable to resolve docker endpoint: context "nope": context not found: '
               "open /Users/me/.docker/contexts/meta/x/meta.json: no such file or directory")


class _Docker:
    """Scripted `docker` answers, in call order; records every argv."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv[1:3])
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        code, out, err = answer
        return subprocess.CompletedProcess(argv, code, out, err)


def _inspect(monkeypatch, *answers):
    docker = _Docker(*answers)
    monkeypatch.setattr(containment_oci.subprocess, "run", docker)
    return inspect_local_image("/usr/local/bin/docker", IMAGE), docker


def test_a_present_image_is_present_with_one_inspect(monkeypatch):
    result, docker = _inspect(monkeypatch, (0, DIGEST + "\n", ""))
    assert result.status is ImageInspectStatus.PRESENT and result.digest == DIGEST
    assert docker.calls == [["image", "inspect"]]


def test_a_missing_image_is_absent_only_after_the_running_engine_says_so(monkeypatch):
    result, docker = _inspect(monkeypatch, (1, "", ENGINE_NOT_FOUND), (0, "", ""), (1, "", ENGINE_NOT_FOUND))
    assert result.status is ImageInspectStatus.IMAGE_NOT_FOUND and result.digest is None
    assert docker.calls == [["image", "inspect"], ["image", "ls"], ["image", "inspect"]]
    assert result.describe() == f"image {IMAGE!r} is not present locally"
    assert result.remediation() == f"Run: docker pull {IMAGE}"


def test_a_cached_404_for_a_present_image_is_rechecked_and_found(monkeypatch):
    """The measured Docker Desktop sequence: cached 404, `image ls` wakes the
    engine, the image is there."""
    result, _ = _inspect(monkeypatch, (1, "", CACHED_NOT_FOUND), (0, "32d322b19846\n", ""), (0, DIGEST + "\n", ""))
    assert result.status is ImageInspectStatus.PRESENT and result.digest == DIGEST
    assert result.first_answer.startswith("DAEMON_UNAVAILABLE")


def test_a_cached_404_that_persists_is_never_reported_as_absence(monkeypatch):
    result, _ = _inspect(monkeypatch, (1, "", CACHED_NOT_FOUND), (0, "", ""), (1, "", CACHED_NOT_FOUND))
    assert result.status is ImageInspectStatus.DAEMON_UNAVAILABLE
    assert "not present" not in result.describe() and "docker pull" not in result.remediation()
    assert result.to_evidence()["stderr"] == CACHED_NOT_FOUND


@pytest.mark.parametrize(("stderr", "status"), [
    (DAEMON_DOWN, ImageInspectStatus.DAEMON_UNAVAILABLE),
    ("Cannot connect to the Docker daemon at tcp://127.0.0.1:1. Is the docker daemon running?",
     ImageInspectStatus.DAEMON_UNAVAILABLE),
    (PERMISSION, ImageInspectStatus.PERMISSION_ERROR),
    (BAD_REFERENCE, ImageInspectStatus.CLIENT_ERROR),
    (BAD_CONTEXT, ImageInspectStatus.CLIENT_ERROR),
    ("Error response from daemon: something new and strange", ImageInspectStatus.UNKNOWN_INSPECT_ERROR),
])
def test_every_other_failure_keeps_its_cause_and_is_never_absence(monkeypatch, stderr, status):
    result, docker = _inspect(monkeypatch, (1, "", stderr))
    assert result.status is status and result.exit_code == 1 and result.stderr == stderr
    assert "not present" not in result.describe() and "docker pull" not in result.remediation()
    assert result.remediation()
    assert docker.calls == [["image", "inspect"]]  # no re-check: nothing claimed absence


def test_a_timeout_and_a_client_launch_failure_are_classified_not_raised(monkeypatch):
    timed_out, _ = _inspect(monkeypatch, subprocess.TimeoutExpired(["docker"], 30))
    assert timed_out.status is ImageInspectStatus.TIMEOUT
    missing_cli, _ = _inspect(monkeypatch, FileNotFoundError("docker"))
    assert missing_cli.status is ImageInspectStatus.CLIENT_ERROR


def test_an_unexpected_success_output_is_unknown_not_present(monkeypatch):
    result, _ = _inspect(monkeypatch, (0, "not-a-digest\n", ""))
    assert result.status is ImageInspectStatus.UNKNOWN_INSPECT_ERROR and result.digest is None


def test_only_the_engines_plain_answer_classifies_as_not_found():
    assert classify_inspect_failure(ENGINE_NOT_FOUND) is ImageInspectStatus.IMAGE_NOT_FOUND
    assert classify_inspect_failure(CACHED_NOT_FOUND) is ImageInspectStatus.DAEMON_UNAVAILABLE


# --- what the doctor and the resume fingerprint report -------------------------------------------

def _unavailable(image, stderr=DAEMON_DOWN):
    return containment_oci.ImageInspection(image, ImageInspectStatus.DAEMON_UNAVAILABLE, exit_code=1,
                                           stderr=stderr)


def test_the_oci_smoke_check_reports_a_stopped_engine_not_a_missing_image(tmp_path):
    from unittest.mock import patch

    from test_production_doctor import _checks, _healthy_boundaries, _production_cfg

    from kriya.production_doctor import CheckStatus, run_production_doctor

    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    from kriya.production_doctor import ContainmentImageUnavailableError

    unavailable = ContainmentImageUnavailableError(_unavailable(IMAGE, CACHED_NOT_FOUND))
    with _healthy_boundaries(), patch("kriya.production_doctor.probe_oci_containment", side_effect=unavailable):
        check = _checks(run_production_doctor(cfg, str(workspace)))["containment.oci_smoke"]
    assert check.status is CheckStatus.UNAVAILABLE
    assert "not present" not in check.evidence["error"]
    assert check.evidence["image_inspection"]["status"] == "DAEMON_UNAVAILABLE"
    assert check.evidence["image_inspection"]["stderr"] == CACHED_NOT_FOUND
    assert "docker pull" not in check.remediation


def test_probe_oci_containment_raises_the_inspection_it_could_not_confirm(monkeypatch):
    from kriya.config import AppConfig
    from kriya.production_doctor import ContainmentImageUnavailableError, probe_oci_containment

    monkeypatch.setattr(containment_oci, "inspect_local_image", lambda docker, image: _unavailable(image))
    with pytest.raises(ContainmentImageUnavailableError) as raised:
        probe_oci_containment(AppConfig(), "/usr/local/bin/docker", None)
    assert raised.value.inspection.status is ImageInspectStatus.DAEMON_UNAVAILABLE
    assert "not present" not in str(raised.value)


def test_the_toolchain_check_reports_a_stopped_engine_not_a_missing_image(tmp_path):
    from unittest.mock import patch

    from test_production_doctor import _checks, _git_workspace, _pom, _run

    from kriya.production_doctor import CheckStatus

    workspace = _git_workspace(tmp_path / "workspace")
    _pom(workspace, 17)
    with patch("kriya.tools.containment_oci.inspect_local_image", side_effect=lambda docker, image: _unavailable(image)), \
         patch("kriya.tools.containment_oci._attest_toolchain_image") as attest:
        check = _checks(_run(tmp_path, workspace=workspace))["toolchain.required"]
    attest.assert_not_called()
    assert check.status is CheckStatus.UNAVAILABLE
    assert "not present" not in check.evidence["error"]
    assert check.evidence["image_inspection"]["status"] == "DAEMON_UNAVAILABLE"
    assert check.remediation.startswith("Start the Docker daemon")


def test_the_resume_fingerprint_names_the_real_inspect_failure(tmp_path, monkeypatch):
    from kriya.config import AppConfig
    from kriya.workflow.resume_fingerprints import generation_resume_fingerprints

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nrequires-python = ">=3.12"\n')
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(containment_oci, "local_image_inspection", lambda image: _unavailable(image))
    config = AppConfig()
    config.autonomy.contained_execution_required = True
    config.autonomy.containment_backend = "oci"
    toolchain = generation_resume_fingerprints(config, str(tmp_path), goal="g")["toolchain"]
    assert not toolchain.available
    assert "DAEMON_UNAVAILABLE" in toolchain.basis and "not present" not in toolchain.basis
