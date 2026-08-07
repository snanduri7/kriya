import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from kriya.config.config import AutonomyConfig
from kriya.tools.sandbox import build_restricted_env, posix_resource_limits_preexec_fn

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

    def _get_pom_dependencies(self, pom_path: str) -> List[str]:
        return get_pom_dependencies(pom_path)

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

    def _run_cmd_with_timeout(self, cmd: List[str], cwd: str, timeout: int = 300) -> Dict[str, Any]:
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
        # start_new_session=True (POSIX setsid) puts the child in its own
        # process group - required for the timeout-kill below to actually
        # work for a command like `mvn exec:exec`, which forks its own
        # separate `java` child process to run the app (that's the entire
        # point of exec:exec over exec:java). Confirmed live, 2026-08-03:
        # subprocess.run()'s own built-in timeout handling only kills the
        # DIRECT child (mvn) - the grandchild java process it forked
        # survived the kill, kept an embedded Qpid broker bound to its port
        # for over an hour, and silently broke a LATER, completely
        # unrelated validation run that happened to need the same port.
        # Killing the whole process group on timeout is the only way to
        # actually terminate what a command like this really started.
        process = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env, preexec_fn=preexec_fn, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr, "timeout": False}
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # already exited between the timeout firing and this kill
            stdout, stderr = process.communicate()  # reap the process, collect whatever output exists
            return {
                "returncode": -1,
                "stdout": stdout,
                "stderr": stderr + f"\n[TIMEOUT] Command timed out after {timeout} seconds.",
                "timeout": True,
            }

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
                cmd = [
                    sys.executable,
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

    def run_app(self, command: List[str], timeout: int = 90) -> Dict[str, Any]:
        """Executes an already-resolved run command for a self-terminating/batch entrypoint
        (not a long-running server) inside the sandboxed workspace, and returns the raw
        execution result. Does not itself judge whether the output is CORRECT - only
        whether the process completed within the timeout and its exit code. Callers
        (the Runtime Verification Gate) are responsible for grading the captured output
        against the goal."""
        if not command:
            return {"success": False, "timed_out": False, "returncode": None, "output": "No run command provided."}
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
