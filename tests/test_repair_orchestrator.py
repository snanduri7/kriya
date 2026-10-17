"""The retry/repair preparation and the coordinated repair (full modularization W4, E7): characterization written and
green at c41e0ff, BEFORE the code moves out of ``kriya/workflow/attempt.py`` (OI-27).

What a retry hands the Developer is pinned at boundaries the move does not rebind: the Developer agent's
``run_generation`` keyword arguments (the full prompt texts the retry families build, normalized), the ``RetryPackage``
every retry preparation builds (captured where the preparation reads it, ``RetryPackage.prior_error_text``), the run
events the preparation and the coordinated repair record, the attempt-evidence retry fingerprints, and the run state
the preparation leaves behind (the projection context ``known_target_context_items`` / member items / omitted members,
the evidence fingerprint and its first-seen record, the sampling resamples, the authority expansions).

Two levels:

- an engine-level scripted run (``tests/state_machine/_scripted_run.py``): the primary keeps failing through attempt 1,
  the targeted retries, the fallback-targeted retry and the full-set retries escalated to the fallback; under both
  Developer response protocols and with the retry temperature unset (the binding's own temperature);
- attempt-level runs of ``run_attempt`` for every retry family on a fixture whose evidence sources each name a
  DIFFERENT member (a failure locator, a rejected SEARCH text, the retrieval grounding, the goal's named members, the
  unfit-target fallback), a fallback whose window and temperature differ from the primary's, an excluded hinted path,
  a target not yet written, the reference-material marker in the error context: targeted, fallback-targeted,
  missing-files, the full-set retry (scoped and unscoped), API-contract recovery (all three phases), the
  verification-only unit (which sends no Developer request), the no-progress and sampling decisions, and a
  coordinated multi-file repair whose RepairContract is derived by the real ``_record_process_boundary_obligation``
  from a recorded surefire crash (attempt-level: a Legacy engine run has no subtask id, so it never opens one).

Every value is normalized with the kernel golden harness's ``Normalizer``; the golden
``tests/golden/repair/retry_preparation.json`` was WRITTEN from c41e0ff (``KRIYA_WRITE_REPAIR_GOLDEN=1``) and is only
ever compared afterwards. The unit pins below the goldens pin what the preparation's values are built FROM.
"""
import asyncio
import dataclasses
import enum
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import developer_double

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, ModelCapabilities
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.core.kernel import Kernel
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import attempt
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.open_findings import OpenFinding
from kriya.workflow.retry_package import RetryPackage
from kriya.workflow.state import APIContractRecovery, APIContractRecoveryPhase, GenerationState

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

GOLDEN = Path(__file__).resolve().parent / "golden" / "repair" / "retry_preparation.json"
WRITE_ENV = "KRIYA_WRITE_REPAIR_GOLDEN"
RETRY_EVENTS = frozenset({
    "attempt.started", "context.retry_goal_member_grounding", "context.retry_member_hint_package",
    "context.retry_target_source", "context.search_evidence_grounding", "authority.expansion", "retry.progress_decision",
    "retry.sampling_resample", "repair_retry", "repair_group_started", "developer_repair_call", "candidate_edit_staged",
    "repair_contract_created", "repair_contract_satisfied", "context.known_target_package",
    "context.known_target_goal_member_fallback",
})
TEXT_KWARGS = ("task_description", "existing_code_context", "prior_error_context", "design_context",
               "error_source_context", "extra_fix_instruction")
SCALAR_KWARGS = ("model_override", "base_url_override", "retry_temperature", "known_target_files", "implicated_files",
                 "sibling_content_budget", "files_with_current_content")
MODE_LINES = ("Targeted retry", "Fallback-targeted retry", "Escalating compilation attempt", "Missing-file recovery",
              "API_CONTRACT_RECOVERY", "Trying ONE targeted fix", "First full-set attempt")


class _Stop(Exception):
    """Ends an attempt-level run at the Developer boundary once the request is recorded."""


@pytest.fixture
def _git_identity(monkeypatch):
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _plain(value):
    """A JSON-able form of a recorded value (dataclasses, enums, tuples, sets)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "to_dict"):
        return _plain(value.to_dict())
    return value


def _item_form(item):
    return None if item is None else {
        "path": item.path, "tier": item.tier, "is_exact": item.is_exact, "member_id": item.member_id,
        "omitted_regions": item.omitted_regions, "revision": item.revision, "reason": item.reason,
        "content": _digest(item.content or ""),
    }


def _state_form(state, norm):
    """What the retry preparation leaves on the run state (the projection context and the evidence record)."""
    return norm.value({
        "known_target_context_items": {path: _item_form(item)
                                       for path, item in sorted(state.known_target_context_items.items())},
        "known_target_member_items": {path: [_item_form(item) for item in items]
                                      for path, items in sorted(state.known_target_member_items.items())},
        "known_target_omitted_members": _plain(state.known_target_omitted_members),
        "last_retry_evidence_fingerprint": _plain(state.budgets.last_retry_evidence_fingerprint),
        "retry_evidence_seen": _plain(state.retry_evidence_seen),
        "sampling_resamples": state.sampling_resamples,
        "authority_expansions": [_plain(expansion) for expansion in state.authority_expansions],
        "model_hops": list(state.model_hops),
        "last_attempt_mode": state.last_attempt_mode,
        "repair_contract": None if state.repair_contract is None else {
            "id": state.repair_contract.id, "status": state.repair_contract.status.value,
            "active_group_id": state.repair_contract.active_group_id,
            "generation_order": list(state.repair_contract.generation_order),
            "groups": [[group.id, list(group.generation_order)] for group in state.repair_contract.repair_groups],
        },
    })


class _Recorder:
    """The boundaries the move does not rebind: RetryPackage reads, run events, the attempt-evidence fingerprints and
    the Developer agent's request."""

    def __init__(self, norm):
        self.norm = norm
        self.packages: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.fingerprints: List[str] = []
        self.requests: List[Dict[str, Any]] = []
        self.state_after_preparation: List[Dict[str, Any]] = []
        self.states: List[Any] = []
        self._seen = set()
        self._held: List[Any] = []

    def request(self, kwargs):
        form = {key: self.norm.text(kwargs[key]) if isinstance(kwargs.get(key), str) else kwargs.get(key)
                for key in TEXT_KWARGS if key in kwargs}
        for key in SCALAR_KWARGS:
            if key in kwargs:
                value = kwargs[key]
                form[key] = sorted(value) if isinstance(value, (set, frozenset)) else _plain(value)
        form["operation_by_file"] = {path: getattr(op, "value", op)
                                     for path, op in (kwargs.get("operation_by_file") or {}).items()}
        form["default_operation"] = getattr(kwargs.get("default_operation"), "value", kwargs.get("default_operation"))
        self.requests.append(self.norm.value(form))

    def patches(self):
        real_prior = RetryPackage.prior_error_text
        real_record = GenerationState.record_event
        real_note = attempt_evidence_scope.note_retry_evidence
        recorder = self

        def prior_error_text(package):
            if id(package) not in recorder._seen:
                recorder._seen.add(id(package))
                recorder._held.append(package)   # held: a freed package's id may be reused by the next one
                recorder.packages.append(recorder.norm.value({
                    "package": _plain(package), "prior_error_text": real_prior(package),
                    "render_context": package.render_context()}))
            return real_prior(package)

        def record_event(state, event):
            if not recorder.states or recorder.states[-1] is not state:
                recorder.states.append(state)
            if event.kind in RETRY_EVENTS:
                details = dict(event.details or {})
                recorder.events.append(recorder.norm.value({
                    "kind": event.kind, "attempt": event.attempt, "source": event.source,
                    "authority": event.authority, "message": event.message, "details": _plain(details)}))
            return real_record(state, event)

        def note_retry_evidence(fingerprint_hash):
            recorder.fingerprints.append(fingerprint_hash)
            return real_note(fingerprint_hash)
        return (patch.object(RetryPackage, "prior_error_text", new=prior_error_text),
                patch.object(GenerationState, "record_event", new=record_event),
                patch.object(attempt_evidence_scope, "note_retry_evidence", new=note_retry_evidence))

    def form(self):
        return {"retry_packages": self.packages, "events": self.events, "fingerprints": self.fingerprints,
                "developer_requests": self.requests}


LONG_TEXT = 1200   # a longer recorded text is kept as its digest and length (byte identity, a reviewable golden)


def _bounded(value):
    if isinstance(value, str) and len(value) > LONG_TEXT:
        return f"{_digest(value)} ({len(value)} chars)"
    if isinstance(value, dict):
        return {key: _bounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bounded(item) for item in value]
    return value


def _render(form):
    return json.dumps(_bounded(form), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _compare(key, form):
    rendered = _render(form)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    if os.environ.get(WRITE_ENV):
        golden[key] = json.loads(rendered)
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(_render(golden), encoding="utf-8")
        pytest.skip(f"golden written for {key}")
    assert key in golden, f"no golden for {key}"
    assert rendered == _render(golden[key])


# ============================================================================= engine level: the scripted run

def _engine_capture(tmp_path, protocol, *, retry_temperature_unset=False):
    from _scripted_run import FALLBACK, engine, git_workspace, script

    with kg.isolated_working_directory(tmp_path) as cwd:
        workspace = git_workspace(tmp_path, {"calc.py": kg.BASE, "test_calc.py": kg.TESTS})
        norm = kg.Normalizer({str(Path(workspace).resolve()): "ws", workspace: "ws", str(tmp_path): "tmp",
                              str(Path(str(tmp_path)).resolve()): "tmp", str(cwd): "cwd"})
        recorder = _Recorder(norm)

        def configure(cfg):
            cfg.autonomy.developer_response_protocol = protocol
            if retry_temperature_unset:
                cfg.llm.retry_temperature = None
                cfg.llm.temperature = 0.15
                cfg.llm_chain[0].temperature = 0.55
            else:
                cfg.llm.retry_temperature = 0.3
        eng, llm, _cfg = engine(chain=True, configure=configure)
        script(llm, plan="Step 1: add mul", target="calc.py",
               developer=lambda model, n, _text: kg.GOOD if (model == FALLBACK and n > 7) else kg.BROKEN)
        real_generation = eng.developer.run_generation

        async def spy_generation(**kwargs):
            recorder.request(kwargs)
            recorder.state_after_preparation.append(_state_form(recorder.states[-1], norm))
            return await real_generation(**kwargs)
        eng.developer.run_generation = spy_generation
        package_patch, event_patch, note_patch = recorder.patches()
        with package_patch, event_patch, note_patch:
            result = asyncio.run(eng.run_generation_workflow(
                goal=kg.GOAL, workspace_path=workspace, approval_callback=MagicMock(return_value=True)))
    form = recorder.form()
    form["state_after_preparation"] = recorder.state_after_preparation
    form["quality_gates_passed"] = result.get("quality_gates_passed")
    return _hash_tokens(form)


_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def _hash_tokens(form):
    """The scripted run's failure signature digests the pytest output, which quotes the tmp workspace: each distinct
    fingerprint digest becomes ``<digest:N>`` in order of first appearance, so the golden keeps which attempts saw
    the SAME evidence (the no-progress and resample decisions) without the path-dependent values."""
    tokens: Dict[str, str] = {}

    def token(match):
        return tokens.setdefault(match.group(0), f"<digest:{len(tokens) + 1}>")
    return json.loads(_HEX64.sub(token, json.dumps(form, sort_keys=True)))


@pytest.mark.parametrize("protocol", ["structured", "legacy_strict"])
def test_every_retry_of_the_scripted_run_prepares_byte_identical_context(protocol, tmp_path, _git_identity):
    form = _engine_capture(tmp_path, protocol)
    assert form["quality_gates_passed"] is True
    assert len(form["developer_requests"]) == 8 and form["retry_packages"]
    _compare(f"engine:{protocol}", form)


def test_the_scripted_run_with_the_retry_temperature_unset_resamples_at_each_bindings_own_temperature(
        tmp_path, _git_identity):
    form = _engine_capture(tmp_path, "structured", retry_temperature_unset=True)
    resamples = [event for event in form["events"] if event["kind"] == "retry.sampling_resample"]
    assert resamples, "the scripted run repeats identical evidence: a resample decision must be recorded"
    _compare("engine:structured:retry_temperature_unset", form)


# ============================================================================= attempt level: every retry family

ELEMENT = (
    "package org.example;\n"
    "\n"
    "public class Element extends Node {\n"
    "    private final String base;\n"
    "\n"
    "    public Element(String base) {\n"
    "        this.base = base;\n"
    "    }\n"
    "\n"
    "    public String attr(String key) {\n"
    "        String value = lookup(key);\n"
    "        return value == null ? \"\" : value;\n"
    "    }\n"
    "\n"
    "    public String absUrl(String key) {\n"
    "        String relative = attr(key);\n"
    "        return base + \"/\" + relative;\n"
    "    }\n"
    "\n"
    "    public String text() {\n"
    "        StringBuilder accum = new StringBuilder();\n"
    "        accum.append(base.trim());\n"
    "        return accum.toString();\n"
    "    }\n"
    "\n"
    "    private String lookup(String key) {\n"
    "        return key.isEmpty() ? null : key.toLowerCase();\n"
    "    }\n"
    "\n"
    "    public String id() {\n"
    "        return base;\n"
    "    }\n"
    "}\n"
)
NODE = (
    "package org.example;\n"
    "\n"
    "public class Node {\n"
    "    public String nodeName() {\n"
    "        return \"node\";\n"
    "    }\n"
    "\n"
    "    public int childCount() {\n"
    "        int counted = 0;\n"
    "        counted += 1;\n"
    "        return counted;\n"
    "    }\n"
    "\n"
    "    public String absUrl(String key) {\n"
    "        return key;\n"
    "    }\n"
    "}\n"
)
UTIL = "package org.example;\n\npublic class Util {\n    public static int twice(int x) {\n        return x * 2;\n    }\n}\n"
BASE_SOURCE = "package org.example;\n\npublic class Base {\n    public String root() {\n        return \"/\";\n    }\n}\n"
E, N, U = "src/org/example/Element.java", "src/org/example/Node.java", "src/org/example/Util.java"
BIG, BASE, MEDIUM, CATALOG = ("src/org/example/Big.java", "src/org/example/Base.java", "src/org/example/Medium.java",
                              "src/org/example/Catalog.java")
# The goal names Element.absUrl and Node.childCount (each file's own qualified member), the other qualified member
# (Node defines absUrl too) and five bare calls Element defines: more goal members than the per-file cap of four.
GOAL = ("Fix Element.absUrl so a relative key resolves: it calls attr(key), text(), lookup(key) and id(); keep "
        "Node.childCount unchanged.")
REFERENCE_MARKER = "\n\n=== Reference material found for this failure ===\nReference: an advisory snippet."


def _big_source():
    methods = "".join(
        f"    public int step{index}(int value) {{\n        int local = value + {index};\n"
        f"        return local * {index % 7 + 1};\n    }}\n\n" for index in range(1400))
    return ("package org.example;\n\npublic class Big {\n" + methods
            + "    public String process(String input) {\n        return input.trim();\n    }\n}\n")


def _class_source(name, methods):
    body = "".join(f"    public int {name.lower()}{index}(int value) {{\n        int local = value + {index};\n"
                   f"        return local * {index % 5 + 1};\n    }}\n\n" for index in range(methods))
    return (f"package org.example;\n\npublic class {name} {{\n" + body
            + "    public String resolve(String input) {\n        return input.strip();\n    }\n}\n")


def _workspace(tmp_path, *, big=False, crowded=False):
    files = {E: ELEMENT, N: NODE, U: UTIL, BASE: BASE_SOURCE}
    if big:
        files[BIG] = _big_source()
    if crowded:   # a target that fits the retry's room only while the base context is not counted, and that context
        files[MEDIUM] = _class_source("Medium", 290)
        files[CATALOG] = _class_source("Catalog", 380)
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


def _config(*, chain=True, retry_temperature=0.3, primary_temperature=0.2, fallback_temperature=0.55,
            fallback_patches=False):
    cfg = AppConfig()
    cfg.llm.model = "primary-coder:30b"
    cfg.llm.temperature = primary_temperature
    cfg.llm.retry_temperature = retry_temperature
    cfg.autonomy.run_verification_enabled = False
    fallback = FallbackModelConfig(model="fallback-coder:35b", base_url=cfg.llm.base_url,
                                   temperature=fallback_temperature, context_window=16384)
    if fallback_patches:   # a measured anchored-edit profile: the fallback may serve a patch-only attempt
        fallback.capabilities = ModelCapabilities(native_tool_calls=True, json_mode=True, reliable_multiline_json=True,
                                                  preferred_edit_protocol="small_native_tools")
    cfg.llm_chain = [fallback] if chain else []
    return cfg


def _ctx(workspace, cfg, **overrides):
    """An AttemptContext for one run_attempt (the defaults of tests/test_workflow.py::_minimal_attempt_ctx)."""
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": [], "command_source": "inferred",
                                                 "success_criteria": ""})
    run_verifier.grade = AsyncMock(return_value={"passed": False, "reasoning": "not requested", "likely_files": []})
    spec_compliance = AsyncMock()
    spec_compliance.check = AsyncMock(return_value={"compliant": True, "reasoning": "not requested",
                                                    "missing_requirements": [], "likely_files": []})
    defaults = dict(
        goal=GOAL, plan="Step 1: fix Element.absUrl", design="Design: Element resolves URLs",
        workspace_path=workspace, worktree_path=workspace, architect_files=[E, N], resume_state=None,
        run_id="repair-run", skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="", verification_contract_block="",
        recovery_contract_block="", required_files_prompt_block="REQUIRED FILES: Element.java, Node.java",
        required_dependencies_prompt_block="", expected_files_upfront=[E, N],
        architect_basename_to_path={"Element.java": E, "Node.java": N, "Util.java": U},
        chain=list(cfg.llm_chain), targeted_max_retries=3, stream_callback=None,
        approval_callback=lambda diffs, reason: True, active_skills=[], active_skill_rules_snapshot={},
        developer=developer_double(), run_verifier=run_verifier, spec_compliance=spec_compliance,
        skill_engine=MagicMock(), kernel=Kernel(config=cfg), max_retries=4, web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
    )
    defaults.update(overrides)
    if "migration_resolution" not in overrides:
        from kriya.workflow.migration import resolve_migration_resolution
        defaults["migration_resolution"] = resolve_migration_resolution(defaults["goal"], workspace)
    return attempt.AttemptContext(**defaults)


def _failure(**overrides):
    values = dict(
        type="compile", message="Element.java:16: error: cannot find symbol relative",
        raw_output="src/org/example/Element.java:16: error: cannot find symbol\n  symbol: variable relative",
        source="compiler", attempt=1, mode="targeted",
        likely_files=[E, N],
        file_locations=[FileLocation(E, 16), FileLocation(E, 11), FileLocation(N, None), FileLocation(U, 5),
                        FileLocation("src/org/example/Other.java", 3)],
        attempted_edits=[{"search": "    public int childCount() {\n        int counted = 0;",
                          "replace": "    public int childCount() {\n        int counted = 1;"}],
    )
    values.update(overrides)
    return Failure(**values)


def _state(workspace, *, attempt_number=1, mode="targeted", implicated=(E, N), failure=None, **fields):
    state = GenerationState()
    state.attempt_number = attempt_number
    state.last_attempt_mode = mode
    state.last_implicated_files = list(implicated)
    state.all_files_written = {E, U}
    state.last_failure = failure if failure is not None else _failure()
    state.error_context = "COMPILE ERROR in Element.java: cannot find symbol relative" + REFERENCE_MARKER
    state.last_error_source_context = {E: "    public String absUrl(String key) {"}
    # a recorded post-compile revision differs from a fresh hash only in name: the package must take it as recorded
    state.validated_file_revisions = {E: content_revision(ELEMENT), U: "sha256:validated-after-compile"}
    state.open_findings = [OpenFinding(failure_type="static_rule_violation", reason_code="RULE-7", files=(E,),
                                       source="static_analysis", evidence="Element.java:16 avoid string concat",
                                       first_attempt=1)]
    state.budgets.anchor_failure_counts = {N: 2}
    state.edit_anchor_loci = {E: [17]}
    state.budgets.last_failure_signature = "compile|cannot find symbol"
    state.all_original_contents = {E: ELEMENT, N: NODE}
    for key, value in fields.items():
        target = state.budgets if hasattr(state.budgets, key) and not hasattr(state, key) else state
        setattr(target, key, value)
    return state


def _attempt_capture(tmp_path, state, ctx, *, answers=None, runs=1, stop_at_compile=False, prelude=None):
    """run_attempt ``runs`` times on ``state``; each Developer request is recorded and answered from ``answers``
    (default: stop the attempt there). Returns the recorded form."""
    norm = kg.Normalizer({str(Path(str(tmp_path)).resolve()): "ws", str(tmp_path): "ws"})
    recorder = _Recorder(norm)
    calls = {"n": 0}

    async def run_generation(**kwargs):
        recorder.request(kwargs)
        recorder.state_after_preparation.append(_state_form(state, norm))
        calls["n"] += 1
        if answers is None:
            raise _Stop()
        return answers(calls["n"], kwargs)
    ctx.developer.run_generation = AsyncMock(side_effect=run_generation)
    outcomes = []
    package_patch, event_patch, note_patch = recorder.patches()
    compile_patch = patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", side_effect=_Stop()) \
        if stop_at_compile else patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
                                      return_value={"success": True, "output": ""})
    with package_patch, event_patch, note_patch, compile_patch, \
            patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}):
        if prelude is not None:
            prelude()
        for _ in range(runs):
            try:
                asyncio.run(attempt.run_attempt(state, ctx))
                outcomes.append("returned")
            except _Stop:
                outcomes.append("stopped at the Developer request" if not stop_at_compile else "stopped at compile")
            except QualityGateFailure as error:
                outcomes.append(norm.value({"type": error.failure.type, "message": error.failure.message,
                                            "raw_output": error.failure.raw_output,
                                            "diagnostics": _plain(error.failure.diagnostics),
                                            "likely_files": error.failure.likely_files}))
    form = recorder.form()
    form["state_after_preparation"] = recorder.state_after_preparation
    after = _state_form(state, norm)
    form["state_after"] = ("= the last state_after_preparation" if recorder.state_after_preparation
                           and after == recorder.state_after_preparation[-1] else after)
    form["outcomes"] = outcomes
    form["developer_calls"] = calls["n"]
    form["files_after"] = {rel: _digest(path.read_text(encoding="utf-8"))
                           for rel, path in sorted((str(p.relative_to(tmp_path)), p) for p in tmp_path.rglob("*.java"))}
    return form


def _retrieval(**overrides):
    # a duplicated retrieval name and a hint for a path that is no target of the retry
    hints = {E: ["text", "text"], N: ["nodeName"], U: ["twice"]}
    hints.update(overrides)
    return hints


def _family_targeted(tmp_path):
    workspace = _workspace(tmp_path)
    cfg = _config()
    return _state(workspace), _ctx(workspace, cfg, retrieval_member_hints=_retrieval(), established_files=[E, BASE]), {}


def _family_targeted_crowded(tmp_path):
    """The graph context (a matched 40k-character file) crowds the retry's room: the member package of the two
    failure-located targets is rendered in what that base context leaves (the large Medium target's overview no
    longer fits beside its member)."""
    workspace = _workspace(tmp_path, crowded=True)
    cfg = _config()
    state = _state(workspace, implicated=(E, MEDIUM), failure=_failure(
        file_locations=[FileLocation(E, 16), FileLocation(MEDIUM, 6)], likely_files=[E, MEDIUM], attempted_edits=[]))
    goal = "Fix Medium.resolve so it trims and Element.absUrl resolves relative keys."
    return state, _ctx(workspace, cfg, goal=goal, retrieval_member_hints={}, expected_files_upfront=[E, MEDIUM],
                       matched_files=[CATALOG]), {}


def _family_targeted_unfit_goal_member(tmp_path):
    workspace = _workspace(tmp_path, big=True)
    cfg = _config()
    state = _state(workspace, implicated=(E, BIG), failure=_failure(
        file_locations=[FileLocation(E, 16)], likely_files=[E, BIG], attempted_edits=[]))
    goal = "Fix Big.process so it keeps the input and Element.absUrl resolves relative keys."
    return state, _ctx(workspace, cfg, goal=goal, retrieval_member_hints={}, expected_files_upfront=[E, BIG]), {}


def _family_fallback_targeted(patches):
    def build(tmp_path):
        workspace = _workspace(tmp_path)
        cfg = _config(retry_temperature=None, fallback_patches=patches)
        state = _state(workspace, targeted_retry_count=3)
        return state, _ctx(workspace, cfg, retrieval_member_hints=_retrieval()), {}
    return build


def _family_missing_files(tmp_path):
    workspace = _workspace(tmp_path)
    cfg = _config()
    state = _state(workspace, implicated=(), mode="full_set")
    state.last_missing_files = ["Util.java"]
    return state, _ctx(workspace, cfg, architect_files=[E, N, U], expected_files_upfront=[E, N, U]), {}


def _family_full_set_unscoped(tmp_path):
    workspace = _workspace(tmp_path)
    cfg = _config()
    state = _state(workspace, implicated=(), mode="full_set", retry_count=1, targeted_retry_count=3,
                   fallback_targeted_attempted=True)
    return state, _ctx(workspace, cfg, retrieval_member_hints=_retrieval()), {}


def _family_full_set_scoped(tmp_path):
    workspace = _workspace(tmp_path)
    cfg = _config(chain=False)
    state = _state(workspace, mode="full_set", targeted_retry_count=3, fallback_targeted_attempted=True)
    return state, _ctx(workspace, cfg, retrieval_member_hints=_retrieval(), generation_dependencies={E: [N]}), {}


def _api_recovery(phase):
    recovery = APIContractRecovery.detected(
        [{"owner": E, "removed_signature": "public String absUrl(String key)"},
         {"owner": N, "removed_signature": "public int childCount()"}],
        ["src/test/org/example/ElementTest.java"], {E: "owner", N: "owner"})
    recovery.phase = phase
    return recovery


def _family_api_recovery(phase):
    def build(tmp_path):
        workspace = _workspace(tmp_path)
        cfg = _config()
        state = _state(workspace, implicated=(E,))
        state.api_contract_recovery = _api_recovery(phase)
        # a baseline owner longer than the 12,000-character cut the recovery prompt applies
        state.all_original_contents = {E: ELEMENT + "// baseline padding line\n" * 600, N: NODE}
        return state, _ctx(workspace, cfg, retrieval_member_hints=_retrieval()), {}
    return build


def _family_verification_only(tmp_path):
    workspace = _workspace(tmp_path)
    cfg = _config()
    state = _state(workspace)
    ctx = _ctx(workspace, cfg, write_scope_mode=WriteScopeMode.DENY_ALL, allowed_write_relpaths=[],
               required_verification=[{"type": "tool", "tool_name": "compile", "description": "the module compiles"}])
    return state, ctx, {"stop_at_compile": True}


def _family_repeated_evidence(reason_code, retry_temperature):
    def build(tmp_path):
        workspace = _workspace(tmp_path)
        cfg = _config(retry_temperature=retry_temperature)
        diagnostics = {"reason_code": reason_code} if reason_code else {}
        state = _state(workspace, failure=_failure(diagnostics=diagnostics))
        return state, _ctx(workspace, cfg, retrieval_member_hints=_retrieval()), {"runs": 2}
    return build


PRV06_CRASH = (
    "[ERROR] The forked VM terminated without properly saying goodbye. VM crash or System.exit called?\n"
    "[ERROR] Crashed tests:\n"
    "[ERROR] AppTest\n"
    "[ERROR] org.apache.maven.surefire.booter.SurefireBooterForkException: The forked VM terminated without properly "
    "saying goodbye. VM crash or System.exit called?\n"
)
APP, APP_TEST = "src/main/java/App.java", "src/test/java/AppTest.java"
APP_SOURCE = ("public class App {\n    public static void main(String[] args) {\n"
              "        if (args.length == 0) { System.exit(1); }\n    }\n}\n")
APP_TEST_SOURCE = "public class AppTest {\n    void testMain() { App.main(new String[0]); }\n}\n"


def _family_coordinated(tmp_path):
    """A RepairContract opened by the real process-boundary obligation from a recorded surefire crash, then one
    targeted attempt: the coordinated generation (the termination surface answers an anchored edit, the crashed
    consumer NO CHANGE NEEDED) through the shared staging pipeline to a passing compile/test gate."""
    for rel, content in ((APP, APP_SOURCE), (APP_TEST, APP_TEST_SOURCE)):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(content, encoding="utf-8")
    workspace = str(tmp_path)
    cfg = _config(chain=False)
    ledger = ObligationLedger()
    ctx = _ctx(workspace, cfg, goal="Make App report a missing argument without exiting the JVM.",
               architect_files=[APP, APP_TEST], expected_files_upfront=[APP, APP_TEST],
               architect_basename_to_path={"App.java": APP, "AppTest.java": APP_TEST},
               allowed_write_relpaths=[APP, APP_TEST], write_scope_mode=WriteScopeMode.ALLOWLIST,
               obligation_ledger=ledger, current_subtask_id="s3", required_files_prompt_block="")
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    state.all_files_written = {APP, APP_TEST}
    state.last_implicated_files = [APP_TEST]
    state.error_context = "TEST_PROCESS_TERMINATED: process boundary conflict" + REFERENCE_MARKER
    state.last_error_source_context = {APP_TEST: "    void testMain() { App.main(new String[0]); }"}
    state.last_failure = Failure(type="test_process_terminated", message="TEST_PROCESS_TERMINATED", raw_output=PRV06_CRASH,
                                 source="test", attempt=1, likely_files=[APP_TEST])
    for rel, content in ((APP, APP_SOURCE), (APP_TEST, APP_TEST_SOURCE)):
        state.known_target_context_items[rel] = make_context_item(
            path=rel, content=content, reason="known_target_full_source", source_type="named_in_request",
            trust_level="repository", tier="full", is_exact=True, revision=content_revision(content))

    def answers(_n, kwargs):
        (target,) = kwargs["known_target_files"]
        if target == APP:
            return [{"filepath": APP, "content": None, "edits": [{
                "search": "        if (args.length == 0) { System.exit(1); }",
                "replace": "        if (args.length == 0) { System.out.println(\"missing argument\"); return; }"}]}]
        return [{"filepath": APP_TEST, "content": None, "edits": []}]

    def prelude():
        attempt._record_process_boundary_obligation(ctx, state, violated=True, evidence={"raw_output": PRV06_CRASH})
        assert state.repair_contract is not None, "the recorded crash must open a coordinated RepairContract"
    return state, ctx, {"answers": answers, "prelude": prelude}


FAMILIES = {
    "coordinated_repair": _family_coordinated,
    "targeted_crowded": _family_targeted_crowded,
    "targeted": _family_targeted,
    "targeted_unfit_goal_member": _family_targeted_unfit_goal_member,
    "fallback_targeted": _family_fallback_targeted(True),
    "fallback_targeted_incompatible": _family_fallback_targeted(False),
    "missing_files": _family_missing_files,
    "full_set_unscoped": _family_full_set_unscoped,
    "full_set_scoped": _family_full_set_scoped,
    "api_contract_recovery_restore": _family_api_recovery(APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT),
    "api_contract_recovery_repair_behavior": _family_api_recovery(APIContractRecoveryPhase.REPAIR_BEHAVIOR),
    "api_contract_recovery_await_terminal": _family_api_recovery(APIContractRecoveryPhase.AWAIT_TERMINAL_SUCCESS),
    "verification_only": _family_verification_only,
    "repeated_deterministic_evidence": _family_repeated_evidence("ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED", 0.3),
    "repeated_evidence_resampled": _family_repeated_evidence(None, 0.3),
    "repeated_evidence_not_sampled": _family_repeated_evidence(None, 0.0),
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_retry_family_prepares_and_renders_byte_identical_context(family, tmp_path, _git_identity):
    state, ctx, options = FAMILIES[family](tmp_path)
    form = _attempt_capture(tmp_path, state, ctx, **options)
    _compare(f"attempt:{family}", form)


def test_a_coordinated_repair_across_three_groups_carries_each_participants_candidate_forward(tmp_path, _git_identity):
    """The coordinated generation on its own (a build file, a source and a test: three RepairGroups in role order):
    each participant's request (the coordinated retry prompt with every earlier candidate, the retry temperature, the
    implicated participants, the operation), the group/call/staged events, and the returned results and candidate
    view."""
    from kriya.workflow.operations import CodeOperation
    from kriya.workflow.repair_contract import RepairContract, RepairKind, _derive_repair_groups

    pom = "pom.xml"
    sources = {pom: "<project><artifactId>app</artifactId></project>\n", APP: APP_SOURCE, APP_TEST: APP_TEST_SOURCE}
    for rel, content in sources.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(content, encoding="utf-8")
    paths = tuple(sorted(sources))
    roles = {pom: "build_manifest", APP: "termination_surface", APP_TEST: "crashed_consumer"}
    groups = _derive_repair_groups(paths, roles)
    assert len(groups) == 3, [group.id for group in groups]
    contract = RepairContract(
        id="repair.s9.attempt.subtask.s9.process_boundary_compatibility",
        source_obligation_ids=("attempt.subtask.s9.process_boundary_compatibility",), kind=RepairKind.COORDINATED,
        repair_intent="Resolve a process-boundary conflict across the build, the source and the test.",
        must_fix=(f"{APP} must not terminate the process",), must_preserve=(f"{APP_TEST} keeps exercising main",),
        participating_artifacts=paths, participant_roles=roles, repair_groups=groups,
        generation_order=tuple(p for g in groups for p in g.generation_order), created_attempt=2,
        immediate_correction_targets=(APP,), active_group_id=groups[0].id)
    state = GenerationState()
    state.attempt_number = 3
    state.all_files_written = set(paths)
    state.error_context = "TEST_PROCESS_TERMINATED" + REFERENCE_MARKER
    state.last_error_source_context = {APP: "        if (args.length == 0) { System.exit(1); }"}
    ctx = _ctx(str(tmp_path), _config(chain=False, retry_temperature=0.45), architect_files=list(paths),
               expected_files_upfront=list(paths), ecosystem_invariant_block="ECOSYSTEM: maven",
               verification_contract_block="VERIFY: mvn test")
    norm = kg.Normalizer({str(Path(str(tmp_path)).resolve()): "ws", str(tmp_path): "ws"})
    recorder = _Recorder(norm)

    async def run_generation(**kwargs):
        recorder.request(kwargs)
        (target,) = kwargs["known_target_files"]
        if target == pom:
            return [{"filepath": pom, "content": "<project><artifactId>app</artifactId><v>2</v></project>\n"}]
        if target == APP:
            return [{"filepath": APP, "content": None, "edits": [{
                "search": "        if (args.length == 0) { System.exit(1); }",
                "replace": "        if (args.length == 0) { return; }"}]}]
        return [{"filepath": APP_TEST, "content": None, "edits": []}]
    ctx.developer.run_generation = AsyncMock(side_effect=run_generation)
    package_patch, event_patch, note_patch = recorder.patches()
    with package_patch, event_patch, note_patch:
        results, candidate_view = asyncio.run(attempt._run_coordinated_repair_generation(
            state, ctx, contract, "BASE CONTEXT\n", None, CodeOperation.REPAIR_WITH_PATCH))
    form = recorder.form()
    form["results"] = norm.value(_plain(results))
    form["candidate_view"] = {path: _digest(text) for path, text in sorted(candidate_view.items())}
    form["active_group_id"] = contract.active_group_id
    assert [request["known_target_files"] for request in form["developer_requests"]] == [[pom], [APP], [APP_TEST]]
    _compare("direct:coordinated_three_groups", form)


# ============================================================================= unit pins: what each value is built FROM

CHILD_COUNT_SEARCH = "    public int childCount() {\n        int counted = 0;"
ABS_URL_SEARCH = "    public String absUrl(String key) {\n        return key;"


@pytest.fixture
def _fixture(tmp_path):
    workspace = _workspace(tmp_path)
    return workspace, _ctx(workspace, _config(), retrieval_member_hints=_retrieval())


def _failed(**fields):
    state = GenerationState()
    state.attempt_number = 2
    state.last_failure = Failure(type="compile", message="m", raw_output="r", source="compiler", attempt=1, **fields)
    return state


def test_failure_locators_ground_only_authorized_targets_in_sorted_member_order(_fixture):
    _workspace_path, ctx = _fixture
    state = _failed(file_locations=[FileLocation(E, 31), FileLocation(E, 16), FileLocation(E, 21), FileLocation(E, 11),
                                    FileLocation(E, 16), FileLocation(U, 5), FileLocation(N, None)])
    assert attempt._resolve_retry_member_hints(ctx, state, [E, N]) == {
        E: ["Element.absUrl", "Element.attr", "Element.id", "Element.text"]}


def test_a_rejected_search_text_grounds_a_target_only_after_an_anchor_failure_and_never_over_a_locator(_fixture):
    _workspace_path, ctx = _fixture
    edits = [{"search": CHILD_COUNT_SEARCH, "replace": "x"}]
    state = _failed(file_locations=[FileLocation(N, None)], attempted_edits=edits)
    assert attempt._resolve_retry_member_hints(ctx, state, [N]) == {}          # no anchor failure yet
    state.budgets.anchor_failure_counts = {N: 1}
    assert attempt._resolve_retry_member_hints(ctx, state, [N]) == {N: ["Node.childCount"]}
    (event,) = [e for e in state.run_events if e.kind == "context.search_evidence_grounding"]
    assert (event.source, event.details["outcome"], event.details["candidate_member_ids"], event.details["edit_index"],
            event.details["search_text_hash"], event.details["search_text_length"]) == (
        "attempt._resolve_retry_member_hints", "grounded_by_containment", ["Node.childCount"], 0,
        content_revision(CHILD_COUNT_SEARCH), len(CHILD_COUNT_SEARCH))
    # a line locator of the same target wins: the SEARCH evidence never overrides it
    located = _failed(file_locations=[FileLocation(N, 5)], attempted_edits=edits)
    located.budgets.anchor_failure_counts = {N: 1}
    assert attempt._resolve_retry_member_hints(ctx, located, [N]) == {N: ["Node.nodeName"]}
    # no locator at all: the failure's likely files name where the SEARCH text is evaluated
    likely = _failed(file_locations=[], likely_files=[N], attempted_edits=edits)
    likely.budgets.anchor_failure_counts = {N: 1}
    assert attempt._resolve_retry_member_hints(ctx, likely, [N]) == {N: ["Node.childCount"]}
    # two edits grounding two different members: ambiguous, no hint
    ambiguous = _failed(file_locations=[FileLocation(N, None)],
                        attempted_edits=edits + [{"search": ABS_URL_SEARCH, "replace": "y"}])
    ambiguous.budgets.anchor_failure_counts = {N: 1}
    assert attempt._resolve_retry_member_hints(ctx, ambiguous, [N]) == {}


def test_only_a_whole_file_view_ends_the_search_evidence_escalation(_fixture):
    _workspace_path, ctx = _fixture
    edits = [{"search": CHILD_COUNT_SEARCH, "replace": "x"}]
    for tier, omitted, expected in (("full", False, {}), ("member_exact", False, {N: ["Node.childCount"]}),
                                    ("full", True, {N: ["Node.childCount"]})):
        state = _failed(file_locations=[FileLocation(N, None)], attempted_edits=edits)
        state.budgets.anchor_failure_counts = {N: 1}
        state.known_target_context_items[N] = make_context_item(
            path=N, content=NODE, reason="known", source_type="named_in_request", trust_level="repository", tier=tier,
            is_exact=not omitted, revision=content_revision(NODE), omitted_regions=omitted,
            member_id="Node.nodeName" if tier == "member_exact" else None)
        assert attempt._resolve_retry_member_hints(ctx, state, [N]) == expected, (tier, omitted)


def test_member_names_are_validated_against_the_current_boundaries_once_each(_fixture):
    _workspace_path, ctx = _fixture
    assert attempt._validated_member_hints(ctx, {E: ["text", "text", "attr", "nothing"], "src/missing.java": ["x"],
                                                 "notes.txt": ["a"]}) == {E: ["Element.text", "Element.attr"]}
    # the run's own retrieval grounding: only for the targets asked about (U's hint is no target here)
    assert attempt._resolve_known_target_member_hints(ctx, [E, N]) == {E: ["Element.text"], N: ["Node.nodeName"]}
    assert attempt._resolve_known_target_member_hints(ctx, [N]) == {N: ["Node.nodeName"]}


def test_the_goals_named_members_are_ordered_own_first_and_capped_per_file(_fixture):
    _workspace_path, ctx = _fixture
    assert attempt._goal_named_member_candidates(GOAL, E) == ["absUrl", "childCount", "attr", "text", "lookup", "id"]
    assert attempt._goal_named_member_candidates(GOAL, N) == ["childCount", "absUrl", "attr", "text", "lookup", "id"]
    assert attempt._goal_named_members_of(GOAL, "Node") == ["childCount"]
    assert attempt._goal_named_member_hints(ctx, [E, N]) == {
        E: ["Element.absUrl", "Element.attr", "Element.text", "Element.lookup"],   # five validated, capped at four
        N: ["Node.childCount", "Node.absUrl"]}
    hints = {E: ["Element.text"], N: []}
    assert attempt._merge_goal_named_member_hints(ctx, hints, [E]) is True
    assert hints == {E: ["Element.text", "Element.absUrl", "Element.attr", "Element.lookup"], N: []}
    assert attempt._merge_goal_named_member_hints(ctx, hints, [E]) is False   # nothing new the second time


def test_the_retry_temperature_is_the_configured_one_else_the_called_bindings_own(_fixture):
    _workspace_path, ctx = _fixture
    config = ctx.kernel.config
    assert attempt._effective_retry_temperature(ctx, "fallback-coder:35b") == 0.3       # llm.retry_temperature wins
    config.llm.retry_temperature = None
    assert attempt._effective_retry_temperature(ctx, "fallback-coder:35b") == 0.55      # the fallback's own
    assert attempt._effective_retry_temperature(ctx, "primary-coder:30b") == 0.2        # the primary's own
    config.llm.temperature = 0.35
    assert attempt._effective_retry_temperature(ctx, "unconfigured:1b") == 0.35         # an unknown model: the primary


def test_the_evidence_fingerprint_is_built_from_every_per_path_field(_fixture):
    state = GenerationState()
    state.budgets.anchor_failure_counts = {E: 3}
    state.edit_anchor_loci = {E: [17, 12]}
    state.budgets.last_failure_signature = "compile|sig"
    state.known_target_context_items[E] = make_context_item(
        path=E, content=ELEMENT, reason="r", source_type="named_in_request", trust_level="repository",
        tier="member_exact", is_exact=True, revision="rev-E", member_id="Element.absUrl", omitted_regions=False)
    assert attempt._compute_retry_evidence_fingerprint(
        state, [N, E, E], "targeted", "primary-coder:30b@d1", {E: ["Element.text", "Element.attr"]}) == (
        "targeted", "primary-coder:30b@d1",
        ((E, "rev-E", "member_exact", True, "Element.absUrl", False, ("Element.attr", "Element.text"), 3, (17, 12)),
         (N, None, None, None, None, None, (), 0, ())),
        "compile|sig")
    assert attempt._DETERMINISTIC_VERDICT_REASON_CODES == frozenset({"ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"})


def test_the_retry_package_is_built_from_the_retrys_own_universe_budget_and_evidence(tmp_path):
    workspace = _workspace(tmp_path)
    big = "src/org/example/Wide.java"
    (tmp_path / big).write_text(_class_source("Wide", 120), encoding="utf-8")
    ctx = _ctx(workspace, _config(), established_files=[BASE])
    finding = OpenFinding(failure_type="static_rule_violation", reason_code="RULE-7", files=(E,), source="sa",
                          evidence="Element.java:16 concat", first_attempt=1)
    state = _failed(likely_files=[E])
    state.all_files_written = {E, U, big}
    state.error_context = "ERROR" + REFERENCE_MARKER
    state.last_error_source_context = {E: "line 16"}
    state.validated_file_revisions = {U: "sha256:validated-after-compile"}
    state.open_findings = [finding]
    assert attempt._retry_package_for_attempt(GenerationState(), ctx, target_files=[E], prompt_window=4000) is None
    package = attempt._retry_package_for_attempt(state, ctx, target_files=[E, N], prompt_window=4000, exclude=[E, U])
    assert package.target_files == (E, N)                       # the exclusion never narrows the targets
    assert [p.path for p in package.target_projections] == [N]  # ... only the universe: N (requested, unwritten)
    assert sorted(p.path for p in package.reference_projections) == [BASE, big]   # established + written, U excluded
    assert package.source_context == {E: "line 16"} and package.open_findings == (finding,)
    assert package.advisory_context == REFERENCE_MARKER.lstrip("\n")
    with_known = attempt._retry_package_for_attempt(state, ctx, target_files=[U], prompt_window=4000)
    assert [p.revision for p in with_known.target_projections if p.path == U] == ["sha256:validated-after-compile"]
    narrow = attempt._retry_package_for_attempt(state, ctx, target_files=[big], prompt_window=4000)
    wide = attempt._retry_package_for_attempt(state, ctx, target_files=[big], prompt_window=40000)
    sizes = [len(p.content) for p in (*narrow.target_projections, *wide.target_projections) if p.path == big]
    assert sizes[0] < sizes[1], sizes                            # the evidence budget is the call's window share


def test_retry_projections_are_recorded_as_context_items_without_losing_member_precision(tmp_path):
    from kriya.workflow.context_projection import FileProjection, ProjectionLevel

    state = GenerationState()
    state.known_target_context_items[N] = make_context_item(
        path=N, content="body", reason="member", source_type="named_in_request", trust_level="repository",
        tier="member_exact", is_exact=True, revision="rev-N", member_id="Node.childCount", omitted_regions=False)
    package = RetryPackage(
        failure_type="compile", authoritative_error="e", advisory_context="", target_files=(E,),
        target_projections=(FileProjection(E, "rev-E", ProjectionLevel.FULL, "target", ELEMENT, False, len(ELEMENT)),),
        reference_projections=(FileProjection(U, "rev-U", ProjectionLevel.FULL, "dependency_reference", UTIL, True, 9),
                               FileProjection(N, "rev-N", ProjectionLevel.FULL, "dependency_reference", NODE, True, 9)),
        source_context={}, omitted_files=())
    attempt._record_retry_projection_context_items(state, package)
    forms = {path: (item.tier, item.is_exact, item.reason, item.revision, item.omitted_regions, item.member_id)
             for path, item in state.known_target_context_items.items()}
    assert forms == {E: ("full", True, "retry_package:target", "rev-E", False, None),
                     U: ("full", False, "retry_package:dependency_reference", "rev-U", True, None),
                     N: ("member_exact", True, "member", "rev-N", False, "Node.childCount")}   # kept: same revision
    changed = make_context_item(path=N, content="x", reason="r", source_type="named_in_request",
                                trust_level="repository", tier="full", is_exact=False, revision="rev-N2")
    assert attempt._preserve_member_exact_precision(state, N, changed) is changed   # a new revision always wins
    attempt._record_retry_projection_context_items(state, None)   # nothing to record
    assert set(state.known_target_context_items) == {E, U, N}


def test_a_retry_targets_source_record_names_where_its_content_came_from(_fixture):
    _workspace_path, ctx = _fixture
    ctx.established_files[:] = [E, BASE]
    state = GenerationState()
    state.all_files_written = {E}
    assert [attempt._classify_retry_target_source_origin(state, ctx, path) for path in (E, BASE, N)] == [
        "already_written", "established", "current_worktree"]
    assert attempt._target_source_record(state, ctx, N) == {
        "path": N, "source_origin": "current_worktree", "tier": None, "is_exact": None, "member_id": None,
        "omitted_regions": None, "known": False}


def _prepared(state, ctx, **kwargs):
    kwargs.setdefault("target_files", [E, N])
    kwargs.setdefault("prompt_window", 17740)
    kwargs.setdefault("model_identity", "primary-coder:30b")
    return attempt._prepare_retry_context(state, ctx, **kwargs)


def _retry_state(**failure):
    state = _failed(**failure)
    state.last_attempt_mode = "targeted"
    state.budgets.last_failure_signature = "compile|sig"
    return state


def test_the_progress_gate_runs_only_when_enabled_and_keys_on_the_called_models_request_profile(_fixture):
    _workspace_path, ctx = _fixture
    off = _retry_state(file_locations=[FileLocation(E, 16)])
    _prepared(off, ctx, enable_no_progress_gate=False)
    assert off.budgets.last_retry_evidence_fingerprint is None and off.retry_evidence_seen == {}
    assert not [e for e in off.run_events if e.kind == "retry.progress_decision"]
    on = _retry_state(file_locations=[FileLocation(E, 16)])
    _prepared(on, ctx)
    (decision,) = [e for e in on.run_events if e.kind == "retry.progress_decision"]
    primary_profile = attempt._developer_request_profile(ctx, None)
    fallback_profile = attempt._developer_request_profile(ctx, "fallback-coder:35b")
    assert primary_profile.digest != fallback_profile.digest
    assert decision.details["request_profile"] == primary_profile.digest
    assert on.budgets.last_retry_evidence_fingerprint[1] == f"primary-coder:30b@{primary_profile.digest}"
    hop = _retry_state(file_locations=[FileLocation(E, 16)])
    _prepared(hop, ctx, model_identity="fallback-coder:35b", prompt_window=8000)
    (decision,) = [e for e in hop.run_events if e.kind == "retry.progress_decision"]
    assert decision.details["request_profile"] == fallback_profile.digest
    assert hop.budgets.last_retry_evidence_fingerprint[1] == f"fallback-coder:35b@{fallback_profile.digest}"


def test_identical_evidence_from_any_earlier_attempt_is_a_repeat_and_keeps_its_first_attempt(_fixture):
    _workspace_path, ctx = _fixture
    state = _retry_state(file_locations=[FileLocation(E, 16)])
    first = _prepared(state, ctx)
    first_hash = next(iter(state.retry_evidence_seen))
    assert state.retry_evidence_seen == {first_hash: 2}
    state.attempt_number = 3
    state.budgets.last_failure_signature = "compile|other"   # B: new evidence
    _prepared(state, ctx)
    state.attempt_number = 4
    state.budgets.last_failure_signature = "compile|sig"     # back to A: a repeat of attempt 2, not of attempt 3
    _prepared(state, ctx)
    resample = [e for e in state.run_events if e.kind == "retry.sampling_resample"]
    assert [(e.attempt, e.details["first_seen_attempt"], e.details["effective_temperature"]) for e in resample] == [
        (4, 2, 0.3)]
    assert state.retry_evidence_seen[first_hash] == 2 and len(state.retry_evidence_seen) == 2
    assert state.sampling_resamples == 1
    assert first.member_hints == {E: ["Element.absUrl", "Element.text", "Element.attr", "Element.lookup"],
                                  N: ["Node.nodeName", "Node.childCount", "Node.absUrl"]}


def test_the_member_authority_expansion_stays_inside_the_write_scope_and_records_its_evidence(tmp_path):
    workspace = _workspace(tmp_path)
    ctx = _ctx(workspace, _config(), retrieval_member_hints={}, write_scope_mode=WriteScopeMode.ALLOWLIST,
               allowed_write_relpaths=[N])
    state = _retry_state(file_locations=[FileLocation(E, 16), FileLocation(N, 5)])
    prepared = _prepared(state, ctx)
    assert [(x.path, x.member_id, x.outcome, x.reason_code) for x in state.authority_expansions] == [
        (E, "Element.absUrl", "INDETERMINATE", "OUT_OF_MUTATION_SCOPE"),
        (N, "Node.nodeName", "GRANTED", "MEMBER_AUTHORITY_GRANTED")]
    assert prepared.member_hints == {N: ["Node.nodeName", "Node.childCount", "Node.absUrl"]}   # E: never granted
    assert [x.to_dict()["evidence"] for x in state.authority_expansions] == [
        {"source": "retry_member_hints", "attempt": 2, "failure_type": "compile"}] * 2
    assert [e.details["reason_code"] for e in state.run_events if e.kind == "authority.expansion"] == [
        "OUT_OF_MUTATION_SCOPE", "MEMBER_AUTHORITY_GRANTED"]


def test_a_deterministic_verdict_blocks_a_repeat_only_with_a_grounded_target(_fixture):
    """The no-progress terminal needs BOTH a deterministic-verdict reason code and at least one target: the same
    repeated evidence over no target is a sampling decision (resampled at the retry temperature)."""
    _workspace_path, ctx = _fixture
    diagnostics = {"reason_code": "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"}
    untargeted = _retry_state(diagnostics=diagnostics)
    _prepared(untargeted, ctx, target_files=[])
    untargeted.attempt_number = 3
    _prepared(untargeted, ctx, target_files=[])
    assert untargeted.sampling_resamples == 1
    targeted = _retry_state(diagnostics=diagnostics, file_locations=[FileLocation(E, 16)])
    _prepared(targeted, ctx)
    targeted.attempt_number = 3
    with pytest.raises(QualityGateFailure) as stopped:
        _prepared(targeted, ctx)
    assert stopped.value.failure.diagnostics == {"reason_code": "NO_PROGRESS_RETRY_EXHAUSTED"}
    assert targeted.sampling_resamples == 0


def test_a_member_package_replaces_the_recorded_omitted_members_wholesale(_fixture):
    _workspace_path, ctx = _fixture
    state = _retry_state(file_locations=[FileLocation(E, 16)])
    state.known_target_omitted_members = {BASE: [(1, 3, "stale-revision")]}
    _prepared(state, ctx)
    assert state.known_target_omitted_members == {}


def test_a_participants_candidate_is_materialized_from_the_workspace_when_the_worktree_lacks_it(tmp_path):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (workspace / "src/main/java").mkdir(parents=True)
    (workspace / APP).write_text(APP_SOURCE, encoding="utf-8")
    ctx = _ctx(str(workspace), _config(chain=False), worktree_path=str(worktree))
    edit = {"search": "        if (args.length == 0) { System.exit(1); }",
            "replace": "        if (args.length == 0) { return; }"}
    assert attempt._materialize_candidate_content(ctx, APP, {"filepath": APP, "content": None, "edits": [edit]}) == \
        APP_SOURCE.replace(edit["search"], edit["replace"])
    assert attempt._materialize_candidate_content(ctx, APP, {"filepath": APP, "content": "WHOLE", "edits": [edit]}) == \
        "WHOLE"
    assert attempt._materialize_candidate_content(ctx, APP, {"filepath": APP, "content": None, "edits": [
        {"search": "absent anchor", "replace": "x"}]}) is None


def test_a_coordinated_participant_outside_the_active_group_is_shown_only_within_the_call_windows_share(tmp_path):
    """A participant of another RepairGroup counts against the sibling share of the call's window: a large build
    file is shown in full to its own call and omitted (named) from a later group's call."""
    from kriya.workflow.operations import CodeOperation
    from kriya.workflow.repair_contract import RepairContract, RepairKind, _derive_repair_groups

    pom = "pom.xml"
    large_build = "<project>\n" + "".join(f"  <property name=\"p{i}\">value-{i}</property>\n" for i in range(600)) \
        + "</project>\n"
    sources = {pom: large_build, APP: APP_SOURCE}
    for rel, content in sources.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(content, encoding="utf-8")
    paths = tuple(sorted(sources))
    roles = {pom: "build_manifest", APP: "termination_surface"}
    groups = _derive_repair_groups(paths, roles)
    assert len(groups) == 2
    contract = RepairContract(
        id="repair.s8.x", source_obligation_ids=("attempt.subtask.s8.process_boundary_compatibility",),
        kind=RepairKind.COORDINATED, repair_intent="r", must_fix=("f",), must_preserve=("p",),
        participating_artifacts=paths, participant_roles=roles, repair_groups=groups,
        generation_order=tuple(p for g in groups for p in g.generation_order), created_attempt=1,
        immediate_correction_targets=(APP,), active_group_id=groups[0].id)
    state = GenerationState()
    state.attempt_number = 2
    state.all_files_written = set(paths)
    state.error_context = "TEST_PROCESS_TERMINATED"
    ctx = _ctx(str(tmp_path), _config(chain=False), architect_files=list(paths), expected_files_upfront=list(paths))
    contexts = {}

    async def run_generation(**kwargs):
        (target,) = kwargs["known_target_files"]
        contexts[target] = kwargs["existing_code_context"]
        return [{"filepath": target, "content": None, "edits": []}]
    ctx.developer.run_generation = AsyncMock(side_effect=run_generation)
    asyncio.run(attempt._run_coordinated_repair_generation(state, ctx, contract, "", None,
                                                           CodeOperation.REPAIR_WITH_PATCH))
    assert large_build in contexts[pom]
    assert large_build not in contexts[APP] and pom in contexts[APP]


def test_retry_prompts_renders_both_api_contract_recovery_phases():
    """E7 (post-move): the API_CONTRACT_RECOVERY request text moved verbatim into retry_prompts. The RESTORE phase's
    text is never sent (that phase restores deterministically, with no Developer call: the attempt-level golden
    pins that no request is made), so it is pinned here, on the renderer itself."""
    from kriya.workflow.retry_prompts import _build_api_contract_recovery_prompt

    recovery = _api_recovery(APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT)
    originals = {E: "E" * 12005}
    task, context, required, protected = _build_api_contract_recovery_prompt(
        "the goal", recovery, originals, "BASE", repair_behavior=False)
    assert required == f"{E}::public String absUrl(String key), {N}::public int childCount()"
    assert protected == "src/test/org/example/ElementTest.java"
    owners = (f"=== EXACT BASELINE OWNER SOURCE: {E} ===\n" + "E" * 12000
              + f"\n\n=== EXACT BASELINE OWNER SOURCE: {N} ===\n<baseline source unavailable>")
    assert context == "BASE\n\n" + owners
    assert task == (
        "=== API_CONTRACT_RECOVERY: RESTORE_PUBLIC_CONTRACT ===\n"
        "This is the only objective for this phase.\n"
        f"Restore these exact public signatures in their existing owners: {required}.\n"
        "Do not solve the behavioral issue yet. Do not rename or replace a method. "
        "Do not modify protected callers/tests. The candidate is invalid unless "
        "every exact signature exists after this edit.\n"
        f"Protected contract evidence: {protected}.\n\n{owners}")
    repair, _context, _required, _protected = _build_api_contract_recovery_prompt(
        "the goal", recovery, originals, "BASE", repair_behavior=True)
    assert repair.startswith("=== API_CONTRACT_RECOVERY: REPAIR_BEHAVIOR ===\n")
    assert repair.endswith(f"Original goal:\nthe goal\n\n{owners}")
