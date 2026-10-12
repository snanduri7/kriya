"""BACKEND-READINESS-V2, PLAN-TARGET-GROUNDING-001 (b)/(c): goal-named facts and the delegating-shim plan check.

Mechanisms measured on the frozen runs (handover/BACKEND-READINESS-V2/01): python-slugify (T4) - the Planner planned a
four-line ``setup.py`` shim whose own text says the metadata lives in ``pyproject.toml``, where the dependency the goal
names is declared; jsoup (T2) - the goal says ``Element.absUrl`` and only Element's supertype declares it. The fixtures
are small synthetic repositories with those shapes; nothing is keyed to a task, repository or file name.
"""
import asyncio
import subprocess
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _fake_embedding import StaticEmbedder
from test_backend_readiness_v2_localization import GOAL as MEMBER_GOAL
from test_backend_readiness_v2_localization import PATHS as JAVA_PATHS
from test_backend_readiness_v2_localization import _service

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.workflow import goal_facts
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.triage import ChangeKind

SHIM = '"""Compatibility shim; metadata and build configuration live in pyproject.toml."""\nfrom setuptools import setup\n\nsetup()\n'
PYPROJECT = textwrap.dedent("""\
    [build-system]
    requires = ["setuptools>=77"]
    build-backend = "setuptools.build_meta"

    [project]
    name = "demo-lib"
    dynamic = ["version"]
    dependencies = []

    [project.optional-dependencies]
    text-unidecode = ["text-unidecode>=1.3"]
    unidecode = ["Unidecode>=1.1.1"]
    """)
DEP_GOAL = ("A plain install no longer works for non-ASCII input.\n\n"
            "Expected: cafe\nActual:   ModuleNotFoundError: No module named 'text_unidecode'\n\n"
            "The README says a plain pip install gives working transliteration via text-unidecode.\n")
PY_PATHS = {"setup.py": SHIM, "pyproject.toml": PYPROJECT, "demo_lib/__init__.py": "x = 1\n", "tests/test_a.py": "def test_a():\n    pass\n"}


def _write(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _plan(*units):
    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[
        Subtask(id=sid, description=f"unit {sid}", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=path, action=FileAction.MODIFY) for path in paths])
        for sid, paths in units])


def test_t4_the_goal_package_is_located_in_the_manifest_that_declares_it(tmp_path):
    ws = _write(tmp_path / "ws", PY_PATHS)
    hits = goal_facts.manifest_hits(DEP_GOAL, str(ws))
    assert [(h.token, h.path) for h in hits] == [("text-unidecode", "pyproject.toml")]  # `_` and `-` spellings are one token
    assert hits[0].line == 11 and hits[0].text.startswith("text-unidecode = ")
    assert goal_facts.delegating_shims(str(ws)) == {"setup.py": "pyproject.toml"}
    assert goal_facts.delegating_shim_targets(DEP_GOAL, str(ws)) == {"setup.py": "pyproject.toml"}
    block = goal_facts.render_goal_facts(goal_facts.build_goal_facts(DEP_GOAL, str(ws)))
    assert "DERIVED_DETERMINISTIC" in block and '"text-unidecode" occurs in pyproject.toml:11' in block
    assert "setup.py is a delegating shim" in block and "declared in pyproject.toml" in block
    assert "They are not instructions about what to change." in block and len(block) <= goal_facts.MAX_BLOCK_CHARS + 1


def test_t4_shim_and_manifest_negative_controls(tmp_path):
    """Every way the facts must NOT appear: a setup.py that declares things, a pyproject without a static [project] table, a
    goal whose names the delegate does not contain, no names at all, a non-whole-token match, an oversized manifest."""
    declared = _write(tmp_path / "a", {**PY_PATHS, "setup.py": 'from setuptools import setup\nsetup(name="x", install_requires=["y"])\n'})
    assert goal_facts.delegating_shims(str(declared)) == {}
    assert goal_facts.is_bare_setup_shim("from setuptools import setup\nsetup(**{})\n") is False
    assert goal_facts.is_bare_setup_shim("import os\nsetup()\nsetup()\n") is False  # two calls
    assert goal_facts.is_bare_setup_shim("def setup():\n    pass\n") is False and goal_facts.is_bare_setup_shim("syntax error (") is False
    assert goal_facts.is_bare_setup_shim(SHIM) is True
    no_table = _write(tmp_path / "b", {**PY_PATHS, "pyproject.toml": "[build-system]\nrequires = []\n"})
    assert goal_facts.delegating_shims(str(no_table)) == {}
    ws = _write(tmp_path / "c", PY_PATHS)
    other_goal = "Rename the helper in demo_lib so that it reads better.\n"
    assert goal_facts.manifest_hits(other_goal, str(ws)) == []  # `demo_lib` is only the project's own `name = "demo-lib"` line
    assert goal_facts.delegating_shim_targets(other_goal, str(ws)) == {}  # a shim exists but the goal gives no reason to suspect it
    assert goal_facts.render_goal_facts(goal_facts.build_goal_facts(other_goal, str(ws))) == ""
    assert goal_facts.manifest_hits("The text-unidecoder module", str(ws)) == []  # whole-token only
    # a longer package name that merely contains the token is a different package
    other_pkg = _write(tmp_path / "f", {**PY_PATHS, "pyproject.toml": PYPROJECT.replace(
        'text-unidecode = ["text-unidecode>=1.3"]', 'flask-text-unidecode-plugin = ["flask-text-unidecode-plugin>=1"]')})
    assert goal_facts.manifest_hits(DEP_GOAL, str(other_pkg)) == []
    # `_` and `-` are one spelling in BOTH directions: an underscore-only goal finds the hyphenated declaration and back
    assert [h.line for h in goal_facts.manifest_hits("No module named 'text_unidecode'.", str(ws))] == [11]
    underscored = _write(tmp_path / "g", {**PY_PATHS, "pyproject.toml": PYPROJECT.replace(
        'text-unidecode = ["text-unidecode>=1.3"]', 'text_unidecode = ["text_unidecode>=1.3"]')})
    assert [h.line for h in goal_facts.manifest_hits("Install text-unidecode properly.", str(underscored))] == [11]
    noisy = _write(tmp_path / "e", {**PY_PATHS, "pyproject.toml": PYPROJECT + '# text-unidecode is optional\nIssues = "https://example.org/demo-lib/issues"\n'})
    assert [h.line for h in goal_facts.manifest_hits(DEP_GOAL + "See demo_lib issues.\n", str(noisy))] == [11]  # comment, link and name lines are not declarations
    big = _write(tmp_path / "d", {**PY_PATHS, "pyproject.toml": PYPROJECT + "# text-unidecode\n" + "x" * (300 * 1024)})
    assert goal_facts.manifest_hits(DEP_GOAL, str(big)) == []  # oversized manifest is not read


def test_t4_validate_plan_refuses_a_delegating_shim_target_with_the_exact_delegate(tmp_path):
    ws = _write(tmp_path / "ws", PY_PATHS)
    shims = goal_facts.delegating_shim_targets(DEP_GOAL, str(ws))

    def check(plan, mapping=shims):
        return asyncio.run(validate_plan(plan, workspace_path=str(ws), delegating_shims=mapping))

    wrong = check(_plan(("s1", ["setup.py"])))
    assert wrong.valid is False and "PLAN_TARGETS_DELEGATING_SHIM" in wrong.reason_codes
    assert "'pyproject.toml'" in "".join(wrong.errors) and "s1:setup.py" in "".join(wrong.errors)
    from kriya.workflow.planner_repair import PLANNER_POLICY_REJECTION_CODES, build_structured_plan_repair_prompt

    assert "PLAN_TARGETS_DELEGATING_SHIM" in PLANNER_POLICY_REJECTION_CODES
    assert "delegates" in build_structured_plan_repair_prompt("g", "{}", ["e"], ["PLAN_TARGETS_DELEGATING_SHIM"], 1)
    assert check(_plan(("s1", ["pyproject.toml"]))).valid is True  # the delegate itself
    assert check(_plan(("s1", ["setup.py"]), ("s2", ["pyproject.toml"]))).valid is True  # the delegate is planned too
    assert check(_plan(("s1", ["setup.py"])), mapping={}).valid is True  # no constraint -> the pre-existing behaviour
    assert check(_plan(("s1", ["demo_lib/__init__.py"]))).valid is True  # unrelated file


def test_t2_member_facts_name_the_declaring_supertype_and_stay_silent_otherwise(tmp_path):
    _root, service = _service(tmp_path)
    try:
        lines = goal_facts.member_facts(service, MEMBER_GOAL)
        assert len(lines) == 1
        assert lines[0].startswith("Element.absUrl: lib.nodes.Element (src/main/java/lib/nodes/Element.java) does not declare absUrl")
        assert "lib.nodes.Node.absUrl at src/main/java/lib/nodes/Node.java:" in lines[0]
        # declared by the owner itself, unknown owner, lower-case receiver: no fact
        assert goal_facts.member_facts(service, "Element.text() is wrong; doc.selectFirst(\"a\").absUrl(\"href\") too.") == []
        assert goal_facts.member_facts(service, "Nowhere.absUrl() is wrong.") == []
    finally:
        service.close()


def test_the_block_is_built_from_the_index_and_never_raises_into_planning(tmp_path):
    root, service = _service(tmp_path)
    service.close()
    db = str(tmp_path / "memory" / "dependency_graph.db")
    block = goal_facts.goal_facts_block(db, str(root), MEMBER_GOAL)
    assert "=== GOAL-NAMED FACTS" in block and "lib.nodes.Node.absUrl at" in block
    assert goal_facts.goal_facts_block(None, str(root), MEMBER_GOAL) == ""  # no index: no member facts, nothing else to say
    py = _write(tmp_path / "py", PY_PATHS)
    assert '"text-unidecode" occurs in pyproject.toml' in goal_facts.goal_facts_block(None, str(py), DEP_GOAL)  # manifest facts need no index
    with patch("kriya.workflow.graph_retrieval.open_code_intelligence", side_effect=RuntimeError("index unreadable")):
        assert goal_facts.goal_facts_block(db, str(py), DEP_GOAL) == ""  # evidence only: planning proceeds without it


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True, capture_output=True)


def _capture_enforce_planner(tmp_path, files, goal):
    """Runs the enforce controller's planning step against ``files`` and returns (planner requests, validate_plan mock)."""
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    repo = _write(tmp_path / "repo", files)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    embedder = StaticEmbedder([0.3, 0.4, 0.5])
    asyncio.run(RepositoryAnalyzer(str(repo)).index_repository(cfg, generate_conventions_skill=False, embedding_client=embedder))
    first = next(iter(files))
    plan = EngineeringPlan(plan_id="gf", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="change it", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=first, action=FileAction.MODIFY)])])
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT))
    we.kernel = SimpleNamespace(config=cfg)
    requests = []

    async def planner(request, **kwargs):
        requests.append(request)
        return "plan"

    async def fake_run(**kwargs):
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.planner.run = planner
    we.run_generation_workflow = fake_run
    validate = AsyncMock(return_value=PlanValidationResult(valid=True))
    with patch.object(wc, "create_git_worktree", lambda workspace: workspace), \
         patch("kriya.memory.embedding.configured_client", return_value=embedder), \
         patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=validate):
        asyncio.run(wc.WorkflowController(we).execute(goal, str(repo), migration_mode="enforce"))
    return requests, validate


@pytest.mark.parametrize("name", ["python_manifest", "java_inherited_member"])
def test_the_enforce_planner_request_carries_the_block_and_validation_gets_the_shim_mapping(tmp_path, name):
    if name == "python_manifest":
        requests, validate = _capture_enforce_planner(tmp_path, PY_PATHS, DEP_GOAL)
        assert '"text-unidecode" occurs in pyproject.toml:11' in requests[0] and "setup.py is a delegating shim" in requests[0]
        assert validate.call_args.kwargs["delegating_shims"] == {"setup.py": "pyproject.toml"}
    else:
        requests, validate = _capture_enforce_planner(tmp_path, JAVA_PATHS, MEMBER_GOAL)
        assert "=== GOAL-NAMED FACTS" in requests[0] and "lib.nodes.Node.absUrl at" in requests[0]
        assert "returns the result of Strings.resolve(...)" in requests[0]  # the delegate fact reaches the real Planner request
        assert validate.call_args.kwargs["delegating_shims"] == {}
    assert requests[0].startswith("Original product request:\n" + (DEP_GOAL if name == "python_manifest" else MEMBER_GOAL))
