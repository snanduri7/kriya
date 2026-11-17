"""One terminal JSON emission boundary for the generate command callback."""

import json
import sys
from typing import Any, Dict, Optional

import click

from kriya.context.workspace_identity import WorkspaceOwnershipError


class GenerateOutput:
    """Keep workflow payloads intact; represent callback failures fail-closed.

    Click argument parsing and parent-command configuration happen before this
    boundary. Human-readable invocations are untouched. This is presentation
    only: it never grants mutation authority or changes verification results.
    """

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.result = None
        self.stdout = None

    def fail(self, error: str, failure_type: Optional[str] = None) -> None:
        self.result = {"status": "failed", "quality_gates_passed": False, "error": error}
        if failure_type:
            # The result family's canonical ``failure_type`` (an enforce planning failure and the .enforce trace row carry it):
            # present only when the failure is an exception whose class is the authority.
            self.result["failure_type"] = failure_type

    def fail_with(self, error: BaseException) -> None:
        """Fail from the propagated exception itself: its message is the error and its class the ``failure_type`` - the one
        place the class is read, so the JSON result and the human line can never disagree."""
        self.fail(str(error) or type(error).__name__, failure_type=type(error).__name__)
        if isinstance(error, WorkspaceOwnershipError):
            # D2 M4 follow-up: an ownership refusal carries its closed reason code (WORKSPACE_OWNERSHIP_MISSING / _MISMATCH), so a consumer keys on
            # it rather than on the class name or the message.
            self.result["reason_codes"] = [error.reason_code]

    def failure_line(self) -> str:
        """The human wording of the failure just recorded: ``<failure_type>: <error>`` (or the error alone)."""
        result = self.result or {}
        failure_type = result.get("failure_type")
        return f"{failure_type}: {result['error']}" if failure_type else str(result.get("error"))

    def __enter__(self):
        if self.enabled:
            self.stdout = sys.stdout
            sys.stdout = sys.stderr
        return self

    def __exit__(self, exc_type, exc, traceback):
        if not self.enabled:
            return False
        exit_code = None
        try:
            if exc is not None and not isinstance(exc, (SystemExit, click.exceptions.Exit)):
                self.fail_with(exc)
                click.echo(f"Workflow error: {self.failure_line()}", err=True)
                exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
            if self.result is None:
                self.fail("Generation exited before a workflow result was available")
                exit_code = 1
            try:
                encoded = json.dumps(self.result, indent=2, default=str)
            except (TypeError, ValueError, RecursionError):
                self.fail("Workflow result could not be serialized")
                encoded = json.dumps(self.result)
                exit_code = 1
        finally:
            sys.stdout = self.stdout
        click.echo(encoded, file=self.stdout)
        if exit_code is not None:
            raise SystemExit(exit_code)
        return False


# C-02 (S6): the ONE table of process exit codes of a run result, used by every exit site that ends a mutating run (`generate`, the
# milestone sequence, proposal execution, `fix`). 2 (invalid arguments), 4 (refused tool) and 130 (interrupt) belong to other commands;
# 5 is the first value no command used (tests/test_s6_result_contract.py pins the table).
EXIT_SUCCESS = 0
EXIT_FAILED = 1
EXIT_KNOWLEDGE_GAP = 3
EXIT_APPLIED_REQUIREMENTS_OPEN = 5
RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN = "applied_requirements_open"


def result_exit_code(result: Any) -> int:
    """The exit code of a RECONCILED run result. It reads the result's canonical words only (``status``, ``quality_gates_passed``):
    nothing here recomputes success. A milestone result carries no ``quality_gates_passed`` and succeeds by its ``status`` alone. The
    knowledge-gap exit (EXIT_KNOWLEDGE_GAP) stays with the two commands that have it (proposal execution, `fix`): they exit before here."""
    if not isinstance(result, dict):
        return EXIT_FAILED
    status = str(result.get("status") or "").lower()
    if status == RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN:
        return EXIT_APPLIED_REQUIREMENTS_OPEN
    quality = result.get("quality_gates_passed")
    if quality is True or (quality is None and status == "success"):
        return EXIT_SUCCESS
    return EXIT_FAILED


def applied_requirements_open_lines(result: Dict[str, Any]) -> list:
    """The human lines of an APPLIED_REQUIREMENTS_OPEN result: what was applied, which requirements are open, and what the sealed
    contract accepts to close each (``open_requirement_authorities``). A projection of the result; it states no verdict of its own."""
    lines = ["Result: APPLIED, REQUIREMENTS OPEN (not successful). The change was applied and its technical gates passed; "
             "it stays applied, nothing was rolled back."]
    if result.get("files"):
        lines.append(f"Files written to workspace: {', '.join(str(f) for f in result['files'])}")
    truth = result.get("requirement_truth") or {}
    if truth.get("status"):
        lines.append(f"Requirement truth: {truth.get('status')} ({truth.get('verified')} verified, {truth.get('accepted')} accepted, "
                     f"{truth.get('total')} mandatory)")
    authorities = result.get("open_requirement_authorities") or {}
    texts = {item.get("id"): item.get("text") for item in truth.get("unresolved") or [] if isinstance(item, dict)}
    outcomes = {item.get("id"): item.get("outcome") for item in truth.get("unresolved") or [] if isinstance(item, dict)}
    for requirement_id in result.get("open_requirements") or []:
        text = texts.get(requirement_id)
        lines.append(f"  open: {requirement_id} ({outcomes.get(requirement_id) or 'open'})" + (f": {text}" if text else ""))
        accepted = authorities.get(requirement_id)
        if accepted:
            lines.append(f"    can be closed by: {'; '.join(accepted)}")
    if not result.get("open_requirements"):
        lines.append(f"  {result.get('error') or 'no valid requirement truth was recorded for this run'}")
    return lines


def no_progress_stop_message(progress: Optional[Dict[str, Any]]) -> str:
    """PRD-026: the terminal message for a run the retry-progress invariant
    stopped (failure_category ``no_progress``)."""
    progress = progress or {}
    return (
        f"\n[NO PROGRESS] {progress.get('terminal_reason') or 'RETRY_NO_PROGRESS_EXHAUSTED'}: Kriya stopped "
        f"retrying after {progress.get('consecutive_no_progress_attempts', 0)} consecutive attempts without "
        f"material progress (last classification {progress.get('classification')}, "
        f"{progress.get('distinct_vectors', 0)} distinct retry states, "
        f"{progress.get('sampling_resamples', 0)} sampling resamples). Another attempt on the same evidence "
        "could not change the outcome; the last failure is recorded in this run's gate outcomes and trace."
    )
