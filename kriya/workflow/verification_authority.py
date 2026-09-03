"""Deterministic authority classification for verification commands.

Build and test tools already communicate their verdict through their process
status.  Their quiet-success output must not be reinterpreted as a behavioral
failure by an LLM grader.  Application execution remains semantic and is not
classified here.
"""
from pathlib import Path
from typing import List, Optional


_MAVEN_LIFECYCLE_GOALS = {
    "validate", "compile", "test", "package", "verify", "install",
    "test-compile", "clean",
}
_GRADLE_BUILD_TASKS = {
    "assemble", "build", "check", "classes", "compilejava",
    "compilekotlin", "test", "testclasses",
}
_CARGO_BUILD_TASKS = {"build", "check", "test"}


def _non_option_tokens(command: List[str], start: int = 1) -> List[str]:
    return [token.lower() for token in command[start:] if token and not token.startswith("-")]


def _maven_goals(command: List[str]) -> List[str]:
    goals: List[str] = []
    skip_next = False
    options_with_values = {"-f", "--file", "-s", "--settings", "-t", "--toolchains"}
    for token in command[1:]:
        lowered = token.lower()
        if skip_next:
            skip_next = False
            continue
        if lowered in options_with_values:
            skip_next = True
            continue
        if lowered.startswith("-") or "=" in lowered:
            continue
        goals.append(lowered)
    return goals


def deterministic_verification_kind(command: List[str]) -> Optional[str]:
    """Return ``build``/``test`` when exit status is the authoritative verdict.

    The classifier is intentionally allowlisted.  Unknown plugin goals and
    application runners (for example ``spring-boot:run`` or ``gradle run``)
    remain subject to contract/behavioral grading.
    """
    if not command:
        return None
    executable = Path(command[0]).name.lower()
    tokens = _non_option_tokens(command)

    if executable in {"mvn", "mvnw"}:
        goals = _maven_goals(command)
        if goals and all(goal in _MAVEN_LIFECYCLE_GOALS for goal in goals):
            return "test" if any(goal in {"test", "verify"} for goal in goals) else "build"
        return None

    if executable in {"gradle", "gradlew"}:
        tasks = [token.split(":")[-1] for token in tokens if "=" not in token]
        if tasks and all(task in _GRADLE_BUILD_TASKS or task.startswith("compile") for task in tasks):
            return "test" if any(task in {"test", "check"} for task in tasks) else "build"
        return None

    if executable in {"pytest", "py.test"}:
        return "test"
    if executable in {"python", "python3"} and len(command) >= 3 and command[1] == "-m":
        module = command[2].lower()
        if module in {"pytest", "unittest"}:
            return "test"
        # ``python -m django test`` delegates to Django's test runner.  The
        # semantic discriminator is the module subcommand, not the framework
        # name alone: runserver and other Django commands remain application
        # or unknown process execution.
        if module == "django" and len(command) >= 4 and command[3].lower() == "test":
            return "test"
    if executable in {"npm", "yarn", "pnpm"} and tokens and tokens[0] == "test":
        return "test"
    if executable == "go" and tokens and tokens[0] == "test":
        return "test"
    if executable == "cargo" and tokens and tokens[0] in _CARGO_BUILD_TASKS:
        return "test" if tokens[0] == "test" else "build"
    if executable == "javac":
        return "build"
    return None


def deterministic_sequence_kind(commands: List[List[str]]) -> Optional[str]:
    """Classify a sequence only when every command has process authority."""
    kinds = [deterministic_verification_kind(command) for command in commands]
    if not kinds or any(kind is None for kind in kinds):
        return None
    return "test" if "test" in kinds else "build"
