"""Javac build adapter: the raw ``javac`` fallback compile gate, its (absent)
test gate and its output layout, moved unchanged from kriya/tools/validate.py
(Capability Adapters R1, javac slice).

javac is not a build system a workspace declares: it is the fallback the
validator reaches for a Java workspace when no build adapter (Maven, then
Gradle) decided the compile - a marker-free project, or one whose build tool
failed to start for a reason other than a missing executable or a containment
setup failure. So it is never part of the marker precedence
(``BUILD_ADAPTERS``) and ``detects`` is always False; the validator calls it
explicitly after the build adapters.

Every command runs through the validator handle (``v._run_cmd_with_timeout``:
containment, resource limits, the JDK home override, the audit policy, gate
binding); this module never starts a process itself. No classpath, release
flag or module path is added: the gate compiles exactly the changed sources
that exist, as before.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from kriya.capabilities.ports import BuildAdapter
from kriya.tools.containment import ContainmentSetupError

logger = logging.getLogger(__name__)

OUTPUT_DIR = "build"


class JavacBuildAdapter(BuildAdapter):
    build_system = "javac"
    language = "java"
    tools = frozenset({"javac"})

    def detects(self, workspace_root: str) -> bool:
        """Never: no workspace declares a raw-javac build; the validator
        reaches this adapter only after every build adapter left the compile
        undecided."""
        del workspace_root
        return False

    def output_roots(self, cmd: List[str], cwd: str) -> List[str]:
        """``javac -d X``: exactly ``X``; nothing without a destination."""
        del cwd
        return [cmd[cmd.index("-d") + 1]] if "-d" in cmd[:-1] else []

    def compile(self, v: Any, files: List[str], *, deadline: Optional[float]) -> Optional[Dict[str, Any]]:
        """A syntax/type check of the changed ``.java`` files that physically
        exist in the sandbox: ``javac -proc:none -d <workspace>/build``. Always
        decides (``deadline`` is unused, as before). A containment setup
        failure propagates; any other start failure is a decided failure."""
        del deadline
        # `files` can include controller-provided established-file context
        # used to inform planning and runtime judgment. Only pass sources
        # that physically exist in this sandbox to javac: a contextual name
        # that has not been materialized here must not turn an otherwise
        # valid compile into javac's unrelated "file not found" failure.
        java_files = [
            os.path.join(v.workspace_path, f)
            for f in files
            if f.endswith(".java")
            and os.path.isfile(os.path.join(v.workspace_path, f))
        ]
        if not java_files:
            return {"success": True, "output": "No Java files to compile."}

        cmd = ["javac", "-proc:none", "-d", os.path.join(v.workspace_path, OUTPUT_DIR)]
        cmd.extend(java_files)
        os.makedirs(os.path.join(v.workspace_path, OUTPUT_DIR), exist_ok=True)

        try:
            res = v._run_cmd_with_timeout(cmd, cwd=v.workspace_path)
            if res["returncode"] != 0:
                error_output = f"Java compilation failed:\n{res['stderr']}"
                try:
                    from kriya.tools.resolver import enrich_java_compiler_errors
                    error_output = enrich_java_compiler_errors(
                        error_output,
                        allow_external_lookup=(
                            v.autonomy_cfg.egress_policy != "local_only"
                            and v.autonomy_cfg.web_lookup_enabled
                        ),
                    )
                except Exception as ree:
                    logger.warning(f"Resolver failed to run: {ree}")
                return v._validation_result(False, error_output, res)
            return v._validation_result(True, "Java classes compiled successfully.", res)
        except ContainmentSetupError:
            # SEC-002 (2026-09-12): must propagate as the distinct
            # containment-setup failure it is, not be reported as an
            # ordinary "javac tool invocation failed" toolchain problem -
            # that framing hides the real root cause and never reaches
            # handle_attempt_failure's containment_setup_failed
            # classification.
            raise
        except Exception as e:
            return {"success": False, "output": f"Javac compilation tool invocation failed: {e}"}

    def run_tests(self, v: Any, test_class: Optional[Any]) -> Dict[str, Any]:
        """Raw javac has no test runner: the test gate is skipped, saying so."""
        del v, test_class
        return {"success": True, "output": "No Java test config found (pom.xml/gradle). Skipping."}
