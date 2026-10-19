"""The process-boundary test-candidate facts and the Java dependency-graph readers of one attempt (full
modularization W4, E14; OI-57).

Moved verbatim from ``kriya/workflow/attempt.py`` (E9 kept them there; E14 gives them an explicit owner):

- the runtime/process-boundary contract of a subtask (``_runtime_contract_requirements``) and the terminating cases it
  explicitly grounds (``_required_process_terminating_cases``, ``_NONZERO_PROCESS_EXIT_RE``,
  ``_TERMINATING_CASE_MARKERS``), the attempt-1 test-generation constraint they imply
  (``_initial_test_process_boundary_constraint``, ``_INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT``);
- the two test-candidate source checks the candidate gates raise on (``find_in_process_terminating_test_invocations``
  with its Java source-shape helpers ``_enclosing_test_method``, ``_enclosing_test_method_source``,
  ``_resolve_java_main_argv_expression``, ``_runtime_contract_expects_numeric_input``,
  ``_terminating_case_from_argv_expression``; ``find_ungrounded_java_child_process_tests``);
- the short-lived dependency-graph readers (``_dependency_graph_db_path``, ``_extract_class_names_best_effort``,
  ``_find_java_main_class_best_effort``) and the entrypoint map built from them (``_build_java_main_class_map``).

``kriya/workflow/attempt.py`` keeps every moved name (a forwarder, or a re-export of a constant) and the Deps fields
that bind them (GateDeps, RuntimeDeps, CandidateDeps, GenerationDeps) still bind attempt.py's names, so a test patch
on ``kriya.workflow.attempt`` keeps intercepting the moved code. What arrives in ``ProcessBoundaryDeps``, resolved
through ``kriya.workflow.attempt`` when a forwarder runs (``attempt._process_boundary_deps()``): attempt.py's
``logger`` (the records keep its name), the moved siblings a body calls (their attempt.py forwarders), and the loader
of the bodies' function-local ``kriya.analyzer.graph`` import (its allowlisted edge stays counted on attempt.py).
This module never imports ``kriya.workflow.attempt``, ``workflow``, ``workflow_controller`` or ``kriya.cli``, nor any
all-import-cycle module.

``AttemptCtx`` is the attempt's ``kriya.workflow.attempt.AttemptContext``, spelled ``Any`` (it lives in attempt.py,
OI-46; the declared E2-E12 departure, OI-33).
"""
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from kriya.workflow.file_resolution import build_grounded_java_launch_command, is_runnable_test_file

AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext


@dataclass(frozen=True)
class ProcessBoundaryDeps:
    """What the moved process-boundary code may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._process_boundary_deps()``. Every field is required: a builder that omits one fails at construction,
    never at the first use."""

    # attempt.py's logger
    logger: Any
    # the loader of the bodies' function-local kriya.analyzer.graph import (allowlisted on attempt.py)
    load_dependency_graph: Callable[[], Any]
    # the moved siblings (their forwarders on attempt.py)
    _dependency_graph_db_path: Callable[..., Any]
    _enclosing_test_method: Callable[..., Any]
    _enclosing_test_method_source: Callable[..., Any]
    _find_java_main_class_best_effort: Callable[..., Any]
    _required_process_terminating_cases: Callable[..., Any]
    _resolve_java_main_argv_expression: Callable[..., Any]
    _runtime_contract_expects_numeric_input: Callable[..., Any]
    _terminating_case_from_argv_expression: Callable[..., Any]


_NONZERO_PROCESS_EXIT_RE = re.compile(
    r"(?:non[ -]?zero(?:\s+[a-z]+){0,3}\s+exit|(?:process\s+)?exits?(?:\s+code)?\s+(?:is\s+|must\s+be\s+)?non[ -]?zero)",
    re.IGNORECASE,
)

_INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT = (
    "\n\n=== Process-boundary test constraint ===\n"
    "The structured runtime/process-boundary contract explicitly requires a non-zero process exit. "
    "Tests must not invoke a process-terminating application path in the test runner's own process. "
    "Do not use SecurityManager or System.setSecurityManager to intercept termination. Preserve the "
    "required System.exit behavior. Verify exit code/stdout/stderr through a child process or leave "
    "that obligation to the declared application_runtime verifier. This constraint changes only the "
    "verification strategy; do not invent additional product behavior or edge cases (for example, "
    "overflow handling) that are absent from the authoritative goal."
)


def _initial_test_process_boundary_constraint(
    deps: ProcessBoundaryDeps,
    required_verification: List[Dict[str, Any]], target_files: Optional[List[str]],
) -> str:
    """Return a test-generation constraint only for an explicit runtime fact."""
    if not any(is_runnable_test_file(path) for path in (target_files or [])):
        return ""
    return (
        _INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT
        if deps._required_process_terminating_cases(required_verification)
        else ""
    )


def _runtime_contract_requirements(ctx: AttemptCtx) -> List[Dict[str, Any]]:
    """Collect runtime/process-boundary facts available to this subtask."""
    requirements = list(ctx.required_verification)
    if ctx.structured_plan is not None:
        for subtask in ctx.structured_plan.subtasks:
            requirements.extend(
                verification.model_dump(mode="json")
                for verification in subtask.verification
            )
        current = ctx.structured_plan.subtask_by_id(ctx.current_subtask_id or "")
        relevant_ids = set(current.relevant_global_invariant_ids if current else [])
        for invariant in ctx.structured_plan.global_invariants:
            if invariant.id in relevant_ids:
                requirements.append({
                    "description": invariant.statement,
                    "process_boundary_authority": "relevant_global_invariant",
                    "invariant_id": invariant.id,
                })
    deduplicated: Dict[str, Dict[str, Any]] = {}
    for requirement in requirements:
        key = repr(sorted(requirement.items()))
        deduplicated[key] = requirement
    return list(deduplicated.values())


_TERMINATING_CASE_MARKERS = {
    "invalid": ("invalid input", "invalid value", "malformed input", "non-numeric input"),
    "missing": ("missing input", "no input", "missing argument", "no argument"),
}


def _required_process_terminating_cases(
    required_verification: List[Dict[str, Any]],
) -> List[str]:
    """Extract only case labels explicitly grounded by the runtime contract."""
    cases = set()
    for requirement in required_verification:
        runtime_verifier = (
            requirement.get("verifier_kind") == "application_runtime"
            and requirement.get("requires_runtime_execution") is True
        )
        relevant_global_invariant = (
            requirement.get("process_boundary_authority") == "relevant_global_invariant"
        )
        if not (runtime_verifier or relevant_global_invariant):
            continue
        description = str(requirement.get("description") or "")
        if not _NONZERO_PROCESS_EXIT_RE.search(description):
            continue
        lowered = description.lower()
        for case, phrases in _TERMINATING_CASE_MARKERS.items():
            if any(phrase in lowered for phrase in phrases):
                cases.add(case)
    return sorted(cases)


def _enclosing_test_method(source: str, offset: int) -> Optional[str]:
    prefix = source[:offset]
    matches = list(re.finditer(
        r"(?m)^\s*(?:public\s+|protected\s+|private\s+)?(?:static\s+)?"
        r"(?:void|[A-Za-z_$][\w.$<>\[\], ?]*)\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*\{",
        prefix,
    ))
    return matches[-1].group(1) if matches else None


def _enclosing_test_method_source(source: str, offset: int) -> str:
    """Return the enclosing Java-like method body using bounded brace matching.

    This is deliberately a small source-shape helper, not a Java parser.  It is
    used only to resolve a local argv variable feeding a grounded main(...) call.
    """
    prefix = source[:offset]
    matches = list(re.finditer(
        r"(?m)^\s*(?:public\s+|protected\s+|private\s+)?(?:static\s+)?"
        r"(?:void|[A-Za-z_$][\w.$<>\[\], ?]*)\s+[A-Za-z_$][\w$]*\s*\([^;{}]*\)\s*\{",
        prefix,
    ))
    if not matches:
        return source
    start = matches[-1].start()
    open_brace = source.find("{", matches[-1].start(), offset + 1)
    if open_brace < 0:
        return source[start:]
    depth = 0
    for index in range(open_brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return source[start:]


def _resolve_java_main_argv_expression(method_source: str, call_expression: str) -> str:
    """Resolve a direct main(...) argument or a simple local String[] variable.

    Generated tests commonly use:
      String[] value = {"not-a-number"};
      App.main(value);
    The old gate inspected the method name, so this neutral-name form escaped.
    Resolve the local initializer instead; unresolved expressions remain as-is.
    """
    expression = call_expression.strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", expression):
        return expression
    variable = re.escape(expression)
    assignments = list(re.finditer(
        rf"(?:String\s*\[\s*\]|String\s+\[\s*\])\s*{variable}\s*=\s*"
        r"(?P<value>new\s+String\s*\[\s*\]\s*\{[^;]*\}|\{[^;]*\})\s*;",
        method_source,
        re.DOTALL,
    ))
    return assignments[-1].group("value").strip() if assignments else expression


def _runtime_contract_expects_numeric_input(
    required_verification: List[Dict[str, Any]],
) -> bool:
    """Whether authoritative runtime text explicitly identifies numeric input."""
    for requirement in required_verification:
        runtime_verifier = (
            requirement.get("verifier_kind") == "application_runtime"
            and requirement.get("requires_runtime_execution") is True
        )
        relevant_global_invariant = (
            requirement.get("process_boundary_authority") == "relevant_global_invariant"
        )
        if not (runtime_verifier or relevant_global_invariant):
            continue
        description = str(requirement.get("description") or "").lower()
        if re.search(r"\b(?:integer|numeric|number)\b", description):
            return True
    return False


def _terminating_case_from_argv_expression(
    cases: List[str], argv_expression: str, *, numeric_input: bool,
) -> Optional[str]:
    """Classify only from the actual argv expression, never the test method name."""
    compact = re.sub(r"\s+", "", argv_expression)
    if "missing" in cases and (
        re.fullmatch(r"newString\[0\]", compact)
        or re.fullmatch(r"newString\[\]\{\}", compact)
        or re.fullmatch(r"\{\}", compact)
    ):
        return "missing input"

    string_literals = re.findall(r'"((?:\\.|[^"\\])*)"', argv_expression)
    if "invalid" in cases and string_literals:
        lowered_literals = [literal.lower() for literal in string_literals]
        if any(
            marker in literal
            for literal in lowered_literals
            for marker in ("invalid", "malformed", "not-a-number", "not_a_number", "nonnumeric", "non-numeric")
        ):
            return "invalid input"
        if numeric_input:
            for literal in string_literals:
                try:
                    int(literal, 10)
                except ValueError:
                    return "invalid input"
    return None


def find_in_process_terminating_test_invocations(
    deps: ProcessBoundaryDeps,
    required_verification: List[Dict[str, Any]],
    worktree_path: str,
    test_files: List[str],
    application_entrypoints: List[str],
) -> List[Dict[str, Any]]:
    """Find test calls that contradict an explicit process-boundary contract.

    Source text never establishes that a path terminates. The structured
    application_runtime requirement establishes that fact and names its case;
    source inspection only connects that known case to an in-process call of a
    grounded entrypoint. Child-process tests contain no such direct call and
    therefore remain admissible.
    """
    cases = deps._required_process_terminating_cases(required_verification)
    if not cases or not application_entrypoints:
        return []
    entrypoint_names = sorted({
        name for fqcn in application_entrypoints
        for name in (fqcn, fqcn.rsplit(".", 1)[-1])
    }, key=len, reverse=True)
    call_re = re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in entrypoint_names)
        + r")\s*\.\s*main\s*\((?P<argv>[^;\n]*)\)",
    )
    numeric_input = deps._runtime_contract_expects_numeric_input(required_verification)
    findings: List[Dict[str, Any]] = []
    for filepath in sorted(test_files):
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError:
            continue
        for match in call_re.finditer(source):
            method = deps._enclosing_test_method(source, match.start())
            method_source = deps._enclosing_test_method_source(source, match.start())
            argv_expression = deps._resolve_java_main_argv_expression(
                method_source, match.group("argv"),
            )
            matched_case = deps._terminating_case_from_argv_expression(
                cases, argv_expression, numeric_input=numeric_input,
            )
            if matched_case is None:
                continue
            findings.append({
                "test_file": filepath,
                "test_method": method,
                "line": source.count("\n", 0, match.start()) + 1,
                "entrypoint_call": match.group(0),
                "terminating_case": matched_case,
            })
    return findings


def find_ungrounded_java_child_process_tests(
    deps: ProcessBoundaryDeps,
    required_verification: List[Dict[str, Any]],
    worktree_path: str,
    test_files: List[str],
    java_main_classes: Dict[str, str],
    known_files: List[str],
) -> List[Dict[str, Any]]:
    """Find invented Java child launch mechanics for a process-exit contract.

    This does not duplicate the in-process safety gate above: that gate decides
    whether a test crossed a process boundary at all.  This check verifies that
    an elected boundary uses the entrypoint and classpath Kriya can ground.
    """
    if not deps._required_process_terminating_cases(required_verification):
        return []
    if len(java_main_classes) != 1:
        return []
    entrypoint = next(iter(java_main_classes.values()))
    simple_name = entrypoint.rsplit(".", 1)[-1]
    if "pom.xml" in known_files:
        classes_dir = "target/classes"
    elif any(path.endswith(("build.gradle", "build.gradle.kts")) for path in known_files):
        classes_dir = "build/classes/java/main"
    else:
        classes_dir = ".kriya/runtime-verification/classes"
    expected = build_grounded_java_launch_command(entrypoint, ["<args>"], classes_dir)
    findings: List[Dict[str, Any]] = []
    for filepath in sorted(test_files):
        try:
            with open(
                os.path.join(worktree_path, filepath),
                encoding="utf-8", errors="replace",
            ) as handle:
                source = handle.read()
        except OSError:
            continue
        if "ProcessBuilder" not in source or ".start()" not in source:
            continue
        mentions_entrypoint = (
            f'"{entrypoint}"' in source or f'"{simple_name}"' in source
        )
        reasons = []
        if not mentions_entrypoint:
            reasons.append(f"does not launch grounded main class {entrypoint}")
        if 'System.getProperty("java.class.path")' in source:
            reasons.append("reuses the test-runner/Surefire classpath")
        if classes_dir not in source:
            reasons.append(f"does not use grounded application classpath {classes_dir}")
        if "waitFor()" not in source:
            reasons.append("does not capture the child exit code")
        if "getInputStream()" not in source and "getErrorStream()" not in source:
            reasons.append("does not capture child stdout/stderr")
        if reasons:
            findings.append({
                "test_file": filepath,
                "reasons": reasons,
                "entrypoint": entrypoint,
                "classes_dir": classes_dir,
                "expected_command": expected,
            })
    return findings


def _dependency_graph_db_path(ctx: AttemptCtx) -> str:
    return os.path.join(ctx.kernel.config.paths.memory, "dependency_graph.db")


def _extract_class_names_best_effort(deps: ProcessBoundaryDeps, db_path: str, filepath: str, content: str) -> List[str]:
    """Short-lived DependencyGraph open/query/close for one file - cheap
    (existing WAL-mode SQLite file, a handful of milliseconds) and avoids
    threading a long-lived connection through the entire per-file write loop
    below just to reuse extract_class_names(), which is an instance method,
    not static. Best-effort: any error (including a missing db_path, checked
    by the caller) degrades to no names extracted, never a false rejection."""
    try:
        DependencyGraph = deps.load_dependency_graph()
        graph = DependencyGraph(db_path)
        try:
            return graph.extract_class_names(filepath, content)
        finally:
            graph.close()
    except Exception as e:
        deps.logger.debug(f"Skipping duplicate-type check for {filepath}: {e}")
        return []


def _find_java_main_class_best_effort(deps: ProcessBoundaryDeps, db_path: str, filepath: str, content: str) -> Optional[str]:
    """Same short-lived DependencyGraph open/query/close shape as
    _extract_class_names_best_effort() above, for
    DependencyGraph.find_java_main_class() instead - see that method's own
    docstring for what it detects and why (deterministic Java entrypoint
    resolution for a no-pom.xml project, ground_java_entrypoint_in_no_build_
    file_projects()'s call site below)."""
    try:
        DependencyGraph = deps.load_dependency_graph()
        graph = DependencyGraph(db_path)
        try:
            return graph.find_java_main_class(filepath, content)
        finally:
            graph.close()
    except Exception as e:
        deps.logger.debug(f"Skipping Java entrypoint detection for {filepath}: {e}")
        return None


def _build_java_main_class_map(deps: ProcessBoundaryDeps, java_files: List[str], ctx: AttemptCtx) -> Dict[str, str]:
    """{filepath: entrypoint_class} for every .java file (already the
    established_files-inclusive union the caller passes in) that has a real
    `public static void main` - see find_java_main_class()'s own docstring.
    Reads each file's CURRENT content fresh (worktree first, workspace
    fallback - same lookup order used throughout this module), never cached
    across attempts unlike judge()'s own judgment: a retry can edit a file's
    content, and this determination must reflect what's actually on disk
    right now, not a stale snapshot from an earlier attempt."""
    db_path = deps._dependency_graph_db_path(ctx)
    result: Dict[str, str] = {}
    for filepath in java_files:
        full_path = os.path.join(ctx.worktree_path, filepath)
        if not os.path.exists(full_path):
            full_path = os.path.join(ctx.workspace_path, filepath)
        if not os.path.exists(full_path):
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:
            deps.logger.debug(f"Java entrypoint detection: couldn't read {filepath}, skipping it: {e}")
            continue
        entrypoint_class = deps._find_java_main_class_best_effort(db_path, filepath, content)
        if entrypoint_class:
            result[filepath] = entrypoint_class
    return result
