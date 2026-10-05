"""The nine M1 questions answered from a run's store (LR-R1-M1 design §2, §8;
test T6 via the CLI).

Built only on ``reader.py`` (invariant I-1). Every answer is one of:
  * ``RECORDED`` with the records that answer it (content as blob refs);
  * ``NOT_APPLICABLE`` with why (the attempt passed, there was no later
    attempt) - a fact, not missing evidence;
  * ``NOT_RECORDED`` with the reason (invariant I-3: absence is explicit,
    never filled from a neighbouring field).
Read-only: nothing here writes, and no production decision imports it.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from kriya.core.attempt_evidence import reader

RECORDED = "RECORDED"
NOT_RECORDED = "NOT_RECORDED"
NOT_APPLICABLE = "NOT_APPLICABLE"

QUESTIONS = OrderedDict([
    ("Q1", "What exactly was the model asked?"),
    ("Q2", "What authoritative source did it receive?"),
    ("Q3", "What did it return?"),
    ("Q4", "What candidate change resulted?"),
    ("Q5", "What deterministic check failed?"),
    ("Q6", "Why did Kriya retry?"),
    ("Q7", "What changed in the next retry?"),
    ("Q8", "Why was fallback selected or refused?"),
    ("Q9", "Why did the run finally succeed or fail?"),
])

AttemptKey = Tuple[Optional[str], Optional[int], int]


def _recorded(items: List[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
    return {"status": RECORDED, "items": items, **extra}


def _absent(status: str, reason: str) -> Dict[str, Any]:
    return {"status": status, "reason": reason}


def _brief(record: Mapping[str, Any], *fields: str) -> Dict[str, Any]:
    payload = record.get("payload") or {}
    brief = {"seq": record.get("seq"), "call_seq": record.get("call_seq"), "wire_seq": record.get("wire_seq"),
             "role": record.get("role")}
    brief.update({name: payload.get(name) for name in fields})
    if record.get("blobs"):
        brief["content"] = dict(record["blobs"])
    elif record.get("content_digests"):
        brief["content_digests"] = {name: entry.get("digest") for name, entry in record["content_digests"].items()}
    return {key: value for key, value in brief.items() if value is not None}


def _with_authority(item: Dict[str, Any], request: Mapping[str, Any],
                    records: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """A Developer request with the authority it was given: the latest
    ``authority.snapshot`` recorded before it in the same attempt."""
    if request.get("role") != "developer":
        return item
    prior = [r for r in records if r.get("kind") == "authority.snapshot" and r.get("seq", 0) < request.get("seq", 0)]
    if not prior:
        return item
    snapshot = prior[-1]
    item["authority_snapshot_seq"] = snapshot.get("seq")
    item["requested_operations"] = {target.get("path"): target.get("requested_operation")
                                    for target in (snapshot.get("payload") or {}).get("targets") or []}
    return item


def _attempt_key(record: Mapping[str, Any]) -> Optional[AttemptKey]:
    """The attempt a record belongs to: its scope's attempt identity (the
    retry-loop iteration, so failure handling after run_attempt included)."""
    number = record.get("attempt_number")
    if not isinstance(number, int):
        return None
    return (record.get("unit_id"), record.get("invocation_seq"), number)


def _group(records: Iterable[Mapping[str, Any]]):
    attempts: "OrderedDict[AttemptKey, List[Mapping[str, Any]]]" = OrderedDict()
    outside: List[Mapping[str, Any]] = []
    for record in records:
        key = _attempt_key(record)
        if key is None:
            outside.append(record)
        else:
            attempts.setdefault(key, []).append(record)
    return attempts, outside


def _of(records: Iterable[Mapping[str, Any]], kind: str, **payload_match: Any) -> List[Mapping[str, Any]]:
    return [r for r in records if r.get("kind") == kind
            and all((r.get("payload") or {}).get(k) == v for k, v in payload_match.items())]


def _answer_attempt(key: AttemptKey, records: List[Mapping[str, Any]],
                    next_records: Optional[List[Mapping[str, Any]]]) -> Dict[str, Any]:
    requests = _of(records, "model.request")
    developer_requests = [r for r in requests if r.get("role") == "developer"]
    answers: Dict[str, Any] = {}
    if _of(records, "tool.execution") and not requests:
        return _answer_tool_attempt(next_records)
    # No model call is a fact Kriya recorded (the attempt ran and called
    # nothing), so it is NOT_APPLICABLE - never missing evidence.
    answers["Q1"] = _recorded([_with_authority(_brief(r, "model", "dispatched", "wire_reason", "refusal_type",
                                                      "temperature"), r, records)
                               for r in requests]) if requests else _absent(
        NOT_APPLICABLE, "no_model_call: no model request was made in this attempt")
    snapshots = _of(records, "authority.snapshot")
    if snapshots:
        answers["Q2"] = _recorded([_brief(r, "write_scope_mode", "targets", "transition") for r in snapshots])
    else:
        answers["Q2"] = _absent(NOT_APPLICABLE, "no_model_call: no Developer request in this attempt") \
            if not developer_requests else _absent(NOT_RECORDED, "no authority snapshot was recorded")
    responses = _of(records, "model.response")
    parses = _of(records, "developer.parse")
    undispatched = [r for r in requests if (r.get("payload") or {}).get("dispatched") is False]
    answered = {(r.get("call_seq"), r.get("wire_seq")) for r in responses}
    unanswered = [r for r in requests if (r.get("payload") or {}).get("dispatched") is True
                  and (r.get("call_seq"), r.get("wire_seq")) not in answered]
    if responses or parses:
        extra: Dict[str, Any] = {"parses": [_brief(r, "kind", "path", "reason_code", "call_seq_parsed")
                                            for r in parses]}
        if undispatched:
            extra["not_dispatched"] = [_brief(r, "refusal_type") for r in undispatched]
        if unanswered:
            extra["missing_responses"] = [_brief(r, "model") for r in unanswered]
        answers["Q3"] = _recorded(
            [_brief(r, "finish_reason", "completion_tokens", "error_type", "cancelled") for r in responses], **extra)
    elif not requests:
        answers["Q3"] = _absent(NOT_APPLICABLE, "no_model_call: no model response in this attempt")
    elif not unanswered:
        # Every request was refused before the provider was ever called.
        refusals = sorted({(r.get("payload") or {}).get("refusal_type") or "unknown" for r in undispatched})
        answers["Q3"] = _absent(NOT_APPLICABLE, "provider_not_dispatched: " + ", ".join(refusals))
    else:
        answers["Q3"] = _absent(NOT_RECORDED, "the provider was called but its response was not recorded")
    changes = _of(records, "candidate.change")
    parses = _of(records, "developer.parse")
    parse_kinds = {(r.get("payload") or {}).get("kind") for r in parses}
    if changes:
        answers["Q4"] = _recorded([_brief(r, "decision", "path", "candidate_staged", "before_digest", "after_digest",
                                          "diff", "proposed_digest", "proposal_kind", "parse_reason_code", "parse_seq",
                                          "reason_code",
                                          "lines_added", "lines_removed") for r in changes])
    elif parses and parse_kinds == {"no_change"}:
        answers["Q4"] = _absent(NOT_APPLICABLE, "model_proposed_no_change")
    else:
        answers["Q4"] = _absent(NOT_RECORDED, "no candidate was staged or refused in this attempt")
    gates = _of(records, "gate.result")
    failed = [r for r in gates if (r.get("payload") or {}).get("success") is not True]
    diagnoses = _of(records, "diagnosis")
    if failed or diagnoses:
        answers["Q5"] = _recorded([_brief(r, "stage", "gate", "success", "exit_code", "error_type") for r in failed],
                                  diagnoses=[_brief(r, "type", "reason_code", "likely_files", "evidence_class",
                                                    "attribution_tier") for r in diagnoses])
    elif gates:
        answers["Q5"] = _absent(NOT_APPLICABLE, f"every recorded check passed ({len(gates)} gate result(s))")
    else:
        answers["Q5"] = _absent(NOT_RECORDED, "no gate ran and no failure was recorded in this attempt")
    decisions = _of(records, "recovery.decision")
    if decisions:
        answers["Q6"] = _recorded([_brief(r, "failure_type", "action", "retry", "stop_loop", "retry_decision",
                                          "no_progress_terminated", "progress_classification",
                                          "no_progress_reason", "stop_reason_code")
                                   for r in decisions])
    elif not diagnoses:
        # A recovery decision follows a recorded failure; none was recorded.
        answers["Q6"] = _absent(NOT_APPLICABLE, "no failure was recorded for this attempt; nothing was retried")
    else:
        answers["Q6"] = _absent(NOT_RECORDED, "a failure was recorded but no recovery decision")
    if next_records is None:
        answers["Q7"] = _absent(NOT_APPLICABLE, "no later attempt in this unit invocation")
    else:
        deltas = _of(next_records, "retry.delta")
        answers["Q7"] = _recorded([_brief(r, "information_gain", "changed", "reason") for r in deltas]) \
            if deltas else _absent(NOT_RECORDED, "the next attempt recorded no retry delta")
    fallbacks = _of(records, "fallback.decision")
    if fallbacks:
        answers["Q8"] = _recorded([_brief(r, "phase", "requested", "selected", "fallback", "requested_rejection",
                                          "newly_rejected") for r in fallbacks])
    else:
        answers["Q8"] = _absent(NOT_APPLICABLE, "no_model_call: no Developer call in this attempt") \
            if not developer_requests else _absent(NOT_RECORDED, "no model decision was recorded")
    # An explicit not_recorded record (the legacy importer, invariant I-3)
    # names why a question cannot be answered; it replaces any inferred
    # absence ("no model call") but never evidence that is present.
    for record in _of(records, "not_recorded"):
        for label in (record.get("payload") or {}).get("questions") or ():
            if label in answers and answers[label].get("status") != RECORDED:
                answers[label] = _absent(NOT_RECORDED, (record.get("payload") or {}).get("reason"))
    return answers


_UNIT_RESULT_FIELDS = ("failure_category", "quality_gates_passed")


def _terminal_cause(units: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """The run's last closed unit as it recorded its own result (the unit
    scope copies the workflow result's fields at close); never inferred."""
    if not units:
        return _absent(NOT_RECORDED, "no unit closed")
    payload = units[-1].get("payload") or {}
    if "tool_status" in payload:
        return {"tool_status": payload.get("tool_status"), "unit_outcome": payload.get("outcome")}
    if not any(field in payload for field in _UNIT_RESULT_FIELDS):
        cause = _absent(NOT_RECORDED, "the last unit recorded no result fields")
        if payload.get("outcome") == "EXCEPTION":
            cause.update(unit_outcome="EXCEPTION", error_type=payload.get("error_type"))
        return cause
    return {"failure_category": payload.get("failure_category"),
            "quality_gates_passed": payload.get("quality_gates_passed"), "unit_outcome": payload.get("outcome")}


_TOOL_NO_MODEL_CALL = "no_model_call: a tool subtask makes no model call"


def _answer_tool_attempt(next_records: Optional[List[Mapping[str, Any]]]) -> Dict[str, Any]:
    """An enforce TOOL subtask's execution: an action, not a model call,
    a candidate or a verification gate; executed once (TOOL-001). Its own
    result is the attempt's ``tool.execution`` record (and Q9)."""
    answers = {label: _absent(NOT_APPLICABLE, _TOOL_NO_MODEL_CALL) for label in ("Q1", "Q2", "Q3", "Q8")}
    answers["Q4"] = _absent(NOT_APPLICABLE, "tool_action: no Developer candidate")
    answers["Q5"] = _absent(NOT_APPLICABLE, "no_verification_gate")
    answers["Q6"] = _absent(NOT_APPLICABLE, "tool_subtask: executed once, never retried")
    answers["Q7"] = _absent(NOT_APPLICABLE, "no later attempt in this unit invocation") if next_records is None \
        else _absent(NOT_RECORDED, "a tool unit recorded a later attempt")
    return answers


def _answer_run(records: List[Mapping[str, Any]], seal: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    closed = _of(records, "run.closed")
    if not closed:
        return _absent(NOT_RECORDED, "the run never closed (crashed or still running)" if seal is None
                       else "no run.closed record")
    terminal = [r for r in _of(records, "gate.result", stage="terminal")
                if (r.get("payload") or {}).get("success") is not True]
    units = _of(records, "unit.closed")
    decisions = _of(records, "recovery.decision")
    terminal_cause = _terminal_cause(units)
    return _recorded(
        [_brief(closed[-1], "terminal_status", "lifecycle_state", "commit_result", "model_calls")],
        terminal_cause=terminal_cause,
        units=[_brief(r, "outcome", "status", "failure_category", "quality_gates_passed", "error_type")
               for r in units],
        failed_terminal_gates=[_brief(r, "gate") for r in terminal],
        last_recovery_decision=_brief(decisions[-1], "failure_type", "action", "retry",
                                      "progress_classification", "no_progress_reason",
                                      "stop_reason_code") if decisions else None,
    )


def explain_run(state_dir: str, run_id: str) -> Dict[str, Any]:
    """Answers per attempt (Q1-Q8) and for the run (Q9)."""
    run = reader.open_run(state_dir, run_id)
    verification = run.verify()
    result: Dict[str, Any] = {"run_id": run_id, "verification": verification.status,
                              "questions": dict(QUESTIONS)}
    if verification.status == reader.NOT_FOUND:
        result["unavailable"] = "ATTEMPT EVIDENCE UNAVAILABLE (no store for this run: pre-M1, capture off, " \
                                "or the recorder was unavailable)"
        return result
    manifest = run.manifest()
    records = list(run.records())
    result["capture"] = manifest.get("capture")
    result["sealed"] = verification.sealed
    attempts, outside = _group(records)
    keys = list(attempts)
    result["attempts"] = []
    for index, key in enumerate(keys):
        nxt = keys[index + 1] if index + 1 < len(keys) else None
        same_invocation = nxt is not None and nxt[:2] == key[:2]
        entry = {
            "unit_id": key[0], "invocation_seq": key[1], "attempt": key[2],
            "answers": _answer_attempt(key, attempts[key], attempts[nxt] if same_invocation else None),
        }
        actions = _of(attempts[key], "tool.execution")
        if actions:
            entry["tool_execution"] = [_brief(r, "tool_name", "status", "error", "reason_codes") for r in actions]
        result["attempts"].append(entry)
    phases: "OrderedDict[str, int]" = OrderedDict()
    for record in _of(outside, "model.request"):
        phase = record.get("phase") or "unscoped"
        phases[phase] = phases.get(phase, 0) + 1
    result["calls_outside_attempts"] = dict(phases)
    result["Q9"] = _answer_run(records, verification.seal)
    return result


def run_summary(state_dir: str, run_id: str) -> Dict[str, Any]:
    """``kriya evidence show``: identity, integrity and record counts."""
    run = reader.open_run(state_dir, run_id)
    verification = run.verify()
    summary: Dict[str, Any] = {"run_id": run_id, "verification": verification.status,
                               "records": verification.record_count, "sealed": verification.sealed}
    if verification.status == reader.NOT_FOUND:
        return summary
    manifest = run.manifest()
    summary.update({key: manifest.get(key) for key in ("capture", "workspace_path", "base_revision", "kriya_build")})
    kinds: "OrderedDict[str, int]" = OrderedDict()
    for record in run.records():
        kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
    summary["kinds"] = dict(kinds)
    if verification.seal:
        summary["seal"] = {key: verification.seal.get(key) for key in ("complete", "gaps", "degraded",
                                                                      "closed_reason", "blob_count")}
    if verification.detail:
        summary["detail"] = verification.detail
    return summary
