#!/usr/bin/env python3
"""PRD-034: the certification-owned, FROZEN Maven repository, and the tool that builds, verifies, attests and serves it.

The canonical certification must be reproducible from an empty Maven cache without live Maven Central (owner ruling 2026-11-02; MAVEN-CENTRAL-CERTIFICATION-
NONDETERMINISM-001). The contained acquisition still runs the real path - test -> Kriya contained acquisition -> Maven resolver -> a standard Maven repository
over HTTP (the operator-owned ``autonomy.acquisition_registry_mirror``) - but the repository is THIS directory, frozen by a manifest. An operator-approved mirror HOSTNAME
is not trusted CONTENT (SEC-006), so the invariant enforced here is: the same requested coordinates with different bytes never count as the certified acquisition.

    certification_maven_repo.py build   POOL_DIR PATHS_FILE REPO_DIR MANIFEST [--purpose TEXT]   assemble REPO_DIR from the listed repository-relative paths
    certification_maven_repo.py verify  REPO_DIR MANIFEST                                         every file present, byte-exact, nothing extra; exit 1 otherwise
    certification_maven_repo.py attest  REPO_DIR MANIFEST [--central URL]                         BOOTSTRAP ONLY: compare each file with the sha1 Central publishes
    certification_maven_repo.py extend  MANIFEST REPO_DIR PATHS_FILE [--central URL]              BOOTSTRAP ONLY: add files the discovery run asked for and the pool lacked; each is
                                                                                                   downloaded WITH Central's published SHA-1 and kept only if they agree
    certification_maven_repo.py fetch   MANIFEST REPO_DIR [--central URL]                         PROVISION (hosted CI / a new machine): download each manifest path and
                                                                                                   keep it only if its SHA-256 equals the manifest's (bytes are the trust root)
    certification_maven_repo.py serve   REPO_DIR MANIFEST --port N [--bind ADDR] [--log FILE] [--progress FILE] [--require-attested]
                                                                                                   verify, then serve ONLY manifest paths, re-hashing each served file
    certification_maven_repo.py record  POOL_DIR --port N [--bind ADDR] --log FILE                DISCOVERY ONLY: serve a candidate pool and log every request

``build``/``attest``/``record`` are bootstrap and discovery (they may read an unverified pool or contact Central); only ``verify`` and ``serve`` take part in a
certification run, and a bootstrap run is never certification evidence. The manifest records, for every file: Maven coordinates (where the layout names them),
repository-relative path, SHA-256, provenance, and why it is needed; the repository's content digest is derived from (path, sha256) pairs only.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
URL_PREFIX = "/maven2"  # the mirror URL is http://host:port/maven2
CHECKSUM_SUFFIXES = (".sha1", ".md5", ".sha256", ".sha512")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def content_digest(files: Sequence[Mapping[str, Any]]) -> str:
    """The repository's content identity: sha256 over the sorted ``path<TAB>sha256`` lines (nothing else - not provenance, not purpose)."""
    lines = "\n".join(f"{item['path']}\t{item['sha256']}" for item in sorted(files, key=lambda item: item["path"]))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def coordinates_of(path: str) -> Optional[Dict[str, str]]:
    """Maven coordinates named by a repository-relative path ``g/r/o/u/p/artifact/version/artifact-version[-classifier].ext`` (None for metadata files)."""
    base = path
    for suffix in CHECKSUM_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    parts = base.split("/")
    if len(parts) < 4 or parts[-1].startswith("maven-metadata"):
        return None
    artifact, version, filename = parts[-3], parts[-2], parts[-1]
    prefix = f"{artifact}-{version}"
    if not filename.startswith(prefix):
        return None
    rest = filename[len(prefix):]
    if rest.startswith("-"):
        classifier, _, extension = rest[1:].partition(".")
    else:
        classifier, extension = "", rest.lstrip(".")
    return {"groupId": ".".join(parts[:-3]), "artifactId": artifact, "version": version, "type": extension or "?", "classifier": classifier}


def kind_of(path: str) -> str:
    if any(path.endswith(suffix) for suffix in CHECKSUM_SUFFIXES):
        return "checksum"
    if os.path.basename(path).startswith("maven-metadata"):
        return "metadata"
    return "pom" if path.endswith(".pom") else "artifact"


def load_manifest(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("schema") != SCHEMA_VERSION or not isinstance(manifest.get("files"), list):
        raise ValueError(f"{path}: not a schema {SCHEMA_VERSION} certification repository manifest")
    return manifest


def _safe_relpath(path: str) -> bool:
    return bool(path) and not path.startswith("/") and ".." not in path.split("/") and "\\" not in path


def verify(repo_dir: str, manifest: Mapping[str, Any], require_attested: bool = False) -> List[str]:
    """Problems (empty = the repository is exactly what the manifest froze). ``require_attested``: a certification run also needs every artifact and pom to have been
    compared with Central's published SHA-1 at bootstrap (``attest``) - a candidate built from an unverified pool must not certify."""
    problems: List[str] = []
    if require_attested:
        unattested = [i["path"] for i in manifest["files"] if i["kind"] in ("artifact", "pom") and not i["provenance"].get("verified_against")]
        if not manifest.get("attested") or unattested:
            problems.append(f"NOT ATTESTED: {len(unattested)} artifact/pom file(s) were never compared with the checksum Central publishes (bootstrap `attest` has not run)")
    expected = {item["path"]: item for item in manifest["files"]}
    for path, item in sorted(expected.items()):
        if not _safe_relpath(path):
            problems.append(f"unsafe manifest path {path!r}")
            continue
        full = os.path.join(repo_dir, path)
        if not os.path.isfile(full):
            problems.append(f"missing: {path}")
        elif sha256_file(full) != item["sha256"]:
            problems.append(f"BYTES DIFFER from the manifest: {path}")
    present = set()
    for root, _dirs, names in os.walk(repo_dir):
        for name in names:
            present.add(os.path.relpath(os.path.join(root, name), repo_dir).replace(os.sep, "/"))
    problems += [f"not in the manifest: {path}" for path in sorted(present - set(expected))]
    if not problems and content_digest(manifest["files"]) != manifest.get("content_digest"):
        problems.append("the manifest's content_digest does not match its own file list")
    return problems


_FAMILIES = (
    (("org/apache/maven/plugins/", "org/codehaus/mojo/"), "Maven build/lifecycle plugin"),
    (("org/junit/", "junit/", "org/hamcrest/", "org/opentest4j/", "org/apiguardian/"), "JUnit/Hamcrest test framework for generated-code verification"),
    (("org/apache/ignite/",), "Ignite reference application (spring-ignite demo tests)"),
    (("org/springframework/",), "Spring reference application"),
    (("org/apache/maven/",), "Maven core/plugin runtime dependency"),
)


def purpose_of(path: str, base: str) -> str:
    """Why a file is in the repository: its family plus the requirement that put it there (precise per-test attribution comes from the serve log's ``node`` field)."""
    for prefixes, label in _FAMILIES:
        if path.startswith(prefixes):
            return f"{label}; {base}"
    return f"transitive dependency resolved by Maven for the above; {base}"


def build(pool_dir: str, paths: Sequence[str], repo_dir: str, manifest_path: str, purpose: str) -> Dict[str, Any]:
    files = []
    for path in sorted(set(paths)):
        if not _safe_relpath(path):
            raise ValueError(f"unsafe path {path!r}")
        source = os.path.join(pool_dir, path)
        if not os.path.isfile(source) and os.path.basename(path) == "maven-metadata.xml":
            source = os.path.join(os.path.dirname(source), "maven-metadata-central.xml")  # a local repository names metadata by repository id
        target = os.path.join(repo_dir, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        files.append({"path": path, "kind": kind_of(path), "sha256": sha256_file(target), **(coordinates_of(path) or {}),
                      "provenance": {"source": "candidate pool (UNVERIFIED until attest)", "verified_against": None}, "purpose": purpose_of(path, purpose)})
    manifest = {"schema": SCHEMA_VERSION, "layout": "maven2", "files": files, "content_digest": content_digest(files), "attested": False,
                "provisioning": "python scripts/certification_maven_repo.py build POOL PATHS REPO MANIFEST (bootstrap, not certification), then attest while Central answers",
                "serving": "python scripts/certification_maven_repo.py serve REPO MANIFEST --port 18080"}
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)
        handle.write("\n")
    return manifest


def attest(repo_dir: str, manifest_path: str, central: str = "https://repo.maven.apache.org/maven2") -> Tuple[int, List[str]]:
    """BOOTSTRAP: each artifact/pom must match the SHA-1 Central publishes for it. Marks the manifest entry; returns (verified count, problems)."""
    manifest = load_manifest(manifest_path)
    problems, verified = [], 0
    for item in manifest["files"]:
        if item["kind"] not in ("artifact", "pom"):
            continue
        local = hashlib.sha1(open(os.path.join(repo_dir, item["path"]), "rb").read()).hexdigest()  # noqa: S324 - comparing with Central's published SHA-1
        time.sleep(0.05)  # bootstrap is polite: Central rate-limits bursts (MEASURED 429 on 2026-11-02)
        try:
            with urllib.request.urlopen(f"{central}/{item['path']}.sha1", timeout=30) as response:
                published = response.read().decode().split()[0].strip().lower()
        except (urllib.error.URLError, OSError) as error:
            problems.append(f"cannot fetch the published checksum of {item['path']}: {error}")
            continue
        if published != local:
            problems.append(f"{item['path']}: sha1 {local} differs from Central's published {published}")
            continue
        item["provenance"] = {"source": f"{central}/{item['path']}", "verified_against": f"{central}/{item['path']}.sha1", "published_sha1": published}
        verified += 1
    if not problems:
        # attested = EVERY artifact and pom was compared with the SHA-1 Central publishes. Metadata files list versions as of their fetch time and cannot be attested
        # against a live Central; they are frozen by digest like everything else and are not claimed to be authoritative bytes.
        manifest["attested"] = all(i["provenance"].get("verified_against") for i in manifest["files"] if i["kind"] in ("artifact", "pom"))
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=1, sort_keys=True)
            handle.write("\n")
    return verified, problems


def extend(manifest_path: str, repo_dir: str, paths: Sequence[str], central: str = "https://repo.maven.apache.org/maven2") -> List[str]:
    """BOOTSTRAP: add to the frozen set files the discovery run requested and the candidate pool did not hold. Each artifact is downloaded together with the SHA-1 Central
    publishes for it and is kept ONLY if they agree - authoritative by construction. Returns the problems; the manifest is rewritten only when there are none."""
    manifest = load_manifest(manifest_path)
    known = {item["path"] for item in manifest["files"]}
    problems: List[str] = []
    added: List[Dict[str, Any]] = []
    for path in sorted(set(paths) - known):
        if not _safe_relpath(path):
            problems.append(f"unsafe path {path!r}")
            continue
        time.sleep(0.2)  # polite (see attest)
        try:
            with urllib.request.urlopen(f"{central}/{path}", timeout=60) as response:
                body = response.read()
            with urllib.request.urlopen(f"{central}/{path}.sha1", timeout=60) as response:
                published_text = response.read()
        except (urllib.error.URLError, OSError) as error:
            problems.append(f"cannot fetch {path}: {error}")
            continue
        published = published_text.decode().split()[0].strip().lower()
        if hashlib.sha1(body).hexdigest() != published:  # noqa: S324 - comparing with Central's published SHA-1
            problems.append(f"REFUSED {path}: the downloaded bytes do not match the SHA-1 Central publishes")
            continue
        for name, content in ((path, body), (path + ".sha1", published_text)):
            target = os.path.join(repo_dir, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
            attested = {"source": f"{central}/{name}", "verified_against": f"{central}/{path}.sha1", "published_sha1": published}
            added.append({"path": name, "kind": kind_of(name), "sha256": sha256_file(target), **(coordinates_of(name) or {}), "provenance": attested,
                          "purpose": purpose_of(name, "requested by the PRD-034 discovery run, absent from the candidate pool; bootstrapped from Central")})
    if not problems:
        manifest["files"] = sorted(manifest["files"] + added, key=lambda item: item["path"])
        manifest["content_digest"] = content_digest(manifest["files"])
        manifest["attested"] = all(i["provenance"].get("verified_against") for i in manifest["files"] if i["kind"] in ("artifact", "pom"))
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=1, sort_keys=True)
            handle.write("\n")
    return problems


def fetch(manifest: Mapping[str, Any], repo_dir: str, central: str = "https://repo.maven.apache.org/maven2") -> List[str]:
    """PROVISIONING: reconstruct the frozen repository from its manifest. A file is kept ONLY if its SHA-256 equals the manifest's; otherwise it is refused (the
    manifest, not the network, is the trust root). Returns the problems (empty = every file present and exact). Not a certification step."""
    problems: List[str] = []
    for item in manifest["files"]:
        if not _safe_relpath(item["path"]):
            problems.append(f"unsafe manifest path {item['path']!r}")
            continue
        target = os.path.join(repo_dir, item["path"])
        if os.path.isfile(target) and sha256_file(target) == item["sha256"]:
            continue
        try:
            with urllib.request.urlopen(f"{central}/{item['path']}", timeout=60) as response:
                body = response.read()
        except (urllib.error.URLError, OSError) as error:
            problems.append(f"cannot fetch {item['path']}: {error}")
            continue
        if hashlib.sha256(body).hexdigest() != item["sha256"]:
            problems.append(f"REFUSED {item['path']}: the downloaded bytes differ from the frozen manifest")
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(body)
    return problems


class _Log:
    """One JSON line per request. With ``progress`` (the canonical run's progress.json) each line also names the pytest node that was executing, so every
    served file is attributable to the test that required it."""

    def __init__(self, path: Optional[str], progress: Optional[str] = None) -> None:
        self._path, self._progress, self._lock = path, progress, threading.Lock()

    def _node(self) -> Optional[str]:
        try:
            with open(self._progress, "r", encoding="utf-8") as handle:  # type: ignore[arg-type]
                return json.load(handle).get("current")
        except (OSError, ValueError, TypeError):
            return None

    def write(self, record: Mapping[str, Any]) -> None:
        if self._path:
            extra = {"node": self._node()} if self._progress else {}
            with self._lock, open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": round(time.time(), 3), **extra, **record}, sort_keys=True) + "\n")


def _make_handler(repo_dir: str, expected: Optional[Mapping[str, Mapping[str, Any]]], log: _Log):
    """``expected`` None = discovery (serve anything in the pool); otherwise ONLY manifest paths, byte-checked at serve time."""

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "kriya-cert-maven-repo/1"

        def log_message(self, *_args) -> None:  # the structured log below is the record
            return

        def _resolve(self) -> Tuple[Optional[str], str]:
            path = self.path.split("?", 1)[0]
            if not path.startswith(URL_PREFIX + "/"):
                return None, path
            relative = path[len(URL_PREFIX) + 1:]
            if not _safe_relpath(relative):
                return None, relative
            return relative, relative

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            relative, shown = self._resolve()
            if relative is None:
                log.write({"path": shown, "status": 404, "why": "outside the repository prefix"})
                return self._reply(404, b"")
            if expected is not None and relative not in expected:
                log.write({"path": relative, "status": 404, "why": "not in the frozen manifest"})
                return self._reply(404, b"")
            full = os.path.join(repo_dir, relative)
            if expected is None and not os.path.isfile(full) and os.path.basename(relative) == "maven-metadata.xml":
                full = os.path.join(os.path.dirname(full), "maven-metadata-central.xml")  # a local repository names metadata by repository id
            if not os.path.isfile(full):
                log.write({"path": relative, "status": 404, "why": "absent"})
                return self._reply(404, b"")
            body = open(full, "rb").read()
            digest = hashlib.sha256(body).hexdigest()
            if expected is not None and digest != expected[relative]["sha256"]:
                log.write({"path": relative, "status": 409, "sha256": digest, "why": "BYTES DIFFER from the manifest - refused"})
                return self._reply(409, b"")
            log.write({"path": relative, "status": 200, "sha256": digest, "bytes": len(body)})
            return self._reply(200, body)

        def _reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_HEAD = do_GET  # noqa: N815 - Maven probes with HEAD

    return Handler


def serve(repo_dir: str, manifest: Optional[Mapping[str, Any]], port: int, bind: str, log_path: Optional[str], progress: Optional[str] = None) -> None:
    expected = None if manifest is None else {item["path"]: item for item in manifest["files"]}
    server = http.server.ThreadingHTTPServer((bind, port), _make_handler(repo_dir, expected, _Log(log_path, progress)))
    print(f"[maven-repo] serving {'POOL (discovery)' if manifest is None else 'the frozen repository ' + manifest['content_digest'][:16]} on http://{bind}:{port}{URL_PREFIX}", flush=True)
    server.serve_forever()


def main(argv: Sequence[str]) -> int:
    args = list(argv[1:])
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    def option(name: str, default: Optional[str] = None) -> Optional[str]:
        return args[args.index(name) + 1] if name in args else default

    command = args[0]
    positional = [a for i, a in enumerate(args[1:], 1) if not a.startswith("--") and not (i > 1 and args[i - 1].startswith("--"))]
    if command == "build" and len(positional) == 4:
        paths = [line.strip() for line in open(positional[1], encoding="utf-8") if line.strip()]
        manifest = build(positional[0], paths, positional[2], positional[3], option("--purpose", "PRD-034 mandatory real-Maven certification tests") or "")
        print(f"[maven-repo] built {len(manifest['files'])} files, content digest {manifest['content_digest']}")
        return 0
    if command == "verify" and len(positional) == 2:
        problems = verify(positional[0], load_manifest(positional[1]), "--require-attested" in args)
        print("[maven-repo] verify: " + ("PASS" if not problems else "FAIL") + "".join(f"\n  - {p}" for p in problems[:30]))
        return 0 if not problems else 1
    if command == "attest" and len(positional) == 2:
        verified, problems = attest(positional[0], positional[1], option("--central", "https://repo.maven.apache.org/maven2") or "")
        print(f"[maven-repo] attest: {verified} verified against Central's published sha1" + "".join(f"\n  - {p}" for p in problems[:30]))
        return 0 if not problems else 1
    if command == "extend" and len(positional) == 3:
        paths = [line.strip() for line in open(positional[2], encoding="utf-8") if line.strip()]
        problems = extend(positional[0], positional[1], paths, option("--central", "https://repo.maven.apache.org/maven2") or "")
        print("[maven-repo] extend: " + ("PASS" if not problems else "FAIL") + "".join(f"\n  - {p}" for p in problems[:30]))
        return 0 if not problems else 1
    if command == "fetch" and len(positional) == 2:
        problems = fetch(load_manifest(positional[0]), positional[1], option("--central", "https://repo.maven.apache.org/maven2") or "")
        print("[maven-repo] fetch: " + ("PASS" if not problems else "FAIL") + "".join(f"\n  - {p}" for p in problems[:30]))
        return 0 if not problems else 1
    if command == "serve" and len(positional) == 2:
        manifest = load_manifest(positional[1])
        problems = verify(positional[0], manifest, "--require-attested" in args)
        if problems:  # refuse to serve bytes that are not the frozen (and, for a certification run, attested) set
            print("[maven-repo] REFUSING to serve: the repository is not the frozen set" + "".join(f"\n  - {p}" for p in problems[:30]), file=sys.stderr)
            return 1
        serve(positional[0], manifest, int(option("--port", "18080") or 18080), option("--bind", "127.0.0.1") or "127.0.0.1", option("--log"), option("--progress"))
        return 0
    if command == "record" and len(positional) == 1:
        serve(positional[0], None, int(option("--port", "18080") or 18080), option("--bind", "127.0.0.1") or "127.0.0.1", option("--log"))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
