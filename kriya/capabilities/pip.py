"""Pip build adapter: the Python project markers, compile gate, pytest gate
and output layout, moved unchanged from kriya/tools/validate.py (Capability
Adapters R1, Python slice).

Every command runs through the validator handle (``v._run_cmd_with_timeout``:
containment, resource limits, gate binding) and the interpreter comes from the
validator's own resolution (``v._resolve_python_interpreter``: the project's
isolated, dependency-installed virtualenv for a requirements.txt or
pyproject.toml, else the default interpreter); this module never starts a
process itself. Poetry/Pipenv-specific dependency installation is not part of
R1 (a Pipfile or setup.* marks the project; dependencies install only from
requirements.txt / pyproject.toml, as before).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from kriya.capabilities.ports import BuildAdapter

MARKERS = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile")

# FS-1A: pytest prints "generated xml file: <path>" after the run. The path
# is unique per invocation, and the console output is gate evidence that
# reaches retry prompts and the no-progress fingerprint (measured: T6's
# repeated-vector detection saw a "new" failure every attempt). The launcher
# silences only that one summary line, so the output stays byte-identical to
# a run without the report; the report itself is still written.
_SILENT_REPORT_SUMMARY = (
    "\ntry:\n"
    "    import _pytest.junitxml as _kriya_junitxml\n"
    "    _kriya_junitxml.LogXML.pytest_terminal_summary = lambda *args, **kwargs: None\n"
    "except Exception:\n"
    "    pass\n"
)


class PipBuildAdapter(BuildAdapter):
    build_system = "pip"
    language = "python"
    tools = frozenset({"pytest", "py.test"})

    def invokes(self, tool: str) -> bool:
        """Any Python interpreter (python, python3, python3.12, ...) or pytest."""
        return tool.startswith("python") or tool in self.tools

    def detects(self, workspace_root: str) -> bool:
        return any(os.path.exists(os.path.join(workspace_root, marker)) for marker in MARKERS)

    def output_roots(self, cmd: List[str], cwd: str) -> List[str]:
        """``__pycache__/`` of every directory holding ``.py`` sources (PEP
        3147), pytest's ``.pytest_cache/`` and the directory of the
        ``--junitxml`` report the command names."""
        from kriya.tools.validate import _project_dirs

        roots = ([os.path.join(d, "__pycache__") for d in _project_dirs(
            cwd, lambda files: any(name.endswith(".py") for name in files))]
                 + [os.path.join(cwd, ".pytest_cache")])
        # FS-1A: the JUnit XML destination this exact command names (Kriya's own).
        roots += [os.path.join(cwd, os.path.dirname(arg.split("=", 1)[1]))
                  for arg in cmd if arg.startswith("--junitxml=")]
        return roots

    def compile(self, v: Any, files: List[str], *, deadline: Optional[float]) -> Optional[Dict[str, Any]]:
        """A syntax check of the changed ``.py`` files: ``python3 -m
        py_compile`` inside containment, else an in-process ``compile()``.
        Always decides (``deadline`` is unused: nothing here is long-running)."""
        del deadline
        if v.autonomy_cfg.contained_execution_required:
            python_files = [
                os.path.relpath(os.path.join(v.workspace_path, f), v.workspace_path)
                for f in files
                if f.endswith(".py") and os.path.isfile(os.path.join(v.workspace_path, f))
            ]
            if not python_files:
                return {"success": True, "output": "No Python files to compile."}
            res = v._run_cmd_with_timeout(
                ["python3", "-m", "py_compile", *python_files], cwd=v.workspace_path,
            )
            return v._validation_result(
                res["returncode"] == 0,
                "Python files compiled successfully." if res["returncode"] == 0 else res["stdout"] + "\n" + res["stderr"],
                res,
            )
        errors = []
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(v.workspace_path, f)
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

    def run_tests(self, v: Any, targets: Optional[List[str]]) -> Dict[str, Any]:
        """pytest through the resolved interpreter; ``targets`` each become
        their own argv entry (never joined into one string)."""
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
        # CONTAINED-PYTHON-TEST-GATE-001 (2026-10-07, blind cohort T5): the
        # roots are passed as names RELATIVE to the child's cwd and resolved
        # inside the child. The child's cwd is the workspace in both modes
        # (host: cwd=workspace_path; OCI: `-w /kriya/workspace`), so host
        # mode resolves to the same roots as before (realpath-equal), while
        # a contained run no longer embeds HOST absolute paths that do not
        # exist at the container mount - which made `extra/` test modules
        # unable to import the package (session aborted at collection, zero
        # tests on baseline and candidate). The existence checks stay on the
        # host side: the mount is the same tree.
        extra_roots = ["."]
        src_dir = os.path.join(v.workspace_path, "src")
        if os.path.isdir(src_dir):
            extra_roots.append("src")
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
            candidate = os.path.join(v.workspace_path, *maven_style_root.split("/"))
            if os.path.isdir(candidate):
                extra_roots.append(maven_style_root)

        # A goal needing a real third-party package (e.g. Django) can only
        # ever pass this gate if that package happens to already be
        # installed in KRIYA'S OWN interpreter (sys.executable) - there was
        # no per-project dependency install step for Python, unlike Ruby's
        # `bundle install` fix. Confirmed live, 2026-08-07
        # (django_healthcheck_gap): every attempt failed identically with
        # ModuleNotFoundError: No module named 'django', regardless of the
        # generated code's correctness - a structurally unwinnable gate.
        python_interpreter, install_error = v._resolve_python_interpreter()
        provenance = getattr(v, "python_interpreter_provenance", None)
        environment = getattr(v, "python_environment_error", None)
        if environment is not None:
            # VERIFICATION-UNIT-ENV-FALLBACK-001: the required environment could not be created - typed
            # (``environment_reason_code``, the verification_infrastructure_failure stop), nothing runs.
            code, message = environment
            return {"success": False, "output": message, "environment_reason_code": code, "python_interpreter": provenance}
        if install_error:
            result = {"success": False, "output": install_error}
            if provenance is not None:  # a resolver stubbed by a test records none: the result shape stays as before
                result["python_interpreter"] = provenance
            return result

        binding = getattr(v, "test_report_binding", None)
        report_argument = binding.pytest_argument if binding is not None else None
        cmd = [
            python_interpreter,
            "-c",
            "import sys, os; "
            "sys.path = [p for p in sys.path if p and os.path.abspath(p) != os.path.abspath('.')]; "
            f"sys.path.extend([os.path.abspath(r) for r in {extra_roots!r}]); "
            + (_SILENT_REPORT_SUMMARY if report_argument else "")
            + "import pytest; sys.exit(pytest.main(sys.argv[1:]))",
        ]
        if report_argument:
            # FS-1A: the Kriya-owned per-invocation JUnit XML destination. An
            # option, so before "--" (after it pytest reads a file path - measured).
            cmd.append(report_argument)
        cmd.append("--")
        if targets:
            # Each target its OWN argv entry - never joined into one
            # string (see this method's own docstring: a single
            # space-joined token is not multiple paths to pytest,
            # empirically confirmed, not assumed).
            cmd.extend(targets)
        res = v._run_cmd_with_timeout(cmd, cwd=v.workspace_path)
        if binding is not None:
            binding.observe(res)
        result = v._validation_result(
            res["returncode"] in (0, 5), res["stdout"] + "\n" + res["stderr"], res,
        )
        if provenance is not None:
            result["python_interpreter"] = provenance  # which environment produced this verdict (evidence only)
        return result
