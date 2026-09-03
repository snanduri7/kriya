"""Expected-vs-written file detection for the Developer retry loop completeness check and missing-file recovery. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import ast
import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import shlex
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


def _semantic_tokens(text: str) -> set[str]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return {
        token.lower() for token in re.split(r"[^A-Za-z0-9]+", expanded)
        if len(token) > 1
    }


def _artifact_name_tokens(path: str) -> set[str]:
    return _semantic_tokens(os.path.basename(path).rsplit(".", 1)[0])


def _explicitly_requests_new_artifact(path: str, goal: str) -> bool:
    """Scope explicit-new intent to the artifact phrase that carries it."""
    phrases = re.findall(
        r"\b(?:create|introduce|add)\s+(?:(?:a|an)\s+)?new\s+"
        r"([^,.;:\n]+?)(?=\s+(?:and|while|without|that|which|to)\b|[,.;:\n]|$)",
        goal or "",
        re.IGNORECASE,
    )
    if not phrases:
        return False
    path_tokens = _artifact_name_tokens(path)
    path_is_test = is_runnable_test_file(path)
    test_tokens = {"test", "tests", "spec", "specs", "regression"}
    generic_tokens = {"artifact", "file", "component", "module"}
    implementation_tokens = {"implementation", "source", "class", "service", "helper"}
    for phrase in phrases:
        phrase_tokens = _semantic_tokens(phrase)
        describes_test = bool(phrase_tokens & test_tokens)
        describes_implementation = bool(phrase_tokens & implementation_tokens)
        if describes_test and not describes_implementation:
            if path_is_test:
                return True
            continue
        if describes_implementation and not describes_test:
            if not path_is_test:
                return True
            continue
        if phrase_tokens & generic_tokens:
            return True
        if len(path_tokens & phrase_tokens) >= 2:
            return True
    return False


def prefer_existing_artifact_owners(
    planned_files: Iterable[str], goal: str, workspace_path: str,
) -> List[str]:
    """Resolve invented parallel artifact names back to a unique brownfield owner.

    This is stack-neutral: candidates compete only within the same extension
    and executable-test role. A unique exact basename or strict basename-token
    containment match is deterministic; otherwise filename tokens plus goal
    vocabulary provide the semantic score.
    An existing owner replaces a nonexistent planned path only when that
    evidence is unique and the request does not explicitly ask for a new artifact.
    """
    planned = list(planned_files)

    ignored = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"}
    existing: List[str] = []
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [name for name in dirs if name not in ignored]
        for filename in filenames:
            existing.append(os.path.relpath(os.path.join(root, filename), workspace_path))

    goal_tokens = _semantic_tokens(goal or "")
    resolved: List[str] = []
    claimed = {path for path in planned if os.path.exists(os.path.join(workspace_path, path))}
    for path in planned:
        if os.path.exists(os.path.join(workspace_path, path)):
            resolved.append(path)
            continue
        if _explicitly_requests_new_artifact(path, goal):
            resolved.append(path)
            continue
        extension = os.path.splitext(path)[1].lower()
        planned_tokens = _artifact_name_tokens(path)
        planned_is_test = is_runnable_test_file(path)
        exact_name_candidates = [
            candidate for candidate in existing
            if candidate not in claimed
            and os.path.splitext(candidate)[1].lower() == extension
            and is_runnable_test_file(candidate) == planned_is_test
            and os.path.basename(candidate) == os.path.basename(path)
        ]
        if len(exact_name_candidates) == 1:
            owner = exact_name_candidates[0]
            resolved.append(owner)
            claimed.add(owner)
            logger.info(
                "Resolved nonexistent planned artifact '%s' to unique exact-name "
                "existing owner '%s'.",
                path, owner,
            )
            continue
        containment_candidates = []
        if len(planned_tokens) >= 2:
            for candidate in existing:
                if candidate in claimed or os.path.splitext(candidate)[1].lower() != extension:
                    continue
                if is_runnable_test_file(candidate) != planned_is_test:
                    continue
                candidate_tokens = _artifact_name_tokens(candidate)
                if (
                    len(candidate_tokens) >= 2
                    and (
                        planned_tokens < candidate_tokens
                        or candidate_tokens < planned_tokens
                    )
                ):
                    containment_candidates.append(candidate)
        if len(containment_candidates) == 1:
            owner = containment_candidates[0]
            resolved.append(owner)
            claimed.add(owner)
            logger.info(
                "Resolved renamed planned artifact '%s' to unique token-containing "
                "existing owner '%s'.",
                path, owner,
            )
            continue
        scored = []
        for candidate in existing:
            if candidate in claimed or os.path.splitext(candidate)[1].lower() != extension:
                continue
            if is_runnable_test_file(candidate) != planned_is_test:
                continue
            candidate_tokens = _artifact_name_tokens(candidate)
            name_overlap = len(planned_tokens & candidate_tokens)
            goal_overlap = len(goal_tokens & candidate_tokens)
            if name_overlap == 0 or goal_overlap < 2:
                continue
            scored.append((name_overlap * 3 + goal_overlap, candidate))
        scored.sort(reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            owner = scored[0][1]
            resolved.append(owner)
            claimed.add(owner)
            logger.info("Resolved planned artifact '%s' to existing owner '%s'.", path, owner)
        else:
            resolved.append(path)
    return resolved


_RESPONSE_SHAPE_GOAL_RE = re.compile(
    r"\b(?:endpoint|response|payload|json|serializ\w*|present\w*|render\w*)\b",
    re.IGNORECASE,
)
_RESPONSE_OWNER_PATH_RE = re.compile(
    r"(?:controller|handler|resource|presenter|serializer|view|response|endpoint)",
    re.IGNORECASE,
)
_RESPONSE_CONSTRUCTION_RE = re.compile(
    # Deliberately excludes a bare `\b(?:Map|dict|object|json)\b` alternative -
    # that matched on a stray import/comment/unrelated field in almost any
    # source file, with no actual construction-call context, and a match
    # here is trusted at hardcoded confidence="high" downstream
    # (attribution.py) to move file ownership during plan surgery.
    r"\b(?:put|set|add|write|render|serializ\w*|toJson|response|payload)\s*\(",
    re.IGNORECASE,
)


def discover_response_construction_owners(
    workspace_path: str, goal: str, planned_files: Iterable[str] = (),
) -> List[str]:
    """Find existing files that construct a requested endpoint/response shape.

    Domain and service owners are insufficient when the repository explicitly
    materializes responses in a controller/presenter/serializer. Candidates
    must have both an architectural owner signal and response-construction
    syntax; goal/planned-file vocabulary then grounds them to this request.
    """
    if not _RESPONSE_SHAPE_GOAL_RE.search(goal or ""):
        return []
    vocabulary = _semantic_tokens(goal or "")
    for path in planned_files:
        vocabulary.update(_artifact_name_tokens(path))
    ignored = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"}
    owners = []
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [name for name in dirs if name not in ignored]
        for filename in filenames:
            path = os.path.relpath(os.path.join(root, filename), workspace_path)
            if _is_test_or_doc_file(path) or not _RESPONSE_OWNER_PATH_RE.search(path):
                continue
            try:
                with open(os.path.join(workspace_path, path), encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
            except OSError:
                continue
            if not _RESPONSE_CONSTRUCTION_RE.search(content):
                continue
            candidate_tokens = _artifact_name_tokens(path) | _semantic_tokens(content[:4000])
            if vocabulary & candidate_tokens:
                owners.append(path)
    return sorted(set(owners))


def include_response_construction_owners(
    planned_files: Iterable[str], goal: str, workspace_path: str,
) -> List[str]:
    """Add grounded existing response owners without replacing planned owners."""
    planned = list(planned_files)
    if any(_explicitly_requests_new_artifact(path, goal) for path in planned):
        # Same guard prefer_existing_artifact_owners applies: if the goal
        # explicitly asks for a brand-new artifact, this heuristic must not
        # widen write scope onto an unrelated existing file just because it
        # shares vocabulary tokens with the goal.
        return planned
    return list(dict.fromkeys(
        planned + discover_response_construction_owners(workspace_path, goal, planned)
    ))


_PRODUCTION_SOURCE_EXTENSIONS = {
    ".py", ".java", ".kt", ".kts", ".groovy", ".rb", ".js", ".jsx",
    ".ts", ".tsx", ".go", ".rs", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp",
}
_DECLARED_OWNER_RE = re.compile(
    r"\b(?:class|interface|record|enum|def|function|func|struct|trait)\s+([A-Za-z_$][\w$]*)",
)


def _behavioral_owner_identifiers(path: str, content: str) -> set[str]:
    """Return bounded, language-neutral identifiers a test can reference."""
    stem = os.path.splitext(os.path.basename(path))[0]
    identifiers = {stem} if len(stem) >= 3 else set()
    identifiers.update(
        match for match in _DECLARED_OWNER_RE.findall(content or "") if len(match) >= 3
    )
    return identifiers


def _references_any_identifier(content: str, identifiers: Iterable[str]) -> bool:
    return any(
        re.search(rf"(?<![\w$]){re.escape(identifier)}(?![\w$])", content or "")
        for identifier in identifiers
    )


def find_brownfield_test_redirections(
    workspace_path: str,
    original_contents: Dict[str, str],
    final_contents: Dict[str, str],
) -> List[Dict[str, str]]:
    """Detect a new parallel implementation taking over an existing owner's tests.

    Existing tests are strong ownership evidence.  A violation requires all three
    facts, so ordinary new production files and legitimate test extensions remain
    allowed: an existing production owner was referenced before the run, a new
    production candidate was created, and an existing test removed the old owner
    reference while adding a reference to the new candidate.
    """
    new_candidates = []
    for path, content in final_contents.items():
        if original_contents.get(path, "") or is_runnable_test_file(path) or _is_test_or_doc_file(path):
            continue
        if os.path.splitext(path)[1].lower() not in _PRODUCTION_SOURCE_EXTENSIONS:
            continue
        new_candidates.append((path, _behavioral_owner_identifiers(path, content)))
    if not new_candidates:
        return []

    ignored = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"}
    existing_owners = []
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [name for name in dirs if name not in ignored]
        for filename in filenames:
            path = os.path.relpath(os.path.join(root, filename), workspace_path)
            if is_runnable_test_file(path) or _is_test_or_doc_file(path):
                continue
            if os.path.splitext(path)[1].lower() not in _PRODUCTION_SOURCE_EXTENSIONS:
                continue
            try:
                with open(os.path.join(workspace_path, path), encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            existing_owners.append((path, _behavioral_owner_identifiers(path, content)))

    violations = []
    for test_path, final_test in final_contents.items():
        original_test = original_contents.get(test_path, "")
        if not original_test or not is_runnable_test_file(test_path):
            continue
        for owner_path, owner_ids in existing_owners:
            if not _references_any_identifier(original_test, owner_ids):
                continue
            if _references_any_identifier(final_test, owner_ids):
                continue
            for candidate_path, candidate_ids in new_candidates:
                if (
                    not _references_any_identifier(original_test, candidate_ids)
                    and _references_any_identifier(final_test, candidate_ids)
                ):
                    violations.append({
                        "existing_owner": owner_path,
                        "new_candidate": candidate_path,
                        "redirected_test": test_path,
                    })
    return violations


_JAVA_PUBLIC_METHOD_RE = re.compile(
    r"\bpublic\s+(?!class\b|interface\b|record\b|enum\b)(?:static\s+)?"
    r"(?:final\s+)?(?:<[^>{}]+>\s*)?([\w.$<>?,\[\]\s]+)\s+"
    r"([A-Za-z_$][\w$]*)\s*\(([^)]*)\)",
)
_PYTHON_PUBLIC_FUNCTION_RE = re.compile(
    r"^[ \t]*(?:async\s+)?def\s+([A-Za-z][A-Za-z0-9]*)\s*\(([^)]*)\)", re.MULTILINE,
)
# A public Java/Kotlin/Groovy record's own canonical constructor component
# list - its real, established data contract, distinct from any method it
# declares. _JAVA_PUBLIC_METHOD_RE explicitly excludes `record` declarations
# (its own negative lookahead), so without this a record's component shape
# is invisible to _normalized_public_signatures entirely.
_JAVA_RECORD_DECLARATION_RE = re.compile(
    r"\bpublic\s+record\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)",
)


def _normalize_java_type_list(raw: str) -> str:
    """Shared by method parameters and record components: strip annotations/
    `final`/parameter names, keep only the normalized type list, matching
    the same normalization already applied to method parameters below."""
    types = []
    for part in filter(None, (p.strip() for p in raw.split(","))):
        part = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", part)
        part = re.sub(r"\bfinal\s+", "", part).strip()
        types.append(re.sub(r"\s+[A-Za-z_$][\w$]*$", "", part))
    return ", ".join(types)


def _normalized_public_signatures(path: str, content: str) -> Dict[str, str]:
    """Extract stable public method/function signatures for common brownfield
    stacks, PLUS (Java/Kotlin/Groovy only) a public record's own canonical
    constructor component shape - its real, established data contract, not
    just its methods.

    The Java/Kotlin/Groovy signature key includes the normalized RETURN TYPE
    (e.g. "Customer find(long)"), not just name+params - found live, PRV-03
    legacy (2026-08-27): a retry changed CustomerService.find(long) to
    return CustomerDto instead of the established Customer, an established-
    contract-breaking change find_brownfield_public_api_changes() couldn't
    see at all when the signature key was name+params only (identical
    before and after, since the parameter list never changed).

    The record-component signature family (found live, PRV-03 hardened,
    same date) closes a sibling gap: a retry changed Customer from a
    4-component record to 5 (adding displayName as a canonical component
    instead of a derived accessor), breaking every existing caller's
    constructor call - this had no representation in this function's output
    at all before, so there was nothing to diff against."""
    extension = os.path.splitext(path)[1].lower()
    signatures: Dict[str, str] = {}
    if extension in {".java", ".kt", ".kts", ".groovy"}:
        for return_type, name, parameters in _JAVA_PUBLIC_METHOD_RE.findall(content or ""):
            normalized_return_type = re.sub(r"\s+", " ", return_type.strip())
            # Matches the pre-existing scoping exactly: only .java/.groovy
            # use "TYPE name" parameter syntax that _normalize_java_type_list
            # can correctly strip down to bare types. Kotlin's "name: TYPE"
            # syntax was never covered by that normalization before this
            # change either - preserved as-is (whitespace-collapsed only)
            # rather than risk mis-stripping a Kotlin parameter's real type.
            normalized_parameters = (
                _normalize_java_type_list(parameters) if extension in {".java", ".groovy"}
                else re.sub(r"\s+", " ", parameters.strip())
            )
            signatures[f"{normalized_return_type} {name}({normalized_parameters})"] = name
        for record_name, components in _JAVA_RECORD_DECLARATION_RE.findall(content or ""):
            normalized_components = _normalize_java_type_list(components)
            signatures[f"record {record_name}({normalized_components})"] = record_name
    elif extension == ".py":
        for name, parameters in _PYTHON_PUBLIC_FUNCTION_RE.findall(content or ""):
            if name.startswith("_"):
                continue
            normalized_parameters = re.sub(r"\s+", " ", parameters.strip())
            signatures[f"{name}({normalized_parameters})"] = name
    return signatures


def _goal_explicitly_requests_api_change(goal: str) -> bool:
    return bool(re.search(
        r"\b(?:rename|remove|replace|break|migrate)\b[^.\n]{0,50}"
        r"\b(?:public\s+)?(?:api|signature|callers?)\b",
        goal or "", re.IGNORECASE,
    ))


def find_brownfield_public_api_changes(
    workspace_path: str,
    original_contents: Dict[str, str],
    final_contents: Dict[str, str],
    goal: str,
) -> List[Dict[str, Any]]:
    """Reject model-preferred API renames during an ordinary brownfield repair.

    A removed signature is blocking only when existing tests or call sites name
    that API. Explicit API-migration requests are outside this repair guard.
    """
    if _goal_explicitly_requests_api_change(goal):
        return []
    evidence_contents: Dict[str, str] = {}
    ignored = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"}
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [name for name in dirs if name not in ignored]
        for filename in filenames:
            evidence_path = os.path.relpath(os.path.join(root, filename), workspace_path)
            try:
                with open(os.path.join(workspace_path, evidence_path), encoding="utf-8", errors="replace") as fh:
                    evidence_contents[evidence_path] = fh.read()
            except OSError:
                continue

    violations = []
    for path, final_content in final_contents.items():
        original_content = original_contents.get(path, "")
        if not original_content or is_runnable_test_file(path) or _is_test_or_doc_file(path):
            continue
        original_signatures = _normalized_public_signatures(path, original_content)
        final_signatures = _normalized_public_signatures(path, final_content)
        for signature, api_name in sorted(original_signatures.items()):
            if signature in final_signatures:
                continue
            evidence_files = sorted(
                evidence_path for evidence_path, evidence_content in evidence_contents.items()
                if evidence_path != path
                and re.search(rf"(?<![\w$]){re.escape(api_name)}\s*\(", evidence_content)
            )
            if evidence_files:
                violations.append({
                    "owner": path,
                    "removed_signature": signature,
                    "evidence_files": evidence_files,
                })
    return violations


_JAVA_MAIN_ENTRYPOINT_RE = re.compile(
    r"\bpublic\s+static\s+void\s+main\s*\(\s*String\s*(?:\[\s*\]|\.\.\.)\s*\w+\s*\)"
)


def _goal_explicitly_requests_new_entrypoint(goal: str) -> bool:
    """Mirrors _goal_explicitly_requests_api_change's own narrow, high-
    precision-only convention (see that function) - a false negative here
    only costs one more retry cycle on a genuinely wanted new entrypoint; a
    false positive would silently disable this whole gate for the goal that
    needed it most."""
    return bool(re.search(
        r"\b(?:add|create|introduce|new)\b[^.\n]{0,60}"
        r"\b(?:new\s+)?(?:entry\s*point|entrypoint|main\s+class|"
        r"cli\s+command|application\s+entrypoint)\b",
        goal or "", re.IGNORECASE,
    ))


def find_unrequested_architectural_surfaces(
    workspace_path: str,
    original_contents: Dict[str, str],
    final_contents: Dict[str, str],
    goal: str,
) -> List[Dict[str, Any]]:
    """UNREQUESTED_ARCHITECTURAL_SURFACE: reject a candidate that introduces a
    NEW executable entrypoint (Java `public static void main(String[] args)`,
    in either production or test source) into a repository that already has
    an established one, when the goal never asked for a second one.

    This is deliberately framed as the general case, not "reject main() in
    test files": the underlying failure is that verification strategy is
    allowed to mutate the product's real architectural surface - a test file
    growing a scaffolding main() is just the one CONFIRMED shape of it (see
    below). A narrower rule keyed on the test-filename convention would have
    missed the sibling incident where the SAME mistake lands in a production
    file instead (e.g. a second Application/Main class introduced purely to
    print a verification verdict). Scoped for now to Java main() entrypoints
    only - REST endpoints, CLI subcommands, schedulers, and bootstrap classes
    are the same failure family in spirit (verification-owned architecture),
    but each needs its own confirmed live incident and detection shape before
    being added here, matching this module's own established practice (see
    find_brownfield_public_api_changes's sibling docstrings) of extending a
    real signal rather than guessing at unconfirmed ones.

    Found live, PRV-04 (2026-08-27): the goal asked to extend the existing
    shared `App.main(String[] args)` entrypoint - done correctly, in place.
    The SAME generation also introduced a brand-new `public static void
    main(String[] args)` into AppTest.java, whose entire body was
    `System.out.println("[VERIFICATION] PASS");` - runtime-verification
    scaffolding satisfied by giving a TEST file its own executable
    entrypoint, instead of using the real application entrypoint, an
    existing test, or a harness-level invocation. "Confirm exactly one
    intended application entrypoint remains" - the goal's own acceptance
    criterion - no longer held once that second main() existed.

    Symmetric with find_brownfield_public_api_changes: same signature shape
    (workspace_path, original_contents, final_contents, goal ->
    List[violation dict]), same pre-write call site convention (compare a
    candidate against real baseline content before any byte reaches the
    worktree), same goal-explicit-request escape hatch. Deliberately does
    NOT reuse the api_contract_recovery phased state machine (RESTORE_
    PUBLIC_CONTRACT -> REPAIR_BEHAVIOR) - that machine exists because
    restoring a REMOVED contract and then repairing behavior behind it are
    two genuinely separate concerns needing two prompts. Here the whole fix
    is one concern ("remove the unrequested surface"), so a plain typed
    Failure with likely_files set is sufficient to ground a normal targeted
    retry at this file - see this function's call site in attempt.py."""
    if _goal_explicitly_requests_new_entrypoint(goal):
        return []

    # Baseline = every REAL entrypoint already on disk in the workspace,
    # independent of whether this candidate batch touches that file at all -
    # mirrors find_brownfield_public_api_changes's own full-repo evidence
    # walk immediately above, for the same reason: a candidate's own
    # `original_contents` only covers files THIS batch is about to write,
    # but "is there already an established entrypoint ANYWHERE in this
    # repo" must be answered against the whole workspace.
    ignored = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"}
    baseline_entrypoints: set = set()
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [name for name in dirs if name not in ignored]
        for filename in filenames:
            if not filename.endswith(".java"):
                continue
            full_path = os.path.join(root, filename)
            try:
                with open(full_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            if _JAVA_MAIN_ENTRYPOINT_RE.search(content):
                baseline_entrypoints.add(os.path.relpath(full_path, workspace_path))

    if not baseline_entrypoints:
        # Nothing established yet (a genuinely first-ever entrypoint, e.g. a
        # brand-new greenfield project) - no baseline to protect, matching
        # find_established_stack_drift's own "nothing established" no-op.
        return []

    violations = []
    for path, final_content in sorted(final_contents.items()):
        if not path.endswith(".java"):
            continue
        original_content = original_contents.get(path, "")
        already_had_entrypoint = bool(_JAVA_MAIN_ENTRYPOINT_RE.search(original_content))
        introduces_entrypoint = bool(_JAVA_MAIN_ENTRYPOINT_RE.search(final_content))
        if introduces_entrypoint and not already_had_entrypoint:
            violations.append({
                "file": path,
                "baseline_entrypoints": sorted(baseline_entrypoints),
                "reason_code": "UNREQUESTED_ARCHITECTURAL_SURFACE",
            })
    return violations


def find_unrestored_public_api_contracts(
    final_contents: Dict[str, str], recovery: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Cheap pre-gate check for a sticky API_CONTRACT_RECOVERY contract."""
    if not recovery:
        return []
    missing = []
    for violation in recovery.get("violations", []):
        owner = violation["owner"]
        signature = violation["removed_signature"]
        if signature not in _normalized_public_signatures(owner, final_contents.get(owner, "")):
            missing.append(dict(violation))
    return missing


def find_protected_api_reference_changes(
    original_contents: Dict[str, str], final_contents: Dict[str, str],
    recovery: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Prevent retries from erasing baseline references that prove the API contract."""
    if not recovery:
        return []
    def balanced_invocations(content: str, name_pattern: str) -> set[str]:
        """Extract formatting/order-insensitive call expressions with balanced parens."""
        fingerprints = set()
        for match in re.finditer(rf"\b({name_pattern})\s*\(", content or "", re.IGNORECASE):
            depth = 1
            index = match.end()
            quote = None
            escaped = False
            while index < len(content) and depth:
                char = content[index]
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = None
                elif char in {'"', "'"}:
                    quote = char
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                index += 1
            if depth == 0:
                expression = content[match.start():index]
                fingerprints.add(re.sub(r"\s+", "", expression))
        return fingerprints

    violations = []
    for contract in recovery.get("violations", []):
        # _normalized_public_signatures() now prefixes a Java/Kotlin/Groovy
        # method signature with its return type ("String format(String)")
        # and a record signature with the literal word "record"
        # ("record Customer(long, String)") - the bare callable name is the
        # LAST whitespace-separated token before "(", not everything before
        # it. A plain split("(", 1)[0] would return "String format" or
        # "record Customer" here, which then matches nothing in real call
        # sites (`format(...)`/`new Customer(...)`) and silently defeats
        # this whole evidence check. Still correct for a bare name with no
        # prefix (Python's "format(String)" shape) - .split() on a single
        # token just returns that token.
        api_name = contract["removed_signature"].split("(", 1)[0].split()[-1]
        for path in contract.get("evidence_files", []):
            if path not in final_contents:
                continue
            original = original_contents.get(path, "")
            final = final_contents[path]
            reference = re.compile(rf"(?<![\w$]){re.escape(api_name)}\s*\(")
            baseline_count = len(reference.findall(original))
            final_count = len(reference.findall(final))
            baseline_assertions = balanced_invocations(
                original, r"assert\w*|expect|should\w*",
            )
            final_assertions = balanced_invocations(
                final, r"assert\w*|expect|should\w*",
            )
            missing_assertions = sorted(baseline_assertions - final_assertions)
            # Contract evidence is presence-based, not cardinality-based. This
            # permits equivalent duplicate calls to be consolidated while still
            # refusing complete removal/redirection of the established API.
            contract_reference_missing = baseline_count > 0 and final_count == 0
            if contract_reference_missing or missing_assertions:
                violations.append({
                    "evidence_file": path,
                    "required_api": api_name,
                    "baseline_call_count": baseline_count,
                    "final_call_count": final_count,
                    "contract_reference_missing": contract_reference_missing,
                    "missing_assertions": missing_assertions,
                })
    return violations


def classify_api_recovery_file_roles(
    violations: Iterable[Dict[str, Any]], all_files: Iterable[str] = (),
) -> Dict[str, str]:
    """Assign explicit recovery roles without repository- or PRV-specific names."""
    roles: Dict[str, str] = {path: "OTHER" for path in all_files}
    for violation in violations:
        roles[violation["owner"]] = "API_OWNER"
        for path in violation.get("evidence_files", []):
            roles[path] = "EVIDENCE_TEST" if is_runnable_test_file(path) else "EVIDENCE_CALLER"
    return roles


_PROSE_CONTAMINATION_PATTERNS = (
    re.compile(r"^\s*(?:The|This)\s+(?:error|failure|issue|code|test|method)\s+"
               r"(?:is|was|occurs|fails|indicates|contains)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:The fix is|Looking at|To fix this|I (?:changed|updated|removed))\b", re.IGNORECASE),
)


def find_explanatory_prose_contamination(path: str, content: str) -> Optional[str]:
    """Return an obvious un-commented model explanation embedded in source."""
    if os.path.splitext(path)[1].lower() not in _PRODUCTION_SOURCE_EXTENSIONS:
        return None
    for line_number, line in enumerate((content or "").splitlines(), start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("//", "#", "/*", "*", "--")):
            continue
        if any(pattern.search(line) for pattern in _PROSE_CONTAMINATION_PATTERNS):
            return f"line {line_number}: {stripped[:160]}"
    return None


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

    Fix: add a narrow POM sourceDirectory only when known main sources share
    a bounded nonstandard directory outside `src/main/java`. Standard test
    roots are never main-source evidence. Workspace-root widening is refused
    because `${project.basedir}` recursively captures `src/test/java` and
    unrelated Java fixtures; root-level sources instead remain an explicit
    build-layout failure for plan/model recovery.

    Second real incident, same day, found via live validation of THIS fix:
    A broad `${project.basedir}` also recursively covers `.kriya/worktree/` -
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
    don't guess about overriding it), if only Maven main/test convention
    paths exist, if the only safe common root is the workspace itself, or
    if the content has no `<build>`/`</project>` anchor. Textual insertion
    (matching this module's existing pom.xml-
    adjacent corrections just above - exec:java/exec:exec and mainClass
    handling - rather than full XML-tree reconstruction: round-tripping
    this file's default Maven namespace through ElementTree risks mangling
    attributes/formatting well beyond the two small, targeted insertions
    actually needed here."""
    normalized_java_files = [
        f.replace("\\", "/").lstrip("./")
        for f in java_files
        if f.endswith(".java")
    ]

    # Maven's standard test tree is deliberately outside main sourceDirectory.
    # It is not evidence that the main-source layout needs widening. The old
    # predicate classified every path outside src/main/java as nonstandard,
    # so the mere presence of src/test/java triggered project.basedir and made
    # Maven compile JUnit tests during its main compile phase.
    def _under_path_segment(path: str, segment: str) -> bool:
        return path.startswith(segment) or f"/{segment}" in path

    relevant = [
        f for f in normalized_java_files
        if not _under_path_segment(f, "src/main/java/")
        and not _under_path_segment(f, "src/test/")
        and not f.startswith(("test/", "tests/"))
    ]
    if not relevant:
        return None
    if "<sourceDirectory>" in pom_content:
        return None

    # A workspace-root source directory is too broad to be a safe automatic
    # Maven repair: it recursively captures src/test/java and any unrelated
    # Java fixtures. Only configure a narrow, common nonstandard source root.
    # Root-level Java remains a detectable build-layout failure (the compile
    # gate's zero-class safety net) for explicit plan/model recovery.
    top_level_roots = {path.split("/", 1)[0] for path in relevant if "/" in path}
    if len(top_level_roots) != 1:
        return None
    source_root = next(iter(top_level_roots))
    if source_root in ("", ".", "..") or os.path.isabs(source_root):
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
        f"<sourceDirectory>${{project.basedir}}/{source_root}</sourceDirectory>"
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


def _correct_java_entrypoint_qualification(
    run_commands: Optional[List[List[str]]],
    java_main_classes: Dict[str, str],
) -> Optional[List[List[str]]]:
    """Corrects a `java <ClassName>` invocation's class-name TOKEN to its real
    fully-qualified form when it matches exactly one known entrypoint's
    simple name - without picking WHICH entrypoint runs (that ambiguous
    choice stays the model's own, unchanged, same as before this function
    existed).

    Found live, 2026-08-25 (protocol_encoder_java): two files each had a real
    main() method (Protocol.java's own self-test main(), ProtocolMain.java's
    demo main()), so the caller's own `len(java_main_classes) != 1` branch
    correctly declined to pick one - but previously just returned the
    model's raw guess completely unchanged. RunVerifierAgent.judge()'s own
    system prompt explicitly instructs "the bare class name only, no path"
    (kriya/agents/agent.py), which is flatly wrong for a class declared
    inside a package: `java ProtocolMain` (bare) fails with
    `NoClassDefFoundError: com/example/protocol/ProtocolMain (wrong name:
    ProtocolMain)` - Java requires the FULLY QUALIFIED name (`java
    com.example.protocol.ProtocolMain`) to run a packaged class from outside
    it. The model reliably repeats this exact wrong guess on retry (the
    live run's own self-correction micro-loop re-inferred and got the
    identical wrong command again) because the underlying INSTRUCTION, not
    sampling noise, is what's wrong - a case for a deterministic fix, not
    another prompt patch, matching this whole module's own established
    pattern (see this function's sibling below).

    Builds a simple-name -> FQCN map from every known entrypoint (a name
    that maps to two DIFFERENT FQCNs - e.g. two same-named classes in
    different packages - is dropped from the map entirely rather than
    guessed at). Only the FIRST token in a `java` command matching a mapped
    simple name is rewritten (the entrypoint class token appears once; a
    later token that happens to coincidentally match, e.g. a CLI argument
    value, is a program argument and must never be touched)."""
    if not run_commands:
        return run_commands
    simple_to_fqcn: Dict[str, str] = {}
    ambiguous_simple_names = set()
    for fqcn in java_main_classes.values():
        simple = fqcn.rsplit(".", 1)[-1]
        if simple in simple_to_fqcn and simple_to_fqcn[simple] != fqcn:
            ambiguous_simple_names.add(simple)
        else:
            simple_to_fqcn[simple] = fqcn
    for simple in ambiguous_simple_names:
        simple_to_fqcn.pop(simple, None)
    if not simple_to_fqcn:
        return run_commands
    corrected: List[List[str]] = []
    for cmd in run_commands:
        if not cmd or cmd[0] != "java":
            corrected.append(cmd)
            continue
        new_cmd = list(cmd)
        for i in range(1, len(new_cmd)):
            if new_cmd[i] in simple_to_fqcn:
                new_cmd[i] = simple_to_fqcn[new_cmd[i]]
                break
        corrected.append(new_cmd)
    return corrected


def ground_java_entrypoint_in_no_build_file_projects(
    run_commands: Optional[List[List[str]]],
    command_source: str,
    files_written: List[str],
    java_main_classes: Dict[str, str],
    jvm_module_flags: List[str],
    build_file_content: Optional[str],
    *,
    prefer_grounded_runtime: bool = False,
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
      own exec:exec/exec:java/mainClass corrections; untouched), unless the
      caller explicitly sets prefer_grounded_runtime for a verification-only
      subtask. That path already has grounded source/main-class evidence and
      must not let an inferred build-plugin command outrank it.
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

    When it DOES activate: compiles every .java file in files_written;
    verification-only build-file override excludes test-source trees and
    recognized test files, whose framework dependencies do not belong on raw
    javac's classpath,
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
    if (build_file_content and not prefer_grounded_runtime) or command_source == "goal_explicit":
        return run_commands
    def _verification_production_java_file(filepath: str) -> bool:
        normalized = filepath.replace("\\", "/").lstrip("./")
        return not (
            normalized.startswith(("src/test/", "test/", "tests/"))
            or "/src/test/" in normalized
            or is_runnable_test_file(filepath)
        )

    if len(java_main_classes) == 0:
        if any(cmd and cmd[0] == "java" for cmd in (run_commands or [])):
            return None
        return run_commands
    if len(java_main_classes) != 1:
        # Genuinely ambiguous WHICH class should run (2+ real main() methods -
        # e.g. one file's own self-test main() plus another file's demo
        # main()) - that choice correctly stays the model's own, unchanged
        # from before this branch existed. But whichever class name the model
        # DID choose can still be QUALIFICATION-corrected deterministically:
        # see _correct_java_entrypoint_qualification()'s own docstring for
        # the real live bug this closes.
        corrected = _correct_java_entrypoint_qualification(run_commands, java_main_classes)
        runtime_classes_dir = ".kriya/runtime-verification/classes"
        compile_files = sorted(
            f for f in files_written
            if f.endswith(".java") and not (
                prefer_grounded_runtime and not _verification_production_java_file(f)
            )
        )
        known_fqcns = set(java_main_classes.values())
        invocations: List[List[str]] = []
        for command in corrected or []:
            if not command or command[0] != "java":
                continue
            for index, token in enumerate(command[1:], start=1):
                if token in known_fqcns:
                    invocations.append(build_grounded_java_launch_command(
                        token, list(command[index + 1:]), runtime_classes_dir,
                        jvm_module_flags,
                    ))
                    break
        if compile_files and invocations:
            return [["javac", "-d", runtime_classes_dir] + compile_files] + invocations
        return corrected
    entrypoint_class = next(iter(java_main_classes.values()))
    compile_files = sorted(
        f for f in files_written
        if f.endswith(".java") and not (
            prefer_grounded_runtime and not _verification_production_java_file(f)
        )
    )
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
        elif prefer_grounded_runtime and cmd and cmd[0] in ("mvn", "mvnw", "./mvnw"):
            for token in cmd[1:]:
                if token.startswith("-Dexec.args="):
                    try:
                        invocations.append(shlex.split(token.split("=", 1)[1]))
                    except ValueError:
                        # A malformed model-inferred argument string is not safe
                        # evidence to reconstruct. The runtime contract can still
                        # supply its grounded input below.
                        pass
                    break
    if not invocations:
        invocations = [[]]

    runtime_classes_dir = ".kriya/runtime-verification/classes"
    return [["javac", "-d", runtime_classes_dir] + compile_files] + [
        build_grounded_java_launch_command(
            entrypoint_class, extra_args, runtime_classes_dir, jvm_module_flags,
        )
        for extra_args in invocations
    ]


def build_grounded_java_launch_command(
    entrypoint_class: str,
    argv: List[str],
    classes_dir: str = ".kriya/runtime-verification/classes",
    jvm_module_flags: Optional[List[str]] = None,
) -> List[str]:
    """Build the canonical Java application launch used by every verifier.

    Keeping this tiny constructor beside the entrypoint resolver prevents a
    generated test from defining a second, unrelated classpath policy.  The
    caller remains responsible for compiling into ``classes_dir``.
    """
    return [
        "java", "-cp", classes_dir,
        *(jvm_module_flags or []), entrypoint_class, *argv,
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
