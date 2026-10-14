# Scenario 03 - Controlled repair

## ID
`03-controlled-repair`

## TITLE
CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO: a wrong candidate is caught by Kriya's deterministic validation and repaired.

## PURPOSE
Show the engineering control system around an imperfect model: the model proposes a plausible but wrong candidate, Kriya's deterministic validation rejects it with explainable evidence,
a permitted next attempt supplies a correct candidate, and the tests pass. Story: "Local models are imperfect; Kriya is the engineering control system around them."
The fixture is a three-line pricing function: `apply_discount(200, 25)` must be 150. The scripted wrong candidate returns the discount amount (`price * percent / 100` = 50) instead of the discounted price.

## STATUS
**REHEARSED, PASS, REPRODUCIBLE (2026-10-14, product `5cf7676`+ on `demo/v2`).** Run from the desktop app against the scripted stub: Attempt 1 failed (gates 2 passed / 2 failed, first failed gate `tests`, recovery decision `retry`), Attempt 2 was a
`repair_with_full_file` that passed (7 passed / 0 failed), the change was approved by a person in the GUI, and the run ended `success` in 0:32 with 9 model requests, all answered by the stub, 123 evidence records, sealed and VERIFIED.
The same script replayed twice through the CLI gave the identical 9-exchange sequence both times, and `verify_demo.sh --phase post` reported 0 failed each time. A third run from a clean reset on the final Demo V2 HEAD (`20dde1d`, fresh work area, stub restarted at position 0) gave the same outcome and the same 9-exchange sequence (`handover/DEMO-V2/evidence/scenario-03-rehearsal-2-clean-reset/`). Evidence: `handover/DEMO-V2/evidence/scenario-03-rehearsal-1/`.

> **LABEL (mandatory in the GUI notes, presenter notes and any recording): this is a CONTROLLED / SCRIPTED scenario.**
> The model responses come from a deterministic stub that replays a fixed script. It is NOT live-model behaviour and must never be presented as evidence of how a real model behaves or fails.
> The GUI shows it honestly: the Attempts view names `scripted-stub` as the model of every role.

What is scripted and what is not: the script has 9 answers. Two are written for this scenario: the **Planner** answer (a single-unit plan, recorded earlier from a real run of the same goal) and the **first Developer answer** (the wrong candidate).
The other 7 (File List planner, the second Developer answer, run-verification judge, goal-spec compliance checks, reviewer, a knowledge-extraction call) are **verbatim answers of a real model** (`qwen3-coder:30b`, fallback `qwen3.6:35b-a3b-q4_K_M`),
recorded through the recording proxy for the exact path the two scripted answers cause. Kriya's decisions in between (running the gates, comparing with the baseline, rejecting the candidate, retrying, committing) are Kriya's own and are not scripted.
The reviewer and spec-compliance texts about attempt 1 are therefore real-model opinions of the scripted candidate. The stub is the one thing standing in for a model; replacing it is the only change to a normal run.

## PRECONDITIONS
- Product checkout contains tag `kriya-v1.2.0-pre-modularization` with a clean tracked tree; `.venv` built; the desktop app built (`cd ui/standalone && npm run package:mac:evidence`).
- Nothing else listening on `127.0.0.1:18767` (the stub's port). No model server is needed and none is contacted.
- **Feasibility, measured (no product special-casing):** Kriya reaches the stub through its ordinary configuration (`llm.base_url`). A non-default model endpoint is a security-authority field, so the one-time operator approval
  (`kriya authority approve`) applies exactly as for any endpoint. Kriya also verifies the provider: a stub that reported 1 prompt token for an 8 KB prompt was refused (`PROVIDER_PROMPT_TRUNCATED`), so the stub reports honest token counts.
- **The fallback chain must be scripted too.** Kriya escalates a failed attempt to the fallback model, and the packaged default chain points at a real model server. `config/kriya.template.yaml` therefore sets `llm_chain` to the stub; without it the retry would be answered by a real model (measured).
- The operator step: read and approve the workspace configuration (RESET step 2).

## PRODUCT HEAD/TAG COMPATIBILITY
Rehearsed on `demo/v2` after tag `kriya-v1.2.0-pre-modularization` (`1064b9e`). Any later product change that alters how roles are called (prompt order, retry path, the set of roles) can change the replay path: re-record
with `stub/stub_model_server.py --mode record --override ...` and `stub/build_script.py`, then re-run this scenario. `verify_demo.sh --phase post` fails if the served sequence is not exactly the script.

## MODEL REQUIREMENT
None. A scripted stub replaces the model. Optional later: one live-model variant, clearly separate and not presented as reproducible.

## NETWORK REQUIREMENT
None. The stub listens on `127.0.0.1` only, and in replay mode makes no outbound connection of any kind.

## RESET
```bash
demo-v2/scripts/prepare_demo.sh --scenario 03 [--check-build]   # 1. workspace from the fixture, revoke any earlier approval, render + place the configuration, START the stub at script position 0
( cd "$DEMO_V2_WORK/scenario-03/workspace" && kriya authority approve )   # 2. OPERATOR STEP: read the listed fields, answer y
demo-v2/scripts/verify_demo.sh  --scenario 03 --phase pre       # 3. must report 0 failed (includes: baseline "2 failed, 1 passed", stub running, stub at position 0)
```
`scripts/stub_ctl.sh start|stop|status` controls the stub. A fresh `start` resets the script position, so every run replays the same sequence. `--check-build` runs the fixture's own pytest and requires the measured baseline.

## RUN
Automated: `demo-v2/scripts/rehearse_gui.sh --scenario 03` (scripted folder chooser; approves the change review). By hand:
1. Start the desktop app; Open Folder: `$DEMO_V2_WORK/scenario-03/workspace`. (Not yet confirmed: whether Run Setup's model-routing table also shows `scripted-stub`; the Attempts view does.)
2. Enter the contents of `goal.md` and Start Run.
3. Watch Attempt 1: gates run and the candidate is rejected; open Attempts and the Why panel (first failed gate, recorded diagnosis, "What Kriya did next": retry).
4. Attempt 2 is a repair; gates pass; the change review appears. Read what it shows (file, `+1 -1`, reviewer text, validation; the diff text is hidden by the default content-visibility policy) and approve it.

## EXPECTED EVENTS
Planner (one unit) -> Developer attempt 1 (wrong candidate) -> gates run; full regression compares with the pre-run baseline and `test_no_discount` (passed before, fails now: `assert 0.0 == 80`) is newly failing -> attempt 1 failed ->
recovery decision: retry on the fallback (also the stub) -> File List planner -> Developer attempt 2 (repair_with_full_file) -> gates pass -> judge, spec compliance, reviewer -> change review (human) -> approve -> terminal check -> success.

## EXPECTED EVIDENCE
Attempts view: two attempts for unit `s1`, #1 `create_full_file` failed, #2 `repair_with_full_file` succeeded ("fell back to scripted-stub"). Validation: first failed gate `tests` at attempt 1. Why: recorded diagnosis (`TEST_DEFECT`, DERIVED_DETERMINISTIC) and the recovery decision.
`<work>/scenario-03/evidence/stub-exchanges.jsonl`: exactly the 9 scripted exchanges, 0 unscripted, Developer asked at index 0 and 1.

## SUCCESS CRITERIA
All of: `verify_demo.sh --phase post` reports 0 failed, i.e. exactly one file changed (`pricing.py`), an independent pytest run passes (3 passed), the stub log shows the whole script served with the Developer asked twice
(evidence that a repair attempt was requested; the recorded cause, the rejected candidate and the failed gate, is read in the GUI Attempts and Why panels), the script labels its first Developer answer as the scripted wrong candidate, and Kriya recorded state. The run's outcome in the GUI is success and the change was approved by a person.

## FAILURE DIAGNOSIS
- Stub log shorter than the script, or an unscripted exchange: the call path changed (a product change, a different Kriya) - re-record, do not edit the script by hand to fit.
- Developer asked once: attempt 1 was not rejected; the wrong candidate is no longer caught by a deterministic check. Choose a different defect; do not weaken a check.
- A model name other than `scripted-stub` in the Attempts view: the configuration is not the scenario's (for example the fallback chain is missing).
- `PROVIDER_PROMPT_TRUNCATED` or another provider-contract refusal: the stub's reported counts are not plausible for the prompt.
- Port in use: another process holds 18767; stop it, do not change the config to point elsewhere.
