"""BACKEND-READINESS-V2 slot 3: intermediate deterministic contract feedback.

A deterministic user contract - the goal's own examples compiled into the sealed acceptance class - is executable while a
subtask is still being generated, but was judged only at the terminal gate, where its precise failure can no longer take part
in repair. This module lets the attempt pipeline run the RELEVANT part of that same sealed class on a coherent candidate (after
the compile gate) and turn an observed contradiction into a typed, retriable ``deterministic_contract_violation`` failure that
enters the existing retry machinery.

What it is not: a second validator. The artifact, the runner (``run_java_acceptance``) and the definition of a contradiction
(``acceptance_jvm.contradicted_case_details``) are the terminal closer's. Terminal acceptance is unchanged and re-evaluates every
example on the final tree; an intermediate pass is feedback only and is never written to the requirement ledger.

Rules (design record, doc 19):
- relevance is deterministic and behavioral (kriya/workflow/contract_relevance.py): an example runs early only when the current
  unit touched the main file that declares its target type, or a file the target method DIRECTLY delegates to / constructs /
  that directly overrides it (one hop, UNKNOWN never promoted); everything else waits for the terminal gate. Module ownership
  decides where the class RUNS, never whether an example is relevant;
- only cases of the sealed class (the enforced forms) take part; unsupported, ambiguous and shadow lines are not in it;
- a run that observed nothing about the candidate (refusal, harness compile error, no report, trust-surface problem) is
  ``UNAVAILABLE`` - recorded, never a violation, never feedback;
- one batched run per attempt; no retry budget is added here.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from kriya.workflow import contract_relevance
from kriya.workflow.acceptance_jvm import contradicted_case_details
from kriya.workflow.example_oracle_java import main_source_path

DETERMINISTIC_CONTRACT_VIOLATION = "DETERMINISTIC_CONTRACT_VIOLATION"
FAILURE_TYPE = "deterministic_contract_violation"
MAX_REPORTED_VIOLATIONS = 5
MAX_REPORTED_RELEVANCE = 8
_MAX_FIELD_CHARS = 300
logger = logging.getLogger(__name__)
_ASSERTION = re.compile(r"expected: <(?P<expected>.*?)> but was: <(?P<observed>.*)>\s*$")


@dataclass(frozen=True)
class ContractExample:
    """One case of the sealed class and the goal line it was compiled from."""

    identity: str  # <package>.<Class>.exampleN, the case identity in the JUnit report
    number: int
    requirement_ids: Tuple[str, ...]
    text: str  # the exact goal line
    line: int
    span: Optional[Tuple[int, int]]
    expected: Optional[str]
    exception: Optional[str]
    form: str
    qualified_type: str
    source_path: Optional[str]  # the main file declaring the target type; None when not exactly one
    method: Optional[str] = None  # the factory/target method the example calls
    relevance: Optional[contract_relevance.Relevance] = None  # why it is relevant to the current unit (set by evaluate)


@dataclass(frozen=True)
class ContractViolation:
    example: ContractExample
    kind: str  # ASSERTION_MISMATCH | EXCEPTION_THROWN | OTHER
    expected: str
    observed: str
    failure_type: str  # the JUnit failure/exception class


@dataclass
class ContractOutcome:
    status: str  # NOT_RELEVANT | PASSED | VIOLATED | UNAVAILABLE
    relevant: Tuple[ContractExample, ...] = ()
    violations: Tuple[ContractViolation, ...] = ()
    reason: str = ""
    seconds: float = 0.0
    compiled_examples: int = 0
    relevance: Tuple[contract_relevance.Relevance, ...] = ()  # one record per compiled example (closed status set, see contract_relevance)

    def summary(self) -> Dict[str, Any]:
        return {"status": self.status, "compiled_examples": self.compiled_examples, "relevant": len(self.relevant),
                "violations": len(self.violations), "reason": self.reason, "seconds": round(self.seconds, 2),
                "relevance": [r.to_dict() for r in self.relevance[:MAX_REPORTED_RELEVANCE]]}


@dataclass
class ContractFeedback:
    """The run's sealed example class and how to run it on the current unit's candidate. ``execute(artifact, touched)`` returns
    the acceptance run (``AcceptanceRun``); it is supplied by the workflow so this module imports no runner wiring."""

    artifact: Any
    examples: Tuple[ContractExample, ...]
    execute: Callable[[Any, Sequence[str]], Any]
    runs: int = field(default=0)
    candidate_root: Optional[str] = None  # the unit's candidate tree: relevance reads the touched and target sources from it


def contract_examples(artifact: Any, report: Dict[str, Any], candidate_root: str) -> Tuple[ContractExample, ...]:
    """The sealed class's cases in class order (legacy static examples first, then the graduated forms: the order
    ``compile_java_example_class`` numbers them), each with the main file that declares its target type."""
    package, class_name = artifact.java["package"], artifact.java["class_name"]
    rows: List[Dict[str, Any]] = [{**e, "text": e.get("source"), "line": (e.get("lines") or [0])[0]}
                                  for e in report.get("examples") or ()] + list(report.get("graduated_examples") or ())
    out: List[ContractExample] = []
    for number, row in enumerate(rows, start=1):
        qualified = row.get("qualified_type") or ""
        span = row.get("span")
        out.append(ContractExample(
            identity=f"{package}.{class_name}.example{number}", number=number,
            requirement_ids=(row["requirement_id"],) if row.get("requirement_id") else (),
            text=str(row.get("text") or ""), line=int(row.get("line") or 0),
            span=tuple(span) if span else None, expected=row.get("expected"), exception=row.get("exception"),
            form=str(row.get("form") or ""), qualified_type=qualified,
            source_path=main_source_path(candidate_root, qualified) if qualified else None, method=row.get("method")))
    return tuple(out)


def assess_examples(examples: Sequence[ContractExample], touched_paths: Sequence[str], candidate_root: Optional[str] = None
                    ) -> Tuple[Tuple[ContractExample, ...], Tuple[contract_relevance.Relevance, ...]]:
    """(the relevant examples, one relevance record per example) for the files the current unit touched. A relevant example's
    ``source_path`` is the touched file that makes it relevant (what repair should look at); its ``relevance`` records why.
    Without a ``candidate_root`` only the direct-target rule can apply."""
    records = contract_relevance.assess_all(examples, touched_paths, candidate_root)
    relevant = tuple(replace(example, source_path=record.touched_path, relevance=record)
                     for example, record in zip(examples, records, strict=True) if record.relevant)
    return relevant, records


def relevant_examples(examples: Sequence[ContractExample], touched_paths: Sequence[str],
                      candidate_root: Optional[str] = None) -> Tuple[ContractExample, ...]:
    return assess_examples(examples, touched_paths, candidate_root)[0]


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_FIELD_CHARS else text[:_MAX_FIELD_CHARS] + "..."


def _violation(example: ContractExample, detail: Dict[str, str]) -> ContractViolation:
    trace = detail.get("trace") or ""
    first = trace.splitlines()[0] if trace else ""
    failure_type = detail.get("type") or ""
    match = _ASSERTION.search(first)
    if match:
        return ContractViolation(example, "ASSERTION_MISMATCH", _clip(match.group("expected")), _clip(match.group("observed")),
                                 failure_type)
    if detail.get("status") == "error":
        return ContractViolation(example, "EXCEPTION_THROWN", _clip(example.expected or example.exception or ""),
                                 _clip(first or failure_type), failure_type)
    return ContractViolation(example, "OTHER", _clip(example.expected or ""), _clip(first or failure_type), failure_type)


def evaluate(feedback: Optional[ContractFeedback], touched_paths: Sequence[str]) -> ContractOutcome:
    """Run the relevant part of the sealed class once on the current candidate. Never raises for a runner problem."""
    if feedback is None:
        return ContractOutcome("NOT_RELEVANT", reason="no sealed example class is bound to this run")
    total = len(feedback.examples)
    relevant, records = assess_examples(feedback.examples, touched_paths, feedback.candidate_root)
    if not relevant:
        return ContractOutcome("NOT_RELEVANT", relevance=records, compiled_examples=total,
                               reason="no example targets a file this unit touched")
    started = time.monotonic()
    try:
        run = feedback.execute(feedback.artifact, list(touched_paths))
    except Exception as error:  # a runner defect is infrastructure, never a candidate verdict
        logger.warning("Intermediate contract check unavailable: %s: %s", type(error).__name__, error)
        return ContractOutcome("UNAVAILABLE", relevant, relevance=records, reason=f"runner error: {type(error).__name__}: {error}",
                               seconds=time.monotonic() - started, compiled_examples=total)
    feedback.runs += 1
    elapsed = time.monotonic() - started
    contradicted = contradicted_case_details(feedback.artifact, run)
    if contradicted is None:
        why = getattr(getattr(run, "refusal", None), "reason_code", None) or "no complete digest-verified report"
        return ContractOutcome("UNAVAILABLE", relevant, relevance=records, reason=str(why), seconds=elapsed, compiled_examples=total)
    violations = tuple(_violation(e, contradicted[e.identity]) for e in relevant if e.identity in contradicted)
    if violations:
        return ContractOutcome("VIOLATED", relevant, violations=violations, relevance=records, seconds=elapsed, compiled_examples=total)
    return ContractOutcome("PASSED", relevant, relevance=records, seconds=elapsed, compiled_examples=total)


def violation_message(outcome: ContractOutcome, digest: str) -> str:
    """The structured repair text: contract, provenance, target, expected, observed, failed example - bounded."""
    shown = outcome.violations[:MAX_REPORTED_VIOLATIONS]
    lines = [
        f"{DETERMINISTIC_CONTRACT_VIOLATION}: the candidate contradicts {len(outcome.violations)} of the user's own explicit "
        f"examples (goal text, compiled deterministically; sealed example class {digest[:12]}). Fix the production code so every "
        "example below holds, without weakening any original obligation (authorized files, public API, plan obligations).",
    ]
    for number, v in enumerate(shown, start=1):
        e = v.example
        lines += [
            f"[{number}] FAILED EXAMPLE: {e.text}",
            f"    PROVENANCE: goal line {e.line}" + (f", characters {e.span[0]}-{e.span[1]}" if e.span else "")
            + (f", requirement {', '.join(e.requirement_ids)}" if e.requirement_ids else ""),
            f"    TARGET: {e.qualified_type}" + (f" ({e.source_path})" if e.source_path else ""),
            *([f"    RELATION: {e.relevance.reason}"] if e.relevance and e.relevance.status == contract_relevance.RELEVANT_DIRECT_DEPENDENCY else []),
            f"    EXPECTED: {v.expected}",
            f"    OBSERVED: {v.observed}  [{v.kind}{', ' + v.failure_type if v.failure_type else ''}]",
        ]
    if len(outcome.violations) > len(shown):
        lines.append(f"... and {len(outcome.violations) - len(shown)} more failing example(s), same sealed class.")
    return "\n".join(lines)


def violation_diagnostics(outcome: ContractOutcome, digest: str) -> Dict[str, Any]:
    return {
        "reason_code": DETERMINISTIC_CONTRACT_VIOLATION, "contract_digest": digest,
        "relevant_examples": len(outcome.relevant), "compiled_examples": outcome.compiled_examples,
        "violations": [
            {"case": v.example.identity, "example": v.example.text, "goal_line": v.example.line,
             "span": list(v.example.span) if v.example.span else None, "requirements": list(v.example.requirement_ids),
             "target": v.example.qualified_type, "source_path": v.example.source_path,
             "relevance": v.example.relevance.to_dict() if v.example.relevance else None, "kind": v.kind,
             "expected": v.expected, "observed": v.observed, "failure_type": v.failure_type}
            for v in outcome.violations[:MAX_REPORTED_VIOLATIONS]],
    }
