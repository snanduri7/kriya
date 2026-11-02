#!/usr/bin/env python3
"""PRD-034: the frozen Python wheelhouse for the canonical certification (PYPI: release-stage clean install, and the contained pip acquisition tests).

The canonical run must not depend on live PyPI (owner ruling 2026-11-02, PRD034-EXTERNAL-ACQUISITION-PYPI-GRADLE-APT-001). The release stage certifies PACKAGING (a clean install of the
built wheel with its locked dependencies), not PyPI; so the content authority is ONE hashed requirements file, ``tests/certification/requirements.release.hashes.txt``: every locked
``name==version`` with the SHA-256 of every file PyPI published for it (the same thing ``pip-compile --generate-hashes`` records), valid on every platform and Python. The wheelhouse
directory is PROVISIONED on each machine from it (``pip download --require-hashes``: a file is kept only if its hash is listed) and the install then runs
``pip install --no-index --find-links WHEELHOUSE --require-hashes`` in a fresh virtualenv - no index, no fallback.

    certification_python_wheels.py hashes REQUIREMENTS OUT [name==ver ...]            BOOTSTRAP: hashed requirements from a pinned lock (asks PyPI for each published file hash)
    certification_python_wheels.py provision HASHED WHEELHOUSE                        PROVISION: download this machine's wheels, each verified against HASHED
    certification_python_wheels.py verify HASHED WHEELHOUSE                           every wheelhouse file is one HASHED lists, and every requirement has a file; exit 1 otherwise
    certification_python_wheels.py digest HASHED WHEELHOUSE                           print the content identity (HASHED sha256 + the wheelhouse file digest)
    certification_python_wheels.py record POOL --port N [--bind A] --log F            DISCOVERY ONLY: a recording pass-through to PyPI that stores every file the contained pip tests
                                                                                       request into POOL (rewrites the index so the files come through here); never certification
    certification_python_wheels.py serve HASHED WHEELHOUSE --port N [--bind A] [--log F] [--progress F]
                                                                                       a PEP 503 index over the wheelhouse for the CONTAINED pip tests: only hash-listed files,
                                                                                       re-hashed at every request; pip also verifies the #sha256 fragment itself

``hashes``/``provision`` are bootstrap (they contact PyPI) and are never certification evidence; ``verify``/``digest``/``serve`` and the install itself need no network.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

PYPI = "https://pypi.org"
_LOCK_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")
_FILE_SUFFIXES = (".whl", ".tar.gz", ".zip")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str) -> List[Tuple[str, str]]:
    """Pinned ``name==version`` pairs of a pip-compile output (comments and ``# via`` lines ignored)."""
    pins = []
    for line in text.splitlines():
        match = _LOCK_LINE.match(line.strip())
        if match and not line.lstrip().startswith("#"):
            pins.append((match.group("name"), match.group("version")))
    return pins


def parse_hashed(text: str) -> Dict[str, Tuple[str, Set[str]]]:
    """{normalized name: (version, {sha256, ...})} from a hashed requirements file."""
    joined = text.replace("\\\n", " ")
    result: Dict[str, Tuple[str, Set[str]]] = {}
    for line in joined.splitlines():
        match = _LOCK_LINE.match(line.strip())
        if not match:
            continue
        result[normalize(match.group("name"))] = (match.group("version"), set(re.findall(r"--hash=sha256:([0-9a-f]{64})", line)))
    return result


def published_hashes(name: str, version: str, index: str = PYPI) -> List[str]:
    with urllib.request.urlopen(f"{index}/pypi/{name}/{version}/json", timeout=60) as response:
        document = json.load(response)
    return sorted({entry["digests"]["sha256"] for entry in document["urls"]})


def write_hashed(pins: Sequence[Tuple[str, str]], out: str, index: str = PYPI) -> None:
    lines = []
    for name, version in sorted(pins, key=lambda pin: normalize(pin[0])):
        time.sleep(0.05)  # polite to PyPI
        hashes = published_hashes(name, version, index)
        lines.append(f"{name}=={version} \\\n" + " \\\n".join(f"    --hash=sha256:{value}" for value in hashes))
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("# PRD-034: hashed lock of the release/contained-pip certification (scripts/certification_python_wheels.py hashes). Every file PyPI published per pin.\n")
        handle.write("\n".join(lines) + "\n")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def wheelhouse_files(wheelhouse: str) -> Dict[str, str]:
    return {name: sha256_file(os.path.join(wheelhouse, name)) for name in sorted(os.listdir(wheelhouse)) if name.endswith(_FILE_SUFFIXES)}


def _project_of(filename: str) -> str:
    """The normalized project a wheel/sdist file belongs to (a wheel's first dash-separated field; an sdist's name before ``-<version>``)."""
    if filename.endswith(".whl"):
        return normalize(filename.split("-", 1)[0])
    stem = filename[:-7] if filename.endswith(".tar.gz") else filename[:-4]
    return normalize(re.sub(r"-\d.*$", "", stem))


def verify(hashed_path: str, wheelhouse: str) -> List[str]:
    hashed = parse_hashed(open(hashed_path, encoding="utf-8").read())
    allowed: Set[str] = set().union(*(hashes for _version, hashes in hashed.values())) if hashed else set()
    problems: List[str] = []
    files = wheelhouse_files(wheelhouse)
    for name, digest in files.items():
        if digest not in allowed:
            problems.append(f"NOT IN THE HASHED LOCK (or bytes differ): {name}")
    present = {_project_of(name) for name in files}
    for project, (version, _hashes) in sorted(hashed.items()):
        if project not in present:
            problems.append(f"no file for {project}=={version}")
    return problems


def digest(hashed_path: str, wheelhouse: str) -> Dict[str, str]:
    files = wheelhouse_files(wheelhouse)
    listing = "\n".join(f"{name}\t{value}" for name, value in files.items())
    return {"hashed_requirements_sha256": sha256_file(hashed_path), "wheelhouse_files": str(len(files)),
            "wheelhouse_sha256": hashlib.sha256(listing.encode("utf-8")).hexdigest()}


def provision(hashed_path: str, wheelhouse: str) -> int:
    os.makedirs(wheelhouse, exist_ok=True)
    command = [sys.executable, "-m", "pip", "download", "--require-hashes", "--no-deps", "--disable-pip-version-check", "-r", hashed_path, "-d", wheelhouse]
    return subprocess.run(command, check=False).returncode


class _Log:
    def __init__(self, path: Optional[str], progress: Optional[str]) -> None:
        self._path, self._progress = path, progress

    def write(self, record: Mapping[str, object]) -> None:
        if not self._path:
            return
        node = None
        if self._progress:
            try:
                with open(self._progress, "r", encoding="utf-8") as handle:
                    node = json.load(handle).get("current")
            except (OSError, ValueError):
                node = None
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": round(time.time(), 3), "node": node, **record}, sort_keys=True) + "\n")


def _make_handler(hashed: Mapping[str, Tuple[str, Set[str]]], wheelhouse: str, log: _Log):
    allowed: Set[str] = set().union(*(hashes for _version, hashes in hashed.values())) if hashed else set()
    served = {name: value for name, value in wheelhouse_files(wheelhouse).items() if value in allowed}  # only hash-listed files exist for the index

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "kriya-cert-python-index/1"

        def log_message(self, *_args) -> None:
            return

        def _reply(self, status: int, body: bytes, kind: str = "text/html") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/simple", "/simple/"):
                projects = sorted({_project_of(name) for name in served})
                body = "<!DOCTYPE html><html><body>" + "".join(f'<a href="/simple/{project}/">{project}</a><br/>' for project in projects) + "</body></html>"
                log.write({"path": path, "status": 200})
                return self._reply(200, body.encode())
            if path.startswith("/simple/"):
                project = normalize(path[len("/simple/"):].strip("/"))
                names = [name for name in served if _project_of(name) == project]
                if not names:
                    log.write({"path": path, "status": 404, "why": "project not in the frozen lock"})
                    return self._reply(404, b"")
                links = "".join(f'<a href="/packages/{name}#sha256={served[name]}">{name}</a><br/>' for name in names)
                log.write({"path": path, "status": 200, "files": len(names)})
                return self._reply(200, f"<!DOCTYPE html><html><body>{links}</body></html>".encode())
            if path.startswith("/packages/"):
                name = path[len("/packages/"):]
                if name not in served or "/" in name:
                    log.write({"path": path, "status": 404, "why": "not a hash-listed file"})
                    return self._reply(404, b"")
                body = open(os.path.join(wheelhouse, name), "rb").read()
                actual = hashlib.sha256(body).hexdigest()
                if actual != served[name]:
                    log.write({"path": path, "status": 409, "sha256": actual, "why": "BYTES DIFFER from the hashed lock - refused"})
                    return self._reply(409, b"")
                log.write({"path": path, "status": 200, "sha256": actual, "bytes": len(body)})
                return self._reply(200, body, "application/octet-stream")
            log.write({"path": path, "status": 404, "why": "outside the index"})
            return self._reply(404, b"")

        do_HEAD = do_GET  # noqa: N815

    return Handler


def _make_recorder(pool: str, log: _Log, upstream: str = PYPI):
    """DISCOVERY: the real PyPI index through this process; the files pip asks for are saved in ``pool`` (with the SHA-256 PyPI publishes in the link, checked on download)."""
    mapping: Dict[str, str] = {}

    class Recorder(http.server.BaseHTTPRequestHandler):
        server_version = "kriya-cert-python-recorder/1"

        def log_message(self, *_args) -> None:
            return

        def _reply(self, status: int, body: bytes, kind: str = "text/html") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path.startswith("/simple/"):
                    request = urllib.request.Request(f"{upstream}{path}", headers={"Accept": "text/html"})
                    with urllib.request.urlopen(request, timeout=60) as response:
                        page = response.read().decode("utf-8", "replace")

                    def rewrite(match):
                        url = match.group(1)
                        filename = url.split("#", 1)[0].rsplit("/", 1)[-1]
                        mapping[filename] = url.split("#", 1)[0]
                        fragment = "#" + url.split("#", 1)[1] if "#" in url else ""
                        return f'href="/packages/{filename}{fragment}"'

                    # PEP 658 metadata is not mirrored: without the advertisement pip simply downloads (and hash-checks) the wheel itself
                    page = re.sub(r' data-(?:dist-info|core)-metadata="[^"]*"', "", page)
                    log.write({"path": path, "status": 200})
                    return self._reply(200, re.sub(r'href="([^"]+)"', rewrite, page).encode())
                if path.startswith("/packages/"):
                    filename = path[len("/packages/"):]
                    if filename not in mapping:
                        log.write({"path": path, "status": 404, "why": "unknown file (its index page was not fetched through here)"})
                        return self._reply(404, b"")
                    target = os.path.join(pool, filename)
                    if not os.path.exists(target):
                        with urllib.request.urlopen(mapping[filename], timeout=120) as response:
                            body = response.read()
                        os.makedirs(pool, exist_ok=True)
                        with open(target, "wb") as handle:
                            handle.write(body)
                    body = open(target, "rb").read()
                    log.write({"path": path, "status": 200, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
                    return self._reply(200, body, "application/octet-stream")
            except (urllib.error.URLError, OSError) as error:
                log.write({"path": path, "status": 502, "why": str(error)})
                return self._reply(502, b"")
            log.write({"path": path, "status": 404})
            return self._reply(404, b"")

        do_HEAD = do_GET  # noqa: N815

    return Recorder


def main(argv: Sequence[str]) -> int:
    args = list(argv[1:])
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    def option(name: str, default: Optional[str] = None) -> Optional[str]:
        return args[args.index(name) + 1] if name in args else default

    command = args[0]
    positional = [a for i, a in enumerate(args[1:], 1) if not a.startswith("--") and not (i > 1 and args[i - 1].startswith("--"))]
    if command == "hashes" and len(positional) >= 2:
        pins = parse_lock(open(positional[0], encoding="utf-8").read())
        for extra in positional[2:]:  # additional pins the contained pip tests install (e.g. six==1.16.0)
            name, _, version = extra.partition("==")
            pins.append((name, version))
        write_hashed(pins, positional[1])
        print(f"[python-wheels] hashed {len(pins)} pins -> {positional[1]}")
        return 0
    if command == "provision" and len(positional) == 2:
        return provision(positional[0], positional[1])
    if command == "verify" and len(positional) == 2:
        problems = verify(positional[0], positional[1])
        print("[python-wheels] verify: " + ("PASS" if not problems else "FAIL") + "".join(f"\n  - {p}" for p in problems[:30]))
        return 0 if not problems else 1
    if command == "digest" and len(positional) == 2:
        print(json.dumps(digest(positional[0], positional[1]), sort_keys=True))
        return 0
    if command == "record" and len(positional) == 1:
        server = http.server.ThreadingHTTPServer((option("--bind", "127.0.0.1") or "127.0.0.1", int(option("--port", "18090") or 18090)),
                                                 _make_recorder(positional[0], _Log(option("--log"), None)))
        print(f"[python-wheels] RECORDING PyPI on port {server.server_address[1]} into {positional[0]}", flush=True)
        server.serve_forever()
        return 0
    if command == "serve" and len(positional) == 2:
        problems = verify(positional[0], positional[1])
        if problems:
            print("[python-wheels] REFUSING to serve" + "".join(f"\n  - {p}" for p in problems[:30]), file=sys.stderr)
            return 1
        hashed = parse_hashed(open(positional[0], encoding="utf-8").read())
        server = http.server.ThreadingHTTPServer((option("--bind", "127.0.0.1") or "127.0.0.1", int(option("--port", "18090") or 18090)),
                                                 _make_handler(hashed, positional[1], _Log(option("--log"), option("--progress"))))
        print(f"[python-wheels] serving the frozen wheelhouse on port {server.server_address[1]}", flush=True)
        server.serve_forever()
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
