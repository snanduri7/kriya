import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from kriya.config.config import AutonomyConfig
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType
from kriya.tools.sandbox import build_restricted_env, posix_resource_limits_preexec_fn
from kriya.tools.process import ProcessController

logger = logging.getLogger(__name__)


def get_pom_dependencies(pom_path: str) -> List[str]:
    """Parses a pom.xml's <dependency> entries into 'groupId:artifactId' strings.
    Module-level (not a PolymorphicValidator method) so callers that need this
    before/without constructing a validator - e.g. the Developer retry loop
    priming a "preserve these existing dependencies" prompt checklist before
    any generation happens, not just the reactive post-hoc regression check
    below - can reuse the exact same parsing logic."""
    if not os.path.exists(pom_path):
        return []
    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        deps = []
        for dep in root.findall(f".//{ns}dependency"):
            groupId_elem = dep.find(f"{ns}groupId")
            artifactId_elem = dep.find(f"{ns}artifactId")
            if groupId_elem is not None and artifactId_elem is not None:
                deps.append(f"{groupId_elem.text.strip()}:{artifactId_elem.text.strip()}")
        return deps
    except Exception as e:
        logger.warning(f"Failed to parse POM dependencies at {pom_path}: {e}")
        return []


def get_pom_own_coordinate(pom_path: str) -> Optional[str]:
    """Reads a pom.xml's own top-level <groupId>/<artifactId> (direct children of
    <project>, not nested inside any <dependency>) as a single 'groupId:artifactId'
    string. Confirmed live as a real, previously-unnoticed gap: Maven's own build
    banner (`[INFO] ----------------< groupId:artifactId >-----------------`,
    printed at the start of every build) matches the exact same coordinate shape
    extract_error_search_terms() looks for - without this, a project's own
    made-up artifact ID gets treated as a genuine third-party library worth an
    outbound search, wasting a real repeated-failure live-lookup recovery
    attempt on a term that can never find anything useful."""
    if not os.path.exists(pom_path):
        return None
    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        group_elem = root.find(f"{ns}groupId")
        artifact_elem = root.find(f"{ns}artifactId")
        if group_elem is not None and artifact_elem is not None and group_elem.text and artifact_elem.text:
            return f"{group_elem.text.strip()}:{artifact_elem.text.strip()}"
        return None
    except Exception as e:
        logger.warning(f"Failed to parse POM's own coordinate at {pom_path}: {e}")
        return None


def _has_real_requirements(requirements_path: str) -> bool:
    """A requirements.txt with no real entries (empty, or comments only) isn't
    worth the cost of creating a venv and running pip over - module-level since
    it's a pure text check, no PolymorphicValidator state needed."""
    try:
        with open(requirements_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return True
    except Exception:
        pass
    return False


class PolymorphicValidator:
    """Detects workspace language stack and executes syntactic compile checks and dynamic test runners."""

    def __init__(
        self,
        workspace_path: str,
        original_workspace_path: Optional[str] = None,
        autonomy_cfg: Optional[AutonomyConfig] = None,
        java_home_override: Optional[str] = None,
    ) -> None:
        self.workspace_path = os.path.abspath(workspace_path)
        self.original_workspace_path = os.path.abspath(original_workspace_path) if original_workspace_path else None
        self.autonomy_cfg = autonomy_cfg or AutonomyConfig()
        self.stack = self._detect_stack()
        # When set (kriya/workflow/workflow.py's _resolve_java_home_override),
        # forces every subprocess this validator launches (mvn compile/test/exec,
        # javac fallback) to run under this specific JDK home via the JAVA_HOME
        # env var - the same mechanism Maven's own launcher script already uses
        # to decide which JDK to run itself under. Closes a real gap: the
        # 'maven.compiler.source/target' pom.xml settings control what Java
        # LANGUAGE version javac targets, not which actual JDK 'mvn' runs under
        # - those are independent, and a machine with more than one JDK
        # installed can easily have 'mvn' default to a different, genuinely
        # incompatible one (confirmed live: JDK 26 removed the Security Manager
        # entirely, breaking a Qpid Broker-J API call no goal-stated Java
        # version could have anticipated).
        self.java_home_override = java_home_override
        # MA4.4 (control-plane implementation plan) - audit-only. See
        # _run_cmd_with_timeout below; never consulted for enforcement.
        self.execution_policy = ExecutionPolicy()

    def _get_pom_dependencies(self, pom_path: str) -> List[str]:
        return get_pom_dependencies(pom_path)

    def _ensure_project_venv(self, requirements_path: str) -> Tuple[Optional[str], Optional[str]]:
        """Creates (if not already present) a project-local virtual environment
        under .kriya/venv and installs requirements.txt into it, so a Python
        goal needing a real third-party package can actually be tested -
        PolymorphicValidator otherwise runs tests via sys.executable (KRIYA'S
        OWN interpreter), which only has whatever Kriya itself depends on
        installed. The same class of gap Ruby's `bundle install` fix closed for
        that stack (2026-08-04) - a structurally unwinnable quality gate the
        model's own code correctness can never fix.

        Deliberately installs into an ISOLATED venv, not sys.executable
        directly: pip-installing an arbitrary generated project's dependencies
        straight into Kriya's OWN environment risks breaking Kriya itself (e.g.
        downgrading a package version Kriya's own pyproject.toml needs).

        Lives inside the (already git-untracked, worktree-scoped) .kriya/
        directory - reused across retries within the same run the same way the
        worktree itself is, and cleaned up the same way (git clean -fd) once
        the worktree is reset for reuse by a later, unrelated run.

        Returns (venv_python_path, None) on success. Returns (None, None) if
        venv CREATION itself fails - an infrastructure problem, not something
        a code retry can fix, so the caller falls back to sys.executable
        (today's pre-existing behavior) rather than failing the gate. Returns
        (None, error_message) if the actual `pip install` fails - a real
        dependency problem (e.g. a nonexistent package/version the model
        wrote), which the caller fails the gate on so the retry loop sees it,
        mirroring the Ruby bundle-install precedent exactly."""
        venv_dir = os.path.join(self.workspace_path, ".kriya", "venv")
        venv_python = os.path.join(venv_dir, "bin", "python")
        if not os.path.exists(venv_python):
            try:
                create_res = self._run_cmd_with_timeout(
                    [sys.executable, "-m", "venv", venv_dir], cwd=self.workspace_path, timeout=60,
                )
                if create_res["returncode"] != 0 or not os.path.exists(venv_python):
                    logger.warning(
                        f"Failed to create project-local venv at {venv_dir} - falling back to "
                        f"Kriya's own interpreter for this test run: {create_res['stderr']}"
                    )
                    return None, None
            except Exception as e:
                logger.warning(
                    f"Failed to create project-local venv at {venv_dir} - falling back to "
                    f"Kriya's own interpreter for this test run: {e}"
                )
                return None, None

        # Re-run on every call, even when the venv already existed - a retry
        # may have just edited requirements.txt, and pip itself is a fast
        # no-op when nothing actually changed since the last install.
        install_res = self._run_cmd_with_timeout(
            [venv_python, "-m", "pip", "install", "-q", "-r", requirements_path, "pytest"],
            cwd=self.workspace_path, timeout=300,
        )
        if install_res["returncode"] != 0:
            return None, (
                f"'pip install -r requirements.txt' failed:\n{install_res['stdout']}\n{install_res['stderr']}"
            )
        return venv_python, None

    def _resolve_python_interpreter(self) -> Tuple[str, Optional[str]]:
        """Resolves which Python interpreter Python subprocesses for this
        workspace should use - shared by run_tests() and run_app_sequence()/
        run_app() so a goal needing a real third-party package (e.g. Django)
        gets the SAME isolated, dependency-installed interpreter for both its
        test gate and its Runtime Verification gate, not just the test gate.
        Confirmed live, 2026-08-07 (django_healthcheck_gap, after the
        RepositoryAnalyzer manage.py-hallucination fix let this goal reach
        Runtime Verification for the first time): the isolated venv from
        run_tests()'s own fix was never reused here - run_verification still
        ran via sys.executable directly, hitting the identical
        'No module named django' failure one gate later.

        Returns (interpreter_path, install_error). interpreter_path is
        sys.executable when there's no requirements.txt, or when venv
        CREATION itself failed (an infrastructure problem, not something a
        retry can fix - degrades silently, same reasoning as
        _ensure_project_venv()'s own docstring). install_error is set ONLY
        when `pip install` of THIS project's own requirements.txt genuinely
        failed (a real, potentially code-fixable dependency problem, e.g. a
        bad package pin) - the caller should treat that as a hard failure
        rather than silently proceeding with an interpreter missing the
        dependencies the goal actually needs."""
        requirements_path = os.path.join(self.workspace_path, "requirements.txt")
        if os.path.exists(requirements_path) and _has_real_requirements(requirements_path):
            venv_python, install_error = self._ensure_project_venv(requirements_path)
            if install_error:
                return sys.executable, install_error
            if venv_python:
                return venv_python, None
        return sys.executable, None

    def _detect_stack(self) -> str:
        """Determines if the workspace uses Python, Java, or Ruby - or "unknown"
        for anything else (JS/TS, Go, Rust, C#, ...). Python used to be the blind
        default for anything not Java/Ruby, which meant a genuinely unsupported
        stack silently ran the Python compile-check branch, matched zero .py
        files, and reported a false-positive "Python files compiled successfully"
        - a quality gate that never actually checked anything. Python is now
        detected the same way Java/Ruby are, by real markers, so "no markers
        matched" is distinguishable from "this is a Python project"."""
        # 1. Check for Java
        if (os.path.exists(os.path.join(self.workspace_path, "pom.xml")) or
            os.path.exists(os.path.join(self.workspace_path, "build.gradle")) or
            os.path.exists(os.path.join(self.workspace_path, "src", "main", "java"))):
            return "java"

        # 2. Check for Ruby
        if (os.path.exists(os.path.join(self.workspace_path, "Gemfile")) or
            os.path.exists(os.path.join(self.workspace_path, "Rakefile")) or
            os.path.exists(os.path.join(self.workspace_path, "spec"))):
            return "ruby"

        # 3. Check for Python
        if (os.path.exists(os.path.join(self.workspace_path, "requirements.txt")) or
            os.path.exists(os.path.join(self.workspace_path, "pyproject.toml")) or
            os.path.exists(os.path.join(self.workspace_path, "setup.py")) or
            os.path.exists(os.path.join(self.workspace_path, "setup.cfg")) or
            os.path.exists(os.path.join(self.workspace_path, "Pipfile")) or
            self._has_any_py_file()):
            return "python"

        return "unknown"

    def _has_any_py_file(self) -> bool:
        """Bounded recursive fallback for a Python project with none of the
        standard marker files (e.g. a single-script goal with no packaging
        metadata yet) - stops at the first hit, skips common non-source/
        dependency directories so it doesn't walk a huge vendored tree."""
        skip_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".kriya"}
        for _root, dirs, filenames in os.walk(self.workspace_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            if any(f.endswith(".py") for f in filenames):
                return True
        return False

    def _audit_run_command(self, cmd: List[str], cwd: str) -> None:
        """MA4.4 - audit-only ExecutionPolicy consultation, mirroring
        kriya/core/llm.py's _audit_llm_network_access (MA4.3) exactly: this
        can never affect whether ProcessController actually runs `cmd` -
        its result is only logged, and any exception it raises is caught
        and logged here, never propagated. kriya/tools/validate.py's own
        PolymorphicValidator is the ONLY real ProcessController call site in
        Kriya today, so this is ExecutionPolicy's first real caller (still
        audit-only - AUDIT/ENFORCE mode itself is MA4.15's config, not
        added yet)."""
        try:
            result = self.execution_policy.evaluate(
                ActionRequest(action_type=ActionType.RUN_COMMAND, command=tuple(cmd), workspace_path=cwd)
            )
            logger.debug(
                "MA4 policy audit (not enforced): RUN_COMMAND '%s' -> %s (%s)",
                " ".join(cmd), result.decision.value, result.reason_code,
            )
        except Exception as e:
            logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)

    def _run_cmd_with_timeout(self, cmd: List[str], cwd: str, timeout: int = 300) -> Dict[str, Any]:
        self._audit_run_command(cmd, cwd)
        env = None
        preexec_fn = None
        if self.autonomy_cfg.sandbox_execution:
            env = build_restricted_env(self.autonomy_cfg.sandbox_env_allowlist)
            preexec_fn = posix_resource_limits_preexec_fn(
                self.autonomy_cfg.sandbox_cpu_seconds, self.autonomy_cfg.sandbox_memory_mb
            )
        if self.java_home_override:
            # env is None here means "inherit the parent process's environment
            # unchanged" (subprocess.Popen's own default) - that's no longer
            # correct once we need to ADD one override on top of it, so make
            # the inheritance explicit before overriding just the two JDK-
            # selection variables. Setting both JAVA_HOME (what mvn's own
            # launcher script checks first) and PATH (so a plain 'java'/'javac'
            # invocation resolves the same way, in case anything downstream
            # doesn't consult JAVA_HOME) covers both real mechanisms a JDK
            # gets selected by.
            env = dict(env) if env is not None else dict(os.environ)
            env["JAVA_HOME"] = self.java_home_override
            env["PATH"] = os.path.join(self.java_home_override, "bin") + os.pathsep + env.get("PATH", "")
        return ProcessController().run(
            cmd, cwd=cwd, timeout=timeout, env=env, preexec_fn=preexec_fn,
        ).to_dict()

    def run_pom_validate(self) -> Dict[str, Any]:
        """Cheap, semantic-level pre-check for a Maven pom.xml - catches a
        well-formed-but-wrong POM (e.g. the wrong root element, a missing
        <modelVersion>, malformed coordinates) before paying for the full
        compile gate's own dependency resolution + javac invocation.

        Motivation: find_structural_corruption() (kriya/workflow/edit_safety.py)
        already checks pom.xml is well-formed XML, but "well-formed" and
        "a valid Maven POM" are different questions - confirmed live,
        2026-08-16 (ignite_qpid_person, run b-6): a pom.xml whose root element
        was <plugin> instead of <project> is perfectly valid XML, passed that
        check cleanly, and was only caught by a full `mvn compile` - by which
        point every other file in the batch had already been written for
        nothing, since nothing else in the project could possibly compile
        without a usable POM.

        `mvn validate` is the Maven lifecycle's own first phase - it checks the
        POM's own shape (coordinates, model version, structure) without
        resolving the transitive dependency graph needed for compilation or
        invoking javac, so a genuinely broken POM fails fast without incurring
        that cost. Deliberately NOT run with --offline: run_compile_check()'s
        own `mvn clean compile` doesn't use it either, and introducing an
        inconsistency here would risk a spurious offline-only failure that has
        nothing to do with the POM itself.

        Returns the same {"success": bool, "output": str} shape as
        run_compile_check(), and is a no-op success (nothing to validate) if
        the project has no pom.xml at all - callers gate on whether pom.xml
        exists/was just written, but this stays safe to call unconditionally."""
        pom_path = os.path.join(self.workspace_path, "pom.xml")
        if not os.path.exists(pom_path):
            return {"success": True, "output": "No pom.xml to validate."}
        try:
            res = self._run_cmd_with_timeout(["mvn", "validate"], cwd=self.workspace_path, timeout=120)
            if res["returncode"] == 0:
                return {"success": True, "output": "Maven POM validation succeeded."}
            return {"success": False, "output": f"Maven POM validation failed:\n{res['stdout']}\n{res['stderr']}"}
        except FileNotFoundError as e:
            # 'mvn' itself isn't on PATH - a toolchain problem, not a POM
            # defect. Same reasoning as run_compile_check()'s identical guard:
            # must be returned, not silently swallowed, or a real toolchain
            # gap gets misread as a code-content bug.
            return {"success": False, "output": f"Failed to invoke mvn validate: {e}"}
        except Exception as e:
            logger.warning(f"Failed to invoke mvn validate: {e}")
            return {"success": True, "output": f"mvn validate could not be run ({e}) - skipped, not confirmed valid."}

    def resolve_maven_classpath(self) -> Optional[str]:
        """Resolves the REAL, full Maven classpath (including transitive
        dependencies - pom.xml's own <dependency> entries only ever list
        DIRECT ones, which is not enough to reliably locate a class that
        arrives transitively, e.g. through ignite-core's own dependency
        graph) via `mvn dependency:build-classpath`, writing the result to a
        temp file rather than parsing stdout - build-classpath's own stdout
        is full of noisy [INFO] lines around the actual classpath string,
        while `-Dmdep.outputFile=...` is the standard, parse-free way this
        goal is meant to be consumed programmatically. Ground truth for
        inspect_external_class() below - deliberately a separate method
        (not folded into it) since a future caller may want the raw
        classpath string for something other than a single javap lookup.
        Returns None on any failure (no pom.xml, mvn not on PATH,
        unresolvable dependencies, timeout) - never raises, since this is
        used from an optional recovery tool call, not a Quality Gate."""
        if self.stack != "java" or not os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
            return None
        fd, cp_file = tempfile.mkstemp(suffix=".kriya-classpath.txt")
        os.close(fd)
        try:
            res = self._run_cmd_with_timeout(
                ["mvn", "-q", "dependency:build-classpath", f"-Dmdep.outputFile={cp_file}"],
                cwd=self.workspace_path, timeout=120,
            )
            if res["returncode"] != 0:
                return None
            with open(cp_file, "r", encoding="utf-8", errors="replace") as fh:
                classpath = fh.read().strip()
            return classpath or None
        except Exception as e:
            logger.debug(f"Failed to resolve Maven classpath: {e}")
            return None
        finally:
            try:
                os.unlink(cp_file)
            except OSError:
                pass

    def inspect_external_class(self, fully_qualified_class_name: str) -> Optional[str]:
        """Deterministic ground truth for an external dependency's REAL
        public API surface, instead of trusting the model's own (possibly
        hallucinated) memory of a third-party library's method/constructor
        signatures - the gap that let a package-mismatch/build-layout guess
        go uncorrected earlier this same investigation, just one level up
        the stack: an external class is invisible to DependencyGraph/
        RepositoryAnalyzer entirely (they only ever index files physically
        inside the workspace), so no amount of workspace-local static
        analysis can ever reach it. `javap -public` against the real,
        resolved classpath returns only the public method/constructor
        signatures (small, structured output) - not a full decompile, same
        "small-argument-out" property read_file already has for workspace
        source. Returns None if the classpath can't be resolved or the
        class genuinely isn't found on it - the caller reports an honest
        "not found" to the model, never fabricates a shape."""
        classpath = self.resolve_maven_classpath()
        if not classpath:
            return None
        try:
            res = self._run_cmd_with_timeout(
                ["javap", "-public", "-classpath", classpath, fully_qualified_class_name],
                cwd=self.workspace_path, timeout=30,
            )
            if res["returncode"] != 0:
                return None
            return res["stdout"].strip() or None
        except Exception as e:
            logger.debug(f"Failed to inspect external class '{fully_qualified_class_name}': {e}")
            return None

    def run_compile_check(self, files: List[str]) -> Dict[str, Any]:
        """Runs language-specific compilation check on changed files."""
        if not files:
            return {"success": True, "output": "No files to compile check."}

        if self.stack == "python":
            errors = []
            for f in files:
                if f.endswith(".py"):
                    full = os.path.join(self.workspace_path, f)
                    if os.path.exists(full):
                        try:
                            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                                source = fh.read()
                            compile(source, f, "exec")
                        except SyntaxError as se:
                            errors.append(f"Syntax error in {f} line {se.lineno}: {se.text.strip() if se.text else ''} ({se.msg})")
            if errors:
                return {"success": False, "output": "\n".join(errors)}
            return {"success": True, "output": "Python files compiled successfully."}

        elif self.stack == "java":
            # 1. Check for dependency regression if original pom.xml exists
            if self.original_workspace_path:
                orig_pom = os.path.join(self.original_workspace_path, "pom.xml")
                new_pom = os.path.join(self.workspace_path, "pom.xml")
                if os.path.exists(orig_pom) and os.path.exists(new_pom):
                    orig_deps = self._get_pom_dependencies(orig_pom)
                    new_deps = self._get_pom_dependencies(new_pom)
                    missing_deps = [d for d in orig_deps if d not in new_deps]
                    if missing_deps:
                        return {
                            "success": False,
                            "output": f"Dependency regression: The following dependencies were removed from pom.xml: {', '.join(missing_deps)}. You must preserve all existing dependencies."
                        }

            # 2. Run Maven compile if pom.xml exists
            if os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
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
                    res = self._run_cmd_with_timeout(
                        [
                            "mvn", "clean", "compile",
                            "-Dmaven.compiler.showWarnings=true",
                            "-Dmaven.compiler.compilerArgument=-Xlint:rawtypes,unchecked",
                        ],
                        cwd=self.workspace_path,
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
                        if any(f.endswith(".java") for f in files):
                            classes_dir = os.path.join(self.workspace_path, "target", "classes")
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
                        return {"success": True, "output": "Maven compilation succeeded."}
                    error_output = f"Maven compilation failed:\n{res['stdout']}\n{res['stderr']}"
                    try:
                        from kriya.tools.resolver import enrich_java_compiler_errors
                        error_output = enrich_java_compiler_errors(error_output)
                    except Exception as ree:
                        logger.warning(f"Resolver failed to run: {ree}")
                    return {"success": False, "output": error_output}
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
                except Exception as e:
                    logger.warning(f"Failed to invoke mvn compile: {e}")

            # 2. Run Gradle compile if build.gradle exists
            if os.path.exists(os.path.join(self.workspace_path, "build.gradle")):
                try:
                    gradle_cmd = "./gradlew" if os.path.exists(os.path.join(self.workspace_path, "gradlew")) else "gradle"
                    res = self._run_cmd_with_timeout([gradle_cmd, "compileJava"], cwd=self.workspace_path)
                    if res["returncode"] == 0:
                        return {"success": True, "output": "Gradle compilation succeeded."}
                    return {"success": False, "output": f"Gradle compilation failed:\n{res['stdout']}\n{res['stderr']}"}
                except FileNotFoundError as e:
                    # Same reasoning as the mvn case above - don't silently fall
                    # through to the misleading raw javac fallback.
                    return {"success": False, "output": f"Failed to invoke {gradle_cmd} compileJava: {e}"}
                except Exception as e:
                    logger.warning(f"Failed to invoke gradle compileJava: {e}")
            
            # 3. Fallback to raw javac syntax check (for simple single-class projects)
            java_files = [os.path.join(self.workspace_path, f) for f in files if f.endswith(".java")]
            if not java_files:
                return {"success": True, "output": "No Java files to compile."}
                
            cmd = ["javac", "-proc:none", "-d", os.path.join(self.workspace_path, "build")]
            cmd.extend(java_files)
            os.makedirs(os.path.join(self.workspace_path, "build"), exist_ok=True)
            
            try:
                res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                if res["returncode"] != 0:
                    error_output = f"Java compilation failed:\n{res['stderr']}"
                    try:
                        from kriya.tools.resolver import enrich_java_compiler_errors
                        error_output = enrich_java_compiler_errors(error_output)
                    except Exception as ree:
                        logger.warning(f"Resolver failed to run: {ree}")
                    return {"success": False, "output": error_output}
                return {"success": True, "output": "Java classes compiled successfully."}
            except Exception as e:
                return {"success": False, "output": f"Javac compilation tool invocation failed: {e}"}

        elif self.stack == "ruby":
            errors = []
            for f in files:
                if f.endswith(".rb"):
                    full = os.path.join(self.workspace_path, f)
                    if os.path.exists(full):
                        try:
                            res = self._run_cmd_with_timeout(["ruby", "-c", full], cwd=self.workspace_path)
                            if res["returncode"] != 0:
                                errors.append(f"Ruby syntax error in {f}:\n{res['stderr']}")
                        except Exception as e:
                            return {"success": False, "output": f"Ruby runtime execution failed: {e}"}
            if errors:
                return {"success": False, "output": "\n".join(errors)}
            return {"success": True, "output": "Ruby files syntax check passed."}

        # "unknown" - no Java/Python/Ruby markers matched. success: True so an
        # unsupported stack doesn't fail the retry loop forever over a gate that
        # was never going to pass, but the message is honest about zero real
        # validation having happened - never claim a check that didn't run.
        return {
            "success": True,
            "output": (
                "No compile check available: workspace does not match a supported "
                "stack (Java/Python/Ruby). Quality gate skipped, NOT confirmed to compile."
            ),
        }

    def run_tests(self, target_test: Optional[str] = None) -> Dict[str, Any]:
        """Runs tech-stack specific test execution suite."""
        try:
            if self.stack == "python":
                # Explicitly (re-)add the workspace root and, if present, its src/ layout
                # directory to sys.path after stripping the auto-inserted CWD entry (which
                # protects pytest's own imports - and everything pytest itself transitively
                # imports, e.g. stdlib random -> math - from being shadowed by an arbitrary
                # file in the workspace root, such as a generated math.py). Without this,
                # whether a generated test's import (`from pkg.module import x` vs. src-layout
                # `from module import x`) resolves is left entirely up to pytest's own
                # rootdir-walk, which only adds the workspace root when every directory
                # between it and the test file has an __init__.py - something the Developer
                # Agent creates inconsistently across retries/models. Appending (not
                # prepending) both known-good roots makes either import convention resolve
                # deterministically without reopening the shadowing risk: stdlib/installed
                # packages earlier in sys.path still win, so this only kicks in as a fallback.
                extra_roots = [self.workspace_path]
                src_dir = os.path.join(self.workspace_path, "src")
                if os.path.isdir(src_dir):
                    extra_roots.append(src_dir)
                # The Developer Agent keeps inventing a Maven/Gradle-style
                # src/main/<lang> (and src/test/<lang>) nesting for pure-Python
                # goals despite an explicit, correctly-worded prompt instruction
                # against it (ECOSYSTEM_INVARIANT_HEADER in workflow.py names this
                # exact anti-pattern verbatim) - confirmed live, 2026-08-07
                # (python_task_tracker): 7/7 attempts across TWO different models
                # wrote to src/main/python/, and every one of the resulting
                # ModuleNotFoundError failures was misdiagnosed by the model's own
                # fix-analysis as a sys.path problem rather than a layout problem,
                # so retries never escaped the pattern. A genuine prompting-ceiling
                # case, not an under-specified one - the same class already hit for
                # Ignite's Security Manager flag (see _strip_jdk_incompatible_jvm_
                # flags). Rather than keep fighting the model's habit at the prompt
                # level, make test collection robust to the specific nesting shape
                # actually observed - the same "meet the model where it is"
                # philosophy already used for Java's classpath-based test-class
                # resolution (java_test_class in run_tests below, resolves
                # regardless of package/src-root convention). Conditioned on the
                # directory actually existing, so this is a no-op for every
                # correctly-flat-laid-out project.
                for maven_style_root in ("src/main/python", "src/main", "src/test/python", "src/test"):
                    candidate = os.path.join(self.workspace_path, *maven_style_root.split("/"))
                    if os.path.isdir(candidate):
                        extra_roots.append(candidate)

                # A goal needing a real third-party package (e.g. Django) can only
                # ever pass this gate if that package happens to already be
                # installed in KRIYA'S OWN interpreter (sys.executable) - there was
                # no per-project dependency install step for Python, unlike Ruby's
                # `bundle install` fix. Confirmed live, 2026-08-07
                # (django_healthcheck_gap): every attempt failed identically with
                # ModuleNotFoundError: No module named 'django', regardless of the
                # generated code's correctness - a structurally unwinnable gate.
                python_interpreter, install_error = self._resolve_python_interpreter()
                if install_error:
                    return {"success": False, "output": install_error}

                cmd = [
                    python_interpreter,
                    "-c",
                    "import sys, os; "
                    "sys.path = [p for p in sys.path if p and os.path.abspath(p) != os.path.abspath('.')]; "
                    f"sys.path.extend({extra_roots!r}); "
                    "import pytest; sys.exit(pytest.main(sys.argv[1:]))",
                    "--"
                ]
                if target_test:
                    cmd.append(target_test)
                res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                return {"success": res["returncode"] in (0, 5), "output": res["stdout"] + "\n" + res["stderr"]}
 
            elif self.stack == "java":
                # target_test comes from extract_target_test() as a raw file path
                # (e.g. "src/test/java/com/example/ProtocolTest.java") - Maven's
                # -Dtest= and Gradle's --tests both expect a class name, not a
                # path, and silently match nothing if given one. Confirmed live,
                # 2026-08-07 (kriya-protocol-parser-app): passing the raw path
                # verbatim made the targeted-test gate structurally unable to
                # ever pass ("No tests matching pattern ... were executed!"),
                # burning the entire retry budget on a Kriya-side invocation bug
                # the generated code had no way to fix - the model's own fix-
                # analysis correctly noticed the pattern was wrong every time but
                # could only ever regenerate ITS OWN files, not Kriya's command
                # construction. The bare (unqualified) class name is enough -
                # both Surefire and Gradle's test filter resolve it via classpath
                # scanning regardless of package, avoiding any need to guess the
                # src-root convention (which isn't always the same layout - see
                # the src/main/python vs flat layout drift documented elsewhere
                # in this project).
                java_test_class = (
                    os.path.splitext(os.path.basename(target_test))[0] if target_test else None
                )
                if os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
                    cmd = ["mvn", "test"]
                    if java_test_class:
                        cmd.append(f"-Dtest={java_test_class}")
                    res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                    return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}
                elif os.path.exists(os.path.join(self.workspace_path, "build.gradle")):
                    gradle_cmd = "./gradlew" if os.path.exists(os.path.join(self.workspace_path, "gradlew")) else "gradle"
                    cmd = [gradle_cmd, "test"]
                    if java_test_class:
                        cmd.extend(["--tests", java_test_class])
                    res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                    return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}
                return {"success": True, "output": "No Java test config found (pom.xml/gradle). Skipping."}
 
            elif self.stack == "ruby":
                # A fresh sandbox never has gems installed, so `bundle exec rspec`
                # fails with "bundler: command not found: rspec" regardless of what
                # the model writes - confirmed live (eval harness batch
                # 20260804-115621) burning a full retry budget on correct Ruby code
                # for exactly this reason. `bundle install` needs a real Gemfile to
                # act on; without one, skip straight to the exec attempt below,
                # which still gets a chance via its own fallback.
                if os.path.exists(os.path.join(self.workspace_path, "Gemfile")):
                    # --path installs gems into a project-local, sandbox-writable
                    # directory instead of the host Ruby's own gem path - confirmed
                    # live (eval harness batch 20260804-151655) that a plain
                    # `bundle install` fails outright on an unmodified macOS system
                    # Ruby (no rbenv/rvm), whose gem directory is permission-
                    # protected and requires sudo the install can never provide
                    # non-interactively (Bundler::SudoNotPermittedError). --path is
                    # portable across Bundler 1.x/2.x and, once set, is remembered
                    # via .bundle/config for the `bundle exec` call below too - no
                    # other plumbing needed.
                    install_res = self._run_cmd_with_timeout(
                        ["bundle", "install", "--path", "vendor/bundle"], cwd=self.workspace_path
                    )
                    if install_res["returncode"] != 0:
                        return {
                            "success": False,
                            "output": f"'bundle install' failed:\n{install_res['stdout']}\n{install_res['stderr']}",
                        }
                cmd = ["bundle", "exec", "rspec"]
                if target_test:
                    cmd.append(target_test)
                try:
                    res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                except Exception as e:
                    logger.debug(f"'bundle exec rspec' failed, falling back to plain 'rspec': {e}")
                    cmd = ["rspec"]
                    if target_test:
                        cmd.append(target_test)
                    res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}

            # "unknown" stack - same reasoning as run_compile_check: succeed so
            # the retry loop doesn't fail forever on a gate that can never run,
            # but say plainly that nothing was actually tested.
            return {
                "success": True,
                "output": (
                    "No test runner available: workspace does not match a supported "
                    "stack (Java/Python/Ruby). Quality gate skipped, NOT confirmed to pass."
                ),
            }

        except Exception as e:
            return {"success": False, "output": f"Failed to execute local test suite: {e}"}

        return {"success": True, "output": "Stack test execution skipped."}

    def _substitute_python_interpreter(
        self, commands: List[List[str]]
    ) -> Tuple[Optional[List[List[str]]], Optional[str]]:
        """Rewrites any command's executable (index 0) from a bare "python" or
        Kriya's own sys.executable to the isolated project-local venv's
        interpreter (_resolve_python_interpreter()) when this workspace is
        Python and needs one - shared by run_app()/run_app_sequence() so
        Runtime Verification gets the SAME isolated, dependency-installed
        interpreter run_tests() already resolves for the test gate, instead
        of diverging and hitting the identical missing-dependency failure one
        gate later. Confirmed live, 2026-08-07 (django_healthcheck_gap, after
        the RepositoryAnalyzer manage.py-hallucination fix let this goal
        reach Runtime Verification for the first time): it did exactly that -
        'No module named django' via sys.executable, despite run_tests()'s
        own isolated venv already existing for this same workspace.

        A no-op (commands unchanged) for every non-Python stack, and for a
        Python workspace with no real requirements.txt.

        Returns (commands, None) on success, or (None, error_message) if
        resolving the interpreter itself hit a real pip install failure -
        treated as a hard failure here too (matching run_tests()'s own
        handling), since every command in the sequence would fail
        identically against a confusing 'module not found' symptom
        otherwise, rather than the real, potentially code-fixable
        dependency problem underneath it."""
        if self.stack != "python":
            return commands, None
        interpreter, install_error = self._resolve_python_interpreter()
        if install_error:
            return None, install_error
        rewritten = [
            ([interpreter] + cmd[1:]) if cmd and cmd[0] in ("python", sys.executable) else cmd
            for cmd in commands
        ]
        return rewritten, None

    def run_app(self, command: List[str], timeout: int = 90) -> Dict[str, Any]:
        """Executes an already-resolved run command for a self-terminating/batch entrypoint
        (not a long-running server) inside the sandboxed workspace, and returns the raw
        execution result. Does not itself judge whether the output is CORRECT - only
        whether the process completed within the timeout and its exit code. Callers
        (the Runtime Verification Gate) are responsible for grading the captured output
        against the goal."""
        if not command:
            return {"success": False, "timed_out": False, "returncode": None, "output": "No run command provided."}
        commands, install_error = self._substitute_python_interpreter([command])
        if install_error:
            return {"success": False, "timed_out": False, "returncode": None, "output": install_error}
        command = commands[0]
        try:
            res = self._run_cmd_with_timeout(command, cwd=self.workspace_path, timeout=timeout)
        except Exception as e:
            return {"success": False, "timed_out": False, "returncode": None, "output": f"Failed to execute run command: {e}"}
        return {
            "success": res["returncode"] == 0 and not res["timeout"],
            "timed_out": res["timeout"],
            "returncode": res["returncode"],
            "output": res["stdout"] + "\n" + res["stderr"],
        }

    def run_app_sequence(self, commands: List[List[str]], timeout: int = 90) -> Dict[str, Any]:
        """Runs an ORDERED sequence of already-resolved commands, one after another, in the
        same workspace directory - so state one command creates (a written file, a database)
        persists for the next. Some goals can only be verified this way: a goal like "add an
        item, then list items" is unobservable from a single invocation, since a CLI's
        no-argument entrypoint can only ever print help/usage - confirmed live as a real,
        previously-unnoticed Runtime Verification Gate failure mode: judge() inferring a
        single no-argument command for a goal that actually needed two sequential invocations
        made every attempt fail with "only shows the help message", which then got
        misread as a code bug (wasting the entire retry budget) when the generated code was
        actually correct the whole time.

        Every command in the sequence runs regardless of an earlier step's exit code, so a
        later step's output is still available as evidence for the grader even if an earlier
        one failed for an unrelated reason - except a timeout, which stops the sequence
        immediately (a hung process means continuing serves no purpose). Each command gets
        its own timeout budget, not a shared one."""
        if not commands:
            return {"success": False, "timed_out": False, "returncode": None, "output": "No run commands provided."}

        commands, install_error = self._substitute_python_interpreter(commands)
        if install_error:
            return {"success": False, "timed_out": False, "returncode": None, "output": install_error}

        output_parts = []
        overall_success = True
        any_timed_out = False
        last_returncode = None
        for i, command in enumerate(commands, 1):
            step_label = f"=== Step {i}/{len(commands)}: {' '.join(command)} ==="
            try:
                res = self._run_cmd_with_timeout(command, cwd=self.workspace_path, timeout=timeout)
            except Exception as e:
                output_parts.append(f"{step_label}\nFailed to execute: {e}")
                overall_success = False
                last_returncode = None
                break
            output_parts.append(f"{step_label}\n{res['stdout']}\n{res['stderr']}")
            last_returncode = res["returncode"]
            if res["timeout"]:
                any_timed_out = True
                overall_success = False
                break
            if res["returncode"] != 0:
                overall_success = False

        return {
            "success": overall_success and not any_timed_out,
            "timed_out": any_timed_out,
            "returncode": last_returncode,
            "output": "\n\n".join(output_parts),
        }


_JAVA_VERSION_PATTERN = re.compile(r'version\s+"?(\d+)(?:\.\d+)*"?')
_MVN_JAVA_VERSION_PATTERN = re.compile(r"Java version:\s*(\d+)(?:\.\d+)*")


def check_java_toolchain() -> Dict[str, Any]:
    """Resolves the actual JDK major version 'java' and 'mvn' will each invoke -
    not always the same JVM. Some Maven installs (e.g. Homebrew's) set their own
    JAVA_HOME independently of whatever 'java' on PATH resolves to, so a machine
    can have a working, version-appropriate 'java' while 'mvn' silently builds
    and runs against a completely different major version. Confirmed live as a
    real, silent failure mode during golden-use-case validation: a JVM flag
    correct for the JDK a manual 'java -version' check found (Temurin 17.0.10)
    was a fatal VM-startup error under the JDK 'mvn' itself actually resolved
    (Homebrew's, silently upgraded to 26 - JDK 24+ removed the Security Manager
    entirely, so a flag that used to just be advisory became fatal). Returns
    found/version for each tool (None if not on PATH or unparseable) plus
    'mismatch' when both are found and their major versions differ."""
    result: Dict[str, Any] = {
        "java_found": False,
        "java_version": None,
        "mvn_found": False,
        "mvn_java_version": None,
        "mismatch": False,
    }
    if shutil.which("java"):
        try:
            proc = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=5)
            m = _JAVA_VERSION_PATTERN.search(proc.stdout + proc.stderr)
            if m:
                result["java_found"] = True
                result["java_version"] = m.group(1)
        except Exception as e:
            logger.debug(f"Failed to resolve 'java -version': {e}")

    if shutil.which("mvn"):
        try:
            proc = subprocess.run(["mvn", "-version"], capture_output=True, text=True, timeout=10)
            m = _MVN_JAVA_VERSION_PATTERN.search(proc.stdout + proc.stderr)
            if m:
                result["mvn_found"] = True
                result["mvn_java_version"] = m.group(1)
        except Exception as e:
            logger.debug(f"Failed to resolve 'mvn -version': {e}")

    if result["java_version"] and result["mvn_java_version"] and result["java_version"] != result["mvn_java_version"]:
        result["mismatch"] = True

    return result
