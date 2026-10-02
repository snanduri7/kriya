"""Maven build adapter: the Maven compile gate, test gate and output layout,
moved unchanged from kriya/tools/validate.py (Capability Adapters R1).

Every command runs through the validator handle (``v._run_maven_cmd``:
containment, registry-scoped acquisition, the Kriya-owned repository under
the state root, gate binding); this module never starts a process itself.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from kriya.capabilities.ports import BuildAdapter
from kriya.tools.containment import ContainmentSetupError

logger = logging.getLogger(__name__)

POM = "pom.xml"


class MavenBuildAdapter(BuildAdapter):
    build_system = "maven"
    language = "java"
    tools = frozenset({"mvn", "mvnw", "mvn.cmd"})

    def detects(self, workspace_root: str) -> bool:
        return os.path.exists(os.path.join(workspace_root, POM))

    def output_roots(self, cmd: List[str], cwd: str) -> List[str]:
        """``target/`` of every module (a directory holding ``pom.xml``)."""
        from kriya.tools.validate import _project_dirs

        return [os.path.join(d, "target") for d in _project_dirs(cwd, lambda files: POM in files)]

    def compile(self, v: Any, files: List[str], *, deadline: Optional[float]) -> Optional[Dict[str, Any]]:
        """The dependency-regression check, then ``mvn clean compile`` when the
        workspace has a pom.xml. None: Maven did not decide (no pom.xml, or
        mvn failed to start for a reason other than a missing executable or a
        containment setup failure) - the caller falls through to Gradle /
        javac exactly as before."""
        from kriya.tools.validate import get_pom_reactor_modules

        # 1. Check for dependency regression if original pom.xml exists
        if v.original_workspace_path:
            orig_pom = os.path.join(v.original_workspace_path, "pom.xml")
            new_pom = os.path.join(v.workspace_path, "pom.xml")
            if os.path.exists(orig_pom) and os.path.exists(new_pom):
                orig_deps = v._get_pom_dependencies(orig_pom)
                new_deps = v._get_pom_dependencies(new_pom)
                missing_deps = [
                    d for d in orig_deps
                    if d not in new_deps and d not in v.authorized_dependency_removals
                ]
                if missing_deps:
                    return {
                        "success": False,
                        "output": f"Dependency regression: The following dependencies were removed from pom.xml: {', '.join(missing_deps)}. You must preserve all existing dependencies."
                    }

        # 2. Run Maven compile if pom.xml exists
        if os.path.exists(os.path.join(v.workspace_path, "pom.xml")):
            try:
                # showWarnings + compilerArgument enable javac's -Xlint:rawtypes,
                # unchecked diagnostics with real file:line pointers (javac's
                # default one-line "uses unchecked or unsafe operations" summary
                # has no location info at all) - these are standard, portable
                # maven-compiler-plugin CLI properties, no pom.xml cooperation
                # needed. On a real compile FAILURE, this text is already
                # captured into error_output below for free - a raw-type mistake
                # (e.g. `ignite.cache(name)` used without generics, causing a
                # later "incompatible types: Object cannot be converted to X"
                # error) now shows up as an explicit, precisely-located "rawtypes"
                # warning alongside the hard error, rather than the model having
                # to infer the root cause from the type-mismatch message alone.
                res = v._run_maven_cmd(
                    [
                        "clean", "compile",
                        "-Dmaven.compiler.showWarnings=true",
                        "-Dmaven.compiler.compilerArgument=-Xlint:rawtypes,unchecked",
                    ],
                    cwd=v.workspace_path, timeout=300, deadline=deadline,
                )
                if res["returncode"] == 0:
                    # Found live, 2026-08-22 (ignite_qpid_protocol): a
                    # returncode of 0 here is a false positive whenever
                    # Maven's default sourceDirectory (src/main/java)
                    # doesn't cover where the project's real .java files
                    # live - "nothing to compile" isn't a build error, so
                    # this branch reported success while target/classes
                    # stayed completely empty. The actual failure only
                    # surfaced downstream, at RUNTIME, as a confusing
                    # "Could not find or load main class" - a build-layout
                    # gap this gate should have caught immediately instead
                    # of ever claiming compilation "succeeded".
                    #
                    # Multi-module reactor branch added (2026-09-07, P7
                    # production-validation): a genuine Maven reactor's
                    # ROOT pom.xml (packaging=pom, real <modules>) has no
                    # compiled output of its own - each declared module
                    # compiles into its OWN <module>/target/classes, not
                    # <workspace>/target/classes. The single-module check
                    # below unconditionally checking the workspace root
                    # was a structural, universal false-positive for ANY
                    # multi-module reactor, confirmed live: 16/16 rejected
                    # attempts, all with correct generated code, all
                    # identical "zero .class files" message. get_pom_
                    # reactor_modules() returning [] (a genuinely single-
                    # module project - the overwhelmingly common case)
                    # leaves this exact single-module check completely
                    # unchanged.
                    if any(f.endswith(".java") for f in files):
                        reactor_modules = get_pom_reactor_modules(
                            os.path.join(v.workspace_path, "pom.xml"),
                        )
                        if reactor_modules:
                            missing_modules = v._java_reactor_modules_missing_compiled_output(
                                files, reactor_modules,
                            )
                            if missing_modules:
                                return {
                                    "success": False,
                                    "output": (
                                        "Maven reported compilation success, but the reactor "
                                        f"module(s) {', '.join(missing_modules)} produced zero "
                                        ".class files under their own target/classes, despite "
                                        "owning a candidate .java file in this change set. This "
                                        f"is a Maven reactor (root pom.xml declares modules: "
                                        f"{', '.join(reactor_modules)}) - each module compiles "
                                        "into its own <module>/target/classes, never the "
                                        "aggregator root's. Check the affected module's own "
                                        "<sourceDirectory> if one is set, or whether the .java "
                                        "file is actually under that module's conventional "
                                        "src/main/java layout."
                                    ),
                                }
                        else:
                            classes_dir = os.path.join(v.workspace_path, "target", "classes")
                            compiled_anything = False
                            if os.path.isdir(classes_dir):
                                for _dirpath, _dirnames, filenames in os.walk(classes_dir):
                                    if any(fn.endswith(".class") for fn in filenames):
                                        compiled_anything = True
                                        break
                            if not compiled_anything:
                                return {
                                    "success": False,
                                    "output": (
                                        "Maven reported compilation success, but zero .class files "
                                        "were actually produced under target/classes. Maven's default "
                                        "sourceDirectory (src/main/java) most likely doesn't cover "
                                        "where this project's .java files actually live - add an "
                                        "explicit <sourceDirectory> to pom.xml's <build> section "
                                        "pointing at their real location, rather than assuming the "
                                        "conventional src/main/java layout."
                                    ),
                                }
                    return v._validation_result(True, "Maven compilation succeeded.", res)
                error_output = f"Maven compilation failed:\n{res['stdout']}\n{res['stderr']}"
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
            except FileNotFoundError as e:
                # 'mvn' itself isn't on PATH - a toolchain problem, not a code
                # defect. Must be returned, not just logged: previously this
                # was silently swallowed to a debug-level warning and fell
                # through to the raw javac fallback below, which - for any
                # project with real Maven dependencies (e.g. Ignite/Qpid) -
                # produces a misleading "cannot find symbol" error that looks
                # exactly like a code/import bug, sending the retry loop
                # hunting for something that was never there.
                return {"success": False, "output": f"Failed to invoke mvn compile: {e}"}
            except ContainmentSetupError:
                # SEC-002 (2026-09-12): a containment/resource setup
                # failure must never be swallowed into the generic
                # warning-and-fall-through below - that would let
                # execution continue on to the Gradle check, then the
                # raw javac fallback, which (when `files` contains no
                # .java entries) reaches "No Java files to compile" ->
                # success:True, silently reporting a real security-
                # significant setup failure as gate PASS.
                raise
            except Exception as e:
                logger.warning(f"Failed to invoke mvn compile: {e}")
        return None

    def run_tests(self, v: Any, test_class: Optional[str]) -> Dict[str, Any]:
        goals = ["test"]
        if test_class:
            goals.append(f"-Dtest={test_class}")
        res = v._run_maven_cmd(goals, cwd=v.workspace_path, timeout=300)
        return v._validation_result(
            res["returncode"] == 0, res["stdout"] + "\n" + res["stderr"], res,
        )
