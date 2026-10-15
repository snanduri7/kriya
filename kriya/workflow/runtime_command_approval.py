"""Runtime-command approval: the typed subject a human is asked to approve before Kriya EXECUTES the generated application (GUI-RUNTIME-COMMAND-APPROVAL-001).

It is NOT a source-change approval (no files) and NOT a configuration approval (no durable authority). Its subject is exactly: the final argv list Kriya will run, in order, the
working directory, the input channel / stdin digest and the unit and criterion that asked for it. ``fingerprint`` is the digest of the part that decides WHAT RUNS (cwd, argv, input
channel, stdin digest); the criterion and unit identify why, and never change what executes.

Two call sites (kriya/workflow/attempt.py: the verification-only unit and the in-attempt gate) build the subject from the RESOLVED commands - after ``_resolve_run_command`` and the
runtime-verification contract - and execute that same resolved set. After the human answers, the call site derives the commands it is about to run again and compares fingerprints
(``approval_matches``): an approval of fingerprint A never executes fingerprint B. A callback that is a plain function (the terminal, the tests, every other caller) is asked the
legacy way - ``callback([], subject.reason)`` - so nothing outside a desktop launch changes.

Deliberately small: one subject, one optional channel on the existing callback. It is not an approval framework.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

REVIEW_KIND = "runtime_command"
MAX_REVIEW_ROUNDS = 2        # the commands drifted while the person decided: ask again once, then decline (never run a changed command)
MAX_COMMANDS = 20
MAX_ARGV_CHARS = 4000


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def stdin_digest(stdin_payload: Optional[str]) -> Optional[str]:
    return None if stdin_payload is None else _sha(stdin_payload)


def fingerprint_of(working_directory: str, commands: Any, input_channel: str, stdin_sha256: Optional[str]) -> str:
    """The digest of what decides WHAT RUNS: cwd, the argv lists in order, the input channel and the stdin digest."""
    return _sha(json.dumps({"cwd": working_directory, "commands": [list(c) for c in commands], "input_channel": input_channel, "stdin_sha256": stdin_sha256},
                           separators=(",", ":"), sort_keys=True))


@dataclass(frozen=True)
class RuntimeCommandSubject:
    """What the person approves. ``commands`` are the FINAL argv lists (resolved, contract applied); ``reason`` is the legacy prompt text, used only by a callback that does
    not understand the typed subject."""

    unit_id: Optional[str]
    criterion: str
    working_directory: str
    commands: Tuple[Tuple[str, ...], ...]
    input_channel: str
    stdin_sha256: Optional[str]
    reason: str
    environment_names: Tuple[str, ...] = ()   # names only, never values; Kriya passes no extra environment to the verification run

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.working_directory, self.commands, self.input_channel, self.stdin_sha256)


def build_subject(*, unit_id: Optional[str], criterion: str, working_directory: str, commands: List[List[str]], input_channel: str,
                  stdin_payload: Optional[str], reason: str) -> RuntimeCommandSubject:
    return RuntimeCommandSubject(unit_id=unit_id, criterion=criterion, working_directory=working_directory, commands=tuple(tuple(str(a) for a in c) for c in commands),
                                 input_channel=input_channel or "none", stdin_sha256=stdin_digest(stdin_payload), reason=reason)


def reviewable(subject: RuntimeCommandSubject) -> bool:
    """A command set that cannot be shown in full is not offered for approval (an unreviewable command is never approved)."""
    return (0 < len(subject.commands) <= MAX_COMMANDS and all(c and all(isinstance(a, str) for a in c) and sum(len(a) for a in c) <= MAX_ARGV_CHARS for c in subject.commands)
            and bool(subject.working_directory))


@dataclass(frozen=True)
class RuntimeCommandDecision:
    """``approved_fingerprint`` is the fingerprint the human's approval is durably bound to; None when not approved."""

    approved_fingerprint: Optional[str]

    @property
    def approved(self) -> bool:
        return self.approved_fingerprint is not None


class TypedApprovalCallback:
    """The approval callback of a desktop launch: a plain ``callback(files, reason)`` for every legacy approval AND one typed channel for the runtime-command subject."""

    def __init__(self, change_callback: Callable[[List[Any], str], Any], runtime_command_callback: Callable[[RuntimeCommandSubject], Any]) -> None:
        self._change_callback = change_callback
        self.runtime_command_review = runtime_command_callback

    def __call__(self, files: List[Any], reason: str) -> Any:
        return self._change_callback(files, reason)


async def ask(callback: Callable[..., Any], subject: RuntimeCommandSubject) -> RuntimeCommandDecision:
    """Ask for the subject. A typed callback answers with the fingerprint its approval is bound to; a legacy callback's boolean is bound to the subject it was shown."""
    if isinstance(callback, TypedApprovalCallback):
        if not reviewable(subject):
            return RuntimeCommandDecision(None)
        answer = callback.runtime_command_review(subject)
        if asyncio.iscoroutine(answer):
            answer = await answer
        return answer if isinstance(answer, RuntimeCommandDecision) else RuntimeCommandDecision(None)
    answer = callback([], subject.reason)
    if asyncio.iscoroutine(answer):
        answer = await answer
    return RuntimeCommandDecision(subject.fingerprint if bool(answer) else None)


def approval_matches(decision: RuntimeCommandDecision, about_to_run: RuntimeCommandSubject) -> bool:
    """True only when the approval is bound to exactly what is about to execute."""
    return decision.approved_fingerprint is not None and decision.approved_fingerprint == about_to_run.fingerprint
