"""D1 (2026-10-01 live KNOW rehearsal): language-server analysis is
observational with respect to the candidate repository.

Measured with the real JDTLS 1.60.0 at ee65e1c: rooted at the candidate
worktree, its Maven import wrote .project, .classpath, .settings/*.prefs and
target/classes/** into it (import/autobuild preferences removed target/ only),
and the file-integrity gate correctly stopped the run. JDTLS now runs on a
Kriya-owned private mirror. D1b, found on the way: diagnostics are published
under canonical URIs, so a symlinked project root (macOS /var) silently got
none; the mirror is canonical."""
import hashlib
import os
import subprocess

import pytest

from kriya.tools.lsp import JdtlsClient

POM = ('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>d</groupId>'
       "<artifactId>d</artifactId><version>1</version><properties><maven.compiler.release>17</maven.compiler.release>"
       "</properties></project>\n")


def _candidate(root):
    (root / "src/main/java/demo").mkdir(parents=True)
    (root / "pom.xml").write_text(POM)
    (root / "src/main/java/demo/A.java").write_text("package demo;\npublic class A {}\n")
    for skipped in (".git/config", ".kriya/state.json", "target/classes/Old.class", ".settings/x.prefs"):
        (root / skipped).parent.mkdir(parents=True, exist_ok=True)
        (root / skipped).write_text("not project source\n")
    (root / ".project").write_text("<projectDescription/>\n")
    return root


def _digest(root):
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def _client(project, mirror):
    client = JdtlsClient(str(project), "/fake/jdtls")
    client._mirror = os.path.realpath(str(mirror))  # pylint: disable=protected-access
    return client


def test_the_mirror_holds_the_candidates_source_and_never_writes_the_candidate(tmp_path):
    project, mirror = _candidate(tmp_path / "candidate"), tmp_path / "mirror"
    mirror.mkdir()
    before = _digest(project)
    client = _client(project, mirror)
    client._sync_mirror()  # pylint: disable=protected-access
    assert sorted(_digest(mirror)) == ["pom.xml", "src/main/java/demo/A.java"]  # no VCS, Kriya state, build, IDE
    (project / "src/main/java/demo/B.java").write_text("package demo;\nclass B {}\n")  # a new candidate file
    (project / "src/main/java/demo/A.java").write_text("package demo;\npublic class A { int y; }\n")  # changed
    (project / "pom.xml").unlink()  # removed
    before = _digest(project)
    client._sync_mirror()  # pylint: disable=protected-access
    assert sorted(_digest(mirror)) == ["src/main/java/demo/A.java", "src/main/java/demo/B.java"]
    assert (mirror / "src/main/java/demo/A.java").read_text() == "package demo;\npublic class A { int y; }\n"
    assert _digest(project) == before  # the candidate is only ever read


def test_candidate_paths_map_to_canonical_mirror_paths_and_nothing_outside(tmp_path):
    project, mirror = _candidate(tmp_path / "candidate"), tmp_path / "mirror"
    mirror.mkdir()
    link = tmp_path / "link-to-candidate"
    link.symlink_to(project)  # the macOS /var -> /private/var shape
    client = JdtlsClient(str(link), "/fake/jdtls")
    client._mirror = os.path.realpath(str(mirror))  # pylint: disable=protected-access
    mapped = client._mirror_path(str(link / "src/main/java/demo/A.java"))  # pylint: disable=protected-access
    assert mapped == os.path.join(os.path.realpath(str(mirror)), "src/main/java/demo/A.java")
    with pytest.raises(ValueError):
        client._mirror_path(str(tmp_path / "elsewhere.java"))  # pylint: disable=protected-access


def _git_state(root):
    out = subprocess.run(["git", "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored"],
                         cwd=root, capture_output=True, check=True).stdout
    return sorted(record for record in out.split(b"\0") if record)


@pytest.mark.asyncio
async def test_every_query_resyncs_the_mirror_so_jdtls_never_sees_stale_siblings(tmp_path):
    from test_lsp import _make_client

    project, mirror = _candidate(tmp_path / "candidate"), tmp_path / "mirror"
    mirror.mkdir()
    client = _make_client([], project, mirror)
    client._sync_mirror()  # pylint: disable=protected-access  # as start() does
    sibling = project / "src/main/java/demo/Helper.java"
    sibling.write_text("package demo;\nclass Helper { static int one() { return 1; } }\n")  # written by a later attempt
    source = str(project / "src/main/java/demo/A.java")
    client._diagnostics["file://" + client._mirror_path(source)] = []  # pylint: disable=protected-access
    await client.check_file(source, "package demo;\npublic class A { int x = Helper.one(); }\n", timeout=1)
    assert (mirror / "src/main/java/demo/Helper.java").read_text() == sibling.read_text()
