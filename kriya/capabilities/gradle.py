"""Gradle build adapter: the Gradle compile gate, test gate and output layout,
moved unchanged from kriya/tools/validate.py (Capability Adapters R1, second
slice).

Every command runs through the validator handle (``v._run_cmd_with_timeout``:
containment, resource limits, gate binding); this module never starts a
process itself. The project's wrapper (``./gradlew``) is used when it ships
one, else ``gradle`` from PATH.

R1 support is what the validator already had: a root ``build.gradle`` (Groovy
DSL) marks the project; ``compileJava`` / ``test`` run once at the root, which
covers every subproject the root build includes. A Kotlin-DSL-only root
(``build.gradle.kts``) is not detected yet - recorded as a capability gap
(registry GRADLE-KOTLIN-DSL-001), not silently widened here. Output roots
already know both DSLs, as before.
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
    tools = frozenset({"gradle", "gradlew", "gradle.bat"})

    def detects(self, workspace_root: str) -> bool:
        return os.path.exists(os.path.join(workspace_root, BUILD_SCRIPT))

    def output_roots(self, cmd: List[str], cwd: str) -> List[str]:
        """``build/`` of every project (``build.gradle[.kts]``) plus the root
        ``.gradle/`` cache."""
        from kriya.tools.validate import _project_dirs

        return ([os.path.join(d, "build") for d in _project_dirs(
            cwd, lambda files: any(script in files for script in _PROJECT_SCRIPTS))]
                + [os.path.join(cwd, ".gradle")])

    @staticmethod
    def command(workspace_root: str) -> str:
        """``./gradlew`` when the project ships the wrapper, else ``gradle``."""
        return "./gradlew" if os.path.exists(os.path.join(workspace_root, _WRAPPER)) else "gradle"

    def compile(self, v: Any, files: List[str], *, deadline: Optional[float]) -> Optional[Dict[str, Any]]:
        """``compileJava`` when the workspace has a build.gradle. None: Gradle
        did not decide (no build.gradle, or the command failed to start for a
        reason other than a missing executable or a containment setup
        failure) - the caller falls through to javac exactly as before."""
        if not self.detects(v.workspace_path):
            return None
        gradle_cmd = self.command(v.workspace_path)
        try:
            # GRADLE-WRAPPER-CONTAINMENT-001: the validator's two-phase
            # Gradle command (offline, bounded acquisition, offline).
            res = v._run_gradle_cmd(gradle_cmd, ["compileJava"], cwd=v.workspace_path)
            if res["returncode"] == 0:
                return v._validation_result(True, "Gradle compilation succeeded.", res)
            if res.get("environment_reason_code"):
                return v._validation_result(
                    False, f"{res['environment_reason_code']}: Gradle could not be started to verify this candidate:\n"
                           f"{res['stdout']}\n{res['stderr']}", res)
            return v._validation_result(False, f"Gradle compilation failed:\n{res['stdout']}\n{res['stderr']}", res)
        except FileNotFoundError as e:
            # Same reasoning as the mvn case - don't silently fall through to
            # the misleading raw javac fallback.
            return {"success": False, "output": f"Failed to invoke {gradle_cmd} compileJava: {e}"}
        except ContainmentSetupError:
            # SEC-002 (2026-09-12): must not silently fall through to the
            # javac fallback, which can report success:True for a real
            # containment/setup failure.
            raise
        except Exception as e:
            logger.warning(f"Failed to invoke gradle compileJava: {e}")
        return None

    def run_tests(self, v: Any, test_class: Optional[str]) -> Dict[str, Any]:
        tasks = ["test"]
        if test_class:
            tasks.extend(["--tests", test_class])
        res = v._run_gradle_cmd(self.command(v.workspace_path), tasks, cwd=v.workspace_path)
        binding = getattr(v, "test_report_binding", None)
        if binding is not None:
            binding.observe(res)  # FS-1A: build/test-results is read after this run
        return v._validation_result(
            res["returncode"] == 0, res["stdout"] + "\n" + res["stderr"], res,
        )
