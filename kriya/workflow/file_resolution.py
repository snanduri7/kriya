"""Expected-vs-written file detection for the Developer retry loop completeness check and missing-file recovery. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import ast
import asyncio
import difflib
import hashlib
import json
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


_NON_EXECUTABLE_PYTEST_FILES = {"__init__.py", "conftest.py"}
_PYTHON_TEST_FILE_RE = re.compile(r"^(?:test_.+|.+_test)\.py$")
_JAVA_TEST_FILE_RE = re.compile(
    r"^(?:Test.+|.+(?:Test|Tests|TestCase))\.(?:java|kt|kts|groovy)$",
)
_RUBY_TEST_FILE_RE = re.compile(r"^(?:test_.+|.+_(?:test|spec))\.rb$")
# Additive coverage beyond the three PolymorphicValidator-recognized stacks
# above - closes a real regression found in review: the goal-explicitly-
# requires-tests hard-fail gate below fires purely on this module's own
# filename recognition, independent of whether PolymorphicValidator can
# actually run anything for the target stack. Without these, a correctly
# generated JS/TS/Go/C# test file (e.g. calculator.test.js) was silently
# invisible to is_runnable_test_file(), hard-failing a goal that explicitly
# required tests even though a real, correctly-named test file existed.
_JS_TS_TEST_FILE_RE = re.compile(r"^.+\.(?:test|spec)\.(?:js|jsx|ts|tsx|mjs|cjs)$")
_GO_TEST_FILE_RE = re.compile(r"^.+_test\.go$")
_CSHARP_TEST_FILE_RE = re.compile(r"^.+(?:Test|Tests)\.cs$")


def is_runnable_test_file(filepath: str) -> bool:
    """Return whether ``filepath`` names a test module a supported runner can execute.

    Directory names such as ``tests/`` and ``src/test/`` are classification
    context, not proof that every file below them is itself a test.  In particular,
    package initializers, pytest configuration, fixtures, and helper modules can all
    live there.  Passing one of those files as pytest's targeted argument produces a
    valid zero-collection result, which is a test-selection error rather than a code
    failure.

    The filename conventions mirror the runners Kriya actually invokes: pytest,
    Maven Surefire/Gradle's common Java test names, and RSpec/minitest - plus
    Jest/Mocha/Vitest (JS/TS), `go test` (Go), and common .NET test naming (C#)
    for goal_explicitly_requires_tests()'s hard-fail acceptance gate, which
    checks filename recognition independent of PolymorphicValidator's own
    (narrower) set of stacks it can actually execute tests for. Unknown or
    unusually named tests remain covered by the full-suite fallback instead of
    being guessed as a target.
    """
    normalized = (filepath or "").replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    lowered = basename.lower()
    if lowered in _NON_EXECUTABLE_PYTEST_FILES:
        return False
    return bool(
        _PYTHON_TEST_FILE_RE.match(basename)
        or _JAVA_TEST_FILE_RE.match(basename)
        or _RUBY_TEST_FILE_RE.match(basename)
        or _JS_TS_TEST_FILE_RE.match(basename)
        or _GO_TEST_FILE_RE.match(basename)
        or _CSHARP_TEST_FILE_RE.match(basename)
    )


def find_runnable_test_files(files_written: Iterable[str]) -> List[str]:
    """Return deterministic, deduplicated executable test candidates."""
    return sorted({path for path in files_written if is_runnable_test_file(path)})


def extract_target_test(error_context: str, files_written: List[str]) -> Optional[str]:
    """Choose a target only when deterministic evidence identifies one test file.

    A failure that names exactly one known runnable test wins.  Otherwise a single
    candidate is unambiguous.  Multiple unreferenced candidates deliberately return
    ``None`` so the caller runs the suite; choosing the first element of a set made
    this decision nondeterministic and selected ``tests/__init__.py`` in the
    python_task_tracker live incident (2026-08-20).
    """
    candidates = find_runnable_test_files(files_written)
    if not candidates:
        return None

    error_lower = (error_context or "").replace("\\", "/").lower()
    referenced = []
    for candidate in candidates:
        normalized = candidate.replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1]
        if normalized.lower() in error_lower or basename.lower() in error_lower:
            referenced.append(candidate)
    if len(referenced) == 1:
        return referenced[0]
    if len(candidates) == 1:
        return candidates[0]
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


_MAIN_METHOD_PATTERN = re.compile(r"public\s+static\s+void\s+main\s*\(\s*String(?:\s*\[\s*\]|\.\.\.)\s+\w+\s*\)")
_PACKAGE_DECL_PATTERN = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _resolve_maven_main_class(worktree_path: str) -> Optional[str]:
    """Scans src/main/java for a class with a real `public static void
    main(String[] args)` method and returns its fully-qualified name (package
    + filename) - deterministic ground truth for exec-maven-plugin's
    mainClass, confirmed live as a recurring gap (2026-08-11, staged-build
    validation): RunVerifierAgent.judge() infers `mvn exec:java` correctly
    per the pom's own shape, but has no way to verify the pom's mainClass
    VALUE actually names a real class - seen twice, once as a completely
    missing mainClass (PluginParameterException: "mainClass ... missing or
    invalid", pom.xml never configured exec-maven-plugin at all) and once as
    a stale/wrong one (ClassNotFoundException: com.example.MainApp - pom.xml
    named a class that was never actually generated that attempt). Both are
    the same underlying gap: the pom's own mainClass claim was never checked
    against the real, generated source tree.

    Deliberately returns None (no correction applied, existing behavior
    unchanged) when zero or MORE THAN ONE class has a main method - a
    genuine multi-entry-point project is a real, if rare, possibility this
    function should never guess about; only an unambiguous single candidate
    is trustworthy enough to override what the model wrote."""
    src_root = os.path.join(worktree_path, "src", "main", "java")
    if not os.path.isdir(src_root):
        return None
    candidates = []
    for dirpath, _dirnames, filenames in os.walk(src_root):
        for fname in filenames:
            if not fname.endswith(".java"):
                continue
            try:
                with open(os.path.join(dirpath, fname), "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except Exception:
                continue
            if not _MAIN_METHOD_PATTERN.search(content):
                continue
            class_name = fname[: -len(".java")]
            pkg_match = _PACKAGE_DECL_PATTERN.search(content)
            candidates.append(f"{pkg_match.group(1)}.{class_name}" if pkg_match else class_name)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _find_plugin_configuration_span(pom_content: str, artifact_id: str) -> Optional[Tuple[int, int, bool]]:
    """Locates the <plugin>...</plugin> block for a given artifactId and
    whether it already has its own <configuration> element. Returns
    (block_start, block_end, has_configuration), or None if the plugin isn't
    present at all. Bounded to this ONE plugin's own block (not just the
    next "<configuration>" anywhere in the file) so a compiler-plugin
    correction can never accidentally land inside a DIFFERENT plugin's
    configuration (e.g. exec-maven-plugin's) when the compiler plugin
    itself happens to have none of its own."""
    marker = f"<artifactId>{artifact_id}</artifactId>"
    marker_idx = pom_content.find(marker)
    if marker_idx == -1:
        return None
    block_start = pom_content.rfind("<plugin>", 0, marker_idx)
    if block_start == -1:
        return None
    block_end = pom_content.find("</plugin>", marker_idx)
    if block_end == -1:
        return None
    block_end += len("</plugin>")
    has_configuration = "<configuration>" in pom_content[block_start:block_end]
    return block_start, block_end, has_configuration


def ensure_maven_covers_nonconventional_java_files(
    pom_content: str, java_files: Iterable[str], skills_relpath: Optional[str],
) -> Optional[str]:
    """Deterministic pom.xml correction for a real live incident, 2026-08-22
    (ignite_qpid_protocol milestone 3/4): milestones 1-2 ran with NO build
    file at all, writing Protocol.java/ProtocolParser.java directly at the
    workspace root (Java's default package needs no directory nesting) -
    exactly what the deterministic no-build-file entrypoint path above
    expects. Milestone 3 then introduced a pom.xml for the first time, and
    its own App.java was ALSO written at the workspace root, matching the
    established files' convention - but Maven's default `sourceDirectory`
    is `src/main/java`, which covers none of them. `mvn clean compile`
    found zero source files, reported success anyway (finding nothing to
    compile isn't itself a build error), and `target/classes` ended up
    empty - so `mvn exec:exec` then failed at RUNTIME with "Could not find
    or load main class App", a failure that looks like a code/classpath
    bug but is actually a build-layout gap the compile gate never caught (a
    genuine false positive: PolymorphicValidator's Maven branch treats
    returncode 0 as success unconditionally, without ever checking that
    anything was actually compiled). 5 straight retries chased phantom
    import/package theories because nothing in the Developer's own context
    said "Maven isn't looking where your established files actually live."

    Fix: widen the POM's own sourceDirectory to cover the workspace root
    whenever a known .java file (established OR just written this attempt)
    lives outside `src/main/java` - `${project.basedir}` is recursively
    scanned, EXCLUDING the skills directory (real .java example files ship
    inside active skills' own `examples/` folders - see e.g.
    skills/qpid/examples/BrokerServer.java - and would otherwise get swept
    into the same compile, risking duplicate/conflicting classes or
    missing-dependency errors from files that were never meant to be part
    of the generated application).

    Second real incident, same day, found via live validation of THIS fix:
    `${project.basedir}` also recursively covers `.kriya/worktree/` -
    Kriya's own sandbox git worktree (kriya/workflow/worktree.py), which
    contains its own nested copy of every active skill's `examples/`
    folder. A top-level `skills/**` exclude does not match that nested
    `.kriya/worktree/skills/**` copy, so it slipped straight through -
    `mvn compile` picked up `.kriya/worktree/skills/qpid/examples/*.java`
    (real external Qpid Broker-J/JMS classes not on this project's
    classpath) and failed a milestone that had already passed its own
    compile+run-verification, burning the whole retry budget on files that
    were never part of the goal. `.kriya` is unconditionally excluded below
    for this reason, independent of whether a skills directory is even
    configured - `.kriya/worktree` exists unconditionally the moment
    Quality Gates has run once (create_git_worktree()), matching the same
    "always exclude .kriya" convention worktree.py itself already applies
    (see its own untracked-file walk and DEFAULT_EXCLUDED_DIRS).

    Deliberately conservative: a no-op if `<sourceDirectory>` already
    appears anywhere in the pom (a real customization already exists -
    don't guess about overriding it), if no known `.java` file actually
    needs it (the ordinary Maven-convention case, left untouched), or if
    the content has no `<build>`/`</project>` anchor to insert against at
    all. Textual insertion (matching this module's existing pom.xml-
    adjacent corrections just above - exec:java/exec:exec and mainClass
    handling - rather than full XML-tree reconstruction: round-tripping
    this file's default Maven namespace through ElementTree risks mangling
    attributes/formatting well beyond the two small, targeted insertions
    actually needed here."""
    relevant = [
        f for f in java_files
        if f.endswith(".java") and not f.replace("\\", "/").startswith("src/main/java/")
    ]
    if not relevant:
        return None
    if "<sourceDirectory>" in pom_content:
        return None

    # Real live incident, same day this widening itself first shipped: when
    # worktree isolation is unavailable (e.g. create_git_worktree() fell back
    # to the real, unisolated workspace), this insertion lands directly in
    # the persistent, often-untracked pom.xml - which then gets synced into a
    # LATER milestone's fresh worktree as ordinary "existing content" the
    # Developer is told to preserve, with nothing explaining what it is or
    # that Quality Gates reapplies it automatically every attempt regardless.
    # A later milestone's Developer, confused by an unexplained non-standard
    # element, tried to imitate/extend it and produced malformed XML,
    # burning that milestone's entire retry budget on a self-inflicted
    # structural-corruption failure. The comment markers make this
    # self-explanatory wherever the file's raw content is later shown,
    # without needing every context-building call site to know about it.
    source_dir_block = (
        "<!-- Kriya: auto-managed by Quality Gates, reapplied automatically "
        "on every attempt when needed - do not duplicate, edit, or remove "
        "this element. -->\n"
        "<sourceDirectory>${project.basedir}</sourceDirectory>"
    )
    if "<build>" in pom_content:
        new_content = pom_content.replace("<build>", f"<build>\n{source_dir_block}", 1)
    elif "</project>" in pom_content:
        new_content = pom_content.replace(
            "</project>", f"<build>\n{source_dir_block}\n</build>\n</project>", 1,
        )
    else:
        return None

    exclude_names = [".kriya"]
    if skills_relpath:
        normalized_skills = skills_relpath.replace("\\", "/").strip("/")
        if normalized_skills and not normalized_skills.startswith(".."):
            exclude_names.append(normalized_skills)

    plugin_span = _find_plugin_configuration_span(new_content, "maven-compiler-plugin")
    if plugin_span:
        block_start, block_end, has_configuration = plugin_span
        exclude_block = "<excludes>" + "".join(
            f"<exclude>{name}/**</exclude>" for name in exclude_names
        ) + "</excludes>"
        if has_configuration:
            config_idx = new_content.index("<configuration>", block_start, block_end)
            insert_at = config_idx + len("<configuration>")
            new_content = new_content[:insert_at] + f"\n{exclude_block}" + new_content[insert_at:]
        else:
            artifact_marker = "<artifactId>maven-compiler-plugin</artifactId>"
            insert_at = new_content.index(artifact_marker, block_start, block_end) + len(artifact_marker)
            new_content = (
                new_content[:insert_at]
                + f"\n<configuration>{exclude_block}</configuration>"
                + new_content[insert_at:]
            )
    return new_content


_EXEC_MAIN_CLASS_PROPERTY_RE = re.compile(r"(<exec\.mainClass>)([^<]*)(</exec\.mainClass>)")


def correct_exec_main_class_property(pom_content: str, java_main_classes: Dict[str, str]) -> Optional[str]:
    """Deterministically corrects pom.xml's <exec.mainClass> property when it
    doesn't match the real class Kriya actually generated.

    Found live, 2026-08-22 (ignite_qpid_protocol): every active skill's own
    example pom.xml (skills/ignite-java17/examples/pom.xml,
    skills/qpid/examples/pom.xml, skills/activemq-artemis/examples/pom.xml)
    sets a concrete, plausible-looking default for this property -
    `<exec.mainClass>com.example.App</exec.mainClass>` and siblings - meant as
    illustration, but with nothing marking it as a placeholder rather than a
    real value. The skill's own rule text correctly warns against a
    HARDCODED ARGUMENT ("never a hardcoded literal class name - use
    ${exec.mainClass}"), and the Developer follows that rule faithfully - the
    exec:exec <argument> element genuinely is ${exec.mainClass}, not a
    literal. But the PROPERTY'S OWN DEFAULT VALUE is a separate, unguarded
    copy risk the rule text never addresses: every class Kriya has generated
    all day lives in the DEFAULT package (no `com.example` wrapper, matching
    Protocol.java/ProtocolParser.java/App.java's own established layout), so
    a Developer that copies the example's `com.example.App` value verbatim
    produces a pom.xml where ${exec.mainClass} correctly resolves to
    "com.example.App" - a class that was never written. `mvn exec:exec` then
    fails at RUNTIME (compile succeeds - this property has no bearing on
    what compiles) with "Could not find or load main class App" /
    ClassNotFoundException, reproducing this exact live incident's final
    failure byte-for-byte.

    This is the same "don't trust an LLM's guess at a concrete generated-
    artifact reference when Kriya can already compute ground truth for it
    deterministically" principle as ground_java_entrypoint_in_no_build_file_
    projects() (which already does this for the NO-pom.xml case) - extended
    to the case that function explicitly declines to touch (a real pom.xml
    already exists). java_main_classes is the same {filepath: class_name}
    map that function's own caller already builds via
    kriya.analyzer.graph.DependencyGraph.find_java_main_class().

    Deliberately conservative, matching this whole session's established
    safe-degrade posture: no-op if pom_content has no <exec.mainClass>
    property at all (this mechanism isn't in play), or if
    len(java_main_classes) != 1 (zero real entrypoints, or a genuinely
    ambiguous multi-entrypoint project - this function has no evidence to
    guess which one confidently, same as its sibling function's own
    len != 1 case)."""
    match = _EXEC_MAIN_CLASS_PROPERTY_RE.search(pom_content)
    if not match:
        return None
    if len(java_main_classes) != 1:
        return None
    real_class = next(iter(java_main_classes.values()))
    if match.group(2).strip() == real_class:
        return None
    return (
        pom_content[:match.start()]
        + match.group(1) + real_class + match.group(3)
        + pom_content[match.end():]
    )


_JAVA_PACKAGE_DECL_LINE_RE = re.compile(r"^[ \t]*package\s+[\w.]+\s*;[ \t]*\n?", re.MULTILINE)


def strip_package_declaration_matching_source_root(filepath: str, content: str) -> Optional[str]:
    """Deterministically strips a Java file's package declaration when that
    file sits DIRECTLY under src/main/java/ with no subdirectory nesting
    below it - the one case where Java's own rules make ANY package
    declaration unconditionally invalid, not merely unverified: a package
    must correspond to real subdirectory structure BELOW the recognized
    source root, and there is none here by construction.

    Found live, 2026-08-22 (ignite_qpid_protocol milestone 3/4): the
    Developer wrote BOTH Protocol.java and ProtocolParser.java (established
    files from milestones 1-2) with `package src.main.java;` - literally the
    Maven source-root PATH, dotted, mistaken for a package NAME. javac's
    resulting error ("duplicate class: src.main.java.Protocol" / "cannot
    access Protocol") reads exactly like a real code defect, not a
    boilerplate-vs-package-name confusion. The model correctly diagnosed the
    root cause on its VERY FIRST retry ("package declaration is incorrect...
    should be default/unnamed package") but then repeatedly failed to
    mechanically apply it - a byte-identical SEARCH/REPLACE no-op, an
    "identifier expected" syntax corruption from a botched edit, an anchor
    mismatch, a misdirected edit landing in the sibling file instead -
    burning 7 of 8 Quality Gate attempts (plus a fallback-model escalation)
    on a single-line deletion whose correctness was never actually in doubt
    after the first diagnosis. This is exactly the kind of mechanically-
    derivable fact ensure_maven_covers_nonconventional_java_files() and
    ground_java_entrypoint_in_no_build_file_projects() already exist to stop
    an LLM from having to reliably self-execute: apply the known-correct fix
    directly, every attempt, rather than trust the model to keep re-deriving
    (and re-botching) it.

    Deliberately narrow and safe: only fires for a file with ZERO
    subdirectory nesting below src/main/java/ (files WITH real nesting, e.g.
    src/main/java/com/example/Foo.java, may legitimately need a real
    package and are left untouched - this function has no way to derive
    what that package should be, and doesn't try to). Returns None (no-op)
    when the file isn't under that recognized source root, has real
    subdirectory nesting, or already has no package declaration to strip."""
    norm_path = filepath.replace("\\", "/")
    prefix = "src/main/java/"
    if not norm_path.startswith(prefix):
        return None
    rel = norm_path[len(prefix):]
    if "/" in rel:
        return None
    match = _JAVA_PACKAGE_DECL_LINE_RE.search(content)
    if not match:
        return None
    return content[:match.start()] + content[match.end():]


def downgrade_ungrounded_goal_explicit_commands(judgment: Dict[str, Any], goal: str) -> Dict[str, Any]:
    """Independent brutal review finding #2 (2026-08-15): RunVerifierAgent.judge()
    self-reports command_source ("goal_explicit" vs "inferred") in the same
    untrusted JSON response as everything else, with nothing anywhere checking
    that a "goal_explicit" claim is actually grounded in the goal text - and
    attempt.py's human-in-the-loop approval gate only fires for "inferred". A
    model that mislabels a guessed command as goal_explicit (confusion, or a
    goal/design/RAG-influenced response) skips the one safety check that
    exists, even in strict human-in-the-loop mode, for a command about to be
    executed via subprocess (kriya/tools/sandbox.py's own docstring: env
    allowlist + best-effort resource limits, NOT a hard sandbox boundary - see
    that module for why this self-report is functionally the only real gate).

    This is the same "never trust an LLM claim without an independent,
    deterministic check" pattern already used elsewhere in this codebase
    (grade()'s likely_files validated against known_files; check_skill_
    conflicts()'s index bounds-checking) - not a new design, closing a gap in
    an existing one. judge()'s own system prompt already tells the model
    "extract that exact command" when the goal states one explicitly - so by
    construction, a genuinely goal_explicit command's executable should
    already appear in the goal text. If it doesn't, don't trust the label:
    downgrade to "inferred" so the existing approval gate actually fires,
    rather than adding a second, parallel enforcement mechanism.

    Deliberately conservative in a specific, considered direction: checks
    ONLY the executable (command[0]'s basename, case-insensitive substring of
    the goal text) - not the full command line or arguments, which a model
    may reasonably expand/complete even for a genuinely goal-stated case
    (e.g. Kriya's own -e/mainClass corrections happen AFTER this check, and
    were never part of what the goal itself stated). A stricter full-command
    match would produce more false positives (a legitimate goal_explicit
    command incorrectly downgraded) for comparatively little extra safety.
    Accepts a real, known false-positive class in exchange (e.g. a goal
    saying "run using Maven" rather than naming "mvn" literally would fail
    this check) - deliberately chosen: the failure mode of a false positive
    here is an extra approval prompt for a command that was actually fine,
    a safe, cheap degrade; the failure mode of a false negative is the
    actual security-relevant gap this exists to close. When in doubt, this
    always resolves toward MORE approval-gating, never less.

    For a multi-command sequence, ALL commands must have a grounded
    executable for the sequence to stay goal_explicit - one ungrounded
    command downgrades the whole sequence (same "AND semantics, one failure
    invalidates the whole thing" pattern already used by
    extract_contract_verdict() for a multi-step FAIL). judgment/goal are
    never mutated - returns a new dict (shallow-copied) when a downgrade is
    needed, the original object unchanged otherwise."""
    if judgment.get("command_source") != "goal_explicit":
        return judgment
    run_commands = judgment.get("run_commands") or []
    goal_lower = goal.lower()
    for cmd in run_commands:
        if not cmd:
            continue
        executable = os.path.basename(cmd[0]).lower()
        if executable and executable not in goal_lower:
            logger.info(
                f"Run-verification judgment claimed command_source=goal_explicit, but "
                f"'{executable}' (from {cmd}) doesn't appear anywhere in the goal text - "
                "not trusting the label; downgrading to 'inferred' so the human-in-the-loop "
                "approval gate applies."
            )
            corrected = dict(judgment)
            corrected["command_source"] = "inferred"
            return corrected
    return judgment


# JPMS module-system flags a skill's own rules.txt may document as mandatory
# for a specific library's JVM startup (e.g. skills/ignite-java17/rules.txt:
# "Always add the mandatory --add-opens flags..."). Deliberately a closed set
# of the SAME flag families judge()'s own system prompt already treats as
# load-bearing for the Maven exec:exec-vs-exec:java distinction - not a
# general "extract any CLI-flag-shaped token" scanner.
_JVM_MODULE_FLAG_RE = re.compile(r"--(?:add-opens|add-exports|illegal-access)=\S+")


def extract_jvm_module_flags(skills_prompt: str) -> List[str]:
    """Deterministically pulls real, already-verified JVM module-system flags
    (--add-opens/--add-exports/--illegal-access) out of the active skills'
    OWN rules text, in the order they appear, deduplicated. Built for the same
    gap ground_java_entrypoint_in_no_build_file_projects() (below) closes: a
    bare (non-Maven) `java` invocation for a no-pom.xml project has no
    mechanism today for picking up flags a skill documents as mandatory
    (skills/ignite-java17/rules.txt: "Always add the mandatory --add-opens
    flags..." - written with Maven's exec:exec <argument> list in mind, since
    that was the only invocation shape ever exercised before this fix, but
    the underlying JVM-startup requirement is identical for a bare `java`
    call). Reuses the SAME trusted rules text already fed to the Developer
    agent when generating code - not a new trust boundary, just reading it
    for a different purpose. Never asks an LLM to reproduce these from
    memory: skills/ignite-java17/rules.txt alone lists over 25 individual
    flags verbatim - free-text reproduction of a list that size is exactly
    the class of positive, multi-part instruction this session's own
    findings already showed local models don't reliably get right, even when
    told to."""
    if not skills_prompt:
        return []
    seen: Dict[str, None] = {}
    for match in _JVM_MODULE_FLAG_RE.finditer(skills_prompt):
        seen.setdefault(match.group(0), None)
    return list(seen.keys())


def ground_java_entrypoint_in_no_build_file_projects(
    run_commands: Optional[List[List[str]]],
    command_source: str,
    files_written: List[str],
    java_main_classes: Dict[str, str],
    jvm_module_flags: List[str],
    build_file_content: Optional[str],
) -> Optional[List[List[str]]]:
    """Deterministically replaces RunVerifierAgent.judge()'s command-
    construction guess with a real compile+run sequence for a Java project
    with NO pom.xml/build.gradle - the shape that has zero deterministic
    compile/test grounding today (PolymorphicValidator's stack detection is
    Maven/Gradle-marker-only). Found live, 2026-08-21 (ignite_qpid_protocol
    milestone 3/4): three consecutive same-day prompt-level patches to
    judge() (established_files visibility, an explicit "no Maven" statement,
    a javac-prepend backstop) each fixed exactly what they targeted and
    surfaced the next gap underneath, because the underlying task - "which
    files to compile, what the entrypoint class is" - is mechanically
    answerable and should never have been a free-form LLM guess in the first
    place. This removes the guess entirely for the unambiguous case.

    java_main_classes is the caller's own precomputed {filepath: class_name}
    map (via kriya.analyzer.graph.DependencyGraph.find_java_main_class()),
    scoped to exactly the files Kriya has tracked as written this run
    (files_written - already the established_files-inclusive union, so a
    file compiled by an earlier milestone is covered automatically with no
    extra plumbing here). Deliberately takes the map as input rather than
    file contents directly, keeping this function pure/trivially testable
    and the DependencyGraph/file-I/O coupling in the caller (attempt.py),
    matching this module's own existing separation (this function is a
    sibling to _resolve_run_command()/downgrade_ungrounded_goal_explicit_
    commands() above - "correct judge()'s raw guess against real evidence" -
    not a file-reading utility).

    Returns run_commands UNCHANGED (a pure no-op) for every case this can't
    confidently resolve, matching this whole session's established safe-
    degrade posture - never a wrong override:
    - build_file_content given (a real pom.xml exists - Maven already has
      full deterministic grounding via PolymorphicValidator + this module's
      own exec:exec/exec:java/mainClass corrections; untouched).
    - command_source == "goal_explicit" - a goal-stated command is
      authoritative and must never be silently replaced.
    - len(java_main_classes) > 1 - a genuinely ambiguous case, e.g. a later
      milestone adds a second entrypoint - picking one would be a guess this
      function has no evidence to make confidently; falls back to judge()'s
      own existing (already-hardened with the "no Maven" prompt instruction
      and javac-prepend backstop) reasoning instead.

    Returns None - a distinct signal from "unchanged", meaning the caller
    should force should_run to False rather than execute anything - when
    len(java_main_classes) == 0 AND the guessed commands actually try to
    invoke a `java <SomeClass>`: zero of files_written has a real main()
    method, so ANY class name in that position is provably fabricated, not
    merely unverified. Found live, 2026-08-22 (ignite_qpid_protocol
    milestone 2/5, a pure Protocol/ProtocolParser library milestone with no
    entrypoint at all): judge() decided should_run=True anyway and
    hallucinated `java ProtocolParserTest` - a JUnit-style test class name
    that was never generated (the goal never asked for a test file). The
    existing "invalidate the cached judgment and re-infer" self-correction
    (kriya/workflow/attempt.py, run_command_targets_missing_entrypoint) only
    fires AFTER a failed execution, and even then re-guessed the identical
    wrong class name on the next attempt - the model doesn't have new
    evidence to guess differently with, so nothing forces it to converge.
    Meanwhile the retry loop burned 5 attempts re-editing ProtocolParser.java
    chasing this phantom failure (the Developer's own fix-analysis correctly
    said each time "this file needs no change, the problem is the missing
    test class" - but the loop kept retrying anyway), corrupting the file by
    attempt 8. This is the SAME "don't trust an LLM's guess at a concrete
    generated-artifact reference when Kriya can already compute ground truth
    for it deterministically" principle the len==1 substitution below already
    applies - extended to the symmetric zero-match case: substitute when
    unambiguous, refuse-to-run when provably nonexistent, only ever fall back
    to the model's own guess in the genuinely ambiguous middle (len > 1).

    When it DOES activate: compiles every .java file in files_written
    together (not just the entrypoint - a multi-file program needs its real
    dependencies compiled in the SAME javac invocation), preserves any CLI
    ARGUMENTS judge()'s own guess already appended after its (possibly
    wrong) class name in a ["java", ...]-shaped command - real semantic
    reasoning about what a goal's CLI needs (e.g. "add"/"list" sequences)
    that this function has no way to derive on its own and shouldn't
    discard - and applies jvm_module_flags (extract_jvm_module_flags() above)
    between "java" and the class name on every invocation, since a flag a
    library's skill documents as mandatory is required regardless of which
    arguments follow it."""
    if build_file_content or command_source == "goal_explicit":
        return run_commands
    if len(java_main_classes) == 0:
        if any(cmd and cmd[0] == "java" for cmd in (run_commands or [])):
            return None
        return run_commands
    if len(java_main_classes) != 1:
        return run_commands
    entrypoint_class = next(iter(java_main_classes.values()))
    compile_files = sorted(f for f in files_written if f.endswith(".java"))
    if not compile_files:
        return run_commands

    # Whatever the model's own guess appended AFTER its class name (real
    # semantic reasoning about CLI arguments this function has no way to
    # derive on its own) is preserved - located by finding wherever the
    # REAL entrypoint class name (fully-qualified or simple) appears as its
    # own token in the model's guessed command, not by a fixed position.
    # A fixed "args start at index 2" assumption would misfire on exactly
    # the observed live shape ["java", "-cp", "target/classes:$(mvn ...)",
    # "App"] - the class name sits at index 3 there, not 1, and everything
    # before it (a garbage classpath guess for a project with no build
    # system to resolve one) must be discarded, not preserved as if it were
    # a program argument.
    simple_name = entrypoint_class.rsplit(".", 1)[-1]
    invocations: List[List[str]] = []
    for cmd in (run_commands or []):
        if cmd and cmd[0] == "java":
            extra_args: List[str] = []
            for i, tok in enumerate(cmd[1:], start=1):
                if tok in (entrypoint_class, simple_name):
                    extra_args = list(cmd[i + 1:])
                    break
            invocations.append(extra_args)
    if not invocations:
        invocations = [[]]

    return [["javac"] + compile_files] + [
        ["java"] + jvm_module_flags + [entrypoint_class] + extra_args
        for extra_args in invocations
    ]


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
    # Deterministically override exec:java's mainClass with ground truth from
    # the real generated source tree, rather than trusting whatever pom.xml
    # happens to say - see _resolve_maven_main_class's own docstring for the
    # two confirmed-live failure modes this closes (a completely missing
    # mainClass, and a stale one pointing at a class that was never actually
    # generated that attempt). -Dexec.mainClass on the command line takes
    # precedence over pom.xml's <configuration><mainClass> per exec-maven-
    # plugin's own documented user-property binding, so this is a safe,
    # additive override - never edits pom.xml itself, and is a no-op
    # (same value) on the common case where the pom's own mainClass was
    # already correct. Only applied when exactly one real entry point is
    # found (see the function's own docstring for why an ambiguous result
    # never guesses); exec:exec is deliberately out of scope here - its
    # main class is one positional element inside <arguments>, not a
    # separately overridable parameter the same way.
    if workspace_path and command and os.path.basename(command[0]) == "mvn" and "exec:java" in command:
        resolved_main_class = _resolve_maven_main_class(workspace_path)
        if resolved_main_class and not any(tok.startswith("-Dexec.mainClass=") for tok in command):
            command = list(command) + [f"-Dexec.mainClass={resolved_main_class}"]
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


_FENCED_CODE_BLOCK_PATTERN = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
_PLANNER_CODE_REUSE_LOOKBACK_CHARS = 300


def _looks_like_java(content: str) -> bool:
    return bool(re.search(r"\b(class|interface|enum|record)\b", content))


def _looks_like_python(content: str) -> bool:
    # A real syntax check, not a keyword heuristic - unlike Java, valid Python
    # has no required top-level keyword to search for (a file with nothing
    # but `print("hi")` is completely valid), so a regex can't distinguish
    # plausible-looking-but-broken content from real source the way the Java
    # check does. ast.parse() is exact rather than approximate: it catches
    # the actual failure mode this table exists for (2026-08-13, live -
    # Kriya's own "[VERIFICATION] PASS" runtime-verification marker ended up
    # embedded as a bare, unquoted line in a Planner-drafted greet.py -
    # syntactically invalid, but it still contains real `def`/`print`
    # elsewhere, so a keyword-presence check would have missed it entirely).
    try:
        ast.parse(content)
        return True
    except SyntaxError:
        return False


def _looks_like_xml(content: str) -> bool:
    try:
        ET.fromstring(content)
        return True
    except ET.ParseError:
        return False


def _looks_like_json(content: str) -> bool:
    try:
        json.loads(content)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


# Extension -> plausibility check. Each entry answers "does this content
# genuinely look like/parse as this language" for a Planner-drafted fenced
# code block before it's trusted as a file's real content (see
# extract_planner_code_blocks() below). Deliberately per-language rather than
# one shared heuristic - Java's cheapest reliable signal is a keyword regex
# (no stdlib Java parser available here); Python/XML/JSON's are exact syntax
# validation via a real parser, which is strictly stronger where available.
# Extensions with no entry here get no plausibility check at all - this is a
# known, incomplete allowlist, not a claim of full coverage (no reliable
# syntax check exists for something like `.properties`, arbitrary key=value
# text - adding a weak heuristic there risks its own false positives/
# negatives, so it's deliberately left unchecked rather than guessed at).
# Root cause of this table's very first version (2026-08-12): a fenced block
# near a ".java" file's heading that was actually the qpid/ignite-java17
# skill's own documented run-command example ("mvn -q compile exec:exec
# -Dexec.mainClass=..."), not Java source - the Planner had written it as a
# "here's how to run this" note, and extraction had no way to tell it apart
# from real code before this check existed. The identical failure shape
# recurred live, 2026-08-17 (ignite_qpid_person, run b-10k), through the
# exact gap this table's own docstring already warned about: `.xml` had no
# entry, so a fenced block near `ignite-config.xml`'s heading - whatever its
# actual content, not recoverable after the fact since the worktree is
# reused/reset across retries with no per-attempt git history - was accepted
# unconditionally and immediately failed structural corruption with
# "malformed XML: syntax error: line 1, column 0", consistent with non-XML
# content (anything not starting with `<` fails `ET.fromstring()` at the
# very first character). `.json` added alongside it for the same reason
# (equally common in this pipeline's generated projects, equally guessable
# via a real parser).
_MIN_PLAUSIBLE_CODE_CHECK: Dict[str, Callable[[str], bool]] = {
    ".java": _looks_like_java,
    ".py": _looks_like_python,
    ".xml": _looks_like_xml,
    ".json": _looks_like_json,
}


def _is_complete_maven_pom(content: str) -> bool:
    """A Maven POM is a standalone document rooted at ``project``.

    XML well-formedness alone is insufficient here: Planner prose often uses
    valid XML fragments (for example ``<dependencies>...</dependencies>``) to
    illustrate one step.  Such a fragment is useful documentation, but it is
    not complete content for the conventional Maven artifact ``pom.xml``.
    Namespace attributes are intentionally ignored by comparing the local
    name; both minimal test fixtures and normal namespaced Maven POMs remain
    valid.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return False
    return root.tag.rsplit("}", 1)[-1] == "project"


# Conventional artifact name -> standalone-file contract.  This layer is
# deliberately separate from the extension parser above: extension checks ask
# whether a block is syntactically valid source, while these checks ask whether
# it is a complete instance of the specific standard artifact the Architect
# requested.  Add only ecosystem conventions with an objective root/container
# contract; never project class names, dependency names, or use-case strings.
_STANDALONE_ARTIFACT_CHECK: Dict[str, Callable[[str], bool]] = {
    "pom.xml": _is_complete_maven_pom,
}


# Coarse "did the Planner call clearly fail" bar - not a quality check, the
# same kind of cheap sanity gate _MIN_PLAUSIBLE_CODE_CHECK above applies to a
# single fenced block, just applied to the whole plan. Real, reproduced
# twice this session (SME review of PlannerAgent + spikes/model_speed_poc/
# planner_reasoning_poc.py, a real ignite_qpid_protocol run): a thinking-
# capable model can burn its entire token budget on invisible reasoning and
# return empty content, or run out of budget mid-response leaving an
# unclosed fenced code block - and PlannerAgent had ZERO check of any kind
# before this (unlike ArchitectAgent's run_with_file_list(), which at least
# validates its JSON block). A truncated plan flowed silently into
# Architect's prompt with nothing anywhere detecting or explaining why.
#
# Deliberately NOT a minimum-length threshold, despite that being the first
# version tried: a real regression run against the existing test suite
# found 103 of 121 first-call (Planner-position) mock strings across
# tests/test_workflow.py are under 20 chars ("Step 1: Write code" and
# similar terse placeholders - those tests are exercising OTHER stages, not
# plan content, and were never meant to look like a real plan). Any length
# floor anywhere near a real plan's actual size is fundamentally
# incompatible with how this whole suite mocks LLM responses. The two
# checks below are both things actually observed in a real incident (true
# empty content; an unclosed fence from running out of budget mid-response)
# and neither can plausibly collide with a short hand-written test string.
def check_plan_completeness(plan_text: str) -> Optional[str]:
    """Returns a human-readable reason string if the Planner's raw plan text
    looks empty or truncated, None if it looks complete enough to proceed."""
    stripped = plan_text.strip() if plan_text else ""
    if not stripped:
        return (
            "plan is empty - the model may have run out of token budget before producing "
            "any real content (e.g. spent it all on reasoning/thinking)"
        )
    if plan_text.count("```") % 2 != 0:
        return (
            "plan contains an unclosed fenced code block (odd number of ``` markers) - "
            "looks truncated mid-response, likely ran out of token budget"
        )
    return None


def extract_planner_code_blocks(plan_text: str, expected_files: Iterable[str]) -> Dict[str, str]:
    """PlannerAgent's own system prompt only ever asks for a step-by-step plan
    ("outline what files need to be created... Format your plan clearly in
    Markdown") - never full code - but qwen3-coder:30b and others routinely
    over-deliver complete, plausible-looking file content in fenced code
    blocks anyway (confirmed live, 2026-08-11: a real Planner response wrote
    out full, syntactically valid pom.xml/Protocol.java/ProtocolParser.java/
    Main.java content under "### path/to/File.java" headings). Architect then
    explicitly discards it (its own prompt: "DO NOT write or output the full
    implementation code"), and Developer regenerates every file from scratch
    regardless of whether the Planner's own draft was already correct -
    confirmed via direct code read this session that nothing anywhere reuses
    it. This function is the first step of closing that gap: given the raw
    plan text and the Architect's own resolved expected-file list, finds
    every fenced code block whose PRECEDING text names one of those specific
    files (by full path or basename) - not any code-looking fence, only ones
    matching a file Kriya ALREADY knows it needs, the same safety scoping
    extract_expected_files() uses elsewhere. Uses the LAST fence found for a
    given file if it's mentioned more than once (a plan that shows a file,
    then a corrected/final version of it later, should yield the final one).

    A matched fence is also checked against _MIN_PLAUSIBLE_CODE_CHECK (for
    extensions with an entry there) before being trusted - a fence that
    doesn't even look like (or, where checkable, doesn't actually parse as)
    the target language is rejected rather than returned, so a filename
    mention followed by an unrelated snippet (e.g. a run-command example) or
    genuinely broken content doesn't get treated as that file's real content.
    Standard artifacts with an objective whole-document contract are then
    checked by _STANDALONE_ARTIFACT_CHECK.  This distinguishes a syntactically
    valid documentation fragment from a complete reusable file (for example a
    ``<dependencies>`` fragment versus a Maven ``<project>`` document).

    Otherwise deliberately does nothing more than this extraction - whether/
    how the result gets used (and re-verified through the exact same
    Quality Gates any Developer-generated content goes through) is the
    caller's decision, not this function's."""
    expected_files = list(expected_files)
    if not expected_files or not plan_text:
        return {}
    basename_to_path: Dict[str, str] = {}
    for f in expected_files:
        basename_to_path.setdefault(os.path.basename(f), f)

    results: Dict[str, str] = {}
    for match in _FENCED_CODE_BLOCK_PATTERN.finditer(plan_text):
        content = match.group(1)
        if not content.strip():
            continue
        preceding = plan_text[max(0, match.start() - _PLANNER_CODE_REUSE_LOOKBACK_CHARS):match.start()]
        # Full paths and basenames are both candidates; whichever mention sits
        # CLOSEST to the fence (the largest rfind position) wins - not just
        # whichever candidate happens to be checked first. An earlier file's
        # own heading is still within the lookback window once the plan has
        # shown 2+ files close together, so "first substring match found"
        # (via an unordered set, no less) would silently steal a later
        # block's match - confirmed as a real bug via a 3-file test fixture
        # before this rfind-based fix.
        best_pos = -1
        found_path = None
        for path in expected_files:
            pos = preceding.rfind(path)
            if pos > best_pos:
                best_pos = pos
                found_path = path
        for basename, path in basename_to_path.items():
            pos = preceding.rfind(basename)
            if pos > best_pos:
                best_pos = pos
                found_path = path
        if found_path:
            ext = os.path.splitext(found_path)[1]
            check = _MIN_PLAUSIBLE_CODE_CHECK.get(ext)
            if check and not check(content):
                # Reject, don't just skip silently into results - the
                # caller's own existing all-or-nothing check (reuse Planner
                # code only when EVERY expected file matched) then safely
                # falls through to a real Developer generation for the
                # whole set, instead of writing obviously-wrong content
                # straight to disk with zero review.
                logger.debug(
                    f"Rejected a Planner code block for '{found_path}': content doesn't look "
                    f"like valid {ext} source - likely an unrelated snippet (e.g. a run-command "
                    "example) near the file's heading, or genuinely broken content."
                )
                continue
            artifact_check = _STANDALONE_ARTIFACT_CHECK.get(os.path.basename(found_path))
            if artifact_check and not artifact_check(content):
                logger.debug(
                    f"Rejected a Planner code block for '{found_path}': content is a valid "
                    "snippet but not a complete standalone instance of that standard artifact."
                )
                continue
            results[found_path] = content
    return results


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
