"""One terminal JSON emission boundary for the generate command callback."""

import json
import sys
from typing import Any, Dict, Optional

import click


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
