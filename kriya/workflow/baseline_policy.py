"""PRD-024: the brownfield full-regression baseline policy's ``auto`` trigger
and the environment identity PRE and POST are compared under.

``autonomy.brownfield_full_regression_baseline_policy``:

- ``required``: always capture the pristine full-suite PRE baseline before
  the first Developer mutation; an indeterminate baseline stops the run
  before generation (production seals this).
- ``disabled``: never capture one; any POST failure blocks, as before.
- ``auto``: capture it exactly when ``decide_auto_baseline`` says the change
  is a risky brownfield change the baseline can actually protect - a pure,
  deterministic function of signals Kriya already computed (the engineering
  route and its impact vector, the planned files, the repository's existing
  tests and git identity). No model decides. A triggered ``auto`` baseline is
  then as binding as ``required``: indeterminate stops the run, and POST
  NEW/CHANGED/NOT_COMPARABLE blocks.

``baseline_environment_identity`` records what PRE ran under (execution mode
plus the PRD-011 toolchain fingerprint, which is attested only for contained
execution and otherwise says why not). POST computes the same over the
candidate's own toolchain declarations, so a candidate that changes the
toolchain makes the comparison NOT_COMPARABLE instead of silently comparing
two different environments.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

_BROWNFIELD_KINDS = ("task", "enhancement", "refactor")
_RISKY_IMPACT = ("public_contract_change", "dependency_change", "build_system_change", "persistence_change",
                 "security_boundary_change", "shared_entrypoint_change")
_IGNORED_DIRS = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv", "__pycache__"}


@dataclass(frozen=True)
class AutoBaselineDecision:
    required: bool
    reasons: Tuple[str, ...]
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "reasons": list(self.reasons)}


def _existing_tests(workspace_path: str) -> List[str]:
    from kriya.workflow.file_resolution import is_runnable_test_file

    tests: List[str] = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), workspace_path)
            if is_runnable_test_file(rel):
                tests.append(rel)
    return sorted(tests)


def _tested_sources(workspace_path: str, sources: Iterable[str], tests: Iterable[str]) -> List[str]:
    """Existing sources an existing test names (module or type stem)."""
    stems = {src: os.path.splitext(os.path.basename(src))[0] for src in sources}
    hit: List[str] = []
    for test in tests:
        try:
            with open(os.path.join(workspace_path, test), "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        for src, stem in stems.items():
            if src not in hit and len(stem) > 2 and re.search(rf"(?<![\w$]){re.escape(stem)}(?![\w$])", text):
                hit.append(src)
    return sorted(hit)


def decide_auto_baseline(
    route: Any, planned_files: Iterable[str], workspace_path: str, *, workspace_revision: Optional[str],
) -> AutoBaselineDecision:
    """Whether ``auto`` captures the PRE full-suite baseline for this run.

    Preconditions (all needed, otherwise not triggered - a baseline could not
    protect anything): a brownfield route (task, enhancement, refactor); a
    git workspace identity; an existing test suite; at least one planned
    change to an existing non-test file. Then any risk signal triggers it:
    risk at least MEDIUM, a non-LIGHT execution weight, a refactor, a risky
    impact component, or a changed source an existing test names. A LOW,
    LIGHT change to an untested file stays cheap (not triggered)."""
    from kriya.workflow.file_resolution import is_runnable_test_file

    kind = getattr(getattr(route, "kind", None), "value", None)
    if route is None or kind not in _BROWNFIELD_KINDS:
        return AutoBaselineDecision(False, (f"route {kind or 'unavailable'} is not a brownfield change",))
    if workspace_revision is None:
        return AutoBaselineDecision(False, ("no git workspace identity to bind a baseline to",))
    tests = _existing_tests(workspace_path)
    if not tests:
        return AutoBaselineDecision(False, ("the repository has no existing tests",))
    changed = sorted({p for p in planned_files
                      if os.path.isfile(os.path.join(workspace_path, p)) and not is_runnable_test_file(p)})
    if not changed:
        return AutoBaselineDecision(False, ("no existing source file is planned to change",))
    impact = getattr(route, "impact", None)
    risk = getattr(route, "max_observed_risk_class", None)
    weight = getattr(getattr(route, "execution_weight", None), "value", None)
    tested = _tested_sources(workspace_path, changed, tests)
    reasons: List[str] = []
    if risk is not None and int(risk) >= 2:
        reasons.append(f"risk {getattr(risk, 'name', risk)}")
    if weight and weight != "light":
        reasons.append(f"execution weight {weight}")
    if kind == "refactor":
        reasons.append("refactor (behaviour must be preserved)")
    reasons.extend(f"impact {name}" for name in _RISKY_IMPACT if getattr(impact, name, False))
    if tested:
        reasons.append(f"changed source(s) named by existing tests: {', '.join(tested)}")
    signals = {"route_kind": kind, "risk": getattr(risk, "name", None), "execution_weight": weight,
               "existing_tests": len(tests), "changed_existing_sources": changed, "tested_changed_sources": tested}
    if not reasons:
        return AutoBaselineDecision(False, ("low-risk, light change to untested source",), signals)
    return AutoBaselineDecision(True, tuple(reasons), signals)


def effective_baseline_policy(policy: str, decision: Optional[AutoBaselineDecision]) -> str:
    """``required``/``disabled`` stand; ``auto`` becomes one of them."""
    if policy != "auto":
        return policy
    return "required" if decision is not None and decision.required else "disabled"


def baseline_environment_identity(
    workspace_path: str, autonomy_cfg: Any, *, goal: Optional[str] = None,
    candidate_files: Optional[Mapping[str, str]] = None,
) -> str:
    """What a validation run executes under: the execution mode and the
    PRD-011 toolchain fingerprint (value and basis; UNAVAILABLE on the host,
    stated as such rather than guessed).

    PRE and POST are computed the same way - over the workspace's own
    toolchain declarations, overlaid with ``candidate_files`` (POST's
    changed declarations; none for PRE) - so a candidate that edits
    ``pom.xml`` without changing the toolchain (a new dependency) has the
    same identity as PRE; only a real toolchain change differs."""
    from kriya.tools.toolchain_identity import ALL_TOOLCHAIN_DECLARATION_FILES
    from kriya.workflow.resume_fingerprints import toolchain_fingerprint

    declarations: Dict[str, str] = {}
    for name in ALL_TOOLCHAIN_DECLARATION_FILES:
        path = os.path.join(workspace_path, name)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                declarations[name] = handle.read()
    declarations.update({k: v for k, v in (candidate_files or {}).items() if k in ALL_TOOLCHAIN_DECLARATION_FILES})
    fingerprint = toolchain_fingerprint(workspace_path, autonomy_cfg, goal=goal,
                                        candidate_files=declarations or None, declaration_mutable=True)
    mode = ("contained" if getattr(autonomy_cfg, "contained_execution_required", False) is True
            else "sandbox" if getattr(autonomy_cfg, "sandbox_execution", False) else "host")
    return json.dumps({"execution": mode, "toolchain": fingerprint.value, "basis": fingerprint.basis},
                      sort_keys=True)
