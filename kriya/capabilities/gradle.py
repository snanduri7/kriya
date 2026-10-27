"""Gradle build adapter: the Gradle compile gate, test gate and output layout,
moved unchanged from kriya/tools/validate.py (Capability Adapters R1, second
slice).

Every command runs through the validator handle (``v._run_cmd_with_timeout``:
containment, resource limits, gate binding); this module never starts a
process itself. The project's wrapper (``./gradlew``) is used when it ships
one, else ``gradle`` from PATH.

A root ``build.gradle`` (Groovy DSL) or - GRADLE-KOTLIN-DSL-001, BACKEND-
READINESS-004 - ``build.gradle.kts`` (Kotlin DSL) marks the project; the
commands are the wrapper's own tasks and do not depend on the script
language. ``compileJava`` / ``test`` run once at the root, which covers every
subproject the root build includes. Output roots already knew both DSLs.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from kriya.capabilities.ports import BuildAdapter
from kriya.tools.containment import ContainmentSetupError

logger = logging.getLogger(__name__)

BUILD_SCRIPT = "build.gradle"
_PROJECT_SCRIPTS = ("build.gradle", "build.gradle.kts")
_WRAPPER = "gradlew"


class GradleBuildAdapter(BuildAdapter):
    build_system = "gradle"
    language = "java"
    supports_modules = True
    tools = frozenset({"gradle", "gradlew", "gradle.bat"})

    def detects(self, workspace_root: str) -> bool:
        return any(os.path.exists(os.path.join(workspace_root, script)) for script in _PROJECT_SCRIPTS)

    def output_roots(self, cmd: List[str], cwd: str) -> List[str]:
        """``build/`` of every project (``build.gradle[.kts]``, or the
        ``<directory>.gradle[.kts]`` a renaming settings file gives a
        subproject - GRADLE-SUBPROJECT-BUILD-FILE-001) plus the root
        ``.gradle/`` cache."""
        from kriya.tools.validate import gradle_project_dirs

        return [os.path.join(d, "build") for d in gradle_project_dirs(cwd)] + [os.path.join(cwd, ".gradle")]

    @staticmethod
    def command(workspace_root: str) -> str:
        """``./gradlew`` when the project ships the wrapper, else ``gradle``."""
        return "./gradlew" if os.path.exists(os.path.join(workspace_root, _WRAPPER)) else "gradle"

    def compile(self, v: Any, files: List[str], *, deadline: Optional[float]) -> Optional[Dict[str, Any]]:
        """``compileJava`` when the workspace has a build.gradle. None means ONLY
        "not applicable" (no build.gradle): the caller continues with javac.
        Once a build script exists Gradle always decides: PASS only from a
        Gradle that ran and exited 0; FAIL from one that ran and rejected the
        code; and when no trustworthy verdict exists (the launcher could not be
        started, an exception, an uninterpretable result) a typed
        ``environment_reason_code`` result (F9, owner decision D6) - never a
        PASS and never a fall-through to the downstream no-source javac
        success. A containment setup failure propagates."""
        from kriya.tools.dependency_execution import (
            GRADLE_COMPILE_INDETERMINATE,
            GRADLE_INVOCATION_FAILED,
            compile_environment_result,
        )

        if not self.detects(v.workspace_path):
            return None
        gradle_cmd = self.command(v.workspace_path)
        try:
            # GRADLE-WRAPPER-CONTAINMENT-001: the validator's two-phase
            # Gradle command (offline, bounded acquisition, offline).
            res = v._run_gradle_cmd(gradle_cmd, ["compileJava"], cwd=v.workspace_path)
            if not isinstance(res, dict) or not isinstance(res.get("returncode"), int):
                return compile_environment_result(
                    GRADLE_COMPILE_INDETERMINATE,
                    f"Gradle compilation returned no trustworthy verdict: its result carried no integer return code ({type(res).__name__}); "
                    "nothing about the candidate was observed.")
            if res["returncode"] == 0:
                return v._validation_result(True, "Gradle compilation succeeded.", res)
            if res.get("environment_reason_code"):
                return v._validation_result(
                    False, f"{res['environment_reason_code']}: Gradle could not be started to verify this candidate:\n"
                           f"{res['stdout']}\n{res['stderr']}", res)
            return v._validation_result(False, f"Gradle compilation failed:\n{res['stdout']}\n{res['stderr']}", res)
        except OSError as e:
            # Same reasoning as the mvn case: the launcher could not be started (missing, not executable, ENOEXEC) - typed, never a fall-through to the
            # javac fallback and never a candidate conviction.
            return compile_environment_result(GRADLE_INVOCATION_FAILED, f"Failed to invoke {gradle_cmd} compileJava: {e}")
        except ContainmentSetupError:
            # SEC-002 (2026-09-12): must not silently fall through to the
            # javac fallback, which can report success:True for a real
            # containment/setup failure.
            raise
        except Exception as e:
            # F9 (owner decision D6): not "Gradle did not decide" - logged with its traceback and returned as a typed no-verdict result.
            logger.error("gradle compileJava produced no trustworthy verdict", exc_info=True)
            return compile_environment_result(
                GRADLE_COMPILE_INDETERMINATE,
                f"Gradle compilation returned no trustworthy verdict ({type(e).__name__}: {e}); nothing about the candidate was observed.")

    def run_tests(self, v: Any, test_class: Optional[str], *, module: Optional[str] = None) -> Dict[str, Any]:
        """``module`` (a declared Gradle project directory such as ``core`` or ``a/b``) runs that project's ``test``
        task only (``:core:test``); without it the selector names every project's ``test`` task, and a project with
        no match for ``--tests`` fails the build."""
        from kriya.workflow.jvm_module_ownership import gradle_project_path

        tasks = [f"{gradle_project_path(module)}:test" if module else "test"]
        if test_class:
            tasks.extend(["--tests", test_class])
        res = v._run_gradle_cmd(self.command(v.workspace_path), tasks, cwd=v.workspace_path)
        binding = getattr(v, "test_report_binding", None)
        if binding is not None:
            binding.observe(res)  # FS-1A: build/test-results is read after this run
        return v._validation_result(
            res["returncode"] == 0, res["stdout"] + "\n" + res["stderr"], res,
        )
