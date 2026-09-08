"""Candidate-independent deterministic failure detection (PRV-17, 2026-09-08,
P7 efficiency finding) - see kriya/workflow/deterministic_failure_diagnostic.py's
own module docstring for the full incident. Two layers of tests:

1. Pure orchestration logic (evaluate_candidate_independent_failure), with
   replay_deterministic_verification_against_baseline mocked - the trigger
   gating and caching decisions, independent of any real subprocess.
2. A real, end-to-end baseline replay against an actual (broken) single-
   module Maven project - proves isolation (the authoritative workspace is
   never mutated) and that the real check genuinely reproduces the exact
   "zero .class files" failure this mechanism exists to detect.
3. Wiring-level tests calling handle_attempt_failure() directly (the same
   fixture style tests/test_workflow.py already uses for that function),
   proving the retry_strategy.py integration point actually sets
   state.environment_failure/stops the loop, without touching
   record_workspace_progress's own behavior at all.
"""
import hashlib
import os
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.attempt import AttemptContext
from kriya.workflow.deterministic_failure_diagnostic import (
    DeterministicFailureCorrectability,
    DeterministicFailureDiagnosticStore,
    _copy_baseline_workspace,
    evaluate_candidate_independent_failure,
    replay_deterministic_verification_against_baseline,
)
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

_SIG_A = ("compile", ("locations", "digest", "aaaa"))
_SIG_B = ("compile", ("locations", "digest", "bbbb"))


def _call(store, **overrides):
    kwargs = dict(
        store=store,
        fail_type="compile",
        current_failure_signature=_SIG_A,
        # Matches whatever the mocked replay's own "output" is set to in
        # each test below (real callers pass retry_strategy.py's own
        # raw_error_context, which typically WRAPS the validator's raw
        # output - see evaluate_candidate_independent_failure's own
        # docstring for why substring containment, not signature equality,
        # is the comparison basis).
        current_error_text="COMPILATION FAILURE:\nsame failure text",
        previous_failure_signature=_SIG_A,
        workspace_changed=True,
        has_implicated_files=False,
        authoritative_workspace_path="/nonexistent",
        known_files=["Foo.java"],
        autonomy_cfg=None,
        subtask_id="s1",
    )
    kwargs.update(overrides)
    return evaluate_candidate_independent_failure(**kwargs)


# --- Category 1/2/3/4/5/6/7: pure orchestration logic ----------------------

def test_baseline_reproduces_failure_classifies_non_candidate_correctable():
    """Test 1 (exact P7 shape, isolated to the orchestration decision): same
    unattributed deterministic failure recurs after a materially different
    candidate, baseline replay reproduces it -> NON_CANDIDATE_CORRECTABLE,
    cached for this exact signature."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        return_value={"success": False, "output": "same failure text"},
    ):
        rec = _call(store)
    assert rec is not None
    assert rec.correctability == DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE
    assert store.get("s1", _SIG_A) is rec


def test_baseline_succeeds_classifies_candidate_correctable_and_retry_continues():
    """Test 2: same compiler error on two candidates, but the baseline
    (pre-candidate state) compiles fine - the candidate remains a plausible
    cause, never terminated."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        return_value={"success": True, "output": "compiled fine"},
    ) as mock_replay:
        rec = _call(store)
    mock_replay.assert_called_once()
    assert rec.correctability == DeterministicFailureCorrectability.CANDIDATE_CORRECTABLE


def test_different_failure_signatures_produce_no_candidate_independent_conclusion():
    """Test 3: the failure changed between attempts - never a candidate-
    independent conclusion, and no replay is even attempted."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay:
        rec = _call(store, previous_failure_signature=_SIG_B)
    assert rec is None
    mock_replay.assert_not_called()


def test_implicated_files_present_never_triggers_replay():
    """Test 4: a strong implicated candidate file means the existing repair
    machinery has a credible target - this mechanism must never evaluate,
    let alone terminate, that case."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay:
        rec = _call(store, has_implicated_files=True)
    assert rec is None
    mock_replay.assert_not_called()


def test_byte_identical_workspace_never_triggers_replay():
    """A byte-identical repeat is record_workspace_progress's own territory
    (its no-progress counter already exists for exactly this case) - this
    mechanism is deliberately scoped to the gap that leaves open (different
    content, identical deterministic outcome), so it must not fire here."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay:
        rec = _call(store, workspace_changed=False)
    assert rec is None
    mock_replay.assert_not_called()


def test_broken_baseline_is_classified_as_a_blocker():
    """Test 5: framed from the 'broken baseline' angle rather than 'exact P7
    reproduction' - same code path, same conclusion: a baseline that itself
    reproduces the deterministic failure is a real, classified blocker."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        return_value={"success": False, "output": "broken baseline output"},
    ):
        rec = _call(store, current_error_text="COMPILATION FAILURE:\nbroken baseline output")
    assert rec.correctability == DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE
    assert rec.baseline_output == "broken baseline output"


def test_retry_family_switch_does_not_erase_cached_evidence():
    """Test 6: evaluate_candidate_independent_failure has no `action`/mode
    parameter at all - a retry-family switch (full-set -> targeted) cannot
    possibly erase a cached conclusion, unlike record_workspace_progress's
    own action_changed reset."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        return_value={"success": False, "output": "same failure text"},
    ):
        first = _call(store)
    # A later call for the exact same signature, simulating a different
    # retry-family attempt - no mode/action is ever passed to this function,
    # so there is nothing for a family switch to erase.
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay_again:
        second = _call(store)
    assert second is first
    mock_replay_again.assert_not_called()


def test_plan_scope_recovery_reset_does_not_erase_proven_diagnostic():
    """Test 7: the store is a plain object owned by the caller (workflow_
    controller.py's _run_structured_enforce), not by GenerationState - a
    plan-scope-recovery reset creates a brand new GenerationState/
    AttemptContext for the SAME subtask, but the SAME store instance is
    threaded through both invocations, so the conclusion survives."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        return_value={"success": False, "output": "same failure text"},
    ):
        _call(store)  # round 1, pre-reset

    # round 2: a fresh GenerationState/AttemptContext would exist in
    # production, but the SAME store instance is reused - simulated here by
    # simply calling again with the same store and no fresh mock state.
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay_round2:
        rec_round2 = _call(store)
    assert rec_round2.correctability == DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE
    mock_replay_round2.assert_not_called()


def test_different_subtasks_do_not_share_a_cached_conclusion():
    """Edge case flagged for review: the store key is (subtask_id,
    failure_signature), not failure_signature alone - the byte-identical
    Kriya-synthesized message colliding for two UNRELATED subtasks must not
    let one subtask's proven baseline-replay conclusion silently apply to
    the other's genuinely different validation context. A fresh subtask
    with no cached record must still trigger its OWN replay."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        return_value={"success": False, "output": "same failure text"},
    ):
        rec_s1 = _call(store, subtask_id="s1")
    assert rec_s1.correctability == DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE

    # s2 shares the exact same failure_signature by coincidence but has
    # never been evaluated - must trigger its own fresh replay, not inherit
    # s1's conclusion.
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        return_value={"success": True, "output": "compiles fine for s2's own baseline"},
    ) as mock_replay_s2:
        rec_s2 = _call(store, subtask_id="s2")
    mock_replay_s2.assert_called_once()
    assert rec_s2.correctability == DeterministicFailureCorrectability.CANDIDATE_CORRECTABLE
    # Both conclusions coexist independently in the same store.
    assert store.get("s1", _SIG_A) is rec_s1
    assert store.get("s2", _SIG_A) is rec_s2
    assert store.get("s1", _SIG_A) is not store.get("s2", _SIG_A)


def test_replayable_fail_types_are_bounded_to_compile():
    """Test 9 (partial, orchestration side): a non-deterministic/model-
    review fail_type (goal_spec_compliance) must never trigger this
    mechanism, regardless of how the other three gates would otherwise
    evaluate."""
    store = DeterministicFailureDiagnosticStore()
    with patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay:
        rec = _call(store, fail_type="goal_spec_compliance")
    assert rec is None
    mock_replay.assert_not_called()


def test_replay_rejects_unbounded_fail_type_defensively():
    with pytest.raises(ValueError):
        replay_deterministic_verification_against_baseline(
            fail_type="run_verification",
            authoritative_workspace_path="/nonexistent",
            known_files=[],
            autonomy_cfg=None,
        )


# --- Category 2: real, end-to-end baseline replay + isolation --------------

_SINGLE_MODULE_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>broken-source-dir</artifactId>
  <version>1.0</version>
  <packaging>jar</packaging>
</project>
"""

_JAVA_SOURCE = """package com.example;
public class Foo {
    public static void main(String[] args) {
        System.out.println("hello");
    }
}
"""


def _dir_fingerprint(path):
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "target"))
        for name in sorted(files):
            full = os.path.join(root, name)
            digest.update(os.path.relpath(full, path).encode())
            with open(full, "rb") as fh:
                digest.update(fh.read())
    return digest.hexdigest()


def _mock_mvn_success():
    mock_process = MagicMock(returncode=0)
    mock_process.communicate.return_value = ("BUILD SUCCESS", "")
    return mock_process


def test_baseline_replay_reproduces_failure_and_is_isolated(tmp_path):
    """Test 8: baseline replay runs against an ISOLATED COPY, never the
    authoritative workspace itself. Maven itself is mocked (matching this
    suite's own established convention in test_polymorphic_validation.py's
    _mock_mvn_success/_mock_mvn_failure - never requires a real toolchain to
    run by default) reporting a clean returncode=0 build with no real
    compilation happening, so the TEMP COPY's own target/classes genuinely
    stays empty - exercising the real, unmocked "zero .class files" branch
    of PolymorphicValidator.run_compile_check() against the isolated copy."""
    workspace = str(tmp_path)
    with open(os.path.join(workspace, "pom.xml"), "w") as fh:
        fh.write(_SINGLE_MODULE_POM)
    java_dir = os.path.join(workspace, "src", "main", "java", "com", "example")
    os.makedirs(java_dir)
    with open(os.path.join(java_dir, "Foo.java"), "w") as fh:
        fh.write(_JAVA_SOURCE)

    before_fingerprint = _dir_fingerprint(workspace)
    before_entries = set(os.listdir(workspace))

    with patch("subprocess.Popen", return_value=_mock_mvn_success()):
        result = replay_deterministic_verification_against_baseline(
            fail_type="compile",
            authoritative_workspace_path=workspace,
            known_files=["src/main/java/com/example/Foo.java"],
            autonomy_cfg=None,
        )

    assert result["success"] is False
    assert "zero .class files" in result["output"]
    assert _dir_fingerprint(workspace) == before_fingerprint
    assert set(os.listdir(workspace)) == before_entries


@pytest.mark.live_model
def test_real_baseline_replay_reproduces_failure_against_real_maven(tmp_path):
    """Same shape as the mocked test above, against a REAL `mvn` binary and
    a genuinely broken <sourceDirectory> - the exact live P7 defect,
    reproduced for real rather than via a mocked subprocess. Excluded by
    default (needs a real local Java/Maven toolchain, not an LLM - the same
    "needs a real local dependency" bar as this repo's live_model tier)."""
    broken_pom = _SINGLE_MODULE_POM.replace(
        "</project>", "  <build>\n    <sourceDirectory>src/nonstandard/java</sourceDirectory>\n  </build>\n</project>",
    )
    workspace = str(tmp_path)
    with open(os.path.join(workspace, "pom.xml"), "w") as fh:
        fh.write(broken_pom)
    java_dir = os.path.join(workspace, "src", "main", "java", "com", "example")
    os.makedirs(java_dir)
    with open(os.path.join(java_dir, "Foo.java"), "w") as fh:
        fh.write(_JAVA_SOURCE)

    before_fingerprint = _dir_fingerprint(workspace)
    before_entries = set(os.listdir(workspace))

    result = replay_deterministic_verification_against_baseline(
        fail_type="compile",
        authoritative_workspace_path=workspace,
        known_files=["src/main/java/com/example/Foo.java"],
        autonomy_cfg=None,
    )

    assert result["success"] is False
    assert "zero .class files" in result["output"]
    assert _dir_fingerprint(workspace) == before_fingerprint
    assert set(os.listdir(workspace)) == before_entries


def test_copy_baseline_workspace_excludes_control_and_build_dirs(tmp_path):
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (source / "target" / "classes").mkdir(parents=True)
    (source / "target" / "classes" / "Stale.class").write_bytes(b"stale")
    (source / "src").mkdir()
    (source / "src" / "Real.java").write_text("class Real {}\n")

    dest = tmp_path / "dest"
    dest.mkdir()
    _copy_baseline_workspace(str(source), str(dest))

    assert not (dest / ".git").exists()
    assert not (dest / "target").exists()
    assert (dest / "src" / "Real.java").read_text() == "class Real {}\n"


# --- Category 3: wiring through handle_attempt_failure ---------------------

def _minimal_ctx(tmp_path, **overrides) -> AttemptContext:
    defaults = dict(
        goal="goal", plan="plan", design="design",
        workspace_path=str(tmp_path), worktree_path=str(tmp_path),
        architect_files=["Foo.java"], resume_state=None, run_id="run",
        skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="",
        verification_contract_block="", recovery_contract_block="",
        required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=["Foo.java"], architect_basename_to_path={"Foo.java": "Foo.java"},
        chain=[], targeted_max_retries=3, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={},
        developer=AsyncMock(), run_verifier=AsyncMock(), spec_compliance=AsyncMock(),
        skill_engine=MagicMock(), kernel=Kernel(config=AppConfig()), max_retries=4,
        web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False),
        established_files=["Foo.java"],
    )
    defaults.update(overrides)
    return AttemptContext(**defaults)


def _compile_failure(message: str) -> QualityGateFailure:
    return QualityGateFailure(Failure(type="compile", message=message, raw_output=message))


@pytest.mark.asyncio
async def test_handle_attempt_failure_stops_the_loop_when_baseline_reproduces_failure(tmp_path):
    """End-to-end wiring proof: two attempts with the SAME normalized compile
    failure, a materially different candidate workspace between them (no
    special mocking needed - GenerationState starts with
    last_failed_workspace_hash=None, so the very first recorded hash always
    differs), no implicated files, and a baseline replay that reproduces the
    failure -> handle_attempt_failure must set state.environment_failure
    with the CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE prefix, without
    touching retry budgets."""
    store = DeterministicFailureDiagnosticStore()
    ctx = _minimal_ctx(tmp_path, deterministic_failure_diagnostics=store)
    state = GenerationState()
    state.last_attempt_mode = "full_set"
    message = "Maven reported compilation success, but zero .class files were produced."

    # compute_effective_workspace_hash hashes REAL files under
    # ctx.worktree_path - a materially different candidate means the
    # Developer actually wrote different content between attempts.
    foo_java = tmp_path / "Foo.java"
    foo_java.write_text("public class Foo { void a() {} }\n")
    exc1 = _compile_failure(message)

    with patch(
        "kriya.workflow.retry_strategy.classify_environment_failure", return_value=None,
    ):
        await handle_attempt_failure(state, ctx, exc1)
    assert not state.environment_failure  # first occurrence: nothing to compare against yet

    foo_java.write_text("public class Foo { void b() {} }\n")
    exc2 = _compile_failure(message)
    with patch(
        "kriya.workflow.retry_strategy.classify_environment_failure", return_value=None,
    ), patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
        # The baseline's raw "output" is a plain substring of exc2's own
        # message (this fixture uses the identical string for both, exactly
        # the real incident's own shape - a byte-identical Kriya-synthesized
        # message) - the substring-containment comparison in
        # evaluate_candidate_independent_failure holds trivially here.
        return_value={"success": False, "output": message},
    ):
        should_break = await handle_attempt_failure(state, ctx, exc2)

    assert state.environment_failure is not None
    assert state.environment_failure.startswith("CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE:")
    # should_break is False by this function's own existing contract (only
    # True for a hard toolchain/environment classification path) - the
    # retry loop itself stops via decide_for_state's own environment_failure
    # check on its next iteration, matching every other environment_failure
    # producer in this module (unrecoverable scope denial, etc.).


@pytest.mark.asyncio
async def test_handle_attempt_failure_does_not_stop_when_diagnostics_store_absent(tmp_path):
    """Backward compatibility (test 10's own spirit): an AttemptContext with
    no deterministic_failure_diagnostics store (every existing test in this
    suite, and every non-MA-structured caller) behaves exactly as before -
    this mechanism contributes nothing."""
    ctx = _minimal_ctx(tmp_path, deterministic_failure_diagnostics=None)
    state = GenerationState()
    state.last_attempt_mode = "full_set"
    message = "Maven reported compilation success, but zero .class files were produced."

    with patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None):
        await handle_attempt_failure(state, ctx, _compile_failure(message))
        await handle_attempt_failure(state, ctx, _compile_failure(message))

    assert not state.environment_failure


@pytest.mark.asyncio
async def test_handle_attempt_failure_never_triggers_for_non_deterministic_fail_type(tmp_path):
    """Test 9: a goal_spec_compliance (LLM judgment) failure repeating twice
    with a changed workspace and no implicated files must never engage this
    mechanism - goal_spec_compliance is not in the bounded replayable set."""
    store = DeterministicFailureDiagnosticStore()
    ctx = _minimal_ctx(tmp_path, deterministic_failure_diagnostics=store)
    state = GenerationState()
    state.last_attempt_mode = "full_set"

    def judgment_failure() -> QualityGateFailure:
        return QualityGateFailure(Failure(
            type="goal_spec_compliance", message="spec says no", raw_output="spec says no",
        ))

    with patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None), patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay:
        await handle_attempt_failure(state, ctx, judgment_failure())
        await handle_attempt_failure(state, ctx, judgment_failure())

    mock_replay.assert_not_called()
    assert not state.environment_failure


@pytest.mark.asyncio
async def test_handle_attempt_failure_with_implicated_file_never_terminates(tmp_path):
    """Test 4, wired end-to-end: a repeated compile failure that DOES name a
    credible file (likely_files non-empty) is an ordinary repair target -
    existing machinery proceeds, this mechanism never engages."""
    store = DeterministicFailureDiagnosticStore()
    ctx = _minimal_ctx(tmp_path, deterministic_failure_diagnostics=store)
    state = GenerationState()
    state.last_attempt_mode = "full_set"
    message = "cannot find symbol: method foo() in Bar.java"

    def attributed_failure() -> QualityGateFailure:
        return QualityGateFailure(Failure(
            type="compile", message=message, raw_output=message, likely_files=["Bar.java"],
        ))

    with patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None), patch(
        "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline",
    ) as mock_replay:
        await handle_attempt_failure(state, ctx, attributed_failure())
        await handle_attempt_failure(state, ctx, attributed_failure())

    mock_replay.assert_not_called()
    assert not state.environment_failure


# --- Category 4: the real behavioral contract - the retry LOOP itself stops,
# not merely that evaluate_candidate_independent_failure() classifies
# correctly in isolation. Same WorkflowEngine.run_generation_workflow()
# fixture style as tests/test_workflow.py's own
# test_workflow_fallback_targeted_fix_succeeds_before_full_set_regeneration
# (mock PolymorphicValidator.run_compile_check + we.developer.run_generation,
# let the REAL retry loop/decide_for_state run end to end). ---

def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("placeholder\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)


@pytest.mark.asyncio
async def test_real_retry_loop_stops_before_a_third_developer_call(tmp_path):
    """The key behavioral contract of this whole architecture extension,
    reproducing the actual P7 attempt-1 shape end to end: Developer attempt 1
    and attempt 2 each write MATERIALLY DIFFERENT candidate content, but the
    SAME deterministic, unattributed Kriya compile-check message rejects
    both. On attempt 2's failure, the baseline replay (also going through
    the same globally-mocked run_compile_check) reproduces the identical
    failure -> the run must stop BEFORE a third Developer call, with
    failure_category correctly reported as the new candidate-independent
    category, never a bare "quality_gates_exhausted" or "environment_failure"
    label."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: not approved - compilation kept failing",
    ])

    zero_class_files_message = (
        "Maven reported compilation success, but zero .class files were actually "
        "produced under target/classes. Maven's default sourceDirectory (src/main/java) "
        "most likely doesn't cover where this project's .java files actually live - add "
        "an explicit <sourceDirectory> to pom.xml's <build> section pointing at their "
        "real location, rather than assuming the conventional src/main/java layout."
    )

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": zero_class_files_message},  # attempt 1's own check
            {"success": False, "output": zero_class_files_message},  # attempt 2's own check
            {"success": False, "output": zero_class_files_message},  # attempt 2's triggered baseline replay
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x2;\n  int y;\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is False
    assert we.developer.run_generation.call_count == 2  # never reached a third Developer call
    assert res.get("failure_category") == "candidate_independent_deterministic_failure"
    assert res.get("environment_failure", "").startswith("CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE:")


@pytest.mark.asyncio
async def test_real_retry_loop_continues_past_a_third_developer_call_when_baseline_reproduces_nothing(
    tmp_path,
):
    """FI-06 (R1 Deliverable 4 fault-injection audit) - negative control for
    the test immediately above. Same recurring-signature shape (attempt 1
    and attempt 2 write materially different candidates, the same
    deterministic compile-check message rejects both, triggering a
    baseline replay) - but this time the baseline replay SUCCEEDS (the
    underlying project/toolchain is fine; attempt 2's own candidate is
    genuinely still broken, not the same deterministic defect the
    candidate-independent detector exists to catch). Unlike the unit-level
    tests for evaluate_candidate_independent_failure() (test_baseline_
    succeeds_classifies_candidate_correctable_and_retry_continues,
    test_different_failure_signatures_produce_no_candidate_independent_
    conclusion), which prove only that the classification FUNCTION returns
    the right enum, this drives the real WorkflowEngine.run_generation_
    workflow() retry loop and proves the loop actually keeps going: a
    THIRD Developer call happens (never short-circuited), and if that
    third candidate genuinely fixes the problem, the run succeeds
    normally - the detector must never terminate a legitimately
    continuing repair."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm_call_log: list = []

    async def mock_complete(*args, **kwargs):
        llm_call_log.append(1)
        n = len(llm_call_log)
        if n == 1:
            return "Step 1: Write code"
        if n == 2:
            return "Design: Write App.java"
        # Reviewer, and (since this run reaches terminal success after a
        # real retry) the post-success advisory lesson-extraction call -
        # both tolerate this generic text fine (extraction just logs a
        # harmless warning when it can't recover a fact list from it).
        return "Review: Approved"

    llm.complete = mock_complete

    zero_class_files_message = (
        "Maven reported compilation success, but zero .class files were actually "
        "produced under target/classes. Maven's default sourceDirectory (src/main/java) "
        "most likely doesn't cover where this project's .java files actually live - add "
        "an explicit <sourceDirectory> to pom.xml's <build> section pointing at their "
        "real location, rather than assuming the conventional src/main/java layout."
    )

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": zero_class_files_message},  # attempt 1's own check
            {"success": False, "output": zero_class_files_message},  # attempt 2's own check
            {"success": True, "output": "BUILD SUCCESS"},  # attempt 2's triggered baseline replay - REPRODUCES NOTHING
            {"success": True, "output": "BUILD SUCCESS"},  # attempt 3's own check - genuinely fixed
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x2;\n  int y;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  public static void main(String[] a) {}\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert we.developer.run_generation.call_count == 3  # the loop was NOT short-circuited
    assert res["quality_gates_passed"] is True
    assert res.get("failure_category") != "candidate_independent_deterministic_failure"
