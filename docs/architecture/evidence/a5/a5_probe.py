"""Slice A5 probe (model-free, read-only against the baseline code; writes only inside a temporary directory; no network).

Run through run_a5.sh (isolated KRIYA_STATE_DIR and KRIYA_LOG_DIR; PYTHONPATH = <worktree>:<worktree>/tests). The probe itself sets a
scratch HOME, KRIYA_AUTHORITY_HOME and KRIYA_MCP_APPROVAL_HOME before importing Kriya.

Each probe prints what it measured. A probe that cannot be set up prints PROBE_SETUP_FAILED with the error and measures nothing
(it is then not evidence for anything).

The packaged default of paths.memory / paths.skills is only RESOLVED here (P1); every store this probe writes lives in a scratch
"shared" directory that stands in for it, so the installation tree is never written by the probe.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import tempfile
import traceback

MARKER = "ALPHA_PRIVATE_MARKER_7391"
SCRATCH = tempfile.mkdtemp(prefix="kriya-a5-probe-")
START = os.getcwd()
# Isolation is set here, before any Kriya module is imported: a scratch HOME (so ~/.kriya is never the operator's), scratch authority
# and MCP-approval homes, and no user/system Git configuration for the `git init` calls below.
for _key, _leaf in (("HOME", "home"), ("KRIYA_AUTHORITY_HOME", "authority"), ("KRIYA_MCP_APPROVAL_HOME", "mcp")):
    os.environ[_key] = os.path.join(SCRATCH, _leaf)
    os.makedirs(os.environ[_key], exist_ok=True)
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def section(name: str) -> None:
    print(f"\n=== {name}")


def guarded(name: str, body) -> None:
    section(name)
    try:
        body()
    except Exception as error:  # a probe that cannot run is reported, never read as a result
        print(f"PROBE_SETUP_FAILED {type(error).__name__}: {error}")
        traceback.print_exc(limit=5)
    finally:
        os.chdir(START)


def _project(name: str, files: dict, git: bool = True) -> str:
    root = os.path.realpath(os.path.join(SCRATCH, name))
    for rel, text in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
    os.makedirs(root, exist_ok=True)
    if git:
        subprocess.run(["git", "init", "-q", root], check=True, capture_output=True)
    return root


A_FILES = {"src/alpha_secret.py": f'class AlphaSecretService:\n    """{MARKER}"""\n\n    def rotate_alpha_key(self):\n        return "{MARKER}"\n'}
B_FILES = {"src/beta.py": "class BetaThing:\n    def greet(self):\n        return 'hello'\n"}


def _counts(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"<absent>": True}
    with sqlite3.connect(db_path) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts_%' AND name NOT LIKE 'ci_fts_%'")]
        out = {}
        for name in sorted(names):
            try:
                out[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            except sqlite3.Error as error:
                out[name] = f"unreadable: {error}"
        return out


def _columns(db_path: str) -> dict:
    with sqlite3.connect(db_path) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%'")]
        return {name: [row[1] for row in conn.execute(f"PRAGMA table_info({name})")] for name in sorted(names)}


# ---------------------------------------------------------------- P1: where the packaged defaults resolve for different projects
def p1_default_resolution() -> None:
    from kriya.application.project_init import initialize_project
    from kriya.config.config import load_config
    from kriya.config.environment import kriya_install_dir
    from kriya.config.resolution import resolve_config_state

    install = kriya_install_dir()
    a = _project("p1-A", A_FILES)
    b = _project("p1-B", B_FILES)
    setup = _project("p1-Setup", {}, git=False)
    resolved = {}
    for label, root in (("A (no kriya.yaml)", a), ("B (no kriya.yaml)", b)):
        os.chdir(root)
        cfg = load_config()
        resolved[label] = (cfg.paths.memory, cfg.paths.skills, cfg.paths.state)
        print(f"{label}: paths.memory={os.path.relpath(cfg.paths.memory, install)!r} paths.skills={os.path.relpath(cfg.paths.skills, install)!r} "
              f"(both relative to the Kriya installation directory) paths.state={cfg.paths.state!r}")
    print("A and B resolve the SAME paths.memory:", resolved["A (no kriya.yaml)"][0] == resolved["B (no kriya.yaml)"][0])
    print("A and B resolve the SAME paths.skills:", resolved["A (no kriya.yaml)"][1] == resolved["B (no kriya.yaml)"][1])
    print("that paths.memory is inside project A:", resolved["A (no kriya.yaml)"][0].startswith(a + os.sep))
    initialize_project(setup)
    os.chdir(setup)
    state = resolve_config_state()
    paths = state.config_dict.get("paths", {})
    print(f"Project Setup folder: config_source={state.config_source} paths.memory={os.path.relpath(paths.get('memory'), install)!r} "
          f"paths.skills={os.path.relpath(paths.get('skills'), install)!r} (relative to the installation directory)")
    print("Project Setup folder resolves the same paths.memory as A:", paths.get("memory") == resolved["A (no kriya.yaml)"][0])
    with open(os.path.join(setup, "kriya.yaml"), "r", encoding="utf-8") as handle:
        import yaml
        written = yaml.safe_load(handle)
    print("Project Setup kriya.yaml top-level keys:", sorted(written), "| has a 'paths' section:", "paths" in written)
    print("Project Setup fields needing operator approval (SEC-009 violations):",
          sorted(f"{v.field_path}[{v.classification.value}]" for v in state.violations))


# ---------------------------------------------------------------- P2..P4: one shared index, two projects
def _shared_cfg(root: str, shared: str):
    from kriya.config.config import load_config

    os.chdir(root)
    cfg = load_config().model_copy(deep=True)
    cfg.paths.memory = shared  # stands in for the packaged default both projects resolve (P1)
    cfg.paths.skills = os.path.join(SCRATCH, "shared-skills")
    os.makedirs(cfg.paths.skills, exist_ok=True)
    return cfg


def p2_shared_index() -> None:
    from _fake_embedding import StaticEmbedder  # tests/_fake_embedding.py (deterministic, no model)
    from kriya.analyzer.analyzer import RepositoryAnalyzer
    from kriya.analyzer.graph import DependencyGraph
    from kriya.memory.vector import LocalVectorStore
    from kriya.workflow.context_budget import RetrievalLimits
    from kriya.workflow.goal_facts import developer_goal_facts
    from kriya.workflow.graph_retrieval import open_code_intelligence, retrieve_graph_context

    a = _project("p2-A", A_FILES)
    b = _project("p2-B", B_FILES)
    shared = os.path.join(SCRATCH, "shared-memory")
    os.makedirs(shared, exist_ok=True)
    graph_db, vector_db = os.path.join(shared, "dependency_graph.db"), os.path.join(shared, "vector_index.db")
    embedder = StaticEmbedder([1.0, 0.0, 0.0, 0.0])

    cfg_a = _shared_cfg(a, shared)
    asyncio.run(RepositoryAnalyzer(a).index_repository(cfg_a, embedding_client=embedder, generate_conventions_skill=False))
    print("after indexing project A:", json.dumps({"dependency_graph.db": _counts(graph_db), "vector_index.db": _counts(vector_db)}, sort_keys=True))
    print("columns (is there any workspace/project/root column?):", json.dumps({"dependency_graph.db": _columns(graph_db), "vector_index.db": _columns(vector_db)}, sort_keys=True))
    graph = DependencyGraph(graph_db)
    try:
        print("index manifest keys:", sorted(graph.manifest()))
    finally:
        graph.close()

    # --- project B now reads through the same configured location (it was never indexed)
    cfg_b = _shared_cfg(b, shared)
    graph = DependencyGraph(graph_db)
    try:
        print("B: DependencyGraph.has_indexed_files() (the auto-index 'already indexed' test):", graph.has_indexed_files())
        print("B: find_symbol_locations('AlphaSecretService'):", graph.find_symbol_locations("AlphaSecretService"))
        print("B: get_class_symbol_locations():", graph.get_class_symbol_locations())
    finally:
        graph.close()
    service = open_code_intelligence(b, graph_db)
    print("B: open_code_intelligence returns a service (a baseline 'exists' for B):", service is not None)
    if service is not None:
        try:
            print("B: baseline find_symbol('AlphaSecretService') WITHOUT current_view():", [s.path for s in service.find_symbol("AlphaSecretService")])
            print("B: current_view().find_symbol('AlphaSecretService'):", [s.path for s in service.current_view().find_symbol("AlphaSecretService")])
            print("B: current_view().find_symbol('BetaThing'):", [s.path for s in service.current_view().find_symbol("BetaThing")])
        finally:
            service.close()
    goal = "Change AlphaSecretService so that rotate_alpha_key logs the rotation."
    store = LocalVectorStore(vector_db)
    try:
        retrieval = asyncio.run(retrieve_graph_context(
            goal, b, embed_client=embedder, vector_store=store, dependency_graph_path=graph_db,
            limits=RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30),
            embedding_model=cfg_b.embedding.model, budget_limit=lambda: 4000))
    finally:
        store.close()
    chunks = json.dumps(retrieval.retrieved_chunks)
    print("B: retrieve_graph_context matched:", retrieval.matched, "| matched_files:", retrieval.matched_files, "| related_files:", retrieval.related_files)
    print("B: A's private marker in retrieved_chunks:", MARKER in chunks, "| in graph_rag_context (the Planner's block):", MARKER in (retrieval.graph_rag_context or ""))
    print("B: retrieval_member_hints:", retrieval.retrieval_member_hints, "| hypothesis_candidates:", retrieval.hypothesis_candidates)
    facts = developer_goal_facts(goal, b, graph_db)
    print("B: developer_goal_facts names A's file:", "alpha_secret" in facts, "| names the marker:", MARKER in facts, "| length:", len(facts))

    # --- project B is then indexed into the same location
    asyncio.run(RepositoryAnalyzer(b).index_repository(cfg_b, embedding_client=embedder, generate_conventions_skill=False))
    graph = DependencyGraph(graph_db)
    try:
        print("after indexing project B into the same location: indexed paths =", sorted(graph.indexed_paths()))
        print("A's symbol still locatable:", bool(graph.find_symbol_locations("AlphaSecretService")))
    finally:
        graph.close()
    print("counts:", json.dumps({"dependency_graph.db": _counts(graph_db), "vector_index.db": _counts(vector_db)}, sort_keys=True))


# ---------------------------------------------------------------- P5: the write gate over workspace paths that carry authority for later runs
def p5_write_gate() -> None:
    from kriya.policy.filesystem import AuthorizedFileWriter

    root = _project("p5-W", {"src/app.py": "x = 1\n", "skills/local/rules.txt": "rule\n", "kriya.yaml": "llm:\n  model: m\n"})
    os.makedirs(os.path.join(root, ".kriya", "control"), exist_ok=True)
    writer = AuthorizedFileWriter(root)
    for rel in ("src/app.py", "skills/local/rules.txt", "skills/new-skill/skill.yaml", "kriya.yaml", "kriya.yml", ".kriya/control/state.json",
                ".git/hooks/pre-commit", ".github/workflows/ci.yml", ".gitignore", ".gitattributes", "../outside.txt", ".env"):
        result = writer.authorize(os.path.join(root, rel))
        print(f"AuthorizedFileWriter.authorize({rel!r}): {result.decision.value} {result.reason_code}")


# ---------------------------------------------------------------- P6: who may set the run's wall-clock budget
def p6_budget_authority() -> None:
    from kriya.config.resolution import resolve_config_state

    def violations(name: str, text: str):
        root = _project(name, {"kriya.yaml": text}, git=False)
        os.chdir(root)
        try:
            state = resolve_config_state()
        except Exception as error:
            return f"REFUSED {type(error).__name__}: {str(error)[:160]}", None
        return sorted(v.field_path for v in state.violations), state.config_dict.get("autonomy", {}).get("generation_time_budget_seconds")

    print("packaged default budget:", violations("p6-none", "llm:\n  model: m\n"))
    print("repository raises the budget to 999999:", violations("p6-raise", "autonomy:\n  generation_time_budget_seconds: 999999\n"))
    print("repository sets the budget to null (no deadline):", violations("p6-null", "autonomy:\n  generation_time_budget_seconds: null\n"))
    print("repository sets 2400 (the Scenario-04 template value):", violations("p6-2400", "autonomy:\n  generation_time_budget_seconds: 2400\n"))
    print("runtime_profile production alone:", violations("p6-prod", "runtime_profile: production\n"))
    print("runtime_profile production + 2400:", violations("p6-prod-2400", "runtime_profile: production\nautonomy:\n  generation_time_budget_seconds: 2400\n"))
    print("runtime_profile production + 7200:", violations("p6-prod-7200", "runtime_profile: production\nautonomy:\n  generation_time_budget_seconds: 7200\n"))
    print("runtime_profile production + null:", violations("p6-prod-null", "runtime_profile: production\nautonomy:\n  generation_time_budget_seconds: null\n"))
    print("repository sets risk_threshold_lines 100000 and mode guardrails:",
          violations("p6-risk", "autonomy:\n  risk_threshold_lines: 100000\n  mode: guardrails\n"))


# ---------------------------------------------------------------- P7: state and identity keys of two projects
def p7_state_keys() -> None:
    from kriya.config.authority_approval import default_local_approval_path
    from kriya.config.config import load_config
    from kriya.context.workspace_identity import workspace_identity
    from kriya.core.state_paths import resolve_state_directory, trace_db_path
    from kriya.mcp.invocation_approval import default_local_approval_path as mcp_approval_path
    from kriya.memory.learned_knowledge import learned_knowledge_db_path

    a = _project("p7-A", A_FILES)
    b = _project("p7-B", B_FILES)
    rows = {}
    for label, root in (("A", a), ("B", b)):
        os.chdir(root)
        cfg = load_config()
        rows[label] = {"workspace_identity": workspace_identity(root), "state_directory": resolve_state_directory(cfg), "trace_db": trace_db_path(cfg),
                       "config_approval": default_local_approval_path(root), "mcp_approval": mcp_approval_path(root),
                       "learned_knowledge_db": learned_knowledge_db_path(cfg)}
    for key in rows["A"]:
        print(f"{key}: same for A and B = {rows['A'][key] == rows['B'][key]}")
    print("workspace .kriya/control is per project by construction (inside each workspace root):",
          os.path.join(a, ".kriya", "control") != os.path.join(b, ".kriya", "control"))


# ---------------------------------------------------------------- P8: one skills location, two projects, and the knowledge gate
def p8_shared_skills_and_knowledge_gate() -> None:
    from kriya.skills.skill import SkillEngine, is_accidental_shared_skills_write
    from kriya.tools.knowledge import KnowledgeGuard

    a = _project("p8-A", A_FILES)
    b = _project("p8-B", B_FILES)
    shared = os.path.join(SCRATCH, "p8-shared-skills")  # stands in for the packaged default both projects resolve (P1)
    os.makedirs(shared, exist_ok=True)
    goal = "Upgrade the cache to use org.apache.ignite:ignite-core 2.16.0."
    guard_a = KnowledgeGuard(skills_dir=shared, cutoff_date_str="2023-12-01", offline=True)
    guard_b = KnowledgeGuard(skills_dir=shared, cutoff_date_str="2023-12-01", offline=True)
    print("project B before anything: gaps =", [g["library"] for g in guard_b.check_goal(goal, b).gaps])
    target = guard_a.generate_skill_template("org.apache.ignite:ignite-core", "2.16.0")  # what auto-accrual does at the end of a run in project A
    print("project A's run scaffolds:", os.path.basename(target))
    print("project B afterwards: gaps =", [g["library"] for g in guard_b.check_goal(goal, b).gaps])
    from kriya.config.environment import kriya_install_dir
    install_skills = os.path.join(kriya_install_dir(), "skills")
    print("is_accidental_shared_skills_write(<install>/skills, project A):", is_accidental_shared_skills_write(install_skills, a))
    engine = SkillEngine(install_skills, load_global=False, load_cwd=False, workspace_path=a)
    print("SkillEngine.new_skill_refusal for project A on <install>/skills is a refusal:", engine.new_skill_refusal("x") is not None)
    print("KnowledgeGuard has a workspace attribute:", any("workspace" in name for name in vars(guard_a)))


if __name__ == "__main__":
    print("scratch:", "<tmp>/" + os.path.basename(SCRATCH))
    guarded("P1 packaged-default paths.memory / paths.skills for two projects and a Project Setup folder", p1_default_resolution)
    guarded("P2-P4 one index location, two projects: what project B reads of project A; what indexing B does to A", p2_shared_index)
    guarded("P5 the write gate over workspace paths that carry authority into later runs", p5_write_gate)
    guarded("P6 who may set the run's wall-clock budget and the size trigger", p6_budget_authority)
    guarded("P7 state and identity keys of two projects", p7_state_keys)
    guarded("P8 one skills location, two projects, and the knowledge gate", p8_shared_skills_and_knowledge_gate)
