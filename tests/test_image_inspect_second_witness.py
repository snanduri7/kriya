"""IMAGE-INSPECT-FALSE-UNAVAILABLE-001: `docker image inspect <reference>` answering "No such image" is not, by itself, proof that the image is absent.

MEASURED (Docker Desktop 27.5.1, 2026-10-25 and again 2026-10-26): inspect-by-reference kept answering the JSON-wrapped "No such image: maven:3.9-eclipse-temurin-17" while
`docker image ls --no-trunc` named the image with its full id and `docker image inspect <that id>` succeeded. The production consequence (scripted docker, 2026-10-26):
`_attest_toolchain_image(allow_pull=True)` read "no digest" as "absent", ran `docker pull`, and offline refused a toolchain image that was present.

The contract kept by the fix: the healthy path is untouched; ONLY the recognized absence condition consults the listing; the listing is a SECOND WITNESS of local presence that
yields the immutable id, and the attestation then proceeds through that id (the same `.Id` the reference would have given, which the contained run uses); zero matches leave the
existing behaviour; an ambiguous / malformed / partial / contradicted listing is the typed IMAGE_WITNESS_CONFLICT, never a pull and never an attestation.
The docker below is scripted by argv (not by call order) and answers exactly the measured outputs."""
import subprocess

import pytest

from kriya.tools import containment_oci
from kriya.tools.containment_oci import (
    BackendUnavailableError,
    ImageInspectStatus,
    _attest_toolchain_image,
    _ensure_image,
    inspect_local_image,
)
from kriya.tools.toolchain_identity import resolve_toolchain_identity

JDK17, JDK21 = "maven:3.9-eclipse-temurin-17", "maven:3.9-eclipse-temurin-21"
ID17 = "sha256:" + "17" * 32
ID21 = "sha256:" + "21" * 32
ID17B = "sha256:" + "1b" * 32      # another local image that is also a JDK 17 image
WRAPPED = 'Error response from daemon: {"message":"No such image: %s"}'
PLAIN = "Error response from daemon: No such image: %s"
OFFLINE = "Error response from daemon: Get https://registry-1.docker.io/v2/: dial tcp: lookup registry-1.docker.io: no such host"


def _listing(*rows):
    return "".join(f"{repo}\t{tag}\t{image_id}\n" for repo, tag, image_id in rows)


class Docker:
    """A scripted `docker`, answering by argv. ``images``: reference -> full id. ``false_absent``: references whose inspect-by-reference answers "No such image" although the
    image exists (the measured window). ``listing``: the exact `image ls --no-trunc` stdout (default: derived from ``images``). ``vanished``: ids that no longer inspect."""

    def __init__(self, images=None, *, false_absent=(), answer=WRAPPED, listing=None, vanished=(), pull_works=False, listing_fails=False):
        self.images = dict(images or {})
        self.false_absent, self.answer, self.vanished = set(false_absent), answer, set(vanished)
        self.listing, self.pull_works, self.listing_fails = listing, pull_works, listing_fails
        self.calls, self.pulled, self.run_images, self.built = [], [], [], []

    def _derived_listing(self):
        rows = []
        for reference, image_id in self.images.items():
            name, _, tag = reference.rpartition(":")
            rows.append((name, tag, image_id))
        return _listing(*rows)

    def __call__(self, argv, **_kwargs):
        verb = argv[1:]
        self.calls.append(verb)
        ok = lambda out="": subprocess.CompletedProcess(argv, 0, out, "")        # noqa: E731
        fail = lambda err, code=1: subprocess.CompletedProcess(argv, code, "", err)  # noqa: E731
        if verb[:2] == ["image", "inspect"]:
            target = verb[2]
            if target in self.false_absent:
                return fail(self.answer % target)
            if target in self.images:
                return ok(self.images[target] + "\n")
            by_id = [image_id for image_id in set(self.images.values()) if image_id not in self.vanished
                     and (image_id == target or (len(target) >= 4 and image_id.removeprefix("sha256:").startswith(target.removeprefix("sha256:"))))]
            if len(by_id) == 1:                                               # Docker resolves an image id or a unique prefix of it, with or without the sha256: scheme
                return ok(by_id[0] + "\n")
            return fail(PLAIN % target)
        if verb[:2] == ["image", "ls"]:
            if "--no-trunc" in verb:
                if self.listing_fails:
                    # a listing that exits non-zero is no witness, even if it printed rows before failing
                    return subprocess.CompletedProcess(argv, 1, self._derived_listing() if self.listing_fails == "with-rows" else "", "listing failed")
                return ok(self.listing if self.listing is not None else self._derived_listing())
            return ok("")                                                         # the engine wake-up listing
        if verb[0] == "pull":
            self.pulled.append(verb[1])
            if self.pull_works:
                self.images[verb[1]] = ID17
                return ok("pulled")
            return fail(OFFLINE)
        if verb[0] == "build":
            self.built.append(verb[2:])
            return ok("built")
        if verb[0] == "run":
            self.run_images.append(verb[4])
            if verb[4] not in self.images.values():
                return fail("Unable to find image", 125)
            tool = verb[3]
            if tool == "java":
                return subprocess.CompletedProcess(argv, 0, "", 'openjdk version "%s.0.9" 2023-10-17' % ("21" if verb[4] == ID21 else "17"))
            return ok("Apache Maven 3.9.6 (bc0240f)")
        raise AssertionError(f"unscripted docker call {verb}")


def _install(monkeypatch, docker):
    monkeypatch.setattr(containment_oci.subprocess, "run", docker)
    return docker


def _identity(tmp_path, release):
    project = tmp_path / f"project{release}"
    project.mkdir(exist_ok=True)
    (project / "pom.xml").write_text(f"<project><modelVersion>4.0.0</modelVersion><properties><maven.compiler.release>{release}</maven.compiler.release></properties></project>")
    identity = resolve_toolchain_identity(str(project), "java")
    assert identity is not None and identity.containment_image == f"maven:3.9-eclipse-temurin-{release}"
    return identity


@pytest.fixture(autouse=True)
def _fresh_attestation_cache():
    containment_oci._ATTESTED_TOOLCHAINS.clear()    # noqa: SLF001 - a per-process cache keyed by the immutable id
    yield
    containment_oci._ATTESTED_TOOLCHAINS.clear()    # noqa: SLF001


# ------------------------------------------------------------------------------------------------------------ A. the healthy path is unchanged

def test_a_healthy_inspect_never_consults_the_listing_and_attests_exactly_as_before(monkeypatch, tmp_path):
    docker = _install(monkeypatch, Docker({JDK17: ID17}))
    inspection = inspect_local_image("docker", JDK17)
    assert inspection.status is ImageInspectStatus.PRESENT and inspection.digest == ID17
    assert inspection.second_witness is None and inspection.first_answer is None
    assert docker.calls == [["image", "inspect", JDK17, "--format", "{{.Id}}"]]

    attested = _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)
    assert attested.image_digest == ID17 and attested.observed_runtime_version == "17" and attested.observed_build_tool_version == "3.9.6"
    assert docker.pulled == [] and not any(call[:2] == ["image", "ls"] for call in docker.calls)
    assert {image for image in docker.run_images} == {ID17}                          # the version probes ran BY the immutable id


# ------------------------------------------------------------------------------------------------------------ B/C. the false-unavailable window

@pytest.mark.parametrize("answer", [WRAPPED, PLAIN], ids=["json-wrapped (measured)", "plain"])
def test_a_false_unavailable_is_recovered_through_the_exact_listing_and_the_immutable_id_without_a_pull(monkeypatch, tmp_path, answer):
    docker = _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}, answer=answer))
    attested = _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)

    assert attested.image_digest == ID17 and attested.observed_runtime_version == "17" and attested.observed_build_tool_version == "3.9.6"
    assert docker.pulled == [] and docker.built == []
    assert set(docker.run_images) == {ID17}                                          # the contained identity is the immutable id, never the tag
    assert ["image", "inspect", ID17, "--format", "{{.Id}}"] in docker.calls          # the final immutable inspection happened


def test_the_recovered_inspection_names_both_witnesses_in_its_evidence(monkeypatch):
    _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}))
    inspection = inspect_local_image("docker", JDK17)
    assert inspection.status is ImageInspectStatus.PRESENT and inspection.digest == ID17 and inspection.present
    evidence = inspection.to_evidence()
    assert evidence["first_answer"].startswith("DAEMON_UNAVAILABLE") and ID17 in evidence["second_witness"]


def test_an_offline_machine_with_the_image_locally_runs_without_any_network_pull(monkeypatch, tmp_path):
    """C: the pull would fail offline (the scripted registry is unreachable); the run must not need it."""
    docker = _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}, pull_works=False))
    assert _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True).image_digest == ID17
    assert docker.pulled == []


# ------------------------------------------------------------------------------------------------------------ D. genuine absence

def test_genuine_absence_keeps_the_existing_pull_path_and_its_typed_error_offline(monkeypatch, tmp_path):
    docker = _install(monkeypatch, Docker({}, pull_works=False))
    inspection = inspect_local_image("docker", JDK17)
    assert inspection.status is ImageInspectStatus.IMAGE_NOT_FOUND and inspection.digest is None   # inspect and listing agree: absent
    assert any(call[:3] == ["image", "ls", "--no-trunc"] for call in docker.calls)
    with pytest.raises(BackendUnavailableError, match="unavailable and could not be pulled"):
        _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)
    assert docker.pulled == [JDK17, ]


def test_genuine_absence_pulls_then_attests_when_the_registry_is_reachable(monkeypatch, tmp_path):
    docker = _install(monkeypatch, Docker({}, pull_works=True))
    attested = _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)
    assert docker.pulled == [JDK17] and attested.image_digest == ID17


def test_a_persisting_wrapped_404_with_an_empty_listing_stays_the_existing_unclassified_answer(monkeypatch):
    """Both witnesses give no evidence of the image: the pre-fix classification (never reported as absence) is unchanged."""
    _install(monkeypatch, Docker({}, false_absent={JDK17}))
    inspection = inspect_local_image("docker", JDK17)
    assert inspection.status is ImageInspectStatus.DAEMON_UNAVAILABLE and "not present" not in inspection.describe()


@pytest.mark.parametrize("failure", [True, "with-rows"], ids=["no-output", "rows-printed-before-failing"])
def test_a_listing_that_cannot_be_read_leaves_the_existing_answer(monkeypatch, failure):
    _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}, listing_fails=failure))
    assert inspect_local_image("docker", JDK17).status is ImageInspectStatus.DAEMON_UNAVAILABLE


# ------------------------------------------------------------------------------------------------------------ E. ambiguous / malformed / partial

@pytest.mark.parametrize("listing", [
    _listing(("maven", "3.9-eclipse-temurin-17", ID17), ("maven", "3.9-eclipse-temurin-17", ID21)),     # two different images for one reference
    _listing(("maven", "3.9-eclipse-temurin-17", ID17[:19])),                                          # a truncated (partial) id: 12 hex of the real one
    _listing(("maven", "3.9-eclipse-temurin-17", ID17[:-1])),                                            # one hex digit short
    _listing(("maven", "3.9-eclipse-temurin-17", "sha256:" + "AB" * 32)),                                # not a lowercase hex id
    _listing(("maven", "3.9-eclipse-temurin-17", "<none>")),
    "maven 3.9-eclipse-temurin-17 " + ID17 + "\n",                                                       # not tab separated
    "this is not a listing\n",
], ids=["ambiguous", "truncated", "short", "uppercase", "none", "not-tab-separated", "garbage"])
def test_an_ambiguous_malformed_or_partial_listing_is_a_typed_conflict_with_no_pull_and_no_attestation(monkeypatch, tmp_path, listing):
    docker = _install(monkeypatch, Docker({JDK17: ID17, JDK21: ID21}, false_absent={JDK17}, listing=listing, pull_works=True))
    inspection = inspect_local_image("docker", JDK17)
    assert inspection.status is ImageInspectStatus.IMAGE_WITNESS_CONFLICT and not inspection.present and inspection.digest is None
    assert "docker pull" not in inspection.remediation() and "disagree" in inspection.remediation()
    with pytest.raises(BackendUnavailableError, match="cannot be attested and is not pulled"):
        _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)
    assert docker.pulled == [] and docker.run_images == []


def test_the_same_id_listed_twice_is_one_image(monkeypatch):
    """A multi-platform listing may repeat a row; one distinct immutable id is exactly one image."""
    _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}, listing=_listing(("maven", "3.9-eclipse-temurin-17", ID17), ("maven", "3.9-eclipse-temurin-17", ID17))))
    assert inspect_local_image("docker", JDK17).digest == ID17


# ------------------------------------------------------------------------------------------------------------ F. exact-reference isolation

@pytest.mark.parametrize("listing", [
    _listing(("maven", "3.9-eclipse-temurin-17-alpine", ID17)),                  # a longer tag
    _listing(("maven", "3.9-eclipse-temurin-1", ID17)),                          # a prefix of the tag
    _listing(("mymaven", "3.9-eclipse-temurin-17", ID17)),                       # a similar repository
    _listing(("library/maven", "3.9-eclipse-temurin-17", ID17)),                 # a different spelling of the repository
    _listing(("maven", "latest", ID17)),                                         # same repository, other tag
    _listing(("maven", "3.9-eclipse-temurin-17-extra", ID17), ("maven", "3.9-eclipse-temurin-17x", ID21)),
], ids=["longer-tag", "tag-prefix", "similar-repo", "other-spelling", "other-tag", "several-lookalikes"])
def test_a_similarly_named_reference_cannot_satisfy_the_lookup(monkeypatch, listing):
    docker = _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}, listing=listing))
    inspection = inspect_local_image("docker", JDK17)
    assert inspection.status is ImageInspectStatus.DAEMON_UNAVAILABLE and inspection.digest is None     # zero EXACT matches: the pre-fix answer
    assert ["image", "inspect", ID17, "--format", "{{.Id}}"] not in docker.calls


def test_a_digest_pinned_or_unlistable_reference_never_uses_the_listing(monkeypatch):
    for reference in ("maven@sha256:" + "ab" * 32, "has space:1", ""):
        docker = _install(monkeypatch, Docker({}, false_absent={reference}))
        inspect_local_image("docker", reference)
        assert not any(call[:3] == ["image", "ls", "--no-trunc"] for call in docker.calls), reference


def test_a_registry_port_in_the_reference_is_not_taken_for_a_tag(monkeypatch):
    reference = "localhost:5000/team/tool"
    docker = _install(monkeypatch, Docker({reference: ID17}, false_absent={reference}, listing=_listing(("localhost:5000/team/tool", "latest", ID17))))
    assert inspect_local_image("docker", reference).digest == ID17 and docker.pulled == []


# ------------------------------------------------------------------------------------------------------------ G. identity correctness

def test_two_references_resolving_to_different_images_get_their_own_immutable_identities(monkeypatch, tmp_path):
    docker = _install(monkeypatch, Docker({JDK17: ID17, JDK21: ID21}, false_absent={JDK17, JDK21}))
    first = _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)
    second = _attest_toolchain_image("docker", JDK21, _identity(tmp_path, 21), allow_pull=True)
    assert (first.image_digest, first.observed_runtime_version) == (ID17, "17")
    assert (second.image_digest, second.observed_runtime_version) == (ID21, "21")
    assert set(docker.run_images) == {ID17, ID21} and docker.pulled == []


def test_a_retagged_reference_is_attested_by_its_new_id_not_a_cached_one(monkeypatch, tmp_path):
    docker = _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}))
    assert _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True).image_digest == ID17
    docker.images[JDK17] = ID17B                                                                      # the tag now points at another local image
    assert _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True).image_digest == ID17B


# ------------------------------------------------------------------------------------------------------------ H. race / removal

def test_an_image_removed_after_the_listing_is_a_conflict_not_an_attestation_and_not_a_pull(monkeypatch, tmp_path):
    docker = _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}, vanished={ID17}, pull_works=True))
    inspection = inspect_local_image("docker", JDK17)
    assert inspection.status is ImageInspectStatus.IMAGE_WITNESS_CONFLICT and inspection.digest is None
    assert ID17 in inspection.second_witness and "No such image" in inspection.second_witness
    with pytest.raises(BackendUnavailableError, match="cannot be attested and is not pulled"):
        _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)
    assert docker.pulled == [] and docker.run_images == []


def test_an_image_removed_after_the_immutable_inspect_fails_the_version_probe_closed(monkeypatch, tmp_path):
    docker = _install(monkeypatch, Docker({JDK17: ID17}, false_absent={JDK17}))
    real = docker.__call__

    def remove_before_the_probe(argv, **kwargs):
        if argv[1] == "run":
            docker.images.pop(JDK17)                                                                  # gone before `docker run <id>`
        return real(argv, **kwargs)

    monkeypatch.setattr(containment_oci.subprocess, "run", remove_before_the_probe)
    with pytest.raises(containment_oci.ToolchainMismatchError, match="could not observe it"):
        _attest_toolchain_image("docker", JDK17, _identity(tmp_path, 17), allow_pull=True)


# ------------------------------------------------------------------------------------------------------------ the same policy where a build would follow an absence

def test_a_false_absent_acquisition_image_is_not_rebuilt(monkeypatch):
    docker = _install(monkeypatch, Docker({"kriya-acq-tools--debian:1": ID17}, false_absent={"kriya-acq-tools--debian:1"}))
    _ensure_image("docker", "kriya-acq-tools--debian:1", "FROM debian", purpose="acquisition firewall-tooling")
    assert docker.built == []


def test_a_genuinely_absent_acquisition_image_is_still_built(monkeypatch):
    docker = _install(monkeypatch, Docker({}))
    _ensure_image("docker", "kriya-acq-tools--debian:1", "FROM debian", purpose="acquisition firewall-tooling")
    assert len(docker.built) == 1


def test_a_conflicting_acquisition_image_is_neither_rebuilt_nor_accepted(monkeypatch):
    docker = _install(monkeypatch, Docker({"kriya-acq-tools--debian:1": ID17}, false_absent={"kriya-acq-tools--debian:1"}, listing="garbage\n"))
    with pytest.raises(BackendUnavailableError, match="is not rebuilt"):
        _ensure_image("docker", "kriya-acq-tools--debian:1", "FROM debian", purpose="acquisition firewall-tooling")
    assert docker.built == []

