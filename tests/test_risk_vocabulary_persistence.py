"""Risk / process-profile vocabulary: persisted and serialized forms are byte-identical across the C2 move (full modularization,
slice C2; ``kriya/workflow/{triage,process_profile}.py`` -> ``kriya/policy/risk.py``).

How the vocabulary is persisted (TRACED at C1 HEAD, before the move): only through ``EngineeringRoute.to_dict()`` /
``ProcessProfile.to_dict()`` (enum ``.value`` / ``.name``, never a module path): ``ControlState.to_dict()`` (control/state.py),
``GenerationState.generation_metrics()`` -> the trace (workflow/state.py, core/trace.py), ``metrics.evidence``. No production code
pickles these objects or records ``__module__``/``__qualname__``/``repr`` of them (grep at C1 HEAD: no ``pickle`` in ``kriya/``). The
forms a ``json.dumps(..., default=str)`` would take (``str``/``repr``/``format``) contain no module path either.

``tests/golden/policy_risk/serialized_forms.json`` was WRITTEN AT C1 HEAD (before any C2 edit) by ``serialized_forms()`` below; this
test recomputes it. A pickle written before the move names ``kriya.workflow.triage``; ``OLD_PICKLES`` (captured at C1 HEAD) must still
load to the same members (the facade keeps the old path importable). Regenerate the golden only for a deliberate format change:
``.venv/bin/python -m tests.test_risk_vocabulary_persistence --write``.
"""
from __future__ import annotations

import base64
import json
import pickle
import sys
from pathlib import Path
from typing import Dict

from kriya.control.state import ControlState
from kriya.workflow.process_profile import (
    HEAVY_PROFILE,
    LIGHT_PROFILE,
    STANDARD_PROFILE,
    ContextDepth,
    ProcessProfile,
    VerificationTier,
)
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass, escalate_risk

GOLDEN = Path(__file__).parent / "golden" / "policy_risk" / "serialized_forms.json"

# pickle.dumps(member, protocol=4), base64, captured at C1 HEAD (module path kriya.workflow.*)
OLD_PICKLES: Dict[str, str] = {
    "RiskClass.HIGH": "gASVLQAAAAAAAACMFWtyaXlhLndvcmtmbG93LnRyaWFnZZSMCVJpc2tDbGFzc5STlEsDhZRSlC4=",
    "ChangeKind.TASK": "gASVMwAAAAAAAACMFWtyaXlhLndvcmtmbG93LnRyaWFnZZSMCkNoYW5nZUtpbmSUk5SMBHRhc2uUhZRSlC4=",
    "ExecutionWeight.HEAVY": "gASVOQAAAAAAAACMFWtyaXlhLndvcmtmbG93LnRyaWFnZZSMD0V4ZWN1dGlvbldlaWdodJSTlIwFaGVhdnmUhZRSlC4=",
    "ContextDepth.NARROW": "gASVQAAAAAAAAACMHmtyaXlhLndvcmtmbG93LnByb2Nlc3NfcHJvZmlsZZSMDENvbnRleHREZXB0aJSTlIwGbmFycm93lIWUUpQu",
    "VerificationTier.STANDARD": "gASVRgAAAAAAAACMHmtyaXlhLndvcmtmbG93LnByb2Nlc3NfcHJvZmlsZZSMEFZlcmlmaWNhdGlvblRpZXKUk5SMCHN0YW5kYXJklIWUUpQu",
}


def _route() -> EngineeringRoute:
    impact = ImpactVector(files_touched=2, symbols_impacted=3, downstream_references=1, public_contract_change=True)
    route = EngineeringRoute(kind=ChangeKind.ENHANCEMENT, impact=impact, initial_risk_class=RiskClass.MEDIUM,
                             current_risk_class=RiskClass.MEDIUM, max_observed_risk_class=RiskClass.MEDIUM,
                             execution_weight=ExecutionWeight.STANDARD, deterministic_signals={"scope": "narrow"},
                             reason_codes=["public_contract_change"], initial_impact=impact,
                             initial_execution_weight=ExecutionWeight.STANDARD)
    return escalate_risk(route, RiskClass.HIGH, ["post_architect:security_boundary_change"])


def serialized_forms() -> Dict[str, str]:
    route = _route()
    forms: Dict[str, str] = {}
    for enum in (ChangeKind, RiskClass, ExecutionWeight, ContextDepth, VerificationTier):
        for member in enum:
            key = f"{enum.__name__}.{member.name}"
            forms[key + ":str"] = str(member)
            forms[key + ":repr"] = repr(member)
            forms[key + ":format"] = f"{member}"
            forms[key + ":json_default_str"] = json.dumps({"v": member}, default=str)
    forms["route.to_dict"] = json.dumps(route.to_dict(), sort_keys=True)
    forms["route.repr"] = repr(route)
    forms["route.json_default_str"] = json.dumps({"route": route}, default=str, sort_keys=True)
    for profile in (LIGHT_PROFILE, STANDARD_PROFILE, HEAVY_PROFILE):
        forms[f"profile.{profile.execution_weight.value}.to_dict"] = json.dumps(profile.to_dict(), sort_keys=True)
        forms[f"profile.{profile.execution_weight.value}.repr"] = repr(profile)
    state = ControlState(schema_version=1, run_id="run-1", engineering_route=route, process_profile=HEAVY_PROFILE,
                         created_at="2026-10-16T00:00:00+00:00", updated_at="2026-10-16T00:00:00+00:00")
    forms["control_state.to_dict"] = json.dumps(state.to_dict(), sort_keys=True)
    forms["class_names"] = json.dumps([cls.__name__ for cls in (ChangeKind, RiskClass, ExecutionWeight, ImpactVector,
                                                                 EngineeringRoute, ContextDepth, VerificationTier, ProcessProfile)])
    return forms


def test_persisted_and_serialized_forms_are_byte_identical_to_the_pre_move_golden():
    assert serialized_forms() == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_a_pickle_written_before_the_move_still_loads_to_the_same_member():
    members = {"RiskClass.HIGH": RiskClass.HIGH, "ChangeKind.TASK": ChangeKind.TASK, "ExecutionWeight.HEAVY": ExecutionWeight.HEAVY,
               "ContextDepth.NARROW": ContextDepth.NARROW, "VerificationTier.STANDARD": VerificationTier.STANDARD}
    assert set(OLD_PICKLES) == set(members)
    for name, payload in OLD_PICKLES.items():
        assert pickle.loads(base64.b64decode(payload)) is members[name], name   # noqa: S301 - bytes captured by this test file


def test_the_old_paths_are_the_policy_owned_objects():
    import kriya.policy.risk as risk  # noqa: PLC0415
    import kriya.workflow.process_profile as process_profile  # noqa: PLC0415
    import kriya.workflow.triage as triage  # noqa: PLC0415

    assert risk.RiskClass is triage.RiskClass
    for name in ("ChangeKind", "RiskClass", "ExecutionWeight", "ImpactVector", "EngineeringRoute", "determine_execution_weight"):
        assert getattr(triage, name) is getattr(risk, name), name
        assert getattr(risk, name).__module__ == "kriya.policy.risk", name
    for name in ("ContextDepth", "VerificationTier", "ProcessProfile", "ExecutionWeight"):
        assert getattr(process_profile, name) is getattr(risk, name), name
    assert process_profile.HEAVY_PROFILE.verification_tier is risk.VerificationTier.HEAVY
    assert triage.EngineeringTriageService.__module__ == "kriya.workflow.triage"         # the classification logic stays in the kernel


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(serialized_forms(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN}")
