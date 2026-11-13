"""KERNEL: what the application run Kriya executed on a candidate OBSERVED (REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001).

The runtime gate is generic: it runs the approved command and records its steps. This module is the one reader of that
evidence for the two consumers that need an observation rather than a verdict:

- the RUNTIME_STDOUT closer (``contract_closers.close_runtime_stdout_requirements``), which closes only the requirement whose
  contract binding states the expected output ("It prints hello world when executed.");
- the result's "Observed output" (``observed_run_output``): the same evidence, capped and redacted for a person to read.

An observation exists only when ALL of these hold - otherwise it is UNAVAILABLE with a closed reason code and nothing is
closed from it:

- the gate outcome is a ``run_verification`` outcome of the attempt asked about that passed (never a ``test`` outcome, never a
  managed-service probe, never another attempt's);
- it carries the digest of the candidate files at the time of the run (``candidate_digest``) and the candidate still has the
  same digest now (the run was of THIS candidate, not an earlier or a later one);
- every step exited 0 without timing out and the final step captured its whole stdout (``stdout_lost_chars`` absent);
- the final step is the project interpreter running exactly one file the candidate wrote (``python3 hello.py``): a model-chosen
  ``echo hello world``, ``python -c ...`` or ``cat`` of something is NOT the candidate's behaviour, whatever it printed.

The comparison is exact: the whole stdout, case-sensitive, one conventional trailing newline normalised, never a substring.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from kriya.config.environment import PROJECT_PYTHON_INTERPRETER
from kriya.foundation.redaction import redact_text
from kriya.platform.filesystem_semantics import PathRelation, path_relation

NO_APPLICATION_RUN = "NO_APPLICATION_RUN"
CANDIDATE_NOT_STAMPED = "CANDIDATE_NOT_STAMPED"
CANDIDATE_CHANGED = "CANDIDATE_CHANGED"
STEP_NOT_PASSED = "STEP_NOT_PASSED"
STDOUT_UNAVAILABLE = "STDOUT_UNAVAILABLE"
STDOUT_LOST = "STDOUT_LOST"
COMMAND_NOT_A_CANDIDATE_FILE = "COMMAND_NOT_A_CANDIDATE_FILE"
SUBJECT_NOT_EXECUTED = "SUBJECT_NOT_EXECUTED"
RUNTIME_OBSERVATION_REASON_CODES = frozenset({
    NO_APPLICATION_RUN, CANDIDATE_NOT_STAMPED, CANDIDATE_CHANGED, STEP_NOT_PASSED, STDOUT_UNAVAILABLE, STDOUT_LOST,
    COMMAND_NOT_A_CANDIDATE_FILE, SUBJECT_NOT_EXECUTED,
})

_NOT_AN_APPLICATION_RUN = ("managed_service_probe",)
MAX_OBSERVED_CHARS = 2000
_ABSENT = b"\x00absent"
_UNREADABLE = b"\x00unreadable"


def candidate_files_digest(root: str, paths: Iterable[str]) -> str:
    """A digest of the bytes of ``paths`` under ``root``, taken when the runtime gate passes and again when a closer reads
    the evidence: equal digests mean the run was of these bytes. A path that is absent or is not a regular file inside
    ``root`` contributes a fixed marker (so it still compares equal to itself and unequal to a present file)."""
    digest = hashlib.sha256()
    real_root = os.path.realpath(root)
    for relpath in sorted(set(paths)):
        full = os.path.join(root, relpath)
        data: bytes
        try:
            if os.path.islink(full) or path_relation(real_root, full) is not PathRelation.WITHIN:
                data = _UNREADABLE
            elif not os.path.isfile(full):
                data = _ABSENT
            else:
                with open(full, "rb") as handle:
                    data = handle.read()
        except OSError:
            data = _UNREADABLE
        digest.update(relpath.encode("utf-8") + b"\x00" + str(len(data)).encode("ascii") + b"\x00" + data + b"\x01")
    return digest.hexdigest()


@dataclass(frozen=True)
class RunObservation:
    """``available`` False carries ``reason_code`` (a member of ``RUNTIME_OBSERVATION_REASON_CODES``); True carries the
    final step's whole ``stdout`` and the run's ``command`` (argv of the final step), ``exit_code`` and ``script``."""
    available: bool
    reason_code: Optional[str] = None
    stdout: str = ""
    command: Sequence[str] = ()
    exit_code: Optional[int] = None
    script: Optional[str] = None
    candidate_digest: Optional[str] = None


def _unavailable(reason_code: str) -> RunObservation:
    return RunObservation(False, reason_code)


def _steps_of(outcome: Mapping[str, Any]) -> Optional[List[Mapping[str, Any]]]:
    steps = outcome.get("steps")
    if isinstance(steps, str):  # the persisted form of the same list
        try:
            steps = json.loads(steps)
        except ValueError:
            return None
    if not isinstance(steps, list) or not steps or not all(isinstance(step, dict) for step in steps):
        return None
    return steps


def _normalised_script(token: str) -> Optional[str]:
    if not token or token.startswith(("/", "-", "~")) or token != posixpath.normpath(token):
        return None
    if any(part in ("", ".", "..") for part in token.split("/")):
        return None
    return token


def application_run_observation(
    gate_outcomes: Optional[Sequence[Mapping[str, Any]]], attempt: Any, *, candidate_root: str, written_files: Iterable[str],
) -> RunObservation:
    """The observation of the LAST passed application run of ``attempt`` on the candidate at ``candidate_root`` whose written
    files are ``written_files`` (see the module docstring for every condition). Pure read of already-recorded evidence plus one
    digest of the candidate."""
    passed = [o for o in (gate_outcomes or ()) if isinstance(o, Mapping) and o.get("type") == "run_verification"
              and o.get("success") is True and o.get("attempt") == attempt
              and o.get("graded_by") not in _NOT_AN_APPLICATION_RUN]
    if not passed:
        return _unavailable(NO_APPLICATION_RUN)
    outcome = passed[-1]
    stamped = outcome.get("candidate_digest")
    if not isinstance(stamped, str) or not stamped:
        return _unavailable(CANDIDATE_NOT_STAMPED)
    written = sorted(set(written_files))
    if candidate_files_digest(candidate_root, written) != stamped:
        return _unavailable(CANDIDATE_CHANGED)
    steps = _steps_of(outcome)
    if steps is None:
        return _unavailable(STDOUT_UNAVAILABLE)
    if any(step.get("exit_code") != 0 or step.get("timed_out") or step.get("target_started") is False for step in steps):
        return _unavailable(STEP_NOT_PASSED)
    final = steps[-1]
    if final.get("stdout_lost_chars"):
        return _unavailable(STDOUT_LOST)
    stdout = final.get("stdout")
    command = final.get("command")
    if not isinstance(stdout, str) or not isinstance(command, list) or not all(isinstance(t, str) for t in command):
        return _unavailable(STDOUT_UNAVAILABLE)
    script = _normalised_script(command[1]) if len(command) == 2 else None
    if len(command) != 2 or command[0] != PROJECT_PYTHON_INTERPRETER or script is None or script not in set(written):
        return _unavailable(COMMAND_NOT_A_CANDIDATE_FILE)
    return RunObservation(True, None, stdout, tuple(command), final.get("exit_code"), script, stamped)


def normalise_stdout(stdout: str) -> str:
    """Exactly ONE conventional trailing newline removed; nothing else is trimmed."""
    if stdout.endswith("\r\n"):
        return stdout[:-2]
    if stdout.endswith("\n"):
        return stdout[:-1]
    return stdout


def stdout_equals(stdout: str, expected: str) -> bool:
    return normalise_stdout(stdout) == expected


def display_command(argv: Sequence[str]) -> str:
    """How the result shows the run's command: the project interpreter is a scratch environment, shown as ``python3``
    (the same mapping the verified run recipe applies)."""
    shown = ["python3" if argv and argv[0] == PROJECT_PYTHON_INTERPRETER else argv[0], *argv[1:]] if argv else []
    return " ".join(shown)


def observed_run_output(
    gate_outcomes: Optional[Sequence[Mapping[str, Any]]], attempt: Any,
) -> Optional[Dict[str, Any]]:
    """The run's Observed output for the result: command, exit status and the final step's stdout, capped and redacted, with
    ``truncated`` set when the characters were cut here and ``stdout_lost`` when the capture itself lost output. ``None``
    when no passed application run of ``attempt`` recorded steps. It uses the same pass conditions as the closer's evidence
    except the candidate digest (this is display, never closure): it shows what the run printed, it proves nothing."""
    passed = [o for o in (gate_outcomes or ()) if isinstance(o, Mapping) and o.get("type") == "run_verification"
              and o.get("success") is True and o.get("attempt") == attempt
              and o.get("graded_by") not in _NOT_AN_APPLICATION_RUN]
    if not passed:
        return None
    steps = _steps_of(passed[-1])
    if steps is None:
        return None
    final = steps[-1]
    command, stdout = final.get("command"), final.get("stdout")
    if not isinstance(command, list) or not command or not all(isinstance(t, str) for t in command) or not isinstance(stdout, str):
        return None
    redacted = redact_text(stdout)
    lost = int(final.get("stdout_lost_chars") or 0)
    return {
        "command": redact_text(display_command(command))[:400],
        "exit_status": final.get("exit_code") if isinstance(final.get("exit_code"), int) else None,
        "stdout": redacted[:MAX_OBSERVED_CHARS],
        "truncated": len(redacted) > MAX_OBSERVED_CHARS,
        "stdout_lost": lost > 0,
        "attempt": attempt if isinstance(attempt, int) else None,
    }
