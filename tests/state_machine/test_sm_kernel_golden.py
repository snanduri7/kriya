"""Kernel golden evidence (full modularization E1): the preservation proof for every W4 E-slice.

Each scenario in ``_kernel_golden.SCENARIOS`` drives one terminal path of the real kernel with only the model
scripted, and its observable outcome (ordered attempt-evidence record kinds with payload/content digests, every
RunRecord, the traces.db run events, the WorkflowResult / result dict) is compared byte for byte with
``tests/golden/kernel/<scenario>.json``. The goldens were WRITTEN FROM 4eaf610 (before any W4 production edit) and
generated three times under PYTHONHASHSEED 1, 2 and 3 with identical bytes. An E-slice (PRESERVE) must leave every
file unchanged; a diff means the extraction changed observable behaviour. Regenerate ONLY from a commit whose
behaviour is known good: ``KRIYA_WRITE_KERNEL_GOLDEN=<dir> .venv/bin/pytest tests/state_machine/test_sm_kernel_golden.py``
writes the forms to ``<dir>`` (and fails, so a write is never mistaken for a pass); review the diff, then copy.

S7 (final containment contract) regenerated all 41 once, from the S7 tree, after the content-level diff against the pre-S7 tree showed ONLY three families: (1) the
egress.authority records describe contained execution (backend oci, required true, network capability denied, the acquisition registry destinations); (2) a verification
outcome record carries ``egress`` and no host ``resources`` (rlimit evidence exists only on the host path, which the secure defaults no longer reach); (3) the
``effective_config_fingerprint`` of the three flipped ``autonomy`` defaults (OLD config + those three fields == NEW default fingerprint, MEASURED). The ``egress.backend`` value
is the pytest stand-in's class name: these goldens prove the kernel's observable shape under the containment seam, not isolation (real OCI tests own that). No behavioural key,
terminal path, attempt count or lifecycle changed.

HX1 (OI-41) made them portable: they pass at any absolute checkout path, under load and without the checkout's untracked
``./memory`` (the controls at the end of this file pin each normalization and the isolation).
"""
import contextlib
import json
import os
import re
from pathlib import Path

import _kernel_golden as kg
import pytest

pytestmark = pytest.mark.state_machine


@pytest.fixture(autouse=True)
def _fixed_git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)



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
# W3-6: the ten stage-stop / enforce rows below now carry the category their trace row records (was None; KERNEL-RESULT-NO-CATEGORY-MORE-PATHS-001).
EXPECTED = {
    "direct_success": ("dict", True, None, None, ["SUCCESS"], True),
    "legacy_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_fallback_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_gates_exhausted": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_repeated_failure_exhausted": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_no_progress": ("WorkflowResult", False, "no_progress", None, ["FAILURE"], True),
    "legacy_budget_stop": ("WorkflowResult", False, "generation_budget_exhausted", None, ["FAILURE"], True),
    "legacy_final_review_refused": ("WorkflowResult", False, "final_review_refused", None, ["FAILURE"], True),
    "legacy_approval_refused": ("WorkflowResult", False, "human_rejected", None, ["FAILURE"], True),
    "legacy_resume_after_failure": ("WorkflowResult", True, None, None, ["FAILURE", "SUCCESS"], True),
    "enforce_success_with_verification_unit": ("WorkflowResult", True, None, "success", ["SUCCESS"], True),
    "enforce_approval_refused": ("WorkflowResult", False, "failed", "failed", ["FAILURE"], True),
    "enforce_plan_invalid": ("WorkflowResult", False, "PLANNING_ERROR", "needs_review", ["FAILURE"], False),
    "enforce_verification_only_failed": ("WorkflowResult", False, "failed", "failed", ["FAILURE"], True),
    "milestone_chain_completed": ("dict", None, None, "success", ["SUCCESS"], False),
    "milestone_rerun_reuses_completed": ("dict", None, None, "success", ["SUCCESS", "SUCCESS"], False),
    "milestone_resume_after_recovered_crash": ("dict", None, None, "success", ["RECOVERED", "SUCCESS"], False),
    # E3 characterization of stages 0-3 (written from bd3c6d8, before the stages moved): the early exits return the
    # engine's dict as legacy_result (no failure_category in it; the traces.db row carries it, see the golden's extra)
    "legacy_knowledge_gap": ("WorkflowResult", None, "knowledge_gap", "knowledge_gap", ["FAILURE"], False),
    "legacy_knowledge_gap_confirmed": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_stage_context_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_planner_output_incomplete": ("WorkflowResult", None, "planner_output_incomplete", "planner_output_incomplete", ["FAILURE"], False),
    "legacy_planner_unauthorized_path": ("WorkflowResult", None, "planner_output_unauthorized_path", "planner_output_unauthorized_path", ["FAILURE"], False),
    "legacy_planner_schema_invalid": ("WorkflowResult", None, "planner_output_schema_invalid", "planner_output_schema_invalid", ["FAILURE"], False),
    "legacy_architect_file_list_rejected": ("WorkflowResult", None, "architect_file_list_rejected", "architect_file_list_rejected", ["FAILURE"], False),
    "legacy_stage2a_dependency_rejected": ("WorkflowResult", False, "human_rejected", "human_rejected", ["FAILURE"], False),   # 1-E: a result (was: ValueError), the apply gate's status
    # E4 characterization of stages 5-7 and the pre-apply boundary (written from 3e5751c, before that code moved): the
    # approval-gate abort returns its own dict (no failure_category in it, as human_rejected; the traces.db row carries it)
    "legacy_static_analysis_blocked": ("WorkflowResult", False, "static_analysis_blocked", None, ["FAILURE"], True),
    "legacy_static_analysis_unknown": ("WorkflowResult", False, "static_analysis_unknown", None, ["FAILURE"], True),
    "legacy_static_analysis_unavailable": ("WorkflowResult", False, "static_analysis_unavailable", None, ["FAILURE"], True),
    "legacy_static_analysis_passed": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_approval_required_no_callback": ("WorkflowResult", False, "approval_required", None, ["FAILURE"], True),
    "legacy_autonomous_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_approval_diff_size_trigger": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_commit_revision_conflict": ("WorkflowResult", False, "workspace_commit_failed", None, ["FAILURE"], True),
    "legacy_final_review_deadline_refused": ("WorkflowResult", False, "final_review_refused", None, ["FAILURE"], True),
    "legacy_rejected_candidate_review": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_lessons_staged": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_dependency_skill_accrued": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_workspace_refused": ("WorkflowResult", False, None, "needs_review", [], False),
    "direct_workspace_refused": ("dict", False, None, "needs_review", ["COMMIT_ELIGIBLE"], False),
    # E5 characterization of the stage-4 prelude (written from aad9ab1, before that code moved; OI-39): both stops
    # return the engine's dict as legacy_result after the design stages, before any attempt
    "legacy_baseline_indeterminate": ("WorkflowResult", False, "baseline_indeterminate", "baseline_indeterminate", ["FAILURE"], False),
    "legacy_worktree_sync_refused": ("WorkflowResult", False, "worktree_sync_failed", "failure", ["FAILURE"], False),
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
# checkout. handover/MODULARIZATION/HX1-CHARACTERIZATION.md has the measurements these controls reproduce.

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


# OI-64: the checkout's own directory name must not decide this test. The normalizer's generic run-id pass rewrites any run-id shaped
# path component (a mutant-campaign sandbox named campaign-<YYYYMMDD>T<HHMMSS>-<hex8> did exactly that), so "stays raw" elsewhere
# means "is not given the typed token and gets only the generic text pass" - identical to the literal path for an ordinary path.
INSTALL_PATHS = {"neutral": "/srv/kriya-checkout/kriya", "run-id-shaped": "/tmp/campaign-20261018T164711-432cf0f6/kriya", "real": None}


@pytest.mark.parametrize("where", sorted(INSTALL_PATHS))
def test_the_install_path_canonicalization_is_typed_and_narrow(monkeypatch, where):
    from kriya.foundation import build_info

    if INSTALL_PATHS[where] is not None:
        monkeypatch.setattr(build_info, "PACKAGE_DIR", INSTALL_PATHS[where])
    installed = build_info.PACKAGE_DIR
    raw_elsewhere = kg.Normalizer({}).text(installed)         # what any non-run.opened position becomes: the generic pass only
    assert "<install_path" not in raw_elsewhere
    if where == "neutral":
        assert raw_elsewhere == installed                      # an ordinary path is untouched, as the pre-OI-64 assertion required
    if where == "run-id-shaped":
        assert raw_elsewhere == "/tmp/campaign-<run:1>/kriya"   # the collision this parameter exists to keep covered
    base = _opened()
    normalized = kg.Normalizer({}).payload("run.opened", base)
    assert normalized["kriya_build"] == {**base["kriya_build"], "install_path": "<install_path:1>"}
    assert {k: v for k, v in normalized.items() if k != "kriya_build"} == {k: v for k, v in base.items() if k != "kriya_build"}
    reference = _payload_digest("run.opened", base)
    assert reference != _payload_digest("run.opened", {**base, "workspace_path": "/srv/other-ws"})   # no path regex
    assert reference != _payload_digest("run.opened", _opened(install_path=ELSEWHERE))   # not the running package
    assert reference != _payload_digest("run.opened", _opened(python="0.0.0"))   # the rest of kriya_build stays pinned
    # the same key in another record kind or at another nesting stays raw
    assert kg.Normalizer({}).payload("unit.opened", base)["kriya_build"]["install_path"] == raw_elsewhere
    nested = kg.Normalizer({}).payload("run.opened", {**base, "extra": {"install_path": installed}})
    assert nested["extra"]["install_path"] == raw_elsewhere


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
    """The harness (not this file) runs every MEMORY_RESOLVING run from a fresh working directory with an empty
    ./memory and restores the caller's afterwards: the relative ./memory resolves there, never in the checkout."""
    before = os.getcwd()
    outcome = kg.SCENARIOS["legacy_budget_stop"](tmp_path)
    assert os.getcwd() == before
    cwd = Path(outcome.working_directory).resolve()
    checkout = Path(request.config.rootpath).resolve()
    assert checkout not in cwd.parents and cwd != checkout and cwd != tmp_path.resolve()
    # C-05 (S3): a run of a project that has no index creates NO store under paths.memory any more. TRACED: what it used to create here was the
    # release cache (KnowledgeCache creates knowledge_cache.db eagerly; it is now the declared global store under <state>/global) and an empty
    # dependency graph (a reader of a project with no index now gets an in-memory graph, never a new file). The isolation this test is about
    # still holds and is stricter: nothing is created in the working directory's ./memory, and nothing in the checkout's.
    assert os.listdir(cwd / "memory") == []
    from kriya.config.resolution import global_store_directory

    assert os.path.isfile(os.path.join(global_store_directory(None), "knowledge_cache.db"))  # where the run's one shared store went
    assert kg.MEMORY_RESOLVING <= set(kg.SCENARIOS)
    assert kg.render(kg.capture(outcome, tmp_path, "legacy_budget_stop")) == _golden("legacy_budget_stop")


def test_the_isolated_working_directory_keeps_the_config_fingerprint(tmp_path, monkeypatch):
    from _scripted_run import engine

    from kriya.control.checkpoint import compute_config_fingerprint

    def fingerprint(memory=None):
        cfg = engine()[2]
        if memory is not None:
            cfg.paths.memory = memory
        return cfg.paths.memory, compute_config_fingerprint(cfg.model_dump())
    with kg.isolated_working_directory(tmp_path):
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


# --- OI-53 (E14 preflight): the lessons scenario's calendar is the harness's, never the machine's ---------------------

LESSONS_SCENARIO = "legacy_lessons_staged"


def _system_calendar(monkeypatch, day: tuple) -> None:
    """Simulate the machine's calendar on ``day``: kriya.knowledge.schema's ``date.today()`` (KnowledgeFact.added_at)."""
    import datetime

    from kriya.knowledge import schema

    class _SystemDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(*day)
    monkeypatch.setattr(schema, "date", _SystemDate)


def _staged_added_at(outcome) -> list:
    """The added_at of every fact the run staged (read back from the staged files; never digested away)."""
    found = []
    for path in sorted(p for p in (Path(outcome.workspace).parent / "skills").rglob("*") if p.is_file()):
        found.extend(re.findall(r"added_at\W+(\d{4}-\d{2}-\d{2})", path.read_text(encoding="utf-8", errors="replace")))
    return found


@pytest.mark.parametrize("day", [(2026, 1, 2), (2027, 12, 31)])
def test_the_lessons_scenario_keeps_its_golden_on_any_calendar_day(tmp_path, monkeypatch, day):
    """OI-53: on two machine calendars other than the golden's day the scenario reproduces its golden byte for byte; the
    staged fact carries the harness's day (kg.GOLDEN_LESSON_DAY), never the machine's."""
    _system_calendar(monkeypatch, day)
    outcome = kg.SCENARIOS[LESSONS_SCENARIO](tmp_path)
    assert set(_staged_added_at(outcome)) == {"%04d-%02d-%02d" % kg.GOLDEN_LESSON_DAY}
    assert kg.render(kg.capture(outcome, tmp_path, LESSONS_SCENARIO)) == _golden(LESSONS_SCENARIO)


@pytest.mark.parametrize("day, keeps_golden", [((2026, 1, 2), False), (kg.GOLDEN_LESSON_DAY, True)])
def test_without_the_harness_clock_the_lessons_golden_follows_the_calendar(tmp_path, monkeypatch, day, keeps_golden):
    """OI-53 negative control: with the harness clock removed, the same scenario breaks its golden on another day and
    keeps it only on the golden's own day - the calendar date is the scenario's only volatile input."""
    monkeypatch.setattr(kg, "golden_test_clock", contextlib.nullcontext)
    _system_calendar(monkeypatch, day)
    outcome = kg.SCENARIOS[LESSONS_SCENARIO](tmp_path)
    assert set(_staged_added_at(outcome)) == {"%04d-%02d-%02d" % day}
    assert (kg.render(kg.capture(outcome, tmp_path, LESSONS_SCENARIO)) == _golden(LESSONS_SCENARIO)) is keeps_golden


# --- OI-43 (E14 preflight): the build environment the goldens recorded is the harness's -------------------------------

def _another_build_environment(monkeypatch, build_dir) -> None:
    """A machine with another interpreter patch version, another installed version and a local setup.py build's valid
    kriya/_build_info.json."""
    import types

    from kriya.foundation import build_info

    embedded = build_dir / "_build_info.json"
    embedded.write_text(json.dumps({"schema": 1, "git_commit": "a" * 40, "git_tree": "b" * 40, "dirty": False}))
    monkeypatch.setattr(build_info, "platform", types.SimpleNamespace(python_version=lambda: "3.14.7"))
    monkeypatch.setattr(build_info, "package_version", lambda: "9.9.9")
    monkeypatch.setattr(build_info, "BUILD_INFO_FILE", str(embedded))


def _opened_build() -> dict:
    (payload,) = _raw_payloads("run.opened")
    return {key: payload["kriya_build"][key] for key in ("python", "version", "build_provenance", "commit", "tree", "dirty")}


def test_the_goldens_hold_in_another_build_environment(tmp_path, scratch_dir, monkeypatch):
    """OI-43: on another interpreter version, installed version and embedded build the scenario still records the golden
    environment (kg.GOLDEN_PYTHON / GOLDEN_KRIYA_VERSION, no embedded build) and reproduces its golden byte for byte."""
    _another_build_environment(monkeypatch, scratch_dir("build"))
    form = _form(INSTALL_SCENARIO, tmp_path)
    assert _opened_build() == {"python": kg.GOLDEN_PYTHON, "version": kg.GOLDEN_KRIYA_VERSION,
                               "build_provenance": "unavailable", "commit": "UNKNOWN", "tree": "UNKNOWN", "dirty": None}
    assert form == _golden(INSTALL_SCENARIO)


def test_without_the_harness_environment_the_golden_follows_the_machine(tmp_path, scratch_dir, monkeypatch):
    """OI-43 negative control: with the harness's environment removed, the same machine's identity reaches run.opened
    and breaks the golden."""
    monkeypatch.setattr(kg, "golden_build_environment", contextlib.nullcontext)
    _another_build_environment(monkeypatch, scratch_dir("build"))
    form = _form(INSTALL_SCENARIO, tmp_path)
    assert _opened_build() == {"python": "3.14.7", "version": "9.9.9", "build_provenance": "embedded",
                               "commit": "a" * 40, "tree": "b" * 40, "dirty": False}
    assert form != _golden(INSTALL_SCENARIO)


# --- TEST-KERNEL-GOLDEN-ENFORCE-FIVE-FAIL-AT-F384061-001: the R1 re-record, pinned as a delta ---------------------------
# The four enforce goldens were re-recorded on 2026-11-11 after a bisect over ce1efb7..0401d22 (owner ruling): every
# difference was introduced by 6d9bfe0 (ENFORCE-TERMINAL-REQUIREMENT-TRUTH-001, audit root R1) and is an ADDITION; R2
# (9c7cf00) and R4 (0401d22) changed no golden byte. The files the goldens replaced are kept in PRE_R1_DIR as the
# characterization of the kernel before R1: the test below proves each current golden is that file plus exactly R1's
# documented additions - never a removal, a changed value or a reordering.
PRE_R1_DIR = kg.GOLDEN_DIR.parent / "kernel_pre_r1_requirement_truth"
# scenario -> (requirement.truth events, evidence.attempt_store events) the .enforce trace row gained. A run that never
# had a requirement set (planning refused the plan) records the store pointer only: no truth is invented for it.
R1_ENFORCE_ROW_ADDITIONS = {
    "enforce_approval_refused": (1, 1),
    "enforce_plan_invalid": (0, 1),
    "enforce_success_with_verification_unit": (1, 1),
    "enforce_verification_only_failed": (1, 1),
}


# K-A / K-B (VERIFICATION-OWNER-REOPEN-ADVISORY-REVIEW-NOT-TRANSPORTED-001, VERIFICATION-OWNER-REOPEN-SINGLE-OWNER-001,
# 2026-11-11): the ONE scenario whose golden those two changes re-recorded - the verification-only failure whose owner
# is reopened and regenerates unchanged. The file it replaced is kept in PRE_KAKB_DIR; the delta is pinned below.
PRE_KAKB_DIR = kg.GOLDEN_DIR.parent / "kernel_pre_kakb_owner_search"
KAKB_RERECORDED = ("enforce_verification_only_failed",)


# S1 / C-09 (owner ruling 7, 2026-11-16): every run now records its model admission (one ``model.admission`` run event,
# mirrored into the evidence store). All 41 goldens were regenerated ONCE from the S1 tree after a leaf-by-leaf diff
# showed, for 38 scenarios, nothing but that added record and event (5 scenarios record no such run and are byte
# identical), and for the three enforce scenarios additionally the enforce row's ``evidence.attempt_store`` pointer
# (``seq_at_row``: the store's sequence number, one higher per added record). The four files the delta tests below
# chain through are kept in PRE_S1_DIR; the S1 delta itself is pinned by the test that follows them.
PRE_S1_DIR = kg.GOLDEN_DIR.parent / "kernel_pre_s1_model_admission"
S1_KEPT = ("enforce_approval_refused", "enforce_success_with_verification_unit", "enforce_verification_only_failed",
           "legacy_budget_stop")


def _before_s1(name: str) -> Path:
    """The golden as it stood before S1: the kept file, or the current one for a scenario whose reference was not kept."""
    kept = PRE_S1_DIR / f"{name}.json"
    return kept if kept.is_file() else kg.GOLDEN_DIR / f"{name}.json"


def _extras(before: list, after: list) -> list:
    """The items of ``after`` that are not ``before``'s, when ``before`` is a subsequence of ``after`` (order kept);
    fails otherwise, so a removed, changed or reordered item can never pass as an addition."""
    extras, position = [], 0
    for item in after:
        if position < len(before) and item == before[position]:
            position += 1
        else:
            extras.append(item)
    assert position == len(before), "an item of the pre-R1 golden is missing, changed or out of order"
    return extras


@pytest.mark.parametrize("name", sorted(R1_ENFORCE_ROW_ADDITIONS))
def test_an_enforce_golden_is_its_pre_r1_golden_plus_exactly_the_r1_additions(name):
    before = json.loads((PRE_R1_DIR / f"{name}.json").read_text(encoding="utf-8"))
    # the golden as R1 left it: the current file, or - for a scenario a LATER accepted change re-recorded again - the
    # file that change replaced (the chain of references: pre-R1 -> R1 -> K-A/K-B -> current)
    after_r1 = PRE_KAKB_DIR / f"{name}.json" if name in KAKB_RERECORDED else _before_s1(name)   # ... -> S1 -> current
    after = json.loads(after_r1.read_text(encoding="utf-8"))
    truths, pointers = R1_ENFORCE_ROW_ADDITIONS[name]
    assert before != after and set(before) == set(after)
    for key in set(before) - {"evidence", "run_events", "result"}:
        assert before[key] == after[key], key                       # the scenario identity and every RunRecord: unchanged

    # the evidence store: only mirrored events of the two R1 kinds were appended, nothing else moved
    assert len(before["evidence"]) == len(after["evidence"])
    added_records = []
    for store_before, store_after in zip(before["evidence"], after["evidence"], strict=True):
        assert {k: v for k, v in store_before.items() if k != "records"} == {k: v for k, v in store_after.items() if k != "records"}
        added_records += _extras(store_before["records"], store_after["records"])
    assert sorted((record["kind"], record["event"]) for record in added_records) == sorted(
        [("mirror.event", "requirement.truth")] * truths + [("mirror.event", "evidence.attempt_store")] * pointers)

    # the traces.db rows: the same two kinds, on the enforce controller's own row only
    assert len(before["run_events"]) == len(after["run_events"])
    added_events = []
    for row_before, row_after in zip(before["run_events"], after["run_events"], strict=True):
        assert {k: v for k, v in row_before.items() if k != "events"} == {k: v for k, v in row_after.items() if k != "events"}
        added_events += _extras(row_before["events"], row_after["events"])
    assert sorted(event["kind"] for event in added_events) == sorted(["requirement.truth"] * truths + ["evidence.attempt_store"] * pointers)
    assert {event["source"] for event in added_events} == {"workflow_controller.enforce"}
    assert [event["authority"] for event in added_events if event["kind"] == "requirement.truth"] == ["authoritative"] * truths

    # the result: one new field, the truth beside the unchanged lifecycle status
    values_before, values_after = before["result"]["legacy_result"]["values"], after["result"]["legacy_result"]["values"]
    assert {k: v for k, v in before["result"].items() if k != "legacy_result"} == {k: v for k, v in after["result"].items() if k != "legacy_result"}
    assert _extras(before["result"]["legacy_result"]["keys"], after["result"]["legacy_result"]["keys"]) == ["requirement_truth"] * truths
    assert {k: v for k, v in values_after.items() if k != "requirement_truth"} == values_before
    if truths:
        truth = values_after["requirement_truth"]
        assert (truth["status"], truth["verified"], truth["accepted"], truth["total"]) == ("OPEN_REQUIREMENTS", 0, 0, 1)
    else:
        assert "requirement_truth" not in values_after


def test_the_reopen_golden_is_its_pre_kakb_golden_plus_exactly_the_review_transport_and_episode_delta():
    """MEASURED content diff (kakb_golden_content_diff.txt): the reopened owner's Developer request now carries its own
    fenced review, so that ONE request's size changes (prompt tokens 2399 -> 2634) in the four records that report it;
    the rejected-reopen event gains its K-A/K-B fields; the result gains the episode. No record, event or key is added,
    removed or reordered anywhere else, and no other scenario's golden changed (41 scenarios regenerated, 1 differed)."""
    (name,) = KAKB_RERECORDED
    before = json.loads((PRE_KAKB_DIR / f"{name}.json").read_text(encoding="utf-8"))
    after = json.loads(_before_s1(name).read_text(encoding="utf-8"))   # as K-A/K-B left it (S1 re-recorded it since)
    assert sorted(path.stem for path in PRE_KAKB_DIR.glob("*.json")) == sorted(KAKB_RERECORDED)
    for key in set(before) - {"evidence", "run_events", "result"}:
        assert before[key] == after[key], key

    def label(record):
        return record.get("event") or record["kind"]

    changed_records = []
    for store_before, store_after in zip(before["evidence"], after["evidence"], strict=True):
        assert [label(r) for r in store_before["records"]] == [label(r) for r in store_after["records"]]   # same records, same order
        changed_records += [label(a) for b, a in zip(store_before["records"], store_after["records"], strict=True) if b != a]
    assert changed_records == ["model.result", "developer.prompt_composition", "generation.completed", "model.role_metrics",
                               "verification.owner_recovery_rejected"]

    changed_events = []
    for row_before, row_after in zip(before["run_events"], after["run_events"], strict=True):
        assert [e["kind"] for e in row_before["events"]] == [e["kind"] for e in row_after["events"]]
        changed_events += [a["kind"] for b, a in zip(row_before["events"], row_after["events"], strict=True) if b != a]
    assert changed_events == ["developer.prompt_composition", "generation.completed", "model.role_metrics"]

    values_before, values_after = before["result"]["legacy_result"]["values"], after["result"]["legacy_result"]["values"]
    assert _extras(before["result"]["legacy_result"]["keys"], after["result"]["legacy_result"]["keys"]) == ["plan_recovery_events"]
    assert {k: v for k, v in values_after.items() if k != "plan_recovery_events"} == values_before
    assert values_after["plan_recovery_events"] == [{
        "failed_subtask": "s2", "classification": "VERIFICATION_OWNER_RECOVERY",
        "reason_codes": ["VERIFICATION_OWNER_EPISODE_ALL_OWNERS_UNCHANGED"], "owners_invoked": ["s1"],
        "unchanged_owners": ["s1"], "accepted_owner": None, "owner_recovery_passed": False}]
    assert (values_after["status"], values_after["quality_gates_passed"]) == ("failed", False)       # still the same truthful failure


@pytest.mark.parametrize("name", S1_KEPT)
def test_a_golden_is_its_pre_s1_golden_plus_exactly_the_model_admission_event(name):
    """S1's delta, pinned: one ``model.admission`` event per run that opens a generation state, mirrored once into the
    evidence store; nothing removed, reordered or changed except the enforce row's store pointer, whose sequence number
    counts the added records."""
    assert sorted(path.stem for path in PRE_S1_DIR.glob("*.json")) == sorted(S1_KEPT)
    before = json.loads((PRE_S1_DIR / f"{name}.json").read_text(encoding="utf-8"))
    after = json.loads((kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert before != after and set(before) == set(after)
    for key in set(before) - {"evidence", "run_events", "extra"}:
        assert before[key] == after[key], key                       # identity, every RunRecord and the result: unchanged

    def without_pointer(items, key):
        """The store pointer's digest is allowed to differ: compared by position and kind, not by value."""
        return [{k: v for k, v in item.items() if not (item.get(key) == "evidence.attempt_store" and k in ("content", "payload", "details"))}
                for item in items]

    added_records, added_events = [], []
    for store_before, store_after in zip(before["evidence"], after["evidence"], strict=True):
        assert {k: v for k, v in store_before.items() if k != "records"} == {k: v for k, v in store_after.items() if k != "records"}
        added_records += _extras(without_pointer(store_before["records"], "event"), without_pointer(store_after["records"], "event"))
    for row_before, row_after in zip(before["run_events"], after["run_events"], strict=True):
        assert {k: v for k, v in row_before.items() if k != "events"} == {k: v for k, v in row_after.items() if k != "events"}
        added_events += _extras(without_pointer(row_before["events"], "kind"), without_pointer(row_after["events"], "kind"))
    assert added_records and {(record["kind"], record["event"]) for record in added_records} == {("mirror.event", "model.admission")}
    assert {(event["kind"], event["authority"], event["attempt"]) for event in added_events} == {("model.admission", "auxiliary", 0)}
    assert len(added_records) == len(added_events)                  # each event mirrored exactly once
    # every other pointer-free difference is excluded above; the scenario's own event list gains only the new kind
    kinds_before, kinds_after = before["extra"].get("event_kinds"), after["extra"].get("event_kinds")
    if kinds_before is not None:
        assert set(_extras(kinds_before, kinds_after)) == {"model.admission"}
    assert {k: v for k, v in before["extra"].items() if k != "event_kinds"} == {k: v for k, v in after["extra"].items() if k != "event_kinds"}


def test_the_pre_r1_reference_is_exactly_the_four_enforce_scenarios_and_no_longer_what_the_kernel_produces(tmp_path):
    """Negative control: the kept reference is not a second accepted golden - the kernel no longer produces it."""
    assert sorted(path.stem for path in PRE_R1_DIR.glob("*.json")) == sorted(R1_ENFORCE_ROW_ADDITIONS)
    name = "enforce_plan_invalid"
    form = _form(name, tmp_path)
    assert form == _golden(name)
    assert form != (PRE_R1_DIR / f"{name}.json").read_text(encoding="utf-8")


# --- TIME-BUDGET-EXHAUSTED-MODEL-ATTRIBUTION-001 (owner ruling 2026-11-13 after Scenario-A run 9) -----------------------------------------
# The ONE golden that correction re-recorded: the budget stop's attribution is now RESOURCE_LIMIT / high / "no source file owns it" instead of
# SOURCE_DEFECT / low / "triage found nothing actionable". The file it replaced is kept in PRE_RUN9_BUDGET_DIR; the delta is pinned below.
# Hashed contents before and after: ~/kriya-evidence/product-72h-confidence/run9-fixes-2026-11-13/budget-attribution/golden_content.diff.
PRE_RUN9_BUDGET_DIR = kg.GOLDEN_DIR.parent / "kernel_pre_run9_budget_attribution"
RUN9_BUDGET_RERECORDED = ("legacy_budget_stop",)
# (the line's key, digest before, digest after): the diagnosis record's reasoning and payload, and the gate outcome that carries the same fields
RUN9_BUDGET_DELTA = [
    ('"attribution_reasoning"', "sha256:529720fc811eddec", "sha256:019fd0947fc19791"),
    ('"payload"', "sha256:f1bbc081d1bd5eea", "sha256:e1745eae00ba0339"),
    ('"outcome"', "sha256:d70d91e3560c99b3", "sha256:ac4204196673eccb"),
]


def test_the_budget_stop_golden_differs_from_its_pre_run9_form_only_in_the_attribution_digests(tmp_path):
    (name,) = RUN9_BUDGET_RERECORDED
    assert sorted(path.stem for path in PRE_RUN9_BUDGET_DIR.glob("*.json")) == sorted(RUN9_BUDGET_RERECORDED)
    before = (PRE_RUN9_BUDGET_DIR / f"{name}.json").read_text(encoding="utf-8").splitlines()
    after = _before_s1(name).read_text(encoding="utf-8").splitlines()   # as the correction left it (S1 re-recorded it since)
    assert len(before) == len(after)                                    # nothing added, removed or reordered
    changed = [(old.strip(), new.strip()) for old, new in zip(before, after, strict=True) if old != new]
    assert len(changed) == len(RUN9_BUDGET_DELTA)
    for (old, new), (key, digest_before, digest_after) in zip(changed, RUN9_BUDGET_DELTA, strict=True):
        assert old.rstrip(",") == f'{key}: "{digest_before}"' and new.rstrip(",") == f'{key}: "{digest_after}"'
    # negative control: the kept reference is not a second accepted golden - the kernel no longer produces it
    form = _form(name, tmp_path)
    assert form == _golden(name) and form != (PRE_RUN9_BUDGET_DIR / f"{name}.json").read_text(encoding="utf-8")
