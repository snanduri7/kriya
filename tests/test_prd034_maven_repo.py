"""PRD-034: the frozen certification Maven repository (scripts/certification_maven_repo.py).

The invariant (owner ruling 2026-11-02, SEC-006): the same requested coordinates with different artifact bytes must never silently count as the certified
acquisition. An operator-approved mirror HOSTNAME is not trusted CONTENT, so the enforcement lives at the serving boundary and in ``verify``:
F. CONTENT TAMPER - altered bytes are refused (at start, and at every served request);  E. MISSING ARTIFACT - a path outside the frozen set is a 404, never a fallback;
D. the manifest binds every file's coordinates, SHA-256 and provenance, and the repository content digest ignores everything but (path, sha256).
"""
import hashlib
import http.server
import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "certification_maven_repo.py"
spec = importlib.util.spec_from_file_location("certification_maven_repo", SCRIPT)
repo_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_tool)

FILES = {
    "org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.jar": b"clean-jar-bytes",
    "org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.jar.sha1": hashlib.sha1(b"clean-jar-bytes").hexdigest().encode(),  # the real checksum, in the frozen bare form (fetch derives it)
    "org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.pom": b"<project>clean</project>",
    "org/apache/maven/plugins/maven-metadata-central.xml": b"<metadata/>",
    "junit/junit/4.13.2/junit-4.13.2-sources.jar": b"junit-sources",
}


def _pool(tmp_path):
    pool = tmp_path / "pool"
    for path, body in FILES.items():
        (pool / path).parent.mkdir(parents=True, exist_ok=True)
        (pool / path).write_bytes(body)
    return pool


def _frozen(tmp_path, paths=None):
    pool, repo, manifest_path = _pool(tmp_path), tmp_path / "repo", tmp_path / "manifest.json"
    manifest = repo_tool.build(str(pool), list(paths or FILES), str(repo), str(manifest_path), "test purpose")
    return repo, manifest_path, manifest


class _Served:
    """The frozen repository served by the tool's own handler on an ephemeral port."""

    def __init__(self, repo, manifest, tmp_path):
        self.log_path = tmp_path / "requests.jsonl"
        expected = {item["path"]: item for item in manifest["files"]}
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), repo_tool._make_handler(str(repo), expected, repo_tool._Log(str(self.log_path))))  # pylint: disable=protected-access
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.server.server_address[1]}{repo_tool.URL_PREFIX}/{path}", timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    def log(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]


def test_d_the_manifest_binds_coordinates_digest_provenance_and_purpose_for_every_file(tmp_path):
    _, _, manifest = _frozen(tmp_path)
    by_path = {item["path"]: item for item in manifest["files"]}
    jar = by_path["org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.jar"]
    assert (jar["groupId"], jar["artifactId"], jar["version"], jar["type"], jar["classifier"]) == (
        "org.apache.maven.plugins", "maven-clean-plugin", "3.2.0", "jar", "")
    assert jar["sha256"] == hashlib.sha256(FILES[jar["path"]]).hexdigest() and jar["purpose"] == "Maven build/lifecycle plugin; test purpose" and jar["kind"] == "artifact"
    assert by_path["junit/junit/4.13.2/junit-4.13.2-sources.jar"]["classifier"] == "sources"
    assert by_path["org/apache/maven/plugins/maven-metadata-central.xml"]["kind"] == "metadata"
    assert by_path["org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.jar.sha1"]["kind"] == "checksum"
    assert "UNVERIFIED" in jar["provenance"]["source"], "a pool-built manifest says it is unverified until attested"


def test_d_the_content_digest_depends_on_paths_and_bytes_only(tmp_path):
    _, _, manifest = _frozen(tmp_path)
    renamed = json.loads(json.dumps(manifest))
    for item in renamed["files"]:
        item["purpose"], item["provenance"] = "another purpose", {"source": "elsewhere"}
    assert repo_tool.content_digest(renamed["files"]) == manifest["content_digest"]
    changed = json.loads(json.dumps(manifest))
    changed["files"][0]["sha256"] = "0" * 64
    assert repo_tool.content_digest(changed["files"]) != manifest["content_digest"]


def test_f_content_tamper_is_refused_by_verify_even_when_the_coordinates_are_unchanged(tmp_path):
    repo, _, manifest = _frozen(tmp_path)
    assert repo_tool.verify(str(repo), manifest) == []
    jar = repo / "org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.jar"
    jar.write_bytes(b"clean-jar-bytes-TAMPERED")
    (jar.parent / "maven-clean-plugin-3.2.0.jar.sha1").write_bytes(hashlib.sha1(b"clean-jar-bytes-TAMPERED").hexdigest().encode())  # a recomputed sidecar
    problems = repo_tool.verify(str(repo), manifest)
    assert any("BYTES DIFFER" in p and p.endswith("maven-clean-plugin-3.2.0.jar") for p in problems), "Maven's own checksum (the sidecar) would have passed this"


def test_f_an_extra_or_missing_file_is_refused_by_verify(tmp_path):
    repo, _, manifest = _frozen(tmp_path)
    (repo / "org/evil/evil/1/evil-1.jar").parent.mkdir(parents=True)
    (repo / "org/evil/evil/1/evil-1.jar").write_bytes(b"x")
    assert any("not in the manifest: org/evil/evil/1/evil-1.jar" in p for p in repo_tool.verify(str(repo), manifest))
    (repo / "org/evil/evil/1/evil-1.jar").unlink()
    (repo / "junit/junit/4.13.2/junit-4.13.2-sources.jar").unlink()
    assert any(p == "missing: junit/junit/4.13.2/junit-4.13.2-sources.jar" for p in repo_tool.verify(str(repo), manifest))


def test_f_serve_refuses_to_start_on_a_repository_that_is_not_the_frozen_set(tmp_path):
    repo, manifest_path, _ = _frozen(tmp_path)
    (repo / "junit/junit/4.13.2/junit-4.13.2-sources.jar").write_bytes(b"tampered")
    assert repo_tool.main(["x", "serve", str(repo), str(manifest_path), "--port", "0"]) == 1


def test_f_every_served_file_is_re_hashed_so_a_change_after_startup_is_refused_not_served(tmp_path):
    repo, _, manifest = _frozen(tmp_path)
    jar = "org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.jar"
    with _Served(repo, manifest, tmp_path) as served:
        assert served.get(jar) == (200, FILES[jar])
        (repo / jar).write_bytes(b"swapped after the start")
        assert served.get(jar) == (409, b"")
        log = served.log()
    assert [(r["status"], r["path"]) for r in log] == [(200, jar), (409, jar)]
    assert log[0]["sha256"] == hashlib.sha256(FILES[jar]).hexdigest() and "BYTES DIFFER" in log[1]["why"]


def test_e_a_path_outside_the_frozen_manifest_is_a_404_and_is_logged_no_fallback_exists(tmp_path):
    subset = [p for p in FILES if "junit" not in p]  # the frozen set LACKS junit
    repo, _, manifest = _frozen(tmp_path, subset)
    with _Served(repo, manifest, tmp_path) as served:
        assert served.get("junit/junit/4.13.2/junit-4.13.2-sources.jar")[0] == 404
        assert served.get("../../etc/passwd")[0] == 404
        assert served.get("org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.pom")[0] == 200
        log = served.log()
    assert next(r for r in log if "junit" in r["path"])["why"] == "not in the frozen manifest"


def test_even_a_file_present_on_disk_is_not_served_unless_the_manifest_lists_it(tmp_path):
    repo, _, manifest = _frozen(tmp_path)
    (repo / "org/unlisted").mkdir(parents=True)
    (repo / "org/unlisted/x.jar").write_bytes(b"x")
    with _Served(repo, manifest, tmp_path) as served:
        assert served.get("org/unlisted/x.jar")[0] == 404


def test_the_recorder_serves_a_pool_maps_metadata_names_and_logs_misses(tmp_path):
    pool = _pool(tmp_path)
    handler = repo_tool._make_handler(str(pool), None, repo_tool._Log(str(tmp_path / "rec.jsonl")))  # pylint: disable=protected-access
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}{repo_tool.URL_PREFIX}"
        assert urllib.request.urlopen(f"{base}/org/apache/maven/plugins/maven-metadata.xml", timeout=10).read() == b"<metadata/>"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/no/such/thing/1/thing-1.jar", timeout=10)
    finally:
        server.shutdown()
        server.server_close()
    rows = [json.loads(line) for line in (tmp_path / "rec.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == [200, 404] and rows[1]["path"] == "no/such/thing/1/thing-1.jar"


class _Central:
    """A fake 'Central' for attest/fetch: serves what it is given."""

    def __init__(self, files):
        files = dict(files)
        self.requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                return

            def do_GET(self):  # noqa: N802
                self.server.requests.append(self.path[len("/maven2/"):])
                body = files.get(self.path[len("/maven2/"):])
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.requests = self.requests
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/maven2"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_attest_marks_files_that_match_the_published_sha1_and_refuses_a_difference(tmp_path):
    repo, manifest_path, _ = _frozen(tmp_path, [p for p in FILES if p.endswith((".jar", ".pom")) and "sources" not in p])
    published = {p + ".sha1": hashlib.sha1(b).hexdigest().encode() for p, b in FILES.items()}
    central = _Central(published)
    try:
        verified, problems = repo_tool.attest(str(repo), str(manifest_path), central.url)
        assert (verified, problems) == (2, [])
        item = repo_tool.load_manifest(str(manifest_path))["files"][0]
        assert item["provenance"]["verified_against"].endswith(".sha1") and item["provenance"]["published_sha1"]
    finally:
        central.close()
    lying = _Central({p + ".sha1": b"0" * 40 for p in FILES})
    try:
        _, problems = repo_tool.attest(str(repo), str(manifest_path), lying.url)
        assert problems and all("differs from Central's published" in p for p in problems)
    finally:
        lying.close()


def test_fetch_provisions_exactly_the_frozen_bytes_and_refuses_anything_else(tmp_path):
    _, _, manifest = _frozen(tmp_path)
    good = _Central(FILES)
    fresh = tmp_path / "fresh"
    try:
        assert repo_tool.fetch(manifest, str(fresh), good.url) == []
        assert repo_tool.verify(str(fresh), manifest) == [], "a hosted runner reconstructs the identical repository from the manifest"
    finally:
        good.close()
    evil = _Central({**FILES, "junit/junit/4.13.2/junit-4.13.2-sources.jar": b"EVIL"})
    other = tmp_path / "other"
    try:
        problems = repo_tool.fetch(manifest, str(other), evil.url)
        assert any("REFUSED junit/junit/4.13.2/junit-4.13.2-sources.jar" in p for p in problems)
        assert not (other / "junit/junit/4.13.2/junit-4.13.2-sources.jar").exists(), "differing bytes are never written"
    finally:
        evil.close()


def test_the_cli_verifies_and_reports(tmp_path, capsys):
    repo, manifest_path, _ = _frozen(tmp_path)
    assert repo_tool.main(["x", "verify", str(repo), str(manifest_path)]) == 0
    os.remove(repo / "junit/junit/4.13.2/junit-4.13.2-sources.jar")
    assert repo_tool.main(["x", "verify", str(repo), str(manifest_path)]) == 1
    assert "missing: junit/junit/4.13.2/junit-4.13.2-sources.jar" in capsys.readouterr().out


def test_extend_adds_a_file_only_when_the_bytes_agree_with_the_sha1_central_publishes(tmp_path):
    repo, manifest_path, manifest = _frozen(tmp_path)
    new = "org/apache/maven/doxia/doxia-core/1.11.1/doxia-core-1.11.1.jar"
    body = b"doxia-core-bytes"
    paths = tmp_path / "wanted.txt"
    paths.write_text(new + "\n")
    liar = _Central({new: body, new + ".sha1": b"0" * 40})
    try:
        problems = repo_tool.extend(str(manifest_path), str(repo), [new], liar.url)
        assert problems and "do not match the SHA-1 Central publishes" in problems[0]
        assert not (repo / new).exists() and repo_tool.load_manifest(str(manifest_path))["content_digest"] == manifest["content_digest"], "nothing added, manifest untouched"
    finally:
        liar.close()
    honest = _Central({new: body, new + ".sha1": hashlib.sha1(body).hexdigest().encode()})
    try:
        assert repo_tool.extend(str(manifest_path), str(repo), [new], honest.url) == []
    finally:
        honest.close()
    after = repo_tool.load_manifest(str(manifest_path))
    added = {i["path"]: i for i in after["files"]}
    assert added[new]["provenance"]["verified_against"].endswith(".sha1") and new + ".sha1" in added
    assert repo_tool.verify(str(repo), after) == [] and after["content_digest"] != manifest["content_digest"]


def test_extend_freezes_the_closed_bare_sidecar_even_when_centrals_own_text_is_irregular_and_a_fresh_fetch_rebuilds_it(tmp_path):
    """2026-11-04 (MEASURED on the real tool): `extend` used to keep Central's raw `.sha1` text, and Central's text for plexus-utils-1.0.4.pom is `hash  /home/projects/maven/...`
    - outside the closed renderings - so the extended manifest verified on the machine that built it but `fetch` REFUSED it on every fresh machine."""
    repo, manifest_path, _ = _frozen(tmp_path)
    new = "org/apache/maven/doxia/doxia-core/1.11.1/doxia-core-1.11.1.jar"
    body = b"doxia-core-bytes"
    digest = hashlib.sha1(body).hexdigest()
    irregular = f"{digest}  /home/projects/maven/repository-staging/elsewhere/{new}\r\n".encode()
    central = _Central({**FILES, new: body, new + ".sha1": irregular})
    try:
        assert repo_tool.extend(str(manifest_path), str(repo), [new], central.url) == []
        after = repo_tool.load_manifest(str(manifest_path))
        assert (repo / (new + ".sha1")).read_bytes() == digest.encode("ascii"), "the sidecar is the closed bare rendering derived from the payload, not Central's text"
        fresh = tmp_path / "fresh-machine"
        assert repo_tool.fetch(after, str(fresh), central.url) == [], "a fresh machine can rebuild every frozen file, the extended ones included"
        assert repo_tool.verify(str(fresh), after) == []
    finally:
        central.close()


# ---- HOSTED-MAVEN-SIDECAR-REPRESENTATION-001: a `.sha1` sidecar is DERIVED from the verified payload, never taken from Central's text ----

JAR = "org/apache/maven/plugins/maven-clean-plugin/3.2.0/maven-clean-plugin-3.2.0.jar"
SIDECAR = JAR + ".sha1"
JAR_SHA1 = hashlib.sha1(FILES[JAR]).hexdigest()


def _central_with_sidecar(text: bytes):
    return _Central({**FILES, SIDECAR: text})


@pytest.mark.parametrize("representation", [
    JAR_SHA1.encode(),                                                                     # bare, no newline (the frozen form)
    (JAR_SHA1 + "\n").encode(),                                                            # bare + newline
    (JAR_SHA1 + "\r\n").encode(),                                                          # CRLF (Central serves this for velocity-tools)
    (JAR_SHA1 + "  maven-clean-plugin-3.2.0.jar\n").encode(),                              # `hash  name`
    (JAR_SHA1 + " *maven-clean-plugin-3.2.0.jar").encode(),                                # `hash *name` (no newline)
    (JAR_SHA1 + "  /home/projects/maven/repository-staging/to-ibiblio/maven2/x.jar\n").encode(),  # staging path
    (JAR_SHA1 + "  -\n").encode(),                                                         # `hash  -`
    b"not even a checksum",                                                                # the remote text is not consulted at all
], ids=["bare", "bare-newline", "crlf", "hash-name", "hash-star-name", "staging-path", "hash-dash", "not-a-checksum"])
def test_fetch_writes_the_frozen_sidecar_whatever_representation_central_serves(tmp_path, representation):
    _, _, manifest = _frozen(tmp_path)
    central = _central_with_sidecar(representation)
    fresh = tmp_path / "fresh"
    try:
        assert repo_tool.fetch(manifest, str(fresh), central.url) == []
        assert (fresh / SIDECAR).read_bytes() == FILES[SIDECAR] == JAR_SHA1.encode(), "the written sidecar is the canonical frozen bytes"
        assert repo_tool.verify(str(fresh), manifest) == []
        assert SIDECAR not in central.requests, "a .sha1 entry is never downloaded"
        assert JAR in central.requests
    finally:
        central.close()


def test_a_wrong_payload_fails_closed_and_no_sidecar_is_derived_from_it(tmp_path):
    _, _, manifest = _frozen(tmp_path)
    central = _Central({**FILES, JAR: b"tampered-jar-bytes"})
    fresh = tmp_path / "fresh"
    try:
        problems = repo_tool.fetch(manifest, str(fresh), central.url)
    finally:
        central.close()
    assert any(p.startswith(f"REFUSED {JAR}:") for p in problems)
    assert any(p.startswith(f"REFUSED {SIDECAR}:") and "has not passed the manifest SHA-256" in p for p in problems)
    assert not (fresh / JAR).exists() and not (fresh / SIDECAR).exists(), "neither the bad payload nor a sidecar derived from it is written"


def test_a_wrong_frozen_sidecar_digest_fails_closed_even_when_the_payload_is_exact(tmp_path):
    _, _, manifest = _frozen(tmp_path)
    tampered = json.loads(json.dumps(manifest))
    entry = next(item for item in tampered["files"] if item["path"] == SIDECAR)
    entry["sha256"] = hashlib.sha256(b"0" * 40).hexdigest()  # the manifest froze some other sidecar than the one the verified payload derives
    central = _Central(FILES)
    fresh = tmp_path / "fresh"
    try:
        problems = repo_tool.fetch(tampered, str(fresh), central.url)
    finally:
        central.close()
    assert [p for p in problems if SIDECAR in p] == [f"REFUSED {SIDECAR}: the sidecar frozen for it matches none of the closed sidecar renderings, derived from the verified payload"]
    assert (fresh / JAR).read_bytes() == FILES[JAR], "the exact payload is still provisioned"
    assert not (fresh / SIDECAR).exists(), "a sidecar that does not match its frozen digest is never written"


def test_a_frozen_sidecar_that_is_not_the_checksum_of_its_payload_fails_closed(tmp_path):
    pool = tmp_path / "pool"
    for path, body in {**FILES, SIDECAR: b"deadbeef"}.items():  # the pre-fix fixture: a sidecar frozen with a value that is not the jar's SHA-1
        (pool / path).parent.mkdir(parents=True, exist_ok=True)
        (pool / path).write_bytes(body)
    manifest = repo_tool.build(str(pool), list(FILES), str(tmp_path / "repo"), str(tmp_path / "m.json"), "test purpose")
    central = _Central({**FILES, SIDECAR: b"deadbeef"})  # Central agrees with the (wrong) frozen sidecar: the old fetch would have accepted this
    try:
        problems = repo_tool.fetch(manifest, str(tmp_path / "fresh"), central.url)
    finally:
        central.close()
    assert any(p.startswith(f"REFUSED {SIDECAR}:") and "matches none of the closed sidecar renderings" in p for p in problems)


def test_a_sidecar_without_a_manifest_payload_and_an_unsupported_checksum_kind_are_refused(tmp_path):
    _, _, manifest = _frozen(tmp_path)
    orphan = json.loads(json.dumps(manifest))
    orphan["files"] = [item for item in orphan["files"] if item["path"] != JAR]
    md5_path = JAR + ".md5"
    orphan["files"].append({"path": md5_path, "kind": "checksum", "sha256": hashlib.sha256(b"x").hexdigest()})
    central = _Central({**FILES, md5_path: b"x"})
    try:
        problems = repo_tool.fetch(orphan, str(tmp_path / "fresh"), central.url)
    finally:
        central.close()
    assert any(p.startswith(f"REFUSED {SIDECAR}:") and "is not in the manifest" in p for p in problems)
    assert any(p.startswith(f"REFUSED {md5_path}:") and "only .sha1 sidecars are derived" in p for p in problems)
    assert md5_path not in central.requests, "no fallback acquisition for a checksum kind the tool does not derive"


def _frozen_sidecar_form(tmp_path, sidecar_bytes: bytes):
    pool = tmp_path / "pool"
    for path, body in {**FILES, SIDECAR: sidecar_bytes}.items():
        (pool / path).parent.mkdir(parents=True, exist_ok=True)
        (pool / path).write_bytes(body)
    return repo_tool.build(str(pool), list(FILES), str(tmp_path / "repo"), str(tmp_path / "m.json"), "test purpose")


@pytest.mark.parametrize("name, frozen", [
    ("bare", JAR_SHA1.encode()),
    ("basename", f"{JAR_SHA1}  maven-clean-plugin-3.2.0.jar\n".encode()),
    ("staging-path", f"{JAR_SHA1}  {repo_tool.STAGING_PATH_PREFIX}{JAR}\n".encode()),
], ids=["bare", "basename", "staging-path"])
def test_each_closed_renderer_provisions_a_sidecar_frozen_in_that_form_regardless_of_central(tmp_path, name, frozen):
    manifest = _frozen_sidecar_form(tmp_path, frozen)
    central = _central_with_sidecar(b"0" * 40 + b" *whatever\r\n")  # remote .sha1 text is irrelevant
    fresh = tmp_path / "fresh"
    try:
        assert repo_tool.fetch(manifest, str(fresh), central.url) == []
        assert (fresh / SIDECAR).read_bytes() == frozen, name
        assert SIDECAR not in central.requests
    finally:
        central.close()


@pytest.mark.parametrize("frozen", [
    (JAR_SHA1 + "\n").encode(), (JAR_SHA1 + "\r\n").encode(), f"{JAR_SHA1} *maven-clean-plugin-3.2.0.jar".encode(), f"{JAR_SHA1}  -\n".encode(),
    f"{JAR_SHA1}  /some/other/staging/{JAR}\n".encode(), (JAR_SHA1.upper()).encode(),
], ids=["bare-newline", "crlf", "hash-star-name", "hash-dash", "other-staging-prefix", "uppercase-hash"])
def test_a_sidecar_frozen_in_a_form_outside_the_closed_set_fails_closed(tmp_path, frozen):
    manifest = _frozen_sidecar_form(tmp_path, frozen)
    central = _Central(FILES)
    fresh = tmp_path / "fresh"
    try:
        problems = repo_tool.fetch(manifest, str(fresh), central.url)
    finally:
        central.close()
    assert [p for p in problems if SIDECAR in p] == [f"REFUSED {SIDECAR}: the sidecar frozen for it matches none of the closed sidecar renderings, derived from the verified payload"]
    assert not (fresh / SIDECAR).exists()


def test_select_sidecar_accepts_exactly_one_match_and_refuses_zero_or_ambiguous_matches():
    one, two = b"one", b"two"
    digest = lambda content: hashlib.sha256(content).hexdigest()  # noqa: E731
    assert repo_tool.select_sidecar({"a": one, "b": two}, digest(two)) == (two, "b")
    assert repo_tool.select_sidecar({"a": one, "b": two}, digest(b"other")) == (None, "matches none of the closed sidecar renderings")
    chosen, detail = repo_tool.select_sidecar({"a": one, "b": one, "c": two}, digest(one))  # two renderers producing identical bytes: not constructible from the real table
    assert chosen is None and detail.startswith("is ambiguous") and "'a'" in detail and "'b'" in detail


def test_the_renderer_table_is_exactly_the_three_frozen_forms():
    rendered = repo_tool.sidecar_renderings(b"x", "g/a/1/a-1.jar")
    assert set(rendered) == {"bare", "basename", "staging-path"} and len({*rendered.values()}) == 3, "the closed set is not widened and never self-collides"


def test_a_tampered_payload_already_on_disk_is_not_a_basis_for_a_sidecar(tmp_path):
    """A payload that exists but never passed the manifest SHA-256 (it was on disk before fetch and the download of the exact bytes failed) must be named as the reason."""
    _, _, manifest = _frozen(tmp_path)
    fresh = tmp_path / "fresh"
    (fresh / JAR).parent.mkdir(parents=True)
    (fresh / JAR).write_bytes(b"tampered-jar-bytes")
    central = _Central({p: b for p, b in FILES.items() if p != JAR})  # Central cannot supply the exact payload, so the tampered file stays where it is
    try:
        problems = repo_tool.fetch(manifest, str(fresh), central.url)
    finally:
        central.close()
    assert any(p.startswith(f"cannot fetch {JAR}") for p in problems)
    assert [p for p in problems if SIDECAR in p] == [f"REFUSED {SIDECAR}: its payload {JAR} is not in the manifest or has not passed the manifest SHA-256"]
    assert not (fresh / SIDECAR).exists()


def test_the_docker_host_bind_rule_is_one_rule_shared_with_certify_sh():
    """HOSTED-MIRROR-REACHABILITY-001: a container reaches the host mirror through host-gateway on Linux Docker Engine (so it must bind beyond loopback) and through a
    loopback proxy on Docker Desktop. The tool owns the rule; certify.sh states the same one for the canonical mirror and the wheel index."""
    assert repo_tool.docker_host_bind_address("Linux") == "0.0.0.0"
    assert repo_tool.docker_host_bind_address("Darwin") == "127.0.0.1" and repo_tool.docker_host_bind_address("Windows") == "127.0.0.1"
    certify = (Path(__file__).resolve().parent.parent / "scripts" / "certify.sh").read_text(encoding="utf-8")
    assert 'Linux) MAVEN_BIND="0.0.0.0" ;; *) MAVEN_BIND="127.0.0.1" ;;' in certify
    assert 'Linux) PIP_BIND="0.0.0.0" ;; *) PIP_BIND="127.0.0.1" ;;' in certify
