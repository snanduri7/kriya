"""JDK/JAVA_HOME toolchain detection and correction for the Java/Maven retry loop - version mismatches, JDK-incompatible JVM flags, missing build manifests. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

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


def _check_java_toolchain_mismatch(stack: str) -> Optional[str]:
    """Toolchain preflight: the first time a generation run's detected stack is
    known to be 'java', checks whether 'java' and 'mvn' resolve to different JDK
    major versions - a mismatch a human would otherwise only discover after a
    wasted retry budget (see classify_environment_failure/the Quality Gates
    circuit breaker), or not at all if this particular goal never happens to
    exercise the version-sensitive flag. Returns None for any non-java stack (a
    Python/Ruby goal never pays for this) or when no mismatch is found."""
    if stack != "java":
        return None
    from kriya.tools.validate import check_java_toolchain
    toolchain = check_java_toolchain()
    if not toolchain["mismatch"]:
        return None
    return (
        f"'java' resolves to JDK {toolchain['java_version']} but 'mvn' will "
        f"build/run against JDK {toolchain['mvn_java_version']} - a JVM startup "
        "flag correct for one may be invalid or fatal under the other. Run "
        "`kriya doctor` for details."
    )


_JAVA_VERSION_MENTION_PATTERN = re.compile(r"\bjava\s+(\d{1,2})\b", re.IGNORECASE)


def _resolve_jdk_home_for_version(version: str) -> Optional[str]:
    """Resolves the real JDK home directory for a SPECIFIC major version
    number, using whichever mechanism is actually reliable on this platform
    - not one "portable" heuristic, since what 'java' on PATH even points to
    differs fundamentally by OS.

    Confirmed live, 2026-08-07, as a real, damaging bug in the original
    single-heuristic design: on macOS, 'java' on PATH is ALWAYS Apple's own
    dispatcher stub (`/usr/bin/java`) - a real, non-symlinked, root-owned
    file, never a symlink into an actual JDK. Deriving a JDK home by walking
    up from it (dirname(dirname(realpath('java')))) silently produced
    '/usr' - a directory that happened to satisfy the '.../bin/java' layout
    check but obviously isn't a JDK home. Set as JAVA_HOME, it hung a real
    `mvn clean compile` subprocess indefinitely rather than erroring
    cleanly, discovered live via `ps` showing the process stuck with near-
    zero CPU time. macOS ships exactly the right tool for this instead:
    `/usr/libexec/java_home -v <version>`, which resolves a SPECIFIC
    registered JDK version directly - more precise than deriving from
    whatever 'java' happens to point to, and unaffected by 'java' being a
    stub at all.

    On non-macOS platforms (Linux, where 'java' on PATH is typically a real
    symlink chain into an actual JDK install via update-alternatives or
    similar - no equivalent stub layer), falls back to resolving 'java' on
    PATH through any symlinks and relying on the standard
    '<JDK home>/bin/java' layout convention; the caller has already
    confirmed via check_java_toolchain() that 'java' resolves to the wanted
    version before this is ever called, so no version re-validation is
    needed on that path.

    Best-effort and defensive throughout - returns None (never raises) if
    the version can't actually be resolved this way."""
    if sys.platform == "darwin" and os.path.exists("/usr/libexec/java_home"):
        try:
            result = subprocess.run(
                ["/usr/libexec/java_home", "-v", version],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                home = result.stdout.strip()
                return home if home and os.path.isdir(home) else None
        except Exception as e:
            logger.debug(f"Failed to resolve JDK {version} home via /usr/libexec/java_home: {e}")
        return None

    java_path = shutil.which("java")
    if not java_path:
        return None
    try:
        real_path = os.path.realpath(java_path)
        bin_dir = os.path.dirname(real_path)
        if os.path.basename(bin_dir) != "bin":
            return None
        jdk_home = os.path.dirname(bin_dir)
        return jdk_home if os.path.isdir(jdk_home) else None
    except Exception as e:
        logger.debug(f"Failed to resolve JDK home from 'java' on PATH: {e}")
        return None


def _resolve_java_home_override(goal: str) -> Optional[str]:
    """When 'java' and 'mvn' resolve to different JDK major versions AND the
    goal explicitly states a target Java version matching the 'java' side of
    that mismatch (not 'mvn's), derives that JDK's real home directory so
    PolymorphicValidator can force every Maven subprocess call to actually
    build/run under it via JAVA_HOME - the same mechanism Maven's own
    launcher script already uses to decide which JDK to run itself under.

    Motivated by a real, live-confirmed gap: 'maven.compiler.source/target'
    in pom.xml controls what Java LANGUAGE version javac targets, not which
    JDK 'mvn' itself actually runs under - those are independent, and a
    goal explicitly saying "targeting Java 17" has no way to make Maven
    actually honor that if the machine's 'mvn' defaults to a different,
    genuinely incompatible JDK (confirmed live: JDK 26 permanently removed
    the Security Manager, breaking a Qpid Broker-J API call with no
    connection to anything the generated code controls).

    Deliberately narrow, matching this session's own established pattern for
    fixes like this: only activates for a real, detected mismatch where the
    goal names a version matching the SIDE OF THE MISMATCH THAT'S ALREADY
    CORRECT ('java', confirmed to already resolve to what's wanted) - never
    guesses at a version the goal doesn't state, never overrides a mismatch
    the goal doesn't actually care about, and does nothing at all on a
    single-JDK machine (nothing to reconcile)."""
    from kriya.tools.validate import check_java_toolchain
    toolchain = check_java_toolchain()
    if not toolchain["mismatch"]:
        return None
    m = _JAVA_VERSION_MENTION_PATTERN.search(goal)
    if not m or m.group(1) != toolchain["java_version"]:
        return None
    return _resolve_jdk_home_for_version(toolchain["java_version"])


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
    if (os.path.exists(os.path.join(workspace_path, "pom.xml")) or
            os.path.exists(os.path.join(workspace_path, "build.gradle"))):
        return True
    return bool(re.search(r"\b(java|jvm|maven|gradle|spring(?:\s*boot)?)\b", goal, re.IGNORECASE))


def _java_toolchain_fact(goal: str, workspace_path: str) -> Optional[str]:
    """Surfaces the actual, resolved target JVM as a concrete fact for the
    Planner/Architect/Developer prompts - not a warning like
    _check_java_toolchain_mismatch(), just ground truth. Skill rules that are
    genuinely JDK-version-conditional (e.g. a startup flag required on one JDK
    range and fatal on another) are otherwise unverifiable at generation time:
    the model has no way to reason about "is my applicable range satisfied"
    without knowing the actual number. Confirmed as a real gap during
    golden-use-case validation - a skill rule correct for JDK 17.0.10-23
    (-Djava.security.manager=allow) was silently wrong on JDK 24+ (JEP 486
    removed the Security Manager entirely), and nothing in the prompt ever told
    the model what JDK it was actually generating for. Prefers the JDK 'mvn'
    itself will build/run against (what a generated app's exec:java/exec:exec
    invocation actually executes under) over plain 'java', falling back to
    'java' if mvn isn't present. Returns None if neither tool is found, OR if
    _goal_or_repo_targets_java() finds no real evidence this generation is
    actually about Java - a non-Java project never pays for or sees this
    (the check this docstring always claimed, but didn't actually enforce
    until the fix documented on _goal_or_repo_targets_java above)."""
    if not _goal_or_repo_targets_java(goal, workspace_path):
        return None
    from kriya.tools.validate import check_java_toolchain
    toolchain = check_java_toolchain()
    version = toolchain["mvn_java_version"] or toolchain["java_version"]
    if not version:
        return None
    return f"Target JVM (resolved on this machine via 'mvn'/'java'): JDK {version}."


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


def _strip_jdk_incompatible_jvm_flags(worktree_path: str, java_home_override: Optional[str] = None) -> Optional[str]:
    """Deterministically strips a JVM flag from the worktree's pom.xml
    exec-maven-plugin <argument> list when it's known to be fatal on the
    actually-resolved target JDK, right before the run-verification gate
    actually executes the app - the correction of last resort for a mistake
    the model keeps making even with everything it needs to avoid it.

    Confirmed live, 2026-08-07: skills/qpid/rules.txt already states this
    exact JDK-version-conditional rule correctly ("on JDK 24+, DO NOT pass
    this flag... check the Target JVM fact given in this prompt"), and
    _java_toolchain_fact() (above) already correctly surfaces that exact
    fact in the prompt - `active_skills` confirmed both were active, the
    fact was present, and the model still added the forbidden flag on every
    attempt of a real batch run. A case where deterministic tooling is the
    right lever, not more prompting (see the durable lesson on file about
    this) - mirrors _resolve_run_command()'s existing pattern of
    deterministically correcting a known-wrong invocation detail rather
    than asking the model to get it right.

    java_home_override, when given, is the JDK home _resolve_java_home_override()
    already resolved for this run - pass it through rather than letting this
    function re-derive the target independently. Confirmed live, 2026-08-07:
    without this, the function called check_java_toolchain() fresh and used
    mvn's UNMODIFIED default JDK (26 on the validation machine) to decide
    what to strip, even on a run where JAVA_HOME was already being forced
    onto JDK 17 for every Maven subprocess - two independent "what JDK is
    this?" computations that can disagree about the one that actually
    matters (the JDK the subprocess will really run under). Harmless that
    time (the flag is optional either way on JDK 17), but not guaranteed to
    stay harmless. When set, the override's JDK IS toolchain['java_version']
    by construction (_resolve_java_home_override only ever overrides onto
    the 'java' side of a detected mismatch), so that's used instead of
    mvn_java_version.

    Best-effort and silent on any I/O problem - this is a defensive
    correction, not a required step, and must never itself break a run that
    would otherwise be fine. Returns a human-readable note describing what
    was stripped (for logging/toolchain_warning), or None if nothing
    needed correcting."""
    pom_path = os.path.join(worktree_path, "pom.xml")
    if not os.path.exists(pom_path):
        return None
    try:
        from kriya.tools.validate import check_java_toolchain
        toolchain = check_java_toolchain()
        if java_home_override:
            version_str = toolchain["java_version"]
        else:
            version_str = toolchain["mvn_java_version"] or toolchain["java_version"]
        if not version_str:
            return None
        resolved_major = int(version_str)

        with open(pom_path, "r", encoding="utf-8") as f:
            content = f.read()

        notes = []
        for flag, min_forbidden_jdk, reason in _JDK_INCOMPATIBLE_JVM_FLAGS:
            if resolved_major < min_forbidden_jdk or flag not in content:
                continue
            pattern = re.compile(rf"[ \t]*<argument>\s*{re.escape(flag)}\s*</argument>[ \t]*\n?")
            new_content, count = pattern.subn("", content)
            if count:
                content = new_content
                notes.append(
                    f"Stripped '{flag}' from pom.xml before running - {reason} "
                    f"(resolved target: JDK {resolved_major})."
                )

        if not notes:
            return None
        with open(pom_path, "w", encoding="utf-8") as f:
            f.write(content)
        return " ".join(notes)
    except Exception as e:
        logger.debug(f"_strip_jdk_incompatible_jvm_flags failed (non-fatal, skipping): {e}")
        return None


def _pin_exec_plugin_executable_to_resolved_jdk(worktree_path: str, java_home_override: Optional[str]) -> Optional[str]:
    """Pins exec-maven-plugin's <executable> (the exec:exec goal only -
    exec:java always runs inside Maven's own already-started JVM and ignores
    <executable>/<arguments> entirely, using <mainClass>/systemProperties
    instead) to the ABSOLUTE path of the JDK Kriya's own verification
    actually used, whenever a java_home_override is active - without this,
    the delivered project carries zero record of which JDK it was actually
    verified under.

    Found live, 2026-08-11 (kriya-staged-protocol-ignite-qpid): Runtime
    Verification reported PASSED because Kriya forces JAVA_HOME onto the
    goal-stated target JDK for its OWN subprocess calls only
    (_resolve_java_home_override) - that override is completely invisible in
    the delivered pom.xml, which still has a bare <executable>java</executable>
    (resolved via PATH at whatever JDK is default for WHOEVER runs it later,
    which has no reason to match what Kriya used). Confirmed live: on a
    machine where 'mvn' itself defaults to a different, genuinely
    incompatible JDK (JDK 26, which removed the Security Manager entirely and
    broke a real Qpid Broker-J API the generated app depends on), the exact
    same pom.xml Kriya had just verified crashed immediately the moment it was
    run outside Kriya's own JAVA_HOME-overridden subprocess environment -
    "Runtime Verification PASSED" was only ever true inside Kriya's own
    execution context, not for a human re-running the identical command
    afterward. This is the general, durable fix; stripping the one flag that
    happened to be the FIRST symptom (_strip_jdk_incompatible_jvm_flags above)
    would not have caught the SECOND, deeper incompatibility this same drift
    exposed once the flag was gone.

    Only activates when java_home_override is set (nothing to reconcile
    without a detected java/mvn mismatch in the first place) and
    <executable> is still the bare, unpinned "java" - never overwrites a
    value the model or a prior pass already pinned deliberately. Best-effort
    and silent on any I/O problem, same discipline as
    _strip_jdk_incompatible_jvm_flags - a defensive correction, never allowed
    to break a run that would otherwise be fine."""
    if not java_home_override:
        return None
    pom_path = os.path.join(worktree_path, "pom.xml")
    if not os.path.exists(pom_path):
        return None
    try:
        with open(pom_path, "r", encoding="utf-8") as f:
            content = f.read()

        resolved_java = os.path.join(java_home_override, "bin", "java")
        pattern = re.compile(r"<executable>\s*java\s*</executable>")
        new_content, count = pattern.subn(f"<executable>{resolved_java}</executable>", content, count=1)
        if not count:
            return None

        with open(pom_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return (
            f"Pinned exec-maven-plugin's <executable> to {resolved_java} - the JDK this "
            "run's verification actually used - so the delivered project runs consistently "
            "later regardless of the default JDK on whoever runs it."
        )
    except Exception as e:
        logger.debug(f"_pin_exec_plugin_executable_to_resolved_jdk failed (non-fatal, skipping): {e}")
        return None


_UNRESOLVED_PACKAGE_PATTERN = re.compile(r"package [\w.]+ does not exist")


def _detect_missing_build_manifest(worktree_path: str, raw_error_text: str) -> Optional[str]:
    """Deterministically detects a Java compile failure caused by a build
    manifest the Architect never explicitly asked the Developer to create -
    not one the Developer merely dropped after being asked (that's
    IncompleteGenerationError's job, and it already works).

    Confirmed live, 2026-08-07 (kriya-protocol-parser-app): pom.xml was
    never written across two separate full runs, three days apart. Every
    retry's own fix-analysis correctly diagnosed "the dependencies aren't
    declared in pom.xml" and explicitly declined to fix it, since a
    per-file targeted retry is told (correctly, in every other case) to
    stay in scope. extract_implicated_files()'s basename-in-text matching
    can never implicate pom.xml either, since a "package X does not exist"
    error never names the missing manifest file that's the real cause -
    so nothing in the retry loop could ever recover it, no matter how many
    attempts ran, because it was never requested in the first place, not
    because it was requested and lost.

    Fires purely from the error shape and the worktree's own current state,
    independent of whether the Architect's design ever listed the file at
    all - closes that structural blind spot as its own detection path,
    parallel to (not replacing) IncompleteGenerationError. A "package X does
    not exist" error can only happen for a genuinely external dependency (a
    JDK-standard java.*/javax.* package always resolves regardless of any
    build manifest), so requiring BOTH "no pom.xml/build.gradle exists" AND
    this specific error shape is a low-false-positive combination - a
    stdlib-only Java goal that never needed a manifest at all will simply
    never produce this error shape to begin with.

    Deliberately Maven-specific (returns "pom.xml", never "build.gradle") -
    only one real instance of this problem class has been found, and it was
    a Maven goal; a Gradle instance would need its own detection (different
    error shape) rather than being guessed at here. Mirrors
    _JDK_INCOMPATIBLE_JVM_FLAGS' philosophy: fix the confirmed instance
    precisely, don't build generality for one that hasn't happened yet."""
    if os.path.exists(os.path.join(worktree_path, "pom.xml")):
        return None
    if os.path.exists(os.path.join(worktree_path, "build.gradle")):
        return None
    if _UNRESOLVED_PACKAGE_PATTERN.search(raw_error_text):
        return "pom.xml"
    return None
