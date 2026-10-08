"""ENFORCE-PARTIAL-NO-CHANGE-001 (owner decision OD-3, BACKEND-FINAL-CLOSURE-005): the deterministic,
repository-independent reproducer the owner required BEFORE any production change.

The required case, in one execution/mutation unit:
    file A is authorized/required to change; file B is explicitly required to remain unchanged;
    the candidate correctly changes A and leaves B byte-identical  ->  the unit may complete successfully.
Negative controls: B changed -> VIOLATED; B deleted -> VIOLATED; B renamed -> VIOLATED; stale baseline -> REFUSED;
a retry/fallback cannot weaken B's no-change requirement.

Two readings of "B must remain unchanged" exist in the repository and both are reproduced here:
1. B is frozen by the GOAL ("Do not modify README.md."): a per-file immutability claim closed by the run's own
   mutation record - exactly how TEST_IMMUTABILITY already closes "do not change any existing test".
2. B is a PLANNED file of a mutating unit the Developer leaves unchanged (the registered row's live shape,
   spring-xml s3): the whole-unit ENFORCE-VERIFIED-NO-CHANGE-001 rule applied per file - the unit completes only
   when every acceptance criterion is covered by deterministic gate evidence of the final attempt.

Every run is end to end through the real run_generation_workflow; the model is scripted at the transport (reading 2)
or as the Developer's result (reading 1); only the toolchain gates are stubbed.
"""
import asyncio
import json
import re
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from _protocol_responses import sentinel, wants_structured
from test_prd020_requirement_lineage import _engine, _gates_pass, _verdicts_json

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

# ------------------------------------------------------------------ reading 1: a goal-frozen file in a mutating run
CALC_WRONG = "def lower(s):\n    return s.upper()\n\n\ndef upper(s):\n    return s.upper()\n"
CALC_OK = "def lower(s):\n    return s.lower()\n\n\ndef upper(s):\n    return s.upper()\n"
README = "# calc\n\nA tiny library.\n"
TEST_CALC = "import calc\n\n\ndef test_upper():\n    assert calc.upper('a') == 'A'\n"
EXAMPLES = "Examples:\n  calc.lower('ABC') -> 'abc'\n  calc.upper('abc') -> 'ABC'\n"
FROZEN_FILE_GOAL = EXAMPLES + "\nDo not modify README.md.\n"
FROZEN_TESTS_GOAL = EXAMPLES + "\nEvery existing test must keep passing unchanged.\n"


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


def _workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC_WRONG)
    (root / "README.md").write_text(README)
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n")  # a real project ignores interpreter caches
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(TEST_CALC)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _bytes(root, *paths):
    return {p: (root / p).read_bytes() if (root / p).exists() else None for p in paths}


def _calc_engine(tmp_path):
    """test_prd020's engine (spec checker scripted, policies block) with Planner / Architect / File List stubs that
    name calc.py - the file the Developer's full-file answer rewrites must be the authoritative known target."""
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    spec = engine.llm.complete

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system = system_prompt or ""
        if "Kriya Planner Agent" in system:
            calls["planner"].append(user_prompt)
            return "Step 1: fix lower() in calc.py so it lower-cases its argument (REQ-1)"
        if "Kriya Architect Agent" in system:
            calls["architect"].append(user_prompt)
            return "Design: calc.py - lower(s) returns s.lower(); upper(s) unchanged"
        if "File List Planner" in system:
            return json.dumps({"files": ["calc.py"]})
        return await spec(system_prompt, user_prompt, *args, **kwargs)

    engine.llm.complete = complete
    return cfg, engine, calls


async def _run_frozen(tmp_path, goal, developer_files):
    """The direct path with every deterministic producer real: the goal's examples run under the B2-a runner, the
    baseline and candidate suites run the workspace's own pytest; only the compile gate is stubbed."""
    _cfg, engine, _calls = _calc_engine(tmp_path)
    workspace = _workspace(tmp_path)
    engine.developer.run_generation = AsyncMock(return_value=developer_files)
    compile_ok, _tests = _gates_pass()
    with compile_ok:
        res = await engine.run_generation_workflow(goal=goal, workspace_path=str(workspace))
    return workspace, engine, res


@pytest.mark.asyncio
async def test_r1_a_changed_as_authorized_and_a_goal_frozen_file_unchanged_completes(tmp_path):
    """The required case: calc.py (A) is fixed, README.md (B) is byte-identical to the baseline."""
    workspace, engine, res = await _run_frozen(tmp_path, FROZEN_FILE_GOAL, [{"filepath": "calc.py", "content": CALC_OK}])
    assert res["quality_gates_passed"] is True, (res.get("failure_category"), res.get("environment_failure"))
    assert engine.developer.run_generation.await_count >= 1  # a model was asked: never a no-mutation shortcut
    assert _bytes(workspace, "calc.py", "README.md") == {"calc.py": CALC_OK.encode(), "README.md": README.encode()}
    # REQ-1 (the examples) and REQ-2 (the frozen file) both close on deterministic evidence, nothing by model word
    assert set(res["requirements"]["outcomes"].values()) == {"closed_by_evidence"}, res["requirements"]["outcomes"]


@pytest.mark.asyncio
async def test_r1_a_goal_frozen_file_the_candidate_changed_is_violated_and_nothing_is_applied(tmp_path):
    workspace, _engine_, res = await _run_frozen(tmp_path, FROZEN_FILE_GOAL, [
        {"filepath": "calc.py", "content": CALC_OK}, {"filepath": "README.md", "content": README + "\nEdited.\n"}])
    assert res["quality_gates_passed"] is False
    assert _engine_.developer.run_generation.await_count >= 1  # attempted and refused, not "not admitted"
    assert "README.md" in json.dumps(res.get("requirements")) or "README.md" in str(res.get("environment_failure"))
    assert _bytes(workspace, "calc.py", "README.md") == {"calc.py": CALC_WRONG.encode(), "README.md": README.encode()}


@pytest.mark.asyncio
async def test_r1_a_retry_that_changes_the_frozen_file_again_never_weakens_the_requirement(tmp_path):
    """Every attempt changes README.md: no attempt may succeed, whatever the retry budget."""
    workspace, engine, res = await _run_frozen(tmp_path, FROZEN_FILE_GOAL, [
        {"filepath": "calc.py", "content": CALC_OK}, {"filepath": "README.md", "content": "# changed\n"}])
    assert res["quality_gates_passed"] is False and engine.developer.run_generation.await_count >= 1
    assert _bytes(workspace, "README.md") == {"README.md": README.encode()}


@pytest.mark.asyncio
async def test_r1_control_the_existing_test_immutability_closer_accepts_a_correct_fix(tmp_path):
    workspace, _engine_, res = await _run_frozen(tmp_path, FROZEN_TESTS_GOAL, [{"filepath": "calc.py", "content": CALC_OK}])
    assert res["quality_gates_passed"] is True, (res.get("failure_category"), res.get("environment_failure"))
    assert _bytes(workspace, "tests/test_calc.py") == {"tests/test_calc.py": TEST_CALC.encode()}


@pytest.mark.asyncio
async def test_r1_control_the_existing_test_immutability_closer_refuses_a_changed_test(tmp_path):
    workspace, _engine_, res = await _run_frozen(tmp_path, FROZEN_TESTS_GOAL, [
        {"filepath": "calc.py", "content": CALC_OK},
        {"filepath": "tests/test_calc.py", "content": TEST_CALC + "\n\ndef test_more():\n    assert True\n"}])
    assert res["quality_gates_passed"] is False and _engine_.developer.run_generation.await_count >= 1
    assert _bytes(workspace, "tests/test_calc.py") == {"tests/test_calc.py": TEST_CALC.encode()}


# ------------------------------------------------------------------ reading 2: a planned file left unchanged in a unit
A = "shop/service.py"
B = "shop/controller.py"
A_SRC = "def find_pet_types():\n    return ('cat', 'dog')\n"
A_FIXED = "import functools\n\n\n@functools.lru_cache(maxsize=None)\ndef find_pet_types():\n    return ('cat', 'dog')\n"
B_SRC = "from shop.service import find_pet_types\n\n\ndef populate_pet_types():\n    return find_pet_types()\n"
GOAL = "Cache the pet types the same way the vets are cached."
TESTS_PASS = {"success": True, "output": "tests/test_shop.py .....\n=================== 5 passed in 0.10s ==================="}
TOOL_CRITERION = {"id": "ac1", "description": "pet types are cached", "method": "tool", "tool_name": "test"}
JUDGMENT_CRITERION = {"id": "ac1", "description": "pet types are cached", "method": "judgment"}
_PATH_IN_PROMPT = re.compile(r'path="([^"]+)"')


def _plan(criterion):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [criterion],
        "subtasks": [
            {"id": "s1", "description": "cache the pet types and use them in the controller", "execution_method": "model",
             "planned_files": [{"path": A, "action": "modify"}, {"path": B, "action": "modify"}],
             "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [criterion["id"]],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ]})


def _answer_per_path(system_prompt, b_changes):
    """One block per planned path the structured contract names: A rewritten, B a NO CHANGE (or rewritten)."""
    assert wants_structured(system_prompt), "the production default protocol is structured"
    blocks = []
    for path in dict.fromkeys(_PATH_IN_PROMPT.findall(system_prompt)):
        if path == A:
            blocks.append(sentinel(A, analysis="cache the service call.", content=A_FIXED))
        elif path == B:
            blocks.append(sentinel(B, analysis="document the cached call.", content=B_SRC.replace(
                "def populate_pet_types():\n", 'def populate_pet_types():\n    """Cached pet types."""\n'))
                          if b_changes else sentinel(B, analysis="populate_pet_types already calls the service.", no_change=True))
    return "".join(blocks)


def _run_unit(tmp_path, *, criterion=TOOL_CRITERION, b_changes=False):
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    (workspace / A).write_text(A_SRC)
    (workspace / B).write_text(B_SRC)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    developer = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [A, B]})
        elif "Developer Agent" in first:
            developer.append(system_prompt)
            content = _answer_per_path(system_prompt, b_changes)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    events = []
    real_record = GenerationState.record_event

    def record(state, event):
        events.append(event)
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: TESTS_PASS):
        result = asyncio.run(engine.run_generation_workflow(
            goal=GOAL, workspace_path=str(workspace), predetermined_plan="cache the pet types",
            predetermined_design="", predetermined_architect_files=[A, B], allowed_write_relpaths=[A, B],
            structured_plan=_plan(criterion), current_subtask_id="s1",
            required_verification=[{"type": "tool", "tool_name": "test", "description": "run the tests"}],
            approval_callback=AsyncMock(return_value=True)))
    return workspace, developer, events, result


def test_r2_a_unit_that_changes_a_and_deterministically_verifies_no_change_for_b_completes(tmp_path):
    """The registered row's shape: one mutating unit, A rewritten, NO CHANGE for the planned B."""
    workspace, developer, events, result = _run_unit(tmp_path)
    assert developer, "the Developer was asked"
    assert result["quality_gates_passed"] is True, (result.get("failure_category"), result.get("environment_failure"))
    assert sorted(result["files"]) == [A]
    assert _bytes(workspace, A, B) == {A: A_FIXED.encode(), B: B_SRC.encode()}
    [verified] = [e.details for e in events if e.kind == "unit.verified_no_change"]
    assert verified["paths"] == [B] and verified["acceptance_coverage"][0]["criterion_id"] == "ac1"
    assert result["completion_kind"] is None  # the unit mutated: an ordinary completion carrying a verified per-file no-change


def test_r2_a_judgment_criterion_is_never_evidence_for_the_planned_files_no_change(tmp_path):
    """Nothing deterministic covers B's no-change: the unit does not complete and A is not applied either."""
    workspace, _developer, events, result = _run_unit(tmp_path, criterion=JUDGMENT_CRITERION)
    assert result["quality_gates_passed"] is False
    assert _bytes(workspace, A, B) == {A: A_SRC.encode(), B: B_SRC.encode()}
    assert not any(e.kind == "unit.verified_no_change" for e in events)


def test_r2_a_unit_that_writes_both_planned_files_is_an_ordinary_mutation(tmp_path):
    workspace, _developer, events, result = _run_unit(tmp_path, b_changes=True)
    assert result["quality_gates_passed"] is True
    assert sorted(result["files"]) == sorted([A, B])
    assert b"Cached pet types" in (workspace / B).read_bytes()
    assert not any(e.kind in ("unit.no_change_proposed", "unit.verified_no_change") for e in events)


# ------------------------------------------------------------------ negative controls, edge by edge (owner OD-3 list)
import shutil  # noqa: E402 - grouped with the unit tests below
from types import SimpleNamespace  # noqa: E402

from kriya.workflow import contract_baseline as cb  # noqa: E402
from kriya.workflow.attempt import _verified_no_change_proposal  # noqa: E402
from kriya.workflow.contract_compilation import CONTRACT_COMPILER_VERSION, compile_verification_contract  # noqa: E402
from kriya.workflow.obligations import ObligationLedger  # noqa: E402
from kriya.workflow.plan_schema import FileAction  # noqa: E402
from kriya.workflow.plan_validation import validate_plan  # noqa: E402
from kriya.workflow.planner_repair import (  # noqa: E402
    PLANNER_POLICY_REJECTION_CODES,
    build_structured_plan_repair_prompt,
)
from kriya.workflow.requirements import (  # noqa: E402
    CLAIM_KINDS,
    FILE_IMMUTABILITY_CLAIM,
    RequirementOutcome,
    derive_requirements,
    frozen_file_statement,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)
from kriya.workflow.workflow import (  # noqa: E402
    close_requirements_by_file_immutability,
    frozen_file_paths,
    immutable_test_files,
)

TRACKED = ["calc.py", "README.md", "tests/test_calc.py", ".gitignore"]


def _contract(goal, tracked=TRACKED, authorities=()):
    reqs = derive_requirements(goal)
    return reqs, compile_verification_contract(reqs, origins=statement_origins(goal), test_files=["tests/test_calc.py"],
                                               tracked_paths=tracked, project_language="python",
                                               external_authorities=authorities)


def test_n1_the_recognizer_is_a_closed_vocabulary_over_exact_tracked_paths():
    """Pure freezes of tracked files are recognized in their stated forms; anything else stays a behaviour claim."""
    for text in ("Do not modify README.md.", "README.md must remain unchanged.", "Keep `README.md` unchanged.",
                 "Never touch or delete README.md", "Leave tests/test_calc.py untouched."):
        assert frozen_file_statement(text, TRACKED) == (("tests/test_calc.py",) if "tests/" in text else ("README.md",)), text
    assert frozen_file_statement("Do not modify README.md or `calc.py`.", TRACKED) == ("README.md", "calc.py")
    for text in ("Do not modify README.md unless necessary.",  # a condition: not pure
                 "Do not modify any other file.",  # the mutation-scope statement, another closer
                 "Do not modify docs/README.md.",  # not a tracked path (exact, never by basename)
                 "Do not modify README.md; instead change calc.py.",  # a second clause
                 "Do not forget to modify README.md.",  # the negation does not freeze
                 "Run `pytest` and do not modify README.md.",  # a command span
                 "Modify README.md."):  # no freeze at all
        assert frozen_file_statement(text, TRACKED) == (), text
    assert frozen_file_statement("Do not modify README.md.", []) == ()  # no base tree: nothing can be frozen
    assert FILE_IMMUTABILITY_CLAIM in CLAIM_KINDS  # an operator disposition may name the claim
    assert CONTRACT_COMPILER_VERSION == 3


def test_n2_the_compiled_contract_binds_the_frozen_paths_and_a_frozen_test_is_a_freeze_not_a_regression_claim():
    reqs, contract = _contract(FROZEN_FILE_GOAL)
    assert contract.required_claims_by_requirement()["REQ-2"] == (FILE_IMMUTABILITY_CLAIM,)
    assert frozen_file_paths(contract) == {"REQ-2": ["README.md"]}
    _reqs, with_test = _contract(EXAMPLES + "\nLeave tests/test_calc.py untouched.\n")
    assert with_test.required_claims_by_requirement()["REQ-2"] == (FILE_IMMUTABILITY_CLAIM,)
    assert immutable_test_files(with_test, "/nonexistent") is None  # not a TEST_IMMUTABILITY claim: another rule
    # the baseline holds a frozen file by definition (identity claim), the bound examples decide the mutation
    from kriya.workflow.contract_compilation import ExternalAuthority
    from kriya.workflow.requirements import BEHAVIOR

    examples = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": "EXACT"}}})
    reqs, contract = _contract(FROZEN_FILE_GOAL, authorities=[examples])
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc",
                                         judge_examples=lambda: {"REQ-1": SimpleNamespace(passed=False, violated=True)},
                                         examples_digest="e" * 64)
    assert report.claims["REQ-2"][FILE_IMMUTABILITY_CLAIM]["state"] == cb.BASELINE_IDENTITY
    assert report.discriminating is True and report.no_mutation_required is False
    assert frozen_file_paths(None) == {}


def _plan_with(files):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [{"id": "ac1", "description": "ok", "method": "tool", "tool_name": "test"}],
        "subtasks": [{"id": "s1", "description": "do it", "execution_method": "model",
                      "planned_files": [{"path": path, "action": action.value} for path, action in files],
                      "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
                      "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]}]})


def test_n3_a_plan_touching_a_frozen_file_is_refused_at_planning_on_every_revision_with_repair_guidance(tmp_path):
    ws = _workspace(tmp_path)
    _reqs, contract = _contract(FROZEN_FILE_GOAL)
    frozen = sorted({p for paths in frozen_file_paths(contract).values() for p in paths})
    for action in (FileAction.MODIFY, FileAction.DELETE, FileAction.CREATE):
        for revision in (0, 3):  # a repaired/fallback plan is judged against the same sealed set
            result = asyncio.run(validate_plan(_plan_with([("calc.py", FileAction.MODIFY), ("README.md", action)]),
                                               workspace_path=str(ws), frozen_files=frozen, revision=revision))
            assert result.valid is False and "PLAN_EDITS_FROZEN_FILE" in result.reason_codes, (action, revision)
            assert "s1:README.md" in "".join(result.errors)
    ok = asyncio.run(validate_plan(_plan_with([("calc.py", FileAction.MODIFY)]), workspace_path=str(ws), frozen_files=frozen))
    assert ok.valid is True
    assert "PLAN_EDITS_FROZEN_FILE" in PLANNER_POLICY_REJECTION_CODES
    prompt = build_structured_plan_repair_prompt("g", "{}", ["frozen"], ["PLAN_EDITS_FROZEN_FILE"], 1)
    assert "remain unchanged" in prompt and "frozen" in prompt


def _candidate(tmp_path, workspace):
    """A candidate tree: a copy of the workspace at the same HEAD (what the run's worktree is)."""
    candidate = tmp_path / "cand"
    shutil.copytree(workspace, candidate, symlinks=True)
    return candidate


def _ledger(reqs):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    return ledger


@pytest.mark.parametrize("shape", ["unchanged", "changed", "deleted", "renamed"])
def test_n4_the_closer_judges_the_frozen_file_from_the_mutation_record(tmp_path, shape):
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace)
    (candidate / "calc.py").write_text(CALC_OK)  # the authorized change
    if shape == "changed":
        (candidate / "README.md").write_text(README + "\nmore\n")
    elif shape == "deleted":
        (candidate / "README.md").unlink()
    elif shape == "renamed":
        (candidate / "README.md").rename(candidate / "README.rst")
    reqs, contract = _contract(FROZEN_FILE_GOAL)
    ledger = _ledger(reqs)
    [entry] = close_requirements_by_file_immutability(ledger, reqs, str(candidate), str(workspace),
                                                       candidate_paths=["calc.py"], revision=1, contract=contract)
    outcome = requirement_outcomes(ledger, reqs)["REQ-2"]
    if shape == "unchanged":
        assert entry["closed"] is True and outcome is RequirementOutcome.CLOSED_BY_EVIDENCE
        assert entry["changed_frozen_paths"] == [] and entry["missing_frozen_paths"] == []
    else:
        assert entry["closed"] is False and entry["violated"] is True and outcome is RequirementOutcome.VIOLATED
        # a deleted or renamed file is "changed" in the diff too; its absence is the stronger, named fact
        assert entry["changed_frozen_paths"] == ["README.md"]
        assert (entry["missing_frozen_paths"] == ["README.md"]) == (shape in ("deleted", "renamed"))
        assert ("missing" in entry["reason"]) == (shape in ("deleted", "renamed"))
    assert entry["base_revision"] == _git(workspace, "rev-parse", "HEAD")


def test_n5_a_candidate_not_at_the_runs_base_is_unavailable_evidence_never_a_closure(tmp_path, monkeypatch):
    """Stale baseline: the owning run's base revision differs from the candidate's HEAD (fail closed)."""
    import kriya.workflow.workflow as wf

    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace)
    (candidate / "calc.py").write_text(CALC_OK)
    monkeypatch.setattr(wf, "_run_committed_paths", lambda _ws: ("run-1", "0" * 40, []))
    reqs, contract = _contract(FROZEN_FILE_GOAL)
    ledger = _ledger(reqs)
    [entry] = close_requirements_by_file_immutability(ledger, reqs, str(candidate), str(workspace),
                                                       candidate_paths=["calc.py"], revision=1, contract=contract)
    assert entry["closed"] is False and "not the run base" in entry["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    # a goal without a frozen file costs nothing
    _reqs2, free = _contract(EXAMPLES)
    assert close_requirements_by_file_immutability(ledger, reqs, str(candidate), str(workspace),
                                                   candidate_paths=["calc.py"], revision=1, contract=free) == []


def test_n6_the_partial_proposal_covers_only_planned_files_the_unit_never_wrote(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / A).write_text(A_SRC)
    (tmp_path / B).write_text(B_SRC)
    ctx = SimpleNamespace(structured_plan=_plan(TOOL_CRITERION), current_subtask_id="s1", worktree_path=str(tmp_path))
    written_a = {"filepath": A, "content": A_FIXED}
    no_change_b = {"filepath": B, "content": None, "no_change": True}
    fresh = SimpleNamespace(all_files_written={A})  # A was staged this attempt
    assert _verified_no_change_proposal(fresh, ctx, [written_a, no_change_b], ["controller.py"]) == [B]
    # the whole-unit shape is unchanged
    assert _verified_no_change_proposal(SimpleNamespace(all_files_written=set()), ctx, [no_change_b], ["controller.py"]) == [B]
    # a file the unit wrote earlier and now calls unchanged is not an assessment of the base
    assert _verified_no_change_proposal(SimpleNamespace(all_files_written={A, B}), ctx, [no_change_b], ["controller.py"]) == []
    # a protocol error is a rejected response, never a no-change
    assert _verified_no_change_proposal(fresh, ctx, [{**no_change_b, "protocol_error": "x"}], ["controller.py"]) == []
    # a missing file no answer explains stays missing: incomplete, not a proposal
    assert _verified_no_change_proposal(fresh, ctx, [written_a, no_change_b], ["controller.py", "other.py"]) == []
    # a no-change for a file that does not exist is a missing artifact
    assert _verified_no_change_proposal(fresh, ctx, [{**no_change_b, "filepath": "shop/new.py"}], ["new.py"]) == []
    # a direct (unstructured) run: unchanged behaviour
    assert _verified_no_change_proposal(fresh, SimpleNamespace(structured_plan=None, current_subtask_id=None,
                                                               worktree_path=str(tmp_path)), [no_change_b], ["controller.py"]) == []
