"""Java toolchain facts and corrections for the generation workflow. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization).

TOOLCHAIN-HOST-PROBE-AUTHORITY-001 (owner ruling 2026-11-12, Scenario-A run 7): project-controlled execution is contained (mandatory), and the JDK a
candidate is compiled, tested and run under is the one decision of ``kriya.tools.toolchain_identity`` (the project's own declaration -> a versioned
image, attested at launch). Nothing in this module reads the HOST's ``java`` / ``mvn``: the host probe (``kriya.tools.validate.check_java_toolchain``)
is a host diagnostic of ``kriya doctor`` only. Run 7 measured why: the host's Homebrew Maven ran on JDK 26, every gate ran in the JDK 17 image, and
every Developer prompt said "Target JVM ... JDK 26"."""

import logging
import os
import re
from typing import Any, Iterable, List, Optional, Tuple

from kriya.capabilities import BUILD_ADAPTERS

logger = logging.getLogger(__name__)


def resolve_run_java_home_override(stack: Optional[str], goal: Optional[str]) -> Optional[str]:
    """The JDK home a run's toolchain selection is forced under, or None: ``_resolve_java_home_override(goal)`` for a Java stack with a goal, nothing
    otherwise. WAVE8 OI-97: the ONE resolution shared by the environment IDENTITY (``resume_fingerprints.toolchain_fingerprint``) and by every
    validator that EXECUTES under it (the candidate gates and the PRE-mutation baseline), so an identity can never record an override that a run did
    not execute under."""
    if goal and stack == "java":
        return _resolve_java_home_override(goal)
    return None


def _resolve_java_home_override(goal: str) -> Optional[str]:
    """The goal-stated JDK handed to the canonical toolchain owner (``resolve_toolchain_selection(java_home_override=...)``). Always None today.

    It used to be derived from the HOST: when the host's ``java`` and ``mvn`` ran different JDK majors and the goal text named the ``java`` side's
    version, that host JDK's home was returned, and under containment its ``release`` file then moved the contained image. So the same goal on the
    same repository selected a different image depending on how the operator's machine happened to be set up (TOOLCHAIN-HOST-PROBE-AUTHORITY-001).
    That host authority is removed. No host-independent authority for a goal-stated Java version exists yet (registered ownership gap
    GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001): the project's own declaration selects the toolchain, as it already did on every machine
    whose host tools agreed. The seam itself is kept: whatever is returned here still reaches the canonical owner and its conflict rules."""
    del goal
    return None


def _goal_or_repo_targets_java(goal: str, workspace_path: str) -> bool:
    """Whether there's real evidence THIS generation is actually about Java -
    either the workspace already has Java markers (an existing/extended
    project - mirrors PolymorphicValidator._detect_stack()'s own marker set,
    checked directly since no PolymorphicValidator instance exists yet this
    early), or the goal text itself names Java/the JVM/a JVM build tool
    explicitly (covers a brand-new Java goal, where no pom.xml/build.gradle
    exists on disk yet at prompt-build time - before ANY file Kriya writes).

    Gates _java_toolchain_fact() below. Confirmed live (2026-08-06 eval
    harness) as the likely real root cause of the long-open "Django goal
    produced Java/Spring code" ecosystem-drift mystery (2026-08-04, never
    fully explained): _java_toolchain_fact() used to fire unconditionally
    whenever Maven/Java were found anywhere ON THE MACHINE, regardless of
    the current goal - so on a machine with Java tooling installed (needed
    for OTHER goals), a pure Django prompt got an unrelated "Target JVM:
    JDK 26" fact injected right next to the ecosystem-preservation
    invariant telling the model NOT to write Java, directly contradicting
    it in the same prompt. Not exhaustive by design (a Java goal that never
    says "Java"/"Maven"/"Gradle"/"Spring"/"JVM" and isn't extending an
    existing Java project would miss this fact) - that's a narrower,
    rarer miss than the false-positive-on-every-non-Java-goal bug it
    replaces."""
    if any(adapter.detects(workspace_path) for adapter in BUILD_ADAPTERS if adapter.language == "java"):
        return True
    return bool(re.search(r"\b(java|jvm|maven|gradle|spring(?:\s*boot)?)\b", goal, re.IGNORECASE))


def _java_toolchain_fact(goal: str, workspace_path: str) -> Optional[str]:
    """The JDK this project's compile, test and runtime gates run under, as a fact for the Planner/Architect/Developer prompts - or None.

    Skill rules that are JDK-version-conditional (a startup flag required on one JDK range and fatal on another) cannot be applied without the
    number, so the prompt states it. The number is the CONTAINED execution JDK and comes only from the canonical owner
    (``toolchain_identity.declared_java_execution_identity``: the project's own build declaration -> the versioned image the gates use). It is
    stated only when the tree already declares it. Before that (a new project whose build file is not written yet, or one that declares no version)
    nothing is said: neither the host's JDK nor a default is a fact about this project's execution (TOOLCHAIN-HOST-PROBE-AUTHORITY-001).
    None as well when ``_goal_or_repo_targets_java()`` finds no evidence that this generation is about Java, and when the declaration cannot be
    resolved (the gates raise that typed, with the reason; a prompt line is not the place to report it)."""
    if not _goal_or_repo_targets_java(goal, workspace_path):
        return None
    from kriya.tools.toolchain_identity import ToolchainResolutionError, declared_java_execution_identity
    try:
        identity = declared_java_execution_identity(workspace_path)
    except ToolchainResolutionError:
        return None
    if identity is None:
        return None
    return (
        f"Target JVM (the contained JDK Kriya's compile, test and runtime gates run this project under, selected from "
        f"{identity.requirement_source}): JDK {identity.runtime_version}."
    )


# Small, extensible table of (JVM flag substring, min JDK major version it's
# forbidden on, human reason) - deliberately NOT a generic "flag
# compatibility" subsystem, since only one real instance of this problem
# class has ever been found. Add the next one here as its own tuple if/when
# it's found; this stays a plain list either way, not new infrastructure.
_JDK_INCOMPATIBLE_JVM_FLAGS: List[Tuple[str, int, str]] = [
    (
        "-Djava.security.manager=allow", 24,
        "JEP 486 removed the Security Manager entirely in JDK 24 - passing "
        "this flag now itself crashes VM startup with \"Enabling a Security "
        "Manager is not supported.\"",
    ),
]


def _strip_jdk_incompatible_jvm_flags(content: str, contained_jdk_major: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
    """Strips a JVM flag from the candidate's pom.xml exec-maven-plugin ``<argument>`` list when it is known to be fatal on the JDK the candidate
    is actually run under - the correction of last resort for a mistake the model kept making with the rule and the fact both in its prompt
    (confirmed live 2026-08-07, skills/qpid).

    ``contained_jdk_major`` is the major version of the candidate's CONTAINED toolchain identity (the validator's ``toolchain_identity``, the one
    owner). The caller resolves it; this function never asks the host which JDK its ``mvn`` runs on (TOOLCHAIN-HOST-PROBE-AUTHORITY-001: that
    answer decided a transform of a candidate that never runs on the host). None (identity not resolvable here) changes nothing.

    A pure transform of pom.xml text (FILE-INTEGRITY-CONTRACT-001): (corrected content, note) or (None, None). It never writes; the attempt
    applies it as a candidate mutation, before any verification gate."""
    if contained_jdk_major is None:
        return None, None
    notes = []
    for flag, min_forbidden_jdk, reason in _JDK_INCOMPATIBLE_JVM_FLAGS:
        if contained_jdk_major < min_forbidden_jdk or flag not in content:
            continue
        pattern = re.compile(rf"[ \t]*<argument>\s*{re.escape(flag)}\s*</argument>[ \t]*\n?")
        new_content, count = pattern.subn("", content)
        if count:
            content = new_content
            notes.append(
                f"Stripped '{flag}' from pom.xml before running - {reason} "
                f"(contained execution JDK {contained_jdk_major})."
            )
    return (content, " ".join(notes)) if notes else (None, None)


def pom_names_a_jdk_conditional_flag(content: str) -> bool:
    """Whether the pom text carries any flag of ``_JDK_INCOMPATIBLE_JVM_FLAGS``: the cheap test that decides whether the contained toolchain
    identity has to be resolved for ``_strip_jdk_incompatible_jvm_flags`` at all."""
    return any(flag in content for flag, _jdk, _reason in _JDK_INCOMPATIBLE_JVM_FLAGS)


def toolchain_declaration_mutable(
    write_scope_mode: Any, allowed_write_relpaths: Optional[Iterable[str]],
    structured_plan: Any = None, extra_relpaths: Iterable[str] = (),
) -> bool:
    """PRD-011: whether this run has structured authority to change the
    repository's toolchain declaration (pom.xml / build.gradle[.kts] /
    pyproject.toml / .python-version at the workspace root) - the authority
    a toolchain migration needs. Derived only from the run's write scope
    (the same value AuthorizedFileWriter enforces) and the approved plan's
    planned files, never from goal wording:
    - an unrestricted direct run may write the declaration;
    - otherwise the declaration must be in the allowed write scope, in some
      subtask's planned files of the approved plan, or in ``extra_relpaths``
      (an owner's authorized files).
    ``write_scope_mode`` None means what run_generation_workflow means by
    it: an allowlist when allowed paths are given, else unrestricted."""
    from kriya.tools.toolchain_identity import ALL_TOOLCHAIN_DECLARATION_FILES

    allowed = [path for path in (allowed_write_relpaths or ()) if path]
    mode = getattr(write_scope_mode, "value", write_scope_mode)
    if mode is None:
        mode = "allowlist" if allowed else "unrestricted"
    if mode == "unrestricted":
        return True
    paths = list(extra_relpaths)
    if mode != "deny_all":
        paths.extend(allowed)
    for subtask in getattr(structured_plan, "subtasks", None) or ():
        paths.extend(planned.path for planned in getattr(subtask, "planned_files", None) or ())
    return any(
        os.path.normpath(path).replace(os.sep, "/") in ALL_TOOLCHAIN_DECLARATION_FILES
        for path in paths if isinstance(path, str)
    )
