"""D1 (2026-10-01 live KNOW rehearsal): language-server analysis is
observational with respect to the candidate repository.

Measured with the real JDTLS 1.60.0 at ee65e1c: rooted at the candidate
worktree, its Maven import wrote .project, .classpath, .settings/*.prefs and
target/classes/** into it (import/autobuild preferences removed target/ only),
and the file-integrity gate correctly stopped the run. JDTLS now runs on a
Kriya-owned private mirror. D1b, found on the way: diagnostics are published
under canonical URIs, so a symlinked project root (macOS /var) silently got
none; the mirror is canonical."""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_lsp import _FakeStdin, _FakeStdout

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


@pytest.mark.asyncio
async def test_jdtls_is_rooted_at_the_private_mirror_never_the_candidate(tmp_path):
    project = _candidate(tmp_path / "candidate")
    client = JdtlsClient(str(project), "/fake/jdtls")
    client.process = MagicMock()
    client.process.stdin = _FakeStdin()
    client.process.stdout = _FakeStdout([{"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}])
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=client.process)):
        await client.start()
    written = client.process.stdin.written.decode()
    initialize, _ = json.JSONDecoder().raw_decode(written[written.index("{"):])  # the first frame
    root_uri = initialize["params"]["rootUri"]
    mirror = client._mirror  # pylint: disable=protected-access
    assert root_uri == "file://" + mirror and mirror == os.path.realpath(mirror)
    assert not mirror.startswith(os.path.realpath(str(project)))
    assert (tmp_path / "candidate" / "src/main/java/demo/A.java").read_text() == "package demo;\npublic class A {}\n"
    client._reader_task.cancel()  # pylint: disable=protected-access
    shutil.rmtree(mirror, ignore_errors=True)
    shutil.rmtree(client._data_dir, ignore_errors=True)  # pylint: disable=protected-access


def _git_state(root):
    out = subprocess.run(["git", "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored"],
                         cwd=root, capture_output=True, check=True).stdout
    return sorted(record for record in out.split(b"\0") if record)


@pytest.mark.skipif(shutil.which("jdtls") is None, reason="the real JDTLS is not installed (CI); deterministic tests above")
def test_the_real_jdtls_analyses_without_any_candidate_write(tmp_path):
    """The measured scenario with the real server: a greenfield Maven
    candidate with no .gitignore, JDTLS started and asked for diagnostics.
    No new Git state (no .project/.classpath/.settings/target), and the real
    diagnostic still arrives (on a symlinked tmp path - D1b)."""
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "src/main/java/demo").mkdir(parents=True)
    (root / "pom.xml").write_text(POM)
    source = root / "src/main/java/demo/A.java"
    source.write_text("package demo;\npublic class A {}\n")
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    before, digest = _git_state(root), _digest(root)

    async def analyse():
        client = JdtlsClient(str(root), shutil.which("jdtls"))
        await client.start()
        try:
            return await client.check_file(str(source), "package demo;\npublic class A { int x() { return missing(); } }\n",
                                           timeout=60)
        finally:
            await client.shutdown()

    diagnostics = asyncio.run(analyse())
    assert _git_state(root) == before
    assert _digest(root) == digest
    assert any("missing()" in d.get("message", "") for d in diagnostics), diagnostics


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
