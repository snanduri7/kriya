"""BASELINE-JDK-PARITY / OI-97: the PRE-mutation baseline executes under the SAME toolchain its recorded environment identity names (and the candidate
runs under), or no baseline comparison is made under a mislabelled identity.

Measured defect (2026-11-06 reconciliation, adversarial probe reproduced by the ledger): ``baseline_environment_identity`` resolved the goal-stated JDK
override (``resume_fingerprints.toolchain_fingerprint``) and recorded it, but the baseline validator (``development_loop.baseline_suite_run``, the
suite-preservation closer's fallback) was built WITHOUT ``java_home_override`` while the candidate validator set it: PRE ran under ``mvn``'s default JDK
(21) and POST under the override (17), both labelled the same environment (``SAME_ENV True``).
"""
import ast
from pathlib import Path

import pytest

from kriya.config import AppConfig
from kriya.workflow import baseline_policy, development_loop, requirement_closers, resume_fingerprints, toolchain


def _source(module):
    return Path(module.__file__).read_text(encoding="utf-8")


OVERRIDE = None   # set by the fixture: a real directory with a JDK ``release`` file (the validator proves the JDK version of JAVA_HOME)


@pytest.fixture
def jdk_mismatch(monkeypatch, scratch_dir):
    """A machine where java and mvn disagree and the goal names the java side: the override resolves to a fake JDK home."""
    global OVERRIDE
    home = scratch_dir("jdk17")
    (home / "release").write_text('JAVA_VERSION="17.0.2"\n')
    OVERRIDE = str(home)
    seen = []

    def fake(goal):
        seen.append(goal)
        return OVERRIDE if "java 17" in goal.lower() else None

    monkeypatch.setattr(toolchain, "_resolve_java_home_override", fake)
    return seen


def _maven(tmp_path):
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>\n")
    return str(tmp_path)


def test_the_baseline_validator_executes_under_the_override_the_identity_records(tmp_path, jdk_mismatch):
    ws = _maven(tmp_path)
    validator = baseline_policy.baseline_validator(ws, AppConfig().autonomy, goal="Build it with Java 17")
    assert validator.stack == "java" and validator.java_home_override == OVERRIDE


def test_the_same_resolution_feeds_the_identity_and_the_execution():
    """Structural: the identity (``toolchain_fingerprint``) and the baseline validator both go through ``resolve_run_java_home_override``; neither
    calls the goal-parsing resolver directly any more (so they cannot drift apart)."""
    identity = ast.unparse(next(n for n in ast.walk(ast.parse(_source(resume_fingerprints))) if isinstance(n, ast.FunctionDef) and n.name == "toolchain_fingerprint"))
    execution = ast.unparse(next(n for n in ast.walk(ast.parse(_source(baseline_policy))) if isinstance(n, ast.FunctionDef) and n.name == "baseline_validator"))
    assert "resolve_run_java_home_override" in identity and "_resolve_java_home_override(" not in identity
    assert "resolve_run_java_home_override" in execution and "_resolve_java_home_override(" not in execution


@pytest.mark.parametrize("goal", [None, "", "Add a feature", "Use Java 11"])
def test_no_override_without_a_goal_naming_the_java_side(tmp_path, jdk_mismatch, goal):
    ws = _maven(tmp_path)
    assert baseline_policy.baseline_validator(ws, AppConfig().autonomy, goal=goal).java_home_override is None


def test_a_non_java_stack_never_gets_an_override(tmp_path, jdk_mismatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    validator = baseline_policy.baseline_validator(str(tmp_path), AppConfig().autonomy, goal="Java 17")
    assert validator.stack != "java" and validator.java_home_override is None


def test_the_baseline_and_the_candidate_validators_agree_on_the_override(tmp_path, jdk_mismatch):
    """PRE (baseline) and POST (candidate) execute under one toolchain: the candidate's override is ``resolve``d the same way."""
    ws = _maven(tmp_path)
    goal = "Java 17 service"
    pre = baseline_policy.baseline_validator(ws, AppConfig().autonomy, goal=goal)
    assert pre.java_home_override == toolchain.resolve_run_java_home_override("java", goal) == OVERRIDE


def test_neither_baseline_runner_builds_a_validator_without_the_override():
    """Structural pin: the run-start baseline runner goes through baseline_validator; the closer's fallback sets the candidate's override."""
    dev = ast.parse(_source(development_loop))
    fn = next(n for n in ast.walk(dev) if isinstance(n, ast.FunctionDef) and n.name == "baseline_suite_run")
    assert "baseline_validator" in ast.unparse(fn) and "PolymorphicValidator" not in ast.unparse(fn)
    closers = ast.parse(_source(requirement_closers))
    fn = next(n for n in ast.walk(closers) if isinstance(n, ast.FunctionDef) and n.name == "untouched_workspace_run")
    assert "java_home_override = java_home_override" in ast.unparse(fn)


def test_mutation_the_old_unparameterised_baseline_validator_disagrees_with_its_identity(tmp_path, jdk_mismatch):
    from kriya.tools.validate import PolymorphicValidator

    ws = _maven(tmp_path)
    old = PolymorphicValidator(ws, original_workspace_path=ws, autonomy_cfg=AppConfig().autonomy)   # the pre-fix construction
    assert old.java_home_override is None
    assert baseline_policy.baseline_validator(ws, AppConfig().autonomy, goal="Java 17").java_home_override != old.java_home_override
