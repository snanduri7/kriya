"""LR-R1-M1 D7: typed reason codes for NO_AUTHORIZED_REPAIR_TARGET and
REGRESSION_UNATTRIBUTED (design §14 D7; test T11).

Both stops existed only as message text. Their producers now also set
``GenerationState.stop_reason_evidence`` = StopReasonEvidence(code, the exact message), an
evidence-only carrier (these stops are not environment failures);
the message and the stop decision are byte-unchanged, the recovery.decision
evidence names the code, and no production decision reads it (structural).
"""
import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_workflow import _minimal_attempt_ctx

from kriya.core.attempt_evidence import reader, scope
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.diagnosis_codes import NO_AUTHORIZED_REPAIR_TARGET, REGRESSION_UNATTRIBUTED, StopReasonEvidence
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

REPO = Path(__file__).resolve().parent.parent


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


def _recorded(tmp_path, monkeypatch, run_id, body):
    from tests._strict_doubles import strict_config

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state_dir))
    with scope.run_scope(_Context(run_id)):
        scope.ensure_store(strict_config())
        outcome = asyncio.run(body())
        scope._RUN.get().writer.seal("test")
    records = list(reader.open_run(str(state_dir), run_id).records())
    return outcome, [r["payload"] for r in records if r["kind"] == "recovery.decision"]


def _regression_unattributed(tmp_path):
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    message = ("REGRESSION_UNATTRIBUTED: the full-regression suite's aggregate outcome changed relative to the "
               "captured PRE-mutation baseline (level1=CHANGED_FAILURE), but no specific test could be confirmed.")
    exc = QualityGateFailure(Failure(type="regression_unattributed", message=message, raw_output="1 failed"))
    return state, ctx, exc, message


def test_regression_unattributed_is_typed_beside_its_unchanged_message(tmp_path, monkeypatch):
    state, ctx, exc, message = _regression_unattributed(tmp_path)

    async def body():
        return await handle_attempt_failure(state, ctx, exc)
    should_break, decisions = _recorded(tmp_path, monkeypatch, "run-d7-regression", body)
    assert should_break is True
    assert state.environment_failure == message                       # the message is byte-unchanged
    assert state.stop_reason_evidence == StopReasonEvidence(REGRESSION_UNATTRIBUTED, message)
    assert decisions[-1]["stop_reason_code"] == REGRESSION_UNATTRIBUTED


def test_no_authorized_repair_target_is_typed_beside_its_unchanged_message(tmp_path, monkeypatch):
    from kriya.workflow.attribution import AttributionResult

    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    state.all_files_written = {"manage.py"}
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    ctx.allowed_write_relpaths = ["manage.py", "requirements.txt"]
    ctx.write_scope_mode = WriteScopeMode.ALLOWLIST
    exc = QualityGateFailure(Failure(type="test", message="ImproperlyConfigured: ROOT_URLCONF",
                                     likely_files=["proj/settings.py"]))
    attribution = AttributionResult(tier="judge", files=["proj/settings.py"], confidence="medium", reasoning="r")

    async def body():
        with patch("kriya.workflow.retry_strategy.attribute_failure", AsyncMock(return_value=attribution)):
            return await handle_attempt_failure(state, ctx, exc)
    should_break, decisions = _recorded(tmp_path, monkeypatch, "run-d7-scope", body)
    assert should_break is True
    assert state.environment_failure.startswith("NO_AUTHORIZED_REPAIR_TARGET: this failure is grounded to ")
    assert state.stop_reason_evidence == StopReasonEvidence(NO_AUTHORIZED_REPAIR_TARGET, state.environment_failure)
    assert decisions[-1]["stop_reason_code"] == NO_AUTHORIZED_REPAIR_TARGET


def test_an_ordinary_failure_carries_no_code(tmp_path, monkeypatch):
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    exc = QualityGateFailure(Failure(type="compile", message="error: ';' expected", raw_output="x"))

    async def body():
        return await handle_attempt_failure(state, ctx, exc)
    _should_break, decisions = _recorded(tmp_path, monkeypatch, "run-d7-none", body)
    assert state.stop_reason_evidence is None
    assert decisions[-1]["stop_reason_code"] is None


def test_a_code_for_a_replaced_message_is_not_reported():
    from kriya.workflow.recovery_coordinator import _typed_stop_reason

    state = GenerationState()
    state.stop_reason_evidence = StopReasonEvidence(REGRESSION_UNATTRIBUTED, "REGRESSION_UNATTRIBUTED: old")
    state.environment_failure = "CONTAINMENT_SETUP_FAILED: newer stop"
    assert _typed_stop_reason(state) is None
    state.environment_failure = "REGRESSION_UNATTRIBUTED: old"
    assert _typed_stop_reason(state) == REGRESSION_UNATTRIBUTED
    state.environment_failure = None
    assert _typed_stop_reason(state) is None


# -- structural: producers, the recorder and tests only ----------------------------------------

_PRODUCERS = {"kriya/workflow/retry_strategy.py"}
_RECORDER_READERS = {("kriya/workflow/recovery_coordinator.py", "_typed_stop_reason")}
_CODE_NAMES = {"NO_AUTHORIZED_REPAIR_TARGET", "REGRESSION_UNATTRIBUTED", "StopReasonEvidence"}
# state.py declares the field (its type annotation); it never reads it.
_DECLARATION = "kriya/workflow/state.py"


def _enclosing_functions(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _function_of(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


def test_the_typed_codes_are_read_only_by_the_recorder_and_named_only_by_producers():
    violations = []
    for path in sorted((REPO / "kriya").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel == "kriya/workflow/diagnosis_codes.py" or rel.startswith("kriya/core/attempt_evidence/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "stop_reason_evidence":
                if isinstance(node.ctx, ast.Store):
                    if rel not in _PRODUCERS:
                        violations.append(f"{rel}:{node.lineno} sets the code outside a producer")
                elif (rel, _function_of(node, parents)) not in _RECORDER_READERS:
                    violations.append(f"{rel}:{node.lineno} reads the code outside the recorder")
            declares = rel == _DECLARATION and isinstance(node, ast.Name) and node.id == "StopReasonEvidence"
            if isinstance(node, ast.Name) and node.id in _CODE_NAMES and rel not in _PRODUCERS and not declares:
                violations.append(f"{rel}:{node.lineno} names {node.id} outside a producer")
            if isinstance(node, ast.ImportFrom) and node.module == "kriya.workflow.diagnosis_codes" \
                    and rel not in _PRODUCERS and not (rel == _DECLARATION
                                                       and {a.name for a in node.names} == {"StopReasonEvidence"}):
                violations.append(f"{rel}:{node.lineno} imports the codes outside a producer")
    assert violations == []


def test_the_structural_check_catches_a_planted_decision_read(tmp_path):
    """Negative control: the same walk flags a read in a decision function."""
    source = ("def decide(state):\n"
              "    if state.stop_reason_evidence:\n"
              "        return 'stop'\n")
    tree = ast.parse(source)
    parents = _enclosing_functions(tree)
    reads = [(_function_of(n, parents)) for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "stop_reason_evidence"
             and not isinstance(n.ctx, ast.Store)]
    assert reads == ["decide"]
    assert ("kriya/workflow/retry_policy.py", "decide") not in _RECORDER_READERS


def test_another_environment_stop_carries_no_d7_code(tmp_path, monkeypatch):
    """A different typed stop (an environment_failure that is neither D7
    stop) is never typed with a D7 code."""
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    exc = QualityGateFailure(Failure(type="time_budget_exhausted", message="TIME_BUDGET_EXHAUSTED: 900s",
                                     raw_output="x"))

    async def body():
        return await handle_attempt_failure(state, ctx, exc)
    should_break, decisions = _recorded(tmp_path, monkeypatch, "run-d7-other", body)
    assert should_break is True and state.environment_failure == "TIME_BUDGET_EXHAUSTED: 900s"
    assert state.stop_reason_evidence is None
    assert decisions[-1]["stop_reason_code"] is None
