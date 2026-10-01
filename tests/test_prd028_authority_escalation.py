"""PRD-028: the language-adapter contract and revision-bound member
authority escalation.

The reproduction at the heart of the batch directive: a retry on an
already-written file resolves members from the worktree CANDIDATE, which is
correct for the edit target. Before PRD-028 the result was indistinguishable
from pristine repository evidence. Every expansion now records its origin,
and claims about the baseline (``member_in_pristine``) come only from the
immutable workspace source.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_dev_inv_001_investigation import _minimal_attempt_ctx as _devinv_ctx
from test_dev_inv_001_investigation import _native_result, _propose_result

from kriya.config.config import ModelCapabilities
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import authority_escalation as ae
from kriya.workflow.attempt import _maybe_run_developer_investigation, _prepare_retry_context
from kriya.workflow.context_source import member_boundaries_for
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.language_adapters import (
    JAVA_ADAPTER,
    PYTHON_ADAPTER,
    Capability,
    CapabilityStatus,
    adapter_for,
    capability_status,
    capability_table,
)
from kriya.workflow.state import GenerationState

# --- adapter contract -----------------------------------------------------------------


def test_java_and_python_adapters_declare_their_real_capabilities():
    assert adapter_for("src/A.java") is JAVA_ADAPTER
    assert adapter_for("pkg/a.py") is PYTHON_ADAPTER
    for capability in Capability:
        assert JAVA_ADAPTER.status(capability) is not CapabilityStatus.UNSUPPORTED
    assert PYTHON_ADAPTER.status(Capability.EDITABLE_REGION) is CapabilityStatus.UNSUPPORTED
    assert Capability.EDITABLE_REGION in PYTHON_ADAPTER.limitations
    assert set(capability_table()) == {"java", "python"}


@pytest.mark.parametrize("path", ["main.go", "app.ts", "lib.rs", "Makefile", "notes.txt"])
def test_every_other_language_is_explicitly_unsupported(path):
    assert adapter_for(path) is None
    for capability in Capability:
        assert capability_status(path, capability) is CapabilityStatus.UNSUPPORTED
    assert member_boundaries_for(path, "anything") is None


def test_member_boundaries_for_delegates_to_the_registry():
    assert [b.member_id for b in member_boundaries_for("a.py", "def f():\n    return 1\n")] == ["f"]
    java = "class Owner {\n  int total() {\n    return 1;\n  }\n}\n"
    assert "Owner.total" in [b.member_id for b in member_boundaries_for("Owner.java", java)]
    assert member_boundaries_for("empty.py", "") == []


# --- escalation decisions ----------------------------------------------------------------

_JAVA = "class Owner {\n  void pay(int a) {\n  }\n  int total() {\n    return 1;\n  }\n}\n"


@pytest.fixture
def roots(tmp_path):
    workspace, worktree = tmp_path / "workspace", tmp_path / "worktree"
    workspace.mkdir()
    worktree.mkdir()
    return workspace, worktree


def test_pristine_member_grant(roots):
    workspace, worktree = roots
    (workspace / "Owner.java").write_text(_JAVA)
    record = ae.request_member_authority(
        path="Owner.java", member_id="Owner.total", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["Owner.java"], evidence={"source": "test"},
    )
    assert record.outcome == ae.GRANTED and record.reason_code == ae.REASON_GRANTED
    assert record.source_origin == ae.ORIGIN_PRISTINE
    assert record.source_revision == record.pristine_revision == content_revision(_JAVA)
    assert record.member_in_pristine is True
    assert record.language == "java" and record.capability == "member_boundaries"
    assert record.to_dict()["mutation_boundary"] == ae.MUTATION_BOUNDARY
    assert record.to_dict()["evidence"] == {"source": "test"}


def test_candidate_member_is_granted_as_candidate_never_as_pristine(roots):
    """The reproduction: a member that exists only in this run's candidate
    is resolvable for editing, but it is recorded as CANDIDATE, and the
    baseline claim (member_in_pristine) is False because it comes only from
    the workspace."""
    workspace, worktree = roots
    baseline = "class Service:\n    def charge(self):\n        return 1\n"
    candidate = baseline + "\n    def invented(self):\n        return 2\n"
    (workspace / "svc.py").write_text(baseline)
    (worktree / "svc.py").write_text(candidate)
    record = ae.request_member_authority(
        path="svc.py", member_id="Service.invented", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["svc.py"],
    )
    assert record.granted
    assert record.source_origin == ae.ORIGIN_CANDIDATE
    assert record.source_revision == content_revision(candidate)
    assert record.pristine_revision == content_revision(baseline)
    assert record.member_in_pristine is False


def test_expansion_never_widens_the_write_scope(roots):
    workspace, worktree = roots
    (workspace / "Owner.java").write_text(_JAVA)
    scope = ["Other.java"]
    before = list(scope)
    record = ae.request_member_authority(
        path="Owner.java", member_id="Owner.total", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=scope,
    )
    assert record.outcome == ae.INDETERMINATE and record.reason_code == ae.REASON_OUT_OF_SCOPE
    assert record.in_write_scope is False
    assert scope == before


def test_unsupported_language_is_indeterminate_never_full_file(roots):
    workspace, worktree = roots
    (workspace / "main.go").write_text("func main() {}\n")
    record = ae.request_member_authority(
        path="main.go", member_id="main", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["main.go"],
    )
    assert record.outcome == ae.INDETERMINATE and record.reason_code == ae.REASON_UNSUPPORTED_LANGUAGE
    assert record.language is None


@pytest.mark.parametrize(("member", "reason"), [
    ("Owner.pay", None),               # one pay(int) in _JAVA: granted
    ("Owner.missing", ae.REASON_MEMBER_NOT_FOUND),
])
def test_member_resolution_outcomes(roots, member, reason):
    workspace, worktree = roots
    (workspace / "Owner.java").write_text(_JAVA)
    record = ae.request_member_authority(
        path="Owner.java", member_id=member, workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["Owner.java"],
    )
    assert record.reason_code == (reason or ae.REASON_GRANTED)


def test_overloaded_member_is_ambiguous(roots):
    workspace, worktree = roots
    overloaded = "class Owner {\n  void pay(int a) {\n  }\n  void pay(String b) {\n  }\n}\n"
    (workspace / "Owner.java").write_text(overloaded)
    record = ae.request_member_authority(
        path="Owner.java", member_id="Owner.pay", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["Owner.java"],
    )
    assert record.outcome == ae.INDETERMINATE and record.reason_code == ae.REASON_MEMBER_AMBIGUOUS


def test_missing_source_is_indeterminate(roots):
    workspace, worktree = roots
    record = ae.request_member_authority(
        path="Gone.java", member_id="Gone.x", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["Gone.java"],
    )
    assert record.reason_code == ae.REASON_SOURCE_UNAVAILABLE


def test_a_source_or_baseline_revision_change_invalidates_the_grant(roots):
    workspace, worktree = roots
    (workspace / "Owner.java").write_text(_JAVA)
    (worktree / "Owner.java").write_text(_JAVA)
    record = ae.request_member_authority(
        path="Owner.java", member_id="Owner.total", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["Owner.java"],
    )
    assert ae.expansion_is_current(record, str(workspace), str(worktree)) is True
    (worktree / "Owner.java").write_text(_JAVA.replace("return 1", "return 2"))
    assert ae.expansion_is_current(record, str(workspace), str(worktree)) is False
    (worktree / "Owner.java").write_text(_JAVA)
    (workspace / "Owner.java").write_text(_JAVA + "\n")
    assert ae.expansion_is_current(record, str(workspace), str(worktree)) is False
    denied = ae.request_member_authority(
        path="Owner.java", member_id="Owner.total", workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=[],
    )
    assert ae.expansion_is_current(denied, str(workspace), str(worktree)) is False


def test_grant_member_hints_keeps_only_granted_members(roots):
    workspace, worktree = roots
    (workspace / "Owner.java").write_text(_JAVA)
    (workspace / "main.go").write_text("func main() {}\n")
    granted, records = ae.grant_member_hints(
        {"Owner.java": ["Owner.total", "Owner.missing"], "main.go": ["main"]},
        workspace_path=str(workspace), worktree_path=str(worktree),
        authorized_paths=["Owner.java", "main.go"], evidence={"source": "test"},
    )
    assert granted == {"Owner.java": ["Owner.total"]}
    assert sorted(record.reason_code for record in records) == sorted([
        ae.REASON_GRANTED, ae.REASON_MEMBER_NOT_FOUND, ae.REASON_UNSUPPORTED_LANGUAGE,
    ])


# --- production wiring: the retry member-hint path (always on) ------------------------------


def _retry_state(path, line):
    state = GenerationState()
    state.last_attempt_mode = "targeted"
    state.attempt_number = 2
    state.last_failure = Failure(
        type="compile", message="error", raw_output="error", likely_files=[path], attempt=1,
        file_locations=[FileLocation(filepath=path, line=line)],
    )
    state.budgets.last_failure_signature = ("compile", "error")
    return state


def test_retry_member_hint_records_candidate_origin_for_an_already_written_file(tmp_path):
    from test_val001_g1r3_retry_context import _minimal_attempt_ctx

    workspace, worktree = tmp_path / "workspace", tmp_path / "worktree"
    workspace.mkdir()
    worktree.mkdir()
    baseline = "class Engine:\n    def run(self):\n        return 1\n"
    candidate = baseline + "\n    def added(self):\n        return undefined_name\n"
    (workspace / "engine.py").write_text(baseline)
    (worktree / "engine.py").write_text(candidate)
    ctx = _minimal_attempt_ctx(tmp_path, workspace_path=str(workspace), worktree_path=str(worktree))
    state = _retry_state("engine.py", 6)

    prep = _prepare_retry_context(
        state, ctx, target_files=["engine.py"], prompt_window=32768, model_identity="primary",
    )
    assert "engine.py" in prep.member_hints
    [record] = [r for r in state.authority_expansions if r.member_id == "Engine.added"]
    assert record.granted and record.source_origin == ae.ORIGIN_CANDIDATE
    assert record.member_in_pristine is False
    events = [e for e in state.run_events if e.kind == "authority.expansion"]
    assert events and events[0].details["source_origin"] == ae.ORIGIN_CANDIDATE
    assert json.dumps(events[0].details)  # persistable


def test_retry_member_hint_is_not_granted_under_deny_all(tmp_path):
    from test_val001_g1r3_retry_context import _minimal_attempt_ctx

    (tmp_path / "engine.py").write_text("class Engine:\n    def run(self):\n        return undefined_name\n")
    ctx = _minimal_attempt_ctx(tmp_path, write_scope_mode=WriteScopeMode.DENY_ALL)
    state = _retry_state("engine.py", 3)
    prep = _prepare_retry_context(
        state, ctx, target_files=["engine.py"], prompt_window=32768, model_identity="primary",
    )
    assert prep.member_hints == {}
    assert state.authority_expansions and all(
        r.reason_code == ae.REASON_OUT_OF_SCOPE for r in state.authority_expansions
    )
    assert ctx.allowed_write_relpaths == []


# --- production wiring: DEV-INV investigation merge ------------------------------------------


async def _investigate(tmp_path, known_target_files):
    (tmp_path / "a.py").write_text("class Foo:\n    def bar(self):\n        return 1\n")
    ctx = _devinv_ctx(tmp_path)
    ctx.kernel.config.autonomy.developer_investigation_enabled = True
    ctx.kernel.config.paths.memory = str(tmp_path)
    ctx.kernel.config.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    (tmp_path / "dependency_graph.db").write_bytes(b"")
    ctx.developer.llm = MagicMock()
    ctx.developer.llm.complete_with_tools = AsyncMock(side_effect=[
        _native_result("inspect_member", {"path": "a.py", "member_id": "Foo.bar"}),
        _propose_result(),
    ])
    state = GenerationState()
    kwargs = {"existing_code_context": "", "task_description": "t", "design_context": "d",
              "known_target_files": known_target_files}
    await _maybe_run_developer_investigation(state, ctx, kwargs, ctx.kernel.config.llm.model)
    return state, kwargs


@pytest.mark.asyncio
async def test_investigation_member_becomes_edit_authority_only_when_granted(tmp_path):
    state, kwargs = await _investigate(tmp_path, ["a.py"])
    assert state.known_target_context_items["a.py"].member_id == "Foo.bar"
    [record] = state.authority_expansions
    assert record.granted and record.evidence["source"] == "developer_investigation"
    assert "Foo" in kwargs["existing_code_context"]


@pytest.mark.asyncio
async def test_investigation_evidence_outside_the_targets_stays_read_only(tmp_path):
    state, kwargs = await _investigate(tmp_path, [])
    assert "a.py" not in state.known_target_context_items
    [record] = state.authority_expansions
    assert record.reason_code == ae.REASON_OUT_OF_SCOPE
    # still shown to the Developer as read-only evidence
    assert "Foo" in kwargs["existing_code_context"]
