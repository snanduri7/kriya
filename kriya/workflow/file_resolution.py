"""Expected-vs-written file detection for the Developer retry loop completeness check and missing-file recovery. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import asyncio
import difflib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from kriya.workflow.failure import Failure

logger = logging.getLogger(__name__)


def extract_target_test(error_context: str, files_written: List[str]) -> Optional[str]:
    for f in files_written:
        if "test" in f.lower() or "spec" in f.lower():
            return f
    if error_context:
        matches = re.findall(r'(?:test_\w+|Test\w+)', error_context)
        if matches:
            exclude = {"test", "testing", "tests", "Test"}
            valid = [m for m in matches if m not in exclude]
            if valid:
                return valid[0]
    return None


def normalize_written_filepath(filepath: str, workspace_path: str) -> Optional[str]:
    """Normalizes a filepath the Developer Agent returned to one relative to
    workspace_path. The Agent occasionally returns an absolute path instead of a
    relative one (observed in a real run); os.path.join(base, filepath) silently
    discards `base` whenever `filepath` is absolute, so an unnormalized absolute path
    bypasses the worktree sandbox entirely and writes straight into the real
    workspace - which then collides with itself as both "source" and "destination"
    when the apply step tries to copy worktree -> workspace, and can trip substring-
    based heuristics (like extract_target_test's "test" in f.lower()) that assume a
    short relative path, not an absolute one that might contain that substring
    incidentally (e.g. a workspace directory whose own name contains "test").

    Returns None (caller should skip the file and log a warning) if the result would
    resolve outside workspace_path - i.e. treat it as invalid Agent output rather than
    ever writing outside the sandbox, not just a cosmetic path issue.
    """
    if not filepath:
        return None
    if os.path.isabs(filepath):
        try:
            filepath = os.path.relpath(filepath, workspace_path)
        except ValueError:
            return None  # e.g. different drive on Windows
    filepath = os.path.normpath(filepath)
    if filepath == os.pardir or filepath.startswith(os.pardir + os.sep) or os.path.isabs(filepath):
        return None
    return filepath


def _resolve_run_command(command: List[str], workspace_path: Optional[str] = None) -> List[str]:
    """Substitutes Kriya's own interpreter for a bare 'python' the Runtime
    Verification judge inferred, if 'python' isn't actually resolvable on PATH - a
    real, reproducible failure observed live: many systems (Homebrew installs,
    Debian/Ubuntu without python-is-python3) only ship 'python3', not a bare
    'python', and running Kriya without an activated venv means the subprocess's
    inherited PATH may not resolve 'python' either. Without this, subprocess.run
    raises FileNotFoundError immediately and the run never gets a chance to prove
    anything - all 4 retry attempts fail the same way regardless of the generated
    code's actual correctness. sys.executable is guaranteed to exist and be a valid
    interpreter, unlike a guessed command name."""
    if command and command[0] == "python" and shutil.which("python") is None:
        command = [sys.executable] + list(command[1:])
    # Force Maven's -e (full stack traces on failure) onto every runtime-verification
    # invocation, regardless of what the goal's own "Runnable via" text says. Confirmed
    # live: a goal-explicit command like "mvn -q compile exec:java ..." (many skills
    # recommend -q so System.out.println output isn't buried in build noise) makes any
    # runtime exception surface only as exec-maven-plugin's generic wrapper message
    # ("An exception occurred while executing the Java class... [Help 1] ... re-run
    # with -e to see the full stack trace") with the actual cause completely invisible -
    # -q suppresses it too. Every subsequent retry attempt then has zero diagnostic
    # signal to work from, and just guesses (observed burning an entire 5-attempt retry
    # budget re-editing pom.xml because that was the only plausible-looking target).
    # -e adds no output on a successful run, so this is safe to always apply.
    if command and os.path.basename(command[0]) == "mvn" and "-e" not in command:
        command = [command[0], "-e"] + list(command[1:])
    # Correct an exec:java/exec:exec goal mismatch against the actual pom.xml on
    # disk, when workspace_path is given. RunVerifierAgent.judge() only ever sees
    # the Architect's own already-minimized design text (just a file list by
    # convention - see extract_expected_files above), never the matched skill's
    # rules or the file actually written, so a goal needing exec:exec (JVM
    # startup flags, which exec:java can never apply - it runs inside Maven's
    # own already-started JVM) can get judged as exec:java anyway. A bare,
    # self-closing <classpath/> element inside exec-maven-plugin's <arguments>
    # list is exec:exec's own recognized placeholder for the resolved project
    # classpath - exec:java's "arguments" parameter is a plain String[] and
    # cannot parse that element at all, crashing immediately and identically on
    # every retry regardless of the generated code's correctness ("Cannot store
    # value into array: ... cannot cast ... to ... String", confirmed live).
    # The pom.xml itself is ground truth for which goal will actually work,
    # more reliable than hoping skill guidance survives intact through the
    # pipeline to judge(). Deliberately one-directional - only the confirmed,
    # observed failure (exec:java chosen against an exec:exec-shaped pom) is
    # corrected, not a speculative, unobserved reverse case.
    if workspace_path and command and os.path.basename(command[0]) == "mvn" and "exec:java" in command:
        pom_path = os.path.join(workspace_path, "pom.xml")
        try:
            with open(pom_path, "r", encoding="utf-8") as f:
                pom_content = f.read()
        except Exception:
            pom_content = ""
        if re.search(r"<classpath\s*/>", pom_content):
            command = [tok if tok != "exec:java" else "exec:exec" for tok in command]
    # Confirmed live (2026-08-04 eval harness batch): RunVerifierAgent.judge() can
    # infer a bare `rspec`/`rake` run command for a Ruby goal - these are Bundler-
    # installed executables, never on a bare system PATH, so the run fails
    # immediately with "No such file or directory: 'rspec'" regardless of whether
    # the generated code is correct. A Gemfile's presence is ground truth that this
    # project's gems are Bundler-managed - same class of correction as the mvn
    # exec:java/exec:exec fix above, just for a different toolchain.
    if (
        workspace_path and command and command[0] in ("rspec", "rake")
        and os.path.exists(os.path.join(workspace_path, "Gemfile"))
    ):
        command = ["bundle", "exec"] + list(command)
    return command


EXPECTED_FILE_EXTENSIONS = ("java", "xml", "properties", "ya?ml", "json", "gradle", "py", "rb")


def extract_expected_files(design: str) -> set:
    """Extracts basenames of files the Architect's design calls for (directory trees,
    bullet lists, or prose mentions all match), so the Developer Agent's actual output
    can be checked for completeness - not just whether what it did write compiles.

    FALLBACK ONLY as of the Architect file-list contract (kriya/agents/contracts.py,
    ArchitectAgent.run_with_file_list): the mainline path now uses the Architect's own
    validated, structured JSON file list, which has no way to distinguish a real
    requirement from an incidental filename mention elsewhere in the design's prose
    (e.g. "similar to Foo.java elsewhere in the codebase" would match here) and only
    ever returns bare basenames requiring a second, separately fragile regex pass
    (_resolve_file_paths_from_design) to recover a real path. Kept specifically for
    when structured extraction fails twice (main response + one corrective retry) and
    the run needs to degrade to something rather than fail outright - not removed."""
    if not design:
        return set()
    pattern = r'\b[\w\-]+\.(?:' + "|".join(EXPECTED_FILE_EXTENSIONS) + r')\b'
    return {m.group(0) for m in re.finditer(pattern, design)}


TEST_OR_DOC_REQUEST_PHRASES = (
    "unit test", "test case", "test coverage", "test suite", "junit",
    "with tests", "including tests", "documentation", "readme"
)


def _is_test_or_doc_file(filename: str) -> bool:
    lower = filename.lower()
    return "test" in lower or "spec" in lower or lower.endswith(".md") or lower == "readme"


def _goal_requests_tests_or_docs(goal: str) -> bool:
    lower = (goal or "").lower()
    return any(phrase in lower for phrase in TEST_OR_DOC_REQUEST_PHRASES)


class IncompleteGenerationError(ValueError):
    """Raised when the completeness check finds design-required files the Developer
    never wrote. A distinct type (not a bare ValueError) so the retry loop's except
    block can recognize this specific failure and route to a missing-file recovery
    retry - asking the Developer for exactly the missing file(s) - instead of either
    a full-file-set regeneration or a compile/test-error-style targeted retry, neither
    of which addresses "the model just didn't write this file" at all.

    Stays a ValueError subclass (not QualityGateFailure) for backward compatibility
    with existing isinstance(err, ValueError) callers/tests, but carries a `.failure`
    Failure object the same way every QualityGateFailure does - the except block
    reads `.failure` off either via getattr, so this is the one failure source that
    was already structured end-to-end (missing_files as a real attribute, no string
    round-trip) before this module existed, now folded into the same shape."""

    def __init__(self, missing_files: List[str], message: str) -> None:
        super().__init__(message)
        self.missing_files = missing_files
        self.failure = Failure(
            type="incomplete_generation",
            message=message,
            raw_output=message,
            likely_files=list(missing_files),
        )


def find_missing_expected_files(expected_files: set, written_files: set, goal: str = "") -> List[str]:
    """Compares expected basenames (from the design) against actually-written filepaths
    (matched by basename, since the design typically doesn't list full paths).

    Test/doc files (e.g. FooTest.java, README.md) that the Architect volunteered on its
    own initiative are excluded unless the goal explicitly asked for tests or docs -
    mirroring ReviewerAgent's existing pragmatism principle ("if the user goal does not
    explicitly request unit tests, test files, or documentation, do not reject the
    submission solely for their absence"). Otherwise a self-volunteered test file can
    burn through the entire retry budget on something the user never asked for, while
    the actual application code the user did ask for is otherwise complete.
    """
    if not expected_files:
        return []
    written_basenames = {os.path.basename(f) for f in written_files}
    missing = expected_files - written_basenames
    if not _goal_requests_tests_or_docs(goal):
        missing = {f for f in missing if not _is_test_or_doc_file(f)}
    return sorted(missing)


def _resolve_file_paths_from_design(basenames: List[str], design: str) -> List[str]:
    """Resolves each bare basename to a real directory path by searching the
    Architect's design text for a fuller path mention ending in that basename (e.g. a
    bullet list line literally saying "src/main/resources/foo.xml" resolves "foo.xml"
    -> "src/main/resources/foo.xml"). Falls back to the bare basename (correct for
    root-level files like pom.xml) when no path mention is found - e.g. a
    directory-tree diagram line like "|-- foo.xml" has no real path separator
    immediately before the name and won't match, which is fine since a bullet-list or
    prose mention of the same file elsewhere in the design usually does.

    FALLBACK ONLY as of the Architect file-list contract (kriya/agents/contracts.py):
    the mainline path resolves basenames via a direct lookup against the Architect's
    own structured, already-resolved file list (_architect_basename_to_path, built
    once where architect_files is finalized) instead of re-scanning prose with a
    regex every time. This function's only remaining caller is the one place that
    lookup can't be built at all - when structured extraction itself failed twice and
    extract_expected_files()'s basename-only regex is the only file list available in
    the first place. Kept, not removed, for exactly that case."""
    resolved = []
    for basename in basenames:
        pattern = re.compile(r'(?<![\w/.-])[\w][\w./-]*/' + re.escape(basename) + r'(?![\w.])')
        matches = pattern.findall(design)
        resolved.append(max(matches, key=len) if matches else basename)
    return resolved
