"""C-05 / R-05 (slice S3), the stores beside the code index: learned knowledge (a PROJECT store), the release cache (a declared GLOBAL
store), the skills overlay and the knowledge gate's roots (A5 P8 inverted), the run-history identity column, the legacy shared-memory report,
and the identity equivalence of every way a run of one authoritative project root is started.

The namespace itself, the code index and the run-entry rule are tests/test_s3_project_state_namespace.py.
"""
import asyncio
import hashlib
import os
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from test_s3_project_state_namespace import A_FILES, B_FILES, _config, _git_project, _project, _real_engine, _Stop

from kriya.cli import main
from kriya.config.config import AppConfig
from kriya.config.environment import kriya_install_dir
from kriya.config.resolution import (
    PROJECT_IDENTITY_MISMATCH,
    PROJECT_STORE_FOREIGN,
    PROJECT_STORE_UNATTRIBUTED,
    ProjectStateError,
    global_store_directory,
    is_project_skill_overlay,
    project_identity,
    resolve_config_state,
)
from kriya.context.workspace_identity import workspace_identity
from kriya.control.trace import TraceLogger
from kriya.core.state_paths import legacy_shared_memory_stores, resolve_state_directory, trace_db_path
from kriya.memory import learned_knowledge
from kriya.memory.learned_knowledge import (
    claim_learned_knowledge_store,
    learned_knowledge_db_path,
    retrieve_learned_references,
)
from kriya.memory.project_store import PROJECT_OWNER_TABLE
from kriya.memory.vector import OllamaEmbeddingClient
from kriya.skills.skill import SkillEngine, get_global_skills_dir
from kriya.tools.knowledge import KnowledgeCache, KnowledgeGuard
from kriya.workflow.context_certification import DeterministicHashingEmbedder

EMBEDDER = DeterministicHashingEmbedder()
FACT = "Project alpha rotates its signing key with the rotateAlphaKey operation every thirty days."
QUESTION = "How does project alpha rotate its signing key with rotateAlphaKey?"
# A library the shipped skill library does not cover (the shipped library is a read-only root of every default project's gate).
IGNITE_GOAL = "Upgrade the cache to use com.kriyaprobe.widgets:widget-core:2.16.0 everywhere."
IGNITE = "com.kriyaprobe.widgets:widget-core"


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    a, b = _project(tmp_path, "A", A_FILES), _project(tmp_path, "B", B_FILES)
    return a, b, _config(monkeypatch, a), _config(monkeypatch, b)


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _embedding_patches():
    async def one(_self, text, **_kwargs):
        return EMBEDDER._vector(text)

    async def many(_self, texts, **_kwargs):
        return [EMBEDDER._vector(text) for text in texts]

    return patch.object(OllamaEmbeddingClient, "get_embedding", new=one), patch.object(OllamaEmbeddingClient, "get_embeddings", new=many)


def _learn(cfg, *texts):
    """`kriya learn -t ...` as the CLI runs it for the project ``cfg`` was loaded in."""
    one, many = _embedding_patches()
    with one, many, patch("kriya.cli.load_config", return_value=cfg):
        return CliRunner().invoke(main, ["learn"] + [argument for text in texts for argument in ("-t", text)])


def _retrieve(cfg, query=QUESTION):
    one, many = _embedding_patches()
    with one, many:
        return asyncio.run(retrieve_learned_references(cfg, query))


def _shared_memory(tmp_path, *configs):
    shared = str(tmp_path / "shared-memory")
    for cfg in configs:
        cfg.paths.memory = shared
    return shared


# ------------------------------------------------------------------ learned knowledge: a project store

def test_what_is_taught_in_one_project_is_read_in_that_project_and_never_in_another(two_projects):
    a, _b, cfg_a, cfg_b = two_projects
    result = _learn(cfg_a, FACT)
    assert result.exit_code == 0, result.output
    path = learned_knowledge_db_path(cfg_a)
    assert path == os.path.join(cfg_a.paths.memory, learned_knowledge.LEARNED_KNOWLEDGE_DB)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(f"SELECT identity FROM {PROJECT_OWNER_TABLE}").fetchall() == [(workspace_identity(a),)]
    finally:
        connection.close()
    assert [reference.text for reference in _retrieve(cfg_a).references] == [FACT]
    in_b = _retrieve(cfg_b)
    assert in_b.references == () and in_b.unavailable_reason == ""  # B simply has none: nothing of A's, and nothing to warn about
    assert learned_knowledge_db_path(cfg_b) is None
    assert not os.path.exists(os.path.join(cfg_b.paths.memory, learned_knowledge.LEARNED_KNOWLEDGE_DB))  # reading never creates one


def test_one_explicit_memory_directory_for_two_projects_does_not_share_what_was_taught(two_projects, tmp_path):
    _a, _b, cfg_a, cfg_b = two_projects
    shared = _shared_memory(tmp_path, cfg_a, cfg_b)
    assert _learn(cfg_a, FACT).exit_code == 0
    store = os.path.join(shared, learned_knowledge.LEARNED_KNOWLEDGE_DB)
    before = _digest(store)
    assert [reference.text for reference in _retrieve(cfg_a).references] == [FACT]
    in_b = _retrieve(cfg_b)
    assert in_b.references == ()
    assert PROJECT_STORE_FOREIGN in in_b.unavailable_reason  # the operator is told why nothing was used
    assert any("Learned knowledge not used" in note for note in in_b.warnings())
    refused = _learn(cfg_b, "Project beta has a different rule entirely.")
    assert refused.exit_code == 1 and PROJECT_STORE_FOREIGN in refused.output
    assert _digest(store) == before  # B's `learn` wrote nothing into A's store
    assert [reference.text for reference in _retrieve(cfg_a).references] == [FACT]


def test_a_refused_learn_fetches_and_embeds_nothing(two_projects, tmp_path):
    _a, _b, cfg_a, cfg_b = two_projects
    _shared_memory(tmp_path, cfg_a, cfg_b)
    assert _learn(cfg_a, FACT).exit_code == 0
    embed = AsyncMock(side_effect=AssertionError("a refused learn must not embed"))
    fetch = AsyncMock(side_effect=AssertionError("a refused learn must not fetch"))
    with patch.object(OllamaEmbeddingClient, "get_embeddings", new=embed), patch("kriya.tools.web.fetch_url_text", new=fetch), \
            patch("kriya.cli.load_config", return_value=cfg_b):
        refused = CliRunner().invoke(main, ["learn", "-u", "http://example.invalid/doc", "-t", "text"])
    assert refused.exit_code == 1 and embed.await_count == 0 and fetch.await_count == 0


def test_an_unattributed_legacy_learned_store_outside_the_project_is_neither_read_nor_adopted(two_projects, tmp_path):
    """The pre-S3 shape: a web_knowledge.db with rows and no owner stamp, in a directory that proves nothing about whose it is."""
    _a, _b, cfg_a, _cfg_b = two_projects
    shared = _shared_memory(tmp_path, cfg_a)
    os.makedirs(shared)
    from kriya.memory.vector import LocalVectorStore

    legacy = os.path.join(shared, learned_knowledge.LEARNED_KNOWLEDGE_DB)
    store = LocalVectorStore(legacy)
    store.add_learned_knowledge(FACT, EMBEDDER._vector(FACT), model_name=cfg_a.embedding.model, dimensions=len(EMBEDDER._vector(FACT)),
                                provenance_url="legacy", fetch_date="2026-01-01 00:00:00")
    store.close()
    before = _digest(legacy)
    retrieval = _retrieve(cfg_a)
    assert retrieval.references == () and PROJECT_STORE_UNATTRIBUTED in retrieval.unavailable_reason
    refused = _learn(cfg_a, "new fact")
    assert refused.exit_code == 1 and PROJECT_STORE_UNATTRIBUTED in refused.output
    assert _digest(legacy) == before


def test_the_learned_store_api_refuses_a_configuration_with_no_project_identity(tmp_path):
    unbound = AppConfig(paths={"memory": str(tmp_path / "memory"), "skills": str(tmp_path / "skills")})
    with pytest.raises(ProjectStateError):
        learned_knowledge_db_path(unbound)
    with pytest.raises(ProjectStateError):
        claim_learned_knowledge_store(unbound)
    with pytest.raises(ProjectStateError):
        _retrieve(unbound)
    assert not os.path.exists(str(tmp_path / "memory"))
    assert _retrieve(unbound, "   ") == learned_knowledge.LearnedRetrieval()  # no query, nothing asked of any store


# ------------------------------------------------------------------ the release cache: a declared global store

def test_the_release_cache_location_is_one_named_global_directory_never_a_projects_memory(two_projects):
    _a, _b, cfg_a, cfg_b = two_projects
    state = resolve_state_directory(cfg_a)[0]
    assert global_store_directory(cfg_a.paths.state) == global_store_directory(cfg_b.paths.state) == os.path.join(state, "global")
    for cfg in (cfg_a, cfg_b):
        assert not global_store_directory(cfg.paths.state).startswith(os.path.join(state, "projects") + os.sep)
        assert global_store_directory(cfg.paths.state) != cfg.paths.memory


class _GuardSpy:
    """Records how the knowledge guard is constructed; the real guard still runs."""

    calls = []

    def __new__(cls, **kwargs):
        cls.calls.append(kwargs)
        return KnowledgeGuard(**kwargs)


def test_the_enforce_entry_gives_the_guard_the_global_cache_and_the_projects_roots(tmp_path, monkeypatch):
    from kriya.workflow.workflow_controller import WorkflowController

    a, cfg_a = _git_project(tmp_path, monkeypatch)
    cfg_a.knowledge.offline_mode = True
    _GuardSpy.calls = []
    monkeypatch.setattr("kriya.tools.knowledge.KnowledgeGuard", _GuardSpy)
    engine, _llm = _real_engine(cfg_a)
    # migration_mode="enforce": the controller's OWN guard construction (before structured planning), not the unit stage's.
    result = asyncio.run(WorkflowController(engine).execute(IGNITE_GOAL, a, migration_mode="enforce"))
    assert result.legacy_result["status"] == "knowledge_gap"  # the guard ran and stopped the run before any model call
    assert result.route is not None  # stopped by the controller after triage, i.e. at its own guard
    (call,) = _GuardSpy.calls
    assert call["memory_dir"] == global_store_directory(cfg_a.paths.state)
    assert call["skills_dir"] == cfg_a.paths.skills and call["library_dirs"] == [get_global_skills_dir()]
    assert os.path.isfile(os.path.join(global_store_directory(cfg_a.paths.state), "knowledge_cache.db"))
    assert not os.path.exists(os.path.join(cfg_a.paths.memory, "knowledge_cache.db"))


def test_the_direct_stage_gives_the_guard_the_global_cache_and_the_projects_roots(two_projects):
    from kriya.workflow.stage_context import StageContext
    from kriya.workflow.stage_knowledge_guard import check_knowledge_guard
    from kriya.workflow.state import GenerationState

    a, _b, cfg_a, _cfg_b = two_projects
    cfg_a.knowledge.offline_mode = True
    _GuardSpy.calls = []
    engine, _llm = _real_engine(cfg_a)
    import dataclasses

    deps = dataclasses.replace(engine._work_unit_deps(), KnowledgeGuard=_GuardSpy, trace_events=lambda state: [])
    ctx = StageContext(goal=IGNITE_GOAL, workspace_path=a, state=GenerationState(), trace_id="t-1", start_time=0.0, run_id="run-1")
    result = asyncio.run(check_knowledge_guard(ctx, deps))
    assert result["status"] == "knowledge_gap"
    (call,) = _GuardSpy.calls
    assert call["memory_dir"] == global_store_directory(cfg_a.paths.state)
    assert call["skills_dir"] == cfg_a.paths.skills and call["library_dirs"] == [get_global_skills_dir()]


def test_the_release_cache_holds_only_public_registry_facts_keyed_by_package(tmp_path):
    """Why it may be global (TRACED, pinned here): a row is (ecosystem, package, version) -> release date; nothing of a project is in it."""
    cache = KnowledgeCache(str(tmp_path / "global"))
    connection = sqlite3.connect(cache.db_path)
    try:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(release_cache)")]
    finally:
        connection.close()
    assert columns == ["ecosystem", "package", "version", "release_date", "retrieved_at", "source"]


# ------------------------------------------------------------------ skills: the project overlay (P1 for paths.skills, P8 inverted)

def test_two_unconfigured_projects_resolve_their_own_skill_overlay_and_read_the_shipped_library(two_projects):
    a, b, cfg_a, cfg_b = two_projects
    state = resolve_state_directory(cfg_a)[0]
    assert cfg_a.paths.skills == os.path.join(state, "projects", workspace_identity(a), "skills")
    assert cfg_b.paths.skills == os.path.join(state, "projects", workspace_identity(b), "skills")
    assert resolve_config_state().config_dict["paths"]["skills"] == cfg_b.paths.skills  # Desktop/authority resolution agrees with the CLI's
    shipped = os.path.realpath(os.path.join(kriya_install_dir(), "skills"))
    assert os.path.realpath(cfg_a.paths.skills) != shipped  # no longer the directory every project writes
    engine = SkillEngine.from_config(cfg_a, workspace_path=a)
    assert engine.skills_dir == cfg_a.paths.skills and engine.skills_dirs[-1] == cfg_a.paths.skills  # the write root, highest precedence
    assert os.path.realpath(get_global_skills_dir()) in [os.path.realpath(directory) for directory in engine.skills_dirs]  # still read
    assert is_project_skill_overlay(cfg_a) and is_project_skill_overlay(cfg_b)


def test_a_skill_one_project_creates_lands_in_its_overlay_and_is_not_another_projects(two_projects):
    a, b, cfg_a, cfg_b = two_projects
    shipped_before = sorted(os.listdir(get_global_skills_dir()))
    engine_a = SkillEngine.from_config(cfg_a, workspace_path=a)
    assert engine_a.new_skill_refusal("alpha-rules") is None
    created = engine_a.create_skill_skeleton("alpha-rules")
    assert os.path.dirname(created) == cfg_a.paths.skills
    engine_a.discover_and_load()
    assert "alpha-rules" in {skill.name for skill in engine_a.list_skills()}
    engine_b = SkillEngine.from_config(cfg_b, workspace_path=b)
    engine_b.discover_and_load()
    assert "alpha-rules" not in {skill.name for skill in engine_b.list_skills()}
    assert not os.path.exists(os.path.join(cfg_b.paths.skills, "alpha-rules"))
    assert sorted(os.listdir(get_global_skills_dir())) == shipped_before  # the shipped library was not written


def test_an_explicit_skills_directory_is_exactly_what_the_user_wrote(tmp_path, monkeypatch):
    b = _project(tmp_path, "B", B_FILES)
    with open(os.path.join(b, "kriya.yaml"), "w", encoding="utf-8") as handle:
        handle.write("paths:\n  skills: ./skills\n")
    cfg = _config(monkeypatch, b)
    assert os.path.realpath(cfg.paths.skills) == os.path.realpath(os.path.join(b, "skills"))  # never relocated
    assert not is_project_skill_overlay(cfg)
    assert SkillEngine.gate_library_roots(cfg) == []  # the gate reads the explicit directory only, as before


def test_the_gate_library_roots_are_the_shipped_library_only_for_a_bound_default_project(two_projects, tmp_path):
    _a, _b, cfg_a, _cfg_b = two_projects
    assert SkillEngine.gate_library_roots(cfg_a) == [get_global_skills_dir()]
    without_global = cfg_a.model_copy(deep=True)
    without_global.skills.load_global = False
    assert SkillEngine.gate_library_roots(without_global) == []
    lookalike = cfg_a.model_copy(deep=True)
    lookalike.paths.skills = os.path.join(os.path.dirname(os.path.dirname(cfg_a.paths.skills)), "0" * 64, "skills")  # another identity's overlay
    assert not is_project_skill_overlay(lookalike) and SkillEngine.gate_library_roots(lookalike) == []
    unbound = AppConfig(paths={"memory": str(tmp_path / "m"), "skills": cfg_a.paths.skills})
    assert not is_project_skill_overlay(unbound) and SkillEngine.gate_library_roots(unbound) == []


def _guard(cfg, root=None):
    """The gate as a run builds it: the project's roots (S3) and, with ``root``, the Skills owner's coverage answer for that project (S4)."""
    coverage = None
    if root is not None:
        engine = SkillEngine.from_config(cfg, workspace_path=root)
        engine.discover_and_load()
        coverage = engine.covers_library
    return KnowledgeGuard(skills_dir=cfg.paths.skills, cutoff_date_str="2023-12-01", offline=True,
                          library_dirs=SkillEngine.gate_library_roots(cfg), coverage=coverage)


def _gaps(cfg, root):
    return [gap["library"] for gap in _guard(cfg, root).check_goal(IGNITE_GOAL, root).gaps]


def _trusted_skill(skills_root, folder="widget-core"):
    """A skill that names the probe library and holds one rule its root's owner authored."""
    path = os.path.join(str(skills_root), folder)
    os.makedirs(path)
    with open(os.path.join(path, "skill.yaml"), "w", encoding="utf-8") as handle:
        handle.write("name: widget-core\ndescription: d\ntags: [widget-core]\n")
    with open(os.path.join(path, "rules.txt"), "w", encoding="utf-8") as handle:
        handle.write("Always close the WidgetPool.\n")
    return path


def test_what_one_project_holds_in_its_overlay_does_not_close_another_projects_knowledge_gate(two_projects):
    """A5 P8, inverted. MEASURED before S3: project B's gap list went from ['org.apache.ignite:ignite-core'] to [] after project A scaffolded.
    S4 (C-06): a scaffold closes nobody's gate, A's included; what closes A's is a skill with a trusted rule, and that stays A's."""
    a, b, cfg_a, cfg_b = two_projects
    assert _gaps(cfg_a, a) == [IGNITE] and _gaps(cfg_b, b) == [IGNITE]
    target = _guard(cfg_a).generate_skill_template(IGNITE, "2.16.0")
    assert os.path.dirname(target) == cfg_a.paths.skills  # the scaffold is written to A's overlay, never to a library root
    assert _gaps(cfg_a, a) == [IGNITE] and _gaps(cfg_b, b) == [IGNITE]
    _trusted_skill(cfg_a.paths.skills)
    assert _gaps(cfg_a, a) == []
    assert _gaps(cfg_b, b) == [IGNITE]


def test_a_library_root_counts_for_coverage_and_is_never_written(tmp_path):
    library, overlay, workspace = tmp_path / "library", tmp_path / "overlay", tmp_path / "project"
    workspace.mkdir()
    _trusted_skill(library)
    engine = SkillEngine(str(overlay), load_global=False, load_cwd=False, workspace_path=str(workspace))
    engine.skills_dirs.insert(0, str(library))  # a read-only root the engine loads beside the project's, as it loads the shipped library
    engine.discover_and_load()
    with_library = KnowledgeGuard(skills_dir=str(overlay), cutoff_date_str="2023-12-01", offline=True, library_dirs=[str(library)],
                                  coverage=engine.covers_library)
    without = KnowledgeGuard(skills_dir=str(overlay), cutoff_date_str="2023-12-01", offline=True, coverage=engine.covers_library)
    assert [gap["library"] for gap in without.check_goal(IGNITE_GOAL, str(tmp_path)).gaps] == [IGNITE]
    assert with_library.check_goal(IGNITE_GOAL, str(tmp_path)).gaps == []
    before = sorted(os.listdir(library))
    assert os.path.dirname(with_library.generate_skill_template("com.example:other", "1.0.0")) == str(overlay)
    assert sorted(os.listdir(library)) == before


# ------------------------------------------------------------------ run history: each row records its project

def _row_identity(cfg, run_id):
    connection = sqlite3.connect(trace_db_path(cfg))
    try:
        return connection.execute("SELECT workspace_identity FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    finally:
        connection.close()


def _log(cfg, run_id, **extra):
    TraceLogger(trace_db_path(cfg)).log_run(run_id=run_id, goal="g", duration_sec=0.0, attempts=0, status="failed", files_modified=[], **extra)


def test_a_row_written_under_a_run_records_the_workspace_the_run_holds_its_lease_for(two_projects, monkeypatch):
    a, b, cfg_a, cfg_b = two_projects
    assert trace_db_path(cfg_a) == trace_db_path(cfg_b)  # one history database for every project: the row must say whose it is

    for root, cfg, run_id in ((a, cfg_a, "run-in-a"), (b, cfg_b, "run-in-b")):
        engine, _llm = _real_engine(cfg)

        async def first_step(_run, _arguments, cfg=cfg, run_id=run_id):
            _log(cfg, run_id)  # any writer under the run: none of them names the project
            raise _Stop()

        monkeypatch.setattr("kriya.workflow.plan_executor.execute_direct_goal", first_step)
        with pytest.raises(_Stop):
            asyncio.run(engine.run_generation_workflow("goal", root))
    assert _row_identity(cfg_a, "run-in-a") == (workspace_identity(a),)
    assert _row_identity(cfg_b, "run-in-b") == (workspace_identity(b),)


def test_a_row_written_outside_a_run_is_unattributed_unless_its_writer_names_the_project(two_projects):
    a, _b, cfg_a, _cfg_b = two_projects
    _log(cfg_a, "outside")
    assert _row_identity(cfg_a, "outside") == (None,)  # never assumed to be the current project's
    _log(cfg_a, "named", workspace_identity=project_identity(cfg_a))
    assert _row_identity(cfg_a, "named") == (workspace_identity(a),)


def test_the_in_progress_marker_between_runs_records_the_configurations_project(two_projects):
    from kriya.application.run import RunService

    a, _b, cfg_a, _cfg_b = two_projects
    RunService().mark_in_progress(None, cfg_a, "marker", "goal")
    assert _row_identity(cfg_a, "marker") == (workspace_identity(a),)


def test_a_history_database_from_before_the_column_gains_it_and_keeps_its_rows(tmp_path):
    path = str(tmp_path / "state" / "traces.db")
    os.makedirs(os.path.dirname(path))
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, timestamp TEXT, goal TEXT, duration_sec REAL, attempts INTEGER, status TEXT, "
                       "files_modified TEXT, retrieved_chunks TEXT, active_skills TEXT, prompt_rendered TEXT, gate_outcomes TEXT, model_hops TEXT)")
    connection.execute("INSERT INTO runs (run_id, goal, status) VALUES ('old', 'g', 'success')")
    connection.commit()
    connection.close()
    TraceLogger(path).log_run(run_id="new", goal="g", duration_sec=0.0, attempts=0, status="failed", files_modified=[], workspace_identity="x" * 64)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT run_id, workspace_identity FROM runs ORDER BY run_id").fetchall() == [("new", "x" * 64), ("old", None)]
    finally:
        connection.close()


# ------------------------------------------------------------------ the legacy shared memory directory: reported, never used

def test_legacy_shared_memory_stores_are_listed_and_left_alone(two_projects, tmp_path):
    _a, _b, cfg_a, _cfg_b = two_projects
    install = tmp_path / "install"
    (install / "memory").mkdir(parents=True)
    assert legacy_shared_memory_stores(cfg_a, install_dir=str(install)) == ()
    names = ("dependency_graph.db", "knowledge_cache.db", "vector_index.db", "web_knowledge.db")
    for name in names:
        (install / "memory" / name).write_bytes(b"legacy " + name.encode())
    (install / "memory" / "notes.txt").write_text("not a store")
    (install / "memory" / "directory.db").mkdir()
    found = legacy_shared_memory_stores(cfg_a, install_dir=str(install))
    assert found == tuple(os.path.join(os.path.realpath(install / "memory"), name) for name in names)
    for name in names:  # inspection only
        assert (install / "memory" / name).read_bytes() == b"legacy " + name.encode()
    # A configuration that names that very directory explicitly has no "legacy" there: they are its configured stores.
    explicit = cfg_a.model_copy(deep=True)
    explicit.paths.memory = str(install / "memory")
    assert legacy_shared_memory_stores(explicit, install_dir=str(install)) == ()
    assert legacy_shared_memory_stores(cfg_a, install_dir=str(tmp_path / "no-such-install")) == ()


def test_doctor_reports_the_legacy_shared_memory_stores(two_projects, monkeypatch):
    _a, _b, cfg_a, _cfg_b = two_projects
    monkeypatch.setattr("kriya.core.state_paths.legacy_shared_memory_stores", lambda _cfg: ("/legacy/memory/vector_index.db",))
    with patch("kriya.cli.load_config", return_value=cfg_a):
        output = CliRunner().invoke(main, ["doctor"]).output
    assert "Legacy shared memory: 1 store file(s)" in output and "IGNORED" in output and "/legacy/memory/vector_index.db" in output
    monkeypatch.setattr("kriya.core.state_paths.legacy_shared_memory_stores", lambda _cfg: ())
    with patch("kriya.cli.load_config", return_value=cfg_a):
        assert "Legacy shared memory" not in CliRunner().invoke(main, ["doctor"]).output


# ------------------------------------------------------------------ identity equivalence for ONE authoritative project root

def test_every_way_of_starting_work_on_the_same_selected_root_has_the_same_project_identity_and_state(tmp_path, monkeypatch):
    """CLI, Desktop/authority resolution, a direct run entry, the enforce entry and a resumed/carried configuration, all for the SAME selected
    authoritative root (a repository; from its root and from a directory inside it). Deliberately NOT a claim about every cwd in a tree that
    is no repository: a directory of such a tree invoked on its own, with no root selected, is its own project under D-001
    (test_a_subdirectory_of_a_project_that_is_no_repository_invoked_on_its_own_is_its_own_project)."""
    from kriya.workflow.workflow_controller import WorkflowController

    root, cli = _git_project(tmp_path, monkeypatch)
    identity = workspace_identity(root)

    desktop = resolve_config_state().config_dict["paths"]
    from_inside = _config(monkeypatch, os.path.join(root, "src"))
    resumed = cli.model_copy(deep=True)  # what a checkpointed run, an enforce unit and a plan sandbox carry

    direct = AppConfig(paths={"memory": cli.paths.memory, "skills": cli.paths.skills})
    engine, _llm = _real_engine(direct)

    async def stop(_run, _arguments):
        raise _Stop()

    monkeypatch.setattr("kriya.workflow.plan_executor.execute_direct_goal", stop)
    with pytest.raises(_Stop):
        asyncio.run(engine.run_generation_workflow("goal", root))

    enforce = AppConfig(paths={"memory": cli.paths.memory, "skills": cli.paths.skills})
    enforce_engine, _llm = _real_engine(enforce)

    async def classify(*_args, **_kwargs):
        raise _Stop()

    enforce_engine.engineering_triage.classify = classify
    with pytest.raises(_Stop):
        asyncio.run(WorkflowController(enforce_engine).execute("goal", root))

    for name, cfg in (("cli", cli), ("subdirectory", from_inside), ("resume", resumed), ("direct", direct), ("enforce", enforce)):
        assert project_identity(cfg) == identity, name
        assert os.path.realpath(cfg._project_root) == os.path.realpath(root), name
        assert (cfg.paths.memory, cfg.paths.skills) == (cli.paths.memory, cli.paths.skills), name
    assert (desktop["memory"], desktop["skills"]) == (cli.paths.memory, cli.paths.skills)
    state = resolve_state_directory(cli)[0]
    assert cli.paths.memory == os.path.join(state, "projects", identity, "memory")
    assert cli.paths.skills == os.path.join(state, "projects", identity, "skills")


# ------------------------------------------------------------------ `kriya fix -w <dir>`: authority/config A + independent workspace B -> refuse

def _fix(workspace):
    """`kriya fix -e ... -w <workspace> -y` with the REAL load_config (the configuration of the directory the command is run in)."""
    never = AsyncMock(side_effect=AssertionError("a refused run must not reach a model"))
    with patch("kriya.core.llm.LLMClient.complete", new=never):
        result = CliRunner().invoke(main, ["fix", "-e", "boom: compilation failed", "-w", workspace, "-y"])
    return result, never


def test_fix_for_another_projects_workspace_is_refused_before_anything_runs(two_projects, monkeypatch):
    a, b, _cfg_a, _cfg_b = two_projects
    monkeypatch.chdir(a)  # the configuration is project A's
    with open(os.path.join(b, "src", "beta.py"), "rb") as handle:
        before = handle.read()
    result, model = _fix(b)
    assert result.exit_code != 0
    assert f"[REFUSED] {PROJECT_IDENTITY_MISMATCH}" in result.output and "Nothing was read" in result.output, result.output
    assert "Repair attempts completed" not in result.output  # nothing was attempted: never worded as a repair that failed
    assert "SUCCESS" not in result.output and model.await_count == 0
    with open(os.path.join(b, "src", "beta.py"), "rb") as handle:
        assert handle.read() == before


def test_fix_run_from_the_projects_own_directory_passes_the_run_entry(two_projects, monkeypatch):
    _a, b, _cfg_a, _cfg_b = two_projects
    monkeypatch.chdir(b)  # B is the selected project: its own configuration, its own workspace
    reached = []

    async def first_step(_run, _arguments):
        reached.append(True)
        raise _Stop()

    monkeypatch.setattr("kriya.workflow.plan_executor.execute_direct_goal", first_step)
    result, _model = _fix(".")
    assert reached == [True], result.output  # the run entry accepted the configuration and the run started
    assert PROJECT_IDENTITY_MISMATCH not in result.output
