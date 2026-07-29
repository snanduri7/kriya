import os
import subprocess
import logging
import sys
from typing import Dict, Any, List, Optional

import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

class PolymorphicValidator:
    """Detects workspace language stack and executes syntactic compile checks and dynamic test runners."""

    def __init__(self, workspace_path: str, original_workspace_path: Optional[str] = None, sandbox_execution: bool = False) -> None:
        self.workspace_path = os.path.abspath(workspace_path)
        self.original_workspace_path = os.path.abspath(original_workspace_path) if original_workspace_path else None
        self.sandbox_execution = sandbox_execution
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
        try:
            res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
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

    def _run_in_docker(self, cmd: List[str], timeout: int = 300) -> Dict[str, Any]:
        """Runs the specified command inside an isolated Docker container."""
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{self.workspace_path}:/workspace",
            "-w", "/workspace"
        ]
        
        if self.stack == "java":
            docker_cmd.append("maven:3.9-eclipse-temurin-17")
        elif self.stack == "ruby":
            docker_cmd.append("ruby:3.2")
        else:
            docker_cmd.append("python:3.11")
            
        inner_cmd = " ".join(cmd)
        docker_cmd.extend(["sh", "-c", inner_cmd])
        
        logger.info(f"Executing sandboxed command: {' '.join(docker_cmd)}")
        return self._run_cmd_with_timeout(docker_cmd, cwd=self.workspace_path, timeout=timeout)

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
                    cmd = ["mvn", "clean", "compile"]
                    res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
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
                    cmd = [gradle_cmd, "compileJava"]
                    res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                    if res["returncode"] == 0:
                        return {"success": True, "output": "Gradle compilation succeeded."}
                    return {"success": False, "output": f"Gradle compilation failed:\n{res['stdout']}\n{res['stderr']}"}
                except Exception as e:
                    logger.warning(f"Failed to invoke gradle compileJava: {e}")
            
            # 3. Fallback to raw javac syntax check (for simple single-class projects)
            if self.sandbox_execution:
                java_files = [os.path.join("/workspace", f) for f in files if f.endswith(".java")]
                build_dir = "/workspace/build"
            else:
                java_files = [os.path.join(self.workspace_path, f) for f in files if f.endswith(".java")]
                build_dir = os.path.join(self.workspace_path, "build")
                
            if not java_files:
                return {"success": True, "output": "No Java files to compile."}
                
            cmd = ["javac", "-proc:none", "-d", build_dir]
            cmd.extend(java_files)
            os.makedirs(os.path.join(self.workspace_path, "build"), exist_ok=True)
            
            try:
                res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
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
                            res = self._run_in_docker(["ruby", "-c", f]) if self.sandbox_execution else self._run_cmd_with_timeout(["ruby", "-c", full], cwd=self.workspace_path)
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
                python_bin = "python" if self.sandbox_execution else sys.executable
                cmd = [
                    python_bin,
                    "-c",
                    "import sys, os; sys.path = [p for p in sys.path if p and os.path.abspath(p) != os.path.abspath('.')]; import pytest; sys.exit(pytest.main(sys.argv[1:]))",
                    "--"
                ]
                if target_test:
                    cmd.append(target_test)
                res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                return {"success": res["returncode"] in (0, 5), "output": res["stdout"] + "\n" + res["stderr"]}
 
            elif self.stack == "java":
                if os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
                    cmd = ["mvn", "test"]
                    if target_test:
                        cmd.append(f"-Dtest={target_test}")
                    res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                    return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}
                elif os.path.exists(os.path.join(self.workspace_path, "build.gradle")):
                    gradle_cmd = "./gradlew" if os.path.exists(os.path.join(self.workspace_path, "gradlew")) else "gradle"
                    cmd = [gradle_cmd, "test"]
                    if target_test:
                        cmd.extend(["--tests", target_test])
                    res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                    return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}
                return {"success": True, "output": "No Java test config found (pom.xml/gradle). Skipping."}
 
            elif self.stack == "ruby":
                cmd = ["bundle", "exec", "rspec"]
                if target_test:
                    cmd.append(target_test)
                try:
                    res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                except Exception:
                    cmd = ["rspec"]
                    if target_test:
                        cmd.append(target_test)
                    res = self._run_in_docker(cmd) if self.sandbox_execution else self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                return {"success": res["returncode"] == 0, "output": res["stdout"] + "\n" + res["stderr"]}
 
        except Exception as e:
            return {"success": False, "output": f"Failed to execute local test suite: {e}"}
 
        return {"success": True, "output": "Stack test execution skipped."}
