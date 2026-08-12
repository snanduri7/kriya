"""Deterministic, no-LLM pre-flight checks for known anti-patterns already
documented in skill rules (e.g. skills/ignite-java17/rules.txt's extensive
"never mix Ignite Method A and Method B" / "never leave Ignition.start()
unclosed" rules). Run once per attempt, after all files are written and known-
complete, before the expensive compile gate - catches a mistake the model
already had the rule for, without waiting through a full Maven build + JVM
boot + broker startup + a live hang/crash to discover it.

Found live, 2026-08-12 (ignite_qpid_protocol): both checks below reproduce
real failures hit in the same session - a generated app that mixed
Ignition.start() with an XML defining IgniteSpringBean threw
"IgniteException: Ignite instance with this name has already been started"
at runtime, and a separate attempt left an Ignition.start()'d node with no
matching close(), hanging the JVM indefinitely after all application logic
had already finished and printed its correct result.

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


STATIC_CHECKS = [IgniteMethodMixingCheck(), IgniteUnclosedResourceCheck()]


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
