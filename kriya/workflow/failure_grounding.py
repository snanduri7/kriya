"""Deterministic error-text parsing that grounds a Quality Gate failure in real source locations/files before it reaches the retry loop or the model - environment-failure classification, error-location/search-term extraction, and Failure construction. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import hashlib
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.generation_manifest import FileRole, classify_file_role

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

# The "location: class Y" sibling of the pattern above - this comment
# previously called it "not an import-path mistake, nothing to usefully
# search for", which undersold it: found live, 2026-08-22 (ignite_qpid_
# protocol milestone 3/4), this exact shape (symbol: class Protocol,
# location: class com.example.App) is precisely what a CROSS-PACKAGE
# reference to an already-existing class looks like from javac's own
# perspective. See find_cross_package_symbol_mismatch() below for how it's
# used - a use-site error with a real, findable candidate elsewhere under a
# different package is a definitive signal, not a guess.
_ERROR_USE_SITE_MISSING_SYMBOL_PATTERN = re.compile(
    r"symbol:\s*class\s+(\w+)[\s\S]{0,80}?location:\s*class\s+([\w.]+)"
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


_BUILD_WRAPPER_COORDINATES = {
    "org.apache.maven.plugins:maven-compiler-plugin",
    "org.apache.maven.plugins:maven-surefire-plugin",
    "org.codehaus.mojo:exec-maven-plugin",
}

_JAVAC_DIAGNOSTIC_PATTERN = re.compile(
    r"\.java:\[\d+,\d+\]\s*([^\n]+)"
)
_JAVAC_DETAIL_PATTERN = re.compile(
    r"^[ \t]*(?:\[ERROR\][ \t]*)?"
    r"(symbol|location|required|found|reason):\s*([^\n]+)$",
    re.MULTILINE,
)
_EXCEPTION_CAUSE_PATTERN = re.compile(
    r"^[ \t]*(?:\[ERROR\][ \t]*)?"
    r"(?:Exception in thread \"[^\"]+\"\s+|Caused by:\s*)?"
    r"(?:class\s+)?([\w.$]+(?:Exception|Error))(?::\s*(.*))?$",
    re.MULTILINE,
)


_JVM_STARTUP_FAILURE_MARKERS = (
    "Error occurred during initialization of VM",
    "Unrecognized VM option",
    "Unrecognized option:",
    "Could not create the Java Virtual Machine",
)


# "getSubject is not supported" is the JDK's OWN UnsupportedOperationException
# message from javax.security.auth.Subject.getSubject() - an API tied to the
# Security Manager, which JEP 486 permanently removed in JDK 24+ - not a
# message any particular library formats itself, so this marker fires for ANY
# dependency that still calls that now-forbidden API, not just the one that
# first surfaced it. First confirmed live, 2026-08-07 (ignite_qpid_person,
# immediately after the _strip_jdk_incompatible_jvm_flags fix started
# correctly removing the now-forbidden -Djava.security.manager=allow flag on a
# resolved JDK 26 target): Qpid Broker-J's own internal logging
# (AbstractMessageLogger.getLogActor()) called Subject.getSubject() and
# crashed on this instead, regardless of whether that flag was present or
# absent - a genuine library-version-vs-JDK-24+ incompatibility, not a code
# defect either way, and distinct enough from a plain VM-startup-flag crash
# (_JVM_STARTUP_FAILURE_MARKERS above) to warrant its own, more specific
# message rather than the generic "JVM flag unsupported" one. Without this,
# the retry loop burned two full attempts trying to code-fix it - both
# correctly concluding (per skill rule) that the flag shouldn't be re-added,
# both still hitting the identical crash anyway - before ever escalating past
# it.
_JDK24_SECURITY_MANAGER_API_MARKER = "getSubject is not supported"


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
    if _JDK24_SECURITY_MANAGER_API_MARKER in error_text:
        return (
            "A dependency on the classpath calls a JDK Security-Manager-era API "
            "(Subject.getSubject()) that throws UnsupportedOperationException once "
            "the Security Manager is permanently removed (JEP 486, JDK 24+) - not "
            "a code defect, and not fixable by adding or omitting "
            "-Djava.security.manager=allow either way (that flag is itself "
            "forbidden on this JDK range). A genuine library-version vs. JDK 24+ "
            "incompatibility - resolve with a newer release of that dependency "
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
    lines) before unstructured error text is hashed as a repeated-failure
    signature."""
    normalized = error_text
    for pattern in _BUILD_TIMING_NOISE_PATTERNS:
        normalized = pattern.sub("", normalized)
    return normalized


def build_failure_signature(failure_type: str, error_text: str) -> Tuple[str, Any]:
    """Content-local identity for retry budgets and repeated-failure checks.

    Public artifact coordinates are intentionally not the identity: Maven's
    compiler/exec plugin wrapper appears around many unrelated source/runtime
    defects and previously collapsed them into one run-global failure. Prefer
    stable validator evidence with dynamic line numbers removed; hash the
    normalized local text only when no structured diagnostic is available.
    """
    locations = tuple(sorted({name for name, _ in extract_error_source_locations(error_text)}))
    javac_diagnostics = tuple(sorted({
        " ".join(message.split())
        for message in _JAVAC_DIAGNOSTIC_PATTERN.findall(error_text)
    }))
    if javac_diagnostics:
        javac_details = tuple(sorted({
            (label, " ".join(value.split()))
            for label, value in _JAVAC_DETAIL_PATTERN.findall(error_text)
        }))
        return failure_type, (locations, "javac", javac_diagnostics, javac_details)

    exception_causes = [
        (exception_type, " ".join((message or "").split()))
        for exception_type, message in _EXCEPTION_CAUSE_PATTERN.findall(error_text)
    ]
    if exception_causes:
        # The deepest cause is normally last, after build-tool wrapper
        # exceptions. Keep locations by basename but not line number so an
        # edit that shifts a stack frame by one line remains the same failure.
        return failure_type, (locations, "exception", exception_causes[-1])

    normalized = _normalize_error_for_repeat_detection(error_text)
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
    return failure_type, (locations, "digest", digest)


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

    dependency_coordinates covers two failure shapes without widening the
    egress boundary. The first was confirmed live during golden-use-case
    validation: "wrong import path within a library the project already,
    legitimately depends on" (e.g. writing
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
    get_pom_dependencies() here. The second is an exception whose package
    begins with a declared dependency's groupId. Only the already-declared
    coordinate is emitted for that case—not the exception class or message—so
    an application stack locator can suppress an irrelevant Maven wrapper
    without sending any project diagnostic text. The outbound public-catalog
    gate still decides whether that coordinate may leave the machine."""
    seen = set()
    terms = []
    exclude = set(exclude_coordinates) if exclude_coordinates else set()
    has_source_evidence = bool(extract_error_source_locations(error_text))
    for m in _ERROR_COORDINATE_PATTERN.finditer(error_text):
        term = f"{m.group(1)}:{m.group(2)}"
        if term in _BUILD_WRAPPER_COORDINATES and has_source_evidence:
            # A real source/stack locator proves the Maven plugin is only the
            # execution wrapper around application evidence. Searching for the
            # wrapper is safe but irrelevant and wastes the one lookup shot.
            continue
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
        for exception_type, _message in _EXCEPTION_CAUSE_PATTERN.findall(error_text):
            exception_package, _, _exception_name = exception_type.rpartition(".")
            for coord in dependency_coordinates:
                group_id = coord.split(":", 1)[0]
                if exception_package == group_id or exception_package.startswith(group_id + "."):
                    term = coord
                    if term not in seen and term not in exclude:
                        seen.add(term)
                        terms.append(term)
                    break
    return terms


_ERROR_LOCATION_PATTERN = re.compile(
    r"(?P<jc_file>[\w./\\-]+\.java):\[(?P<jc_line>\d+),\d+\]"      # javac compile-error shape: File.java:[line,col]
    r"|\((?P<jt_file>[\w.-]+\.java):(?P<jt_line>\d+)\)"            # Java stack-trace shape: (File.java:line)
    r"|Syntax error in (?P<py_file>[\w./\\-]+\.py) line (?P<py_line>\d+):"  # PolymorphicValidator's Python SyntaxError shape
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
    to prevent, just never wired up for this shape.

    ALSO recognizes PolymorphicValidator.run_compile_check()'s own Python
    SyntaxError shape ('Syntax error in greet.py line 6: ... (invalid
    syntax)', kriya/tools/validate.py) - confirmed live, 2026-08-13
    (python_greeter, reproduced identically across two separate eval-harness
    runs): the SAME "no anchor -> full-file regeneration -> stated fix gets
    lost in the rewrite" failure mode as the JVM-stack-trace gap above, this
    time for every Python goal rather than one specific JVM shape - a model
    (glm-4.7-flash) correctly diagnosed a one-line fix in its own FIX ANALYSIS
    text on 4 consecutive retries and never once implemented it, because it
    was never shown the exact broken line and never asked to prefer a small
    anchored patch over rewriting the whole file. Purely local: reads real
    source lines from the worktree to show the LOCAL model, in the SAME
    prompt-building path a compile error already uses - not related to, and
    does not touch, extract_error_search_terms()'s separate, narrowly-scoped
    live-lookup mechanism (a hard-coded Maven/Gradle-coordinate-only regex
    that never sees raw error/stack-trace text at all, by design - see its
    own docstring). All three location shapes share this one function/return
    contract; a match's file/line comes from whichever alternative's named
    groups matched (jc_*/jt_*/py_* for the compile/stack-trace/Python
    shapes respectively) - named rather than positional groups specifically
    so a future fourth shape doesn't need to renumber every existing one."""
    seen = set()
    locations: List[Tuple[str, int]] = []
    for m in _ERROR_LOCATION_PATTERN.finditer(error_text):
        filepath = m.group("jc_file") or m.group("jt_file") or m.group("py_file")
        line = m.group("jc_line") or m.group("jt_line") or m.group("py_line")
        key = (os.path.basename(filepath), int(line))
        if key not in seen:
            seen.add(key)
            locations.append(key)
    return locations


def _files_by_basename(known_files: Iterable[str]) -> Dict[str, List[str]]:
    """Groups known files by basename, preserving EVERY file sharing a
    basename - not just the last one seen. A plain `{os.path.basename(f): f
    for f in known_files}` dict comprehension (the shape both call sites
    below independently used, before this fix) silently drops all but the
    LAST file for a shared basename (e.g. the same-named class in two
    packages, or a `test/`+`main/` tree with a same-named fixture) - a
    FileLocation or a source-context snippet could silently attach to the
    WRONG file's path whenever two known files share a basename. Found by
    code inspection, 2026-08-14 (the file-attribution-consolidation
    taxonomy review), zero live incident yet but zero test coverage either.
    extract_implicated_files() was never affected (it already scans via a
    list comprehension over every known file, not a basename dict), which is
    exactly why this went unnoticed elsewhere - this is the one shared
    helper both basename-keyed call sites now use instead of each
    reimplementing the same lossy dict."""
    by_basename: Dict[str, List[str]] = {}
    for f in known_files:
        by_basename.setdefault(os.path.basename(f), []).append(f)
    return by_basename


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
    deleted, or a dependency's own source) is silently skipped, not an error.
    A basename shared by more than one known file (see _files_by_basename)
    adds context for EVERY matching file, not just one - the error text
    itself gives no way to disambiguate which of several same-named files it
    meant, so showing all of them is the honest behavior."""
    locations = extract_error_source_locations(error_text)
    if not locations:
        return {}
    by_basename = _files_by_basename(known_files)
    context_by_file: Dict[str, str] = {}
    for filename, line_no in locations:
        for filepath in by_basename.get(filename, []):
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
    """Same basename resolution _build_error_source_context() does internally
    (via _files_by_basename, shared rather than reimplemented), but returning
    structured FileLocation objects for a Failure instead of a prompt-ready
    snippet dict - lets a compile-error raise site populate
    Failure.file_locations directly instead of leaving it to be re-derived
    later from str(e). A basename shared by more than one known file produces
    a FileLocation for EACH matching file, same reasoning as
    _build_error_source_context above."""
    by_basename = _files_by_basename(known_files)
    locations: List[FileLocation] = []
    for filename, line_no in extract_error_source_locations(error_text):
        for filepath in by_basename.get(filename, []):
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


def find_cross_package_symbol_mismatch(
    compile_output: str,
    type_index: Dict[str, List[str]],
    java_packages: Dict[str, Optional[str]],
) -> Optional[Tuple[str, str, str]]:
    """Detects a Java compile failure caused by a cross-milestone PACKAGE
    mismatch, not a genuinely missing/typo'd class - found live, 2026-08-22
    (ignite_qpid_protocol milestone 3/4): a fresh milestone's Architect chose
    a Maven-conventional package (`com.example`) for its own new file, but an
    EARLIER milestone's already-established file lives in the default
    package (no package declaration at all) - a `cannot find symbol: class
    Protocol, location: class com.example.App` error that recurred BYTE-FOR-
    BYTE IDENTICAL across 3+ retries, because a class in one named package
    can never reference a class in a different (or the default) package,
    under any circumstances - not a missing import, a genuine language-level
    incompatibility no amount of prose-level retrying can resolve. The
    Developer's own reasoning actually diagnosed this correctly, more than
    once, then talked itself out of fixing it every time - caught between
    "only touch the targeted file" and "don't restructure what already
    works". This function exists to hand the retry loop a diagnosis precise
    enough that there's nothing left to talk itself out of.

    type_index is the SAME Dict[str, List[str]] (".java:SimpleName" ->
    [paths], extension-scoped) _build_workspace_type_index() already builds
    for the duplicate-type-across-files gate - full reuse, no new indexing.
    java_packages is {filepath: package_or_None} for every currently-tracked
    .java file (the caller's own responsibility - see
    _build_java_package_map() in attempt.py - keeping this function pure and
    trivially testable, matching ground_java_entrypoint_in_no_build_file_
    projects()'s own established separation of I/O from decision logic).

    Returns (missing_symbol, referencing_path, candidate_path) - the file
    that's MISSING the symbol (referencing_path) and the file that ALREADY
    HAS it under a different package (candidate_path) - or None whenever
    this can't be resolved with confidence: the symbol isn't in type_index
    at all (a genuinely missing/typo'd class - let the existing generic
    compile-failure path handle it), the symbol resolves to more than one
    file (an ambiguity a flat lookup can't safely break), the candidate's
    package already matches the referencing class's own package (not
    actually a mismatch - some other compile error), or the referencing
    file's own path can't be uniquely resolved the same way."""
    for missing_symbol, referencing_qualified in dict.fromkeys(
        _ERROR_USE_SITE_MISSING_SYMBOL_PATTERN.findall(compile_output)
    ):
        candidates = type_index.get(f".java:{missing_symbol}", [])
        if len(candidates) != 1:
            continue
        candidate_path = candidates[0]
        # Membership check, not .get()'s silent default: a candidate whose
        # package was never actually read (out of the caller's known-files
        # scope) must never be treated as "confirmed no package" - that
        # would collide with a genuine default-package file and could
        # fabricate a mismatch (or miss a real one) from pure absence of
        # data, not evidence.
        if candidate_path not in java_packages:
            continue
        candidate_pkg = java_packages[candidate_path]
        referencing_pkg = (
            referencing_qualified.rsplit(".", 1)[0] if "." in referencing_qualified else None
        )
        if candidate_pkg == referencing_pkg:
            continue
        referencing_simple = referencing_qualified.rsplit(".", 1)[-1]
        referencing_matches = [
            p for p in type_index.get(f".java:{referencing_simple}", [])
            if p != candidate_path and java_packages.get(p, object()) == referencing_pkg
        ]
        if len(referencing_matches) != 1:
            continue
        return missing_symbol, referencing_matches[0], candidate_path
    return None


def build_cross_package_mismatch_message(
    missing_symbol: str,
    referencing_path: str,
    referencing_pkg: Optional[str],
    candidate_path: str,
    candidate_pkg: Optional[str],
) -> str:
    """The part that actually breaks the retry deadlock, not just detects it -
    see find_cross_package_symbol_mismatch()'s own docstring for the live
    incident where the Developer correctly diagnosed a package mismatch
    THREE TIMES and never fixed it, stuck between "only touch the targeted
    file" and "don't restructure what already works". States plainly that
    changing the referencing (new, current-milestone) file's package is a
    REQUIRED compatibility fix, not a forbidden restructuring - and defaults
    to recommending exactly that direction (adapt the new file to the
    established one, never the reverse) so this stays consistent with, not
    in tension with, the "don't touch already-established, working files"
    instruction milestone goals already carry."""
    referencing_desc = f"package `{referencing_pkg}`" if referencing_pkg else "the default (unnamed) package"
    candidate_desc = f"package `{candidate_pkg}`" if candidate_pkg else "the default (unnamed) package"
    return (
        f"PACKAGE MISMATCH: {referencing_path} (in {referencing_desc}) references `{missing_symbol}`, "
        f"which already exists at {candidate_path} - but that file is declared in {candidate_desc}, "
        "a DIFFERENT package. This is a Java language rule, not a missing import: a class in one "
        "named package can NEVER reference a class in a different (or the default) package under "
        "any circumstances, no matter how the import statement is written. "
        f"REQUIRED FIX: change {referencing_path} to use {candidate_desc} (matching "
        f"{candidate_path}, which already works and should NOT be moved or modified). "
        "This is a REQUIRED compatibility fix, not a forbidden restructuring of already-working "
        f"code - {candidate_path} stays exactly as it is; only {referencing_path}'s own package "
        "changes, since it is the new file being added to an existing, already-established "
        "package layout."
    )


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


# Known build-tool signatures for "the test PROCESS itself was killed",
# distinct from an ordinary assertion/compile failure - PRV-06 (2026-08-28,
# real live-validation finding): a JUnit test invoked a System.exit()-calling
# main() in-process, which kills the Surefire fork outright rather than
# reporting a normal test result. Deliberately a signature LIST (not a single
# string) so a future stack's equivalent (pytest's own worker-crash text,
# Node's forced-exit output, ...) can be added without restructuring the
# caller - only the Java/Surefire entries are populated now, matching the
# one stack this incident's live evidence actually covers; do not add
# unverified signatures for other stacks speculatively.
_PROCESS_TERMINATION_SIGNATURES: Tuple[str, ...] = (
    "SurefireBooterForkException",
    "forked VM terminated",
    "VM crash or System.exit called?",
)

_PROCESS_TERMINATION_GUIDANCE = (
    "This is a process-boundary/testability conflict, not an ordinary assertion "
    "failure: the test runner's own process was killed while running a test. Resolve "
    "it structurally or verify the terminating behavior out-of-process instead of "
    "invoking it directly from a test. A single-file edit that toggles the terminating "
    "call cannot resolve this structural conflict."
)

_VERIFICATION_STRATEGY_INCOMPATIBLE_GUIDANCE = (
    "VERIFICATION_STRATEGY_INCOMPATIBLE: the test runner's own process was killed "
    "because the test invoked process-terminating application behavior IN-PROCESS. "
    "Do not change or remove the product's required process-exit behavior. Repair "
    "only the verification strategy: observe that CLI path through a child process "
    "and assert its exit code/stdout/stderr, or leave process-exit verification to "
    "the declared application_runtime verifier. Do not use SecurityManager exit "
    "interception and do not add dependencies or annotations to support it."
)

_CRASHED_TEST_CLASS_RE = re.compile(r"Crashed tests:\s*\n(?:\[ERROR\]\s*)?([\w.$]+)")


def _crashed_test_artifacts(raw_output: str, known_files: Iterable[str]) -> List[str]:
    match = _CRASHED_TEST_CLASS_RE.search(raw_output or "")
    if not match:
        return []
    simple_name = match.group(1).rsplit(".", 1)[-1].split("$", 1)[0]
    return sorted(
        path for path in known_files
        if classify_file_role(path) is FileRole.TEST
        and os.path.splitext(os.path.basename(path))[0] == simple_name
    )


def detect_process_termination_signature(output: str) -> Optional[str]:
    """Returns the first known process/fork-termination signature found in
    `output`, or None. Pure/deterministic - no LLM call, matching this
    module's own "ground before it reaches the model" role."""
    text = output or ""
    for signature in _PROCESS_TERMINATION_SIGNATURES:
        if signature in text:
            return signature
    return None


def _build_test_quality_gate_failure(
    type_: str,
    banner: str,
    raw_output: str,
    worktree_path: str,
    known_files: Iterable[str],
    attempt: int,
) -> Failure:
    """Same contract as _build_quality_gate_failure, for the "test"/
    "targeted_test" call sites specifically - upgrades `type_`/`banner` to
    the process-termination shape when `raw_output` carries a known
    signature (see _PROCESS_TERMINATION_SIGNATURES above), so the retry
    loop's own failure-family signature (keyed off `type_`) treats this as
    genuinely distinct from an ordinary test failure, and the Developer
    sees the structural guidance instead of re-deriving it (inconsistently)
    from scratch every attempt. `raw_output` itself is left untouched -
    still just the tool's real output, used for file-location extraction -
    only `banner` (the human/model-facing message) gets the guidance
    prepended."""
    signature = detect_process_termination_signature(raw_output)
    if signature:
        crashed_tests = _crashed_test_artifacts(raw_output, known_files)
        type_ = (
            "verification_strategy_incompatible"
            if crashed_tests else "test_process_terminated"
        )
        label = (
            "VERIFICATION_STRATEGY_INCOMPATIBLE"
            if crashed_tests else "TEST_PROCESS_TERMINATED"
        )
        guidance = (
            _VERIFICATION_STRATEGY_INCOMPATIBLE_GUIDANCE
            if crashed_tests else _PROCESS_TERMINATION_GUIDANCE
        )
        banner = (
            f"{label} (evidence: {signature!r}):\n"
            f"{guidance}\n\n{raw_output}"
        )
    failure = _build_quality_gate_failure(
        type_, banner, raw_output, worktree_path, known_files, attempt,
    )
    if signature and crashed_tests:
        failure.likely_files = crashed_tests
        failure.diagnostics = {
            **(failure.diagnostics or {}),
            "reason_code": "VERIFICATION_STRATEGY_INCOMPATIBLE",
            "crashed_test_artifacts": crashed_tests,
        }
    return failure


def _strip_build_tool_info_noise(text: str) -> str:
    """Strips lines that start with Maven's own `[INFO]`-level log prefix before the
    plain-substring implication scan below runs. Maven prints an unconditional
    reactor/build banner ("[INFO]   from pom.xml", "[INFO] Building <name> ...") on
    EVERY invocation, success or failure - a known file's name appearing only inside
    that banner is not evidence it caused anything, any more than the 2026-08-10 fix's
    original finding was (see that fix's docstring note above) - it's just the build
    tool announcing itself.

    Found live again, 2026-08-12 (ignite_qpid_protocol): the 2026-08-10 fix above only
    helps when a REAL locator exists for some OTHER file to outrank the banner mention.
    A Runtime Verification hang (an unclosed resource keeping the JVM alive after all
    application logic already succeeded) produces NO compile-style locator anywhere -
    there's no error to point at, the code that's actually wrong is a `finally` block
    that never called `.close()` - so `located_basenames` is always empty for this
    failure class, the "prefer a locator" branch above never engages, and the plain
    substring fallback matched "pom.xml" via this exact banner line on every targeted
    retry of a real run, even though the Developer's own fix-analysis correctly
    diagnosed the true broken file (the main entrypoint) each time - the targeting
    mechanism simply never gave it that file to edit, burning the whole retry budget
    on a file that could never contain the fix.

    Deliberately narrow: only strips lines literally prefixed `[INFO]`, Maven's own
    log-level marker for its own banner/preamble noise. A genuine pom.xml problem is
    reported via `[ERROR]`/`[FATAL]` lines (e.g. "[FATAL] 'modelVersion' is missing.
    @ line 1, column 10", or a bare non-prefixed message like "Non-resolvable parent
    POM: ... in pom.xml") - neither is touched by this, so real pom.xml-caused
    failures remain fully detectable exactly as before."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("[INFO]")
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
    error with no file:line info), falls back to a plain substring scan - with
    Maven's own `[INFO]`-level banner noise stripped first (see
    _strip_build_tool_info_noise), so a build tool announcing its own manifest
    file's name can't masquerade as evidence when nothing else narrows the
    result either (found live, 2026-08-12 - see that helper's own docstring).
    This still only narrows the result when real evidence is available, never
    adds a new failure-to-match case; a genuine pom.xml-caused failure (which
    Maven reports via `[ERROR]`/`[FATAL]` lines, or a bare non-prefixed message)
    remains fully detectable exactly as before."""
    known_files = list(known_files)
    located_basenames = {basename for basename, _line in extract_error_source_locations(error_text)}
    if located_basenames:
        located = [f for f in known_files if os.path.basename(f) in located_basenames]
        if located:
            return located
        # A precise file:line locator exists but names NO known file at all -
        # found live, 2026-08-22 (ignite_qpid_protocol, workspace reused
        # across two unrelated runs without clearing prior output): a fresh
        # milestone 1 wrote only Protocol.java/Main.java, but the compile
        # error's real locators pointed at App.java/ProtocolTest.java - stale
        # leftovers from an EARLIER run's different package layout, sitting
        # in the workspace root, swept into this attempt's Maven compile scope
        # by the worktree sync, but never part of state.all_files_written or
        # ctx.established_files. Falling through to the substring/stem scan
        # below in this situation is actively dangerous: the error text
        # necessarily repeats the missing symbol's bare name ("cannot find
        # symbol: class Protocol") many times, which the bare-TitleCase-stem
        # fallback then misreads as evidence implicating Protocol.java - a
        # known, unrelated file that already correctly defines that class -
        # burning the whole retry budget re-editing it while the real,
        # unrecognized files causing the error are never even considered.
        # Stronger, more specific evidence (a real locator) must never be
        # overridden by a weaker heuristic just because it names files
        # outside this run's own scope - return the honest "nothing known
        # implicated" answer instead, so the caller falls back to a full-set
        # retry rather than confidently targeting the wrong file.
        return []

    scan_text = _strip_build_tool_info_noise(error_text)
    implicated = []
    for filepath in known_files:
        basename = os.path.basename(filepath)
        # Filename-token boundaries matter: ``App.java`` is not evidence for
        # that file when the validator actually named ``IntegrationApp.java``.
        # A plain substring check used to fabricate precisely that attribution.
        # The trailing boundary deliberately excludes ``.`` from the disallowed
        # set (unlike the leading one) - free-form prose (e.g. a Developer's own
        # FIX ANALYSIS text, read by extract_self_diagnosed_files()) routinely
        # ends a sentence with "...the real issue is in Config.json." and a
        # sentence-final period must not suppress an otherwise-real match.
        path_named = bool(re.search(
            rf"(?<![\w.-]){re.escape(filepath)}(?![\w-])", scan_text,
        ))
        basename_named = bool(basename and re.search(
            rf"(?<![\w.-]){re.escape(basename)}(?![\w-])", scan_text,
        ))
        # A bare stem match (the filename with its extension stripped) catches
        # prose that names the TYPE, not the FILE - found live, 2026-08-21
        # (protocol_encoder_java): the Developer's own FIX ANALYSIS said "the
        # Protocol class ... does not have the expected methods and
        # constructor" and never once spelled "Protocol.java", so the
        # extension-anchored basename check above missed it entirely and the
        # self-diagnosis redirect this feeds (extract_self_diagnosed_files)
        # silently failed to fire, leaving a targeted retry stuck re-editing
        # the wrong file (ProtocolDemo.java, where the compiler error
        # surfaced) instead of the file that actually needed the fix.
        # Deliberately narrow, not a blanket "match any bare word": gated on
        # the stem being TitleCase (stem[0].isupper()) and at least 3 chars.
        # TitleCase-stem-equals-type-name is a real, load-bearing convention
        # in Java/C#/C++/Kotlin (Protocol.java <-> class Protocol) but not in
        # Python/Ruby/JS, where the module filename and the type name are
        # different tokens by convention (protocol.py's class is still
        # `Protocol`, but the FILE's own stem is lowercase `protocol`) - so
        # this fallback naturally self-limits to the languages/conventions
        # where it's actually reliable signal, rather than special-casing by
        # language. The length/case guard also keeps a short or lowercase
        # word ("the protocol used here", "the db connection") from being
        # misread as naming a specific file.
        stem = os.path.splitext(basename)[0]
        stem_named = bool(
            stem and len(stem) >= 3 and stem[0].isupper()
            and re.search(rf"(?<![\w.-]){re.escape(stem)}(?![\w-])", scan_text)
        )
        if path_named or basename_named or stem_named:
            implicated.append(filepath)
    return implicated


def find_locator_files_outside_known_scope(error_text: str, known_files: Iterable[str]) -> List[str]:
    """Companion to extract_implicated_files() above - returns the basenames a
    real file:line locator named that matched NO known file, or [] when every
    located file is recognized (or there was no locator at all). Used to turn
    the same live incident that function's own docstring describes into a
    clear, actionable message instead of a silent full-set fallback: stale
    content left over in a workspace from an earlier, unrelated run (a
    different package layout, a differently-named entrypoint) can get swept
    into a fresh attempt's compile scope by the worktree sync while never
    being part of state.all_files_written or ctx.established_files - the
    compiler's own precise locator already says exactly which file(s), this
    just surfaces that instead of discarding it once extract_implicated_files()
    has already decided not to trust it for targeting."""
    basenames = {basename for basename, _line in extract_error_source_locations(error_text)}
    if not basenames:
        return []
    known_basenames = {os.path.basename(f) for f in known_files}
    return sorted(basenames - known_basenames)


def resolve_repository_locator_files(
    error_text: str,
    workspace_path: str,
    known_files: Iterable[str],
    *,
    max_files_scanned: int = 20_000,
) -> List[str]:
    """Resolve precise failure locators to unique existing repository files.

    This is a bounded, local re-grounding step for terminal regression
    failures that name an implementation file outside the current repair set.
    It never guesses between duplicate basenames and never reads file content:
    only a unique on-disk match for a real file:line locator is returned.
    """
    located_basenames = {
        basename for basename, _line in extract_error_source_locations(error_text)
    }
    known_basenames = {os.path.basename(path) for path in known_files}
    unresolved = located_basenames - known_basenames
    if not unresolved:
        return []

    excluded_dirs = {
        ".git", ".kriya", ".pytest_cache", "__pycache__", "node_modules",
        "target", "build", "dist", ".venv", "venv",
    }
    matches: Dict[str, List[str]] = {basename: [] for basename in unresolved}
    scanned = 0
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = sorted(name for name in dirs if name not in excluded_dirs)
        for filename in sorted(files):
            scanned += 1
            if scanned > max_files_scanned:
                return []
            if filename not in unresolved:
                continue
            full_path = os.path.join(root, filename)
            if not os.path.isfile(full_path) or os.path.islink(full_path):
                continue
            matches[filename].append(os.path.relpath(full_path, workspace_path))

    return sorted(
        paths[0] for paths in matches.values() if len(paths) == 1
    )
