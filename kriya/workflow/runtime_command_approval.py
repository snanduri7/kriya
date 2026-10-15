"""Runtime-command approval: the typed subject a human is asked to approve before Kriya EXECUTES the generated application (GUI-RUNTIME-COMMAND-APPROVAL-001).

It is NOT a source-change approval (no files) and NOT a configuration approval (no durable authority). Its subject is exactly: the final argv list Kriya will run, in order, the
working directory, the input channel / stdin digest and the unit and criterion that asked for it. ``fingerprint`` is the digest of the part that decides WHAT RUNS (cwd, argv, input
channel, stdin digest); the criterion and unit identify why, and never change what executes.

Two call sites (kriya/workflow/attempt.py: the verification-only unit and the in-attempt gate) build the subject from the RESOLVED commands - after ``_resolve_run_command`` and the
runtime-verification contract - and execute that same resolved set. After the human answers, the call site derives the commands it is about to run again and compares fingerprints
(``approval_matches``): an approval of fingerprint A never executes fingerprint B.

Honest boundary (independent review of 52b89ea): the fingerprint binds the commands handed to the runtime executor. Two things happen AFTER that hand-off and are NOT in it: for a Python
project ``PolymorphicValidator._substitute_python_interpreter`` rewrites ``python``/``python3`` to the project's virtual-environment interpreter (it may also install dependencies, so it
cannot run before the review), and Kriya-owned build preparation (``_prepare_runtime``, ``_prepare_finite_command_runtime_artifacts``: e.g. ``mvn package`` when the command needs a
built artifact) runs first. The review copy says so; these are Kriya's own prerequisites, not commands a model chose. A callback that is a plain function (the terminal, the tests, every other caller) is asked the
legacy way - ``callback([], subject.reason)`` - so nothing outside a desktop launch changes.

Deliberately small: one subject, one optional channel on the existing callback. It is not an approval framework.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
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
    incomplete_reason: Optional[str] = None   # the runtime-verification contract could not be satisfied: nothing will run, so nothing is asked (not part of the fingerprint)

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.working_directory, self.commands, self.input_channel, self.stdin_sha256)


_WHITESPACE = re.compile(r"\s+")
# Interpreters that run CODE passed as an argument, and tools that move data over a network: a command naming them is never "the user's own words" however it is written (the person wrote a
# command line, not a program; review shows exactly what it would do).
_CODE_INTERPRETERS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "python", "python2", "python3", "node", "nodejs", "deno", "ruby", "perl", "php", "osascript", "powershell", "pwsh"})
_CODE_FLAGS = frozenset({"-c", "-e", "-E", "-p", "--eval", "--command", "-Command", "-EncodedCommand"})
_NETWORK_TOOLS = frozenset({"curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "ftp", "telnet", "socat"})
_NEGATION = re.compile(r"\b(?:not|never|no|don't|dont|doesn't|doesnt|do not|without|avoid|shouldn't|mustn't|cannot|can't|nor)\b", re.IGNORECASE)
_NEGATION_WINDOW_WORDS = 6


def _same_resolved_tool(written: str, argv0: str) -> bool:
    """``argv0`` is only a PATH-resolved form of ``written`` when it is absolute and is exactly what PATH resolves ``written`` to (or Kriya's own interpreter for a python the user named)."""
    if not os.path.isabs(argv0):
        return False
    resolved = shutil.which(os.path.basename(argv0))
    real = os.path.realpath(argv0)
    if resolved is not None and os.path.realpath(resolved) == real:
        return True
    return os.path.basename(argv0).startswith("python") and real == os.path.realpath(sys.executable)


def _is_code_or_network_command(argv: Any) -> bool:
    name = os.path.basename(str(argv[0]))
    if name in _NETWORK_TOOLS:
        return True
    return name in _CODE_INTERPRETERS and any(str(a) in _CODE_FLAGS for a in argv[1:])


def _negated(text: str, start: int) -> bool:
    """A mention shortly after a negation ("never run rm -rf build", "do not run python main.py") is the user forbidding the command, not writing it."""
    words = text[:start].split()[-_NEGATION_WINDOW_WORDS:]
    return _NEGATION.search(" ".join(words)) is not None


def commands_authored_in_goal(commands: Any, goal: str, stdin_sha256: Optional[str] = None) -> bool:
    """True only when EVERY command that will execute is the user's own, exact words: its argv appears verbatim (whitespace-normalised, on word boundaries) in the user's goal text,
    in the order the goal wrote them, and nothing Kriya or a model added exists (no synthetic stdin). The executable must be the one the user WROTE: a different ``argv[0]`` is accepted
    only when it is the absolute path PATH resolves that name to (or Kriya's own interpreter for ``python``) - never ``./mvn`` or ``/tmp/x/python``. A single bare word (``make``, ``pytest``,
    ``python``) is not a command line: it counts only inside backticks or quotes. A mention that follows a negation ("never run ...") is not authorship. Interpreters given code to run
    (``bash -c ...``, ``python -c ...``) and network tools are never authored-exempt. A goal that merely NAMES a tool, a judge-invented command, an added or reordered argument, a substituted
    executable or an appended input are not authorship. The judge's own ``command_source`` label is never consulted: it is a model's claim, and this is the deterministic check of it."""
    if stdin_sha256 is not None or not commands:
        return False
    text = _WHITESPACE.sub(" ", goal or "")
    position = 0
    for argv in commands:
        if not argv or not all(isinstance(a, str) and a for a in argv) or _is_code_or_network_command(argv):
            return False
        written = [" ".join(argv)]
        if len(argv) > 0 and argv[0] != os.path.basename(argv[0]) and _same_resolved_tool(os.path.basename(argv[0]), argv[0]):
            written.append(" ".join((os.path.basename(argv[0]), *argv[1:])))
        found = None
        for candidate in dict.fromkeys(written):
            for match in re.finditer(r"(?<![\w./-])" + re.escape(candidate) + r"(?![\w-])", text):
                if match.start() < position or _negated(text, match.start()):
                    continue
                if len(argv) == 1 and not (text[max(0, match.start() - 1):match.start()] in "`'\"" and text[match.end():match.end() + 1] in "`'\""):
                    continue
                if found is None or match.start() < found.start():
                    found = match
                break
        if found is None:
            return False
        position = found.end()
    return True


def build_subject(*, unit_id: Optional[str], criterion: str, working_directory: str, commands: List[List[str]], input_channel: str,
                  stdin_payload: Optional[str], reason: str, incomplete_reason: Optional[str] = None) -> RuntimeCommandSubject:
    return RuntimeCommandSubject(unit_id=unit_id, criterion=criterion, working_directory=working_directory, commands=tuple(tuple(str(a) for a in c) for c in commands),
                                 input_channel=input_channel or "none", stdin_sha256=stdin_digest(stdin_payload), reason=reason, incomplete_reason=incomplete_reason)


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
