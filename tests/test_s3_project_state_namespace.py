"""S3 (C-05 / R-05): project-derived state belongs to exactly one project.

The reproduced defect (docs/architecture/evidence/a5, probes P1-P4, re-measured at 57c966e): two projects with no ``paths`` section resolved
the SAME ``paths.memory`` (the installation directory), so project B read project A's index and chunk text, B was "already indexed" because
A was, and indexing B deleted A's index. These tests drive the real ``load_config`` and the real ``RepositoryAnalyzer`` over two project
roots in one installation (one state directory), which is the measured scenario; the explicit-shared-directory tests are the case in which
only the recorded identity (not the location) separates the projects.
"""
import asyncio
import hashlib
import os
import sqlite3
import subprocess

import pytest
from _fake_embedding import StaticEmbedder

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.analyzer.graph import DependencyGraph
from kriya.config import resolution
from kriya.config.config import AppConfig, load_config
from kriya.config.environment import kriya_install_dir
from kriya.config.resolution import PROJECT_IDENTITY_MISMATCH, resolve_config_state
from kriya.context.workspace_identity import workspace_identity
from kriya.control.run_coordinator import authorize_candidate_workspace, begin_mutating_run
from kriya.core.state_paths import (
    PROJECT_IDENTITY_MISSING,
    PROJECT_SOURCE_FOREIGN,
    PROJECT_STATE_REFUSALS,
    PROJECT_STORE_FOREIGN,
    PROJECT_STORE_UNATTRIBUTED,
    PROJECT_STORE_UNREADABLE,
    ProjectStateError,
    bind_project_identity,
    project_identity,
    require_project_source,
    resolve_state_directory,
)
from kriya.memory.project_store import PROJECT_OWNER_TABLE, claim_project_store, project_store_path
from kriya.workflow import workflow as workflow_module
from kriya.workflow import worktree
from kriya.workflow.graph_retrieval import open_code_intelligence

GRAPH, VECTORS = "dependency_graph.db", "vector_index.db"
A_MARKER, B_MARKER = "ALPHA_PRIVATE_MARKER_7391", "BETA_PRIVATE_MARKER_2204"
# The SAME relative path and the SAME symbol name in both projects, plus one file only each has.
A_FILES = {
    "src/shared.py": f"class SharedService:\n    def run(self):\n        return '{A_MARKER}'\n",
    "src/alpha_secret.py": "class AlphaSecretService:\n    def rotate_alpha_key(self):\n        return 1\n",
}
B_FILES = {
    "src/shared.py": f"class SharedService:\n    def run(self):\n        return '{B_MARKER}'\n",
    "src/beta.py": "class BetaThing:\n    def go(self):\n        return 2\n",
}


def _project(tmp_path, name, files):
    root = tmp_path / name
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return str(root)


def _config(monkeypatch, root):
    """The configuration a CLI or Desktop run of ``root`` loads: resolved from the project's own directory, no ``paths`` section."""
    monkeypatch.chdir(root)
    return load_config()


def _index(root, cfg):
    asyncio.run(RepositoryAnalyzer(root).index_repository(
        cfg, embedding_client=StaticEmbedder([1.0, 0.0, 0.0, 0.0]), generate_conventions_skill=False))


def _indexed_paths(path):
    graph = DependencyGraph(path)
    try:
        return set(graph.indexed_paths())
    finally:
        graph.close()


def _chunk_text(path):
    connection = sqlite3.connect(path)
    try:
        return "\n".join(str(row) for row in connection.execute("SELECT * FROM vector_chunks").fetchall())
    finally:
        connection.close()


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    a, b = _project(tmp_path, "A", A_FILES), _project(tmp_path, "B", B_FILES)
    return a, b, _config(monkeypatch, a), _config(monkeypatch, b)


# ------------------------------------------------------------------ the namespace (P1 inverted)

def test_two_unconfigured_projects_resolve_different_memory_directories_keyed_by_identity(two_projects):
    a, b, cfg_a, cfg_b = two_projects
    assert cfg_a.paths.memory != cfg_b.paths.memory
    state = resolve_state_directory(cfg_a)[0]
    assert cfg_a.paths.memory == os.path.join(state, "projects", workspace_identity(a), "memory")
    assert cfg_b.paths.memory == os.path.join(state, "projects", workspace_identity(b), "memory")
    assert project_identity(cfg_a) == workspace_identity(a) and project_identity(cfg_b) == workspace_identity(b)
    install = os.path.realpath(kriya_install_dir())
    assert not os.path.realpath(cfg_a.paths.memory).startswith(install + os.sep)  # no longer the directory every project shares


def test_cli_and_desktop_resolution_agree_and_a_subdirectory_or_a_copied_config_is_the_same_project(tmp_path, monkeypatch):
    root = _project(tmp_path, "A", A_FILES)
    os.makedirs(os.path.join(root, ".git"))  # a repository: its root is the authority root of every directory inside it
    cfg = _config(monkeypatch, root)
    # `kriya authority inspect` and the Desktop's configuration operations read resolve_config_state(), a run reads load_config().
    assert resolve_config_state().config_dict["paths"]["memory"] == cfg.paths.memory
    from_subdirectory = _config(monkeypatch, os.path.join(root, "src"))
    assert from_subdirectory.paths.memory == cfg.paths.memory and project_identity(from_subdirectory) == project_identity(cfg)
    # An enforce unit, a resume and a plan sandbox carry (a copy of) the run's configuration: the namespace travels with it.
    carried = cfg.model_copy(deep=True)
    assert project_identity(carried) == project_identity(cfg) and carried.paths.memory == cfg.paths.memory


# ------------------------------------------------------------------ P2-P4 inverted: two projects, default locations

def test_after_indexing_a_every_reader_called_for_b_answers_no_index(two_projects):
    a, b, cfg_a, cfg_b = two_projects
    _index(a, cfg_a)
    assert _indexed_paths(project_store_path(cfg_a, GRAPH)) == set(A_FILES)
    assert project_store_path(cfg_b, GRAPH) is None and project_store_path(cfg_b, VECTORS) is None
    assert open_code_intelligence(b, project_store_path(cfg_b, GRAPH)) is None
    assert not os.path.exists(cfg_b.paths.memory)  # asking never created a store for B either


def test_indexing_b_leaves_a_intact_and_reopening_a_sees_a_not_b(two_projects, monkeypatch):
    a, b, cfg_a, cfg_b = two_projects
    _index(a, cfg_a)
    before = {name: _digest(os.path.join(cfg_a.paths.memory, name)) for name in (GRAPH, VECTORS)}
    _index(b, cfg_b)
    assert {name: _digest(os.path.join(cfg_a.paths.memory, name)) for name in (GRAPH, VECTORS)} == before
    reopened = _config(monkeypatch, a)  # a later session of project A
    assert _indexed_paths(project_store_path(reopened, GRAPH)) == set(A_FILES)
    assert _indexed_paths(project_store_path(cfg_b, GRAPH)) == set(B_FILES)
    # The same relative path and the same symbol in both: each project's store holds its own bytes, never the other's.
    a_chunks, b_chunks = _chunk_text(project_store_path(reopened, VECTORS)), _chunk_text(project_store_path(cfg_b, VECTORS))
    assert A_MARKER in a_chunks and B_MARKER not in a_chunks
    assert B_MARKER in b_chunks and A_MARKER not in b_chunks
    service = open_code_intelligence(a, project_store_path(reopened, GRAPH))
    try:
        assert [s.path for s in service.current_view().find_symbol("SharedService")] == ["src/shared.py"]
        assert service.current_view().find_symbol("BetaThing") == []
    finally:
        service.close()


# ------------------------------------------------------------------ an explicit directory two projects share: only the recorded identity separates them

def _shared(two_projects, tmp_path):
    a, b, cfg_a, cfg_b = two_projects
    shared = str(tmp_path / "shared-memory")  # outside both projects
    cfg_a.paths.memory = shared
    cfg_b.paths.memory = shared
    return a, b, cfg_a, cfg_b, shared


def test_a_store_of_another_identity_is_never_read_and_never_replaced(two_projects, tmp_path, caplog):
    a, b, cfg_a, cfg_b, shared = _shared(two_projects, tmp_path)
    _index(a, cfg_a)
    before = {name: _digest(os.path.join(shared, name)) for name in (GRAPH, VECTORS)}
    with caplog.at_level("WARNING", logger="kriya.memory.project_store"):
        assert project_store_path(cfg_b, GRAPH) is None and project_store_path(cfg_b, VECTORS) is None
        assert project_store_path(cfg_b, GRAPH) is None
    reports = [r for r in caplog.records if PROJECT_STORE_FOREIGN in r.getMessage() and GRAPH in r.getMessage()]
    assert len(reports) == 1  # reported once, not on every read
    with pytest.raises(ProjectStateError) as refused:
        _index(b, cfg_b)
    assert refused.value.reason_code == PROJECT_STORE_FOREIGN
    assert refused.value.details["actual"] == workspace_identity(a) and refused.value.details["expected"] == workspace_identity(b)
    assert {name: _digest(os.path.join(shared, name)) for name in (GRAPH, VECTORS)} == before  # byte-for-byte A's
    assert _indexed_paths(project_store_path(cfg_a, GRAPH)) == set(A_FILES)


def test_altering_the_recorded_identity_yields_the_refusal(two_projects, tmp_path):
    a, _b, cfg_a, _cfg_b, shared = _shared(two_projects, tmp_path)
    _index(a, cfg_a)
    assert project_store_path(cfg_a, GRAPH) == os.path.join(shared, GRAPH)
    connection = sqlite3.connect(os.path.join(shared, GRAPH))
    with connection:
        connection.execute(f"UPDATE {PROJECT_OWNER_TABLE} SET identity = 'someone-else' WHERE id = 1")
    connection.close()
    assert project_store_path(cfg_a, GRAPH) is None
    with pytest.raises(ProjectStateError) as refused:
        claim_project_store(cfg_a, GRAPH, a)
    assert refused.value.reason_code == PROJECT_STORE_FOREIGN


def test_an_unattributed_legacy_store_is_adopted_only_where_its_location_proves_the_project(two_projects, tmp_path):
    a, _b, cfg_a, cfg_b = two_projects
    outside = tmp_path / "legacy-shared"  # what <install>/memory was: an index with no owner, outside every project
    outside.mkdir()
    DependencyGraph(str(outside / GRAPH)).close()
    cfg_b.paths.memory = str(outside)
    assert project_store_path(cfg_b, GRAPH) is None
    with pytest.raises(ProjectStateError) as refused:
        claim_project_store(cfg_b, GRAPH, _b)
    assert refused.value.reason_code == PROJECT_STORE_UNATTRIBUTED
    inside = os.path.join(a, "memory")  # a project-local ./memory written before S3: only A's configuration put it in A's tree
    os.makedirs(inside)
    DependencyGraph(os.path.join(inside, GRAPH)).close()
    cfg_a.paths.memory = inside
    # A is no repository: ./memory is an ordinary directory of the project's bound root (the greenfield rule), so it is A's.
    assert project_store_path(cfg_a, GRAPH) == os.path.join(inside, GRAPH)
    os.makedirs(os.path.join(a, ".git"))  # A is a repository: ./memory is a directory OF that repository, by the same root search
    assert workspace_identity(a) == project_identity(cfg_a)
    assert project_store_path(cfg_a, GRAPH) == os.path.join(inside, GRAPH)
    nested = os.path.join(a, "vendor", "other")  # an independent repository inside A: its ./memory is not A's by being beneath A
    os.makedirs(os.path.join(nested, ".git"))
    os.makedirs(os.path.join(nested, "memory"))
    DependencyGraph(os.path.join(nested, "memory", GRAPH)).close()
    cfg_a.paths.memory = os.path.join(nested, "memory")
    assert project_store_path(cfg_a, GRAPH) is None
    with pytest.raises(ProjectStateError) as refused:
        claim_project_store(cfg_a, GRAPH, a)
    assert refused.value.reason_code == PROJECT_STORE_UNATTRIBUTED
    cfg_a.paths.memory = inside
    assert claim_project_store(cfg_a, GRAPH, a) == os.path.join(inside, GRAPH)
    cfg_b.paths.memory = inside  # B pointed at A's tree: stamped A's now, and never B's by location
    assert project_store_path(cfg_b, GRAPH) is None


def test_an_unstamped_store_under_the_projects_own_state_root_is_that_projects_only(two_projects):
    """The second location proof: ``<state>/projects/<identity>/`` is derived from the identity itself, so an unstamped store there is that
    project's - and nobody else's, even when another project's configuration is pointed at it."""
    _a, _b, cfg_a, cfg_b = two_projects
    os.makedirs(cfg_a.paths.memory)
    DependencyGraph(os.path.join(cfg_a.paths.memory, GRAPH)).close()
    assert project_store_path(cfg_a, GRAPH) == os.path.join(cfg_a.paths.memory, GRAPH)
    cfg_b.paths.memory = cfg_a.paths.memory
    assert project_store_path(cfg_b, GRAPH) is None


def test_a_file_that_is_not_a_store_is_not_this_projects(two_projects):
    _a, _b, cfg_a, _cfg_b = two_projects
    os.makedirs(cfg_a.paths.memory)
    with open(os.path.join(cfg_a.paths.memory, GRAPH), "wb") as handle:
        handle.write(b"this is not a sqlite database, " * 40)
    assert project_store_path(cfg_a, GRAPH) is None
    with pytest.raises(ProjectStateError) as refused:
        claim_project_store(cfg_a, GRAPH, _a)
    assert refused.value.reason_code == PROJECT_STORE_UNREADABLE


# ------------------------------------------------------------------ missing identity fails closed

def test_a_configuration_with_no_project_identity_opens_no_project_store(tmp_path):
    root = _project(tmp_path, "A", A_FILES)
    unbound = AppConfig(paths={"memory": str(tmp_path / "anywhere"), "skills": str(tmp_path / "skills")})
    for attempt in (lambda: project_identity(unbound), lambda: project_store_path(unbound, GRAPH),
                    lambda: claim_project_store(unbound, GRAPH, root), lambda: _index(root, unbound)):
        with pytest.raises(ProjectStateError) as refused:
            attempt()
        assert refused.value.reason_code == PROJECT_IDENTITY_MISSING
    assert not os.path.exists(tmp_path / "anywhere")  # nothing was created under a default or guessed owner
    bind_project_identity(unbound, root)
    assert project_identity(unbound) == workspace_identity(root)
    assert project_store_path(unbound, GRAPH) is None


def test_the_refusal_table_is_closed():
    assert set(PROJECT_STATE_REFUSALS) == {
        PROJECT_IDENTITY_MISSING, PROJECT_IDENTITY_MISMATCH, PROJECT_STORE_FOREIGN, PROJECT_STORE_UNATTRIBUTED, PROJECT_STORE_UNREADABLE, PROJECT_SOURCE_FOREIGN}
    codes = {value for name, value in vars(resolution).items() if name.startswith("PROJECT_") and name.isupper()
             and isinstance(value, str) and value == name}
    assert codes == set(PROJECT_STATE_REFUSALS)


# ------------------------------------------------------------------ the writer: bytes written into project P's namespace belong to P

def _memory_snapshot(cfg):
    """Every file under the project's memory directory with its digest ({} when the directory does not exist)."""
    found = {}
    for directory, _dirs, files in os.walk(cfg.paths.memory):
        for name in files:
            found[os.path.relpath(os.path.join(directory, name), cfg.paths.memory)] = _digest(os.path.join(directory, name))
    return found


def _refused_source(root, cfg):
    with pytest.raises(ProjectStateError) as refused:
        _index(root, cfg)
    assert refused.value.reason_code == PROJECT_SOURCE_FOREIGN
    return refused.value


def test_indexing_another_projects_root_with_this_projects_configuration_is_refused_and_mutates_nothing(two_projects):
    a, b, cfg_a, _cfg_b = two_projects
    # A has no store yet: the refusal creates none (not even the directory).
    refusal = _refused_source(b, cfg_a)
    assert os.path.realpath(refusal.details["source"]) == os.path.realpath(b)
    assert os.path.realpath(refusal.details["root"]) == os.path.realpath(a)
    assert not os.path.exists(cfg_a.paths.memory)
    # A has an index: the refusal leaves every byte of it, and none of B's content is in it.
    _index(a, cfg_a)
    before = _memory_snapshot(cfg_a)
    assert set(before) >= {GRAPH, VECTORS}
    _refused_source(b, cfg_a)
    assert _memory_snapshot(cfg_a) == before
    assert _indexed_paths(project_store_path(cfg_a, GRAPH)) == set(A_FILES)
    assert B_MARKER not in _chunk_text(project_store_path(cfg_a, VECTORS))


def test_the_claim_itself_refuses_a_foreign_source_for_every_writer(two_projects):
    a, b, cfg_a, _cfg_b = two_projects
    for name in (GRAPH, VECTORS):
        with pytest.raises(ProjectStateError) as refused:
            claim_project_store(cfg_a, name, b)
        assert refused.value.reason_code == PROJECT_SOURCE_FOREIGN
    assert not os.path.exists(cfg_a.paths.memory)
    assert claim_project_store(cfg_a, GRAPH, a) == os.path.join(cfg_a.paths.memory, GRAPH)


def test_a_directory_of_the_projects_repository_is_this_projects_source(tmp_path, monkeypatch):
    root = _project(tmp_path, "A", A_FILES)
    os.makedirs(os.path.join(root, ".git"))
    cfg = _config(monkeypatch, root)
    _index(os.path.join(root, "src"), cfg)  # the canonical root authority says src/ belongs to A
    assert _indexed_paths(project_store_path(cfg, GRAPH)) == {"shared.py", "alpha_secret.py"}


def _nested_repository(root):
    """An INDEPENDENT repository physically inside the project at ``root`` (its own ``.git``, its own files)."""
    nested = os.path.join(root, "vendor", "beta")
    for relative, content in B_FILES.items():
        target = os.path.join(nested, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)
    os.makedirs(os.path.join(nested, ".git"))
    return nested


def test_an_independent_repository_nested_inside_the_project_is_refused_and_mutates_nothing(tmp_path, monkeypatch):
    """Owner ruling 2026-11-16: filesystem containment alone does not establish project ownership."""
    from click.testing import CliRunner

    from kriya.cli import main

    root = _project(tmp_path, "A", A_FILES)
    os.makedirs(os.path.join(root, ".git"))
    nested = _nested_repository(root)
    cfg = _config(monkeypatch, root)
    assert workspace_identity(nested) != project_identity(cfg)
    # No store yet: the refusal creates none, through the analyzer, the claim and the command alike.
    refusal = _refused_source(nested, cfg)
    assert os.path.realpath(refusal.details["source"]) == os.path.realpath(nested)
    for name in (GRAPH, VECTORS):
        with pytest.raises(ProjectStateError) as refused:
            claim_project_store(cfg, name, nested)
        assert refused.value.reason_code == PROJECT_SOURCE_FOREIGN
    with pytest.raises(ProjectStateError):
        require_project_source(cfg, os.path.join(nested, "src"))  # a directory of the nested repository is the nested repository's
    monkeypatch.chdir(root)
    result = CliRunner().invoke(main, ["analyze", nested])
    assert result.exit_code == 1 and "is not part of this project" in result.output
    assert not os.path.exists(cfg.paths.memory)
    # A has an index: the refusal leaves every byte of it, and none of the nested repository's content is in it.
    _index(root, cfg)
    before = _memory_snapshot(cfg)
    assert set(before) >= {GRAPH, VECTORS}
    _refused_source(nested, cfg)
    assert _memory_snapshot(cfg) == before
    # Nor does indexing A itself absorb it: the walk of A stops at the nested repository's root.
    assert _indexed_paths(project_store_path(cfg, GRAPH)) == set(A_FILES)
    assert B_MARKER not in _chunk_text(project_store_path(cfg, VECTORS))
    service = open_code_intelligence(root, project_store_path(cfg, GRAPH))
    try:
        assert service.current_view().find_symbol("BetaThing") == []
    finally:
        service.close()


def test_an_ordinary_subdirectory_of_a_project_that_is_no_repository_is_that_projects_source(two_projects):
    """Owner decision 2026-11-16 (greenfield): a project that is no repository owns its ordinary subdirectories exactly as a repository does.
    The verdict is the D-001 root search from the directory up to the project's bound root, the same one the indexing walk prunes with."""
    a, _b, cfg_a, _cfg_b = two_projects
    require_project_source(cfg_a, os.path.join(a, "src"))
    _index(os.path.join(a, "src"), cfg_a)
    assert _indexed_paths(project_store_path(cfg_a, GRAPH)) == {"shared.py", "alpha_secret.py"}
    assert open(project_store_path(cfg_a, GRAPH), "rb").read(16) == b"SQLite format 3\x00"


def test_an_independently_rooted_directory_inside_a_project_that_is_no_repository_stays_foreign(two_projects):
    """The other half of the greenfield rule: inside a project that is no repository, an independent repository and a Kriya-owned directory are
    their own roots. Neither is a source of the project, and indexing the project does not walk into either."""
    a, _b, cfg_a, _cfg_b = two_projects
    nested = _nested_repository(a)
    owned = os.path.join(a, ".kriya", "sandbox", "candidate")
    os.makedirs(owned)
    with open(os.path.join(owned, "owned_secret.py"), "w", encoding="utf-8") as handle:
        handle.write("class OwnedSandboxThing:\n    pass\n")
    for foreign in (nested, os.path.join(nested, "src"), owned, os.path.join(a, ".kriya")):
        with pytest.raises(ProjectStateError) as refused:
            claim_project_store(cfg_a, GRAPH, foreign)
        assert refused.value.reason_code == PROJECT_SOURCE_FOREIGN
    assert not os.path.exists(cfg_a.paths.memory)
    prune = resolution.foreign_root_within(a)
    assert [prune(relative) for relative in ("src", os.path.join("vendor", "beta"), ".kriya", os.path.join(".kriya", "sandbox", "candidate"))] == [
        False, True, True, True]
    _index(a, cfg_a)
    assert _indexed_paths(project_store_path(cfg_a, GRAPH)) == set(A_FILES)
    assert B_MARKER not in _chunk_text(project_store_path(cfg_a, VECTORS))


def test_a_subdirectory_of_a_project_that_is_no_repository_invoked_on_its_own_is_its_own_project(two_projects, monkeypatch):
    """D-001 is unchanged: Kriya run FROM ``A/src`` outside a repository is the project ``A/src`` (its own lock, records and namespace); its
    index is never A's, and A's is never its."""
    a, _b, cfg_a, _cfg_b = two_projects
    sub = os.path.join(a, "src")
    cfg_sub = _config(monkeypatch, sub)
    assert project_identity(cfg_sub) == workspace_identity(sub) != project_identity(cfg_a)
    assert cfg_sub.paths.memory != cfg_a.paths.memory
    _index(a, cfg_a)
    assert project_store_path(cfg_sub, GRAPH) is None
    with pytest.raises(ProjectStateError) as refused:
        claim_project_store(cfg_sub, GRAPH, a)  # the parent directory is not part of the project rooted beneath it
    assert refused.value.reason_code == PROJECT_SOURCE_FOREIGN


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _git_project(tmp_path, monkeypatch):
    root = _project(tmp_path, "A", A_FILES)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root, _config(monkeypatch, root)


class _IndexedRoots:
    roots = []


@pytest.fixture
def model_free_analyzer(monkeypatch):
    """The real analyzer and the real store claim; only the embedding endpoint and the conventions-skill model call are replaced, and the
    root each pass was asked to index is recorded."""
    real = RepositoryAnalyzer.index_repository
    _IndexedRoots.roots = []

    async def index_repository(self, cfg, **options):
        _IndexedRoots.roots.append(os.path.realpath(self.root_path))
        return await real(self, cfg, embedding_client=StaticEmbedder([1.0, 0.0, 0.0, 0.0]), generate_conventions_skill=False, **options)

    monkeypatch.setattr(RepositoryAnalyzer, "index_repository", index_repository)
    return _IndexedRoots


def test_the_runs_plan_worktree_is_admitted_by_the_runs_lease_never_by_where_it_lies(tmp_path, monkeypatch, model_free_analyzer):
    """Case E of the owner ruling: the plan worktree is its own authority root; the active run's explicit authorization (STATE) is what says it
    is this project's run, and the index is then built from the project's own workspace."""
    root, cfg = _git_project(tmp_path, monkeypatch)
    with begin_mutating_run(root):
        plan = worktree.create_git_worktree(root)
        assert workspace_identity(plan) != project_identity(cfg)
        with pytest.raises(ProjectStateError) as refused:
            claim_project_store(cfg, GRAPH, plan)  # lying inside the project's .kriya establishes nothing
        assert refused.value.reason_code == PROJECT_SOURCE_FOREIGN
        # Not authorized by the run: the auto-index of that directory is refused and nothing is created.
        assert asyncio.run(workflow_module._ensure_repository_indexed(cfg, plan)) is False
        assert not os.path.exists(cfg.paths.memory)
        authorize_candidate_workspace(plan)
        assert asyncio.run(workflow_module._ensure_repository_indexed(cfg, plan)) is True
    assert model_free_analyzer.roots == [os.path.realpath(plan), os.path.realpath(root)]
    assert _indexed_paths(project_store_path(cfg, GRAPH)) == set(A_FILES)
    # The authorization was the run's: after it, the same directory is again not this project's source.
    assert asyncio.run(workflow_module._ensure_repository_indexed(cfg, plan)) is True  # already indexed: asks this project's store only
    with pytest.raises(ProjectStateError):
        claim_project_store(cfg, GRAPH, plan)


def test_a_candidate_authorized_by_another_projects_run_is_not_this_projects(tmp_path, monkeypatch, model_free_analyzer):
    """Identity A with a run of project B: B's lease resolves B's candidate to B's workspace, which A's store refuses."""
    a = _project(tmp_path, "A", A_FILES)
    cfg_a = _config(monkeypatch, a)
    b = tmp_path / "B"
    b.mkdir()
    (b / "beta.py").write_text("class BetaThing:\n    pass\n", encoding="utf-8")
    _git(b, "init", "-q")
    _git(b, "config", "user.email", "t@example.com")
    _git(b, "config", "user.name", "T")
    _git(b, "add", "-A")
    _git(b, "commit", "-q", "-m", "base")
    with begin_mutating_run(str(b)):
        plan = worktree.create_git_worktree(str(b))
        authorize_candidate_workspace(plan)
        assert asyncio.run(workflow_module._ensure_repository_indexed(cfg_a, plan)) is False
    assert model_free_analyzer.roots == [os.path.realpath(str(b))]
    assert not os.path.exists(cfg_a.paths.memory)


def test_an_alias_does_not_change_whose_source_a_directory_is(two_projects, tmp_path):
    a, b, cfg_a, _cfg_b = two_projects
    os.symlink(b, os.path.join(a, "vendored"))  # a name inside A that IS project B
    _refused_source(os.path.join(a, "vendored"), cfg_a)
    assert not os.path.exists(cfg_a.paths.memory)
    alias = str(tmp_path / "alias-of-a")  # another spelling of A itself
    os.symlink(a, alias)
    _index(alias, cfg_a)
    assert _indexed_paths(project_store_path(cfg_a, GRAPH)) == set(A_FILES)


def test_a_configuration_with_an_identity_but_no_root_cannot_prove_a_source(two_projects):
    a, _b, cfg_a, _cfg_b = two_projects
    cfg_a._project_root = None
    with pytest.raises(ProjectStateError) as refused:
        claim_project_store(cfg_a, GRAPH, a)
    assert refused.value.reason_code == PROJECT_IDENTITY_MISSING
    assert not os.path.exists(cfg_a.paths.memory)


def test_kriya_analyze_of_another_project_from_this_projects_directory_is_refused(two_projects, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main

    a, b, cfg_a, _cfg_b = two_projects
    monkeypatch.chdir(a)
    result = CliRunner().invoke(main, ["analyze", b])
    assert result.exit_code == 1
    assert "is not part of this project" in result.output
    assert not os.path.exists(cfg_a.paths.memory)


# ------------------------------------------------------------------ the run-entry rule (owner decision 2026-11-16, option 1)

class _Stop(Exception):
    """Raised by a stub to end a run at the first thing it would do after its entry."""


def _real_engine(cfg):
    from unittest.mock import AsyncMock

    from kriya.core import LLMClient
    from kriya.core.kernel import Kernel
    from kriya.workflow.workflow import WorkflowEngine

    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=AssertionError("a refused run must not reach a model"))
    return WorkflowEngine(Kernel(config=cfg), llm), llm


def _memory_free_config(tmp_path):
    return AppConfig(paths={"memory": str(tmp_path / "unbound-memory"), "skills": str(tmp_path / "unbound-skills")})


def _run_states(root):
    from kriya.control.persistence import load_run_record

    directory = os.path.join(root, ".kriya", "control", "runs")
    return [load_run_record(root, name[:-len(".json")]).lifecycle_state.value for name in sorted(os.listdir(directory)) if name.endswith(".json")]


def test_an_unbound_configuration_is_bound_to_the_runs_project_at_direct_run_entry(tmp_path, monkeypatch):
    root = _project(tmp_path, "A", A_FILES)
    cfg = _memory_free_config(tmp_path)
    engine, _llm = _real_engine(cfg)
    seen = []

    async def first_step(_run, _arguments):
        seen.append(project_identity(cfg))
        raise _Stop()

    monkeypatch.setattr("kriya.workflow.plan_executor.execute_direct_goal", first_step)
    with pytest.raises(ProjectStateError):
        project_identity(cfg)
    with pytest.raises(_Stop):
        asyncio.run(engine.run_generation_workflow("goal", root))
    assert seen == [workspace_identity(root)]
    assert os.path.realpath(cfg._project_root) == os.path.realpath(root)
    # Bound once: the same configuration is the same project's for the next run of that project.
    with pytest.raises(_Stop):
        asyncio.run(engine.run_generation_workflow("goal", root))
    assert seen == [workspace_identity(root)] * 2


def test_a_configuration_bound_to_this_project_is_accepted_unchanged_at_run_entry(two_projects, monkeypatch):
    a, _b, cfg_a, _cfg_b = two_projects
    engine, _llm = _real_engine(cfg_a)
    before = (cfg_a._project_identity, cfg_a._project_root, cfg_a.paths.memory)

    async def first_step(_run, _arguments):
        raise _Stop()

    monkeypatch.setattr("kriya.workflow.plan_executor.execute_direct_goal", first_step)
    with pytest.raises(_Stop):
        asyncio.run(engine.run_generation_workflow("goal", a))
    assert (cfg_a._project_identity, cfg_a._project_root, cfg_a.paths.memory) == before


def test_a_configuration_bound_to_another_project_is_refused_at_direct_run_entry_before_anything_runs(two_projects, monkeypatch):
    a, b, cfg_a, _cfg_b = two_projects
    _index(a, cfg_a)
    before = _memory_snapshot(cfg_a)
    engine, llm = _real_engine(cfg_a)

    async def never(_run, _arguments):
        raise AssertionError("a refused run must not start")

    monkeypatch.setattr("kriya.workflow.plan_executor.execute_direct_goal", never)
    with open(os.path.join(b, "src", "beta.py"), "rb") as handle:
        candidate_before = handle.read()
    result = asyncio.run(engine.run_generation_workflow("goal", b))
    assert result["status"] == "failed" and result["quality_gates_passed"] is False and result["files"] == []
    assert result["reason_codes"] == [PROJECT_IDENTITY_MISMATCH]
    assert repr(cfg_a._project_root) in result["error"] and "Nothing was read" in result["error"]
    assert result["workspace_path"] == b and result["goal"] == "goal"
    assert llm.complete.call_count == 0
    assert _memory_snapshot(cfg_a) == before  # A's stores: not a byte read into B's run, not a byte changed
    assert project_identity(cfg_a) == workspace_identity(a)  # never rebound
    with open(os.path.join(b, "src", "beta.py"), "rb") as handle:
        assert handle.read() == candidate_before
    assert _run_states(b) == ["FAILURE"]  # the refused run is recorded as failed, never as success


def test_the_enforce_entry_binds_and_refuses_before_triage(two_projects):
    from unittest.mock import AsyncMock

    from kriya.workflow.workflow_controller import WorkflowController

    a, b, cfg_a, _cfg_b = two_projects
    engine, llm = _real_engine(cfg_a)
    engine.engineering_triage.classify = AsyncMock(side_effect=AssertionError("a refused run must not be triaged"))
    refused = asyncio.run(WorkflowController(engine).execute("goal", b))
    assert refused.legacy_result["reason_codes"] == [PROJECT_IDENTITY_MISMATCH] and refused.legacy_result["status"] == "failed"
    assert refused.route is None and refused.control_state is None
    assert llm.complete.call_count == 0 and engine.engineering_triage.classify.await_count == 0

    unbound = AppConfig(paths={"memory": os.path.join(os.path.dirname(a), "m"), "skills": os.path.join(os.path.dirname(a), "s")})
    engine_b, _llm_b = _real_engine(unbound)
    seen = []

    async def classify(*_args, **_kwargs):
        seen.append(project_identity(unbound))
        raise _Stop()

    engine_b.engineering_triage.classify = classify
    with pytest.raises(_Stop):
        asyncio.run(WorkflowController(engine_b).execute("goal", b))
    assert seen == [workspace_identity(b)]


def test_a_unit_in_the_plan_worktree_is_bound_through_the_runs_lease_never_to_the_worktree(tmp_path, monkeypatch):
    """The enforce shape: the unit's ``workspace_path`` is the authorized plan worktree (its own authority root). The project is the workspace
    of the run that holds the lease."""
    root, _cfg = _git_project(tmp_path, monkeypatch)
    unbound = _memory_free_config(tmp_path)
    engine, _llm = _real_engine(unbound)
    seen = []

    async def first_step(_run, arguments):
        seen.append((project_identity(unbound), arguments["workspace_path"]))
        raise _Stop()

    monkeypatch.setattr("kriya.workflow.plan_executor.execute_direct_goal", first_step)
    with begin_mutating_run(root):
        plan = worktree.create_git_worktree(root)
        authorize_candidate_workspace(plan)
        assert workspace_identity(plan) != workspace_identity(root)
        with pytest.raises(_Stop):
            asyncio.run(engine.run_generation_workflow("goal", plan))
    assert seen == [(workspace_identity(root), plan)]
    assert os.path.realpath(unbound._project_root) == os.path.realpath(root)


def test_a_unit_in_a_directory_the_run_did_not_authorize_never_reaches_the_binding(tmp_path, monkeypatch):
    from kriya.control.run_coordinator import InvalidRunContextError

    root, _cfg = _git_project(tmp_path, monkeypatch)
    unbound = _memory_free_config(tmp_path)
    engine, _llm = _real_engine(unbound)
    with begin_mutating_run(root):
        plan = worktree.create_git_worktree(root)  # created, NOT authorized
        with pytest.raises(InvalidRunContextError):
            asyncio.run(engine.run_generation_workflow("goal", plan))
    with pytest.raises(ProjectStateError):
        project_identity(unbound)


def test_the_run_entry_rule_is_the_boundary_only_the_store_api_below_it_still_refuses_an_unbound_configuration(tmp_path):
    root = _project(tmp_path, "A", A_FILES)
    unbound = _memory_free_config(tmp_path)
    with begin_mutating_run(root):  # a lease alone binds nothing: only an entry point's configuration is bound, at the entry
        for attempt in (lambda: project_store_path(unbound, GRAPH), lambda: claim_project_store(unbound, GRAPH, root)):
            with pytest.raises(ProjectStateError) as refused:
                attempt()
            assert refused.value.reason_code == PROJECT_IDENTITY_MISSING
    assert not os.path.exists(tmp_path / "unbound-memory")


def test_an_entry_point_without_a_refusal_renderer_raises_the_typed_refusal(two_projects):
    from types import SimpleNamespace

    from kriya.control.run_coordinator import coordinated_mutation

    a, b, cfg_a, _cfg_b = two_projects
    ran = []

    @coordinated_mutation
    async def entry(we, workspace_path):
        ran.append(workspace_path)
        return {"status": "success", "quality_gates_passed": True}

    with pytest.raises(ProjectStateError) as refused:
        asyncio.run(entry(SimpleNamespace(kernel=SimpleNamespace(config=cfg_a)), b))
    assert refused.value.reason_code == PROJECT_IDENTITY_MISMATCH and ran == []
    assert _run_states(b) == ["FAILURE"]


# ------------------------------------------------------------------ the paths.memory default relocation (the pre-D3 golden delta)

def test_an_explicit_paths_memory_is_exactly_what_the_user_wrote_and_the_default_is_deterministic(tmp_path, monkeypatch):
    a, b = _project(tmp_path, "A", A_FILES), _project(tmp_path, "B", B_FILES)
    explicit = str(tmp_path / "chosen" / "memory")
    with open(os.path.join(b, "kriya.yaml"), "w", encoding="utf-8") as handle:
        handle.write(f"paths:\n  memory: {explicit}\n")
    monkeypatch.chdir(b)
    assert resolve_config_state().config_dict["paths"]["memory"] == explicit  # never relocated, never keyed
    monkeypatch.chdir(a)
    first = resolve_config_state().config_dict["paths"]
    second = resolve_config_state().config_dict["paths"]
    assert first["memory"] == second["memory"]  # the same project identity resolves the same directory, every time
    assert first["memory"].endswith(os.path.join("projects", workspace_identity(a), "memory"))
    # The unconfigured skills root is the project's overlay beside its memory, never the shipped library every project reads.
    assert first["skills"] == os.path.join(os.path.dirname(first["memory"]), "skills")
    assert first["skills"] != os.path.realpath(os.path.join(kriya_install_dir(), "skills"))


def test_the_configuration_identity_hashes_neither_where_the_project_is_nor_where_the_state_directory_is(two_projects, tmp_path, monkeypatch):
    """The default paths.memory names the state directory and the project's identity (a hash of the workspace's location). The configuration
    fingerprint (resume, preflight, proposals) is the SEMANTIC configuration: it takes the per-project default in its identity form, exactly
    as it takes a path inside the checkout (Q0). MEASURED before the fix: the fingerprint differed per project and per state directory."""
    from kriya.control.checkpoint import canonical_identity_config, compute_config_fingerprint

    a, _b, cfg_a, cfg_b = two_projects
    assert cfg_a.paths.memory != cfg_b.paths.memory
    fingerprint = compute_config_fingerprint(cfg_a.model_dump())
    assert compute_config_fingerprint(cfg_b.model_dump()) == fingerprint  # another project, the same configuration
    assert canonical_identity_config(cfg_a.model_dump())["paths"]["memory"] == "<kriya-project-state>/memory"
    state = resolve_state_directory(cfg_a)[0]
    # An explicit directory is real identity, and so is anything that merely looks like the default.
    for elsewhere in (str(tmp_path / "chosen" / "memory"),
                      str(tmp_path / "projects" / ("0" * 64) / "memory"),          # not under the state directory
                      os.path.join(state, "projects", "not-an-identity", "memory"),  # under it, but no project identity
                      os.path.join(state, "other", workspace_identity(a), "memory")):
        explicit = cfg_a.model_copy(deep=True)
        explicit.paths.memory = elsewhere
        assert canonical_identity_config(explicit.model_dump())["paths"]["memory"] == elsewhere
        assert compute_config_fingerprint(explicit.model_dump()) != fingerprint
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state-elsewhere"))
    moved = _config(monkeypatch, a)
    assert moved.paths.memory != cfg_a.paths.memory and moved.paths.memory.startswith(str(tmp_path / "state-elsewhere"))
    assert compute_config_fingerprint(moved.model_dump()) == fingerprint  # another state location, the same configuration
