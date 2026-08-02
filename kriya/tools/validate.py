import logging
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from kriya.config.config import AutonomyConfig
from kriya.tools.sandbox import build_restricted_env, posix_resource_limits_preexec_fn

logger = logging.getLogger(__name__)

class PolymorphicValidator:
    """Detects workspace language stack and executes syntactic compile checks and dynamic test runners."""

    def __init__(
        self,
        workspace_path: str,
        original_workspace_path: Optional[str] = None,
        autonomy_cfg: Optional[AutonomyConfig] = None,
    ) -> None:
        self.workspace_path = os.path.abspath(workspace_path)
        self.original_workspace_path = os.path.abspath(original_workspace_path) if original_workspace_path else None
        self.autonomy_cfg = autonomy_cfg or AutonomyConfig()
        self.stack = self._detect_stack()

    def _get_pom_dependencies(self, pom_path: str) -> List[str]:
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

    def _detect_stack(self) -> str:
        """Determines if the workspace uses Python, Java, or Ruby."""
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
            
        # Default fallback to Python
        return "python"

    def _run_cmd_with_timeout(self, cmd: List[str], cwd: str, timeout: int = 300) -> Dict[str, Any]:
        env = None
        preexec_fn = None
        if self.autonomy_cfg.sandbox_execution:
            env = build_restricted_env(self.autonomy_cfg.sandbox_env_allowlist)
            preexec_fn = posix_resource_limits_preexec_fn(
                self.autonomy_cfg.sandbox_cpu_seconds, self.autonomy_cfg.sandbox_memory_mb
            )
        try:
            res = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                env=env, preexec_fn=preexec_fn,
            )
            return {"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr, "timeout": False}
        except subprocess.TimeoutExpired as te:
            stdout = te.stdout.decode("utf-8", errors="replace") if isinstance(te.stdout, bytes) else (te.stdout or "")
            stderr = te.stderr.decode("utf-8", errors="replace") if isinstance(te.stderr, bytes) else (te.stderr or "")
            return {
                "returncode": -1, 
                "stdout": stdout, 
                "stderr": stderr + f"\n[TIMEOUT] Command timed out after {timeout} seconds.", 
                "timeout": True
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
                    res = self._run_cmd_with_timeout(["mvn", "clean", "compile"], cwd=self.workspace_path)
                    if res["returncode"] == 0:
                        return {"success": True, "output": "Maven compilation succeeded."}
                    error_output = f"Maven compilation failed:\n{res['stdout']}\n{res['stderr']}"
                    try:
                        from kriya.tools.resolver import enrich_java_compiler_errors
                        error_output = enrich_java_compiler_errors(error_output)
                    except Exception as ree:
                        logger.warning(f"Resolver failed to run: {ree}")
                    return {"success": False, "output": error_output}
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

        return {"success": True, "output": "Unsupported tech stack compilation check skipped."}

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
                if os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
                    cmd = ["mvn", "test"]
                    if target_test:
                        cmd.append(f"-Dtest={target_test}")
                    res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                    return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}
                elif os.path.exists(os.path.join(self.workspace_path, "build.gradle")):
                    gradle_cmd = "./gradlew" if os.path.exists(os.path.join(self.workspace_path, "gradlew")) else "gradle"
                    cmd = [gradle_cmd, "test"]
                    if target_test:
                        cmd.extend(["--tests", target_test])
                    res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                    return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}
                return {"success": True, "output": "No Java test config found (pom.xml/gradle). Skipping."}
 
            elif self.stack == "ruby":
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
