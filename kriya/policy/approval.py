"""Approval helpers owned by AUTHORITY (full modularization, slice C3; MODULE-CONTRACTS.md §4).

Moved here unchanged (bodies, names, messages and reason codes):

* from ``kriya/workflow/runtime_command_approval.py`` (now a facade, removed in P1): the runtime-command subject, its fingerprint, the
  reviewability rule, the typed decision, the typed callback and ``ask``, the goal-authorship check (its own docstring follows below);
* from ``kriya/control/launch_approval.py`` (keeps the review record, the wait and the decision action; re-exports these names, removed
  in P1): the review kinds, the refusal codes and ``ApprovalRefused``, the candidate digest, the reviewer-section delimiter and
  ``split_reason``;
* from ``kriya/workflow/attempt.py`` (keeps both names as kernel adapters that raise its own ``Failure``): the two source-review
  decisions ``defer_execution_until_source_review`` and ``stale`` (the post-approval guard), with the stop text.

``RuntimeCommandReviewPort`` is what ``control.launch_approval`` takes as a parameter for the runtime-command review (fingerprint,
reviewability, decision type): it no longer imports the kernel-side module lazily. ``RUNTIME_COMMAND_REVIEW`` (this module's own
functions, looked up at call time) is the default every caller gets.

This module imports nothing from Kriya but the FOUNDATION redaction owner (pure, standard library only; S2d): it is a leaf of
AUTHORITY, so ``control.launch_approval`` and the kernel may import it at module level without a cycle.

S2d (OI-153, owner decision 2026-10-20 (c)): a runtime-command subject whose exact bytes carry a value the one redaction owner
hides (``commands_carry_secret``: a positional ``mysql -p<pw>``, ``sshpass -p <pw>``, a ``--token <pw>`` pair, ...) can be
reviewed faithfully by nobody - shown raw it discloses the secret, shown redacted it is not what runs - so it is never offered:
a plain (legacy) callback is not asked (``ask``), the desktop review is not recorded (``control.launch_approval``), and the
CLI's typed channel refuses with ``RUNTIME_COMMAND_SECRET_REFUSAL``. The operator's own authority is not a review and is
unchanged in what it may run: ``kriya generate --yes`` outside a launch, an autonomy mode without review, and (legacy
channel) a command the person wrote in the goal (``commands_authored_in_goal``, never asked).

--- moved from kriya/workflow/runtime_command_approval.py ---

Runtime-command approval: the typed subject a human is asked to approve before Kriya EXECUTES the generated application (GUI-RUNTIME-COMMAND-APPROVAL-001).

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

import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from kriya.foundation.redaction import redact_value

logger = logging.getLogger(__name__)

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


def commands_carry_secret(commands: Any) -> bool:
    """S2d (OI-153 (c)): True when any command, read WHOLE as an argv (a ``--token`` pair, a command's own positional grammar),
    carries a value the one redaction owner hides - no human review of those exact bytes can be faithful."""
    return any(redact_value(list(c)) != list(c) for c in commands)


# What the person is told instead of a review (the launch record's ``approval_required`` reason, the terminal line, the log
# line of a legacy channel). At most 400 characters: the launch record keeps that much of a reason.
RUNTIME_COMMAND_SECRET_REFUSAL = (
    "Not offered for review: the command carries a password or token on its command line. Shown as written it would expose "
    "the secret, shown hidden you would approve bytes you cannot read, so nothing ran. Pass the secret on stdin, in a file "
    "or the environment (docker login --password-stdin, sshpass -f), or authorize it yourself: kriya generate --yes in "
    "a terminal, or an autonomy mode without review."
)


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


class ApprovalDecision(str, Enum):
    """The closed vocabulary of an approval decision. UNAVAILABLE is "no decision could be obtained" and is NOT a decline: nobody said no."""

    APPROVED = "approved"
    DECLINED = "declined"
    UNAVAILABLE = "unavailable"

    def __bool__(self) -> bool:
        # A str member is truthy by its text, so `if outcome.decision:` would read DECLINED and UNAVAILABLE as approval: same fail-loud rule as ApprovalOutcome.
        raise TypeError("an ApprovalDecision has no truth value; compare it with `is` or read ApprovalOutcome.approved / .declined / .unavailable")


# The closed reason-code table of an approval outcome (key policy on the code, never on prose). The pairing with the decision is enforced by ApprovalOutcome.
APPROVAL_GRANTED = "APPROVAL_GRANTED"                          # APPROVED: an explicit approval
APPROVAL_DECLINED = "APPROVAL_DECLINED"                        # DECLINED: a person (or the terminal prompt) said no
APPROVAL_LAUNCH_CANNOT_ASK = "APPROVAL_LAUNCH_CANNOT_ASK"      # UNAVAILABLE: a desktop launch has no approval action for this decision (an empty change set, a policy action) or could not record the review
APPROVAL_REVIEW_EXPIRED = "APPROVAL_REVIEW_EXPIRED"            # UNAVAILABLE: the review ended with no decision (expired, the host went away, the wait was interrupted)
APPROVAL_DECISION_NOT_RECORDED = "APPROVAL_DECISION_NOT_RECORDED"  # UNAVAILABLE: no decision was captured - one arrived but could not be made durable, or a terminal prompt ended without an explicit y/n (EOF, bare Enter, Ctrl-C) - so it is not honoured either way
APPROVAL_RESULT_UNRECOGNIZED = "APPROVAL_RESULT_UNRECOGNIZED"  # UNAVAILABLE: the callback answered with something that is neither a bool nor an ApprovalOutcome (never read through truthiness)
APPROVAL_CHANNEL_UNAVAILABLE = "APPROVAL_CHANNEL_UNAVAILABLE"  # UNAVAILABLE: a desktop launch's private review channel was not provided or failed closed (D2 M2), so no decision could be obtained - never a file fallback
UNAVAILABLE_REASON_CODES = (APPROVAL_LAUNCH_CANNOT_ASK, APPROVAL_REVIEW_EXPIRED, APPROVAL_DECISION_NOT_RECORDED, APPROVAL_RESULT_UNRECOGNIZED, APPROVAL_CHANNEL_UNAVAILABLE)
APPROVAL_REASON_CODES = (APPROVAL_GRANTED, APPROVAL_DECLINED, *UNAVAILABLE_REASON_CODES)


@dataclass(frozen=True)
class ApprovalOutcome:
    """The canonical answer of the general approval callback (``callback(files, reason)``): a closed decision and the reason code that explains it.

    It deliberately has NO truth value: a site that wrote ``if approved:`` on an outcome would read UNAVAILABLE or DECLINED as approval, so ``bool(outcome)`` raises. Read ``.approved``."""

    decision: ApprovalDecision
    reason_code: str

    def __post_init__(self) -> None:
        expected = {ApprovalDecision.APPROVED: (APPROVAL_GRANTED,), ApprovalDecision.DECLINED: (APPROVAL_DECLINED,), ApprovalDecision.UNAVAILABLE: UNAVAILABLE_REASON_CODES}
        if not isinstance(self.decision, ApprovalDecision) or self.reason_code not in expected[self.decision]:
            raise ValueError(f"approval outcome {self.decision!r} cannot carry reason code {self.reason_code!r}")

    def __bool__(self) -> bool:
        raise TypeError("an ApprovalOutcome has no truth value; read .approved / .declined / .unavailable")

    @property
    def approved(self) -> bool:
        return self.decision is ApprovalDecision.APPROVED

    @property
    def declined(self) -> bool:
        return self.decision is ApprovalDecision.DECLINED

    @property
    def unavailable(self) -> bool:
        return self.decision is ApprovalDecision.UNAVAILABLE

    @classmethod
    def approve(cls) -> "ApprovalOutcome":
        return cls(ApprovalDecision.APPROVED, APPROVAL_GRANTED)

    @classmethod
    def decline(cls) -> "ApprovalOutcome":
        return cls(ApprovalDecision.DECLINED, APPROVAL_DECLINED)

    @classmethod
    def unavailable_because(cls, reason_code: str) -> "ApprovalOutcome":
        return cls(ApprovalDecision.UNAVAILABLE, reason_code)


def classify_approval_answer(answer: Any) -> ApprovalOutcome:
    """The ONE classification of an approval callback's RESOLVED answer: an ApprovalOutcome is itself; a real ``bool`` is the legacy answer (True -> APPROVED, False -> DECLINED);
    anything else - None, a number, a string, a mock, a still-unresolved awaitable - is UNAVAILABLE, never read through Python truthiness (an unresolved Task is truthy)."""
    if isinstance(answer, ApprovalOutcome):
        return answer
    if isinstance(answer, bool):
        return ApprovalOutcome.approve() if answer else ApprovalOutcome.decline()
    if inspect.iscoroutine(answer):
        answer.close()  # never awaited: do not leave a "coroutine was never awaited" warning behind a refusal
    logger.warning("approval callback answered with %s, which is neither a bool nor an ApprovalOutcome: not approved", type(answer).__name__)
    return ApprovalOutcome.unavailable_because(APPROVAL_RESULT_UNRECOGNIZED)


async def await_if_awaitable(answer: Any) -> Any:
    """Resolve ``answer`` when it is ANY awaitable (a coroutine, an asyncio Task or Future, an object with ``__await__``); anything else is returned as is. An exception raised by the
    awaitable propagates unchanged - an approval that failed is never an approval."""
    return await answer if inspect.isawaitable(answer) else answer


async def resolve_approval_result(answer: Any) -> ApprovalOutcome:
    """Resolve what an approval callback returned (sync or any awaitable) into the canonical ApprovalOutcome. Exceptions propagate (each caller keeps its own fail-closed policy for them)."""
    return classify_approval_answer(await await_if_awaitable(answer))


async def resolve_approval(callback: Callable[..., Any], files: List[Any], reason: str) -> ApprovalOutcome:
    """Ask the general approval callback and resolve its answer. The ONE place a caller turns ``callback(files, reason)`` into a decision: no caller resolves a coroutine and then
    tests the object's truthiness itself."""
    return await resolve_approval_result(callback(files, reason))


async def ask(callback: Callable[..., Any], subject: RuntimeCommandSubject) -> RuntimeCommandDecision:
    """Ask for the subject. A typed callback answers with the fingerprint its approval is bound to; a legacy callback's answer is resolved first, and only an APPROVED outcome is bound to the subject it was shown."""
    if isinstance(callback, TypedApprovalCallback):
        if not reviewable(subject):
            return RuntimeCommandDecision(None)
        answer = await await_if_awaitable(callback.runtime_command_review(subject))
        return answer if isinstance(answer, RuntimeCommandDecision) else RuntimeCommandDecision(None)
    if commands_carry_secret(subject.commands):
        # S2d: a plain callback shows ``subject.reason`` (which quotes the commands) through the redactor and cannot say
        # whether it would prompt or auto-approve; it is not asked. Fail closed: nothing runs.
        logger.warning(RUNTIME_COMMAND_SECRET_REFUSAL)
        return RuntimeCommandDecision(None)
    outcome = await resolve_approval(callback, [], subject.reason)
    return RuntimeCommandDecision(subject.fingerprint if outcome.approved else None)


def approval_matches(decision: RuntimeCommandDecision, about_to_run: RuntimeCommandSubject) -> bool:
    """True only when the approval is bound to exactly what is about to execute."""
    return decision.approved_fingerprint is not None and decision.approved_fingerprint == about_to_run.fingerprint


# ----------------------------------------------------------------------------- review kinds, refusal codes, candidate digest (moved from kriya/control/launch_approval.py)

REFUSAL_CODES = ("INVALID_REQUEST", "LAUNCH_NOT_FOUND", "LAUNCH_RECORD_UNREADABLE", "WRONG_WORKSPACE", "LAUNCH_ENDED", "LAUNCH_NOT_ACTIVE", "NOT_PENDING",
                 "STALE_REVIEW", "ALREADY_DECIDED", "REVIEW_EXPIRED", "DECISION_NOT_RECORDED", "WRONG_REVIEW_KIND",
                 "CAPABILITY_MISMATCH")  # D2 M2: the waiter's refusal of a decision that does not carry the live capability
# The two subjects a launch can hold for a human. A decision names the kind it is for, and `decide` refuses it for a review of the other kind: the change-approval action can never
# approve a runtime-command review, nor the reverse.
KIND_CHANGE, KIND_RUNTIME = "change_approval", "runtime_command"
KINDS = (KIND_CHANGE, KIND_RUNTIME)
WORKING_DIRECTORY_KIND = "ISOLATED_WORKTREE"   # Kriya runs verification in its isolated candidate worktree, never in the project folder


class ApprovalRefused(Exception):
    """A decision that was not accepted. ``code`` is one of REFUSAL_CODES."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# The delimiter between the approval policy note and the automated reviewer's report inside the reason text the change gate composes (kriya/workflow/workflow.py). One owner:
# the gate writes it with this constant and `split_reason` reads it with the same one, so a display never has to guess where the reviewer's words start.
REVIEW_SECTION_HEADER = "=== Automated Code Review ==="


def split_reason(reason: str) -> tuple:
    """(policy_note, review_summary) of a gate reason. review_summary is None when the reason carries no reviewer section; the text is never altered or summarised."""
    marker = f"\n\n{REVIEW_SECTION_HEADER}\n"
    if marker in reason:
        note, summary = reason.split(marker, 1)
        return note.strip(), summary.strip()
    return reason.strip(), None


def candidate_digest(files: List[Dict[str, str]]) -> str:
    """The identity of the candidate as the gate presented it: the per-file digests of the unified diffs, in order."""
    parts = [[str(f.get("filepath", "")), _sha(str(f.get("content", "")))] for f in files]
    return _sha(json.dumps(parts, separators=(",", ":"), sort_keys=True))


# ----------------------------------------------------------------------------- source review (moved from kriya/workflow/attempt.py)

CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW_MESSAGE = (
    "CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW: the candidate changed after you approved its source, so that approval no longer covers it. Nothing was run and nothing was applied."
)


def defer_execution_until_source_review(enabled: bool, autonomy_mode: str, wrote_source: Any) -> bool:
    """Demo V2 owner decision (2026-10-15, Gap B): in human-in-the-loop mode no model-authored candidate content is executed - POM validation, build, tests, runtime verification -
    before the person has reviewed and approved its source. ``enabled`` is the caller's "I run the post-approval phase" flag
    (``AttemptContext.defer_execution_until_source_review_enabled``), ``wrote_source`` whether this attempt wrote source (so the pre-apply source review WILL be asked). Held even
    with NO approver: the review can then never succeed, so the run ends at the approval gate (approval_required) having executed nothing. Never deferred: other autonomy modes
    (their approval policy - sensitive paths, size, process profile - is unchanged and out of this decision's scope) and an attempt that wrote no source (a verification-only unit
    has no source review)."""
    from kriya.config.authority import is_human_in_the_loop  # OI-135: the fail-closed reader (same AUTHORITY module)

    return bool(enabled and is_human_in_the_loop(autonomy_mode) and wrote_source)


def stale(guard: Optional[Callable[[], bool]]) -> bool:
    """True when the candidate is no longer the one the person approved: right before the reviewed commands execute, the post-approval guard (set only after a source
    approval; ``None`` outside that phase) is consulted exactly once and a False answer makes the approval stale - nothing may execute and nothing may be applied."""
    return not (guard is None or guard())


# ----------------------------------------------------------------------------- the review port control.launch_approval takes as a parameter

class RuntimeCommandReviewPort(Protocol):
    """What the runtime-command review record and wait need from the subject: its fingerprint, whether it can be shown, and the typed decision."""

    def fingerprint_of(self, working_directory: str, commands: Any, input_channel: str, stdin_sha256: Optional[str]) -> str: ...

    def reviewable(self, subject: RuntimeCommandSubject) -> bool: ...

    def decision(self, approved_fingerprint: Optional[str]) -> RuntimeCommandDecision: ...


class _PolicyRuntimeCommandReview:
    """The default port: this module's own functions, looked up at call time (a patch of the module name is what runs)."""

    def fingerprint_of(self, working_directory: str, commands: Any, input_channel: str, stdin_sha256: Optional[str]) -> str:
        return fingerprint_of(working_directory, commands, input_channel, stdin_sha256)

    def reviewable(self, subject: RuntimeCommandSubject) -> bool:
        return reviewable(subject)

    def decision(self, approved_fingerprint: Optional[str]) -> RuntimeCommandDecision:
        return RuntimeCommandDecision(approved_fingerprint)


RUNTIME_COMMAND_REVIEW: RuntimeCommandReviewPort = _PolicyRuntimeCommandReview()
