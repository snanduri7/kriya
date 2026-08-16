"""Deterministic, no-LLM pre-flight checks for known anti-patterns already
documented in skill rules (e.g. skills/ignite-java17/rules.txt's extensive
"never mix Ignite Method A and Method B" / "never leave Ignition.start()
unclosed" rules). Run once per attempt, after all files are written and known-
complete, before the expensive compile gate - catches a mistake the model
already had the rule for, without waiting through a full Maven build + JVM
boot + broker startup + a live hang/crash to discover it.

Found live, 2026-08-12 (ignite_qpid_protocol): the two Ignite checks below
reproduce real failures hit in the same session - a generated app that mixed
Ignition.start() with an XML defining IgniteSpringBean threw
"IgniteException: Ignite instance with this name has already been started"
at runtime, and a separate attempt left an Ignition.start()'d node with no
matching close(), hanging the JVM indefinitely after all application logic
had already finished and printed its correct result. Both are inherently
Java/Ignite-specific. A third check (BareVerificationMarkerCheck, added
2026-08-13) is deliberately language-generic instead - it scans every
written file regardless of extension, since the anti-pattern it catches
(Kriya's own runtime-verification marker text getting embedded unprinted)
can happen in any target language, not just Java.

A fourth check (TestContradictsVerificationMarkerCheck, added 2026-08-14)
catches a different, previously-undiscovered shape of the same underlying
theme - not a FALSE application defect this time, but a FALSE Quality Gate
failure: the model writes a test asserting a subprocess's stdout is exactly
empty, while the SAME entrypoint that subprocess invokes is (correctly)
required to print a "[VERIFICATION] PASS"/"FAIL" line per the verification
contract. Both facts can never be true together - the test is unsatisfiable
by construction, not a bug in the actual application. Found live,
2026-08-14 (a hand-run `wordcount.py` demo goal, not the eval harness): the
generated app worked correctly when run manually; the retry loop spent its
entire budget chasing a test whose own assertion contradicted a requirement
the same completion had already (correctly) satisfied elsewhere.

Best-effort by design, matching this project's own established philosophy for
this class of check (see kriya/workflow/failure_grounding.py's
extract_implicated_files() docstring): a false positive is low-cost since it's
just one more retry cycle, not a hard block.
"""
import os
import re
from typing import Dict, Iterable, Optional


class StaticCheck:
    """One deterministic anti-pattern check. `name` becomes part of the
    violation message so a human reading gate_outcomes/traces.db can tell
    which check fired."""

    name = "static_check"

    def check(self, files: Dict[str, str]) -> Optional[str]:
        raise NotImplementedError


class IgniteMethodMixingCheck(StaticCheck):
    name = "ignite_method_mixing"

    def check(self, files: Dict[str, str]) -> Optional[str]:
        direct_start_files = [f for f, c in files.items() if f.endswith(".java") and "Ignition.start(" in c]
        spring_bean_files = [f for f, c in files.items() if f.endswith(".xml") and "IgniteSpringBean" in c]

        if direct_start_files and spring_bean_files:
            return (
                f"{', '.join(sorted(direct_start_files))} calls Ignition.start(...) directly (Method A) "
                f"while {', '.join(sorted(spring_bean_files))} also defines an IgniteSpringBean (Method B). "
                "Mixing these two Ignite startup mechanisms in the same app throws "
                "'IgniteException: Ignite instance with this name has already been started' at "
                "runtime. Use exactly ONE: either Ignition.start(...) directly with a plain "
                "IgniteConfiguration bean in the XML (no IgniteSpringBean), or load the XML via "
                "ClassPathXmlApplicationContext and retrieve the already-started instance with "
                "context.getBean(...) - never call Ignition.start() at all under that approach."
            )
        return None


class BareVerificationMarkerCheck(StaticCheck):
    """Catches Kriya's own Verification Contract instruction
    (retry_prompts.py's VERIFICATION_CONTRACT_HEADER, which asks the model to
    make its entrypoint PRINT an exact "[VERIFICATION] PASS"/"[VERIFICATION]
    FAIL: ..." verdict line) getting written into the source as bare,
    unquoted, un-printed text instead of an actual print/output call.

    Found live, 2026-08-13 (python_greeter, reproduced identically across two
    separate eval-harness runs): qwen3-coder:30b's first-draft generation
    wrote the literal, unquoted tokens `[VERIFICATION] PASS` as a standalone
    line - genuinely invalid Python (a syntax error), but nothing guarantees
    the same mistake is a compile-time error in every target language; a
    language where a bare identifier-like expression is legal would let this
    slip through as a silent no-op that never actually prints the verdict.
    This check is language-generic by design (scans every written file, not
    just one extension) since the marker text and the mistake shape are
    identical regardless of target language - unlike the Ignite checks
    above, which are inherently Java/XML-specific to the frameworks
    involved.

    Deliberately narrow: only flags the exact confirmed shape (the marker
    starting a stripped line completely bare, no leading quote or print-call
    prefix). A correctly quoted-but-never-printed marker (e.g. a bare
    "[VERIFICATION] PASS" string expression sitting on its own, syntactically
    valid in some languages but still never reaching real output) is a
    plausible sibling mistake but not yet a confirmed live incident - not
    covered here, matching this module's own established practice of one
    check per concretely-observed failure rather than speculative coverage."""

    name = "bare_verification_marker"

    _BARE_MARKER_RE = re.compile(r"^\[VERIFICATION\]\s+(PASS|FAIL)\b")

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            for line_no, line in enumerate(content.splitlines(), start=1):
                stripped = line.strip()
                if self._BARE_MARKER_RE.match(stripped):
                    return (
                        f"{filepath} line {line_no} contains the literal, unquoted text "
                        f"'{stripped}' sitting on its own. The verification contract's verdict "
                        "line must be the argument to an actual print/console-output call in "
                        "this file's language (e.g. Python's print(...), Java's "
                        "System.out.println(...), Ruby's puts) - not a bare line of source text. "
                        "As written, this either fails to compile, or compiles but never actually "
                        "prints anything, so the verdict will never reach the program's real "
                        "output. Wrap it in the correct print/output call for this file's "
                        "language."
                    )
        return None


class IgniteUnclosedResourceCheck(StaticCheck):
    name = "ignite_unclosed_resource"

    _TRY_WITH_RESOURCES_RE = re.compile(r"try\s*\([^)]*Ignition\.start\(")

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not filepath.endswith(".java"):
                continue
            if "Ignition.start(" not in content:
                continue
            if ".close(" in content:
                continue
            if self._TRY_WITH_RESOURCES_RE.search(content):
                continue
            return (
                f"{filepath} calls Ignition.start(...) but never calls .close() on the result, "
                "and doesn't use try-with-resources either. Background discovery/communication "
                "threads keep the JVM alive indefinitely after all application logic has already "
                "finished - the process hangs and gets force-killed after timing out, even though "
                "every expected output already printed correctly. Close the Ignite instance "
                "explicitly (e.g. in the same finally block that shuts down any embedded broker), "
                "or wrap the assignment in try-with-resources."
            )
        return None


class TestContradictsVerificationMarkerCheck(StaticCheck):
    """Catches a test file asserting a subprocess's stdout is EXACTLY empty
    when the specific script it invokes is itself required (by the goal's
    own verification contract) to print a "[VERIFICATION] PASS"/"FAIL" line
    on every run - the two facts are structurally incompatible, so the test
    can never pass regardless of how correct the invoked script actually is.

    Found live, 2026-08-14 (a hand-run wordcount.py demo goal): `wordcount.py`
    worked correctly when run manually - correct word counts, clean exit,
    ending with `print("[VERIFICATION] PASS")` per the standing convention.
    The model's OWN generated `test_empty_file` asserted
    `result.stdout.strip() == ""` for that same script - impossible once the
    verification line is always printed, regardless of input. The retry loop
    spent its entire budget (multiple targeted + full-set attempts, plus an
    escalation) chasing this before running out, never touching the real
    contradiction: two artifacts written in the same completion, one
    requiring output the other asserts can't exist. Distinct from
    BareVerificationMarkerCheck above (that catches the marker never
    reaching real output at all) - here the marker prints correctly, and a
    SEPARATE, sibling file's test is what's wrong.

    Deliberately narrow to the exact confirmed shape, matching this module's
    own established practice (see BareVerificationMarkerCheck's own
    docstring): Python only (`subprocess.run` is the specific idiom that
    produced the real incident - a Java/JUnit or Ruby/RSpec equivalent would
    need its own detection, not guessed at here), and only an EXACT-equality
    assertion against an empty string literal (`.stdout(.strip())? == ""` or
    `''`) - unambiguously wrong whenever it fires, since a script required to
    print the marker can never produce fully empty stdout. A looser
    "asserts some non-marker-inclusive literal" version was considered and
    rejected: distinguishing a genuinely wrong assertion from one that
    legitimately checks only a substring, or invokes a DIFFERENT script with
    no verification-contract obligation of its own, isn't reliable from text
    alone - the empty-string case is the one shape with no such ambiguity.

    Cross-references the SPECIFIC invoked script (extracted from the
    subprocess.run(...) call itself, not just "some file in this batch
    mentions the marker somewhere") against every already-written file
    sharing that basename, so a file that merely happens to also be in this
    batch but isn't the one actually being tested never triggers a false
    match."""

    name = "test_contradicts_verification_marker"

    _SUBPROCESS_INVOKE_RE = re.compile(
        r"subprocess\.run\(\s*\[\s*(?:sys\.executable|[\"']python3?[\"'])\s*,\s*[\"']([^\"']+)[\"']"
    )
    _STDOUT_EXACT_EMPTY_RE = re.compile(r"\.stdout(?:\.strip\(\))?\s*==\s*(?:\"\"|'')")

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not filepath.endswith(".py"):
                continue
            if not self._STDOUT_EXACT_EMPTY_RE.search(content):
                continue
            for m in self._SUBPROCESS_INVOKE_RE.finditer(content):
                invoked = m.group(1)
                invoked_contents = [c for f, c in files.items() if os.path.basename(f) == invoked]
                if any("[VERIFICATION]" in c for c in invoked_contents):
                    return (
                        f"{filepath} asserts a subprocess's stdout is exactly empty "
                        f"(`.stdout ... == \"\"`), but it invokes {invoked}, which is required to "
                        "print a '[VERIFICATION] PASS'/'[VERIFICATION] FAIL: ...' line on every run "
                        "per the goal's verification contract - stdout can never be exactly empty. "
                        f"This assertion can never pass regardless of whether {invoked} is correct. "
                        "Assert on the actual expected content instead (e.g. that the verification "
                        "marker is present, or the specific output besides it), not exact emptiness."
                    )
        return None


STATIC_CHECKS = [
    IgniteMethodMixingCheck(),
    IgniteUnclosedResourceCheck(),
    BareVerificationMarkerCheck(),
    TestContradictsVerificationMarkerCheck(),
]


def run_static_checks(worktree_path: str, all_files_written: Iterable[str]) -> Optional[str]:
    """Reads every known-written file back from the worktree (nothing keeps a
    Dict[str,str] of full content in memory this late in run_attempt() - it's
    read-and-discarded per-file during the earlier write loop) and runs every
    registered check against the combined set. Returns the first violation
    message found, or None. Best-effort: a file that can't be read is silently
    skipped rather than failing the whole check."""
    files: Dict[str, str] = {}
    for filepath in all_files_written:
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                files[filepath] = fh.read()
        except Exception:
            continue

    for check in STATIC_CHECKS:
        violation = check.check(files)
        if violation:
            return f"[{check.name}] {violation}"
    return None
