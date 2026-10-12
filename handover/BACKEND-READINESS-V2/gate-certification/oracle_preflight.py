#!/usr/bin/env python3
"""Observational qualification preflight (owner decision 2026-10-12 item 5).

A frozen oracle reports a broken build/test environment as a plain non-zero exit for several tasks (T2, T3, S4_A: doc 11
section 3), indistinguishable by exit code from a rejected candidate. This module classifies a finished oracle run from its
exit code and log WITHOUT changing the oracle:

  PASS                      exit 0
  ENVIRONMENT_UNAVAILABLE   non-zero exit and a known environment signature anywhere in the log (checked FIRST)
  REJECT                    non-zero exit, no environment signature, and evidence that the candidate was judged: tests executed (N > 0), a compile
                            or syntax error in the candidate, or an explicit contract line (TAMPERED / FORBIDDEN / NOOP / COMPAT)
  INDETERMINATE             non-zero exit with neither: the run proved nothing about the candidate

Only PASS and REJECT may be counted as a candidate verdict. ENVIRONMENT_UNAVAILABLE and INDETERMINATE make the qualification
INVALID FOR THAT RUN: the run is neither a SAFE_FAILURE nor a GENUINE, and is repeated after the environment is repaired.
`preflight(task)` runs the frozen ACCEPT on the pristine baseline tree and requires REJECT: a viable environment must fail
the baseline by executed tests, not by build breakage.

    python3 oracle_preflight.py preflight T2,T3,S4_A
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

PASS, REJECT, ENVIRONMENT_UNAVAILABLE, INDETERMINATE = "PASS", "REJECT", "ENVIRONMENT_UNAVAILABLE", "INDETERMINATE"

_TESTS_RAN = (
    re.compile(r"\b[1-9]\d* (?:passed|failed)\b"),                 # pytest summary
    re.compile(r"Tests run: [1-9]\d*"),                            # Maven surefire
    re.compile(r"\btests=\"?[1-9]\d*"),                            # xml totals / UPSTREAM TOTALS / HIDDEN lines
    re.compile(r"\b[1-9]\d* tests completed"),                     # Gradle
)
_CANDIDATE_FAULT = (
    re.compile(r"COMPILATION ERROR|Compilation failure|error: cannot find symbol|:compile\w* FAILED|Execution failed for task ':[^']*:compile"),
    re.compile(r"SyntaxError|IndentationError|ERROR collecting|error during collection|ModuleNotFoundError|ImportError"),
    re.compile(r"\.java:\[\d+,\d+\]|\.java:\d+: error:"),
    re.compile(r"^(?:TAMPERED|FORBIDDEN_CHANGES|NOOP\w*|COMPAT\w*)=(?!\[\]$|0\b)", re.M),
)
_ENVIRONMENT = (
    re.compile(r"INSTALL_FAILED|ORACLE_ENVIRONMENT"),
    re.compile(r"in offline mode and the artifact .* has not been downloaded"),
    re.compile(r"Non-resolvable import POM|Unresolveable build extension|Could not resolve|Could not GET|Could not transfer"),
    re.compile(r"Downloading from https?://services\.gradle\.org\S* failed"),
    re.compile(r"No matching distribution found"),
)


def tests_ran(log: str) -> bool:
    return any(pattern.search(log) for pattern in _TESTS_RAN)


def classify_run(exit_code: int, log: str) -> tuple[str, str]:
    """(verdict, reason) for one oracle run."""
    if exit_code == 0:
        return PASS, "oracle exit 0"
    # Environment first: a multi-phase oracle can print executed tests and then die resolving dependencies, and a
    # compile task can fail on 'Could not resolve'. Either way the run cannot be trusted as a verdict on the candidate,
    # and repeating a run is cheap while a mislabelled rejection is not.
    if any(pattern.search(log) for pattern in _ENVIRONMENT):
        return ENVIRONMENT_UNAVAILABLE, "known environment signature present: the run does not judge the candidate"
    if any(pattern.search(log) for pattern in _CANDIDATE_FAULT):
        return REJECT, "candidate fault: compile/syntax/import error or explicit contract violation"
    if tests_ran(log):
        return REJECT, "tests executed and the candidate failed them"
    return INDETERMINATE, "no test executed and no known cause: the run proves nothing about the candidate"


def counts_as_candidate_verdict(verdict: str) -> bool:
    return verdict in (PASS, REJECT)


def preflight(task: str) -> tuple[str, str]:
    """Run the frozen ACCEPT oracle on the pristine baseline of ``task``; a viable environment rejects it by tests."""
    import subprocess

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gate_controls as g

    with tempfile.TemporaryDirectory(dir=g.OUT) as work:
        tree, scr, log = Path(work) / "tree", Path(work) / "scr", Path(work) / "log.txt"
        g.build_tree(task, "base", tree)
        subprocess.run(["rsync", "-a", "--exclude", ".kriya", f"{tree}/", f"{scr}/"], check=True)
        result = g.run_oracle(task, "accept", tree, scr, log)
        verdict, reason = classify_run(result["exit"], log.read_text(errors="replace"))
    if task in ("T6", "S6_A"):  # genuine no-op tasks: the pristine baseline is the correct answer
        return ("VIABLE", "baseline accepted") if verdict == PASS else (verdict, reason)
    return ("VIABLE", reason) if verdict == REJECT else (verdict, reason)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "preflight":
        for name in sys.argv[2].split(","):
            print(name, *preflight(name))
