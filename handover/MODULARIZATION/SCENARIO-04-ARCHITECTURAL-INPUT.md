# Scenario 04: architectural input to full modularization

Source: two live failures of the Spring XML + Apache Ignite + Protocol greenfield scenario on the frozen Demo V2 product plus a small planner fix. Full analyses: `handover/SPRING-IGNITE-PROTOCOL/LIVE-FAILURE-ANALYSIS.md` (run `20261016T085640-13e8ec80`, planner) and `LIVE-FAILURE-2-ANALYSIS.md` (run `20261016T094912-e7b4804c`, generation / repair). Evidence and replay scripts: `handover/SPRING-IGNITE-PROTOCOL/evidence/live-failure-*/`. This file is the structural residue: what the failures say about module boundaries. **No product code was changed for this input; scenario 04 is NOT validated and not tagged.** Statuses as in `handover/ENGINEERING_RULES.md`.

## 1. What the two runs established
1. Run 1 (planner): a validator rule (every subtask of a multi-subtask plan needs an invariant id) that no Planner contract stated. Fixed generically (contract sentence + typed repair block), merged on the scenario branch. Run 2 shows the planner now converges (one repair of a different, schema-level problem; every subtask carries invariants).
2. Run 2 (generation / repair): the model's first candidate was wrong (private field access), Kriya rejected and attributed it correctly, and then **Kriya's own repair architecture turned a correct model fix into a failure.**

## 2. First wrong decision (run 2)
Model: attempt 1 `ProtocolParser.java` reads `Protocol`'s private fields. Kriya, first wrong decision: the attempt 2 -> 3 retarget. After the model's repair edit for `ProtocolParser.java` was refused (`ANCHOR_NOT_IN_FILE`, fabricated anchor), `extract_self_diagnosed_files` read the model's prose ("the private fields of the `Protocol` class") as a diagnosis naming `Protocol.java`; that MODEL_CLAIMED attribution outranked the compiler locator (DERIVED_DETERMINISTIC, `ProtocolParser.java:10-21`); `Protocol.java` (which already had every accessor) became a repair target. Decisive consequence: in attempts 3 and 4 the model produced a **valid** `ProtocolParser.java` edit (replay: applies, javac 0 errors) and the attempt, being atomic, refused it because the sibling `Protocol.java` response violated the single-file contract (`INVALID_EDIT_PROTOCOL`) or its edit failed (`INDENTATION_STYLE_MISMATCH`).

## 3. Deterministic reproducers (no model)
All under `handover/SPRING-IGNITE-PROTOCOL/evidence/live-failure-20261016T094912-e7b4804c/`; run with the repository `.venv` Python, `javac` on PATH.
- `replay_attribution.py` / `replay-attribution.txt`: the five recorded analyses, attached to the parser, all yield `[Protocol.java]`. Controls: an analysis that only quotes the compiler message also yields `[Protocol.java]`; one that names no other file yields `[]`. (A mention is not a diagnosis, and the extractor cannot tell them apart.)
- `replay_generation_failure.py` / `replay-generation.txt`: the recorded Developer responses through `parse_structured` and the real edit engine against the staged attempt-1 files, then javac: attempt 2 `ANCHOR_NOT_IN_FILE`; attempt 3 Protocol `INVALID_EDIT_PROTOCOL`, parser edit APPLIES with 0 javac errors; attempt 4 Protocol `INDENTATION_STYLE_MISMATCH`, parser edit APPLIES with 0 javac errors.
- `budget_timeline.py` / `budget-timeline.txt`: 673.0 s of the 900 s budget used when the fallback pass was refused, of which 128.5 s was the owner's source review; without it 355.5 s would have remained against the 300 s required.

## 4. Current owners and the coupling involved
| Concern | Current owner (file) | Coupling that bit |
|---|---|---|
| Free-text analysis -> attribution | `kriya/workflow/attribution.py` (`extract_self_diagnosed_files`, `attribute_failure` tier order), capture site `workflow/attempt.py` (`state.last_self_diagnosis`), gating `workflow/retry_strategy.py:951-957` | model prose parsed by a filename-substring scan inside the attribution module; ranks above deterministic locators; gated by a failure signature that an edit-protocol failure collapses onto the authoritative failure |
| Retry target set and single-file repair contract | `retry_strategy.py` (target union), `agents/agent.py` ("Repair exactly one existing file X ... ONLY"), `agents/response_protocol.py` (block must name X) | the target set can contain files the evidence does not implicate; per-file requests then carry findings about another file |
| Attempt = atomic candidate | `workflow/attempt.py` (candidate staging / `candidate.change` REFUSED for every file when one response is invalid) | a valid proposal for the blamed file is discarded with the invalid sibling |
| Anchor context shape | `workflow/attempt.py` retry package, `known_target` / `member_exact` tiers (`code_intel`) | attempt 2 showed member excerpts only; the model anchored a whole file |
| Generation budget | `workflow/workflow.py:2825` + `control/run_coordinator.py::claim_run_generation_clock` + `attempt.py::_ensure_generation_time_budget` + `config.py` (`generation_*`) | one run-wide clock from the first planner call; includes human approval wait; the fallback model's pass is estimated from the configured 90 s/file because it has no timings |
| Triage on orchestrator failures | `attribution.py` triage tier | the fallback model was asked to attribute Kriya's own budget message to a source file |
| Plan shape | `workflow/plan_validation.py` ownership chain, planner contract | a first unit owning every source file, a second unit re-owning two of them |

## 5. Desired modular ownership
- **Repair/Retry module** owns: failure attribution (typed evidence classes; a MODEL_CLAIMED attribution may add a read-only context file but must not replace or outrank a DERIVED_DETERMINISTIC locator without corroboration), the retry target set, the retry budget and progress classification, and the **candidate transaction** (per-file proposals validated independently; a valid proposal for an implicated file is staged even if another proposal is invalid; the attempt fails only for what is actually invalid).
- **Developer/Generation module** owns: the response protocol and edit engine, the single-file repair contract (a response may state "the defect is in file Y" as a typed signal that the orchestrator can act on, instead of an invalid-protocol refusal), the context shape for anchored edits (whole-file for small files).
- **Execution/Build and Qualification** own: gate execution and the structured finding (file, line, symbol owner), including the machine-readable "private access in <type>" owner/site distinction so attribution never needs prose.
- **Provider module** owns: model selection, fallback transitions, per-model timing estimates and the role of the fallback (generation vs triage); a fallback pass must not be priced from a default when other-model timings exist.
- **Planner/Kernel** owns: plan shape rules (sequential same-file ownership chains), the plan contract text (single source for validator rules, as `GLOBAL_INVARIANT_PROJECTION_RULE` now is), and the run clock semantics (what counts against a generation deadline: model time, not human wait).
- **Knowledge/Skills** owns: skill selection and delivery (worked correctly in both runs: planner correctly receives none, Developer receives it in every request including repairs) and the `knowledge.offline_mode` decision for post-cutoff libraries (backlog `KNOWLEDGEGUARD-SKILL-IDENTITY-001`).
- **Evidence** owns: the attempt-evidence recorder (it made this reconstruction possible). Gap: the derivation of a retry target set (which tier contributed which file) is not one record; it was reconstructed from `diagnosis.likely_files`, `file_locations` and the log.

## 6. Tests needed after modularization (to be written against the new module boundaries)
1. Attribution: recorded run-2 analyses + the compile locator must yield target `[ProtocolParser.java]` only (or `Protocol.java` strictly as read-only context); the quoted-compiler-message control must not redirect; a corroborated redirect still must (the original PRV-05 case).
2. Transaction: attempt with {valid edit for the blamed file, invalid response for a sibling} stages the valid edit and fails only the sibling; mutation: restore atomic refusal and the test fails.
3. Replay of the recorded attempt 3: the repaired candidate compiles; the attempt does not end FAILED for that reason.
4. Single-file repair contract: a response to a request for file X that states the defect is in file Y is classified as a typed redirect signal, not as a protocol violation of the whole attempt.
5. Budget: the human-approval wait is excluded from (or reported separately in) the generation budget; the fallback estimate uses available timings; the fallback is not asked to triage an orchestrator notice.
6. Context shape: for a small file the retry request always shows the whole file the edit must anchor in.
7. The existing planner regression (`tests/test_plan_global_invariant_projection_001.py`) and the scenario-04 qualification (`demo-v2/scripts/qualify_spring_ignite.sh`) must keep passing across the refactor (behaviour-preserving); the first post-modularization live scenario-04 run is the validation of all of this, not a rerun of the same candidate.

## 7. Not decided here
Whether the self-diagnosis tier should survive in any form (it fixed a real opposite failure in PRV-05); whether repair should be per-file or per-finding; whether the generation deadline should be per phase. These are modularization design decisions.
