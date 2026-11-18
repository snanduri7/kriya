"""TOOLCHAIN-HOST-PROBE-AUTHORITY-001 (owner ruling 2026-11-12, items 6-34; Scenario-A run 7, finding R7-A).

MEASURED in run 20261112T150652-e9c5502b: the desktop app's child environment resolved the host ``java`` to JDK 21 and the host (Homebrew) ``mvn`` to
JDK 26; every gate of the run executed in ``maven:3.9-eclipse-temurin-17`` (observed runtime 17); and every Developer prompt carried
``Target JVM (resolved on this machine via 'mvn'/'java'): JDK 26.`` The host probe (``kriya.tools.validate.check_java_toolchain``) was a second,
independent answer to "which JDK is this" beside the canonical owner (``kriya.tools.toolchain_identity``), and it fed four workflow consumers: the
mismatch warning, the prompt fact, the goal-stated JAVA_HOME override (which moves the contained image) and a pom.xml flag transform.

These controls replay that shape (host java 21, host mvn 26, contained identity 17) and pin the contract:
  * the host probe is never consulted by the workflow (it raises here: any call fails the test);
  * the prompt fact is the contained identity once the project declares it, and nothing before that - never the host's JDK, never a default;
  * changing ONLY the host values changes no prompt fact, no toolchain selection and no pom transform;
  * changing the contained identity changes those consumers;
  * a compiler ``<release>`` alone is not a claim about the execution JVM;
  * ``kriya doctor`` still reports the host 21 / 26 split, labelled HOST (tests/test_doctor_command.py).
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import strict_config

from kriya.policy.filesystem import WriteScopeMode
from kriya.tools import toolchain_identity
from kriya.workflow import toolchain as workflow_toolchain
from kriya.workflow.state import GenerationState

REPO = Path(__file__).resolve().parents[1]
JAVA_GOAL = "Create a Java 17 Maven application that prints a result line."
SECURITY_MANAGER_FLAG = "-Djava.security.manager=allow"

HOST_STATES: Dict[str, Dict[str, Any]] = {
    "run7_java21_mvn26": {"java_found": True, "java_version": "21", "mvn_found": True, "mvn_java_version": "26", "mismatch": True},
    "goal_side_java17_mvn26": {"java_found": True, "java_version": "17", "mvn_found": True, "mvn_java_version": "26", "mismatch": True},
    "coherent_17": {"java_found": True, "java_version": "17", "mvn_found": True, "mvn_java_version": "17", "mismatch": False},
    "coherent_26": {"java_found": True, "java_version": "26", "mvn_found": True, "mvn_java_version": "26", "mismatch": False},
    "no_host_java": {"java_found": False, "java_version": None, "mvn_found": False, "mvn_java_version": None, "mismatch": False},
}


def _pom(version: Optional[int], *, flag: bool = False, plugin_release: Optional[int] = None) -> str:
    properties = f"  <properties>\n    <maven.compiler.release>{version}</maven.compiler.release>\n  </properties>\n" if version else ""
    compiler = (
        "      <plugin>\n        <artifactId>maven-compiler-plugin</artifactId>\n        <configuration>\n"
        f"          <release>{plugin_release}</release>\n        </configuration>\n      </plugin>\n"
    ) if plugin_release else ""
    arguments = f"            <argument>{SECURITY_MANAGER_FLAG}</argument>\n" if flag else ""
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <modelVersion>4.0.0</modelVersion>\n'
        "  <groupId>com.example</groupId>\n  <artifactId>app</artifactId>\n  <version>1.0</version>\n"
        f"{properties}  <build>\n    <plugins>\n{compiler}"
        "      <plugin>\n        <groupId>org.codehaus.mojo</groupId>\n        <artifactId>exec-maven-plugin</artifactId>\n"
        "        <configuration>\n          <executable>java</executable>\n          <arguments>\n"
        f"{arguments}            <argument>-classpath</argument>\n          </arguments>\n        </configuration>\n      </plugin>\n"
        "    </plugins>\n  </build>\n</project>\n"
    )


def _project(root: Path, pom: Optional[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if pom is not None:
        (root / "pom.xml").write_text(pom, encoding="utf-8")
    return root


class _HostProbe:
    """Stands in for the host probe. ``answer`` set: it reports that host state and counts the calls (what a consumer that still asked the host would
    read). The workflow must not ask at all, so every workflow control asserts ``calls == 0``."""

    def __init__(self, answer: Dict[str, Any]) -> None:
        self.answer = answer
        self.calls = 0

    def __call__(self) -> Dict[str, Any]:
        self.calls += 1
        return dict(self.answer)


@pytest.fixture(params=sorted(HOST_STATES), ids=sorted(HOST_STATES))
def host(request, monkeypatch):
    """Every control below runs under each scripted host state; a JDK-home lookup on the host is refused outright."""
    probe = _HostProbe(HOST_STATES[request.param])
    monkeypatch.setattr("kriya.tools.validate.check_java_toolchain", probe)
    monkeypatch.setattr("kriya.platform.toolchain_locator.jdk_home_for_version",
                        lambda version: f"/host/jdk-{version}")   # what the removed host override would have returned
    return probe


# ----------------------------------------------------------------------------- the prompt fact

def test_run7_replay_the_prompt_fact_is_the_contained_jdk_17_never_the_hosts_26(tmp_path, host):
    """Items 26-28. Pre-fix on the run-7 host state this returned ``... JDK 26.``"""
    project = _project(tmp_path / "ws", _pom(17))
    fact = workflow_toolchain._java_toolchain_fact(JAVA_GOAL, str(project))
    assert fact == ("Target JVM (the contained JDK Kriya's compile, test and runtime gates run this project under, selected from pom.xml): "
                    "JDK 17.")
    assert "26" not in fact and "21" not in fact
    assert host.calls == 0


def test_before_the_project_declares_a_version_no_execution_jvm_is_asserted(tmp_path, host):
    """Item 18: not yet resolved -> nothing. Neither the host's JDK nor the selection default (21) is put in the prompt."""
    assert workflow_toolchain._java_toolchain_fact(JAVA_GOAL, str(_project(tmp_path / "empty", None))) is None
    assert workflow_toolchain._java_toolchain_fact(JAVA_GOAL, str(_project(tmp_path / "undeclared", _pom(None)))) is None
    assert host.calls == 0


def test_a_compiler_release_alone_is_not_a_claim_about_the_execution_jvm(tmp_path, host):
    """Item 31. ``<release>17</release>`` in the compiler plugin is a language level. The canonical owner does not select a toolchain from it, so
    nothing is claimed; the claim follows the owner's identity, whatever the pom text says."""
    release_only = _project(tmp_path / "release_only", _pom(None, plugin_release=17))
    assert toolchain_identity.declared_java_execution_identity(str(release_only)) is None
    assert workflow_toolchain._java_toolchain_fact(JAVA_GOAL, str(release_only)) is None
    declared = _project(tmp_path / "declared", _pom(17))
    other = toolchain_identity._java_identity(21, "the canonical owner", "maven", "3.9")
    with patch.object(toolchain_identity, "declared_java_execution_identity", return_value=other):
        fact = workflow_toolchain._java_toolchain_fact(JAVA_GOAL, str(declared))
    assert fact is not None and fact.endswith("selected from the canonical owner): JDK 21.") and "17" not in fact
    assert host.calls == 0


@pytest.mark.parametrize("declared", [8, 11, 17, 21])
def test_the_prompt_fact_follows_the_contained_identity(tmp_path, host, declared):
    """Item 30: the authoritative identity changes -> the fact changes."""
    fact = workflow_toolchain._java_toolchain_fact(JAVA_GOAL, str(_project(tmp_path / "ws", _pom(declared))))
    assert fact is not None and fact.endswith(f": JDK {declared}.")
    assert host.calls == 0


def test_an_unresolvable_declaration_is_not_reported_through_the_prompt(tmp_path, host):
    """Conflicting declarations are the gates' typed refusal (ToolchainResolutionError); the prompt line states nothing rather than one side."""
    conflicting = _pom(17).replace("</properties>", "  <java.version>21</java.version>\n  </properties>")
    project = _project(tmp_path / "ws", conflicting)
    with pytest.raises(toolchain_identity.ToolchainResolutionError):
        toolchain_identity.declared_java_execution_identity(str(project))
    assert workflow_toolchain._java_toolchain_fact(JAVA_GOAL, str(project)) is None
    assert host.calls == 0


def test_a_goal_that_is_not_about_java_gets_no_fact_even_in_a_declaring_tree(tmp_path, host):
    plain = _project(tmp_path / "plain", None)
    assert workflow_toolchain._java_toolchain_fact("Add a CLI flag to the Python tool.", str(plain)) is None
    assert host.calls == 0


@pytest.mark.asyncio
async def test_run7_replay_the_planner_prompt_of_a_real_generation_run_says_17_and_never_26(tmp_path, host):
    """The same replay through ``run_generation_workflow``: the fact that reaches the model."""
    from _chaos_harness import git_workspace

    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    project = git_workspace(tmp_path, {"pom.xml": _pom(17), ".gitignore": "target/\n"})
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write App.java", '[{"filepath": "App.java", "content": "class App {}"}]', "Review: Approved"])
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}), \
            patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}):
        result = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(goal="Create a Java app", workspace_path=str(project))
    planner_prompt = llm.complete.call_args_list[0].args[1]
    assert "=== Environment Fact ===" in planner_prompt
    assert "selected from pom.xml): JDK 17." in planner_prompt
    assert "JDK 26" not in planner_prompt and "JDK 21" not in planner_prompt
    assert result["toolchain_warning"] is None        # no host java/mvn mismatch warning on a contained run
    assert host.calls == 0


# ----------------------------------------------------------------------------- the goal-stated override and the image selection

def test_the_host_never_selects_the_contained_toolchain(tmp_path, host):
    """Items 25 and 29. Pre-fix, on the ``goal_side_java17_mvn26`` host the goal "Java 17" resolved the host's JDK-17 home, and under containment
    that moved an undeclared project from the default image to the JDK 17 image: the same goal and tree, another image, by host setup alone."""
    assert workflow_toolchain._resolve_java_home_override(JAVA_GOAL) is None
    assert workflow_toolchain.resolve_run_java_home_override("java", JAVA_GOAL) is None
    undeclared = _project(tmp_path / "undeclared", _pom(None))
    declared = _project(tmp_path / "declared", _pom(17))
    override = workflow_toolchain.resolve_run_java_home_override("java", JAVA_GOAL)
    selected = {
        name: toolchain_identity.resolve_toolchain_selection(str(tree), "java", java_home_override=override, declaration_mutable=True)
        for name, tree in (("undeclared", undeclared), ("declared", declared))
    }
    assert (selected["undeclared"].containment_image, selected["undeclared"].selection["basis"]) == (
        "maven:3.9-eclipse-temurin-21", "repository_declaration")
    assert (selected["declared"].containment_image, selected["declared"].runtime_version) == ("maven:3.9-eclipse-temurin-17", "17")
    assert host.calls == 0


def test_the_resume_identity_does_not_depend_on_the_host(tmp_path, host):
    """The environment identity a resume compares (toolchain_fingerprint) is computed through the same override."""
    from kriya.workflow import resume_fingerprints

    project = _project(tmp_path / "ws", _pom(17))
    first = resume_fingerprints.toolchain_fingerprint(str(project), strict_config().autonomy, goal=JAVA_GOAL)
    assert "maven:3.9-eclipse-temurin-17" in f"{first.value} {first.basis}"   # the contained image, on every host (the image itself is absent under the test seam)
    assert str(first) == _FINGERPRINTS.setdefault("value", str(first))   # identical under every host state of the fixture
    assert host.calls == 0


_FINGERPRINTS: Dict[str, Any] = {}


def test_the_canonical_seam_for_a_goal_stated_jdk_is_kept(tmp_path, monkeypatch):
    """The seam is unchanged (registered gap GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001: nothing host-independent feeds it yet): a value
    returned by ``_resolve_java_home_override`` still reaches the canonical owner and its conflict rules."""
    home = tmp_path / "jdk-17"
    home.mkdir()
    (home / "release").write_text('JAVA_VERSION="17.0.10"\n', encoding="utf-8")
    monkeypatch.setattr(workflow_toolchain, "_resolve_java_home_override", lambda goal: str(home))
    override = workflow_toolchain.resolve_run_java_home_override("java", JAVA_GOAL)
    assert override == str(home)
    undeclared = _project(tmp_path / "ws", _pom(None))
    identity = toolchain_identity.resolve_toolchain_selection(str(undeclared), "java", java_home_override=override)
    assert (identity.runtime_version, identity.selection["basis"]) == ("17", "goal_requirement_undeclared")
    declared_21 = _project(tmp_path / "ws21", _pom(21))
    with pytest.raises(toolchain_identity.ToolchainRequirementConflictError):
        toolchain_identity.resolve_toolchain_selection(str(declared_21), "java", java_home_override=override, declaration_mutable=False)


# ----------------------------------------------------------------------------- the pom.xml flag transform

def _corrections_ctx(worktree: Path, tmp_path: Path) -> SimpleNamespace:
    from _strict_doubles import for_project

    cfg = for_project(strict_config(paths={"skills": str(tmp_path / "skills"), "memory": str(tmp_path / "memory")}), worktree)  # C-05 (S3)
    return SimpleNamespace(
        worktree_path=str(worktree), workspace_path=str(worktree), protected_relpath=None, allowed_write_relpaths=(),
        write_scope_mode=WriteScopeMode.UNRESTRICTED, established_files=[], structured_plan=None, migration_resolution=None,
        kernel=SimpleNamespace(config=cfg), project_authority_relpaths=(),  # C-04 (S4): the write gate reads the run's project-authority paths
    )


def _apply_corrections(worktree: Path, tmp_path: Path) -> GenerationState:
    from kriya.workflow.attempt import _apply_candidate_pom_corrections

    state = GenerationState()
    state.all_files_written.add("pom.xml")
    _apply_candidate_pom_corrections(state, _corrections_ctx(worktree, tmp_path), [])
    return state


def test_run7_shape_the_hosts_jdk_26_does_not_strip_a_flag_from_a_jdk_17_candidate(tmp_path, host):
    """Items 24, 25, 29. The candidate declares 17 and runs in the JDK 17 image, where the flag is valid. Pre-fix on the run-7 host the transform
    asked the host's ``mvn`` (JDK 26 >= 24) and removed the flag from the candidate's pom."""
    worktree = _project(tmp_path / "wt", _pom(17, flag=True))
    before = (worktree / "pom.xml").read_bytes()
    state = _apply_corrections(worktree, tmp_path)
    assert (worktree / "pom.xml").read_bytes() == before
    assert state.toolchain_warning is None
    assert [event for event in state.run_events if event.kind == "candidate.deterministic_transformation"] == []
    assert host.calls == 0


def test_the_flag_transform_follows_the_contained_identity(tmp_path, host, monkeypatch):
    """Item 30. A contained JDK on which the flag is fatal (a JDK >= 24 image, once one is supported) strips it - decided by the candidate
    validator's toolchain identity, on every host."""
    monkeypatch.setattr(toolchain_identity, "_SUPPORTED_JAVA", (25, 21, 17, 11, 8))
    worktree = _project(tmp_path / "wt", _pom(25, flag=True))
    state = _apply_corrections(worktree, tmp_path)
    after = (worktree / "pom.xml").read_text(encoding="utf-8")
    assert SECURITY_MANAGER_FLAG not in after and "<argument>-classpath</argument>" in after
    assert "contained execution JDK 25" in state.toolchain_warning
    [event] = [event for event in state.run_events if event.kind == "candidate.deterministic_transformation"]
    assert event.details["corrections"] == ["jdk_incompatible_jvm_flags"]
    assert host.calls == 0


@pytest.mark.parametrize("major,stripped", [(None, False), (8, False), (17, False), (21, False), (23, False), (24, True), (26, True)])
def test_the_flag_rule_is_a_pure_function_of_the_contained_major(major, stripped):
    content = _pom(17, flag=True)
    transformed, note = workflow_toolchain._strip_jdk_incompatible_jvm_flags(content, major)
    assert (transformed is not None) is stripped and (note is not None) is stripped
    if stripped:
        assert SECURITY_MANAGER_FLAG not in transformed and f"contained execution JDK {major}" in note


def test_a_pom_without_a_conditional_flag_resolves_no_identity_at_all(tmp_path, host):
    """The identity is resolved only when the pom names such a flag: an ordinary pom never builds an extra validator."""
    worktree = _project(tmp_path / "wt", _pom(17))
    with patch("kriya.workflow.gate_orchestrator._contained_jdk_major", side_effect=AssertionError("not needed")):
        _apply_corrections(worktree, tmp_path)
    assert host.calls == 0


def test_no_host_executable_path_is_written_into_a_candidate_pom(tmp_path, host, monkeypatch):
    """The removed 'pin <executable> to the host JDK' transform wrote a host path into the delivered pom; a contained run cannot execute it."""
    home = tmp_path / "jdk-17"
    home.mkdir()
    (home / "release").write_text('JAVA_VERSION="17.0.10"\n', encoding="utf-8")
    from kriya.workflow.attempt import _apply_candidate_pom_corrections

    worktree = _project(tmp_path / "wt", _pom(17))
    before = (worktree / "pom.xml").read_bytes()
    state = GenerationState()
    state.all_files_written.add("pom.xml")
    state.java_home_override = str(home)
    _apply_candidate_pom_corrections(state, _corrections_ctx(worktree, tmp_path), [])
    assert (worktree / "pom.xml").read_bytes() == before and b"<executable>java</executable>" in before


# ----------------------------------------------------------------------------- the candidate preflight

def test_the_candidate_preflight_asks_the_host_nothing_and_warns_about_nothing(host):
    """Item 12/14: no host mismatch warning in a workflow result or log; the one-time step only resolves the goal-stated JDK."""
    from kriya.workflow import attempt

    state = GenerationState()
    validator = SimpleNamespace(stack="java", java_home_override="unset")
    ctx = SimpleNamespace(goal=JAVA_GOAL)
    attempt._resolve_candidate_toolchain(state, ctx, validator)
    assert state.toolchain_checked is True and state.toolchain_warning is None and state.java_home_override is None
    assert validator.java_home_override is None
    assert host.calls == 0


# ----------------------------------------------------------------------------- structure: who may call the host probe

HOST_PROBE_CALLERS = {"kriya/cli.py"}      # ``kriya doctor``
HOST_PROBE_OWNER = "kriya/tools/validate.py"


def _modules_naming(name: str) -> List[str]:
    found = []
    for path in sorted((REPO / "kriya").rglob("*.py")) + sorted((REPO / "plugins").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id == name) or (isinstance(node, ast.Attribute) and node.attr == name) \
                    or (isinstance(node, ast.alias) and node.name == name):
                found.append(path.relative_to(REPO).as_posix())
                break
    return found


def test_only_doctor_calls_the_host_probe():
    """One mutant per former consumer (item 33) restores a call of the host probe in a workflow module; each is refused here by name, and by the
    behavioural controls above."""
    assert set(_modules_naming("check_java_toolchain")) == HOST_PROBE_CALLERS      # references; its definition is in HOST_PROBE_OWNER
    assert "def check_java_toolchain(" in (REPO / HOST_PROBE_OWNER).read_text(encoding="utf-8")
    assert _modules_naming("jdk_home_for_version") == []     # the host JDK-home locator has no caller left (it stays defined in kriya/platform)


def test_the_workflow_has_no_host_mismatch_check_and_no_host_pin():
    for removed in ("_check_java_toolchain_mismatch", "_pin_exec_plugin_executable_to_resolved_jdk"):
        assert _modules_naming(removed) == [], removed
