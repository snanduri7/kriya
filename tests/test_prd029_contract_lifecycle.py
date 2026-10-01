"""PRD-029: ContractRegistry lifecycle - schema, deterministic derivation from
committed code evidence, the commit transaction (stage -> source bytes ->
promote -> settle), crash recovery at every boundary, the resume
fingerprint, and the workflow's deterministic stop.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS

from kriya.control.contracts import (
    CONTRACT_REGISTRY_CORRUPT,
    CONTRACT_REGISTRY_SCHEMA_VERSION,
    KIND_MILESTONE_CAPABILITY,
    KIND_PUBLIC_API,
    ContractRegistry,
    ContractState,
    registry_digest,
)
from kriya.control.persistence import (
    contract_registry_path,
    load_contract_registry,
    pending_contract_registry_path,
    save_contract_registry,
    scan_run_records,
)
from kriya.control.recovery import canonical_workspace, recover_workspace
from kriya.control.run_coordinator import begin_mutating_run, transition_mutating_run
from kriya.control.run_record import COMMIT_COMMITTED, RunLifecycle
from kriya.workflow import contract_lifecycle as cl
from kriya.workflow.edit_safety import StagedFileWrite, read_file_revision
from kriya.workflow.resume_fingerprints import (
    ARTIFACT_DEPENDENCIES,
    FINGERPRINT_NAMES,
    FingerprintStatus,
    compare_resume_fingerprints,
    contract_registry_fingerprint,
    fingerprint_block,
)
from kriya.workflow.terminal_commit import commit_terminal_candidate
from kriya.workflow.verification_binding import bind_candidate

ROOT = Path(__file__).resolve().parents[1]
OWNER = "src/Owner.java"
CALLER = "src/Caller.java"
OWNER_V1 = "public class Owner {\n    public int total() {\n        return 1;\n    }\n}\n"
OWNER_V2 = "public class Owner {\n    public long total() {\n        return 1L;\n    }\n}\n"
CALLER_SRC = "public class Caller {\n    int use(Owner o) {\n        return o.total();\n    }\n}\n"


def _auth(symbol="total", owner=OWNER):
    return SimpleNamespace(
        authorization_id=f"auth-{symbol}", provenance=SimpleNamespace(value="direct"),
        affected_owner=owner, affected_symbol=symbol, allowed_change_category=SimpleNamespace(value="modify"),
    )


def _workspace(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    (workspace / OWNER).write_text(OWNER_V1)
    (workspace / CALLER).write_text(CALLER_SRC)
    return canonical_workspace(str(workspace))


def _derive(workspace, *, verified=True, authorizations=None, registry=None, final=OWNER_V2):
    return cl.derive_contract_transition(
        workspace_path=workspace, registry=registry, original_contents={OWNER: OWNER_V1},
        final_contents={OWNER: final}, authorizations=authorizations if authorizations is not None else [_auth()],
        transaction_id="tx1", candidate_hash="hash1", downstream_verified=verified,
    )


# --- schema --------------------------------------------------------------------------------


def test_schema_one_payload_migrates_to_milestone_capability_records():
    legacy = {"contracts": {"M1:cap": [{
        "id": "M1:cap", "name": "cap", "provider_milestone_id": "M1", "shape": "s", "state": "implemented",
    }]}}
    registry = ContractRegistry.from_dict(legacy)
    [record] = registry.all_records()
    assert record.kind == KIND_MILESTONE_CAPABILITY and record.state is ContractState.IMPLEMENTED
    payload = registry.to_dict()
    assert payload["schema_version"] == CONTRACT_REGISTRY_SCHEMA_VERSION and payload["revision"] == 0
    assert ContractRegistry.from_dict(payload).digest() == registry.digest() == registry_digest(payload)


@pytest.mark.parametrize("payload", [
    {"schema_version": 99, "contracts": {}},
    {"schema_version": 2, "revision": -1, "contracts": {}},
    {"contracts": {"x": [{"id": "x", "name": "x", "provider_milestone_id": "", "shape": "s",
                          "state": "proposed", "kind": "mystery"}]}},
])
def test_unsupported_or_invalid_registry_payloads_are_corrupt(tmp_path, payload):
    path = contract_registry_path(str(tmp_path))
    os.makedirs(os.path.dirname(path))
    Path(path).write_text(json.dumps(payload))
    with pytest.raises(Exception) as exc_info:
        load_contract_registry(str(tmp_path))
    assert getattr(exc_info.value, "reason_code", None) == CONTRACT_REGISTRY_CORRUPT


# --- deterministic derivation ------------------------------------------------------------------


def test_authorized_change_creates_an_established_public_api_contract(tmp_path):
    workspace = _workspace(tmp_path)
    live = load_contract_registry(workspace)
    transition = _derive(workspace, registry=live)
    assert transition.created == ("api:src/Owner.java",) and not transition.changed and not transition.stale
    assert transition.before_digest == live.digest()
    assert live.all_records() == ()  # the live registry is never mutated
    assert transition.after_digest == registry_digest(transition.after_payload)
    after = ContractRegistry.from_dict(transition.after_payload)
    assert after.revision == 1 and after.source_revision == "tx1:hash1"
    record = after.get("api:src/Owner.java")
    assert record.kind == KIND_PUBLIC_API and record.owner == OWNER
    assert record.state is ContractState.IMPLEMENTED
    assert record.source_revision == "tx1:hash1"
    assert record.consumers == (CALLER,)
    assert record.consumers_complete is False
    assert record.consumer_provenance[0]["provenance"] == cl.CONSUMER_PROVENANCE_NAME_SCAN
    assert record.authorization["authorizations"][0]["authorization_id"] == "auth-total"
    assert transition.invalidated_consumers[0]["consumer"] == CALLER
    assert transition.downstream_verification == {
        "required": True, "satisfied": True, "satisfied_by": cl.DOWNSTREAM_VERIFIED_BY,
    }
    intent = transition.intent()
    assert intent["after_digest"] == transition.after_digest and intent["revision"] == 1


def test_invalidated_consumers_without_downstream_verification_refuse_the_transition(tmp_path):
    with pytest.raises(cl.ContractTransitionRefused) as exc_info:
        _derive(_workspace(tmp_path), verified=False)
    assert exc_info.value.reason_code == cl.CONTRACT_CONSUMER_VERIFICATION_MISSING


def test_unauthorized_change_is_never_recorded_as_contract_fact(tmp_path):
    assert _derive(_workspace(tmp_path), authorizations=[]) is None


def test_unchanged_signatures_and_test_files_produce_no_transition(tmp_path):
    workspace = _workspace(tmp_path)
    assert _derive(workspace, final=OWNER_V1.replace("return 1;", "return 2;")) is None
    assert cl.derive_contract_transition(
        workspace_path=workspace, registry=None,
        original_contents={"src/test/OwnerTest.java": OWNER_V1},
        final_contents={"src/test/OwnerTest.java": OWNER_V2}, authorizations=[_auth(owner="src/test/OwnerTest.java")],
        transaction_id="tx1", candidate_hash="h", downstream_verified=True,
    ) is None


def _established(workspace):
    registry = ContractRegistry.from_dict(_derive(workspace).after_payload)
    save_contract_registry(workspace, registry)
    return registry


def test_unauthorized_change_to_an_established_contract_marks_it_stale(tmp_path):
    workspace = _workspace(tmp_path)
    _established(workspace)
    transition = cl.derive_contract_transition(
        workspace_path=workspace, registry=None, original_contents={OWNER: OWNER_V2},
        final_contents={OWNER: OWNER_V2.replace("long total()", "double total()")}, authorizations=[],
        transaction_id="tx2", candidate_hash="h2", downstream_verified=True,
    )
    assert transition.stale == ("api:src/Owner.java",)
    record = ContractRegistry.from_dict(transition.after_payload).get("api:src/Owner.java")
    assert record.stale_reason == cl.STALE_UNAUTHORIZED_SOURCE_CHANGE
    assert record.invalidated_consumers[0]["reason"] == cl.INVALIDATED_BY_STALE_CONTRACT


def test_an_authorized_change_to_an_established_contract_is_a_new_revision(tmp_path):
    workspace = _workspace(tmp_path)
    _established(workspace)
    transition = cl.derive_contract_transition(
        workspace_path=workspace, registry=None, original_contents={OWNER: OWNER_V2},
        final_contents={OWNER: OWNER_V2.replace("long total()", "double total()")}, authorizations=[_auth()],
        transaction_id="tx2", candidate_hash="h2", downstream_verified=True,
    )
    assert transition.changed == ("api:src/Owner.java",)
    after = ContractRegistry.from_dict(transition.after_payload)
    assert [r.revision for r in after.history_for("api:src/Owner.java")] == ["v1", "v2"]
    assert after.get("api:src/Owner.java").state is ContractState.IMPLEMENTED
    assert after.revision == 2


# --- the commit transaction -------------------------------------------------------------------


def _writes(workspace):
    path = os.path.join(workspace, OWNER)
    return [StagedFileWrite(
        target_path=path, content=OWNER_V2, base_path=path, expected_base_revision=read_file_revision(path),
        expected_base_exists=True, content_bytes=OWNER_V2.encode(), mode=0o644,
    )]


def _builder(workspace, **kwargs):
    def build(*, candidate_hash):
        return cl.derive_contract_transition(
            workspace_path=workspace, registry=None, original_contents={OWNER: OWNER_V1},
            final_contents={OWNER: OWNER_V2}, authorizations=kwargs.get("authorizations", [_auth()]),
            transaction_id="tx1", candidate_hash=candidate_hash, downstream_verified=kwargs.get("verified", True),
        )
    return build


def _in_run(workspace, action):
    with begin_mutating_run(workspace) as context:
        transition_mutating_run(context, RunLifecycle.RUNNING)
        transition_mutating_run(context, RunLifecycle.CANDIDATE)
        return action()


def _record(workspace):
    [record] = scan_run_records(workspace).records
    return record


def test_committed_transition_is_bound_to_the_commit_and_recorded_in_the_run_record(tmp_path):
    workspace = _workspace(tmp_path)
    outcome = _in_run(workspace, lambda: commit_terminal_candidate(
        _writes(workspace), workspace_path=workspace, verified_candidate=bind_candidate(_writes(workspace), workspace), static_analysis=DISABLED_STATIC_ANALYSIS, transaction_id="tx1", contract_transition=_builder(workspace),
    ))
    assert outcome.committed and outcome.workspace_state == "COMMITTED"
    live = load_contract_registry(workspace)
    assert live.digest() == outcome.contract_registry["after_digest"]
    assert live.source_revision.startswith("tx1:")
    assert not os.path.exists(pending_contract_registry_path(workspace, "tx1"))
    record = _record(workspace)
    [cycle] = record.commits
    assert cycle["result"] == COMMIT_COMMITTED
    assert cycle["contract_registry"]["after_digest"] == live.digest()
    assert record.committed_contract_registry["after_digest"] == live.digest()


def test_refused_transition_refuses_the_commit_before_any_intent(tmp_path):
    workspace = _workspace(tmp_path)
    outcome = _in_run(workspace, lambda: commit_terminal_candidate(
        _writes(workspace), workspace_path=workspace, verified_candidate=bind_candidate(_writes(workspace), workspace), static_analysis=DISABLED_STATIC_ANALYSIS, transaction_id="tx1",
        contract_transition=_builder(workspace, verified=False),
    ))
    assert not outcome.committed and outcome.workspace_state == "UNCHANGED"
    assert outcome.reason_code == cl.CONTRACT_CONSUMER_VERIFICATION_MISSING
    assert Path(workspace, OWNER).read_text() == OWNER_V1
    assert _record(workspace).commits == []
    assert not os.path.exists(pending_contract_registry_path(workspace, "tx1"))


def test_corrupt_registry_refuses_the_commit_and_is_left_untouched(tmp_path):
    workspace = _workspace(tmp_path)
    path = contract_registry_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text("{bad")
    outcome = _in_run(workspace, lambda: commit_terminal_candidate(
        _writes(workspace), workspace_path=workspace, verified_candidate=bind_candidate(_writes(workspace), workspace), static_analysis=DISABLED_STATIC_ANALYSIS, transaction_id="tx1", contract_transition=_builder(workspace),
    ))
    assert outcome.reason_code == CONTRACT_REGISTRY_CORRUPT and not outcome.committed
    assert Path(path).read_text() == "{bad" and Path(workspace, OWNER).read_text() == OWNER_V1


def test_staging_failure_leaves_the_workspace_unchanged(tmp_path):
    workspace = _workspace(tmp_path)
    with patch("kriya.control.persistence.stage_pending_contract_registry", side_effect=OSError("disk full")):
        outcome = _in_run(workspace, lambda: commit_terminal_candidate(
            _writes(workspace), workspace_path=workspace, verified_candidate=bind_candidate(_writes(workspace), workspace), static_analysis=DISABLED_STATIC_ANALYSIS, transaction_id="tx1",
            contract_transition=_builder(workspace),
        ))
    assert outcome.reason_code == cl.CONTRACT_REGISTRY_STAGING_FAILED and outcome.workspace_state == "UNCHANGED"
    assert Path(workspace, OWNER).read_text() == OWNER_V1
    assert _record(workspace).commits[0]["result"] == "NOT_COMMITTED"


def test_promotion_failure_is_uncertain_never_success_and_recovery_completes_it(tmp_path):
    workspace = _workspace(tmp_path)
    before = load_contract_registry(workspace).digest()
    with patch("kriya.control.persistence.promote_pending_contract_registry", side_effect=OSError("io")):
        outcome = _in_run(workspace, lambda: commit_terminal_candidate(
            _writes(workspace), workspace_path=workspace, verified_candidate=bind_candidate(_writes(workspace), workspace), static_analysis=DISABLED_STATIC_ANALYSIS, transaction_id="tx1",
            contract_transition=_builder(workspace),
        ))
    assert not outcome.committed and outcome.workspace_state == "UNCERTAIN"
    assert outcome.reason_code == cl.CONTRACT_REGISTRY_TRANSITION_INCOMPLETE
    assert Path(workspace, OWNER).read_text() == OWNER_V2      # the source landed
    assert load_contract_registry(workspace).digest() == before  # the registry did not
    assert _record(workspace).lifecycle_state is RunLifecycle.UNCERTAIN

    report = recover_workspace(workspace)
    assert report.errors == []
    assert report.contract_transitions[0]["outcome"] == cl.TRANSITION_PROMOTED
    record = _record(workspace)
    assert load_contract_registry(workspace).digest() == record.commits[0]["contract_registry"]["after_digest"]
    assert record.lifecycle_state is RunLifecycle.RECOVERED and record.terminal_status != "SUCCESS"
    assert not os.path.exists(pending_contract_registry_path(workspace, "tx1"))


def test_recovery_refuses_a_registry_changed_behind_the_transaction(tmp_path):
    workspace = _workspace(tmp_path)
    with patch("kriya.control.persistence.promote_pending_contract_registry", side_effect=OSError("io")):
        _in_run(workspace, lambda: commit_terminal_candidate(
            _writes(workspace), workspace_path=workspace, verified_candidate=bind_candidate(_writes(workspace), workspace), static_analysis=DISABLED_STATIC_ANALYSIS, transaction_id="tx1",
            contract_transition=_builder(workspace),
        ))
    foreign = ContractRegistry()
    foreign.register("M9:cap", "cap", "M9", "other")
    save_contract_registry(workspace, foreign)
    report = recover_workspace(workspace)
    assert report.contract_transitions[0]["outcome"] == cl.TRANSITION_NEEDS_REVIEW
    assert report.errors
    assert _record(workspace).lifecycle_state is RunLifecycle.UNCERTAIN  # left open for review


_CRASH_SCRIPT = r'''
import os, sys
from types import SimpleNamespace
import kriya.control.persistence as persistence
from kriya.control.run_coordinator import begin_mutating_run, transition_mutating_run
from kriya.control.run_record import RunLifecycle
from kriya.workflow import contract_lifecycle as cl
from kriya.workflow.edit_safety import StagedFileWrite, read_file_revision
from kriya.workflow.terminal_commit import commit_terminal_candidate
from kriya.static_analysis.service import commit_guard
from kriya.workflow.verification_binding import bind_candidate

workspace, crash_at = sys.argv[1], sys.argv[2]
OWNER = "src/Owner.java"
V1 = open(os.path.join(workspace, OWNER)).read()
V2 = V1.replace("int total()", "long total()").replace("return 1;", "return 1L;")
auth = SimpleNamespace(authorization_id="a", provenance=SimpleNamespace(value="direct"), affected_owner=OWNER,
                       affected_symbol="total", allowed_change_category=SimpleNamespace(value="modify"))
real_promote = persistence.promote_pending_contract_registry
real_replace = os.replace

def promote(*args, **kwargs):
    if crash_at == "before_promote":
        os._exit(9)
    real_promote(*args, **kwargs)
    if crash_at == "after_promote":
        os._exit(9)

def crashing_replace(src, dst):
    if crash_at == "first_source_byte" and os.path.basename(src).startswith(".kriya-stage-"):
        os._exit(9)
    return real_replace(src, dst)

persistence.promote_pending_contract_registry = promote
os.replace = crashing_replace
path = os.path.join(workspace, OWNER)
writes = [StagedFileWrite(target_path=path, content=V2, base_path=path, expected_base_revision=read_file_revision(path),
                          expected_base_exists=True, content_bytes=V2.encode(), mode=0o644)]

def build(*, candidate_hash):
    return cl.derive_contract_transition(
        workspace_path=workspace, registry=None, original_contents={OWNER: V1}, final_contents={OWNER: V2},
        authorizations=[auth], transaction_id="tx1", candidate_hash=candidate_hash, downstream_verified=True,
        capability_contracts=("M1:Pricing",))

with begin_mutating_run(workspace) as ctx:
    transition_mutating_run(ctx, RunLifecycle.RUNNING)
    transition_mutating_run(ctx, RunLifecycle.CANDIDATE)
    commit_terminal_candidate(writes, workspace_path=ctx.workspace_path, verified_candidate=bind_candidate(writes, ctx.workspace_path), static_analysis=commit_guard(None, None), transaction_id="tx1", contract_transition=build)
os._exit(0)
'''


def _crash(workspace, crash_at):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", _CRASH_SCRIPT, workspace, crash_at],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 9, result.stdout + result.stderr


@pytest.mark.parametrize(("crash_at", "expected", "source_after"), [
    ("first_source_byte", cl.TRANSITION_DISCARDED, OWNER_V1),
    ("before_promote", cl.TRANSITION_PROMOTED, OWNER_V2),
    ("after_promote", cl.TRANSITION_ALREADY_APPLIED, OWNER_V2),
])
def test_a_crash_at_every_boundary_recovers_to_an_exact_registry(tmp_path, crash_at, expected, source_after):
    """The directive's three crash windows, with a milestone capability in
    the same transaction: before the source commit, after it but before the
    registry is persisted, and after the registry but before the RunRecord
    settles."""
    workspace = _workspace(tmp_path)
    planned = ContractRegistry()
    planned.register("M1:Pricing", "Pricing", "M1", "pricing")  # PROPOSED at plan time
    save_contract_registry(workspace, planned)
    before = load_contract_registry(workspace).digest()
    _crash(workspace, crash_at)
    # Before recovery: the intent names both exact registry identities.
    [cycle] = _record(workspace).commits
    assert cycle["contract_registry"]["before_digest"] == before
    assert cycle["contract_registry"]["established_capabilities"] == ["M1:Pricing"]
    assert _record(workspace).terminal_status != "SUCCESS"
    report = recover_workspace(workspace)
    assert report.errors == []
    [transition] = report.contract_transitions
    assert transition["outcome"] == expected
    assert Path(workspace, OWNER).read_text() == source_after
    live = load_contract_registry(workspace).digest()
    record = _record(workspace)
    capability = load_contract_registry(workspace).get("M1:Pricing")
    if source_after == OWNER_V2:
        assert live == record.commits[0]["contract_registry"]["after_digest"]
        assert capability.state is ContractState.IMPLEMENTED
    else:
        assert live == before
        assert capability.state is ContractState.PROPOSED
    assert record.terminal_status != "SUCCESS"
    assert not os.path.exists(pending_contract_registry_path(workspace, "tx1"))
    # No duplicate transition: a second recovery changes nothing.
    again = recover_workspace(workspace)
    assert again.contract_transitions == [] and load_contract_registry(workspace).digest() == live


# --- resume fingerprint ------------------------------------------------------------------------


def test_contract_registry_is_a_planning_and_candidate_resume_dependency():
    assert "contract_registry" in FINGERPRINT_NAMES
    for artifact in ("plan", "design", "candidate", "candidate_gate_outcomes"):
        assert "contract_registry" in ARTIFACT_DEPENDENCIES[artifact]


def test_registry_fingerprint_absent_changed_and_corrupt(tmp_path):
    workspace = _workspace(tmp_path)
    empty = contract_registry_fingerprint(workspace)
    assert empty.available and empty.value == ContractRegistry().digest()
    established = _established(workspace)
    changed = contract_registry_fingerprint(workspace)
    assert changed.value == established.digest() != empty.value
    [comparison] = [c for c in compare_resume_fingerprints(
        fingerprint_block({"contract_registry": changed}), {"contract_registry": empty}, {"plan"},
    ) if c.name == "contract_registry"]
    assert comparison.status is FingerprintStatus.CHANGED and comparison.invalidates
    Path(contract_registry_path(workspace)).write_text("{bad")
    corrupt = contract_registry_fingerprint(workspace)
    assert not corrupt.available
    [comparison] = [c for c in compare_resume_fingerprints(
        fingerprint_block({"contract_registry": changed}), {"contract_registry": corrupt}, {"plan"},
    ) if c.name == "contract_registry"]
    assert comparison.status is FingerprintStatus.UNVERIFIED and comparison.invalidates


# --- the workflow's deterministic stop ----------------------------------------------------------


def test_an_illegal_registry_lifecycle_step_is_a_typed_refusal(tmp_path):
    from kriya.control.contracts import ContractStateError

    workspace = _workspace(tmp_path)
    _seed_planned_capability(workspace)
    before = Path(contract_registry_path(workspace)).read_bytes()
    with patch.object(ContractRegistry, "freeze", side_effect=ContractStateError("freeze refused")), \
         pytest.raises(cl.ContractTransitionRefused) as refused:
        _derive(workspace)
    assert refused.value.reason_code == cl.CONTRACT_REGISTRY_TRANSITION_INVALID
    assert refused.value.reason_code in cl.CONTRACT_REGISTRY_STOP_REASON_CODES
    assert Path(contract_registry_path(workspace)).read_bytes() == before


def test_a_coding_error_inside_the_derivation_is_never_turned_into_a_refusal(tmp_path):
    workspace = _workspace(tmp_path)
    _seed_planned_capability(workspace)
    with patch.object(ContractRegistry, "freeze", side_effect=TypeError("bug")), pytest.raises(TypeError):
        _derive(workspace)


def _seed_planned_capability(workspace):
    planned = ContractRegistry()
    planned.register("M1:Pricing", "Pricing", "M1", "pricing")  # PROPOSED at plan time
    save_contract_registry(workspace, planned)


def _derive_with_capability():
    """The real derivation, with a planned capability to establish, so the
    commit carries a real (non-empty) registry transition."""
    real = cl.derive_contract_transition
    return lambda **kwargs: real(**{**kwargs, "capability_contracts": ("M1:Pricing",)})


def _refuse_consumers(_workspace):
    refusal = cl.ContractTransitionRefused(cl.CONTRACT_CONSUMER_VERIFICATION_MISSING, "consumers unverified")
    return [patch("kriya.workflow.workflow.derive_contract_transition", side_effect=refusal)]


def _corrupt_registry(workspace):
    path = contract_registry_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text("{not json", encoding="utf-8")
    return []


def _illegal_lifecycle_step(workspace):
    from kriya.control.contracts import ContractStateError

    _seed_planned_capability(workspace)
    return [
        patch("kriya.workflow.workflow.derive_contract_transition", side_effect=_derive_with_capability()),
        patch.object(ContractRegistry, "approve", side_effect=ContractStateError("approve refused")),
    ]


def _staging_fails(workspace):
    _seed_planned_capability(workspace)
    return [
        patch("kriya.workflow.workflow.derive_contract_transition", side_effect=_derive_with_capability()),
        patch("kriya.control.persistence.stage_pending_contract_registry", side_effect=OSError("disk full")),
    ]


def _live_registry_mismatch(workspace):
    from kriya.control.contracts import ContractRegistryTransitionError

    _seed_planned_capability(workspace)
    return [
        patch("kriya.workflow.workflow.derive_contract_transition", side_effect=_derive_with_capability()),
        patch("kriya.control.persistence.promote_pending_contract_registry",
              side_effect=ContractRegistryTransitionError("the live registry changed")),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("arrange", "reason", "source_committed"), [
    (_refuse_consumers, cl.CONTRACT_CONSUMER_VERIFICATION_MISSING, False),
    (_corrupt_registry, CONTRACT_REGISTRY_CORRUPT, False),
    (_illegal_lifecycle_step, cl.CONTRACT_REGISTRY_TRANSITION_INVALID, False),
    (_staging_fails, cl.CONTRACT_REGISTRY_STAGING_FAILED, False),
    # The source bytes landed and the cycle stays open for `runs recover`.
    (_live_registry_mismatch, cl.CONTRACT_REGISTRY_TRANSITION_INCOMPLETE, True),
], ids=["consumer_verification_missing", "corrupt", "invalid_transition", "staging_failed",
        "promotion_state_mismatch"])
async def test_a_registry_refusal_is_a_terminal_stop_never_a_retry(tmp_path, arrange, reason, source_committed):
    """Every deterministic registry refusal ends the run with the typed
    contract-registry failure: one Developer call, no retry, never
    ``no_progress`` and never ``quality_gates_exhausted``. (An unauthorized
    public API change is different: PRD-023 names the candidate change, so
    the model can repair it, and it stays retryable.)"""
    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=["Step 1: write code", "Design: math.py"] + ["Review: done"] * 5)
    we = WorkflowEngine(Kernel(config=cfg), llm)
    we.developer.run_generation = AsyncMock(
        return_value=[{"filepath": "math.py", "content": "def add(a, b):\n    return a + b\n"}],
    )
    # A git workspace, so the run commits through the terminal commit seam.
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    workspace = str(tmp_path)
    patches = arrange(workspace)
    live = Path(contract_registry_path(workspace))
    registry_before = live.read_bytes() if live.exists() else None
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": "ok"}):
        for extra in patches:
            extra.start()
        try:
            res = await we.run_generation_workflow(goal="Create math library", workspace_path=workspace)
        finally:
            for extra in patches:
                extra.stop()

    assert res["quality_gates_passed"] is False
    assert res["failure_category"] == "contract_registry_blocked"
    assert res["failure_category"] not in ("no_progress", "quality_gates_exhausted")
    assert res["environment_failure"].startswith(reason)
    assert res["contract_registry"] is None
    assert we.developer.run_generation.await_count == 1  # no redundant model retry
    assert (tmp_path / "math.py").exists() is source_committed
    # The live registry is never rewritten by a refused transition.
    assert (live.read_bytes() if live.exists() else None) == registry_before


@pytest.mark.asyncio
async def test_a_successful_run_reports_its_contract_registry_transition_field(tmp_path):
    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=["Step 1: write code", "Design: math.py"] + ["Review: done"] * 5)
    we = WorkflowEngine(Kernel(config=cfg), llm)
    we.developer.run_generation = AsyncMock(
        return_value=[{"filepath": "math.py", "content": "def add(a, b):\n    return a + b\n"}],
    )
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": "ok"}):
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    assert "contract_registry" in res and res["contract_registry"] is None  # greenfield: no contract changed


def test_enforce_builder_requires_the_final_subtasks_full_suite_with_tests(tmp_path):
    from kriya.workflow.workflow_controller import _enforce_contract_transition

    workspace = _workspace(tmp_path)
    writes = _writes(workspace)
    plan = SimpleNamespace(subtasks=[])
    for evidence, expected in (
        ([{"type": "regression_test", "status": "PASS_WITH_TESTS"}], True),
        ([{"type": "regression_test", "status": "NO_TESTS_EXECUTED"}], False),
        ([], False),
    ):
        builder = _enforce_contract_transition(
            writes, workspace_path=workspace, goal="g", plan=plan, transaction_id="tx1",
            subtask_call_results=[{"deterministic_gate_evidence": evidence}],
        )
        assert builder.keywords["downstream_verified"] is expected
        assert builder.keywords["original_contents"] == {OWNER: OWNER_V1}
        assert builder.keywords["final_contents"] == {OWNER: OWNER_V2}


def test_uncommitted_cycle_discards_only_while_the_registry_is_still_the_before_state(tmp_path):
    from kriya.control.persistence import stage_pending_contract_registry

    workspace = _workspace(tmp_path)
    transition = _derive(workspace)
    stage_pending_contract_registry(workspace, "tx1", transition.after_payload)
    assert cl.complete_contract_transition(workspace, "tx1", transition.intent(), committed=False) == \
        cl.TRANSITION_DISCARDED
    assert not os.path.exists(pending_contract_registry_path(workspace, "tx1"))

    stage_pending_contract_registry(workspace, "tx1", transition.after_payload)
    _established(workspace)  # the live registry moved on without this transaction
    assert cl.complete_contract_transition(workspace, "tx1", transition.intent(), committed=False) == \
        cl.TRANSITION_NEEDS_REVIEW
    assert os.path.exists(pending_contract_registry_path(workspace, "tx1"))


def test_promotion_rejects_a_staged_payload_that_is_not_the_recorded_after_state(tmp_path):
    from kriya.control.contracts import ContractRegistryTransitionError
    from kriya.control.persistence import promote_pending_contract_registry, stage_pending_contract_registry

    workspace = _workspace(tmp_path)
    transition = _derive(workspace)
    tampered = dict(transition.after_payload, revision=transition.after_payload["revision"] + 5)
    stage_pending_contract_registry(workspace, "tx1", tampered)
    with pytest.raises(ContractRegistryTransitionError):
        promote_pending_contract_registry(
            workspace, "tx1", before_digest=transition.before_digest, after_digest=transition.after_digest,
        )
    assert load_contract_registry(workspace).digest() == transition.before_digest


def test_generation_resume_fingerprints_carry_the_live_registry_identity(tmp_path):
    from kriya.config import AppConfig
    from kriya.workflow.resume_fingerprints import generation_resume_fingerprints

    workspace = _workspace(tmp_path)
    established = _established(workspace)
    fingerprints = generation_resume_fingerprints(AppConfig(), workspace, goal="g")
    assert fingerprints["contract_registry"].value == established.digest()


# --- milestone bookkeeping never overwrites committed contract records -----------------------


@pytest.mark.asyncio
async def test_milestone_completion_bookkeeping_preserves_contracts_its_unit_committed(tmp_path):
    """A milestone unit's commit promotes a public_api record into the live
    registry; the milestone's own capability bookkeeping afterwards must
    build on the LIVE registry, never write a start-of-run copy over it."""
    from unittest.mock import MagicMock

    from _strict_doubles import strict_engine
    from test_milestones import mkv2

    from kriya.workflow.milestones import MilestoneRunState, run_milestones

    workspace = _workspace(tmp_path)
    calls = {"n": 0}

    async def unit_commits_a_contract(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # What the commit seam promotes: ONE transition from the live
            # registry carrying both the API contract and the capabilities
            # this milestone unit provides.
            transition = cl.derive_contract_transition(
                workspace_path=workspace, registry=None, original_contents={OWNER: OWNER_V1},
                final_contents={OWNER: OWNER_V2}, authorizations=[_auth()], transaction_id="tx1",
                candidate_hash="hash1", downstream_verified=True,
                capability_contracts=kwargs["work_unit"].provided_capabilities,
            )
            save_contract_registry(workspace, ContractRegistry.from_dict(transition.after_payload))
        return {"quality_gates_passed": True, "design": "d", "files": [OWNER]}

    we = strict_engine()
    we.run_generation_workflow = AsyncMock(side_effect=unit_commits_a_contract)
    we.run_verifier = MagicMock()
    we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=[
        mkv2("M1", goal="g1", provides=[{"name": "Pricing"}]),
    ])
    result = await run_milestones(we, state, workspace)

    assert result["status"] == "success"
    live = load_contract_registry(workspace)
    assert live.get("api:src/Owner.java").source_revision == "tx1:hash1"
    assert live.get("M1:Pricing").state is ContractState.IMPLEMENTED



# --- milestone capabilities are part of the commit transaction ---------------------------------


def _planned_registry(workspace, *capabilities):
    registry = ContractRegistry()
    for contract_id in capabilities:
        milestone, name = contract_id.split(":")
        registry.register(contract_id, name, milestone, name)
    save_contract_registry(workspace, registry)
    return registry


def test_capabilities_are_established_by_the_commit_transition(tmp_path):
    workspace = _workspace(tmp_path)
    _planned_registry(workspace, "M1:Pricing")
    transition = cl.derive_contract_transition(
        workspace_path=workspace, registry=None, original_contents={}, final_contents={}, authorizations=[],
        transaction_id="tx1", candidate_hash="h1", downstream_verified=False,
        capability_contracts=("M1:Pricing", "M9:NeverRegistered"),
    )
    assert transition.established_capabilities == ("M1:Pricing",)
    assert transition.created == transition.changed == transition.stale == ()
    record = ContractRegistry.from_dict(transition.after_payload).get("M1:Pricing")
    assert record.state is ContractState.IMPLEMENTED and record.source_revision == "tx1:h1"
    assert ContractRegistry.from_dict(transition.after_payload).try_get("M9:NeverRegistered") is None
    assert transition.intent()["established_capabilities"] == ["M1:Pricing"]


def test_an_already_established_capability_is_not_transitioned_twice(tmp_path):
    workspace = _workspace(tmp_path)
    registry = _planned_registry(workspace, "M1:Pricing")
    for step in (registry.approve, registry.freeze, registry.mark_implemented):
        step("M1:Pricing")
    save_contract_registry(workspace, registry)
    assert cl.derive_contract_transition(
        workspace_path=workspace, registry=None, original_contents={}, final_contents={}, authorizations=[],
        transaction_id="tx2", candidate_hash="h2", downstream_verified=False, capability_contracts=("M1:Pricing",),
    ) is None


def test_a_committed_milestone_without_its_capability_transition_is_incomplete(tmp_path, monkeypatch):
    from kriya.workflow import milestones as milestones_module
    from kriya.workflow.execution_plan import WorkUnitRole

    workspace = _workspace(tmp_path)
    _planned_registry(workspace, "M1:Pricing")
    from test_milestones import mkv2

    milestone = mkv2("M1", goal="g1", provides=[{"name": "Pricing"}])
    driver = milestones_module._MilestonePlanDriver.__new__(milestones_module._MilestonePlanDriver)
    driver.workspace_path = workspace
    driver.contract_registry = load_contract_registry(workspace)
    driver.run_state = SimpleNamespace(established_dependencies={})
    driver.by_id = {"M1": milestone}
    driver._cycles_before = {"M1": 0}
    unit = SimpleNamespace(id="M1", role=WorkUnitRole.PRIMARY)

    monkeypatch.setattr(milestones_module, "check_dependency_regression", lambda *a: [])
    monkeypatch.setattr(milestones_module, "owning_run_commits",
                        lambda ws: ("run", [{"transaction_id": "tx1", "result": COMMIT_COMMITTED}]))
    import asyncio

    result = asyncio.run(driver.check_passed_unit(unit, {"quality_gates_passed": True, "status": "success"}))
    assert result["quality_gates_passed"] is False
    assert result["status"] == "contract_registry_incomplete"
    assert result["reason_codes"] == [cl.CONTRACT_REGISTRY_TRANSITION_INCOMPLETE]
    assert result["unestablished_capabilities"] == ["M1:Pricing"]

    # The same unit with its capability established by the transaction passes.
    registry = load_contract_registry(workspace)
    for step in (registry.approve, registry.freeze, registry.mark_implemented):
        step("M1:Pricing")
    save_contract_registry(workspace, registry)
    ok = asyncio.run(driver.check_passed_unit(unit, {"quality_gates_passed": True, "status": "success"}))
    assert ok["quality_gates_passed"] is True

    # A unit that committed nothing establishes nothing and is not failed for it.
    monkeypatch.setattr(milestones_module, "owning_run_commits", lambda ws: ("run", []))
    _planned_registry(workspace, "M1:Pricing")
    idle = asyncio.run(driver.check_passed_unit(unit, {"quality_gates_passed": True, "status": "success"}))
    assert idle["quality_gates_passed"] is True


@pytest.mark.asyncio
async def test_a_milestone_units_commit_establishes_its_capabilities(tmp_path):
    """End to end through run_generation_workflow: the unit's own terminal
    commit carries WorkUnitInvocation.provided_capabilities into the
    registry transition, committed with its source."""
    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.execution_plan import PlanSourceKind
    from kriya.workflow.plan_executor import WorkUnitInvocation
    from kriya.workflow.workflow import WorkflowEngine

    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    _planned_registry(str(tmp_path), "M1:MathLib")
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=["Step 1: write code", "Design: math.py"] + ["Review: done"] * 5)
    we = WorkflowEngine(Kernel(config=cfg), llm)
    we.developer.run_generation = AsyncMock(
        return_value=[{"filepath": "math.py", "content": "def add(a, b):\n    return a + b\n"}],
    )
    invocation = WorkUnitInvocation(
        PlanSourceKind.MILESTONE, "plan-1", "M1", provided_capabilities=("M1:MathLib",),
        authoritative_goal="Create math library",
    )
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": "ok"}):
        res = await we.run_generation_workflow(goal="M1: math", workspace_path=str(tmp_path), work_unit=invocation)

    assert res["quality_gates_passed"] is True, res.get("failure_category")
    assert res["contract_registry"]["established_capabilities"] == ["M1:MathLib"]
    assert load_contract_registry(str(tmp_path)).get("M1:MathLib").state is ContractState.IMPLEMENTED
