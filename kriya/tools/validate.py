import functools
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from kriya.capabilities import BUILD_ADAPTERS, JAVA, PIP, PYTHON, build_adapter_for_tool
from kriya.config.config import AutonomyConfig
from kriya.policy.enforcement import enforce_hard_invariants
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy, extract_install_package_target
from kriya.policy.filesystem import is_within_scope, make_workspace_scope
from kriya.policy.model import ActionRequest, ActionType
from kriya.tools.containment import (
    ContainmentBackend,
    ContainmentProfile,
    ContainmentSetupError,
    NetworkAuthority,
    TrustClass,
    resolve_containment_backend,
)
from kriya.tools.containment_oci import MAVEN_CACHE_MOUNT, finalize_registry_acquisition_result
from kriya.tools.dependency_execution import (
    OfflineFailureKind,
    classify_maven_offline_failure_text,
    log_acquisition_outcome,
    maven_missing_artifact_signature,
)
from kriya.tools.process import ProcessController
from kriya.tools.sandbox import ResourcePlan, build_restricted_env, resource_plan
from kriya.tools.toolchain_identity import resolve_toolchain_selection

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


def get_pom_reactor_modules(pom_path: str) -> List[str]:
    """Parses a pom.xml's <modules><module>...</module></modules> entries -
    the declared child modules of a Maven reactor aggregator. Module-level
    (not a PolymorphicValidator method), same style/namespace-handling as
    get_pom_dependencies() above, so a caller needing this before/without a
    validator instance can reuse it identically.

    A genuine multi-module reactor's ROOT pom.xml (packaging=pom) has no
    compiled output of its own - each declared module compiles into its
    OWN <module>/target/classes, not <workspace_root>/target/classes (P7
    production-validation, 2026-09-07: confirmed live - Maven correctly
    reported BUILD SUCCESS for a real 3-module reactor while the compile-
    check gate's own workspace-root-only target/classes check rejected
    every single attempt, 16 times, regardless of code correctness, because
    it was written assuming a single-module layout). Degrades to an empty
    list (never raises) on any parse failure or a genuinely single-module
    project with no <modules> block at all - callers must treat an empty
    result as "not a reactor," not as "reactor with zero modules."""
    if not os.path.exists(pom_path):
        return []
    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        modules_elem = root.find(f"{ns}modules")
        if modules_elem is None:
            return []
        return [
            m.text.strip() for m in modules_elem.findall(f"{ns}module")
            if m.text and m.text.strip()
        ]
    except Exception as e:
        logger.warning(f"Failed to parse POM reactor modules at {pom_path}: {e}")
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


def _pyproject_dependencies(pyproject_path: str) -> List[str]:
    """Extracts PEP 621's [project.dependencies] array - plain requirement
    strings, the same shape a requirements.txt line already is - so a
    Python goal using "Python packaging conventions" (pyproject.toml, no
    requirements.txt) gets the same isolated, dependency-installed
    interpreter _resolve_python_interpreter() already gives a requirements.
    txt project. PRV-17 (2026-09-03) root cause: this project-marker was
    already recognized for STACK DETECTION (_detect_stack below already
    treats pyproject.toml as a Python marker) but never consulted for
    DEPENDENCY INSTALLATION - a goal declaring Django only in pyproject.toml
    got a bare interpreter with nothing installed, so `python -m pytest`
    failed with `ModuleNotFoundError: No module named 'django'` on every
    attempt regardless of how many times the Developer regenerated source.

    Degrades to an empty list (never raises) on any parse failure or a
    missing/malformed [project.dependencies] - matching _has_real_
    requirements' own "not worth the cost" posture and _ensure_project_venv's
    "infrastructure problem, not a code retry's job" posture for venv
    resolution: a caller that gets [] here falls through to sys.executable
    exactly as it did before this function existed, never a hard failure.
    Parsed with kriya/core/tomlcompat.py, so a 3.10 interpreter reads the
    same dependencies as 3.11+."""
    from kriya.core.tomlcompat import tomllib

    try:
        with open(pyproject_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return []
    dependencies = data.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list):
        return []
    return [dep for dep in dependencies if isinstance(dep, str) and dep.strip()]


_EXECUTION_EVIDENCE_KEYS = (
    # PRD-011: the attested contained toolchain (image, content digest,
    # declared and observed runtime/build-tool versions).
    "toolchain_identity",
    # PRD-012: the outbound-network authority the process ran under
    # (capability class, destinations, authority source, backend).
    "egress",
    # LINUX-JVM-RLIMIT-AS-001: how the CPU/memory budget was enforced
    # (strategy, budget, address-space limit, JVM options).
    "resources",
    # D6: untracked files the application created while it ran (paths only),
    # recorded and discarded - never candidate bytes.
    "runtime_artifacts",
)


def execution_evidence(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The containment evidence a validator/process result carries, as the
    gate-outcome fields that persist it with the verdict. Empty for an
    uncontained result - host mode never claims an identity or a network
    decision it did not make."""
    if not isinstance(result, dict):
        return {}
    return {key: result[key] for key in _EXECUTION_EVIDENCE_KEYS if result.get(key) is not None}



_GATE_WALK_SKIP = frozenset({".git", ".kriya", "node_modules", "target", "build", ".gradle", "__pycache__"})


def _project_dirs(cwd: str, holds: Callable[[List[str]], bool]) -> List[str]:
    """Directories under ``cwd`` (inclusive) whose files satisfy ``holds``."""
    found = []
    for directory, subdirs, files in os.walk(cwd):
        subdirs[:] = [d for d in subdirs if d not in _GATE_WALK_SKIP and not d.startswith(".")]
        if holds(files):
            found.append(directory)
    return found


def gate_output_roots(cmd: List[str], cwd: str) -> List[str]:
    """FILE-INTEGRITY-CONTRACT-001B: where the toolchain ``cmd`` invokes
    writes its own build output, by that tool's documented default layout -
    the only new files a verification gate running ``cmd`` may create:
    Maven: ``target/`` of every module (a directory holding ``pom.xml``);
    Gradle: ``build/`` of every project (``build.gradle[.kts]``) plus the
    root ``.gradle/`` cache; ``javac -d X``: exactly ``X``; Python (any
    interpreter, ``py_compile`` or pytest run): ``__pycache__/`` of every
    directory holding ``.py`` sources (PEP 3147) plus pytest's
    ``.pytest_cache/``. Anything else is repository content."""
    if not cmd:
        return []
    tool = os.path.basename(cmd[0])
    adapter = build_adapter_for_tool(tool)
    if adapter is not None:
        return adapter.output_roots(cmd, cwd)
    if tool == "javac" and "-d" in cmd[:-1]:
        return [cmd[cmd.index("-d") + 1]]
    return []


RUNTIME_VERIFICATION_GATE = "runtime_verification"


def _verification_gate(name: str):
    """A validator method that runs repository/toolchain code is a named
    verification gate. FILE-INTEGRITY-CONTRACT-001: when a tree is bound
    (``self.tree_binding``, file_integrity.VerificationTreeBinding), the gate
    is preceded by a check (a change between verification steps is caught
    before this gate runs on it) and followed - after the whole method,
    outside every handler inside it, so no internal ``except`` can turn it
    into an ordinary result - by the check
    that repository content and candidate are exactly what was bound; a
    change raises the typed VerificationTreeMutated stop. It is checked on
    the exception path too, so an error never hides a mutation."""
    # D6: only the gate that runs the candidate APPLICATION may leave untracked
    # runtime state; it is recorded and discarded (VerificationTreeBinding.check).
    ephemeral = name == RUNTIME_VERIFICATION_GATE

    def decorate(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            if self.tree_binding is not None:
                self.tree_binding.check(name, "before")
            previous, self._gate = self._gate, name
            try:
                result = method(self, *args, **kwargs)
            except BaseException:
                if self.tree_binding is not None:
                    self.tree_binding.check(name, ephemeral_untracked=ephemeral)
                raise
            finally:
                self._gate = previous
            if self.tree_binding is not None:
                artifacts = self.tree_binding.check(name, ephemeral_untracked=ephemeral)
                if ephemeral and isinstance(result, dict):
                    result["runtime_artifacts"] = artifacts
            return result
        return wrapper
    return decorate


class PolymorphicValidator:
    """Detects workspace language stack and executes syntactic compile checks and dynamic test runners."""

    def __init__(
        self,
        workspace_path: str,
        original_workspace_path: Optional[str] = None,
        autonomy_cfg: Optional[AutonomyConfig] = None,
        java_home_override: Optional[str] = None,
        authorized_dependency_removals: Optional[Iterable[str]] = None,
        toolchain_declaration_mutable: bool = False,
    ) -> None:
        self.workspace_path = os.path.abspath(workspace_path)
        self.original_workspace_path = os.path.abspath(original_workspace_path) if original_workspace_path else None
        self.autonomy_cfg = autonomy_cfg or AutonomyConfig()
        self.stack = self._detect_stack()
        self.toolchain_identity = None
        # FILE-INTEGRITY-CONTRACT-001: when a verification sequence binds its
        # tree (file_integrity.VerificationTreeBinding), every gate method
        # (_verification_gate) is followed by a check that the repository
        # content and candidate are exactly what was bound.
        self.tree_binding: Any = None
        self._gate = "command"
        # Whether this run may change the repository's toolchain declaration
        # (the caller's structured write scope - see
        # kriya/workflow/toolchain.py::toolchain_declaration_mutable). Set
        # before java_home_override, whose assignment resolves the identity.
        self._toolchain_declaration_mutable = toolchain_declaration_mutable
        self._java_home_override = None
        self.java_home_override = java_home_override
        # 'group:artifact' keys the caller has already determined are
        # explicitly authorized for removal by the goal (kriya/workflow/
        # migration.py::resolve_authorized_dependency_removals) - excluded
        # from the dependency-regression check below. Plain Optional[Iterable
        # [str]] rather than importing that module's own richer type here:
        # kriya/tools/ never depends on kriya/workflow/ anywhere else in this
        # codebase, and this is a deliberately thin, ownership-agnostic
        # boundary rather than a new layering exception. Found live, PRV-05
        # (2026-08-28, run 5): this check used to reject ANY pom.xml
        # dependency removal unconditionally, restoring Gson every time the
        # subtask that owns pom.xml tried to remove it - even though the
        # top-level goal explicitly authorized replacing it. Defaults to
        # None (no exclusions), so every caller that doesn't pass this stays
        # byte-for-byte unchanged - this remains a hard rejection for any
        # dependency removal that ISN'T backed by a resolved, goal-explicit
        # migration.
        self.authorized_dependency_removals = frozenset(authorized_dependency_removals or ())
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
        # MA4.4 (control-plane implementation plan) - audit-only. See
        # _run_cmd_with_timeout below; never consulted for enforcement.
        self.execution_policy = ExecutionPolicy()
        # PERF/DEPENDENCY-001 (2026-09-19): one instance of PolymorphicValidator
        # is constructed ONCE per attempt and reused across compile_check/
        # run_tests/run_app_sequence (kriya/workflow/attempt.py's own real
        # construction sites - e.g. line ~6914's `validator`, threaded through
        # every gate in that one attempt) - each of which independently calls
        # _resolve_python_interpreter() -> _ensure_project_venv(), which
        # unconditionally re-ran `pip install` every single call even when
        # nothing changed. This cache is scoped to THIS INSTANCE's own
        # lifetime only (never a module-level/global cache - a fresh
        # PolymorphicValidator, e.g. the next attempt or a different
        # workspace, always starts with an empty cache and performs its own
        # real acquisition) - see _venv_install_signature()'s own docstring
        # for how the key stays correctly invalidated on a real dependency-
        # manifest content change within that lifetime, not merely on
        # `install_args`' own (path-only, content-blind) shape.
        self._venv_install_cache: Dict[Tuple[Any, ...], Tuple[Optional[str], Optional[str]]] = {}
        # Observability counter (task's own "record before/after acquisition
        # count deterministically") - incremented ONLY on a real cache MISS,
        # i.e. only when the expensive pip install subprocess actually ran.
        self.venv_install_attempts = 0

    def _get_pom_dependencies(self, pom_path: str) -> List[str]:
        return get_pom_dependencies(pom_path)

    def _java_reactor_modules_missing_compiled_output(
        self, files: List[str], reactor_modules: List[str],
    ) -> List[str]:
        """P7 production-validation (2026-09-07): for a genuine Maven
        multi-module reactor, returns the distinct OWNING module names among
        `files`' real .java candidates whose own <module>/target/classes
        contains no .class file - the modules that actually needed to
        compile something for THIS candidate set, and didn't.

        Deliberately does NOT require every declared reactor module to
        contain .class files - a module can legitimately be interfaces-
        only, resources-only, packaging=pom, or otherwise produce no
        bytecode for this specific candidate set (confirmed live in the
        very repo this fix was built against - modular-app's own `app`
        aggregator module has no src/ at all). Only modules that actually
        OWN one of the given candidate .java files are checked - a stronger,
        more precise proof than "some declared module produced some class
        somewhere," and one that can never be satisfied by stale output left
        over in an unrelated module."""
        owning_modules = set()
        for f in files:
            if not f.endswith(".java"):
                continue
            for module in reactor_modules:
                prefix = module.rstrip("/") + "/"
                if f.startswith(prefix):
                    owning_modules.add(module)
                    break
        missing = []
        for module in sorted(owning_modules):
            classes_dir = os.path.join(self.workspace_path, module, "target", "classes")
            compiled_anything = False
            if os.path.isdir(classes_dir):
                for _dirpath, _dirnames, filenames in os.walk(classes_dir):
                    if any(fn.endswith(".class") for fn in filenames):
                        compiled_anything = True
                        break
            if not compiled_anything:
                missing.append(module)
        return missing

    def _venv_install_signature(self, install_args: List[str]) -> Tuple[Any, ...]:
        """PERF/DEPENDENCY-001: a cache key that correctly invalidates when
        the underlying dependency MANIFEST'S REAL CONTENT changes, not
        merely when `install_args`' own shape stays the same. The
        requirements.txt branch's install_args is `["-r", <path>]` - a
        PATH, which does not itself change on a content edit (a retry that
        just edited requirements.txt would otherwise get a stale cache hit)
        - so this hashes that file's current bytes instead. The
        pyproject.toml branch's install_args IS already a flat list of
        dependency specifiers freshly re-parsed from the file on every
        _resolve_python_interpreter() call (_pyproject_dependencies), so
        its own tuple form is already a correct, content-derived key with
        no extra hashing needed."""
        if len(install_args) == 2 and install_args[0] == "-r":
            req_path = install_args[1]
            full = req_path if os.path.isabs(req_path) else os.path.join(self.workspace_path, req_path)
            try:
                with open(full, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                digest = "unreadable"
            return ("-r", digest)
        return tuple(install_args)

    def _ensure_project_venv(self, install_args: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """PERF/DEPENDENCY-001 (2026-09-19): instance-scoped memoization
        wrapper over _ensure_project_venv_impl() (the real, unmodified
        implementation below) - see this class's own __init__ docstring
        comment for why instance scope (never global/cross-workspace) is
        the correct, safe boundary. Caches the EXACT (venv_python,
        install_error) tuple _ensure_project_venv_impl() returned,
        including a genuine failure - the alternative (re-running a 300s-
        timeout-bounded `pip install` a second/third time in the SAME
        attempt against byte-identical manifest content, which pip would
        resolve identically) burns real wall-clock time for a
        deterministically identical outcome, never a "failure silently
        becomes success" risk (the cached value IS the real, already-
        observed outcome, verbatim - not re-derived or reinterpreted)."""
        signature = self._venv_install_signature(install_args)
        if signature in self._venv_install_cache:
            return self._venv_install_cache[signature]
        result = self._ensure_project_venv_impl(install_args)
        self._venv_install_cache[signature] = result
        return result

    def _ensure_project_venv_impl(self, install_args: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """Creates (if not already present) a project-local virtual environment
        under .kriya/venv and pip-installs `install_args` into it, so a Python
        goal needing a real third-party package can actually be tested -
        PolymorphicValidator otherwise runs tests via sys.executable (KRIYA'S
        OWN interpreter), which only has whatever Kriya itself depends on
        installed. The same class of gap Ruby's `bundle install` fix closed for
        that stack (2026-08-04) - a structurally unwinnable quality gate the
        model's own code correctness can never fix.

        `install_args` is whatever should follow `pip install -q` - either
        `["-r", requirements_path]` (requirements.txt) or a flat list of PEP
        621 dependency specifiers (pyproject.toml, see _pyproject_dependencies)
        - both are just argv fragments to the SAME pip invocation, so this
        method doesn't need to know or care which manifest shape produced them.

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
        # SEC-001-P6 Stage 3 (2026-09-11): execution-environment-aware venv
        # creation/reference - the ONE thing this needed to NOT be is a
        # host-path-translation hack (this stage's own explicit
        # instruction). Kriya's own `sys.executable` is a host macOS/ARM64
        # binary that does not exist inside a Linux container at all; a
        # host-ABSOLUTE venv_dir handed to a container's own `venv`
        # invocation would be created under that literal path INSIDE the
        # container's ephemeral rootfs (no /Users tree exists there) - NOT
        # under the persisted workspace bind mount - since the container
        # has no knowledge of what that host path even means. The fix is
        # not translation, it's using paths that are ALREADY correct in
        # both modes: `venv_dir_host` (used for every `os.path.exists`
        # check - Kriya itself always inspects the real, shared host
        # directory, contained or not) versus a workspace-RELATIVE path
        # (used as the actual command argv, resolved by whichever `cwd`/
        # `-w` the command already runs under - `self.workspace_path` on
        # the host, the OCI backend's own fixed container workdir when
        # contained - exactly the same mechanism `cwd=self.workspace_path`
        # already relies on for every other command this class runs).
        contained = self.autonomy_cfg.contained_execution_required
        venv_dir_host = os.path.join(self.workspace_path, ".kriya", "venv")
        venv_python_host = os.path.join(venv_dir_host, "bin", "python")
        venv_relative = os.path.join(".kriya", "venv")
        venv_python_relative = os.path.join(venv_relative, "bin", "python")
        create_interpreter = "python3" if contained else sys.executable
        create_target = venv_relative if contained else venv_dir_host
        venv_python = venv_python_relative if contained else venv_python_host
        # LINUX-OCI-VENV-INTERPRETER-001: a venv created INSIDE a container
        # links bin/python to the container's interpreter (e.g.
        # /usr/local/bin/python3), which need not exist on the host - only
        # the link itself is the host-visible evidence. os.path.exists
        # follows it: it held on macOS only because Homebrew happens to
        # install /usr/local/bin/python3, and on a Linux host every contained
        # venv was judged "failed" and silently replaced by an interpreter
        # without the project's dependencies.
        venv_present = os.path.lexists if contained else os.path.exists

        if not venv_present(venv_python_host):
            try:
                create_res = self._run_cmd_with_timeout(
                    [create_interpreter, "-m", "venv", create_target], cwd=self.workspace_path, timeout=60,
                )
                if create_res["returncode"] != 0 or not venv_present(venv_python_host):
                    logger.warning(
                        f"Failed to create project-local venv at {venv_dir_host} - falling back to "
                        f"the default interpreter for this test run: {create_res['stderr']}"
                    )
                    return None, None
            except Exception as e:
                logger.warning(
                    f"Failed to create project-local venv at {venv_dir_host} - falling back to "
                    f"the default interpreter for this test run: {e}"
                )
                return None, None

        # PERF/DEPENDENCY-001 (2026-09-19): this method (the real
        # implementation, only ever reached through _ensure_project_venv()'s
        # own caching wrapper above on a genuine cache MISS) still re-runs on
        # every call whose manifest content actually differs from the last
        # one this instance observed - a retry that just edited requirements.
        # txt/pyproject.toml correctly reaches here again (a different cache
        # key), and pip itself is a fast no-op when its OWN dependency
        # resolution finds nothing to do. What no longer happens is
        # re-running this exact subprocess for byte-identical manifest
        # content within the same instance's lifetime (e.g. compile_check
        # then run_tests then run_app_sequence, all in one attempt).
        # network=UNRESTRICTED (SEC-001-P6 Stage 2): this is the ACQUISITION
        # step - a no-op under contained_execution_required=False (default
        # network stays irrelevant there), and under containment this is
        # the one call in this method that genuinely needs to reach a
        # package registry; still fully filesystem/process-contained.
        # acquisition=True (SEC-007, 2026-09-12): pip resolving/building a
        # real dependency tree gets the separate, more generous acquisition
        # resource authority - never the (possibly deliberately very
        # tight) target-code cap this same run may be using to bound a
        # suspected-hostile application.
        self.venv_install_attempts += 1
        install_res = self._run_cmd_with_timeout(
            [venv_python, "-m", "pip", "install", "-q", *install_args, "pytest"],
            cwd=self.workspace_path, timeout=300, network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, acquisition=True,
        )
        log_acquisition_outcome(
            "python", f"pip install {' '.join(install_args)}",
            returncode=install_res["returncode"], timed_out=install_res.get("timeout", False),
        )
        if install_res["returncode"] != 0:
            return None, (
                f"'pip install {' '.join(install_args)}' failed:\n{install_res['stdout']}\n{install_res['stderr']}"
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
        sys.executable when there's no requirements.txt/pyproject.toml
        dependency declaration, or when venv CREATION itself failed (an
        infrastructure problem, not something a retry can fix - degrades
        silently, same reasoning as _ensure_project_venv()'s own docstring).
        install_error is set ONLY when `pip install` of THIS project's own
        declared dependencies genuinely failed (a real, potentially
        code-fixable dependency problem, e.g. a bad package pin) - the
        caller should treat that as a hard failure rather than silently
        proceeding with an interpreter missing the dependencies the goal
        actually needs.

        requirements.txt takes priority when both exist (today's
        pre-existing behavior, unchanged); pyproject.toml (PRV-17,
        2026-09-03) is the fallback for a "Python packaging conventions"
        goal that declares dependencies there instead - see
        _pyproject_dependencies' own docstring for the live incident this
        closes: a goal declaring Django only in pyproject.toml got a bare
        sys.executable with nothing installed, so every attempt's test gate
        failed with `ModuleNotFoundError: No module named 'django'`
        regardless of source-file correctness."""
        # SEC-001-P6 Stage 3: the "no project-local venv" fallback must
        # also be execution-environment aware - Kriya's own `sys.executable`
        # (host mode) versus a generic "python3" token resolved by the
        # container image's own PATH (contained mode). This is the one
        # path a project with literally no requirements.txt/pyproject.toml
        # dependency declaration falls back to - it will not have `pytest`
        # preinstalled under containment the way Kriya's own host
        # environment happens to (a real, smaller, documented residual
        # limitation distinct from the venv case above, which always
        # installs pytest explicitly regardless of mode).
        contained = self.autonomy_cfg.contained_execution_required
        default_interpreter = "python3" if contained else sys.executable
        requirements_path = os.path.join(self.workspace_path, "requirements.txt")
        if os.path.exists(requirements_path) and _has_real_requirements(requirements_path):
            # SEC-001-P6 Stage 3: the host-absolute requirements_path (used
            # for the existence/content check just above, which is always a
            # real Kriya-side/host filesystem read regardless of mode) is
            # meaningless as a PIP ARGUMENT inside the container - only
            # switched to a workspace-relative "requirements.txt" when
            # contained; host mode keeps passing the exact absolute path
            # unchanged (existing test coverage pins this exact argv shape,
            # and there is no reason to touch behavior that already works).
            pip_requirements_arg = "requirements.txt" if contained else requirements_path
            venv_python, install_error = self._ensure_project_venv(["-r", pip_requirements_arg])
            if install_error:
                return default_interpreter, install_error
            if venv_python:
                return venv_python, None
            return default_interpreter, None
        pyproject_path = os.path.join(self.workspace_path, "pyproject.toml")
        if os.path.exists(pyproject_path):
            dependencies = _pyproject_dependencies(pyproject_path)
            if dependencies:
                venv_python, install_error = self._ensure_project_venv(dependencies)
                if install_error:
                    return default_interpreter, install_error
                if venv_python:
                    return venv_python, None
        return default_interpreter, None

    def _detect_stack(self) -> str:
        """Determines if the workspace uses Python, Java, or Ruby - or "unknown"
        for anything else (JS/TS, Go, Rust, C#, ...). Python used to be the blind
        default for anything not Java/Ruby, which meant a genuinely unsupported
        stack silently ran the Python compile-check branch, matched zero .py
        files, and reported a false-positive "Python files compiled successfully"
        - a quality gate that never actually checked anything. Python is now
        detected the same way Java/Ruby are, by real markers, so "no markers
        matched" is distinguishable from "this is a Python project"."""
        # Explicit project markers take precedence over loose source files. A
        # mixed or migrating workspace can retain a standalone source from a
        # different ecosystem; that residue must not override the build system
        # the workspace explicitly declares.
        # 1. Check for Java project markers
        if (any(adapter.detects(self.workspace_path) for adapter in BUILD_ADAPTERS if adapter.language == "java")
                or os.path.exists(os.path.join(self.workspace_path, "src", "main", "java"))):
            return "java"

        # 2. Check for Ruby
        if (os.path.exists(os.path.join(self.workspace_path, "Gemfile")) or
            os.path.exists(os.path.join(self.workspace_path, "Rakefile")) or
            os.path.exists(os.path.join(self.workspace_path, "spec"))):
            return "ruby"

        # 3. Check for Python
        if any(adapter.detects(self.workspace_path) for adapter in BUILD_ADAPTERS if adapter.language == "python"):
            return "python"

        # Marker-free, from-scratch Java projects still need a real compiler
        # gate once their first generated source appears. Keep this fallback
        # after every explicit ecosystem marker so it cannot misclassify a
        # mixed workspace merely because an old .java file remains.
        if self._has_any_java_file():
            return "java"

        if self._has_any_py_file():
            return "python"

        return "unknown"

    def _has_any_java_file(self) -> bool:
        """Recognize standalone Java sources without requiring build metadata."""
        return JAVA.has_sources(self.workspace_path)

    def _has_any_py_file(self) -> bool:
        """Recognize standalone Python sources without packaging metadata."""
        return PYTHON.has_sources(self.workspace_path)

    def _audit_run_command(self, cmd: List[str], cwd: str) -> None:
        """MA4.4 - ExecutionPolicy consultation, mirroring kriya/core/llm.py's
        _audit_llm_network_access (MA4.3): the decision is logged, never
        branched on for ALLOW/ALLOW_SANDBOXED/REQUIRE_APPROVAL/most DENY
        reasons - those can never affect whether ProcessController actually
        runs `cmd`. kriya/tools/validate.py's own PolymorphicValidator is
        the ONLY real ProcessController call site in Kriya today.

        MA7.3 (2026-08-24): kriya.policy.enforcement.enforce_hard_invariants
        now really raises PolicyDeniedError for one specific DENY reason_code
        here - COMMAND_SUDO_DENIED - the same narrow, explicitly-authorized
        real-enforcement pattern as kriya/policy/filesystem.py's
        AuthorizedFileWriter. Deliberately NOT COMMAND_NOT_ALLOWLISTED - MA4.4's
        narrow starter allowlist denies plenty of real, legitimate compile/test
        commands across supported stacks that just aren't on that short list
        yet, and turning THAT into a real gate would break real generation
        runs. `cmd` here is always constructed by this class's own stack-
        detection logic (mvn/gradle/pytest/npm/...), never from untrusted
        model/tool output, so COMMAND_SUDO_DENIED firing here is real
        defense-in-depth for a future bug, not the closure of a currently-live
        gap - it should structurally never trigger today. PolicyDeniedError
        propagates out of this function (this function's caller,
        _run_cmd_with_timeout, has no try/except of its own around this call,
        so a raise here genuinely prevents the real subprocess from running);
        every other exception is still caught and logged, exactly as before.

        MA4.7 - also issues a SECOND, separately-classified audit request
        (INSTALL_PACKAGE, not RUN_COMMAND) whenever `cmd` looks like a
        package-manager install invocation (extract_install_package_target -
        real, live examples at this exact call site: _ensure_project_venv's
        `pip install -r requirements.txt`, run_tests' Ruby path's `bundle
        install --path vendor/bundle`). Per design doc section 26 ("treat
        package installation as a supply-chain action, not just another
        command"), this gets its own dedicated policy-stage reasoning
        (kriya/policy/execution.py's _check_package_supply_chain) instead of
        blending into the generic command-allowlist's COMMAND_NOT_ALLOWLISTED
        signal - still audit-only (no INSTALL_PACKAGE reason_code is in
        HARD_ENFORCED_REASON_CODES)."""
        try:
            result = enforce_hard_invariants(
                self.execution_policy,
                ActionRequest(action_type=ActionType.RUN_COMMAND, command=tuple(cmd), workspace_path=cwd),
            )
            logger.debug(
                "MA4 policy audit: RUN_COMMAND '%s' -> %s (%s)",
                " ".join(cmd), result.decision.value, result.reason_code,
            )
        except PolicyDeniedError:
            raise
        except Exception as e:
            logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)

        try:
            install_target = extract_install_package_target(tuple(cmd))
            if install_target:
                install_result = self.execution_policy.evaluate(
                    ActionRequest(action_type=ActionType.INSTALL_PACKAGE, target=install_target, workspace_path=cwd)
                )
                logger.debug(
                    "MA4 policy audit (not enforced): INSTALL_PACKAGE '%s' -> %s (%s)",
                    install_target, install_result.decision.value, install_result.reason_code,
                )
        except Exception as e:
            logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)

    def host_resource_plan(self, command: Optional[List[str]] = None) -> Optional[ResourcePlan]:
        """LINUX-JVM-RLIMIT-AS-001: how the sandbox's CPU/memory budget is
        enforced for a host process of this validator (None when
        sandbox_execution is off). A Java stack's commands are JVM-backed:
        explicit JVM memory bounds instead of RLIMIT_AS, which a JVM's
        address-space reservation exceeds on Linux."""
        if not self.autonomy_cfg.sandbox_execution:
            return None
        language = self.toolchain_identity.language if self.toolchain_identity is not None else self.stack
        return resource_plan(
            command, self.autonomy_cfg.sandbox_cpu_seconds, self.autonomy_cfg.sandbox_memory_mb, language=language,
        )

    def build_subprocess_env_and_preexec(
        self, command: Optional[List[str]] = None,
    ) -> Tuple[Optional[Dict[str, str]], Optional[Callable[[], None]]]:
        """Shared sandbox-env + JAVA_HOME-override construction for every
        subprocess this validator launches - factored out of
        _run_cmd_with_timeout (2026-09-11) so a second caller (finite_command
        runtime-artifact preparation, kriya/workflow/attempt.py, reusing
        kriya/tools/service_runtime.py's _prepare_required_artifact for its
        own `mvn package` call) gets the IDENTICAL policy instead of
        reimplementing it - an `mvn package` that silently ran under a
        different JDK than the one the compile gate just validated against
        would be a real, confusing mismatch, not a hypothetical one. Pure
        extraction: _run_cmd_with_timeout's own behavior is unchanged by
        this refactor - no leading underscore, since it's now a shared
        cross-module policy accessor, not a validator-internal detail."""
        env = None
        preexec_fn = None
        plan = self.host_resource_plan(command)
        if plan is not None:
            env = plan.apply_env(build_restricted_env(self.autonomy_cfg.sandbox_env_allowlist))
            preexec_fn = plan.preexec_fn()
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
        return env, preexec_fn

    @property
    def java_home_override(self) -> Optional[str]:
        return self._java_home_override

    @java_home_override.setter
    def java_home_override(self, value: Optional[str]) -> None:
        """Production callers pick the goal-stated JDK AFTER construction
        (workflow.py/attempt.py assign this attribute), so the contained
        toolchain identity is re-resolved on every assignment - otherwise a
        goal-stated JDK would be ignored under containment and the
        repository-vs-JDK mismatch refusal could never fire."""
        self._java_home_override = value
        self._resolve_toolchain_identity()

    @property
    def toolchain_declaration_mutable(self) -> bool:
        return self._toolchain_declaration_mutable

    @toolchain_declaration_mutable.setter
    def toolchain_declaration_mutable(self, value: bool) -> None:
        self._toolchain_declaration_mutable = bool(value)
        self._resolve_toolchain_identity()

    def _resolve_toolchain_identity(self) -> None:
        """PRD-011: stack ownership remains in _detect_stack(); contained
        execution only resolves a versioned OCI profile from that one
        decision plus the selected JDK. Runs before any subprocess can start
        and raises (a ContainmentSetupError) for unsupported/conflicting
        requirements - never a host-tool fallback."""
        self.toolchain_identity = (
            resolve_toolchain_selection(
                self.workspace_path, self.stack, baseline_path=self.original_workspace_path,
                java_home_override=self._java_home_override,
                declaration_mutable=self._toolchain_declaration_mutable,
            )
            if self.autonomy_cfg.contained_execution_required else None
        )

    def build_containment_profile_and_backend(
        self, *, network: NetworkAuthority = NetworkAuthority.DENIED,
        dependency_cache_path: Optional[str] = None, dependency_cache_writable: bool = False,
        acquisition: bool = False, workspace_path: Optional[str] = None,
    ) -> Tuple[Optional[ContainmentProfile], Optional[ContainmentBackend]]:
        """SEC-001-P6 (2026-09-11): the real-containment counterpart to
        `build_subprocess_env_and_preexec`, gated by
        `autonomy_cfg.contained_execution_required` (default False -
        "existing behavior must remain compatible when containment is not
        required" is preserved exactly by staying on the raw env/preexec_fn
        path below until this is explicitly opted into).

        PRD-011: the existing `_detect_stack()` result is resolved once to
        a versioned ToolchainIdentity and carried on the profile. The OCI
        backend verifies the actual runtime and image content digest before
        execution; it never installs a JDK/compiler dynamically and never
        falls back to host tools on a mismatch.

        SECOND residual limitation, CLOSED this pass (SEC-001-P6 Stage 3,
        2026-09-11): `_ensure_project_venv`/`_resolve_python_interpreter`
        are now execution-environment aware - contained mode creates the
        venv with the container's own "python3" (never Kriya's host
        `sys.executable`, which does not exist inside a Linux container)
        and references it by a workspace-relative path
        (`.kriya/venv/bin/python`), resolved by whichever cwd/workdir the
        command already runs under - the SAME mechanism already used for
        every other contained command, not a host-path-translation hack.
        Proven end-to-end through the real `run_tests()` entry point, see
        tests/test_validate_oci.py.

        `network` (SEC-001-P6 Stage 2, 2026-09-11): defaults to DENIED
        (the execution-phase posture every OTHER contained command in this
        class uses) - `_ensure_project_venv`'s own `pip install` call is
        the one exception, passing UNRESTRICTED explicitly (it is a real
        dependency-ACQUISITION step, not execution of already-resolved
        code, matching kriya/tools/dependency_execution.py's own
        acquisition/execution split - still fully filesystem/process-
        contained throughout, only network differs).

        `acquisition` (SEC-007, 2026-09-12): explicit resource-authority
        selector - True routes cpu_seconds/memory_mb from
        `autonomy_cfg.acquisition_cpu_seconds`/`acquisition_memory_mb`
        instead of `sandbox_cpu_seconds`/`sandbox_memory_mb`. Deliberately
        a caller-supplied flag, never inferred from `network`/goals/command
        text (Invariant: do not infer acquisition trust merely from
        command text) - every call site that constructs an acquisition
        request already knows it's doing so structurally (it's calling a
        dedicated acquisition helper, not guessing from what the command
        looks like). Filesystem/environment/network containment and the
        containment MECHANISM itself are completely unchanged either way -
        only which resource-limit numbers land on the same
        `ContainmentProfile.cpu_seconds`/`memory_mb` fields that already
        existed; no parallel containment/execution code path is
        introduced (Invariant: do not duplicate containment execution
        code)."""
        if not self.autonomy_cfg.contained_execution_required:
            return None, None
        if acquisition:
            cpu_seconds = self.autonomy_cfg.acquisition_cpu_seconds
            memory_mb = self.autonomy_cfg.acquisition_memory_mb
        else:
            cpu_seconds = self.autonomy_cfg.sandbox_cpu_seconds
            memory_mb = self.autonomy_cfg.sandbox_memory_mb
        # SEC-006 (2026-09-12): the ONLY place destination authority enters
        # a real ContainmentProfile for this validator - always
        # AutonomyConfig.acquisition_registry_hosts (already normalized/
        # validated by its own field_validator), NEVER derived from the
        # command/goal/repository content about to run. A caller cannot
        # widen this by passing its own host list; there is no parameter
        # for that.
        network_destinations: Tuple[str, ...] = (
            tuple(self.autonomy_cfg.acquisition_registry_hosts)
            if network is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY else ()
        )
        profile = ContainmentProfile(
            trust_class=TrustClass.UNTRUSTED_EXECUTION,
            # D2B: a tooling-only acquisition mounts an empty Kriya-owned
            # directory instead of the candidate (never anything wider).
            workspace_path=workspace_path or self.workspace_path,
            network=network,
            network_destinations=network_destinations,
            env_allowlist=self.autonomy_cfg.sandbox_env_allowlist,
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            dependency_cache_paths=[dependency_cache_path] if dependency_cache_path else [],
            dependency_cache_writable=dependency_cache_writable,
            toolchain_identity=self.toolchain_identity,
        )
        backend = resolve_containment_backend(self.autonomy_cfg.containment_backend)
        return profile, backend

    def _run_cmd_with_timeout(
        self, cmd: List[str], cwd: str, timeout: int = 300, stdin_payload: Optional[str] = None,
        network: NetworkAuthority = NetworkAuthority.DENIED,
        dependency_cache_path: Optional[str] = None, dependency_cache_writable: bool = False,
        acquisition: bool = False, workspace_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._audit_run_command(cmd, cwd)
        self._register_gate_output(cmd, cwd)
        profile, backend = self.build_containment_profile_and_backend(
            network=network, dependency_cache_path=dependency_cache_path,
            dependency_cache_writable=dependency_cache_writable, acquisition=acquisition,
            workspace_path=workspace_path,
        )
        if profile is not None:
            result = ProcessController().run(
                cmd, cwd=cwd, timeout=timeout, stdin_payload=stdin_payload,
                containment_profile=profile, containment_backend=backend,
            )
            if network is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY:
                # SEC-006: raises RegistryAcquisitionSetupError (a
                # ContainmentSetupError) rather than returning if the
                # acquisition container's own trusted setup script
                # (firewall/IPv6/privilege-drop) failed - never let that
                # failure be misread as an ordinary mvn/pip result.
                finalize_registry_acquisition_result(result)
            return result.to_dict()
        env, preexec_fn = self.build_subprocess_env_and_preexec(cmd)
        result = ProcessController().run(
            cmd, cwd=cwd, timeout=timeout, env=env, preexec_fn=preexec_fn, stdin_payload=stdin_payload,
        ).to_dict()
        plan = self.host_resource_plan(cmd)
        if plan is not None:
            result["resources"] = plan.evidence()
        return result

    def _register_gate_output(self, cmd: List[str], cwd: str) -> None:
        """FILE-INTEGRITY-CONTRACT-001B: the verification gate now running
        designates the output roots of the exact toolchain command it is
        about to run (gate_output_roots); nothing else it creates is
        accepted."""
        binding, gate = self.tree_binding, self._gate
        if binding is None or gate is None:
            return
        for root in gate_output_roots(cmd, cwd):
            binding.authorize_gate_output(gate, os.path.relpath(root, binding.root))

    def _maven_cache_dir(self) -> str:
        """A persistent, per-workspace Maven local-repository cache
        (SEC-001-P6 Stage 2), OUTSIDE every source tree: under the Kriya state
        root (``KRIYA_STATE_DIR``, else ``~/.kriya/state``) at
        ``dependency-cache/maven/<workspace key>``, keyed by the validator's
        original workspace (the run's reused ``.kriya/worktree`` for gates, a
        fixed path per workspace), so later runs reuse what the
        registry-scoped acquisition fetched. It used to live in the
        worktree's ``.kriya/m2_cache``, inside the tree the project's own
        build walks: Apache RAT failed commons-lang's compile on the 567
        cached artifact files it found there. Created on demand -
        ``OCIContainmentBackend`` refuses to mount a ``dependency_cache_paths``
        entry that does not already exist as a real directory."""
        from kriya.core.state_paths import ENV_STATE_DIR, default_state_directory

        root = os.path.realpath(os.path.expanduser(os.environ.get(ENV_STATE_DIR) or default_state_directory()))
        workspace = os.path.realpath(self.original_workspace_path or self.workspace_path)
        key = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:16]
        cache_dir = os.path.join(root, "dependency-cache", "maven", key)
        # The cache Kriya kept in the tree before must not stay where the
        # build walks it (a reused worktree keeps untracked .kriya/ content):
        # it becomes the new cache when there is none yet, else it is dropped.
        legacy = os.path.join(self.workspace_path, ".kriya", "m2_cache")
        if os.path.isdir(legacy) and not os.path.islink(legacy):
            if not os.path.isdir(cache_dir) or not os.listdir(cache_dir):
                os.makedirs(os.path.dirname(cache_dir), exist_ok=True)
                if os.path.isdir(cache_dir):
                    os.rmdir(cache_dir)
                shutil.move(legacy, cache_dir)
            else:
                shutil.rmtree(legacy)
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    @staticmethod
    def _validation_result(success: bool, output: str, command_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Keep attested contained-toolchain evidence with the gate verdict."""
        result: Dict[str, Any] = {"success": success, "output": output}
        result.update(execution_evidence(command_result))
        return result

    _MAVEN_ACQUISITION_INCOMPLETE_MARKER = "MAVEN_ACQUISITION_INCOMPLETE:"

    def _run_maven_cmd(self, goals: List[str], cwd: str, timeout: int = 300, stdin_payload: Optional[str] = None,
                       deadline: Optional[float] = None, tooling_only: bool = False) -> Dict[str, Any]:
        """The `contained_execution_required`-aware replacement for a
        direct `_run_cmd_with_timeout(["mvn"] + goals, ...)` call - SEC-001
        live-validation follow-up (2026-09-11): the ORIGINAL version of
        this method warmed the cache via a single, fixed `dependency:
        go-offline` call - a real live run found this insufficient:
        `dependency:go-offline` resolves declared `<dependencies>` but
        does NOT reliably resolve build-LIFECYCLE PLUGIN artifacts
        (confirmed live - `maven-resources-plugin`, a DEFAULT lifecycle
        plugin the goal never even declared, and `maven-surefire-plugin`,
        both failed the identical way `dependency:go-offline` had
        supposedly already run for). Fixed by deriving acquisition from
        the REAL goal about to run, not a static goal/plugin list:
        whatever `mvn <goals>` actually needs - declared dependencies,
        transitive plugin dependencies, anything the real lifecycle
        resolves - gets cached, because acquisition runs THE SAME goals,
        just with network permitted, instead of a narrower proxy goal.

        Host mode: byte-for-byte pass-through, unchanged, no flags added.

        Contained mode: tries OFFLINE first (network=DENIED, whatever is
        already cached from a prior gate call in this same run). Only on
        a genuine `OfflineFailureKind.MISSING_DEPENDENCY` (not just any
        nonzero exit) does it run ONE bounded acquisition - `mvn <goals>`
        again, network=UNRESTRICTED, still fully filesystem/process-
        contained - then ONE more offline retry. The acquisition run's
        own result/side effects are always discarded (logged, never
        returned) - it is PREPARATION ONLY and must never be mistaken for
        compile/test/runtime PASS evidence; only an offline attempt's
        result is ever returned from this method. If the retry still
        shows materially identical missing-artifact evidence (compared
        via `maven_missing_artifact_signature`, falling back to "still
        MISSING_DEPENDENCY" if the specific coordinate can't be
        extracted from either message), this terminates deterministically
        - the retry's own result is returned with a distinguishing
        `MAVEN_ACQUISITION_INCOMPLETE:` marker prefixed onto stderr, so a
        caller can tell "acquisition could not complete" apart from an
        ordinary code-level test/compile failure - never a third
        acquisition, never a loop, never a fallback to unrestricted
        networking for the goals themselves.

        ``tooling_only`` (D2B, runtime verification): the acquisition never
        runs ``goals`` - for a runtime command those execute the candidate
        application, which must never hold registry network authority. It
        resolves only the Maven plugins the command names, through each
        plugin's inert ``help`` goal, in an empty Kriya-owned directory
        mounted as the container's workspace (no candidate POM, ``.mvn/``,
        sources or classes exist there), then the exact command runs offline
        once more. A command that names no plugin goal gets no acquisition.
        Compile and test keep the goal-derived acquisition above: those goals
        build and test candidate code by design, and their dependency set is
        not knowable without the project's own build."""
        if not self.autonomy_cfg.contained_execution_required:
            # Byte-for-byte pass-through (stdin only when a runtime step has one).
            stdin = {"stdin_payload": stdin_payload} if stdin_payload is not None else {}
            return self._run_cmd_with_timeout(["mvn"] + goals, cwd=cwd, timeout=timeout, **stdin)

        cache_dir = self._maven_cache_dir()

        def _bounded(seconds: int) -> int:
            """``deadline`` (the run's root generation deadline, runtime
            verification): every step is capped by what remains of it."""
            if deadline is None:
                return seconds
            return max(1, min(seconds, int(deadline - time.monotonic())))

        def _offline_attempt() -> Dict[str, Any]:
            return self._run_cmd_with_timeout(
                ["mvn", "-B", "-o", f"-Dmaven.repo.local={MAVEN_CACHE_MOUNT}"] + goals,
                cwd=cwd, timeout=_bounded(timeout), network=NetworkAuthority.DENIED,
                dependency_cache_path=cache_dir, dependency_cache_writable=True,
                **({"stdin_payload": stdin_payload} if stdin_payload is not None else {}),
            )

        def _deadline_exhausted() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        goal_desc = f"mvn {' '.join(goals)}"

        def _acquire_for_this_goal() -> None:
            if tooling_only:
                self._acquire_maven_tooling(goals, cache_dir, _bounded(timeout))
                return
            # PREPARATION/ACQUISITION ONLY - network=DEPENDENCY_REGISTRY_ONLY
            # (SEC-006: registry-scoped, not unrestricted), same goals as
            # the authoritative offline run, result discarded. Never
            # contributes Quality Gate PASS evidence: the caller never sees
            # this call's own return value (OBS-005: its OUTCOME - exit
            # code/timeout/likely-resource-termination - is still always
            # recorded via _log_acquisition_outcome, just never its
            # content).
            try:
                result = self._run_cmd_with_timeout(
                    ["mvn", "-B", f"-Dmaven.repo.local={MAVEN_CACHE_MOUNT}"] + goals,
                    cwd=cwd, timeout=_bounded(timeout), network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, acquisition=True,
                    dependency_cache_path=cache_dir, dependency_cache_writable=True,
                )
            except ContainmentSetupError:
                # SEC-006 (Invariant: containment/resource setup failure
                # must block execution, never degrade silently): a
                # RegistryAcquisitionSetupError (firewall/proxy/IPv6/
                # privilege-drop failure) must propagate as the distinct
                # containment-setup failure it is - NEVER get folded into
                # the generic "acquisition failed to invoke" warning below,
                # which would let a real security-mechanism failure look
                # like an ordinary, retryable missing-dependency outcome.
                raise
            except Exception as e:
                logger.warning(f"Maven acquisition (goal={goal_desc!r}) failed to invoke: {e}")
                return
            log_acquisition_outcome("maven", goal_desc, returncode=result["returncode"], timed_out=result.get("timeout", False))

        first = _offline_attempt()
        if first["returncode"] == 0:
            return first
        first_output = first.get("stdout", "") + first.get("stderr", "")
        if classify_maven_offline_failure_text(first_output) != OfflineFailureKind.MISSING_DEPENDENCY:
            return first

        if _deadline_exhausted():
            return self._acquisition_incomplete(first, goals, "the run's generation deadline left no time to acquire it")
        if tooling_only and not self._maven_plugin_goals(goals):
            return self._acquisition_incomplete(
                first, goals, "the runtime command names no Maven plugin goal, and runtime verification never "
                "acquires by running candidate goals")
        logger.info(
            "mvn %s failed offline with a missing-dependency signature - running ONE bounded "
            "acquisition (network-enabled, preparation only, goals=%s) then one more offline "
            "attempt.", " ".join(goals), goals,
        )
        _acquire_for_this_goal()
        if _deadline_exhausted():
            # Never a retry once the deadline is gone (it would be cut anyway).
            return self._acquisition_incomplete(first, goals, "the run's generation deadline ran out during acquisition")
        second = _offline_attempt()
        if second["returncode"] == 0:
            logger.info(f"Acquisition (maven, goal={goal_desc!r}): authoritative offline retry followed and succeeded.")
            return second
        logger.info(f"Acquisition (maven, goal={goal_desc!r}): authoritative offline retry followed but still failed.")
        second_output = second.get("stdout", "") + second.get("stderr", "")
        if classify_maven_offline_failure_text(second_output) != OfflineFailureKind.MISSING_DEPENDENCY:
            return second

        first_artifact = maven_missing_artifact_signature(first_output)
        second_artifact = maven_missing_artifact_signature(second_output)
        if first_artifact is not None and second_artifact is not None:
            # Both messages named a specific artifact/plugin coordinate -
            # compare them directly.
            materially_identical = first_artifact == second_artifact
        else:
            # Couldn't extract a specific coordinate from at least one
            # message - both are already confirmed MISSING_DEPENDENCY-
            # classified at this point, so treat that shared classification
            # as sufficient evidence of the same failure class rather than
            # attempting a third acquisition on an unclear signal.
            materially_identical = True
        if materially_identical:
            logger.warning(
                "mvn %s still reports a missing dependency/plugin after one bounded "
                "reacquisition - terminating deterministically as acquisition-incomplete, "
                "not retrying further.", " ".join(goals),
            )
            second = self._acquisition_incomplete(
                second, goals, "it is still missing after one bounded, network-enabled reacquisition attempt")
        return second

    @staticmethod
    def _maven_plugin_goals(goals: List[str]) -> List[str]:
        """The plugin invocations of a Maven command line (``prefix:goal`` or
        ``group:artifact[:version]:goal``); lifecycle phases and options excluded."""
        return [goal for goal in goals if not goal.startswith("-") and ":" in goal]

    def _declared_maven_plugin(self, prefix: str) -> Optional[str]:
        """``group:artifact[:version]`` of the plugin the project's own POM
        declares for ``prefix`` (Maven's naming: ``<prefix>-maven-plugin`` or
        ``maven-<prefix>-plugin``), read as XML - never by running Maven."""
        try:
            root = ET.parse(os.path.join(self.workspace_path, "pom.xml")).getroot()
        except (OSError, ET.ParseError):
            return None

        def local(element: ET.Element) -> str:
            return element.tag.rsplit("}", 1)[-1]  # namespace-agnostic tag name
        properties = {local(e): (e.text or "").strip() for p in root if local(p) == "properties" for e in p}
        for plugin in (e for e in root.iter() if local(e) == "plugin"):
            fields = {local(child): (child.text or "").strip() for child in plugin}
            if fields.get("artifactId") not in (f"{prefix}-maven-plugin", f"maven-{prefix}-plugin"):
                continue
            group = fields.get("groupId") or "org.apache.maven.plugins"
            version = re.sub(r"^\$\{([^}]+)\}$", lambda m: properties.get(m.group(1), ""), fields.get("version", ""))
            return f"{group}:{fields['artifactId']}" + (f":{version}" if version and "$" not in version else "")
        return None

    def _acquire_maven_tooling(self, goals: List[str], cache_dir: str, timeout: int) -> None:
        """D2B: resolve exactly the plugins ``goals`` invoke - nothing of the
        candidate runs. One registry-scoped process in an empty Kriya-owned
        directory: ``mvn <coordinate>:help`` per plugin (a prefix the POM does
        not declare is resolved the way Maven itself resolves it, from the
        plugin groups' metadata)."""
        coordinates = []
        for goal in self._maven_plugin_goals(goals):
            plugin = goal.rsplit(":", 1)[0]
            coordinate = (self._declared_maven_plugin(plugin) or plugin) if ":" not in plugin else plugin
            if coordinate not in coordinates:
                coordinates.append(coordinate)
        desc = f"mvn {' '.join(c + ':help' for c in coordinates)} (tooling only)"
        with tempfile.TemporaryDirectory(prefix="kriya-maven-tooling-") as empty:
            empty = os.path.realpath(empty)
            try:
                result = self._run_cmd_with_timeout(
                    ["mvn", "-B", f"-Dmaven.repo.local={MAVEN_CACHE_MOUNT}", *(c + ":help" for c in coordinates)],
                    cwd=empty, timeout=timeout, network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, acquisition=True,
                    dependency_cache_path=cache_dir, dependency_cache_writable=True, workspace_path=empty,
                )
            except ContainmentSetupError:
                raise  # SEC-006: a setup failure is never an ordinary acquisition outcome
            except Exception as e:
                logger.warning(f"Maven tooling acquisition ({desc}) failed to invoke: {e}")
                return
        log_acquisition_outcome("maven", desc, returncode=result["returncode"], timed_out=result.get("timeout", False))

    def _acquisition_incomplete(self, result: Dict[str, Any], goals: List[str], why: str) -> Dict[str, Any]:
        """``result`` marked as a dependency-acquisition gap (the
        ``MAVEN_ACQUISITION_INCOMPLETE:`` marker callers classify), never as
        a defect of the generated code."""
        marked = dict(result)
        marked["stderr"] = (
            f"{self._MAVEN_ACQUISITION_INCOMPLETE_MARKER} offline execution for goals {goals!r} "
            f"reports a missing dependency/plugin: {why} - this is a dependency-acquisition gap, "
            f"not necessarily a defect in the generated code.\n{result.get('stderr', '')}"
        )
        return marked

    @_verification_gate("pom_validate")
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
            res = self._run_maven_cmd(["validate"], cwd=self.workspace_path, timeout=120)
            if res["returncode"] == 0:
                return {"success": True, "output": "Maven POM validation succeeded."}
            return {"success": False, "output": f"Maven POM validation failed:\n{res['stdout']}\n{res['stderr']}"}
        except FileNotFoundError as e:
            # 'mvn' itself isn't on PATH - a toolchain problem, not a POM
            # defect. Same reasoning as run_compile_check()'s identical guard:
            # must be returned, not silently swallowed, or a real toolchain
            # gap gets misread as a code-content bug.
            return {"success": False, "output": f"Failed to invoke mvn validate: {e}"}
        except ContainmentSetupError:
            # SEC-002 (2026-09-12): a containment/resource setup failure
            # (docker daemon unreachable, NET_ADMIN unavailable, proxy/
            # firewall setup failure, etc.) must never be treated as an
            # ordinary "mvn validate could not be run, skip it" toolchain
            # gap - unlike FileNotFoundError above, this is not a benign
            # "tool missing" case, it is a security-significant setup
            # failure that must propagate to the same containment-failure
            # classification every other contained call site uses
            # (kriya/workflow/retry_strategy.py's handle_attempt_failure),
            # never silently reported as gate PASS.
            raise
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
        if self.autonomy_cfg.contained_execution_required:
            # SEC-001-P6 Stage 2: NOT wired onto the contained path this
            # pass - `-Dmdep.outputFile` needs a path OUTSIDE the workspace
            # mount (tempfile.mkstemp's own system temp dir), which a
            # container cannot see or write to; this method's own contract
            # ("never raises, returns None on any failure") already covers
            # this cleanly - an optional recovery-tool lookup returning
            # nothing is a correct, honest degrade, not a silent bypass of
            # anything security-relevant (this reads dependency classpath
            # info, it doesn't execute untrusted code any differently).
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

    @_verification_gate("classpath_inspection")
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

    @_verification_gate("compile")
    def run_compile_check(self, files: List[str], *, deadline: Optional[float] = None) -> Dict[str, Any]:
        """Runs language-specific compilation check on changed files.

        ``deadline`` (D3): the run's root generation deadline, when the check
        runs as a runtime-verification prerequisite; the Maven compile is then
        bounded by what remains of it. The ordinary compile gate passes none."""
        if not files:
            return {"success": True, "output": "No files to compile check."}

        # A from-scratch workspace can be marker-free when the validator is
        # constructed and gain its first source file moments later during the
        # Developer stage. Refresh only an unknown result at the gate boundary;
        # established stack decisions remain stable.
        if self.stack == "unknown":
            self.stack = self._detect_stack()
            self._resolve_toolchain_identity()

        if self.stack == "python":
            return PIP.compile(self, files, deadline=deadline)

        elif self.stack == "java":
            # The build adapters in precedence order (Maven: dependency
            # regression, then mvn clean compile; Gradle: compileJava); None:
            # not decided, continue with the next one, then javac.
            for adapter in BUILD_ADAPTERS:
                if adapter.language == "java":
                    decided = adapter.compile(self, files, deadline=deadline)
                    if decided is not None:
                        return decided

            # 3. Fallback to raw javac syntax check (for simple single-class projects)
            # `files` can include controller-provided established-file context
            # used to inform planning and runtime judgment. Only pass sources
            # that physically exist in this sandbox to javac: a contextual name
            # that has not been materialized here must not turn an otherwise
            # valid compile into javac's unrelated "file not found" failure.
            java_files = [
                os.path.join(self.workspace_path, f)
                for f in files
                if f.endswith(".java")
                and os.path.isfile(os.path.join(self.workspace_path, f))
            ]
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
                        error_output = enrich_java_compiler_errors(
                            error_output,
                            allow_external_lookup=(
                                self.autonomy_cfg.egress_policy != "local_only"
                                and self.autonomy_cfg.web_lookup_enabled
                            ),
                        )
                    except Exception as ree:
                        logger.warning(f"Resolver failed to run: {ree}")
                    return self._validation_result(False, error_output, res)
                return self._validation_result(True, "Java classes compiled successfully.", res)
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
                        except ContainmentSetupError:
                            # SEC-002 (2026-09-12): must propagate, not be
                            # reported as an ordinary "Ruby runtime
                            # execution failed" toolchain/code problem.
                            raise
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

    @_verification_gate("tests")
    def run_tests(self, target_test: Optional[Union[str, Sequence[str]]] = None) -> Dict[str, Any]:
        """Runs tech-stack specific test execution suite.

        `target_test` accepts either a single string (unchanged, existing
        contract - every pre-existing caller keeps working identically) or
        an ordered sequence of strings (VAL-001 G1-R3: brownfield PRE/POST
        baseline comparison needs to select several specific test files at
        once - e.g. the calibrated `tests/test_csharp_type_resolution.py` +
        `tests/test_csharp_member_calls.py` pair). A sequence is NEVER
        joined into one string and NEVER shell-interpreted - each element
        becomes its own separate argv entry to the underlying test-runner
        subprocess, structurally, the same way a real shell would word-split
        several unquoted paths - see PipBuildAdapter.run_tests
        (kriya/capabilities/pip.py) for the one place this actually matters today (a single opaque argv token
        containing a space is not multiple paths to pytest's own arg
        parser - proven empirically during G1-R3 PREPARE, not assumed)."""
        target_test_list: Optional[List[str]] = None
        if target_test is not None:
            target_test_list = [target_test] if isinstance(target_test, str) else list(target_test)
        try:
            if self.stack == "python":
                return PIP.run_tests(self, target_test_list)

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
                # Multi-target selection is a Python-stack (pytest) concept
                # only today (VAL-001 G1-R3) - Java honors just the first
                # target, unchanged single-target behavior for the common
                # (and, so far, only real) case of one string being passed.
                java_test_class = (
                    os.path.splitext(os.path.basename(target_test_list[0]))[0] if target_test_list else None
                )
                for adapter in BUILD_ADAPTERS:
                    if adapter.language == "java" and adapter.detects(self.workspace_path):
                        return adapter.run_tests(self, java_test_class)
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
                # Multi-target selection is a Python-stack (pytest) concept
                # only today - Ruby honors every target given (rspec accepts
                # multiple path args natively), same structural argv-extend
                # treatment as the Python branch above.
                cmd = ["bundle", "exec", "rspec"]
                if target_test_list:
                    cmd.extend(target_test_list)
                try:
                    res = self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
                except ContainmentSetupError:
                    # SEC-002 (2026-09-12): a containment/resource setup
                    # failure is not the ordinary "bundle exec rspec isn't
                    # set up right, try plain rspec" case this fallback
                    # exists for - falling through here would only ever
                    # re-hit the same unavailable backend via the plain
                    # rspec invocation below, wasting an attempt while
                    # hiding the real cause; must propagate immediately.
                    raise
                except Exception as e:
                    logger.debug(f"'bundle exec rspec' failed, falling back to plain 'rspec': {e}")
                    cmd = ["rspec"]
                    if target_test_list:
                        cmd.extend(target_test_list)
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

        except ContainmentSetupError:
            # SEC-002 (2026-09-12): must propagate to the existing
            # containment-failure classification (handle_attempt_failure),
            # never be reported as an ordinary "failed to execute local
            # test suite" gate failure - that framing would feed a real
            # infrastructure problem back into the model-repair retry loop
            # as if it were a fixable test/code defect.
            raise
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
        # "python3" added to the match set (SEC-001-P6 Stage 3, 2026-09-11):
        # a model-authored command can just as easily say "python3" as
        # "python" - without this, a command already spelled "python3"
        # would silently skip substitution and run against the container's
        # bare interpreter even when a project-local venv exists to use
        # instead (pre-existing gap, not contained-mode-specific, but only
        # actually noticed once "python3" itself became a real return value
        # of _resolve_python_interpreter's own default-interpreter case).
        rewritten = [
            ([interpreter] + cmd[1:]) if cmd and cmd[0] in ("python", "python3", sys.executable) else cmd
            for cmd in commands
        ]
        return rewritten, None

    @_verification_gate(RUNTIME_VERIFICATION_GATE)
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
        not_ready, prerequisite = self._prepare_runtime([command])
        if not_ready is not None:
            return not_ready
        try:
            res = self._run_runtime_step(command, timeout)
        except ContainmentSetupError:
            # SEC-002 (2026-09-12): must propagate to the existing
            # containment-failure classification, not be reported as an
            # ordinary "failed to execute run command" runtime failure.
            raise
        except Exception as e:
            return {"success": False, "timed_out": False, "returncode": None, "output": f"Failed to execute run command: {e}"}
        return {
            "success": res["returncode"] == 0 and not res["timeout"],
            "timed_out": res["timeout"],
            "returncode": res["returncode"],
            "output": res["stdout"] + "\n" + res["stderr"],
            "runtime_prerequisite": prerequisite,
            "entrypoint_diagnosis": (self.diagnose_runtime_entrypoint(command)
                                     if res["returncode"] != 0 and not res["timeout"] else None),
        }

    RUNTIME_PREREQUISITE_FAILED = "RUNTIME_PREREQUISITE_FAILED"

    # D4: who chose the main class a runtime command actually runs.
    ENTRYPOINT_KRIYA_COMMAND = "KRIYA_COMMAND"
    ENTRYPOINT_CANDIDATE_BUILD_CONFIG = "CANDIDATE_BUILD_CONFIG"
    ENTRYPOINT_UNKNOWN = "UNKNOWN"
    _EXEC_PLUGIN = "exec-maven-plugin"
    _JAVA_OPTIONS_WITH_VALUE = frozenset({"-cp", "-classpath", "--class-path", "-p", "--module-path", "--add-opens",
                                          "--add-exports", "--add-modules", "--add-reads", "--patch-module"})

    def _exec_plugin_main_class(self, cli_properties: Dict[str, str]) -> Tuple[Optional[str], Optional[str], str]:
        """(value, config key, provenance) of the main class the project's own
        POM gives exec-maven-plugin's `mainClass`, by Maven's precedence for a
        command-line `exec:java`: the `default-cli` execution's configuration,
        else the plugin's own, else pluginManagement's - each of which beats a
        `-Dexec.mainClass` user property (measured, evidence/demo-defect-d4).
        A `${property}` value resolves from the command's -D properties
        (KRIYA_COMMAND) or the POM's <properties> (CANDIDATE_BUILD_CONFIG); a
        value it cannot resolve, or no local configuration under a <parent>
        (which may configure it), is UNKNOWN. No POM configuration at all:
        (None, None, KRIYA_COMMAND) - the command's property decides."""
        try:
            root = ET.parse(os.path.join(self.workspace_path, "pom.xml")).getroot()
        except (OSError, ET.ParseError):
            return None, None, self.ENTRYPOINT_UNKNOWN

        def local(element: ET.Element) -> str:
            return element.tag.rsplit("}", 1)[-1]

        def child(element: ET.Element, name: str) -> Optional[ET.Element]:
            return next((c for c in element if local(c) == name), None)

        def main_class(element: Optional[ET.Element]) -> Optional[str]:
            configuration = child(element, "configuration") if element is not None else None
            value = child(configuration, "mainClass") if configuration is not None else None
            return (value.text or "").strip() if value is not None else None

        properties = {local(e): (e.text or "").strip() for p in root if local(p) == "properties" for e in p}
        build = child(root, "build")
        candidates: List[Tuple[Optional[str], str]] = []
        for section, key_prefix in ((build, "build/plugins"), (child(build, "pluginManagement") if build is not None
                                                               else None, "build/pluginManagement/plugins")):
            plugins = child(section, "plugins") if section is not None else None
            for plugin in (plugins if plugins is not None else []):
                if local(plugin) != "plugin" or (child(plugin, "artifactId") is None
                                                  or (child(plugin, "artifactId").text or "").strip() != self._EXEC_PLUGIN):
                    continue
                key = f"{key_prefix}/plugin[{self._EXEC_PLUGIN}]"
                executions = child(plugin, "executions")
                for execution in (executions if executions is not None else []):
                    if (child(execution, "id") is not None and (child(execution, "id").text or "").strip() == "default-cli"
                            and main_class(execution) is not None):
                        candidates.append((main_class(execution), f"{key}/executions/execution[default-cli]/configuration/mainClass"))
                if main_class(plugin) is not None:
                    candidates.append((main_class(plugin), f"{key}/configuration/mainClass"))
        if not candidates:
            if child(root, "parent") is not None:
                return None, None, self.ENTRYPOINT_UNKNOWN
            return None, None, self.ENTRYPOINT_KRIYA_COMMAND
        value, key = candidates[0]
        reference = re.fullmatch(r"\$\{([^}]+)\}", value or "")
        if reference:
            name = reference.group(1)
            if name in cli_properties:
                return cli_properties[name], key, self.ENTRYPOINT_KRIYA_COMMAND
            if name in properties and "$" not in properties[name]:
                return properties[name], key, self.ENTRYPOINT_CANDIDATE_BUILD_CONFIG
            return None, key, self.ENTRYPOINT_UNKNOWN
        if not value or "$" in value:
            return None, key, self.ENTRYPOINT_UNKNOWN
        return value, key, self.ENTRYPOINT_CANDIDATE_BUILD_CONFIG

    def diagnose_runtime_entrypoint(self, command: List[str]) -> Optional[Dict[str, Any]]:
        """D4: a deterministic account of the main class a failed JVM runtime
        command ran - what was requested, what actually ran and who chose it,
        and whether the CURRENT source declares it and the fresh build (the D3
        prerequisite) produced it. Read from the command, the POM (as XML) and
        the tree; never from the model and never from output text alone.
        None for commands that do not launch a JVM main class."""
        if not command:
            return None
        tool, requested, effective, provenance, config_key = os.path.basename(command[0]), None, None, None, None
        if tool in ("mvn", "mvnw"):
            goals = [g for g in command[1:] if not g.startswith("-")]
            if not any(g == "exec:java" or (g.endswith(":java") and self._EXEC_PLUGIN in g) for g in goals):
                return None
            cli = dict(arg[2:].split("=", 1) for arg in command[1:] if arg.startswith("-D") and "=" in arg)
            requested = cli.get("exec.mainClass")
            effective, config_key, provenance = self._exec_plugin_main_class(cli)
            if provenance == self.ENTRYPOINT_KRIYA_COMMAND and config_key is None:
                effective = requested
        elif tool == "java":
            args, i = command[1:], 0
            while i < len(args) and args[i].startswith("-"):
                if args[i] in ("-jar", "--module", "-m"):
                    return None
                i += 2 if args[i] in self._JAVA_OPTIONS_WITH_VALUE else 1
            requested = effective = args[i] if i < len(args) else None
            provenance = self.ENTRYPOINT_KRIYA_COMMAND
        else:
            return None
        if not effective:
            provenance = self.ENTRYPOINT_UNKNOWN
        relative = (effective or "").replace(".", "/")
        simple, package = (effective or "").rsplit(".", 1)[-1], (effective or "").rpartition(".")[0]
        declared = bool(effective) and any(
            source.endswith(f"/{relative}.java") or source == f"{relative}.java"
            or self._source_declares(source, package, simple)
            for source in self._java_sources())
        compiled = bool(effective) and self._compiled_class_exists(f"{relative}.class")
        return {"requested_entrypoint": requested, "effective_entrypoint": effective,
                "effective_entrypoint_provenance": provenance,
                "candidate_config_source": "pom.xml" if provenance == self.ENTRYPOINT_CANDIDATE_BUILD_CONFIG else None,
                "candidate_config_key": config_key if provenance == self.ENTRYPOINT_CANDIDATE_BUILD_CONFIG else None,
                "source_declares_entrypoint": declared, "compiled_artifact_exists": compiled,
                "command": list(command)}

    def _compiled_class_exists(self, class_file: str) -> bool:
        """Whether any module's target/classes holds ``class_file`` (the
        fresh D3 build output; VCS, Kriya state and other build trees skipped)."""
        for directory, dirnames, _ in os.walk(self.workspace_path):
            if os.path.isfile(os.path.join(directory, "target", "classes", class_file)):
                return True
            dirnames[:] = [d for d in dirnames if d not in (".git", ".kriya", "target", "node_modules")]
        return False

    def _source_declares(self, source: str, package: str, simple: str) -> bool:
        try:
            with open(os.path.join(self.workspace_path, source), encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            return False
        declares_package = (re.search(rf"^\s*package\s+{re.escape(package)}\s*;", text, re.M) is not None
                            if package else re.search(r"^\s*package\s", text, re.M) is None)
        return declares_package and re.search(
            rf"\b(class|record|enum|interface)\s+{re.escape(simple)}\b", text) is not None

    def _runtime_needs_compiled_classes(self, command: List[str]) -> bool:
        """D3: a runtime command that consumes this Maven project's compiled
        classes - any Maven invocation, or a JVM launched on target/classes."""
        if not command or not os.path.isfile(os.path.join(self.workspace_path, "pom.xml")):
            return False
        tool = os.path.basename(command[0])
        return tool in ("mvn", "mvnw") or (tool == "java" and any("target/classes" in arg for arg in command[1:]))

    def _java_sources(self) -> List[str]:
        """Every .java source of the project, workspace-relative (build output,
        VCS and Kriya state excluded)."""
        return JAVA.source_files(self.workspace_path)

    def _prepare_runtime(self, commands: List[List[str]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """D3 (KNOW-A 2026-10-01): a runtime verification establishes its own
        prerequisites from the CURRENT source, never from build output an
        earlier work unit left behind. Each work unit resets the sandbox with
        `git clean -fd`, which deletes an untracked target/ (and keeps an
        ignored one, whose classes may then be stale), and a runtime command
        such as `mvn exec:java` builds nothing itself. So before a command
        that consumes the compiled classes of a Maven project runs, the
        existing compile gate (`mvn clean compile`, its own bounded
        acquisition, within the run's root deadline) rebuilds them; whether
        the command itself says `compile` does not matter. Other stacks need
        no preparation here (an interpreted Python run has none; a raw javac
        run compiles in its own command). Returns None when ready, else the
        runtime result that stops verification before anything runs, plus the
        prerequisite that ran (None when the command needs none)."""
        if not any(self._runtime_needs_compiled_classes(c) for c in commands):
            return None, None
        if self.stack != "java":
            # A pom.xml is present, and _detect_stack checks it first: this is a
            # Java project even when the validator was built before it existed.
            self.stack = self._detect_stack()
            self._resolve_toolchain_identity()
        prepared = self.run_compile_check(self._java_sources() or ["pom.xml"], deadline=self._run_deadline())
        if prepared["success"]:
            return None, "mvn clean compile (current source): PASSED"
        return {"success": False, "timed_out": False, "returncode": None, "prerequisite_failed": True,
                "output": (f"{self.RUNTIME_PREREQUISITE_FAILED}: the application could not be built from the "
                           f"current source before runtime verification (mvn clean compile); nothing was run.\n\n"
                           f"{prepared['output']}")}, "mvn clean compile (current source): FAILED"

    def _run_runtime_step(self, command: List[str], timeout: int,
                          stdin_payload: Optional[str] = None) -> Dict[str, Any]:
        """D2 (2026-10-01): a runtime-verification command. A Maven
        invocation gets the same bounded, registry-scoped dependency/plugin
        acquisition as the compile and test gates (_run_maven_cmd; host mode:
        byte-for-byte pass-through), within the run's root generation
        deadline; anything else runs as is."""
        if command and os.path.basename(command[0]) == "mvn":
            return self._run_maven_cmd(list(command[1:]), cwd=self.workspace_path, timeout=timeout,
                                       stdin_payload=stdin_payload, deadline=self._run_deadline(),
                                       tooling_only=True)
        return self._run_cmd_with_timeout(command, cwd=self.workspace_path, timeout=timeout,
                                          stdin_payload=stdin_payload)

    def _run_deadline(self) -> Optional[float]:
        """The run's root generation deadline (PROVIDER-CONTRACT-001A), if any."""
        from kriya.control.run_coordinator import claim_run_generation_clock

        budget = self.autonomy_cfg.generation_time_budget_seconds
        started = claim_run_generation_clock()
        return started + budget if started is not None and budget is not None else None

    @_verification_gate(RUNTIME_VERIFICATION_GATE)
    def run_app_sequence(
        self, commands: List[List[str]], timeout: int = 90, stdin_payload: Optional[str] = None,
    ) -> Dict[str, Any]:
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
        its own timeout budget, not a shared one.

        stdin_payload (Runtime Verification Contract, PRV-06, 2026-08-29): applied ONLY to
        the LAST command - the one that actually exercises the application's behavior in
        every real sequence this codebase produces (an "add an item, then list items"-style
        multi-command sequence's own earlier steps are themselves separate app invocations
        already fully specified by the judge's own literal argv, not something this
        parameter is meant to feed). Every OTHER command in the sequence still gets stdin
        explicitly closed (ProcessController's own new default), never left open - the
        difference this parameter makes is only whether the LAST command receives a real
        payload instead of immediate EOF."""
        if not commands:
            return {"success": False, "timed_out": False, "returncode": None, "output": "No run commands provided."}

        commands, install_error = self._substitute_python_interpreter(commands)
        if install_error:
            return {"success": False, "timed_out": False, "returncode": None, "output": install_error}
        not_ready, prerequisite = self._prepare_runtime(commands)
        if not_ready is not None:
            return not_ready

        output_parts = [f"=== Runtime prerequisite: {prerequisite} ==="] if prerequisite else []
        entrypoint: Optional[Dict[str, Any]] = None
        steps = []
        sequence_toolchain: Dict[str, Any] = {}
        overall_success = True
        any_timed_out = False
        last_returncode = None
        for i, command in enumerate(commands, 1):
            # Raw Java runtime verification compiles into an isolated class
            # root. javac requires the -d destination to exist; create only
            # that explicitly named workspace-relative directory here rather
            # than adding a shell-specific `mkdir` command to the portable
            # argv sequence.
            if command and os.path.basename(command[0]) == "javac" and "-d" in command:
                destination_index = command.index("-d") + 1
                if destination_index < len(command):
                    destination = command[destination_index]
                    if not os.path.isabs(destination):
                        destination_path = os.path.join(self.workspace_path, destination)
                        # Symlink-safe containment (kriya/policy/filesystem.py's
                        # is_within_scope) - a bare os.path.abspath/commonpath
                        # check has no symlink resolution and reopens exactly
                        # the sibling-prefix/symlink bypass that primitive was
                        # built to close.
                        scope = make_workspace_scope(self.workspace_path)
                        if is_within_scope(scope, destination_path):
                            os.makedirs(destination_path, exist_ok=True)
            step_label = f"=== Step {i}/{len(commands)}: {' '.join(command)} ==="
            try:
                res = self._run_runtime_step(
                    command, timeout, stdin_payload=stdin_payload if i == len(commands) else None,
                )
            except Exception as e:
                output_parts.append(f"{step_label}\nFailed to execute: {e}")
                steps.append({
                    "command": list(command), "exit_code": None, "stdout": "",
                    "stderr": f"Failed to execute: {e}", "timed_out": False,
                })
                overall_success = False
                last_returncode = None
                break
            output_parts.append(f"{step_label}\n{res['stdout']}\n{res['stderr']}")
            steps.append({
                "command": list(command), "exit_code": res["returncode"],
                "stdout": res["stdout"], "stderr": res["stderr"],
                "timed_out": res["timeout"], **execution_evidence(res),
                # PRD-025: capture truncation travels with the step it came
                # from; absent when nothing was lost.
                **{
                    key: res[key] for key in ("stdout_lost_chars", "stderr_lost_chars")
                    if res.get(key)
                },
            })
            sequence_toolchain = execution_evidence(res) or sequence_toolchain
            last_returncode = res["returncode"]
            if res["timeout"]:
                any_timed_out = True
                overall_success = False
                break
            if res["returncode"] != 0:
                overall_success = False
                # D4: diagnose on the fresh build, before anything cleans it.
                entrypoint = entrypoint or self.diagnose_runtime_entrypoint(command)

        return {
            "success": overall_success and not any_timed_out,
            "timed_out": any_timed_out,
            "returncode": last_returncode,
            "output": "\n\n".join(output_parts),
            "steps": steps,
            "runtime_prerequisite": prerequisite,
            "entrypoint_diagnosis": entrypoint,
            **sequence_toolchain,
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
