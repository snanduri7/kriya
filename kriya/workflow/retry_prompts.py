"""Standing invariant checklists (ecosystem preservation, resource lifecycle) and the three retry-prompt builders (targeted, full-set, missing-files) for the Developer retry loop. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

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

logger = logging.getLogger(__name__)


ECOSYSTEM_INVARIANT_HEADER = (
    "\n\n=== Ecosystem Preservation (required) ===\n"
    "Preserve the language, framework, build system, project directory layout, "
    "dependency manager, and configuration file formats that the goal or the "
    "existing repository already establishes. Do not substitute a different "
    "ecosystem's conventions for the one actually requested - for example, do "
    "not write Java/Spring/Maven code for a Python/Django goal, and do not "
    "invent a Maven-style src/main/src/test directory layout for a goal that "
    "only ever asked for a flat layout. This applies even if you are more "
    "confident writing a different stack's idioms for the same kind of task. "
    "The only exceptions: the goal explicitly asks you to migrate, port, or "
    "rewrite the project into a different stack, or the repository already "
    "contains more than one language/framework side by side (a genuine "
    "polyglot project) - in either case, follow what the goal actually asks "
    "for instead of defaulting to a single ecosystem."
)


RESOURCE_LIFECYCLE_HEADER = (
    "\n\n=== Resource Lifecycle (required) ===\n"
    "Any stateful resource you open in this goal's code - a database/cache client "
    "or connection (e.g. Ignite, JDBC, Redis), a message broker client/connection, "
    "a thread pool or executor, a network socket or server, a file handle - must be "
    "started/opened exactly ONCE for the lifetime it's actually needed, reused "
    "rather than re-opened per operation/method call, and explicitly closed/shut "
    "down exactly once when the application is done with it. Prefer your "
    "language's own scoped-resource construct (e.g. Java/Kotlin try-with-resources, "
    "Python's 'with', Go's 'defer') over a manual close call you might forget on an "
    "error path. Opening a fresh instance of the same kind of resource in a "
    "different method just to \"read back\" what an earlier method wrote is almost "
    "always wrong - reuse the SAME instance across every method/step that needs it "
    "instead of creating a new, disconnected one with no knowledge of the first. An "
    "unclosed resource with its own background threads (a cache/broker client, an "
    "executor) can leave the process alive indefinitely even after all application "
    "logic, including any output the goal expects, has already completed "
    "successfully - a silent hang with no visible error is the classic symptom of "
    "exactly this mistake."
)


# Standing convention letting Runtime Verification trust a deterministic
# marker instead of re-deriving correctness from raw captured text after the
# fact - added 2026-08-11 after RunVerifierAgent.grade() twice hallucinated a
# wrong "expected" value (independently recomputing a UTF-8 byte length as 13
# when it was actually 15) and rejected code that had been correct since the
# first attempt, even though the program's own real comparison
# (original.equals(decoded)) had already passed and printed so. The program
# has the actual values in memory, computed by real, exact, non-hallucinating
# arithmetic - Kriya should trust that computation, not ask an LLM to
# reconstruct it worse from flattened stdout. See
# kriya.workflow.verification_contract.extract_contract_verdict() for the
# deterministic scan this convention feeds - a soft, prompt-level convention
# like every other standing invariant here, not a hard requirement: if the
# marker isn't present, Runtime Verification falls through to today's
# LLM-graded behavior unchanged.
VERIFICATION_CONTRACT_HEADER = (
    "\n\n=== Verification Contract (required whenever applicable) ===\n"
    "If this goal describes observable runtime behavior with a checkable outcome "
    "(e.g. a round-trip encode/decode, a value written then read back, a computed "
    "result compared against an expected one), your entrypoint must end by printing "
    "an exact verdict line on its own: \"[VERIFICATION] PASS\" if your own code "
    "confirms - via a real comparison against real values it computed or observed, "
    "not just printing intermediate numbers and hoping they look right - that the "
    "behavior occurred correctly, or \"[VERIFICATION] FAIL: <one-sentence reason>\" "
    "otherwise. This is in addition to, not instead of, any [RESULT] output the goal "
    "itself asks for."
)


def _build_ecosystem_invariant_block(repo_model: Any) -> str:
    """A standing, always-present checklist instruction (not conditionally
    triggered) that the goal/existing repo's language and framework must be
    preserved, not silently substituted for a different one the model is more
    confident writing. Confirmed live (2026-08-04 eval harness): a Django/
    Python goal produced Java/Spring Boot code, and a plain Python goal
    invented a Maven-style src/main/src/test layout, neither goal ever having
    mentioned Java - checked traces.db's active_skills directly and confirmed
    this is NOT skill-content bias (zero skills were active for the Django
    run), so a prompting-level fix is the right lever, matching the
    already-validated pattern that passive context alone doesn't stop this
    class of drift, an explicit instruction does (see the dependency-
    preservation checklist this mirrors, `required_dependencies_prompt_block`
    below).

    When the repo analyzer already detected real frameworks (a non-empty,
    non-fresh repo), name them explicitly - the same specificity that made
    the dependency-preservation checklist effective (naming the actual
    dependencies, not just saying "preserve existing ones") over a purely
    generic instruction."""
    block = ECOSYSTEM_INVARIANT_HEADER
    detected_frameworks = getattr(repo_model, "frameworks", None) or []
    if detected_frameworks:
        block += (
            "\n\nThis repository's analyzer already detected: "
            + ", ".join(sorted(detected_frameworks))
            + ". Treat this as the established ecosystem unless the goal explicitly says otherwise."
        )
    return block


def _build_targeted_retry_prompt(
    goal: str, plan: str, error_context: str, target_files: List[str],
    all_files_written: Iterable[str], worktree_path: str, active_code_context: str,
    ecosystem_invariant_block: str = "",
    resource_lifecycle_block: str = "",
    verification_contract_block: str = "",
) -> Tuple[str, str]:
    """Builds the task description and code context for a targeted (single/few-
    file) retry: the target file(s) are framed as the fix, every other already-
    written file is included as read-only reference context (not asked to be
    regenerated) rather than omitted entirely - this is what makes soft-scoping
    real rather than just a prompt instruction with nothing behind it, and it's a
    genuine improvement over the full-set retry path, which never shows the model
    its own previous attempt's content at all, only the error text describing what
    went wrong with it."""
    target_set = set(target_files)
    target_section = ""
    reference_section = ""
    for filepath in sorted(all_files_written):
        try:
            with open(os.path.join(worktree_path, filepath), "r", encoding="utf-8", errors="replace") as fh:
                current_content = fh.read()
        except Exception as ex:
            logger.debug(f"Failed to read '{filepath}' from worktree for targeted retry context: {ex}")
            continue
        if filepath in target_set:
            target_section += f"=== File to fix: {filepath} ===\n{current_content}\n\n"
        else:
            reference_section += (
                f"=== Existing file (already correct, reference only - regenerate ONLY if your fix "
                f"genuinely requires changing it too): {filepath} ===\n{current_content}\n\n"
            )

    task_desc = f"Goal: {goal}\nPlan: {plan}"
    task_desc += ecosystem_invariant_block
    task_desc += resource_lifecycle_block
    task_desc += verification_contract_block
    task_desc += (
        f"\n\n=== Previous Error to Fix ===\n{error_context}\n\n"
        f"This is a TARGETED fix attempt. Based on the error above, the following file(s) are most "
        f"likely responsible: {', '.join(sorted(target_set))}. Focus your fix there - the rest of the "
        "codebase (given below as reference) is already correct, so only touch another file if your "
        "fix genuinely cannot be made without it."
    )
    targeted_context = active_code_context + "\n\n" + reference_section + target_section
    return task_desc, targeted_context


def _build_full_set_retry_prompt(
    goal: str, plan: str, error_context: str, required_files_prompt_block: str,
    all_files_written: Iterable[str], worktree_path: str, active_code_context: str,
    required_dependencies_prompt_block: str = "",
    ecosystem_invariant_block: str = "",
    resource_lifecycle_block: str = "",
    verification_contract_block: str = "",
) -> Tuple[str, str]:
    """Full-set retries previously never showed the model its own prior attempt's
    content at all, only the abstract error text describing what went wrong -
    the exact gap _build_targeted_retry_prompt's own docstring already names as
    something targeted retry fixes and full-set doesn't. Confirmed live as a
    real bug via the golden Ignite+Qpid use case: a "Dependency regression: ...
    you must preserve all existing dependencies" compile error doesn't tell the
    model WHAT those dependencies actually are, so a full-set regeneration
    (which naturally rewrites a file to match the current goal) kept dropping
    an existing, goal-irrelevant dependency (ignite-indexing, from an earlier
    milestone) it had no way to see - the instruction was there, the data to
    act on it wasn't. Mirrors targeted retry's approach exactly: every
    already-written file's current worktree content is included as reference,
    so the model can make an informed choice about what to keep vs. change
    rather than reconstructing everything from a clean slate.

    required_dependencies_prompt_block (a later addition): showing the prior
    attempt's content as passive reference material, on its own, was confirmed
    live NOT sufficient to stop the same dependency-drop from recurring across
    repeated full-set retries (the golden-use-case validation's own tug-of-war
    got worse, not better, across attempts) - an explicit, structured "preserve
    these" checklist mirrors the required_files_prompt_block pattern that
    already proved effective for the analogous missing-file problem."""
    reference_section = ""
    for filepath in sorted(all_files_written):
        try:
            with open(os.path.join(worktree_path, filepath), "r", encoding="utf-8", errors="replace") as fh:
                current_content = fh.read()
        except Exception as ex:
            logger.debug(f"Failed to read '{filepath}' from worktree for full-set retry context: {ex}")
            continue
        reference_section += (
            f"=== Your previous attempt's content for {filepath} (fix/rewrite as needed for the "
            f"goal and error above, but don't silently drop anything still needed) ===\n{current_content}\n\n"
        )

    task_desc = f"Goal: {goal}\nPlan: {plan}"
    task_desc += ecosystem_invariant_block
    task_desc += resource_lifecycle_block
    task_desc += verification_contract_block
    if error_context:
        task_desc += f"\n\n=== Previous Error to Fix ===\n{error_context}"
    task_desc += required_files_prompt_block
    task_desc += required_dependencies_prompt_block

    return task_desc, active_code_context + "\n\n" + reference_section


def _build_missing_files_retry_prompt(
    goal: str, plan: str, design: str, missing_files: List[str],
    all_files_written: Iterable[str], worktree_path: str, active_code_context: str,
    ecosystem_invariant_block: str = "",
    resource_lifecycle_block: str = "",
    verification_contract_block: str = "",
) -> Tuple[str, str]:
    """Builds the task description and code context for a missing-file recovery
    retry, used after an IncompleteGenerationError: the Architect's design called
    for these file(s) but the Developer never wrote them, so instead of re-describing
    a compile/test error, this asks for exactly the named file(s), with every
    already-written file included as read-only reference context so the model can
    see the existing package layout/imports it needs to match - mirroring
    _build_targeted_retry_prompt's soft-scoping approach."""
    reference_section = ""
    for filepath in sorted(all_files_written):
        try:
            with open(os.path.join(worktree_path, filepath), "r", encoding="utf-8", errors="replace") as fh:
                current_content = fh.read()
        except Exception as ex:
            logger.debug(f"Failed to read '{filepath}' from worktree for missing-files retry context: {ex}")
            continue
        reference_section += (
            f"=== Existing file (already written, reference only - do not regenerate unless "
            f"integrating the new file(s) genuinely requires a change to it too): {filepath} ===\n{current_content}\n\n"
        )

    task_desc = f"Goal: {goal}\nPlan: {plan}"
    task_desc += ecosystem_invariant_block
    task_desc += resource_lifecycle_block
    task_desc += verification_contract_block
    task_desc += (
        "\n\nThis is a MISSING-FILE recovery attempt. The Architect's design (below, in the code context) "
        f"calls for the following file(s), which were NOT generated in the previous attempt: "
        f"{', '.join(sorted(missing_files))}. Generate ONLY these missing file(s) now, in full - do not "
        f"regenerate any file already listed as existing below unless integrating the new file(s) "
        f"genuinely requires a change to it too."
    )
    targeted_context = active_code_context + "\n\n=== Architect Design ===\n" + design + "\n\n" + reference_section
    return task_desc, targeted_context
