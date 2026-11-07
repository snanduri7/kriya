"""KERNEL: the VERIFIED RUN RECIPE of a finished run (owner ruling 2026-11-07, "How to run").

A derived projection of what Kriya ACTUALLY executed and saw succeed - never model prose, never a README, never a guess. It is built from the run's own ``run_verification`` gate outcome of the attempt that
passed: that outcome's executed steps (each exit 0, none timed out) are the commands Kriya really ran, and since the effective-argv authority (RUNTIME-APPROVED-ARGV-VS-EXECUTED-INTERPRETER-001) the argv a human approved,
the one fingerprinted and the one executed are the same. The backend, not the desktop, decides how that maps to a normal invocation:

- the project interpreter ``.kriya/venv/bin/python`` (Kriya's scratch environment, discarded when its verification scope ends) is presented as ``python3`` - plus, when the project declares dependencies
  in a ``requirements.txt``, the install step that environment had; a ``pyproject.toml``-only declaration is a note, not an invented command;
- a bare tool (``mvn``, ``node``, ...) is presented as it ran;
- any other executable path (an absolute host path, ``./x``) cannot be truthfully presented as a user invocation: no recipe is produced.

``None`` when no application runtime check ran and passed (the card then says so). It records what was run in Kriya's isolated environment, which is evidence of how the application was verified, not a
guarantee about the user's machine; the recipe says so in its ``evidence`` block.
"""
from __future__ import annotations

import json
import os
import shlex
from typing import Any, Dict, List, Optional, Sequence

from kriya.config.environment import PROJECT_PYTHON_INTERPRETER

RUN_RECIPE_SCHEMA = 1
MAX_RECIPE_STEPS = 8
_NOT_AN_APPLICATION_RUN = ("managed_service_probe",)


def _executed_commands(outcome: Dict[str, Any]) -> Optional[List[List[str]]]:
    """The argv lists the run really executed and saw succeed, or None when any step failed, timed out or the evidence is not a clean list of argv."""
    steps = outcome.get("steps")
    if isinstance(steps, str):       # the persisted form of the same list
        try:
            steps = json.loads(steps)
        except ValueError:
            steps = None
    if isinstance(steps, list) and steps:
        commands: List[List[str]] = []
        for step in steps:
            if not isinstance(step, dict) or step.get("exit_code") != 0 or step.get("timed_out"):
                return None
            argv = step.get("command")
            if not isinstance(argv, list) or not argv or not all(isinstance(token, str) and token for token in argv):
                return None
            commands.append(list(argv))
        return commands
    recorded = outcome.get("commands")
    if isinstance(recorded, list) and recorded and all(isinstance(argv, list) and argv and all(isinstance(token, str) and token for token in argv) for argv in recorded):
        return [list(argv) for argv in recorded]
    return None


def _user_argv(argv: Sequence[str]) -> Optional[List[str]]:
    if argv[0] == PROJECT_PYTHON_INTERPRETER:
        return ["python3", *argv[1:]]
    if os.sep in argv[0] or "/" in argv[0]:
        return None
    return list(argv)


def derive_verified_run_recipe(gate_outcomes: Sequence[Dict[str, Any]], attempt_number: int, workspace_path: str) -> Optional[Dict[str, Any]]:
    """The recipe of the run that passed at ``attempt_number``, or None (see the module docstring)."""
    passed = [o for o in gate_outcomes
              if isinstance(o, dict) and o.get("type") == "run_verification" and o.get("success") is True and o.get("attempt") == attempt_number
              and o.get("graded_by") not in _NOT_AN_APPLICATION_RUN]
    if not passed:
        return None
    outcome = passed[-1]
    executed = _executed_commands(outcome)
    if not executed or len(executed) > MAX_RECIPE_STEPS:
        return None
    user_commands = [_user_argv(argv) for argv in executed]
    if any(argv is None for argv in user_commands):
        return None
    uses_project_python = any(argv[0] == PROJECT_PYTHON_INTERPRETER for argv in executed)
    steps: List[Dict[str, str]] = []
    note: Optional[str] = None
    if uses_project_python:
        from kriya.tools.validate import python_dependency_declaration

        has_requirements, has_pyproject = python_dependency_declaration(workspace_path)
        if has_requirements:
            steps.append({"kind": "setup", "command": "python3 -m pip install -r requirements.txt"})
        elif has_pyproject:
            note = "This project declares its dependencies in pyproject.toml: install them before running."
    steps.extend({"kind": "run", "command": shlex.join(argv)} for argv in user_commands if argv)
    entrypoint = next((token for token in (user_commands[0] or [])[1:] if not token.startswith("-")), None)
    return {
        "schema": RUN_RECIPE_SCHEMA,
        "working_directory": ".",
        "steps": steps,
        "entrypoint": entrypoint,
        "environment_note": note,
        "evidence": {
            "attempt": attempt_number, "gate": "run_verification", "graded_by": outcome.get("graded_by"),
            "verified_argv": [list(argv) for argv in executed],
            "statement": "Kriya ran this in its isolated project environment and it succeeded; this is how it was verified, not a guarantee about another machine.",
        },
    }
