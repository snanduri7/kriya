import os
import subprocess
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class PolymorphicValidator:
    """Detects workspace language stack and executes syntactic compile checks and dynamic test runners."""

    def __init__(self, workspace_path: str) -> None:
        self.workspace_path = os.path.abspath(workspace_path)
        self.stack = self._detect_stack()

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
            # Run javac syntax compile check on Java files
            java_files = [os.path.join(self.workspace_path, f) for f in files if f.endswith(".java")]
            if not java_files:
                return {"success": True, "output": "No Java files to compile."}
                
            # Execute javac -help-like syntactic check
            cmd = ["javac", "-proc:none", "-d", os.path.join(self.workspace_path, "build")]
            cmd.extend(java_files)
            
            # Ensure build directory exists
            os.makedirs(os.path.join(self.workspace_path, "build"), exist_ok=True)
            
            try:
                res = subprocess.run(cmd, cwd=self.workspace_path, capture_output=True, text=True)
                if res.returncode != 0:
                    return {"success": False, "output": f"Java compilation failed:\n{res.stderr}"}
                return {"success": True, "output": "Java classes compiled successfully."}
            except Exception as e:
                # If javac is not installed, fallback to Maven if pom.xml exists
                if os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
                    try:
                        res = subprocess.run(["mvn", "compile"], cwd=self.workspace_path, capture_output=True, text=True)
                        return {"success": res.returncode == 0, "output": res.stdout + "\n" + res.stderr}
                    except Exception:
                        pass
                return {"success": False, "output": f"Javac compilation tool invocation failed: {e}"}

        elif self.stack == "ruby":
            errors = []
            for f in files:
                if f.endswith(".rb"):
                    full = os.path.join(self.workspace_path, f)
                    if os.path.exists(full):
                        try:
                            res = subprocess.run(["ruby", "-c", full], cwd=self.workspace_path, capture_output=True, text=True)
                            if res.returncode != 0:
                                errors.append(f"Ruby syntax error in {f}:\n{res.stderr}")
                        except Exception as e:
                            return {"success": False, "output": f"Ruby runtime execution failed: {e}"}
            if errors:
                return {"success": False, "output": "\n".join(errors)}
            return {"success": True, "output": "Ruby files syntax check passed."}

        return {"success": True, "output": "Unsupported tech stack compilation check skipped."}

    def run_tests(self) -> Dict[str, Any]:
        """Runs tech-stack specific test execution suite."""
        try:
            if self.stack == "python":
                venv_pytest = os.path.join(self.workspace_path, ".venv", "bin", "pytest")
                cmd = [venv_pytest] if os.path.exists(venv_pytest) else ["pytest"]
                res = subprocess.run(cmd, cwd=self.workspace_path, capture_output=True, text=True)
                return {"success": res.returncode == 0, "output": res.stdout + "\n" + res.stderr}

            elif self.stack == "java":
                if os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
                    res = subprocess.run(["mvn", "test"], cwd=self.workspace_path, capture_output=True, text=True)
                    return {"success": res.returncode == 0, "output": res.stdout + "\n" + res.stderr}
                elif os.path.exists(os.path.join(self.workspace_path, "build.gradle")):
                    gradle_cmd = "./gradlew" if os.path.exists(os.path.join(self.workspace_path, "gradlew")) else "gradle"
                    res = subprocess.run([gradle_cmd, "test"], cwd=self.workspace_path, capture_output=True, text=True)
                    return {"success": res.returncode == 0, "output": res.stdout + "\n" + res.stderr}
                return {"success": True, "output": "No Java test config found (pom.xml/gradle). Skipping."}

            elif self.stack == "ruby":
                cmd = ["bundle", "exec", "rspec"]
                # Fallback if bundler is not used
                try:
                    res = subprocess.run(cmd, cwd=self.workspace_path, capture_output=True, text=True)
                except Exception:
                    res = subprocess.run(["rspec"], cwd=self.workspace_path, capture_output=True, text=True)
                return {"success": res.returncode == 0, "output": res.stdout + "\n" + res.stderr}

        except Exception as e:
            return {"success": False, "output": f"Failed to execute local test suite: {e}"}

        return {"success": True, "output": "Stack test execution skipped."}
