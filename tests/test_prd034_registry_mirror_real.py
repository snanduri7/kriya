"""PRD-034: the mirror seam through the REAL contained acquisition path, against the certification-owned frozen Maven repository.

C. REAL ACQUISITION PATH - an EMPTY ephemeral Maven cache, the contained Maven container, the standard Maven resolver and the configured mirror: the artifacts are acquired
   and the OFFLINE execution then succeeds. Nothing is dropped into the test's local repository.
D. NO CENTRAL FALLBACK - Maven Central is not in the proxy ACL at all (the mirror is the only authorized registry host), and the complete frozen mirror still passes; the proxy's own
   denied-host record names no Central request.
E. MISSING ARTIFACT - one required artifact removed from the served set, empty cache: the acquisition fails deterministically; the served log shows the 404; nothing reached Central.
(F, content tamper, is tests/test_prd034_maven_repo.py: the server refuses to start on, and refuses to serve, bytes that differ from the manifest.)

The repository is provisioned by scripts/certify.sh (``KRIYA_CERT_MAVEN_REPO`` = its directory; ``KRIYA_CERT_MAVEN_MANIFEST`` = its manifest, default
tests/certification/maven_repository.manifest.json). Not provisioned -> the tests SKIP, and an unexpected skip fails the canonical certification.
"""
import http.server
import importlib.util
import logging
import os
import subprocess
import threading
from pathlib import Path

import pytest
from _real_oci import REAL_OCI_MAVEN

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.file_integrity import VerificationTreeBinding
from kriya.workflow.worktree import repository_content_paths

ROOT = Path(__file__).resolve().parent.parent
REPO_DIR = os.environ.get("KRIYA_CERT_MAVEN_REPO")
MANIFEST = os.environ.get("KRIYA_CERT_MAVEN_MANIFEST", str(ROOT / "tests" / "certification" / "maven_repository.manifest.json"))
_PROVISIONED = bool(REPO_DIR) and os.path.isdir(REPO_DIR or "") and os.path.isfile(MANIFEST)

pytestmark = [REAL_OCI_MAVEN, pytest.mark.skipif(not _PROVISIONED, reason="certification Maven repository not provisioned (KRIYA_CERT_MAVEN_REPO / the manifest)")]

_spec = importlib.util.spec_from_file_location("certification_maven_repo", ROOT / "scripts" / "certification_maven_repo.py")
repo_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repo_tool)

MIRROR_HOST = "host.docker.internal"


class _Mirror:
    """The frozen repository (optionally without some paths) served by the tool's own handler, bound to loopback; the Docker host name reaches it."""

    def __init__(self, tmp_path, drop=()):
        tmp_path.mkdir(parents=True, exist_ok=True)
        manifest = repo_tool.load_manifest(MANIFEST)
        manifest = {**manifest, "files": [item for item in manifest["files"] if item["path"] not in set(drop)]}
        self.log_path = tmp_path / "served.jsonl"
        expected = {item["path"]: item for item in manifest["files"]}
        handler = repo_tool._make_handler(REPO_DIR, expected, repo_tool._Log(str(self.log_path)))  # pylint: disable=protected-access
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://{MIRROR_HOST}:{self.server.server_address[1]}/maven2"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def served(self):
        import json

        return [json.loads(line) for line in self.log_path.read_text().splitlines()] if self.log_path.exists() else []

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _compile_greenfield(tmp_path, mirror_url):
    """The greenfield Maven compile gate (the same shape as tests/test_file_integrity_contract_001b.py), under a config whose ONLY registry host is the mirror."""
    ws = tmp_path / "ws"
    (ws / "src/main/java/demo").mkdir(parents=True)  # (tmp_path is created here)
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)
    (ws / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<groupId>demo</groupId><artifactId>demo</artifactId><version>1</version><properties>"
        "<maven.compiler.release>17</maven.compiler.release>"
        "<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties></project>\n")
    (ws / "src/main/java/demo/App.java").write_text('package demo; public class App { public static void main(String[] a) { System.out.println("hi"); } }\n')
    autonomy = AutonomyConfig(acquisition_registry_hosts=[MIRROR_HOST], acquisition_registry_mirror=mirror_url)  # Central is NOT an authorized host
    validator = PolymorphicValidator(str(ws), autonomy_cfg=autonomy)
    validator.tree_binding = VerificationTreeBinding(str(ws), repository_content_paths(str(ws)), ["pom.xml", "src/main/java/demo/App.java"])
    return validator.run_compile_check(["src/main/java/demo/App.java"])


def test_c_d_an_empty_cache_acquires_through_the_mirror_and_executes_offline_with_central_not_even_authorized(tmp_path, caplog):
    mirror = _Mirror(tmp_path)
    try:
        with caplog.at_level(logging.WARNING):
            result = _compile_greenfield(tmp_path, mirror.url)
        served = mirror.served()
    finally:
        mirror.close()
    assert result["success"], result.get("output")
    ok = [row for row in served if row["status"] == 200]
    assert any("maven-compiler-plugin" in row["path"] for row in ok) and len(ok) > 50, "the resolver really fetched through the mirror, into an empty cache"
    assert all(row["status"] in (200, 404) and "sha256" in row or row["status"] == 404 for row in served)
    denied = [rec.getMessage() for rec in caplog.records if "proxy denied requests" in rec.getMessage()]
    assert not any("repo.maven.apache.org" in message for message in denied), denied
    assert not any("repo.maven.apache.org" in row["path"] for row in served)


def test_e_a_required_artifact_missing_from_the_mirror_fails_the_acquisition_and_nothing_reaches_central(tmp_path, caplog):
    # first the COMPLETE mirror, to learn the artifact this project really requires (no hard-coded version: the control cannot be vacuous) ...
    complete = _Mirror(tmp_path / "complete")
    try:
        assert _compile_greenfield(tmp_path / "a", complete.url)["success"]
        required = sorted({row["path"] for row in complete.served() if row["status"] == 200 and row["path"].endswith(".jar") and "maven-compiler-plugin" in row["path"]})
    finally:
        complete.close()
    assert required, "the compile used the compiler plugin through the mirror"
    # ... then the SAME scenario with exactly that artifact outside the served set
    mirror = _Mirror(tmp_path / "missing", drop=required)
    try:
        with caplog.at_level(logging.WARNING):
            result = _compile_greenfield(tmp_path / "b", mirror.url)
        served = mirror.served()
    finally:
        mirror.close()
    assert not result["success"], "an artifact outside the frozen set must not be satisfiable"
    assert any(row["path"] in required and row["status"] == 404 for row in served), "the resolver asked the mirror for it and was told no"
    denied = [rec.getMessage() for rec in caplog.records if "proxy denied requests" in rec.getMessage()]
    assert not any("repo.maven.apache.org" in message for message in denied), "and never tried Central instead"


