#!/usr/bin/env python3
"""PRD-034: the ONE authoritative host Maven (Apache Maven 3.10.0) for the certification / demo-v2 path, provisioned independently of the CI runner image.

Maven is a supplied, qualified toolchain, never something Kriya's behaviour is written against. For the current production-certification baseline the host-side
Maven is exactly the one pinned in ``tests/certification/maven_toolchain.json`` (version, archive URL, size, Apache-published SHA-512, ``mvn -v`` banner). The runner
image's own ``/usr/bin/mvn`` is NEVER the authority, even when it happens to report the same version: it carries no provisioning marker, so ``verify`` refuses it.
(Container Maven versions are separate supplied-toolchain identities, recorded in the evidence; they are not forced to this one.)

    certification_maven_toolchain.py provision DEST [--archive FILE] [--pin FILE]   download (or take FILE), VERIFY THE PINNED SHA-512 BEFORE ANY EXTRACTION, extract into
                                                                                    DEST (the new MAVEN_HOME), prove ``mvn -v`` reports the pinned banner, write the marker
    certification_maven_toolchain.py verify    HOME [--pin FILE]                    the marker is present and equals the pin, the tree is byte-identical to what was
                                                                                    provisioned, ``mvn -v`` reports the pinned banner and its ``Maven home`` is HOME itself
    certification_maven_toolchain.py show      [--pin FILE]                         print the pin

Exit 0 = provisioned / verified; 1 = refused (the reason is printed); 2 = usage. Every check fails closed: a wrong archive digest, a wrong or missing Maven, an
unmarked or altered tree and a ``bin/mvn`` that is a link to some other installation are all refusals, never a fallback. Only the standard library is used (this runs before any
virtual environment exists). The shell side is ``require_maven_authority`` in ``demo-v2/scripts/_common.sh``; ``scripts/certify.sh`` runs ``verify`` as stage ``maven_toolchain``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
PIN_PATH = ROOT / "tests" / "certification" / "maven_toolchain.json"
MARKER = ".kriya-maven-toolchain.json"
CONTRACT = "kriya.maven-toolchain.v1"
_DOWNLOAD_TIMEOUT = 120
_BANNER_TIMEOUT = 60


class ToolchainError(Exception):
    """A refusal. The message is the reason, and is printed as such."""


def load_pin(path: Optional[Path] = None) -> Dict[str, Any]:
    pin = json.loads(Path(path or PIN_PATH).read_text(encoding="utf-8"))
    for key in ("contract", "version", "version_banner", "archive_url", "archive_bytes", "archive_sha512", "archive_root"):
        if key not in pin:
            raise ToolchainError(f"the Maven toolchain pin has no '{key}'")
    if pin["contract"] != CONTRACT:
        raise ToolchainError(f"the Maven toolchain pin is contract {pin['contract']!r}, not {CONTRACT!r}")
    if len(pin["archive_sha512"]) != 128 or any(c not in "0123456789abcdef" for c in pin["archive_sha512"]):
        raise ToolchainError("the pinned archive_sha512 is not a 128-hex-digit SHA-512")
    if not str(pin["version_banner"]).startswith(f"Apache Maven {pin['version']} "):
        raise ToolchainError("the pinned version_banner does not name the pinned version")
    return pin


def sha512_file(path: Path) -> str:
    digest = hashlib.sha512()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(home: Path) -> str:
    """sha256 over the sorted (relative path, executable bit, file sha256) lines of every file under HOME except the marker; a link is refused (a distribution has none)."""
    lines: List[str] = []
    for directory, dirnames, filenames in os.walk(home):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(directory) / name
            relative = path.relative_to(home).as_posix()
            if relative == MARKER:
                continue
            if path.is_symlink():
                raise ToolchainError(f"{relative} is a symbolic link: a provisioned distribution contains none")
            executable = "x" if os.access(path, os.X_OK) else "-"
            lines.append(f"{relative}\t{executable}\t{hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def banner_of(home: Path) -> Dict[str, str]:
    """What ``HOME/bin/mvn -v`` itself reports: its first line (the banner) and its ``Maven home`` (the installation that actually runs)."""
    mvn = home / "bin" / "mvn"
    if not mvn.is_file() or not os.access(mvn, os.X_OK):
        raise ToolchainError(f"{mvn} is not an executable file")
    env = {key: value for key, value in os.environ.items() if key not in ("MAVEN_OPTS", "MAVEN_ARGS", "MAVEN_CONFIG", "M2_HOME")}
    env["MAVEN_HOME"] = str(home)
    try:
        done = subprocess.run([str(mvn), "-v"], cwd=str(home), env=env, capture_output=True, text=True, timeout=_BANNER_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolchainError(f"{mvn} -v could not be run: {error}") from error
    lines = done.stdout.splitlines()
    if done.returncode != 0 or not lines:
        raise ToolchainError(f"{mvn} -v failed (exit {done.returncode}): {(done.stderr or done.stdout).strip()[:300]}")
    reported_home = next((line.split(":", 1)[1].strip() for line in lines if line.startswith("Maven home:")), "")
    return {"banner": lines[0].strip(), "maven_home": reported_home}


def verify(home: Path, pin: Mapping[str, Any]) -> Dict[str, Any]:
    """The marker equals the pin, the tree is byte-identical to the provisioned one, and the Maven that runs is HOME's own at exactly the pinned version."""
    home = Path(home)
    if not home.is_dir():
        raise ToolchainError(f"{home} is not a directory (provision the authority: scripts/certification_maven_toolchain.py provision DIR)")
    marker_path = home / MARKER
    if not marker_path.is_file():
        raise ToolchainError(f"{home} carries no provisioning marker ({MARKER}): it was not provisioned by this tool, so it is not the authoritative Maven (an ambient or hand-installed Maven is never the authority)")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    for key, expected in (("contract", CONTRACT), ("version", pin["version"]), ("archive_sha512", pin["archive_sha512"]), ("version_banner", pin["version_banner"])):
        if marker.get(key) != expected:
            raise ToolchainError(f"the provisioning marker's {key} is {marker.get(key)!r}, the pin says {expected!r}")
    if tree_digest(home) != marker.get("tree_sha256"):
        raise ToolchainError(f"{home} is not byte-identical to what was provisioned (tree digest differs from the marker)")
    reported = banner_of(home)
    if reported["banner"] != pin["version_banner"]:
        raise ToolchainError(f"{home}/bin/mvn reports {reported['banner']!r}, the pinned Maven is {pin['version_banner']!r}")
    if os.path.realpath(reported["maven_home"]) != os.path.realpath(home):
        raise ToolchainError(f"{home}/bin/mvn runs the installation at {reported['maven_home']!r}, not {home}: it is a link to some other Maven")
    return {"maven_home": os.path.realpath(home), "version": pin["version"], "version_banner": reported["banner"], "archive_sha512": pin["archive_sha512"],
            "tree_sha256": marker["tree_sha256"], "archive_url": pin["archive_url"]}


def _download(url: str, target: Path, limit: int) -> None:
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response, open(target, "wb") as out:  # noqa: S310 - the pinned https URL; the bytes are verified below
        total = 0
        for block in iter(lambda: response.read(1 << 20), b""):
            total += len(block)
            if total > limit:
                raise ToolchainError(f"the download from {url} exceeds the pinned size ({limit} bytes): refused")
            out.write(block)


def _safe_members(archive: tarfile.TarFile, root: str) -> List[tarfile.TarInfo]:
    members = []
    for member in archive.getmembers():
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts or not (name == root or name.startswith(root + "/")):
            raise ToolchainError(f"the archive member {name!r} is outside {root!r}: refused")
        if member.isdev() or member.issym() or member.islnk():
            raise ToolchainError(f"the archive member {name!r} is a link or device: refused")
        members.append(member)
    return members


def provision(dest: Path, pin: Mapping[str, Any], archive: Optional[Path] = None) -> Dict[str, Any]:
    """Archive -> pinned size and SHA-512 (BEFORE extraction) -> extraction into a staging directory -> banner -> marker -> DEST. DEST must not exist."""
    dest = Path(dest)
    if dest.exists():
        raise ToolchainError(f"{dest} already exists: provisioning never overwrites (remove it, or `verify` it)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kriya-maven-provision.", dir=str(dest.parent)) as staging_name:
        staging = Path(staging_name)
        if archive is None:
            archive = staging / "archive.tar.gz"
            _download(str(pin["archive_url"]), archive, int(pin["archive_bytes"]))
        archive = Path(archive)
        if not archive.is_file():
            raise ToolchainError(f"the archive {archive} does not exist")
        if archive.stat().st_size != int(pin["archive_bytes"]):
            raise ToolchainError(f"the archive is {archive.stat().st_size} bytes, the pin says {pin['archive_bytes']}: refused before extraction")
        actual = sha512_file(archive)
        if actual != pin["archive_sha512"]:
            raise ToolchainError(f"the archive's SHA-512 is {actual}, the pinned (Apache-published) value is {pin['archive_sha512']}: refused before extraction")
        extracted = staging / "x"
        extracted.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(extracted, members=_safe_members(tar, str(pin["archive_root"])), filter="data")  # nosec - members are vetted above
        candidate = extracted / str(pin["archive_root"])
        if not (candidate / "bin" / "mvn").is_file():
            raise ToolchainError(f"the archive holds no {pin['archive_root']}/bin/mvn")
        reported = banner_of(candidate)
        if reported["banner"] != pin["version_banner"]:
            raise ToolchainError(f"the extracted Maven reports {reported['banner']!r}, the pinned Maven is {pin['version_banner']!r}: refused")
        marker = {"contract": CONTRACT, "version": pin["version"], "version_banner": pin["version_banner"], "archive_sha512": pin["archive_sha512"],
                  "archive_url": pin["archive_url"], "tree_sha256": tree_digest(candidate)}
        (candidate / MARKER).write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        shutil.move(str(candidate), str(dest))
    return verify(dest, pin)


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    pin_path: Optional[Path] = None
    archive: Optional[Path] = None
    for flag in ("--pin", "--archive"):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                print(f"{flag} needs a value", file=sys.stderr)
                return 2
            value = Path(args[index + 1])
            del args[index:index + 2]
            if flag == "--pin":
                pin_path = value
            else:
                archive = value
    if not args or args[0] not in ("provision", "verify", "show"):
        print(__doc__, file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    try:
        pin = load_pin(pin_path)
        if command == "show":
            print(json.dumps(pin, indent=2, sort_keys=True))
            return 0
        if len(rest) != 1:
            print(f"{command} takes exactly one directory", file=sys.stderr)
            return 2
        record = provision(Path(rest[0]), pin, archive) if command == "provision" else verify(Path(rest[0]), pin)
    except ToolchainError as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
