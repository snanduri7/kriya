"""Deterministic error-text parsing that grounds a Quality Gate failure in real source locations/files before it reaches the retry loop or the model - environment-failure classification, error-location/search-term extraction, and Failure construction. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

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
from kriya.workflow.failure import Failure, FileLocation

logger = logging.getLogger(__name__)


_ERROR_COORDINATE_PATTERN = re.compile(
    r"\b([a-zA-Z][\w]*(?:\.[a-zA-Z][\w-]*)+):([a-zA-Z][\w-]*)(?::[\w.-]+)?\b"
)


# javac's "cannot find symbol" shape specifically for a failed import
# resolution: "symbol:   class X" followed by "location: package Y" (as
# opposed to "location: class Y", which is javac's shape for a symbol used
# but never imported at all - not an import-path mistake, nothing to
# usefully search for). [X,Y] is a bounded gap so this only matches the two
# lines actually adjacent in real javac output, never spans unrelated error
# blocks.
_ERROR_UNRESOLVED_IMPORT_PATTERN = re.compile(
    r"symbol:\s*class\s+(\w+)[\s\S]{0,80}?location:\s*package\s+([\w.]+)"
)


# Maven prints these two lines at the end of EVERY build, success or failure,
# and their values (build duration, wall-clock timestamp) differ on every
# single invocation even when the underlying failure is byte-for-byte
# identical otherwise - confirmed live: a real, repeated exception (Qpid's
# SystemLauncher.startup() UnsupportedOperationException) never matched
# itself across 3 consecutive identical failures, because
# extract_error_search_terms found no Maven groupId:artifactId coordinate in
# it (it's a plain Java stack trace) and the code fell back to comparing the
# ENTIRE raw error text - these two always-different lines alone were enough
# to defeat that comparison every time, permanently blocking the repeated-
# failure-triggered live lookup for this whole class of error.
_BUILD_TIMING_NOISE_PATTERNS = (
    re.compile(r"^\[INFO\] Total time:.*$", re.MULTILINE),
    re.compile(r"^\[INFO\] Finished at:.*$", re.MULTILINE),
)


_JVM_STARTUP_FAILURE_MARKERS = (
    "Error occurred during initialization of VM",
    "Unrecognized VM option",
    "Unrecognized option:",
    "Could not create the Java Virtual Machine",
)


# Qpid Broker-J's own internal logging (AbstractMessageLogger.getLogActor())
# unconditionally calls the JDK's Subject.getSubject() - an API tied to the
# Security Manager, which JEP 486 permanently removed in JDK 24+. Confirmed
# live, 2026-08-07 (ignite_qpid_person, immediately after the
# _strip_jdk_incompatible_jvm_flags fix started correctly removing the now-
# forbidden -Djava.security.manager=allow flag on a resolved JDK 26 target):
# the broker then crashes on this instead, regardless of whether that flag is
# present or absent - a genuine Qpid-Broker-J-version-vs-JDK-24+
# incompatibility, not a code defect either way, and distinct enough from a
# plain VM-startup-flag crash (_JVM_STARTUP_FAILURE_MARKERS above) to warrant
# its own, more specific message rather than the generic "JVM flag
# unsupported" one. Without this, the retry loop burned two full attempts
# trying to code-fix it - both correctly concluding (per skill rule) that the
# flag shouldn't be re-added, both still hitting the identical crash anyway -
# before ever escalating past it.
_QPID_JDK24_SECURITY_MANAGER_API_MARKER = "getSubject is not supported"


# Only matches the specific "Failed to invoke/execute ...: [Errno 2] No such
# file or directory: '<name>'" wrapper PolymorphicValidator itself produces
# when a subprocess.run() call can't find the launched executable at all
# (kriya/tools/validate.py's run_compile_check/run_app) - deliberately NOT a
# bare "No such file or directory" substring match anywhere in captured
# output, which a generated app's own legitimate (code-fixable)
# FileNotFoundError could also produce in a traceback.
_MISSING_EXECUTABLE_PATTERN = re.compile(
    r"Failed to (?:invoke|execute)[^:]*:\s*\[Errno 2\] No such file or directory: '([^']+)'"
)


def classify_environment_failure(error_text: str) -> Optional[str]:
    """Returns a short, human-readable description if error_text shows a failure
    class no amount of code regeneration can ever fix - a JVM crashing during its
    own startup (before any generated code runs, e.g. a startup flag unsupported
    by the actually-resolved JDK) or a build/run tool binary missing from PATH
    entirely (e.g. Maven not installed). Distinguishing these lets the Quality
    Gates retry loop stop burning its retry budget re-generating code for a
    problem that was never a code defect - confirmed as a real, wasteful gap
    during golden-use-case validation: the same JVM-startup crash (a flag correct
    for JDK 17.0.10 became fatal under JDK 26, which removed the Security Manager
    entirely) recurred identically across 3 real retry attempts before a human
    had to intervene, and Kriya had no way to recognize it wasn't a code bug."""
    for marker in _JVM_STARTUP_FAILURE_MARKERS:
        if marker in error_text:
            return (
                f"JVM failed during its own startup ('{marker}') - not a code "
                "defect, most likely a JVM flag unsupported by the actually-"
                "resolved Java version (see `kriya doctor`)."
            )
    if _QPID_JDK24_SECURITY_MANAGER_API_MARKER in error_text:
        return (
            "Qpid Broker-J's own internal logging calls a JDK Security-Manager-era "
            "API (Subject.getSubject()) that throws UnsupportedOperationException "
            "once the Security Manager is permanently removed (JEP 486, JDK 24+) - "
            "not a code defect, and not fixable by adding or omitting "
            "-Djava.security.manager=allow either way (that flag is itself "
            "forbidden on this JDK range). A genuine Qpid Broker-J version vs. "
            "JDK 24+ incompatibility - resolve with a newer Qpid Broker-J release "
            "known to support JDK 24+, or by targeting an older JDK."
        )
    m = _MISSING_EXECUTABLE_PATTERN.search(error_text)
    if m:
        return (
            f"Required build/run tool '{m.group(1)}' was not found on PATH - "
            "not a code defect, the toolchain itself is missing or misconfigured."
        )
    return None


def _normalize_error_for_repeat_detection(error_text: str) -> str:
    """Strips known non-deterministic per-run noise (Maven's own build-timing
    lines) before error text is used as a repeated-failure signature - see the
    fallback branch of the current_failure_signature computation below, used
    only when extract_error_search_terms found no stable coordinate to key
    on instead."""
    normalized = error_text
    for pattern in _BUILD_TIMING_NOISE_PATTERNS:
        normalized = pattern.sub("", normalized)
    return normalized


def extract_error_search_terms(
    error_text: str,
    exclude_coordinates: Optional[Iterable[str]] = None,
    dependency_coordinates: Optional[Iterable[str]] = None,
) -> List[str]:
    """Extracts safe search terms from Quality Gate error/output text for
    error-triggered live lookup (kriya/workflow/workflow.py's Developer retry
    loop) - restricted to well-known, publicly-referenceable Maven/Gradle-style
    artifact coordinates (groupId:artifactId, e.g. "org.codehaus.mojo:exec-maven-
    plugin") found IN the error text. This is a hard, code-enforced boundary, not
    a prompt instruction: never the raw error/stack-trace text itself (which can
    contain project-specific class/variable/file names), never goal/design/code
    text - the same principle as the existing extract_library_versions()-based
    goal/design-stage live lookup.

    exclude_coordinates filters out matches that are technically present but
    useless as a search target - confirmed live as a real bug: Maven's own build
    banner (`[INFO] ----------------< groupId:artifactId >-----------------`,
    printed at the start of every single build, success or failure) matches this
    exact coordinate shape, so without exclusion a project's own made-up
    artifact ID gets treated as a genuine third-party library worth searching
    for, wasting a real repeated-failure live-lookup recovery attempt on a term
    that can never find anything useful. Callers pass the workspace's own
    pom.xml coordinate (get_pom_own_coordinate()) here.

    dependency_coordinates covers a DIFFERENT, previously-unhandled failure
    shape - confirmed live during golden-use-case validation as a real,
    generalizable gap, not library-specific: "wrong import path within a
    library the project already, legitimately depends on" (e.g. writing
    `import org.apache.ignite.cache.IgniteCache;` when the class actually
    lives at the top-level `org.apache.ignite` package). Unlike a missing-
    dependency error, javac's diagnostic for this has no groupId:artifactId
    coordinate anywhere in it - just "symbol: class X" / "location: package
    Y" - so without this, such a failure yields zero terms and the repeated-
    failure trigger has nothing to search for, no matter how many times it
    recurs identically. Matched safely, same trust boundary as the rest of
    this function: Y (the WRONG package javac tried and failed to resolve)
    only becomes a search term if its dot-prefix matches the groupId of one
    of the caller-supplied dependency_coordinates - i.e. only for a class
    that demonstrably belongs to a library already declared as a real
    project dependency, never an arbitrary/private symbol name. Pass
    get_pom_dependencies() here. Renders as "{matched coordinate} {symbol}",
    e.g. "org.apache.ignite:ignite-core IgniteCache", which _resolve_via_web_
    lookup() then suffixes with " example" like every other term."""
    seen = set()
    terms = []
    exclude = set(exclude_coordinates) if exclude_coordinates else set()
    for m in _ERROR_COORDINATE_PATTERN.finditer(error_text):
        term = f"{m.group(1)}:{m.group(2)}"
        if term not in seen and term not in exclude:
            seen.add(term)
            terms.append(term)

    if dependency_coordinates:
        for symbol, wrong_package in _ERROR_UNRESOLVED_IMPORT_PATTERN.findall(error_text):
            for coord in dependency_coordinates:
                group_id = coord.split(":", 1)[0]
                if wrong_package == group_id or wrong_package.startswith(group_id + "."):
                    term = f"{coord} {symbol}"
                    if term not in seen and term not in exclude:
                        seen.add(term)
                        terms.append(term)
                    break
    return terms


_ERROR_LOCATION_PATTERN = re.compile(
    r"([\w./\\-]+\.java):\[(\d+),\d+\]"    # javac compile-error shape: File.java:[line,col]
    r"|\(([\w.-]+\.java):(\d+)\)"          # Java stack-trace shape: (File.java:line)
)


def extract_error_source_locations(error_text: str) -> List[Tuple[str, int]]:
    """Extracts (filename, line_number) pairs from compiler error text via
    javac's own universal locator shape (`File.java:[line,col]`) - deliberately
    generic, not tied to any specific error MESSAGE (unlike
    extract_error_search_terms()'s coordinate/wrong-import patterns, which each
    needed their own new regex the first time that error shape was actually
    hit live). Every javac diagnostic - incompatible types, cannot find symbol,
    missing return, wrong argument count, all of them - carries this same
    locator, so this one mechanism covers the whole family without needing to
    anticipate each message shape in advance. Returns the bare basename
    referenced in the error text (which may be an absolute worktree path,
    e.g. '.../worktree/src/main/java/com/example/Foo.java:[91,79]') - callers
    resolve it against the known written-files list.

    ALSO recognizes a JUnit/JVM stack trace's own locator shape
    ('at pkg.Class.method(File.java:60)') - confirmed live, 2026-08-07
    (kriya-protocol-parser-app): a real test failure (BufferUnderflowException
    in a hand-rolled binary parser's decode()) carried this exact file:line
    precision, but the compile-error-only regex never recognized it, so this
    failure class got zero source-line grounding and no anchored-edit
    preference - unlike a compile error with the identical kind of precision,
    just a different textual shape. The model's own fix-analysis correctly
    diagnosed the bug but, with no precise anchor to patch against, fell back
    to full-file regeneration and reintroduced an equivalent bug - the exact
    failure mode _build_error_source_context/anchored-edit preference exists
    to prevent, just never wired up for this shape. Purely local: reads real
    source lines from the worktree to show the LOCAL model, in the SAME
    prompt-building path a compile error already uses - not related to, and
    does not touch, extract_error_search_terms()'s separate, narrowly-scoped
    live-lookup mechanism (a hard-coded Maven/Gradle-coordinate-only regex
    that never sees raw error/stack-trace text at all, by design - see its
    own docstring). Both location shapes share this one function/return
    contract; a match's file/line comes from whichever alternative matched
    (group 1/2 for the compile shape, group 3/4 for the stack-trace shape)."""
    seen = set()
    locations: List[Tuple[str, int]] = []
    for m in _ERROR_LOCATION_PATTERN.finditer(error_text):
        filepath = m.group(1) or m.group(3)
        line = m.group(2) or m.group(4)
        key = (os.path.basename(filepath), int(line))
        if key not in seen:
            seen.add(key)
            locations.append(key)
    return locations


def _build_error_source_context(
    worktree_path: str, error_text: str, known_files: Iterable[str]
) -> Dict[str, str]:
    """For each (file, line) the error text locates, reads a small window of
    the REAL current source around that line from the worktree and formats it
    for direct inclusion in that file's own retry prompt - the exact broken
    line(s), not a prose description buried inside a noisy multi-line Maven
    build banner. Generic across any compile error shape (see
    extract_error_source_locations). Returns {filepath: formatted_snippet},
    keyed by the real relative filepath (matched against known_files by
    basename, since the error text's own path may be absolute/worktree-
    rooted) - a location naming a file Kriya doesn't know about (already
    deleted, or a dependency's own source) is silently skipped, not an error."""
    locations = extract_error_source_locations(error_text)
    if not locations:
        return {}
    by_basename = {os.path.basename(f): f for f in known_files}
    context_by_file: Dict[str, str] = {}
    for filename, line_no in locations:
        filepath = by_basename.get(filename)
        if not filepath:
            continue
        try:
            with open(os.path.join(worktree_path, filepath), "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except Exception as ex:
            logger.debug(f"Failed to read source context for {filepath}:{line_no}: {ex}")
            continue
        if not (1 <= line_no <= len(lines)):
            continue
        start, end = max(0, line_no - 4), min(len(lines), line_no + 3)
        snippet = "\n".join(
            f"{'>>' if (i + 1) == line_no else '  '} {i + 1}: {lines[i].rstrip()}"
            for i in range(start, end)
        )
        context_by_file[filepath] = (
            context_by_file.get(filepath, "")
            + f"\n=== Source context at the reported error location (line {line_no}) ===\n{snippet}\n"
        )
    return context_by_file


def _resolve_file_locations(error_text: str, known_files: Iterable[str]) -> List[FileLocation]:
    """Same basename resolution _build_error_source_context() does internally,
    but returning structured FileLocation objects for a Failure instead of a
    prompt-ready snippet dict - lets a compile-error raise site populate
    Failure.file_locations directly instead of leaving it to be re-derived
    later from str(e)."""
    by_basename = {os.path.basename(f): f for f in known_files}
    locations: List[FileLocation] = []
    for filename, line_no in extract_error_source_locations(error_text):
        filepath = by_basename.get(filename)
        if filepath:
            locations.append(FileLocation(filepath=filepath, line=line_no))
    return locations


def _capture_failed_content(worktree_path: str, files: Iterable[str]) -> Dict[str, str]:
    """Reads the real current content of every given file from the worktree at
    the moment a Failure is raised - still on disk, since the next Developer
    attempt hasn't overwritten it yet. Closes a real forensics gap found live
    (2026-08-04 eval harness batch): a failed attempt's actual generated
    content was otherwise never persisted anywhere, only the tool's error
    text, making a recurring live bug impossible to root-cause after the
    fact. Best-effort - a file that can't be read (already deleted, race)
    is silently skipped, matching _build_error_source_context's own
    tolerance for a location naming a file Kriya doesn't have."""
    content: Dict[str, str] = {}
    for filepath in files:
        try:
            with open(os.path.join(worktree_path, filepath), "r", encoding="utf-8", errors="replace") as fh:
                content[filepath] = fh.read()
        except Exception as ex:
            logger.debug(f"Failed to capture failed content for {filepath}: {ex}")
    return content


def _build_quality_gate_failure(
    type_: str,
    message: str,
    raw_output: str,
    worktree_path: str,
    known_files: Iterable[str],
    attempt: int,
    extra_likely_files: Optional[List[str]] = None,
) -> Failure:
    """Shared construction for the compile/test/run_verification/regression_test
    raise sites - each just supplies its own type/message/raw_output (and, for
    run_verification, RunVerifierAgent.grade()'s already-validated likely_files
    as extra_likely_files). Populates file_locations/likely_files/failed_content
    uniformly instead of leaving them to be re-derived later from str(e), and
    captures failed_content only for the files this failure actually implicates
    (not every written file) to keep the I/O bounded."""
    known_files = list(known_files)
    file_locations = _resolve_file_locations(raw_output, known_files)
    likely_files = list(dict.fromkeys(
        (extra_likely_files or []) + extract_implicated_files(raw_output, known_files)
    ))
    implicated = sorted({loc.filepath for loc in file_locations} | set(likely_files))
    return Failure(
        type=type_,
        message=message,
        raw_output=raw_output,
        file_locations=file_locations,
        likely_files=likely_files,
        failed_content=_capture_failed_content(worktree_path, implicated),
        attempt=attempt,
    )


def extract_implicated_files(error_text: str, known_files: Iterable[str]) -> List[str]:
    """Deterministically identifies which of this run's already-written files a
    Quality Gate failure implicates, for the Developer retry loop's targeted-retry
    path - no LLM call. A known file counts as implicated if its basename (or full
    relative path) literally appears in the error/output text, which naturally
    covers every failure type without per-tool-specific parsing: compiler errors
    name the file directly (`path/File.java:[19,45] ...`), test runners name the
    failing test file, and even some runtime stack traces mention a class/file
    name. A failure that names no known file (a bare exit code, a Maven plugin
    config error with no source file involved, etc.) simply yields no matches -
    that attempt falls back to a full-file-set retry instead, exactly as if this
    function didn't exist. Best-effort/heuristic by design: a false-positive match
    is low-cost since targeted retries are soft-scoped (the model may still touch
    other files if a fix genuinely needs it), not a hard restriction.

    Prefers a precise file:line locator (extract_error_source_locations - javac's
    `File.java:[line,col]`, or a JVM stack trace's `(File.java:line)`) over a bare
    substring match when at least one known file has one. Found live, 2026-08-10
    (ignite_qpid_protocol): Maven's OWN reactor startup banner
    ("[INFO]   from pom.xml") appears verbatim in the captured output of every
    single Maven build, success or failure, regardless of what actually broke -
    so the old plain-substring check unconditionally implicated pom.xml on
    effectively every retry for any Maven Java goal. Confirmed live: a targeted
    retry burned a full extra per-file completion call on "Developer fix analysis
    for 'pom.xml'" correctly diagnosing an unrelated `BufferOverflowException` in
    ProtocolParser.java, then regenerating pom.xml with nothing useful to change -
    real wall-clock cost (one extra multi-minute completion per retry attempt) for
    a file a build-manifest edit could never plausibly have fixed. A file named
    alongside a real, precise line locator is much stronger evidence of genuine
    involvement than a filename appearing anywhere in a noisy multi-line build-tool
    banner - deliberately reuses the SAME general locator mechanism
    _resolve_file_locations()/_build_error_source_context() already rely on,
    rather than special-casing pom.xml/Maven by name. When no known file has a
    locator at all (a bare exit code, a non-Java stack, a dependency-resolution
    error with no file:line info), falls back to today's plain substring
    behavior unchanged - this only narrows the result when locator evidence is
    actually available, never adds a new failure-to-match case."""
    known_files = list(known_files)
    located_basenames = {basename for basename, _line in extract_error_source_locations(error_text)}
    if located_basenames:
        located = [f for f in known_files if os.path.basename(f) in located_basenames]
        if located:
            return located

    implicated = []
    for filepath in known_files:
        basename = os.path.basename(filepath)
        if basename and (basename in error_text or filepath in error_text):
            implicated.append(filepath)
    return implicated
