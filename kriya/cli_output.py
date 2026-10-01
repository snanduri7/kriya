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

    def fail(self, error: str) -> None:
        self.result = {"status": "failed", "quality_gates_passed": False, "error": error}

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
                self.fail(str(exc) or type(exc).__name__)
                click.echo(f"Workflow error: {self.result['error']}", err=True)
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
