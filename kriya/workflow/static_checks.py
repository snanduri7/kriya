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


STATIC_CHECKS = [IgniteMethodMixingCheck(), IgniteUnclosedResourceCheck(), BareVerificationMarkerCheck()]


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
