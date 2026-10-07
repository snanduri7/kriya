"""LR-R1-M1.6: prompt.sections (design §3.3 "prompt.sections"; test T9
sections part).

A Developer request's recorded segment map reproduces the user message the
model was actually sent, byte for byte: each segment's offset, length and
digest match the recorded message, and the segments tile it exactly. The
variable-section primitive records every fit it decides.
"""
import json

from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)

from kriya.core.attempt_evidence import reader
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.context_budget import (
    OptionalSection,
    RequestCapacity,
    digest_text,
    fit_developer_request,
    fit_variable_section,
)


def _run_pipeline(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer"
                           else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    run = reader.open_run(str(state), run_id)
    return run, list(run.records())


def test_developer_segments_reproduce_the_message_actually_sent(tmp_path, monkeypatch):
    run, records = _run_pipeline(tmp_path, monkeypatch)
    pairs = []
    for index, record in enumerate(records):
        if record["kind"] == "prompt.sections" and record["payload"]["fitter"] == "fit_developer_request":
            request = next(r for r in records[index + 1:] if r["kind"] == "model.request" and r["role"] == "developer")
            pairs.append((record, request))
    assert pairs, "a Developer request must record its sections"
    for sections, request in pairs:
        messages = json.loads(run.blob(request["blobs"]["messages"]))
        user = next(m["content"] for m in messages if m["role"] == "user")
        segments = sections["payload"]["segments"]
        assert sum(s["chars"] for s in segments) == len(user)
        assert [s["offset"] for s in segments] == [sum(x["chars"] for x in segments[:i]) for i in range(len(segments))]
        for segment in segments:
            text = user[segment["offset"]:segment["offset"] + segment["chars"]]
            assert digest_text(text) == segment["digest"], segment


def test_a_trimmed_section_is_recorded_with_what_was_kept_and_what_was_asked(tmp_path, monkeypatch):
    """Unit level, through the real fitter: an optional section that does not
    fit is rebuilt smaller; the record names it, its requested and kept
    size, and the kept segments still tile the returned prompt."""
    from kriya.core.attempt_evidence import scope

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    big = "graph line\n" * 400
    section = OptionalSection(kind="graph_context", text=big, rebuild=lambda budget: "graph line\n" * max(0, budget // 4))
    prompt = f"TASK HEADER\n{big}TASK FOOTER\n"
    capacity = RequestCapacity(tokens=600)

    from tests._strict_doubles import strict_config

    class _Context:
        run_id = "run-sections"
    with scope.run_scope(_Context()):
        scope.ensure_store(strict_config())
        fitted, details = fit_developer_request(capacity, "SYSTEM", prompt, [section])
        scope._RUN.get().writer.seal("test")
    record = [r for r in reader.open_run(str(state), "run-sections").records()
              if r["kind"] == "prompt.sections" and r["payload"]["fitter"] == "fit_developer_request"][0]
    payload = record["payload"]
    assert payload["fitted"] is True and details["sections"]["graph_context"]["reduced"] is True
    optional = [s for s in payload["segments"] if not s["mandatory"]]
    assert [(s["name"], s["outcome"]) for s in optional] in ([("graph_context", "trimmed")],
                                                             [("graph_context", "dropped")])
    assert optional[0]["requested_chars"] == len(big) and optional[0]["chars"] < len(big)
    segments = payload["segments"]
    assert len(segments) == 3 and sum(s["chars"] for s in segments) == len(fitted)
    for index, segment in enumerate(segments):
        assert segment["offset"] == sum(s["chars"] for s in segments[:index])
        assert digest_text(fitted[segment["offset"]:segment["offset"] + segment["chars"]]) == segment["digest"]
    assert fitted.startswith("TASK HEADER\n") and fitted.endswith("TASK FOOTER\n")


def test_the_primitive_records_each_fit_it_decides(tmp_path, monkeypatch):
    from kriya.core.attempt_evidence import scope
    from tests._strict_doubles import strict_config

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))

    class _Context:
        run_id = "run-primitive"

    def build_reference(budget):
        return "r" * min(budget * 4, 10_000)
    with scope.run_scope(_Context()):
        scope.ensure_store(strict_config())
        fit = fit_variable_section(RequestCapacity(tokens=600), ["fixed text"], build_reference)
        scope._RUN.get().writer.seal("test")
    [record] = [r for r in reader.open_run(str(state), "run-primitive").records() if r["kind"] == "prompt.sections"]
    assert record["payload"]["fitter"] == "fit_variable_section"
    assert record["payload"]["section"].endswith("build_reference")
    assert record["payload"]["kept_digest"] == digest_text(fit.value)
    assert record["payload"]["omitted"] is fit.omitted and record["payload"]["builds"] == fit.builds
