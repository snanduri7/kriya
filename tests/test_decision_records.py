"""Typed decision records (full modularization D1; kriya/core/attempt_evidence/decisions.py, MODULE-CONTRACTS §5/§12).

Each record is emitted through a REAL run scope (``begin_mutating_run`` + the attempt-evidence scopes) into a real store and read back
through the reader: the record must arrive under its own kind with its payload verbatim and NO recorder gap, so a kind missing from
``model.KINDS`` fails here (the writer refuses it, ``emit_decision`` never raises, and the refusal would only show as a gap). The seal
stays complete, ``explain`` renders each new kind in its own section (and nothing new for a store without them), an unknown kind is
still refused, and no production code emits the new kinds yet (F5/G5/H5 add the emitters).
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest
from _strict_doubles import strict_config

from kriya.control.run_coordinator import begin_mutating_run
from kriya.core.attempt_evidence import decisions as d
from kriya.core.attempt_evidence import explain, model, reader, scope
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.core.state_paths import ENV_STATE_DIR

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "ab" * 32
NEW_KINDS = ("attribution.decision", "retry_target.decision", "candidate_transaction.decision", "budget.decision")


def _source(provenance="deterministic_exact_locator", evidence_class="derived_deterministic", files=("src/ProtocolParser.java",)):
    return d.AttributionSourceRef(tier="compiler", evidence_class=evidence_class, provenance=provenance, authority_level=3, files=files,
                                  confidence="high", evidence_ref="gate.result#12", exhausted=False)


def _budget(action="admit"):
    view = d.BudgetViewRef(phase="fallback_reserve", allowed_seconds=900.0, consumed_seconds=544.5, remaining_seconds=355.5,
                           liveness_remaining=2155.5)
    return d.BudgetDecisionRecord(decision=action, reason_code="BUDGET_ADMITTED", inputs_digest=DIGEST, attempt=3, unit="direct",
                                  phase="fallback_reserve", estimate_seconds=300.0, estimate_source="configured_fallback", provider="local",
                                  model="qwen3-coder:30b", file_count=2, view=view)


def _records():
    return {
        "attribution.decision": d.AttributionDecisionRecord(
            decision="attributed", reason_code="DETERMINISTIC_LOCATOR", inputs_digest=DIGEST, attempt=2, unit="direct",
            finding_id="F-1", selected=_source(), considered=(_source(),),
            ignored_advisory=(_source("model_self_diagnosis", "model_claimed", ("src/Protocol.java",)),),
            precedence_rule="derived_deterministic>model_claimed"),
        "retry_target.decision": d.RetryTargetDecisionRecord(
            decision="original", reason_code="LOCATOR_AUTHORITATIVE", inputs_digest=DIGEST, attempt=2, unit="direct",
            findings=("F-1",), attribution=("F-1",), selected_files=("src/ProtocolParser.java",), read_only_context=("src/Protocol.java",),
            ignored_advisory=(("src/Protocol.java", "model mention, advisory"),), rejected_candidates=(("src/Other.java", "outside scope"),),
            authoritative_locator=d.LocatorRef(file="src/ProtocolParser.java", line=10, symbol=None, evidence_class="derived_deterministic",
                                               source="javac"),
            advisory_evidence=(_source("model_self_diagnosis", "model_claimed", ("src/Protocol.java",)),),
            scope_filter="work_unit_write_scope", precedence_rule="locator_first"),
        "candidate_transaction.decision": d.CandidateTransactionRecord(
            decision="partially_staged", reason_code="INDEPENDENT_VALID_STAGED", inputs_digest=DIGEST, attempt=3, unit="direct",
            transaction_id="tx-3", proposals=(
                d.FileProposalRef(path="src/ProtocolParser.java", response_seq=4, parse="ok", reason_code=None, staged_digest=DIGEST,
                                  findings_addressed=("F-1",)),
                d.FileProposalRef(path="src/Protocol.java", response_seq=5, parse="invalid", reason_code="INVALID_EDIT_PROTOCOL",
                                  staged_digest=None)),
            independence=(("src/ProtocolParser.java", "src/Protocol.java", "independent", "no shared symbol edge"),),
            staged=("src/ProtocolParser.java",), refused=(("src/Protocol.java", "INVALID_EDIT_PROTOCOL"),), atomic_groups=(),
            staged_identity=DIGEST),
        "budget.decision": _budget(),
        "fallback.decision": d.FallbackDecisionRecord(
            decision="selected", reason_code="FALLBACK_ELIGIBLE", inputs_digest=DIGEST, attempt=3, unit="direct", role="developer",
            candidates=("qwen3-coder:30b", "qwen3.6"), selected="qwen3-coder:30b", skipped=(("qwen3.6", "not qualified"),),
            eligibility_rule="qualified_and_within_budget", timing_estimate=_budget()),
    }


def _git_workspace(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (Path(path) / "tracked.txt").write_text("v1\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=path, check=True)
    return str(path)


def _emit_in_a_run(tmp_path, records, *, inside_attempt=True):
    """Emit ``records`` through the real scopes of one run; return (run, records read back)."""
    workspace = _git_workspace(tmp_path / "ws")
    cfg = strict_config()
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "direct", "direct", {"plan_id": None}) as closing:
            if inside_attempt:
                with scope.attempt_scope(lambda: 2):
                    for record in records:
                        d.emit_decision(record)
            else:
                for record in records:
                    d.emit_decision(record)
            closing["status"] = "success"
    run = reader.open_run(os.environ[ENV_STATE_DIR], context.run_id)
    return run, list(run.records())


def test_the_record_kinds_are_the_four_new_store_kinds_and_the_existing_fallback_kind():
    assert d.DECISION_KINDS == NEW_KINDS + ("fallback.decision",)
    assert {cls.kind for cls in d.RECORD_TYPES} == set(d.DECISION_KINDS)
    assert set(_records()) == set(d.DECISION_KINDS)


@pytest.mark.parametrize("kind", d.DECISION_KINDS)
def test_each_record_round_trips_through_a_real_run_scope_writer_and_reader(tmp_path, kind):
    record = _records()[kind]
    run, records = _emit_in_a_run(tmp_path, [record])
    found = [r for r in records if r["kind"] == kind]
    gaps = [r for r in records if r["kind"] == "recorder.gap"]
    assert gaps == [], gaps  # a kind the store does not know is refused and becomes a gap: this is where a missing KINDS entry shows
    assert len(found) == 1, [r["kind"] for r in records]
    assert found[0]["payload"] == record.payload()
    assert found[0]["payload"]["decision_schema"] == d.DECISION_SCHEMA and found[0]["payload"]["decision"] == record.decision
    assert found[0]["attempt_number"] == 2 and found[0]["unit_id"] == "direct"
    assert found[0]["provenance"] == model.OBSERVED and found[0]["blobs"] == {} and found[0]["content_digests"] == {}
    verification = run.verify()
    assert verification.status == reader.VERIFIED
    assert verification.seal["complete"] is True and verification.seal["gaps"] == 0
    assert verification.seal["final_seq"] == len(records)


def test_explain_renders_each_new_kind_in_its_own_attempt_section(tmp_path):
    records = _records()
    run, _ = _emit_in_a_run(tmp_path, list(records.values()))
    answer = explain.explain_run(os.environ[ENV_STATE_DIR], run.run_id)
    attempt = answer["attempts"][0]
    sections = {label: kind for label, kind, _fields in explain.DECISION_SECTIONS}
    assert sorted(sections.values()) == sorted(NEW_KINDS)
    for label, kind in sections.items():
        assert len(attempt[label]) == 1, label
        brief = attempt[label][0]
        assert brief["decision"] == records[kind].decision and brief["reason_code"] == records[kind].reason_code
    assert attempt["retry_target_decisions"][0]["origin"] == "original"
    assert attempt["candidate_transactions"][0]["staged"] == ["src/ProtocolParser.java"]
    assert attempt["budget_decisions"][0]["action"] == "admit" and attempt["budget_decisions"][0]["view"]["remaining_seconds"] == 355.5
    # The typed fallback record is a fallback.decision: it answers Q8 like every fallback record, as phase "typed".
    q8 = attempt["answers"]["Q8"]
    assert q8["status"] == explain.RECORDED and [item["phase"] for item in q8["items"]] == [d.FALLBACK_TYPED_PHASE]
    assert "decisions_outside_attempts" not in answer


def test_a_decision_outside_an_attempt_is_rendered_at_run_level(tmp_path):
    run, _ = _emit_in_a_run(tmp_path, [_records()["budget.decision"]], inside_attempt=False)
    answer = explain.explain_run(os.environ[ENV_STATE_DIR], run.run_id)
    assert answer["decisions_outside_attempts"]["budget_decisions"][0]["phase"] == "fallback_reserve"


def test_a_store_without_decision_records_explains_exactly_as_before(tmp_path):
    run, records = _emit_in_a_run(tmp_path, [])
    assert not {r["kind"] for r in records} & set(NEW_KINDS)
    answer = explain.explain_run(os.environ[ENV_STATE_DIR], run.run_id)
    labels = {label for label, _kind, _fields in explain.DECISION_SECTIONS}
    assert "decisions_outside_attempts" not in answer
    assert all(not labels & set(entry) for entry in answer["attempts"])


def test_an_unknown_kind_is_still_refused_by_the_writer_and_becomes_a_gap_through_emit_decision(tmp_path):
    writer = AttemptEvidenceWriter(str(tmp_path), "run-1", capture=model.CAPTURE_FULL, manifest={"run_kind": "generate"})
    with pytest.raises(ValueError, match="unknown record kind"):
        writer.append("decision.unknown", {})

    class _Unknown:
        kind = "decision.unknown"

        @staticmethod
        def payload():
            return {"decision": "x"}

    run, records = _emit_in_a_run(tmp_path, [_Unknown()])
    gaps = [r["payload"] for r in records if r["kind"] == "recorder.gap"]
    assert [gap["lost_kind"] for gap in gaps] == ["decision.unknown"]
    assert run.verify().seal["complete"] is False


def test_emit_decision_never_raises_and_records_nothing_for_a_record_that_cannot_build_its_payload(tmp_path):
    class _Broken:
        kind = "budget.decision"

        @staticmethod
        def payload():
            raise TypeError("broken")

    _run, records = _emit_in_a_run(tmp_path, [_Broken()])
    assert not [r for r in records if r["kind"] in NEW_KINDS or r["kind"] == "recorder.gap"]


def test_emit_decision_outside_any_run_scope_is_a_no_op():
    d.emit_decision(_records()["attribution.decision"])  # no open store: nothing to record, nothing raised
    assert scope.store_pointer() == {"status": "NO_RUN_SCOPE"}


@pytest.mark.parametrize("build", [
    lambda: d.AttributionDecisionRecord(decision="guessed", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None, finding_id="F",
                                        selected=_source(), precedence_rule="p"),
    lambda: d.AttributionDecisionRecord(decision="attributed", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None, finding_id="F",
                                        selected=None, precedence_rule="p"),
    lambda: d.AttributionDecisionRecord(decision="attributed", reason_code="R", inputs_digest="md5:00", attempt=1, unit=None,
                                        finding_id="F", selected=_source(), precedence_rule="p"),
    lambda: d.AttributionDecisionRecord(decision="attributed", reason_code="", inputs_digest=DIGEST, attempt=1, unit=None, finding_id="F",
                                        selected=_source(), precedence_rule="p"),
    lambda: _source(provenance="filename_mention"),
    lambda: _source(evidence_class="TRACED"),
    lambda: d.RetryTargetDecisionRecord(decision="redirect", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None, scope_filter="s",
                                        precedence_rule="p"),
    lambda: d.CandidateTransactionRecord(decision="refused", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None,
                                         transaction_id="t", staged=("a.java",)),
    lambda: d.CandidateTransactionRecord(decision="partially_staged", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None,
                                         transaction_id="t", staged=("a.java",)),
    lambda: d.CandidateTransactionRecord(decision="staged", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None,
                                         transaction_id="t", staged=("a.java",), independence=(("a", "b", "maybe", "x"),)),
    lambda: d.BudgetDecisionRecord(decision="admit", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None, phase="gate",
                                   estimate_seconds=1.0, estimate_source="configured_fallback", provider="p", model="m", file_count=1,
                                   view=d.BudgetViewRef("planner", None, 0.0, None, None)),
    lambda: d.BudgetDecisionRecord(decision="admit", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None, phase="developer",
                                   estimate_seconds=1.0, estimate_source="configured_fallback", provider="p", model="m", file_count=1,
                                   view=d.BudgetViewRef("planner", None, 0.0, None, None)),
    lambda: d.BudgetViewRef("planner", None, float("nan"), None, None),
    lambda: d.FallbackDecisionRecord(decision="selected", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None, role="developer",
                                     candidates=("a",), selected="b", eligibility_rule="e"),
    lambda: d.FallbackDecisionRecord(decision="refused", reason_code="R", inputs_digest=DIGEST, attempt=1, unit=None, role="developer",
                                     candidates=("a",), selected="a", eligibility_rule="e"),
    lambda: d.FallbackDecisionRecord(decision="selected", reason_code="R", inputs_digest=DIGEST, attempt=-1, unit=None, role="developer",
                                     candidates=("a",), selected="a", eligibility_rule="e"),
])
def test_a_record_outside_its_closed_vocabulary_is_refused_on_construction(build):
    with pytest.raises(ValueError):
        build()


def test_records_are_frozen():
    record = _records()["budget.decision"]
    with pytest.raises(AttributeError):
        record.decision = "refuse"  # type: ignore[misc]


def test_no_production_code_emits_the_new_kinds_yet():
    """D1 adds no emitter: outside the evidence package no kriya module names a new kind or calls emit_decision (F5/G5/H5 add them)."""
    hits = []
    for path in sorted((ROOT / "kriya").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("kriya/core/attempt_evidence/"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in NEW_KINDS:
                hits.append((rel, node.value))
            elif isinstance(node, (ast.Name, ast.Attribute)) and getattr(node, "id", getattr(node, "attr", None)) == "emit_decision":
                hits.append((rel, "emit_decision"))
            elif isinstance(node, ast.ImportFrom) and node.module and (
                    node.module.endswith("attempt_evidence.decisions")
                    or (node.module.endswith("attempt_evidence") and any(alias.name == "decisions" for alias in node.names))):
                hits.append((rel, "imports decisions"))
    assert hits == []
