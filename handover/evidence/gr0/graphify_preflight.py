"""GR-R0 Part 3: Graphify no-model preflight (harness; no model is contacted).

The frozen Graphify shape through the real direct workflow at the current
Kriya checkout: the original goal (goal.txt, sha256 f96bf5a3...), the real
graphify/extractors/engine.py at the frozen base 67f99bd (331,064 bytes, 6,318
lines), the historical plan's single target, and the v5 production Developer
bindings (32,768-token window, 16,384 output tokens, the qwen3.6 fallback).
The Developer transport answers with the canonical run's real attempt-1
SEARCH/REPLACE (the historical answer, no new information) and every
Developer request is captured at DeveloperAgent._fit_request (tokens against
the request's own capacity). Gates are stubbed green. Nothing is sent.

usage: PYTHONPATH=<checkout>:<checkout>/tests python graphify_preflight.py <tmpdir> <goal.txt> <engine.py>
       <canonical_attempt1_edits.json> <out.json> [<requirements contract.json>]

GR-R1: with a requirement contract the run is bound to it exactly as `generate --requirements` binds it."""
import hashlib
import json
import sys
from pathlib import Path

import pytest
from _edit_protocol_harness import PRODUCTION_CAPABILITIES, run_edit_protocol
from test_prd020_milestone_requirements import _probe

from kriya.agents import agent as agent_module
from kriya.workflow.context_budget import estimate_tokens

TARGET = "graphify/extractors/engine.py"
PRIMARY_OUTPUT_TOKENS = 16384  # v5 llm.max_tokens
WINDOW = 32768                 # v5 num_ctx, primary and fallback
FALLBACK = "qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a"


def main(tmp, goal_file, engine_file, edits_file, out_file, contract_file=None):
    goal = Path(goal_file).read_text(encoding="utf-8")
    source = Path(engine_file).read_text(encoding="utf-8")
    edits = json.loads(Path(edits_file).read_text())
    answer = "FIX ANALYSIS: the canonical run's attempt-1 answer, replayed.\n" + "".join(
        f"SEARCH:\n{e['search']}\nREPLACE:\n{e['replace']}\n" for e in edits)
    captured = []
    original_fit = agent_module.DeveloperAgent._fit_request

    def capture_fit(self, request_fit, system_prompt, prompt, filepath, model_override, sibling_section,
                    reduced_sibling_section, expectation):
        fitted = original_fit(self, request_fit, system_prompt, prompt, filepath, model_override, sibling_section,
                              reduced_sibling_section, expectation)
        capacity = request_fit.capacity(getattr(expectation, "tokens", None))
        captured.append({
            "model": model_override or "primary", "capacity_tokens": capacity.tokens,
            "pre_fit_tokens": capacity.count(system_prompt) + capacity.count(prompt),
            "final_request_tokens": capacity.count(system_prompt) + capacity.count(fitted),
            "final_request_bytes": len((system_prompt or "").encode()) + len(fitted.encode()),
            "fitted": fitted,
        })
        return fitted

    contract = None
    if contract_file:
        from kriya.workflow.requirement_contract import load_requirement_contract

        contract = load_requirement_contract(contract_file, goal, state_root=str(Path(tmp) / "contract-state"),
                                             workspace=str(Path(tmp) / "ws"))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(agent_module.DeveloperAgent, "_fit_request", capture_fit)
    try:
        run = run_edit_protocol(Path(tmp), monkeypatch, [answer], probe=_probe, goal=goal, source=source,
                                target=TARGET, window=WINDOW, capabilities=PRODUCTION_CAPABILITIES,
                                max_tokens=PRIMARY_OUTPUT_TOKENS, fallback=FALLBACK, fallback_answers=[answer],
                                requirement_contract=contract)
    finally:
        monkeypatch.undo()
    events = run.events
    capabilities = [e.details for e in events if e.kind == "context.edit_capability"]
    first = capabilities[0]["targets"][0] if capabilities else None
    lines = source.splitlines()
    spans = [(s["start_line"], s["end_line"], s["unit"]) for s in (first or {}).get("spans", [])]
    span_texts = ["\n".join(lines[a - 1:b]) for a, b, _ in spans]
    req1 = captured[0] if captured else {}
    derived = [e.details for e in events if e.kind == "requirement.derived"]
    report = {
        "requirements": {
            "contract_digest": contract.digest if contract else None,
            "authoritative_ids": [r["id"] for r in derived[0]["requirements"]] if derived else None,
            "authoritative_texts": [r["text"] for r in derived[0]["requirements"]] if derived else None,
            "set_digest": derived[0]["digest"] if derived else None,
            "raw_goal_in_developer_request": bool(captured) and goal.strip().splitlines()[0] in captured[0]["fitted"],
        },
        "kriya_shape": {"file_bytes": len(source.encode()), "lines": source.count("\n"),
                        "revision": hashlib.sha256(source.encode()).hexdigest(),
                        "goal_sha256": hashlib.sha256(goal.encode()).hexdigest(),
                        "full_file_token_estimate": estimate_tokens(source)},
        "attempt1": {
            "full_file_authority": first["full_file"] if first else None,
            "operations": first["operations"] if first else None,
            "spans": spans, "loci": first["loci"] if first else None,
            "insertion": first.get("insertion") if first else None,
            "whole_file_in_final_request": bool(req1) and source.strip() in req1["fitted"],
            "every_exact_span_in_final_request": bool(req1) and all(t in req1["fitted"] for t in span_texts),
            "final_request_tokens": req1.get("final_request_tokens"), "pre_fit_tokens": req1.get("pre_fit_tokens"),
            "capacity_tokens": req1.get("capacity_tokens"),
            "fits": bool(req1) and req1["final_request_tokens"] <= req1["capacity_tokens"],
        },
        "requests": [{k: v for k, v in c.items() if k != "fitted"} | {"fits": c["final_request_tokens"] <= c[
            "capacity_tokens"]} for c in captured],
        "terminal": {"quality_gates_passed": run.result.get("quality_gates_passed"),
                     "failure_category": run.result.get("failure_category"),
                     "developer_models": run.developer_models},
        "trajectory": [(e.attempt, e.kind, (e.message or "")[:140]) for e in events
                       if e.kind in ("failure.recorded", "retry.strategy_transition", "model.transition",
                                     "context.anchor_authorized_by_sent_request",
                                     "context.structural_insertion_authorized")],
        "capabilities": [(c["model"], [(t["digest"][:10], t["operations"], t["full_file"], t["loci"])
                                        for t in c["targets"]]) for c in capabilities],
    }
    Path(out_file).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"requirements": report["requirements"], "attempt1": report["attempt1"],
                      "requests": report["requests"]}, indent=1, default=str))

if __name__ == "__main__":
    main(*sys.argv[1:7])
