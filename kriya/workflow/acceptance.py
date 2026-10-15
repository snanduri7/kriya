"""Deterministic acceptance assertions above compiler/test exit codes."""

import re
from typing import Any, Dict, Optional

from kriya.workflow.generation_manifest import FileRole, classify_file_role

# Annotation-only names, imported at runtime so typing.get_type_hints()
# resolves (PRD-001); no cycle.
from kriya.workflow.plan_schema import EngineeringPlan, Subtask

_EXPLICIT_TEST_REQUEST_RE = re.compile(
    r"\b(?:include|add|write|create|provide|with|run)\s+(?:appropriate\s+)?"
    r"(?:"
    # A qualified test - "unit test(s)", "integration tests",
    # "automated test" - singular or plural, always a genuine request.
    r"(?:unit\s+|integration\s+|automated\s+)tests?\b"
    # Bare PLURAL "tests" always counts - "write tests", "include tests" -
    # nothing after it needs checking, plural is unambiguous.
    r"|tests\b"
    # Bare SINGULAR "test" only counts when followed by a noun that
    # itself names a real test-suite artifact (suite/case/class/module/
    # file) - "test suite", "test case".
    r"|test\s+(?:suite|case|cases|class|classes|module|modules|file|files)\b"
    r")(?!\s+where\s+appropriate)",
    # Structural fix, 2026-08-24, replacing a growing per-phrase exclusion
    # list: bare singular "test" used as an attributive adjective before an
    # arbitrary noun ("test values", "test data", "test logic", "test
    # functionality", "test code", ...) describes sample/demo input or ad
    # hoc verification code, not a request for a real test suite - found
    # live 3 times on the same project (2026-08-21 "test values", 2026-08-24
    # "test logic", 2026-08-24 again "test functionality" minutes after the
    # "test logic" fix shipped), always in PLANNER-GENERATED subtask/goal
    # text, never anything the human actually asked for. Each prior fix
    # added one more excluded noun to a blocklist and predicted, correctly,
    # that another paraphrase would defeat it - "functionality" wasn't on
    # the list. Rather than adding a fourth word, this closes the whole
    # class structurally: singular "test" only ever counts when qualified
    # (unit/integration/automated before it, or suite/case/class/module/
    # file after it) or when plural - "test <anything else>" can no longer
    # match regardless of which noun follows, so no future paraphrase of
    # this exact shape can reopen the same bug.
    re.IGNORECASE,
)
_ZERO_TEST_PATTERNS = (
    re.compile(r"collected\s+0\s+items?", re.IGNORECASE),
    re.compile(r"tests?\s+run:\s*0\b", re.IGNORECASE),
    re.compile(r"\bno\s+tests?\s+to\s+run\b", re.IGNORECASE),
    re.compile(r"\bno\s+tests?\s+(?:were\s+)?(?:collected|executed|found|ran)\b", re.IGNORECASE),
    re.compile(r"\bno\s+tests?\s+matching\s+pattern\b", re.IGNORECASE),
    re.compile(r"\b0\s+tests?\s+(?:passed|executed|run)\b", re.IGNORECASE),
    re.compile(r"\btest\b.*\bno[- ]source\b", re.IGNORECASE),
)
# A runtime-verification command whose target simply doesn't exist - found
# live, 2026-08-21 (ignite_qpid_protocol milestone 2/4): RunVerifierAgent.
# judge() inferred `java -cp . ProtocolParserTest` (a test class that was
# never generated), and that judgment stays cached for the rest of the run
# (kriya/workflow/attempt.py's state.cached_run_verification_judgment) -
# already a documented, not-yet-fixed gap (SME review finding #8: "a stale
# inferred entrypoint path from attempt 1 can persist even after the file
# layout changes"). Confirmed live: attempt 5 added a main() method directly
# to ProtocolParser.java specifically to satisfy verification, but the
# cached command still tried to run the never-existent ProtocolParserTest -
# identical failure, 6 attempts straight, budget exhausted. Deliberately
# broad, not Java-only (Python/Ruby covered too) - the cost of a false
# positive here is one extra judge() call, not a wrongly-rejected fix, so
# erring toward re-checking more often than strictly necessary is the right
# tradeoff (contrast _EXPLICIT_TEST_REQUEST_RE above, where a false positive
# actually burns a retry budget on an unwinnable gate).
_MISSING_ENTRYPOINT_PATTERNS = (
    re.compile(r"could not find or load main class", re.IGNORECASE),
    re.compile(r"\bClassNotFoundException\b"),
    re.compile(r"\bNoClassDefFoundError\b"),
    re.compile(r"\bModuleNotFoundError\b"),
    re.compile(r"\bNo module named\b", re.IGNORECASE),
    re.compile(r"\bcannot load such file\b", re.IGNORECASE),
    re.compile(r"\bLoadError\b"),
)
# Deterministic, bounded runtime-execution INTENT detector (Production
# Validation P3, 2026-09-07). The previous version matched any bare
# occurrence of run/start/launch/execute/print/get/put/etc ANYWHERE in the
# goal text, no context or negation awareness at all. Found live, checking
# real frozen production-validation goal texts directly (not assumed):
# P2's own goal.md false-positived on ordinary prose ("...which employees
# get their fields changed and get saved..."); P3's own goal.md false-
# positived on BOTH ordinary prose ("before any location check would even
# run") AND, worse, its own explicit denial ("No live application run is
# required to verify this change") - the sentence REFUSING runtime
# verification was the literal text that triggered Kriya's demand for it.
# P1's goal.md had zero matches and passed cleanly - the only reason this
# defect went unnoticed for two full runs of this track.
#
# Two-stage, bounded (not general NLP) redesign:
#  1. An explicit negative runtime-verification statement ("no ... run ...
#     is required", "do not run/start/launch/execute", "execution is not
#     required") is checked FIRST and wins unconditionally - matches this
#     codebase's own repeated "explicit goal text is ground truth, never
#     overridden by a generic-looking match" convention (see
#     MISSING_GROUNDED_PRODUCTION_ARTIFACT's own "do not infer... solely
#     because" repair guidance for the same principle applied elsewhere).
#     Scoped to the whole goal text, not locally to the matched clause -
#     deliberately simple: this function does not attempt to determine
#     WHICH runtime action a negation refers to when a goal mentions
#     several; an authoritative requirement that both denies one runtime
#     action and genuinely requires a different one is expected to state
#     the positive requirement unambiguously enough to be a real, separate
#     concern for whichever caller needs that distinction, not something
#     this bounded detector untangles.
#  2. Otherwise, a positive match requires CONTEXT, never an isolated verb:
#     either a runtime-action verb next to a runtime noun (run/start/
#     launch/execute/connect/send/receive/publish/consume within a few
#     words of application/service/process/program/server), or explicit
#     process-output/exit-code language (exit code/status, "exits
#     non-zero", stdout/stderr, process exit/output) - inherently specific
#     enough to stand alone with no extra context needed. Bare "get"/
#     "put"/"run"/"print" with none of this context are no longer
#     sufficient evidence - every real false positive found above lacked
#     this context entirely.
_RUNTIME_VERIFICATION_NEGATED_RE = re.compile(
    r"\bno\b(?:\s+\S+){0,6}?\s+(?:run|execution|launch(?:ing)?|start(?:up)?)\b"
    r"(?:\s+\S+){0,6}?\s+(?:is|are)\s+(?:required|needed|necessary)\b"
    r"|\b(?:do\s+not|does\s+not|don'?t|doesn'?t|never)\b(?:\s+\S+){0,3}?"
    r"\s+(?:run|start|launch|execute)\b"
    r"|\b(?:execution|running|startup|launching)\b(?:\s+\S+){0,4}?"
    r"\s+(?:is|are|was|were)\s+not\s+(?:required|needed|necessary)\b",
    re.IGNORECASE,
)
_RUNTIME_EXECUTION_CONTEXT_RE = re.compile(
    r"\b(?:run|start|launch|execute|connect|send|receive|publish|consume)\b"
    r"(?:\s+\S+){0,3}?\s+(?:the\s+|a\s+|an\s+)?(?:application|app|service|program|server|process)\b"
    r"|\b(?:application|app|service|program|process)\b(?:\s+\S+){0,3}?"
    r"\s+(?:is\s+|the\s+)?(?:launched|started|executed|running)\b"
    r"|\bexit\s*(?:code|status)\b"
    r"|\bexits?\s+(?:non-?zero|with\s+(?:code|status)\s+\d+|normally|cleanly)\b"
    r"|\bprocess\s+(?:exit|output|stdout|stderr)\b"
    r"|\b(?:stdout|stderr)\b"
    r"|\bshut\s*down\b",
    re.IGNORECASE,
)


def goal_explicitly_requires_tests(goal: str) -> bool:
    return bool(_EXPLICIT_TEST_REQUEST_RE.search(goal or ""))


def _subtask_owns_tests(subtask: "Subtask") -> bool:
    return (
        any(classify_file_role(pf.path) is FileRole.TEST for pf in subtask.planned_files)
        or goal_explicitly_requires_tests(subtask.description)
    )


def subtask_owns_test_obligation(
    overall_goal: str,
    plan: Optional["EngineeringPlan"],
    current_subtask_id: Optional[str],
) -> bool:
    """Whether the CURRENTLY EXECUTING bounded subtask must itself produce a
    runnable test module to satisfy the goal's own test requirement -
    obligation/ownership-aware for a structured plan, never a blind scan over
    the FULL goal text (which, since the authority-isolation fix (§11.13),
    legitimately contains the ENTIRE raw top-level goal on EVERY subtask's
    own ctx.goal, not just the current one's own scope).

    PRV-11 (2026-08-30, live incident): `goal_explicitly_requires_tests(ctx.goal)`
    used to be called directly and unconditionally on every subtask attempt.
    Before the authority-isolation fix, this happened to be harmless for a
    subtask whose own Planner-authored description never mentioned tests
    (ctx.goal was scoped to just that text). Once ctx.goal started ALSO
    carrying the real top-level goal (correct and necessary for
    SpecCompliance/the Developer - see contracts.py's own
    AUTHORITATIVE_GOAL_SECTION_HEADER docstring), this ownership-blind check
    started firing for ANY subtask whenever the goal mentioned tests
    ANYWHERE in the plan - live case: s1 (Customer.java, zero test
    ownership) kept failing TEST ACCEPTANCE FAILURE because s3 (which
    genuinely owns "add unit tests...", with its own planned test file) was
    a LATER, not-yet-reached subtask. In MA8 terms: a FUTURE_ORDERED
    obligation was being treated as CURRENT merely because ITS OWN TEXT
    happened to be visible in ctx.goal - not because plan-scope recovery (or
    anything else) actually reassigned ownership.

    ctx.goal itself is deliberately NOT narrowed back to fix this - the
    authority-isolation fix stays exactly as it is. This function fixes the
    ACTUAL bug: a consumer that was using raw goal text as a proxy for
    subtask ownership, when real plan/ownership state was available and
    should have been consulted instead.

    Rule: the overall goal must require tests at all (unchanged legacy
    signal, still text-based - there is no narrower authoritative source for
    "does this GOAL want tests" than the goal's own words). If a structured
    plan and a current subtask id are both available, defer to whichever
    OTHER subtask already owns test-writing (a planned file that classifies
    as FileRole.TEST, or its own description explicitly requesting tests) -
    when one exists, this subtask's own obligation is FUTURE_ORDERED/
    PAST_ORDERED relative to it, not CURRENT, regardless of whether this
    subtask was just reopened by plan-scope recovery (recovery never
    reassigns verification ownership by itself - see
    revise_plan_for_grounded_scope_owner's own docstring). When NO other
    subtask owns it (including the single-subtask-plan case, and the
    legacy/unpartitioned pipeline where no plan exists at all), the current
    subtask remains responsible - unchanged, backward-compatible legacy
    behavior."""
    if not goal_explicitly_requires_tests(overall_goal):
        return False
    if plan is None or not current_subtask_id:
        return True
    other_subtask_owns_it = any(
        st.id != current_subtask_id and _subtask_owns_tests(st)
        for st in plan.subtasks
    )
    return not other_subtask_owns_it


# GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001 (owner run 20261015T075602-2b5a0ad7): the goal "Create a small Java 17 Maven application named hello-kriya that prints 'Hello from
# Kriya' and includes a unit test. Make the application runnable." was read as NOT requiring runtime behavior, so plan normalization removed the runtime verifier and the run
# "succeeded" without ever executing the application. Measured: of nine explicit runtime statements (prints / outputs / displays / should start and respond / should exit and
# print / must execute / runnable and produce ...) only "Run the application and confirm ..." matched. The detector had no OUTPUT-INTENT or EXECUTION-MODALITY clause, only
# "run/start/launch ... <noun>" and exit-code language. Three explicit clauses are added below, each needing an executable SUBJECT (application, program, service, command,
# ...) in the same sentence as the observable behavior - never an isolated verb, and never "runnable" alone (build/package readiness; "do not infer runtime execution merely
# because a project is executable in principle"). They run per sentence so a documentation/comment sentence ("add a comment noting that the program will print ...") or a repair
# sentence describing an existing defect ("fix the application that prints the wrong total") does not turn into a runtime obligation. Negation stays first and whole-goal.
_EXECUTABLE_SUBJECT = r"(?:application|app|program|executable|binary|service|server|command|cli|script|tool|utility)(?!\s+(?:class|interface|pattern|layer|method|function|type|object|module|package|component|handler|trait|enum)\b)"   # "a Server class", "the Command interface" name source code, not a program that runs
_WORDS = r"(?:\s+[^\s;!?]+)"   # a token may hold an inner dot ("main.py", "17.0.1"): sentences are already split on ". "
_OUTPUT_INTENT_RE = re.compile(
    rf"\b{_EXECUTABLE_SUBJECT}\b{_WORDS}{{0,10}}?\s+"
    r"(?P<verb>(?:prints|outputs|displays|emits|echoes)\b|responds\s+with(?=\s+(?:[\"'`]|\d|http\b|status\b))"
    r"|(?:should|must|will|shall|can|to)\s+(?:also\s+)?(?:(?:print|output|display|emit|echo)\b|respond\s+with(?=\s+(?:[\"'`]|\d|http\b|status\b)))"
    r"|writes\s+(?:\S+\s+){0,3}?to\s+(?:stdout|standard\s+output|the\s+console)\b)",
    re.IGNORECASE,
)
_EXECUTION_MODALITY_RE = re.compile(
    rf"\b{_EXECUTABLE_SUBJECT}\b{_WORDS}{{0,4}}?\s+(?:should|must|will|shall|needs?\s+to|has\s+to|can)\s+(?:\w+\s+)?"
    r"(?:start|execute|run|launch|exit|terminate)\b"
    # the verb must END the clause or be followed by an outcome word: "should run on Java 17", "run on port 8080", "execute handlers asynchronously" describe the design, not an
    # observable execution requirement (independent review of b8cc982)
    r"(?=\s*(?:$|[.,;:!?)]|\s+(?:and|then|successfully|correctly|cleanly|normally|without|when|if|before|after|within|with\s+(?:exit|code|status)|to\s+completion)\b))",
    re.IGNORECASE,
)
_RUNNABLE_PRODUCES_RE = re.compile(
    r"\brunnable\b(?:\s+[^\s.;!?]+){0,3}?\s+(?:and|,)\s+(?:also\s+)?(?:produces?|prints?|outputs?|displays?|emits?)\b",
    re.IGNORECASE,
)
# A documentation sentence ahead of the subject ("document how ...", "add a comment noting that the program will print ...") is about text, not about the artifact running.
_DOCUMENTATION_FRAMING_RE = re.compile(
    r"\b(?:comment|comments|javadoc|docstring|readme|documentation|document|describe|describing|explain|explaining|note|noting"
    # a test that asserts the behavior is a test to write, not a run to perform ("write a unit test asserting that the program prints x")
    r"|asserting|asserts|assertion|assertions)\b", re.IGNORECASE)
# "how a user could run the application": instructions for a human, not an execution requirement.
_HOW_TO_RUN_RE = re.compile(
    r"\bhow\s+(?:to|a\s+user|users|one|you|someone)\b[^.;!?]*", re.IGNORECASE)
# A repair/refactor sentence's relative clause ("fix the application that prints the wrong total") describes existing behavior, not a requirement.
_REPAIR_LEAD_RE = re.compile(r"^\W*(?:fix|repair|debug|refactor|rename|remove|delete|investigate|diagnose)\b", re.IGNORECASE)
_RELATIVE_CLAUSE_RE = re.compile(r"\b(?:that|which)\s+(?:prints|outputs|displays|emits|echoes)\b", re.IGNORECASE)
# a hard-wrapped line is NOT a sentence boundary (the owner goal wraps after "that"); punctuation, a blank line and a list bullet are
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n\s*\n|\n\s*(?:[-*\u2022]|\d+[.)])\s+")


def _sentence_requires_runtime_behavior(sentence: str) -> bool:
    if _RUNTIME_EXECUTION_CONTEXT_RE.search(_HOW_TO_RUN_RE.sub(" ", sentence)):
        return True
    repair_sentence = bool(_REPAIR_LEAD_RE.search(sentence))
    for pattern in (_OUTPUT_INTENT_RE, _EXECUTION_MODALITY_RE):
        for match in pattern.finditer(sentence):
            if _DOCUMENTATION_FRAMING_RE.search(sentence[:match.start()]):
                continue
            if repair_sentence and _RELATIVE_CLAUSE_RE.search(match.group(0)):
                continue
            return True
    runnable = _RUNNABLE_PRODUCES_RE.search(sentence)
    return bool(runnable) and not _DOCUMENTATION_FRAMING_RE.search(sentence[:runnable.start()])


def goal_requires_runtime_behavior(goal: str) -> bool:
    """Bounded deterministic signal that the goal explicitly requires observable
    behavior that only EXECUTING the resulting artifact can show: output, process
    exit, service startup/response, or an explicit execution requirement. Explicit
    negation of runtime verification wins for the whole goal (live false positives
    P2, P3); otherwise any sentence that states such behavior about an executable
    subject requires it. See the comments above the patterns for what is, and
    deliberately is not, treated as a requirement (``runnable`` alone is not)."""
    text = goal or ""
    if _RUNTIME_VERIFICATION_NEGATED_RE.search(text):
        return False
    return any(_sentence_requires_runtime_behavior(s) for s in _SENTENCE_SPLIT_RE.split(text) if s.strip())


def output_confirms_nonzero_test_execution(output: str) -> bool:
    """Fail only on known zero-test evidence; unfamiliar runners remain allowed."""
    return not any(pattern.search(output or "") for pattern in _ZERO_TEST_PATTERNS)


def run_command_targets_missing_entrypoint(output: str) -> bool:
    """True when captured runtime-verification output shows the run command
    itself targeted a class/module/file that doesn't exist - as opposed to a
    genuine application-logic failure. The caller's job (kriya/workflow/
    attempt.py) is to invalidate a cached RunVerifierAgent.judge() judgment
    when this fires, so the NEXT attempt re-infers a fresh command against
    the CURRENT file layout instead of repeating the same broken one."""
    return any(pattern.search(output or "") for pattern in _MISSING_ENTRYPOINT_PATTERNS)


RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE = "RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE"
MAVEN_PLUGIN_UNAVAILABLE = "MAVEN_PLUGIN_UNAVAILABLE"
RUNTIME_PREREQUISITE_BUILD_FAILED = "RUNTIME_PREREQUISITE_BUILD_FAILED"
# D4: runtime entrypoint failures, classified by who chose the entrypoint and
# what the freshly built candidate holds - never "missing main class =
# infrastructure" alone.
CANDIDATE_RUNTIME_ENTRYPOINT_INVALID = "CANDIDATE_RUNTIME_ENTRYPOINT_INVALID"  # A: candidate defect
RUNTIME_COMMAND_ENTRYPOINT_INVALID = "RUNTIME_COMMAND_ENTRYPOINT_INVALID"  # B: Kriya's command
RUNTIME_ENTRYPOINT_NOT_LOADABLE = "RUNTIME_ENTRYPOINT_NOT_LOADABLE"  # C: built, still not loaded
RUNTIME_ENTRYPOINT_NOT_BUILT = "RUNTIME_ENTRYPOINT_NOT_BUILT"  # D: source declares it, build lacks it
_CLASS_NOT_FOUND = re.compile(r"(?:ClassNotFoundException|NoClassDefFoundError|could not find or load main class)"
                              r":?\s+([\w.$/]+)", re.IGNORECASE)


def classify_runtime_entrypoint(diagnosis: Optional[Dict[str, Any]], output: str) -> Optional[str]:
    """D4: the owner of a runtime entrypoint failure, from the validator's
    deterministic diagnosis (PolymorphicValidator.diagnose_runtime_entrypoint).

    Only for a launch failure (missing-entrypoint output); only when the class
    the JVM reports missing is the diagnosed effective entrypoint (the output
    confirms the diagnosis, never replaces it); UNKNOWN provenance -> None,
    the caller's existing classification stands.
      A  candidate build configuration selects a class no current source
         declares and the fresh build did not produce -> candidate defect
      B  Kriya's own command selects such a class -> verifier infrastructure
      C  declared and built, yet not loaded -> runtime/classpath infrastructure
      D  declared, but the prerequisite build did not produce it -> build
         diagnosis (infrastructure, never a guessed candidate typo)"""
    if not diagnosis or not run_command_targets_missing_entrypoint(output):
        return None
    effective = diagnosis.get("effective_entrypoint")
    provenance = diagnosis.get("effective_entrypoint_provenance")
    if not effective or provenance not in ("KRIYA_COMMAND", "CANDIDATE_BUILD_CONFIG"):
        return None
    reported = {name.replace("/", ".") for name in _CLASS_NOT_FOUND.findall(output or "")}
    if effective not in reported:
        return None
    declared, built = diagnosis.get("source_declares_entrypoint"), diagnosis.get("compiled_artifact_exists")
    if built:
        return RUNTIME_ENTRYPOINT_NOT_LOADABLE
    if declared:
        return RUNTIME_ENTRYPOINT_NOT_BUILT
    return (CANDIDATE_RUNTIME_ENTRYPOINT_INVALID if provenance == "CANDIDATE_BUILD_CONFIG"
            else RUNTIME_COMMAND_ENTRYPOINT_INVALID)
_MAVEN_PLUGIN_PREFIX_UNRESOLVED = re.compile(r"No plugin found for prefix '[^']+'")


def runtime_verification_infrastructure_reason(result: Dict[str, Any]) -> Optional[str]:
    """Identify a verifier launch failure before behavioral grading.

    These signals describe Kriya's command/execution environment, not the
    candidate's behavior. They must never be localized to an application
    source file or sent to the Developer repair loop.
    """
    output = result.get("output") or ""
    if run_command_targets_missing_entrypoint(output):
        return "runtime command could not load its configured application entrypoint"
    # D2: a Maven artifact/plugin Kriya's own runtime command needs is a
    # verification prerequisite, never an application defect to repair.
    if "MAVEN_ACQUISITION_INCOMPLETE:" in output or "GRADLE_ACQUISITION_INCOMPLETE:" in output:
        return (f"{RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE}: a Maven/Gradle dependency/plugin the runtime "
                "command needs could not be acquired within one bounded, registry-scoped acquisition")
    if _MAVEN_PLUGIN_PREFIX_UNRESOLVED.search(output):
        return (f"{MAVEN_PLUGIN_UNAVAILABLE}: the runtime command names a Maven plugin prefix that could not be "
                "resolved")
    # D3: the runtime gate rebuilds the application from the current source
    # before running it; when that build fails nothing ran, so there is no
    # runtime behavior to grade or repair (the compile gate owns compile
    # failures of the candidate itself).
    if result.get("prerequisite_failed") or output.startswith("RUNTIME_PREREQUISITE_FAILED:"):
        return (f"{RUNTIME_PREREQUISITE_BUILD_FAILED}: the application could not be built from the current source "
                "before runtime verification, so nothing was run")
    for step in result.get("steps") or []:
        if step.get("exit_code") is None and not step.get("timed_out"):
            return "runtime verification command could not be executed"
    return None


def runtime_application_step_started(result: Dict[str, Any]) -> bool:
    """Whether a nonzero final application result is behavioral evidence.

    Every setup command must have succeeded, the final process must have
    launched and exited normally (rather than timing out), and its output
    must not carry a known entrypoint/classpath launch failure. Only then may
    semantic grading recognize an expected invalid-input rejection.
    """
    if runtime_verification_infrastructure_reason(result) is not None:
        return False
    steps = result.get("steps") or []
    if not steps:
        return False
    if any(step.get("exit_code") != 0 or step.get("timed_out") for step in steps[:-1]):
        return False
    final = steps[-1]
    return final.get("exit_code") is not None and not final.get("timed_out")
