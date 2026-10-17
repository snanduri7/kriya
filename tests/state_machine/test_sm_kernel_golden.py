"""Kernel golden evidence (full modularization E1): the preservation proof for every W4 E-slice.

Each scenario in ``_kernel_golden.SCENARIOS`` drives one terminal path of the real kernel with only the model
scripted, and its observable outcome (ordered attempt-evidence record kinds with payload/content digests, every
RunRecord, the traces.db run events, the WorkflowResult / result dict) is compared byte for byte with
``tests/golden/kernel/<scenario>.json``. The goldens were WRITTEN FROM 4eaf610 (before any W4 production edit) and
generated three times under PYTHONHASHSEED 1, 2 and 3 with identical bytes. An E-slice (PRESERVE) must leave every
file unchanged; a diff means the extraction changed observable behaviour. Regenerate ONLY from a commit whose
behaviour is known good: ``KRIYA_WRITE_KERNEL_GOLDEN=<dir> .venv/bin/pytest tests/state_machine/test_sm_kernel_golden.py``
writes the forms to ``<dir>`` (and fails, so a write is never mistaken for a pass); review the diff, then copy.

HX1 (OI-41) made them portable: they pass at any absolute checkout path, under load and without the checkout's untracked
``./memory`` (the controls at the end of this file pin each normalization and the isolation).
"""
import json
import os
from pathlib import Path

import _kernel_golden as kg
import pytest

pytestmark = pytest.mark.state_machine


@pytest.fixture(autouse=True)
def _fixed_git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def _isolated_working_directory(tmp_path_factory, monkeypatch):
    """Every test here runs from a fresh working directory holding an empty ./memory (never the checkout's)."""
    monkeypatch.chdir(kg.isolated_working_directory(tmp_path_factory.mktemp("hx1-cwd")))


@pytest.mark.parametrize("name", sorted(kg.SCENARIOS))
def test_the_kernel_scenario_matches_its_pre_decomposition_golden(name, tmp_path):
    captured = kg.capture(kg.SCENARIOS[name](tmp_path), tmp_path, name)
    form = kg.render(captured)
    contents = os.environ.get(kg.CONTENT_ENV)
    if contents:
        Path(contents).mkdir(parents=True, exist_ok=True)
        (Path(contents) / f"{name}.content.json").write_text(kg.render({"contents": kg.form_contents[id(captured)]}),
                                                               encoding="utf-8")
    target = os.environ.get(kg.WRITE_ENV)
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / f"{name}.json").write_text(form, encoding="utf-8")
        pytest.fail(f"golden form written to {target}/{name}.json; rerun without {kg.WRITE_ENV}")
    golden = (kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8")
    assert form == golden, f"{name}: the kernel's observable outcome differs from tests/golden/kernel/{name}.json"


# What each golden must show for its terminal path (read from the committed file: a regenerated golden whose run
# silently took another path fails here, not only on the byte comparison). (result type, quality_gates_passed,
# failure_category, status, RunRecord lifecycles in order, whether an attempt was opened)
EXPECTED = {
    "direct_success": ("dict", True, None, None, ["SUCCESS"], True),
    "legacy_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_fallback_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_gates_exhausted": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_repeated_failure_exhausted": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_no_progress": ("WorkflowResult", False, "no_progress", None, ["FAILURE"], True),
    "legacy_budget_stop": ("WorkflowResult", False, "generation_budget_exhausted", None, ["FAILURE"], True),
    "legacy_final_review_refused": ("WorkflowResult", False, "final_review_refused", None, ["FAILURE"], True),
    "legacy_approval_refused": ("WorkflowResult", False, None, None, ["FAILURE"], True),
    "legacy_resume_after_failure": ("WorkflowResult", True, None, None, ["FAILURE", "SUCCESS"], True),
    "enforce_success_with_verification_unit": ("WorkflowResult", True, None, "success", ["SUCCESS"], True),
    "enforce_approval_refused": ("WorkflowResult", False, None, "failed", ["FAILURE"], True),
    "enforce_plan_invalid": ("WorkflowResult", False, None, "needs_review", ["FAILURE"], False),
    "enforce_verification_only_failed": ("WorkflowResult", False, None, "failed", ["FAILURE"], True),
    "milestone_chain_completed": ("dict", None, None, "success", ["SUCCESS"], False),
    "milestone_rerun_reuses_completed": ("dict", None, None, "success", ["SUCCESS", "SUCCESS"], False),
    "milestone_resume_after_recovered_crash": ("dict", None, None, "success", ["RECOVERED", "SUCCESS"], False),
    # E3 characterization of stages 0-3 (written from bd3c6d8, before the stages moved): the early exits return the
    # engine's dict as legacy_result (no failure_category in it; the traces.db row carries it, see the golden's extra)
    "legacy_knowledge_gap": ("WorkflowResult", None, None, "knowledge_gap", ["FAILURE"], False),
    "legacy_knowledge_gap_confirmed": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_stage_context_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_planner_output_incomplete": ("WorkflowResult", None, None, "planner_output_incomplete", ["FAILURE"], False),
    "legacy_planner_unauthorized_path": ("WorkflowResult", None, None, "planner_output_unauthorized_path", ["FAILURE"], False),
    "legacy_planner_schema_invalid": ("WorkflowResult", None, None, "planner_output_schema_invalid", ["FAILURE"], False),
    "legacy_architect_file_list_rejected": ("WorkflowResult", None, None, "architect_file_list_rejected", ["FAILURE"], False),
    "legacy_stage2a_dependency_rejected": ("dict", None, None, None, ["FAILURE"], False),
    # E4 characterization of stages 5-7 and the pre-apply boundary (written from 3e5751c, before that code moved): the
    # approval-gate abort returns its own dict (no failure_category in it, as human_rejected; the traces.db row carries it)
    "legacy_static_analysis_blocked": ("WorkflowResult", False, "static_analysis_blocked", None, ["FAILURE"], True),
    "legacy_static_analysis_unknown": ("WorkflowResult", False, "static_analysis_unknown", None, ["FAILURE"], True),
    "legacy_static_analysis_unavailable": ("WorkflowResult", False, "static_analysis_unavailable", None, ["FAILURE"], True),
    "legacy_static_analysis_passed": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_approval_required_no_callback": ("WorkflowResult", False, None, None, ["FAILURE"], True),
    "legacy_autonomous_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_approval_diff_size_trigger": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_commit_revision_conflict": ("WorkflowResult", False, "workspace_commit_failed", None, ["FAILURE"], True),
    "legacy_final_review_deadline_refused": ("WorkflowResult", False, "final_review_refused", None, ["FAILURE"], True),
    "legacy_rejected_candidate_review": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_lessons_staged": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_dependency_skill_accrued": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_workspace_refused": ("WorkflowResult", False, None, "needs_review", [], False),
    "direct_workspace_refused": ("dict", False, None, "needs_review", ["COMMIT_ELIGIBLE"], False),
}
# PRD-008 refusals: refused before any model call, so no attempt-evidence store exists (the prior run's record is the
# only RunRecord of the engine scenario; the controller scenario has none).
NO_EVIDENCE = frozenset({"legacy_workspace_refused", "direct_workspace_refused"})


def test_every_golden_shows_its_terminal_path_and_real_evidence():
    assert sorted(EXPECTED) == sorted(kg.SCENARIOS) == sorted(kg.TERMINAL_PATHS)
    for name, (kind, passed, category, status, lifecycles, attempted) in EXPECTED.items():
        golden = json.loads((kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
        values = golden["result"]["legacy_result"]["values"]
        assert golden["_scenario"]["terminal_path"] == kg.TERMINAL_PATHS[name], name
        assert golden["_scenario"]["generated_from"] == kg.generated_from(name), name
        assert (golden["result"]["type"], values.get("quality_gates_passed"), values.get("failure_category"),
                values.get("status")) == (kind, passed, category, status), name
        assert [record["lifecycle_state"] for record in golden["run_records"]] == lifecycles, name
        kinds = [record["kind"] for store in golden["evidence"] for record in store["records"]]
        assert ("attempt.opened" in kinds) is attempted, name
        if name in NO_EVIDENCE:
            assert golden["evidence"] == [], name
        elif not name.startswith("milestone_"):   # the FakeEngine milestone runs make no model call: no evidence store
            assert kinds[0] == "run.opened" and "run.closed" in kinds, name
            assert all(store["verification"] == "VERIFIED" and store["sealed"] for store in golden["evidence"]), name


# ----------------------------------------------------------------------------- HX1 controls (OI-41)
# Each normalization is proven both ways: the value it hides really varies in a real run and the golden still holds;
# anything next to it (another path, another kriya_build field, a deadline, a classification, an identity, the order)
# still breaks the golden. The isolation is proven to keep the logical config and to resolve ./memory away from the
# checkout. handover/modularization/HX1-CHARACTERIZATION.md has the measurements these controls reproduce.

INSTALL_SCENARIO = "legacy_knowledge_gap"   # a short run with one run.opened record
ELAPSED_SCENARIO = "enforce_approval_refused"   # model calls through LLMClient._request_once -> _finish
ELSEWHERE = "/opt/elsewhere/Kriya-checkout/kriya"


def _form(name: str, tmp_path: Path) -> str:
    return kg.render(kg.capture(kg.SCENARIOS[name](tmp_path), tmp_path, name))


def _golden(name: str) -> str:
    return (kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8")


def _raw_payloads(kind: str) -> list:
    """The payloads of every ``kind`` record the run wrote, as stored (production evidence, before any normalization)."""
    from kriya.core.attempt_evidence import reader
    from kriya.core.state_paths import ENV_STATE_DIR

    state_dir = os.environ[ENV_STATE_DIR]
    return [record.get("payload") or {} for run_id in reader.list_runs(state_dir)
            for record in reader.open_run(state_dir, run_id).records() if record["kind"] == kind]


def test_run_opened_evidence_keeps_the_real_install_path(tmp_path):
    import kriya
    from kriya.foundation import build_info

    form = _form(INSTALL_SCENARIO, tmp_path)
    installed = os.path.dirname(os.path.abspath(kriya.__file__))
    assert [p["kriya_build"]["install_path"] for p in _raw_payloads("run.opened")] == [installed]
    assert build_info.PACKAGE_DIR == installed and os.path.isabs(installed)
    assert form == _golden(INSTALL_SCENARIO)


def test_another_checkout_path_keeps_the_golden(tmp_path, monkeypatch):
    from kriya.foundation import build_info

    monkeypatch.setattr(build_info, "PACKAGE_DIR", ELSEWHERE)
    form = _form(INSTALL_SCENARIO, tmp_path)
    assert [p["kriya_build"]["install_path"] for p in _raw_payloads("run.opened")] == [ELSEWHERE]
    assert form == _golden(INSTALL_SCENARIO)


@pytest.mark.parametrize("field, value", [("install_path", ELSEWHERE), ("python", "0.0.0"), ("product", "other")])
def test_any_other_kriya_build_value_breaks_the_golden(tmp_path, monkeypatch, field, value):
    """install_path that is NOT the running package directory, or any other kriya_build field, stays pinned."""
    from kriya.foundation import build_info

    real = build_info.version_report
    monkeypatch.setattr(build_info, "version_report", lambda path=None: {**real(path), field: value})
    form = _form(INSTALL_SCENARIO, tmp_path)
    assert [p["kriya_build"][field] for p in _raw_payloads("run.opened")] == [value]
    assert form != _golden(INSTALL_SCENARIO)


def _opened(**build_changes):
    from kriya.foundation import build_info

    return {"base_revision": "r1", "base_tree_hash": "t1", "workspace_identity": "w1", "workspace_path": "/srv/ws",
            "kriya_build": {"build_provenance": "unavailable", "commit": "UNKNOWN", "dirty": None,
                            "install_path": build_info.PACKAGE_DIR, "product": "kriya", "python": "3.14.6",
                            "tree": "UNKNOWN", "version": "0.1.0", **build_changes}}


def _payload_digest(kind: str, payload) -> str:
    return kg.digest(kg.Normalizer({}).payload(kind, payload))


def test_the_install_path_canonicalization_is_typed_and_narrow():
    from kriya.foundation import build_info

    base = _opened()
    normalized = kg.Normalizer({}).payload("run.opened", base)
    assert normalized["kriya_build"] == {**base["kriya_build"], "install_path": "<install_path:1>"}
    assert {k: v for k, v in normalized.items() if k != "kriya_build"} == {k: v for k, v in base.items() if k != "kriya_build"}
    reference = _payload_digest("run.opened", base)
    assert reference != _payload_digest("run.opened", {**base, "workspace_path": "/srv/other-ws"})   # no path regex
    assert reference != _payload_digest("run.opened", _opened(install_path=ELSEWHERE))   # not the running package
    assert reference != _payload_digest("run.opened", _opened(python="0.0.0"))   # the rest of kriya_build stays pinned
    # the same key in another record kind or at another nesting stays raw
    assert kg.Normalizer({}).payload("unit.opened", base)["kriya_build"]["install_path"] == build_info.PACKAGE_DIR
    nested = kg.Normalizer({}).payload("run.opened", {**base, "extra": {"install_path": build_info.PACKAGE_DIR}})
    assert nested["extra"]["install_path"] == build_info.PACKAGE_DIR


def _late_finish(monkeypatch, seconds: float, *, timeout_delta_ms: int = 0) -> None:
    """Every LLMClient._finish sees a request that took ``seconds`` longer (wall clock) and, with timeout_delta_ms, a
    changed effective request timeout in its deadline record (before the evidence is recorded)."""
    from kriya.core.llm import LLMClient

    real = LLMClient._finish

    def finish(self, result, *, started, budget):
        if timeout_delta_ms:
            result.protocol["provider_contract"]["deadline"]["effective_request_timeout_ms"] += timeout_delta_ms
        return real(self, result, started=started - seconds, budget=budget)
    monkeypatch.setattr(LLMClient, "_finish", finish)


def test_request_latency_keeps_the_golden(tmp_path, monkeypatch):
    """The E3 flake mechanism, deterministic: every scripted request takes 1.234 s more wall clock."""
    _late_finish(monkeypatch, 1.234)
    form = _form(ELAPSED_SCENARIO, tmp_path)
    elapsed = [p["protocol"]["provider_contract"]["deadline"]["elapsed_request_ms"] for p in _raw_payloads("model.result")]
    assert elapsed and all(ms >= 1234 for ms in elapsed)
    assert form == _golden(ELAPSED_SCENARIO)


def test_a_changed_request_deadline_breaks_the_golden(tmp_path, monkeypatch):
    _late_finish(monkeypatch, 0.0, timeout_delta_ms=1)
    form = _form(ELAPSED_SCENARIO, tmp_path)
    assert [p["protocol"]["provider_contract"]["deadline"]["effective_request_timeout_ms"]
            for p in _raw_payloads("model.result")][:1] == [900_001]
    assert form != _golden(ELAPSED_SCENARIO)


def _model_result(**deadline_changes):
    deadline = {"configured_transport_timeout_ms": 900_000, "deadline_bound": False, "deadline_source": None,
                "effective_request_timeout_ms": 900_000, "elapsed_request_ms": 0, **deadline_changes}
    return {"model": "dev-model", "status": "OK", "reason_code": None,
            "protocol": {"provider_contract": {"adapter": "ollama", "deadline": deadline}}}


def test_the_elapsed_request_canonicalization_is_exact():
    normalized = kg.Normalizer({}).payload("model.result", _model_result(elapsed_request_ms=7))
    assert normalized["protocol"]["provider_contract"]["deadline"] == {
        **_model_result()["protocol"]["provider_contract"]["deadline"], "elapsed_request_ms": kg.ELAPSED_REQUEST_PLACEHOLDER}
    reference = _payload_digest("model.result", _model_result())
    assert all(_payload_digest("model.result", _model_result(elapsed_request_ms=ms)) == reference
               for ms in (1, 5, 1234, 900_000))
    # the mirrored copies (a run event's model_use, in the event blob and in traces.db) follow the same rule
    assert (_payload_digest("mirror.event", {"details": {"model_use": _model_result(elapsed_request_ms=9)}})
            == _payload_digest("mirror.event", {"details": {"model_use": _model_result()}}))
    deadline = _model_result()["protocol"]["provider_contract"]["deadline"]
    pinned = [
        _model_result(elapsed_request_ms=-1),   # not a valid elapsed value: stays raw
        _model_result(effective_request_timeout_ms=899_999), _model_result(configured_transport_timeout_ms=1),
        _model_result(deadline_bound=True), _model_result(deadline_source="run"),
        _model_result(remaining_budget_at_dispatch_ms=5), _model_result(timeout_reason="INFERENCE_DEADLINE_EXCEEDED"),
        {**_model_result(), "model": "other-model"}, {**_model_result(), "status": "TIMEOUT"},
        {**_model_result(), "reason_code": "GENERATION_DEADLINE_EXHAUSTED"},
        {**_model_result(), "protocol": {"provider_contract": {"adapter": "openai", "deadline": deadline}}},
    ]
    assert all(_payload_digest("model.result", payload) != reference for payload in pinned)
    # the same key at any other nesting stays pinned
    for shape in (lambda ms: {"elapsed_request_ms": ms}, lambda ms: {"deadline": {"elapsed_request_ms": ms}},
                  lambda ms: {"provider_contract": {"elapsed_request_ms": ms}}):
        assert _payload_digest("model.result", shape(1)) != _payload_digest("model.result", shape(2))
    # and the order of records stays pinned
    first, second = _model_result(), {**_model_result(), "model": "other-model"}
    assert kg.digest(kg.Normalizer({}).value([first, second])) != kg.digest(kg.Normalizer({}).value([second, first]))


def test_scenarios_resolve_memory_in_an_isolated_working_directory(request, tmp_path):
    cwd = Path.cwd().resolve()
    checkout = Path(request.config.rootpath).resolve()
    assert checkout not in cwd.parents and cwd != checkout and os.listdir("memory") == []
    assert kg.MEMORY_RESOLVING <= set(kg.SCENARIOS)
    assert _form("legacy_budget_stop", tmp_path) == _golden("legacy_budget_stop")
    assert os.listdir("memory") != []   # the run opened (created) its sqlite stores in THIS ./memory, not the checkout's


def test_the_isolated_working_directory_keeps_the_config_fingerprint(tmp_path, monkeypatch):
    from _scripted_run import engine

    from kriya.control.checkpoint import compute_config_fingerprint

    def fingerprint(memory=None):
        cfg = engine()[2]
        if memory is not None:
            cfg.paths.memory = memory
        return cfg.paths.memory, compute_config_fingerprint(cfg.model_dump())
    isolated = fingerprint()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    golden = json.loads(_golden("legacy_success"))["run_records"][0]["effective_config_fingerprint"]
    assert isolated == fingerprint() == ("./memory", golden)
    # the rule it protects: an absolute tmp memory path would be a different config (a different fingerprint)
    assert fingerprint(str(tmp_path / "memory"))[1] != golden


def test_the_request_elapsed_contract_is_pinned_outside_the_goldens():
    """The golden hides the elapsed_request_ms VALUE; the field itself is pinned by a direct contract test."""
    source = (Path(__file__).resolve().parents[1] / "test_provider_contract_001a.py").read_text(encoding="utf-8")
    assert "def test_elapsed_request_ms_is_the_request_interval(" in source
