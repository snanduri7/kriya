"""PRD-034 (owner ruling 2026-11-04): ONE authoritative host Maven - Apache Maven 3.10.0 - provisioned independently of the CI runner image.

Hosted run 41812207261 (MEASURED): the post-check's fixed PATH resolved ``/usr/bin/mvn`` = Maven 3.10.0 while the test process had the pinned 3.9.9 first; 3.10.0 binds
maven-clean-plugin:3.5.0 / maven-resources-plugin:3.5.0, which the offline repository (seeded by 3.9.9) did not hold. These tests execute the real provisioning and
verification code (against small fake distributions that behave like a Maven: ``mvn -v`` prints a banner and its ``Maven home``) and the real shell helpers, so the
controls are behaviour, not a grep of the workflow: wrong archive digest, wrong version, missing Maven, ambient/decoy Maven, an altered tree, a link to another
installation and a hostile archive are all refused; and the reference POM pins every lifecycle plugin the certified path runs.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "certification_maven_toolchain.py"
COMMON = ROOT / "demo-v2" / "scripts" / "_common.sh"
PIN = ROOT / "tests" / "certification" / "maven_toolchain.json"
REFERENCE_POM = ROOT / "demo-v2" / "fixtures" / "spring-ignite-protocol" / "reference" / "pom.xml"
BANNER = "Apache Maven 3.10.0 (c43a36b8d67be7e0805a411bc0898af1a51f5472)"


def _module():
    spec = importlib.util.spec_from_file_location("certification_maven_toolchain", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _module()


def _mvn_script(banner: str = BANNER, home_line: str = "") -> str:
    """A shell script that behaves like `mvn -v` for the parts the verifier reads: the banner and the installation it really runs ('Maven home')."""
    return ("#!/bin/sh\nhere=\"$(cd \"$(dirname \"$0\")/..\" && pwd -P)\"\n"
            f"echo '{banner}'\necho \"Maven home: {home_line or '$here'}\"\necho 'Java version: 17'\n")


def _archive(path: Path, *, banner: str = BANNER, home_line: str = "", extra=None, root: str = "apache-maven-3.10.0") -> Path:
    with tarfile.open(path, "w:gz") as tar:
        def add(name, data: bytes, mode=0o644):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        add(f"{root}/bin/mvn", _mvn_script(banner, home_line).encode(), 0o755)
        add(f"{root}/lib/maven-core.jar", b"jar-bytes")
        for name, data in (extra or {}).items():
            add(name, data)
    return path


def _pin(tmp_path: Path, archive: Path, **override) -> Path:
    pin = json.loads(PIN.read_text(encoding="utf-8"))
    pin.update({"archive_bytes": archive.stat().st_size, "archive_sha512": hashlib.sha512(archive.read_bytes()).hexdigest()}, **override)
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(pin), encoding="utf-8")
    return path


def _provisioned(tmp_path: Path, **archive_kwargs):
    archive = _archive(tmp_path / "m.tar.gz", **archive_kwargs)
    pin = tool.load_pin(_pin(tmp_path, archive))
    home = tmp_path / "authority"
    record = tool.provision(home, pin, archive)
    return home, pin, record, archive


# ----------------------------------------------------------------------------------------------------------------- the pin itself

def test_the_pin_names_maven_3_10_0_with_an_apache_sha512_and_no_3_9_9():
    pin = tool.load_pin()
    assert pin["version"] == "3.10.0" and pin["version_banner"].startswith("Apache Maven 3.10.0 (")
    assert pin["archive_url"].startswith("https://archive.apache.org/dist/maven/maven-3/3.10.0/") and pin["archive_url"].endswith("apache-maven-3.10.0-bin.tar.gz")
    assert re.fullmatch(r"[0-9a-f]{128}", pin["archive_sha512"]) and pin["archive_root"] == "apache-maven-3.10.0"
    assert "3.9.9" not in PIN.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------------------------------------------- provisioning controls

def test_a_pinned_archive_provisions_and_verifies(tmp_path):
    home, pin, record, _archive_path = _provisioned(tmp_path)
    assert record["version"] == "3.10.0" and record["version_banner"] == BANNER and record["archive_sha512"] == pin["archive_sha512"]
    assert (home / tool.MARKER).is_file() and os.access(home / "bin" / "mvn", os.X_OK)
    assert tool.verify(home, pin) == record


def test_a_wrong_archive_digest_is_refused_before_anything_is_extracted(tmp_path):
    archive = _archive(tmp_path / "m.tar.gz")
    pin = tool.load_pin(_pin(tmp_path, archive, archive_sha512="0" * 128))
    with pytest.raises(tool.ToolchainError, match="SHA-512"):
        tool.provision(tmp_path / "authority", pin, archive)
    assert not (tmp_path / "authority").exists() and not list(tmp_path.glob("kriya-maven-provision.*")), "nothing was extracted or left behind"


def test_a_corrupted_archive_is_refused_even_though_it_is_a_valid_tarball(tmp_path):
    archive = _archive(tmp_path / "m.tar.gz")
    pin = tool.load_pin(_pin(tmp_path, archive))
    other = _archive(tmp_path / "other.tar.gz", extra={"apache-maven-3.10.0/lib/evil.jar": b"x"})
    with pytest.raises(tool.ToolchainError, match="SHA-512|bytes"):
        tool.provision(tmp_path / "authority", pin, other)
    assert not (tmp_path / "authority").exists()


def test_a_wrong_size_is_refused(tmp_path):
    archive = _archive(tmp_path / "m.tar.gz")
    pin = tool.load_pin(_pin(tmp_path, archive, archive_bytes=archive.stat().st_size + 1))
    with pytest.raises(tool.ToolchainError, match="bytes"):
        tool.provision(tmp_path / "authority", pin, archive)


def test_a_wrong_maven_version_is_refused_even_when_the_digest_is_the_pinned_one(tmp_path):
    archive = _archive(tmp_path / "m.tar.gz", banner="Apache Maven 3.9.9 (8e8579a9e76f7d015ee5ec7bfcdc97d260186937)")
    pin = tool.load_pin(_pin(tmp_path, archive))   # the pin digest equals this archive: only the version it reports is wrong
    with pytest.raises(tool.ToolchainError, match="reports 'Apache Maven 3.9.9"):
        tool.provision(tmp_path / "authority", pin, archive)
    assert not (tmp_path / "authority").exists()


def test_a_missing_maven_is_refused(tmp_path):
    pin = tool.load_pin()
    with pytest.raises(tool.ToolchainError, match="not a directory"):
        tool.verify(tmp_path / "nowhere", pin)
    with pytest.raises(tool.ToolchainError, match="does not exist"):
        tool.provision(tmp_path / "authority", pin, tmp_path / "no-such.tar.gz")


def test_provisioning_never_overwrites(tmp_path):
    home, pin, _record, archive = _provisioned(tmp_path)
    with pytest.raises(tool.ToolchainError, match="already exists"):
        tool.provision(home, pin, archive)


@pytest.mark.parametrize("member", ["../escape.txt", "/abs.txt", "other-root/bin/mvn"])
def test_a_hostile_archive_member_is_refused(tmp_path, member):
    archive = _archive(tmp_path / "m.tar.gz", extra={member: b"x"})
    pin = tool.load_pin(_pin(tmp_path, archive))
    with pytest.raises(tool.ToolchainError, match="outside"):
        tool.provision(tmp_path / "authority", pin, archive)
    assert not (tmp_path / "escape.txt").exists()


def test_a_link_in_the_archive_is_refused(tmp_path):
    archive = tmp_path / "m.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("apache-maven-3.10.0/bin/mvn")
        link.type, link.linkname = tarfile.SYMTYPE, "/usr/bin/mvn"
        tar.addfile(link)
    pin = tool.load_pin(_pin(tmp_path, archive))
    with pytest.raises(tool.ToolchainError, match="link or device"):
        tool.provision(tmp_path / "authority", pin, archive)


# ----------------------------------------------------------------------------------------------------------------- verification controls

def test_an_ambient_maven_that_reports_the_right_version_is_never_the_authority(tmp_path):
    """The runner image's /usr/bin/mvn is 3.10.0 too: it has no provisioning marker, so it is refused (the owner's rule: not even when it happens to match)."""
    ambient = tmp_path / "usr-share-apache-maven-3.10.0"
    (ambient / "bin").mkdir(parents=True)
    (ambient / "bin" / "mvn").write_text(_mvn_script())
    (ambient / "bin" / "mvn").chmod(0o755)
    assert tool.banner_of(ambient)["banner"] == BANNER   # it really is a working "3.10.0"
    with pytest.raises(tool.ToolchainError, match="no provisioning marker"):
        tool.verify(ambient, tool.load_pin())


def test_a_marker_copied_onto_an_ambient_maven_does_not_launder_it(tmp_path):
    home, pin, _record, _archive_path = _provisioned(tmp_path)
    ambient = tmp_path / "ambient"
    (ambient / "bin").mkdir(parents=True)
    (ambient / "bin" / "mvn").write_text(_mvn_script("Apache Maven 3.10.0 (c43a36b8d67be7e0805a411bc0898af1a51f5472)", "/usr/share/apache-maven-3.10.0"))
    (ambient / "bin" / "mvn").chmod(0o755)
    (ambient / tool.MARKER).write_text((home / tool.MARKER).read_text())
    with pytest.raises(tool.ToolchainError, match="not byte-identical"):
        tool.verify(ambient, pin)


def test_a_tree_altered_after_provisioning_is_refused(tmp_path):
    home, pin, _record, _archive_path = _provisioned(tmp_path)
    (home / "lib" / "maven-core.jar").write_bytes(b"tampered")
    with pytest.raises(tool.ToolchainError, match="not byte-identical"):
        tool.verify(home, pin)


def test_a_link_to_another_installation_is_refused(tmp_path):
    home, pin, _record, _archive_path = _provisioned(tmp_path)
    (home / "bin" / "mvn").unlink()
    (home / "bin" / "mvn").symlink_to("/bin/echo")
    with pytest.raises(tool.ToolchainError, match="symbolic link"):
        tool.verify(home, pin)


def test_a_maven_that_runs_another_installation_is_refused(tmp_path):
    """Right banner, right bytes (it is what the pin says) - but `Maven home` is some other installation: a wrapper around an ambient Maven."""
    archive = _archive(tmp_path / "m.tar.gz", home_line="/usr/share/apache-maven-3.10.0")
    pin = tool.load_pin(_pin(tmp_path, archive))
    with pytest.raises(tool.ToolchainError, match="not "):
        tool.provision(tmp_path / "authority", pin, archive)


def test_verify_asks_the_maven_itself_for_its_version_even_when_every_byte_of_the_tree_is_unchanged(tmp_path):
    """A tree that is byte-identical to the provisioned one can still run as another Maven (a wrapper that reads its identity from outside the tree). The banner is read
    from `mvn -v` at every verify, independently of the tree digest."""
    outside = tmp_path / "banner.txt"
    outside.write_text(BANNER)
    archive = tmp_path / "m.tar.gz"
    wrapper = (f"#!/bin/sh\nhere=\"$(cd \"$(dirname \"$0\")/..\" && pwd -P)\"\ncat '{outside}'; echo\necho \"Maven home: $here\"\n").encode()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("apache-maven-3.10.0/bin/mvn")
        info.size, info.mode = len(wrapper), 0o755
        tar.addfile(info, io.BytesIO(wrapper))
    pin = tool.load_pin(_pin(tmp_path, archive))
    home = tmp_path / "authority"
    tool.provision(home, pin, archive)
    outside.write_text("Apache Maven 3.9.9 (8e8579a9e76f7d015ee5ec7bfcdc97d260186937)")
    with pytest.raises(tool.ToolchainError, match="reports 'Apache Maven 3.9.9"):
        tool.verify(home, pin)


def test_the_marker_must_equal_the_pin(tmp_path):
    home, pin, _record, _archive_path = _provisioned(tmp_path)
    other = dict(pin, archive_sha512="1" * 128)
    with pytest.raises(tool.ToolchainError, match="archive_sha512"):
        tool.verify(home, other)


def test_the_cli_exits_1_with_the_reason_and_0_on_success(tmp_path):
    archive = _archive(tmp_path / "m.tar.gz")
    pin_path = _pin(tmp_path, archive)
    ok = subprocess.run([sys.executable, "-I", str(SCRIPT), "provision", str(tmp_path / "authority"), "--archive", str(archive), "--pin", str(pin_path)], capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["version"] == "3.10.0"
    bad = subprocess.run([sys.executable, "-I", str(SCRIPT), "verify", str(tmp_path / "nowhere"), "--pin", str(pin_path)], capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and bad.stderr.startswith("REFUSED:")


# ----------------------------------------------------------------------------------------------------------------- the shell side (demo-v2 scripts)

def _verifier_with_test_pin(tmp_path: Path, pin_path: Path) -> Path:
    """`resolve_maven_authority` runs `$KRIYA_MAVEN_VERIFY_PY -I certification_maven_toolchain.py verify HOME`: this interpreter stand-in appends the test pin, so the
    production code path (and its default pin) is untouched."""
    wrapper = tmp_path / "verify-with-test-pin"
    wrapper.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\" --pin '{pin_path}'\n")
    wrapper.chmod(0o755)
    return wrapper


def _bash(snippet: str, *, env: dict) -> subprocess.CompletedProcess:
    script = f"set -u; source '{COMMON}'; {snippet}"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=False)


def _decoy(directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / "mvn").write_text("#!/bin/sh\necho DECOY-MVN-RAN\n")
    (directory / "mvn").chmod(0o755)
    return directory


def test_a_decoy_mvn_earlier_on_the_path_and_inside_the_fixed_base_cannot_override_the_authority(tmp_path):
    home, _pin_obj, _record, archive = _provisioned(tmp_path)
    verifier = _verifier_with_test_pin(tmp_path, tmp_path / "pin.json")
    decoy_ambient, decoy_base = _decoy(tmp_path / "ambient-bin"), _decoy(tmp_path / "base-bin")
    env = {"PATH": f"{decoy_ambient}:{os.environ['PATH']}", "HOME": str(tmp_path), "MAVEN_HOME": str(home), "KRIYA_MAVEN_VERIFY_PY": str(verifier), "DEMO_V2_APP_BASE_PATH": str(decoy_base)}
    done = _bash('resolve_maven_authority || { echo "ERR: $MAVEN_AUTHORITY_ERROR"; exit 9; }; echo "PATH=$(app_path)"; PATH="$(app_path)" authority_mvn -v | head -1; '
                 'echo "bare-mvn-resolves-to: $(PATH="$(app_path)" command -v mvn)"; '
                 'PATH="$AMBIENT_FIRST:$PATH" authority_mvn -v | head -1', env={**env, "AMBIENT_FIRST": str(decoy_ambient)})
    assert done.returncode == 0, done.stderr + done.stdout
    out = done.stdout.splitlines()
    assert out[0].startswith(f"PATH={home.resolve()}/bin:"), out
    assert out[1] == BANNER and "DECOY" not in done.stdout, out
    assert out[2] == f"bare-mvn-resolves-to: {home.resolve()}/bin/mvn", "even a bare `mvn` on the scripts' PATH is the authority's, ahead of the decoy in the fixed base"
    assert out[3] == BANNER, "authority_mvn runs the authority by ABSOLUTE path: a decoy first on the PATH (the scripts' PATH order is not what protects it) cannot stand in"
    assert archive.is_file()


def test_the_shell_helpers_refuse_without_a_valid_maven_home_and_never_fall_back_to_the_decoy(tmp_path):
    home, _pin_obj, _record, _archive_path = _provisioned(tmp_path)
    verifier = _verifier_with_test_pin(tmp_path, tmp_path / "pin.json")
    decoy = _decoy(tmp_path / "ambient-bin")
    base_env = {"PATH": f"{decoy}:{os.environ['PATH']}", "HOME": str(tmp_path), "KRIYA_MAVEN_VERIFY_PY": str(verifier), "DEMO_V2_APP_BASE_PATH": str(decoy)}
    (home / "lib" / "maven-core.jar").write_bytes(b"tampered")
    cases = {"unset": {}, "unprovisioned": {"MAVEN_HOME": str(decoy)}, "altered": {"MAVEN_HOME": str(home)}}
    for name, extra in cases.items():
        done = _bash('if resolve_maven_authority; then echo RESOLVED; else echo "REFUSED: $MAVEN_AUTHORITY_ERROR"; fi; authority_mvn -v || echo "rc=$?"', env={**base_env, **extra})
        assert "REFUSED:" in done.stdout and "RESOLVED" not in done.stdout, (name, done.stdout, done.stderr)
        assert "DECOY-MVN-RAN" not in done.stdout and "rc=127" in done.stdout, (name, done.stdout)


def test_require_maven_authority_stops_the_script_with_the_provisioning_instruction(tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    done = _bash("require_maven_authority; echo NOT-REACHED", env=env)
    assert done.returncode == 1 and "NOT-REACHED" not in done.stdout
    assert "MAVEN_HOME is not set" in done.stderr and "certification_maven_toolchain.py provision" in done.stderr


# ----------------------------------------------------------------------------------------------------------------- the wiring

def test_every_host_maven_site_of_the_demo_v2_path_goes_through_the_authority():
    qualify = (ROOT / "demo-v2" / "scripts" / "qualify_spring_ignite.sh").read_text()
    verify = (ROOT / "demo-v2" / "scripts" / "verify_demo.sh").read_text()
    helper = (ROOT / "demo-v2" / "scripts" / "qualify_spring_ignite.py").read_text()
    prereqs = (ROOT / "demo-v2" / "scripts" / "greenfield_prereqs.sh").read_text()
    assert "require_maven_authority" in qualify and 'APP_PATH="$(app_path)"' in qualify
    assert not re.search(r'HOME="\$HOME" mvn ', qualify), "a bare `mvn` resolved through the PATH"
    assert not re.search(r'PATH="[^"]*" mvn\b', verify), "an invocation of a bare `mvn` resolved through the PATH in verify_demo.sh"
    assert verify.count("authority_mvn -o") == 3
    assert '[_authority_mvn(), "-o"' in helper and 'subprocess.run(["mvn"' not in helper
    assert "resolve_maven_authority" in prereqs and "command -v mvn" not in prereqs


def test_the_ci_certification_job_provisions_the_authority_from_the_pin_and_not_from_the_runner_image():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("    name: Production certification"):]
    assert "scripts/certification_maven_toolchain.py provision" in job and 'MAVEN_HOME=$HOME/kriya-maven-authority" >> "$GITHUB_ENV"' in job
    assert "apache-maven-3.9.9" not in ci and "archive.apache.org/dist/maven" not in ci, "the download lives in the pinned, checksum-verified tool, not in an unchecked curl | tar"
    assert job.index("certification_maven_toolchain.py provision") < job.index("scripts/seed_offline_maven.sh") < job.index("scripts/certify.sh certification-out/ci")


def test_certify_sh_verifies_the_authority_first_and_refuses_the_canonical_run_without_it():
    certify = (ROOT / "scripts" / "certify.sh").read_text(encoding="utf-8")
    assert certify.index("stage maven_toolchain") < certify.index("stage environment") < certify.index("stage pytest UNAVAILABLE")
    assert 'elif [ "$MAVEN_AUTHORITY_OK" != "1" ]; then' in certify
    assert 'export PATH="$(cd "$MAVEN_HOME" && pwd -P)/bin:$PATH"' in certify
    assert '"container_maven"' in certify and '"maven_authority"' in certify, "the environment record names the authority and each pinned image's own Maven (separate identities)"
    summary = (ROOT / "scripts" / "certification_summary.py").read_text(encoding="utf-8")
    assert '"maven_toolchain"' in summary.split("MANDATORY =")[1].split("\n")[0]


def test_the_reference_pom_pins_every_lifecycle_plugin_the_certified_path_runs():
    """`clean test` and `compile exec:exec` run exactly these plugins (MEASURED under Maven 3.10.0): none may float with a Maven version's default bindings."""
    pom = REFERENCE_POM.read_text(encoding="utf-8")
    pinned = dict(re.findall(r"<artifactId>(maven-[a-z-]+-plugin|exec-maven-plugin)</artifactId>\s*<version>([^<]+)</version>", pom))
    assert pinned == {"maven-clean-plugin": "3.5.0", "maven-resources-plugin": "3.5.0", "maven-compiler-plugin": "3.11.0", "maven-surefire-plugin": "2.22.2", "exec-maven-plugin": "3.1.0"}, pinned


def test_no_active_certification_surface_claims_maven_3_9_9():
    """3.9.9 equivalence is no longer a PRD-034 requirement. Historical evidence keeps the version each run actually used (handover/evidence is not scanned)."""
    surfaces = [ROOT / ".github" / "workflows" / "ci.yml", ROOT / "scripts" / "certify.sh", ROOT / "scripts" / "seed_offline_maven.sh", ROOT / "tests" / "certification" / "maven_toolchain.json",
                ROOT / "demo-v2" / "scripts" / "_common.sh", ROOT / "demo-v2" / "scripts" / "qualify_spring_ignite.sh", ROOT / "demo-v2" / "scripts" / "verify_demo.sh"]
    assert [str(path.relative_to(ROOT)) for path in surfaces if "3.9.9" in path.read_text(encoding="utf-8")] == []
