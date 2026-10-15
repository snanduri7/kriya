"""ApplicationOutcome (PRODUCT-UX-COMPLETION): did Kriya EXECUTE the application it built, and what did it print? A read-only projection of data Kriya already persisted.

Source (TRACED, kriya/workflow/attempt.py): the Runtime Verification Gate runs a resolved command in the sandboxed workspace and records, in the run's gate outcome
(``runs.gate_outcomes``), ``type`` ``run_verification`` (or ``run_verification_hung`` when the process had to be killed) and ``steps``: per command ``command`` (argv),
``exit_code``, ``stdout``, ``stderr``, ``timed_out``. This module only reads those recorded fields.

What it never does: invent a command, derive one from a build file, execute anything, or claim success the record does not carry. ``succeeded`` is true only when the
gate outcome says success AND the last step exited 0 without timing out. A run whose goal never triggered the gate is ``NOT_APPLICABLE`` - there is no authoritative
"how to start it" in Kriya, so none is offered. Command and output are passed through the existing secret scrub (kriya/policy/telemetry.py) and bounded.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from kriya.policy.telemetry import scrub_potential_secrets

STATUS_EXECUTED = "EXECUTED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_UNKNOWN = "UNKNOWN"
RUNTIME_GATE_TYPES = ("run_verification", "run_verification_hung")
# Why runtime verification could not even start, as Kriya's OWN closed vocabulary. The failure records no structured field in the persisted gate outcome (``diagnostics`` stays on the in-memory
# Failure), but Kriya authors the message and begins it with the code and a colon, so the projection reads exactly that prefix against this closed table - never free text, never a substring.
BLOCKED_REASON_CODES = ("MANAGED_SERVICE_APPROVAL_UNSUPPORTED", "REQUIRED_RUNTIME_VERIFICATION_DECLINED", "REQUIRED_RUNTIME_VERIFICATION_DISABLED")
INFRASTRUCTURE_GATE_TYPE = "verification_infrastructure_failure"
OUTPUT_MAX_CHARS = 4000


def _bounded(text: Any) -> Dict[str, Any]:
    value = scrub_potential_secrets(text) if isinstance(text, str) else ""
    return {"text": value[:OUTPUT_MAX_CHARS], "truncated": len(value) > OUTPUT_MAX_CHARS}


def _command(argv: Any) -> Optional[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    return scrub_potential_secrets(" ".join(argv))


def _blocked_reason(gate_outcomes: List[Any]) -> Optional[str]:
    """The newest recorded runtime-verification stop whose message begins with a code of the closed table, or None."""
    for gate in reversed(gate_outcomes):
        if isinstance(gate, dict) and gate.get("type") == INFRASTRUCTURE_GATE_TYPE and isinstance(gate.get("output"), str):
            for code in BLOCKED_REASON_CODES:
                if gate["output"].startswith(code + ":"):
                    return code
    return None


def application_outcome(gate_outcomes: Any) -> Dict[str, Any]:
    """The projection for one run's recorded gate outcomes (a list of dicts, or whatever the column held)."""
    if not isinstance(gate_outcomes, list):
        return {"status": STATUS_UNKNOWN, "reason": "no_gate_outcomes_recorded"}
    runtime = [g for g in gate_outcomes if isinstance(g, dict) and g.get("type") in RUNTIME_GATE_TYPES and isinstance(g.get("steps"), list) and g["steps"]]
    if not runtime:
        blocked = _blocked_reason(gate_outcomes)
        return {"status": STATUS_NOT_APPLICABLE, "reason": "no_runtime_execution_recorded", **({"blocked_reason_code": blocked} if blocked else {})}
    gate = runtime[-1]  # the newest recorded execution (the last attempt that ran the application)
    steps: List[Dict[str, Any]] = [s for s in gate["steps"] if isinstance(s, dict)]
    if not steps:
        return {"status": STATUS_UNKNOWN, "reason": "execution_steps_unreadable"}
    last = steps[-1]
    exit_code = last.get("exit_code") if isinstance(last.get("exit_code"), int) and not isinstance(last.get("exit_code"), bool) else None
    timed_out = bool(last.get("timed_out")) or gate.get("type") == "run_verification_hung"
    return {
        "status": STATUS_EXECUTED,
        "succeeded": gate.get("success") is True and exit_code == 0 and not timed_out,
        "attempt": gate.get("attempt") if isinstance(gate.get("attempt"), int) else None,
        "command": _command(last.get("command")),
        "commands": [c for c in (_command(s.get("command")) for s in steps) if c is not None],
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": _bounded(last.get("stdout")),
        "stderr": _bounded(last.get("stderr")),
    }
