"""Verification of a candidate that is already on disk (PRD-031).

:class:`VerificationCoordinator` runs a verification-only subtask's declared
verifiers against the existing worktree. It runs the deterministic
compile/test verifiers first (through the same PolymorphicValidator every
attempt uses), then the runtime verifiers. It owns no mutation: no Developer
call and no candidate write. It owns no retry policy either: the first
failing verifier raises QualityGateFailure, the typed failure the recovery
path (kriya/workflow/recovery_coordinator.py) already handles. Each gate
outcome is recorded as it happens, so a verifier that raises leaves the same
outcomes behind as before.

The runtime verifier (judge, then execute, then grade) stays in attempt.py
and is injected, so this module never imports attempt.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from kriya.tools.validate import execution_evidence
from kriya.workflow.failure import Failure, QualityGateFailure


def is_verification_only_unit(write_scope_mode: Any, required_verification: List[Dict[str, Any]]) -> bool:
    """A unit whose attempts are verification-only (run_attempt's branch): a
    DENY_ALL write scope - it owns no file, so the Developer is never asked
    to mutate anything - with at least one directly executable verifier. Its
    attempts run only that verification against the existing worktree."""
    from kriya.policy.filesystem import WriteScopeMode

    return write_scope_mode == WriteScopeMode.DENY_ALL and bool(
        _directly_executable_verifiers(required_verification)
        or _directly_executable_runtime_verifiers(required_verification))


def _directly_executable_verifiers(required_verification: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """type=tool verifiers naming a BUILTIN_QUALITY_GATE_VERIFIERS tool_name
    (compile/test/tests/regression/quality_gates) - the ones
    _run_verification_only_attempt can execute directly via
    PolymorphicValidator, the same deterministic gate every ordinary
    implementation-subtask attempt already uses. See
    _directly_executable_runtime_verifiers below for the sibling case
    (a runtime-execution verifier) - kept as a separate function rather
    than folded in here since the two are executed through genuinely
    different machinery (PolymorphicValidator vs RunVerifierAgent)."""
    from kriya.workflow.plan_schema import BUILTIN_QUALITY_GATE_VERIFIERS
    return [
        requirement for requirement in required_verification
        if requirement.get("type") == "tool"
        and requirement.get("tool_name") in BUILTIN_QUALITY_GATE_VERIFIERS
    ]


def _directly_executable_runtime_verifiers(required_verification: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """PRV-06 (2026-08-28, real live-validation finding): a verification-
    only subtask (write_scope_mode=DENY_ALL, planned_files=[]) declaring
    ONLY an application_runtime verifier used to fall through to the
    ordinary Developer/Quality-Gates loop anyway (this exact case is what
    _directly_executable_verifiers' own docstring flagged as "a larger,
    separate lift" not yet built) - the Developer, asked to generate files
    for a subtask that legitimately has none, reliably invented a
    duplicate entrypoint every attempt. DENY_ALL correctly rejected each
    one before it ever reached disk (zero corruption, confirmed live), but
    the subtask still burned its entire retry budget on candidates that
    could never be written, because nothing short-circuited BEFORE the
    Developer call for this specific verifier shape.

    Deliberately narrow, matching _directly_executable_verifiers' own
    discipline: only matches an entry whose `verifier_kind` is EXPLICITLY
    "application_runtime" AND `requires_runtime_execution` is True - not
    just "files == []" alone (a malformed/under-specified plan could
    accidentally have zero planned_files for a genuinely mutating subtask;
    that must still enter ordinary validation/repair, never be silently
    reinterpreted as verification-only). `type` is intentionally NOT
    checked here (a validated plan's application_runtime verifier is
    normally type=judgment/tool_name=None, but the CALLER already only
    reaches this function under write_scope_mode=DENY_ALL, which is itself
    gated on execution_role=verification/planned_files=[] upstream - the
    verifier_kind+requires_runtime_execution pair is what actually
    identifies "this is the runtime-execution check," not the type tag)."""
    return [
        requirement for requirement in required_verification
        if requirement.get("verifier_kind") == "application_runtime"
        and requirement.get("requires_runtime_execution") is True
    ]


@dataclass(frozen=True)
class VerificationRequest:
    required_verification: Sequence[Dict[str, Any]]
    # Files the compile check covers: the subtask's established files.
    known_files: Sequence[str]
    attempt_number: int


@dataclass(frozen=True)
class VerificationResult:
    """Every declared verifier passed. ``gates`` = the deterministic gate
    types that ran, in order."""

    gates: Tuple[str, ...]
    runtime_verified: bool


class VerificationCoordinator:
    """Runs a verification-only subtask's declared verifiers, in order."""

    def __init__(
        self, validator: Any, *, record_gate_outcome: Callable[[Dict[str, Any]], None],
        run_runtime_verification: Callable[[], Awaitable[None]],
        judge_suite: Optional[Callable[[Dict[str, Any]], Tuple[bool, str, Dict[str, Any]]]] = None,
    ) -> None:
        self._validator = validator
        self._record = record_gate_outcome
        self._run_runtime_verification = run_runtime_verification
        # CANDIDATE-GATE-BASELINE-POLICY-001: the caller's suite judgment -
        # (blocking, evidence text, gate evidence) against the frozen PRE
        # baseline (kriya/workflow/suite_attribution.py); None keeps the raw
        # rule. Injected, so this module still never imports attempt.py.
        self._judge_suite = judge_suite

    def _gate(self, outcome_type: str, result: Dict[str, Any], attempt: int,
              extra: Optional[Dict[str, Any]] = None) -> List[str]:
        self._record({
            "attempt": attempt, "type": outcome_type,
            "success": result["success"], "output": result.get("output", ""),
            **execution_evidence(result), **(extra or {}),
        })
        return [outcome_type]

    def _raise(self, failure: Failure, extra: Optional[Dict[str, Any]] = None) -> None:
        if extra:
            failure.diagnostics = {**(failure.diagnostics or {}), **extra}
        self._record({**failure.to_gate_outcome(), **(extra or {})})
        raise QualityGateFailure(failure)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        attempt = request.attempt_number
        known_files = list(request.known_files)
        gates: List[str] = []
        for requirement in _directly_executable_verifiers(list(request.required_verification)):
            tool_name = requirement.get("tool_name")
            if tool_name == "compile":
                result = self._validator.run_compile_check(known_files)
                outcome_type = "compile"
            else:
                # test/tests/regression/quality_gates all ultimately mean "run
                # the test suite" for this direct-execution path - quality_gates
                # additionally implies compile must pass first.
                if tool_name == "quality_gates":
                    compile_result = self._validator.run_compile_check(known_files)
                    gates += self._gate("compile", compile_result, attempt)
                    if not compile_result["success"]:
                        self._raise(Failure(
                            type="compile",
                            message=f"COMPILATION FAILURE (verification-only subtask):\n{compile_result.get('output', '')}",
                            raw_output=compile_result.get("output", ""), attempt=attempt,
                        ))
                result = self._validator.run_tests()
                outcome_type = "test"
            blocking, evidence, extra = (self._judge_suite(result) if outcome_type == "test" and self._judge_suite is not None
                                         else (not result["success"], result.get("output", ""), {}))
            gates += self._gate(outcome_type, {**result, "success": not blocking}, attempt, extra)
            if blocking:
                self._raise(Failure(
                    type=outcome_type,
                    message=(
                        f"{outcome_type.upper()} FAILURE (verification-only subtask):\n"
                        f"{evidence}"
                    ),
                    raw_output=evidence, attempt=attempt,
                ), extra)

        runtime_verified = bool(_directly_executable_runtime_verifiers(list(request.required_verification)))
        if runtime_verified:
            await self._run_runtime_verification()
        return VerificationResult(gates=tuple(gates), runtime_verified=runtime_verified)
