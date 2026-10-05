"""LR-R1-M1.11: import one pre-M1 run's evidence into the attempt-evidence
schema (design §8, §13; invariant I-3; test T16).

Input: a CAGC-style run evidence directory, read only:
  * ``traces.json``  - the run's trace rows (one per workflow invocation);
  * ``run.txt``, ``identity.pre.json`` - the harness's run identity;
  * ``workspace.diff`` - the workspace's final diff (run level);
  * ``untracked.tar`` - the workspace's ``.kriya/control`` archive (RunRecord,
    decision ledger), read from the archive, never extracted.
Output: a new store under ``--state-dir`` written through the writer API.

Invariant I-3: nothing is inferred. Every record carries provenance
``LEGACY_RECONSTRUCTED`` and holds only what the legacy artifacts hold;
everything they never had is an explicit ``not_recorded`` record naming the
questions it leaves unanswered and why. In particular:
  * no prompt, raw response, per-attempt diff, authority snapshot or
    recovery-decision object is ever synthesized - not from a neighbouring
    event, and not from the final diff;
  * the final ``workspace.diff`` is a run-level record, never an attempt's;
  * run events are mirrored verbatim, never converted into typed decisions.

Usage:
  python benchmarks/reliability/import_legacy.py RUN_EVIDENCE_DIR --state-dir OUT_STATE_DIR [--run-id ID]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter

LEGACY = model.LEGACY_RECONSTRUCTED

# What a pre-M1 attempt never recorded, and the questions each leaves open.
ATTEMPT_ABSENCES: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("model.request", ("Q1",), "pre-M1 traces kept no Developer prompt or request body"),
    ("authority.snapshot", ("Q2",), "pre-M1 traces kept no per-request authority snapshot"),
    ("model.response", ("Q3",), "pre-M1 traces kept no raw model response"),
    ("candidate.change", ("Q4",), "pre-M1 traces kept no per-attempt candidate diff (the final workspace.diff is "
                                  "run-level and is never attributed to an attempt)"),
    ("recovery.decision", ("Q6",), "pre-M1 traces kept no RecoveryDecision/RetryDecision object (only the mirrored "
                                   "run events)"),
    ("retry.delta", ("Q7",), "retry information delta did not exist before M1"),
    ("fallback.decision", ("Q8",), "pre-M1 traces kept fallback events only, mirrored verbatim, not a decision record"),
)
PASSING_GATES_ABSENT = ("gate.result(passing)", ("Q5",),
                        "pre-M1 traces kept only the gate outcomes the run appended; no passing check was "
                        "recorded for this attempt")


class LegacyImportError(ValueError):
    """The input is not a legacy run evidence directory this importer reads."""


def _json_field(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def _control_members(archive: str) -> Dict[str, bytes]:
    """``.kriya/control/runs/*.json`` and ``decisions.jsonl`` from the archive."""
    members: Dict[str, bytes] = {}
    if not os.path.isfile(archive):
        return members
    with tarfile.open(archive, "r") as tar:
        for member in tar.getmembers():
            name = member.name.removeprefix("./")
            # macOS archivers add AppleDouble metadata members ("._<name>",
            # a "Mac OS X" resource-fork header): OS metadata, never evidence.
            if not member.isfile() or os.path.basename(name).startswith("._"):
                continue
            if name == ".kriya/control/decisions.jsonl" or (
                    name.startswith(".kriya/control/runs/") and name.endswith(".json")):
                handle = tar.extractfile(member)
                if handle is not None:
                    members[name] = handle.read()
    return members


def _read_text(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="strict") as handle:
        return handle.read()


def _attempt_numbers(gates: Iterable[Mapping[str, Any]], events: Iterable[Mapping[str, Any]]) -> List[int]:
    numbers = {g.get("attempt") for g in gates} | {e.get("attempt") for e in events}
    return sorted(n for n in numbers if isinstance(n, int) and n > 0)


class _Importer:
    def __init__(self, writer: AttemptEvidenceWriter) -> None:
        self.writer = writer

    def put(self, kind: str, payload: Mapping[str, Any], *, content: Optional[Mapping[str, Any]] = None,
            **identity: Any) -> None:
        self.writer.append(kind, payload, identity=identity, provenance=LEGACY, content=content)

    def absent(self, field: str, questions: Tuple[str, ...], reason: str, **identity: Any) -> None:
        self.writer.append("not_recorded", {"field": field, "questions": list(questions), "reason": reason},
                           identity=identity, provenance=model.NOT_RECORDED)


def import_run(evidence_dir: str, state_dir: str, run_id: Optional[str] = None) -> str:
    """Import one run; returns the new store's run id."""
    traces_path = os.path.join(evidence_dir, "traces.json")
    rows = _json_field(_read_text(traces_path), None)
    if not isinstance(rows, list):
        raise LegacyImportError(f"{traces_path}: no trace rows")
    evidence_real = os.path.realpath(evidence_dir)
    state_real = os.path.realpath(state_dir)
    if state_real == evidence_real or state_real.startswith(evidence_real + os.sep):
        raise LegacyImportError("the output state directory must be outside the legacy evidence")
    control = _control_members(os.path.join(evidence_dir, "untracked.tar"))
    records = {name: json.loads(data) for name, data in control.items() if name.endswith(".json")}
    run_records = sorted(records.items())
    if run_id is None:
        base = run_records[0][1].get("run_id") if run_records else os.path.basename(evidence_real)
        run_id = f"legacy-{base}"
    identity_pre = _json_field(_read_text(os.path.join(evidence_dir, "identity.pre.json")), {})
    writer = AttemptEvidenceWriter(state_dir, run_id, capture=model.CAPTURE_FULL, manifest={
        "legacy_source": evidence_real, "legacy_identity": identity_pre,
    })
    out = _Importer(writer)
    out.put("run.opened", {"legacy_run_txt": True},
            content={"run_txt": _read_text(os.path.join(evidence_dir, "run.txt")), "identity_pre": identity_pre},
            run_id=run_id)
    for invocation, row in enumerate(rows, start=1):
        _import_row(out, run_id, invocation, row)
    for name, data in sorted(control.items()):
        if name.endswith("decisions.jsonl"):
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    out.put("mirror.decision", json.loads(line), run_id=run_id)
    final_diff = _read_text(os.path.join(evidence_dir, "workspace.diff"))
    if final_diff is None:
        out.absent("run.final_diff", ("Q9",), "no workspace.diff in the legacy evidence", run_id=run_id)
    else:
        out.put("candidate.change", {"decision": "RUN_FINAL_DIFF", "scope": "run",
                                     "note": "the workspace's final diff; not any attempt's candidate"},
                content={"diff": final_diff}, run_id=run_id)
    for name, record in run_records:
        out.put("run.closed", {"legacy_run_record": name, "terminal_status": record.get("terminal_status"),
                               "lifecycle_state": record.get("lifecycle_state"),
                               "commit_result": record.get("commit_result")}, run_id=run_id)
    if not run_records:
        out.absent("run.closed", ("Q9",), "no RunRecord in the legacy control archive", run_id=run_id)
    writer.seal("legacy_import")
    return run_id


def _import_row(out: _Importer, run_id: str, invocation: int, row: Mapping[str, Any]) -> None:
    unit = str(row.get("run_id"))
    ident = {"run_id": run_id, "unit_id": unit, "unit_kind": "legacy_trace_row", "invocation_seq": invocation}
    gates = _json_field(row.get("gate_outcomes"), [])
    events = _json_field(row.get("run_events"), [])
    out.put("unit.opened", {"unit_id": unit, "status": row.get("status"),
                            "failure_category": row.get("failure_category"), "attempts": row.get("attempts"),
                            "model_hops": _json_field(row.get("model_hops"), []),
                            "timestamp": row.get("timestamp")}, content={"goal": row.get("goal")}, **ident)
    if row.get("prompt_rendered"):
        out.put("mirror.evidence", {"field": "prompt_rendered",
                                    "note": "as the trace row kept it; whether it is the exact request sent is "
                                            "not recorded"},
                content={"prompt_rendered": row.get("prompt_rendered")}, **ident)
    for event in (e for e in events if not isinstance(e.get("attempt"), int) or e.get("attempt") <= 0):
        out.put("mirror.event", dict(event), **ident)
    for number in _attempt_numbers(gates, events):
        attempt = {**ident, "phase": "attempt", "attempt_number": number}
        started = [e for e in events if e.get("attempt") == number and e.get("kind") == "attempt.started"]
        out.put("attempt.opened", {"mode": (started[0].get("details") or {}).get("mode") if started else None},
                **attempt)
        for event in (e for e in events if e.get("attempt") == number):
            out.put("mirror.event", dict(event), **attempt)
            if event.get("kind") == "failure.recorded":
                out.put("diagnosis", {"type": event.get("failure_type"), "source": event.get("source"),
                                      "attempt": number, "likely_files": (event.get("details") or {}).get(
                                          "likely_files"), "evidence_class": "UNKNOWN"},
                        content={"message": event.get("message")}, **attempt)
        attempt_gates = [g for g in gates if g.get("attempt") == number]
        for gate in attempt_gates:
            out.put("gate.result", {"stage": "legacy_gate_outcome", "gate": gate.get("type"),
                                    "success": gate.get("success"), "mode": gate.get("mode"),
                                    "likely_files": gate.get("likely_files")},
                    content={"output": gate.get("output")}, **attempt)
        if not any(g.get("success") is True for g in attempt_gates):
            field, questions, reason = PASSING_GATES_ABSENT
            out.absent(field, questions, reason, **attempt)
        for field, questions, reason in ATTEMPT_ABSENCES:
            out.absent(field, questions, reason, **attempt)
        out.absent("attempt.concluded", (), "whether each attempt as a whole succeeded was not recorded before M1",
                   **attempt)
    out.put("unit.closed", {"status": row.get("status"), "failure_category": row.get("failure_category")}, **ident)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("evidence_dir")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    try:
        run_id = import_run(args.evidence_dir, args.state_dir, args.run_id)
    except (LegacyImportError, OSError, ValueError) as error:
        print(f"legacy import refused: {error}", file=sys.stderr)
        return 2
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
