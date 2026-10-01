"""Code Intelligence R1, Stage 4: index correctness adjacent to the new
structural index (E-01, E-17, E-13, E-14).

Each test reproduces the measured scenario through the real
``RepositoryAnalyzer.index_repository`` with a deterministic embedder.
"""
import json
import os
import subprocess

import pytest

from kriya.analyzer.analyzer import RepositoryAnalyzer, RepositoryModel, chunk_file_with_metadata_headers
from kriya.analyzer.graph import DependencyGraph
from kriya.config import AppConfig
from kriya.memory.vector import LocalVectorStore
from kriya.skills.skill import Skill, fact_match, mentions_term
from _fake_embedding import StaticEmbedder


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "T")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _cfg(tmp_path):
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "global-skills")
    return cfg


async def _index(repo, cfg, **kwargs):
    return await RepositoryAnalyzer(str(repo)).index_repository(
        cfg, generate_conventions_skill=False, embedding_client=StaticEmbedder([0.3, 0.4, 0.5]), **kwargs)


def _state(cfg):
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    graph = DependencyGraph(os.path.join(cfg.paths.memory, "dependency_graph.db"))
    try:
        vectors = {r[0] for r in store.conn.execute("SELECT filepath FROM vector_chunks WHERE is_current = 1")}
        return set(store.file_metadata.keys()), vectors, graph.indexed_paths()
    finally:
        store.close()
        graph.close()


SEVEN = {f"m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(7)}


@pytest.mark.asyncio
async def test_e01_changed_index_keeps_every_unchanged_file(tmp_path):
    """The review's reproducer: index 7 files, edit one, `analyze --changed`;
    all 7 must still be indexed (before: only the edited one survived)."""
    repo = _repo(tmp_path, SEVEN)
    cfg = _cfg(tmp_path)
    await _index(repo, cfg)
    (repo / "m3.py").write_text("def f3():\n    return 33\n")
    report = await _index(repo, cfg, changed=True)
    cache, vectors, graph = _state(cfg)
    assert cache == vectors == graph == set(SEVEN)
    assert report.indexed == 1 and report.removed == []


@pytest.mark.asyncio
async def test_e01_changed_index_still_removes_a_really_deleted_file(tmp_path):
    repo = _repo(tmp_path, SEVEN)
    cfg = _cfg(tmp_path)
    await _index(repo, cfg)
    os.remove(repo / "m5.py")
    report = await _index(repo, cfg, changed=True)
    cache, vectors, graph = _state(cfg)
    assert cache == vectors == graph == set(SEVEN) - {"m5.py"}
    assert report.removed == ["m5.py"]


@pytest.mark.asyncio
async def test_e17_deleted_file_leaves_no_cache_row_vector_or_graph_row(tmp_path):
    repo = _repo(tmp_path, SEVEN)
    cfg = _cfg(tmp_path)
    await _index(repo, cfg)
    os.remove(repo / "m0.py")
    await _index(repo, cfg)
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    try:
        assert "m0.py" not in store.file_metadata
        assert store.conn.execute("SELECT COUNT(*) FROM vector_chunks WHERE filepath = 'm0.py'").fetchone()[0] == 0
        assert "m0.py" not in store.indexed_paths()
    finally:
        store.close()
    assert "m0.py" not in _state(cfg)[2]


@pytest.mark.asyncio
async def test_e17_deleted_file_whose_embedding_had_failed_is_still_removed(tmp_path):
    """A file whose last embedding failed has no cache entry (by design) but
    keeps stale vectors and graph rows; deleting it must remove them too."""
    repo = _repo(tmp_path, SEVEN)
    cfg = _cfg(tmp_path)
    await _index(repo, cfg)
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    store.mark_stale("m2.py")
    del store.file_metadata["m2.py"]
    store.close()
    os.remove(repo / "m2.py")
    report = await _index(repo, cfg)
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    try:
        assert store.conn.execute("SELECT COUNT(*) FROM vector_chunks WHERE filepath = 'm2.py'").fetchone()[0] == 0
    finally:
        store.close()
    assert "m2.py" not in _state(cfg)[2] and report.removed == ["m2.py"]


def test_e17_remove_file_is_one_transaction(tmp_path):
    store = LocalVectorStore(str(tmp_path / "v.db"))
    try:
        from _fake_embedding import seed_index
        seed_index(store, [("a.py", "x", [0.1, 0.2])])
        store.file_metadata["a.py"] = {"mtime": 1.0, "hash": "h"}
        store.conn.execute("CREATE TEMP TRIGGER refuse BEFORE DELETE ON file_metadata "
                           "BEGIN SELECT RAISE(ABORT, 'refused'); END")
        with pytest.raises(Exception, match="refused"):
            store.remove_file("a.py")
        # Nothing of the file was removed: the vector delete rolled back.
        assert store.conn.execute("SELECT COUNT(*) FROM vector_chunks WHERE filepath = 'a.py'").fetchone()[0] == 1
        assert "a.py" in store.file_metadata
    finally:
        store.close()


def test_e17_module_declarations_chunk_records_its_real_line_range():
    content = "import os\n\n\ndef f():\n    pass\n\n\nX = 1\nY = 2\n"
    module = next(c for c in chunk_file_with_metadata_headers(content, "m.py") if "Module Declarations" in c["text"])
    # import on line 1 .. Y on line 9 (before: start 1, end = 3 lines counted)
    assert (module["start"], module["end"]) == (1, 9)


@pytest.mark.asyncio
async def test_e13_skill_packages_and_owned_roots_are_not_indexed_but_code_named_skills_is(tmp_path):
    repo = _repo(tmp_path, {
        "src/app/Main.java": "package app;\npublic class Main {}\n",
        # a real code package that happens to be called "skills"
        "src/app/skills/Ranker.java": "package app.skills;\npublic class Ranker {}\n",
        # a skill package (marker file) shipping a Java example
        "skills/payments/skill.yaml": "name: payments\n",
        "skills/payments/examples/Main.java": "package examples;\npublic class Main {}\n",
        # the configured project skills root holding a loose file
        "kriya-skills/notes.py": "x = 1\n",
    })
    cfg = _cfg(tmp_path)
    cfg.paths.skills = str(repo / "kriya-skills")
    await _index(repo, cfg)
    _cache, vectors, graph = _state(cfg)
    assert vectors == graph == {"src/app/Main.java", "src/app/skills/Ranker.java"}
    graph_db = DependencyGraph(os.path.join(cfg.paths.memory, "dependency_graph.db"))
    try:
        # the skill example no longer collides with the real Main type
        assert graph_db.get_class_symbol_locations()[".java:Main"] == ["src/app/Main.java"]
    finally:
        graph_db.close()


def test_e14_tags_match_by_normalized_token_equality():
    assert not mentions_term("javascript", "java")
    assert not mentions_term("org.webjars:javascript-utils", "java")
    assert mentions_term("org.springframework.boot:spring-boot-starter-web", "spring-boot")
    assert mentions_term("Build a Spring Boot REST API", "spring-boot")
    assert mentions_term("junit:junit", "JUnit")
    assert not mentions_term("springboot", "spring")
    assert not mentions_term("anything", "")


def test_e14_fact_match_does_not_activate_java_skill_for_a_javascript_repo():
    java_skill = Skill(name="java-conventions", description="d", tags=["java"])
    js_repo = RepositoryModel(root_path="/r", languages={"JavaScript": 3}, dependencies=["javascript-obfuscator"],
                              frameworks=["Express"])
    java_repo = RepositoryModel(root_path="/r", languages={"Java": 3}, dependencies=["java-jwt"], frameworks=[])
    assert not fact_match(java_skill, js_repo)
    assert fact_match(java_skill, java_repo)


class _CountingEmbedder(StaticEmbedder):
    def __init__(self, vector):
        super().__init__(vector)
        self.texts = 0

    async def embed(self, texts, *, is_query=False, deadline=None):
        self.texts += len(texts)
        return await super().embed(texts, is_query=is_query, deadline=deadline)


async def _index_counting(repo, cfg):
    embedder = _CountingEmbedder([0.3, 0.4, 0.5])
    report = await RepositoryAnalyzer(str(repo)).index_repository(
        cfg, generate_conventions_skill=False, embedding_client=embedder)
    return report, embedder.texts


def _graph(cfg):
    return DependencyGraph(os.path.join(cfg.paths.memory, "dependency_graph.db"))


@pytest.mark.asyncio
async def test_manifest_binds_structure_to_parser_identity_embedding_and_raw_digests(tmp_path):
    import hashlib

    from kriya.analyzer.graph import structural_identity
    from kriya.code_intel.parsing import parser_identity

    repo = _repo(tmp_path, SEVEN)
    cfg = _cfg(tmp_path)
    report = await _index(repo, cfg)
    graph = _graph(cfg)
    try:
        manifest = graph.manifest()
        identity = json.loads(manifest["structural_identity"])
        assert identity == structural_identity()
        assert identity["tree_sitter"] == parser_identity().tree_sitter
        assert {"schema_version", "java_grammar", "python_grammar", "structural_parser"} <= set(identity)
        assert manifest["embedding_fingerprint"] == report.fingerprint
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        assert manifest["repository_revision"] == head
        digests = dict(graph.conn.execute("SELECT filepath, source_digest FROM files").fetchall())
        assert digests["m1.py"] == hashlib.sha256((repo / "m1.py").read_bytes()).hexdigest()
    finally:
        graph.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["drop_manifest", "other_parser"])
async def test_structure_from_another_identity_is_reparsed_without_reembedding(tmp_path, tamper):
    repo = _repo(tmp_path, SEVEN)
    cfg = _cfg(tmp_path)
    await _index(repo, cfg)
    graph = _graph(cfg)
    try:
        if tamper == "drop_manifest":  # a pre-R1 graph
            graph.conn.execute("DELETE FROM index_manifest")
        else:
            graph.conn.execute("UPDATE index_manifest SET value = replace(value, ?, 'ci-structural/0')"
                               " WHERE key = 'structural_identity'", (structural_parser_version(),))
        # stale structure that must not survive: a symbol the current parser never produces
        graph.conn.execute("INSERT INTO symbols (filepath, name, type, start_line, end_line)"
                           " VALUES ('m1.py', 'ghost', 'function', 1, 1)")
        graph.conn.commit()
    finally:
        graph.close()
    report, embedded = await _index_counting(repo, cfg)
    assert report.structure_rebuilt is True and report.restructured == 7
    assert embedded == 0 and report.indexed == 0  # vectors were current: nothing re-embedded
    graph = _graph(cfg)
    try:
        assert graph.find_symbol_locations("ghost") == []
        assert {r["filepath"] for r in graph.find_symbol_locations("f1")} == {"m1.py"}
    finally:
        graph.close()


@pytest.mark.asyncio
async def test_same_identity_reuses_structure_and_vectors(tmp_path):
    repo = _repo(tmp_path, SEVEN)
    cfg = _cfg(tmp_path)
    await _index(repo, cfg)
    report, embedded = await _index_counting(repo, cfg)
    assert report.structure_rebuilt is False and report.restructured == 0 and embedded == 0


def structural_parser_version():
    from kriya.code_intel.parsing import STRUCTURAL_PARSER_VERSION
    return STRUCTURAL_PARSER_VERSION
