# Backlog Closure 6.5 - directives (user, 2026-09-27)

Verbatim record of the user's directive (second, superseding version, which added INF-001).

Do not start Batch 7 until all six items below are addressed and verified. Scope is limited to these items plus any newly discovered P0/P1 correctness/security defect.

## Required items
1. `KNOWLEDGE-READPATH-001` P1
2. `FALLBACK-CONTEXT-WINDOW-001` P2/triage
3. `PROMPT-BUDGET-FIT-001A` P2
4. `PROMPT-BUDGET-FIT-001B` P2
5. `PRD027-SCORE-NORMALIZATION-001` P2
6. `INF-001` P2

Implementation order: 1 → 2 → 3+4 together → 5 → 6.

## 1 KNOWLEDGE-READPATH-001
Map every learned-knowledge writer, DB/schema and reader before changing code. Establish one canonical read/write contract or explicit adapters/migration where required. `kriya learn` output must be consumable by intended ask/generate/workflow paths.
Hard invariants:
* learned/retrieved content is untrusted `reference_context`;
* never append it to `authoritative_user_goal`;
* it cannot create requirements, mutation authority, API authorization, expected-exit authorization, approval, proposal-promotion authority or resume-goal identity;
* retrieval queries remain goal/intent-derived and never recursively consume retrieved text;
* preserve provenance/source metadata;
* malformed/corrupt stores fail closed where correctness/security requires.

Tests: write→read E2E; ask/generate/workflow parity; legacy/current store compatibility as applicable; malicious learned content cannot grant authority; provenance preserved.

## 2 FALLBACK-CONTEXT-WINDOW-001
Fix the mismatch where fallback `context_window` affects Kriya budgeting but may not be sent to the inference runtime.
Invariant: `budgeted effective context == served effective context`, unless runtime capability forces a smaller value, which must be explicitly recorded/refused.
Requirements:
* config authors must not need duplicate `context_window` + provider-specific `num_ctx`;
* generic orchestration must not contain Ollama-specific behavior;
* runtime adapter translates generic context request to provider representation;
* explicit provider override conflicts must be deterministic and tested;
* runtime fingerprint/qualification evidence records actual served context.

Tests: primary/fallback; explicit/implicit context; runtime cap; conflicting override; fingerprint; qualification; fallback transition.

## 3 PROMPT-BUDGET-FIT-001A/B
Fix together through one shared fixed-overhead-aware budgeting primitive. Do not create independent Planner/Reviewer patches or arbitrary constants.
Compute variable capacity from:
`effective prompt capacity - mandatory fixed request cost - output reserve - safety reserve`.
Apply to Planner graph/reference context and Reviewer file batches. Account for system prompt, goal/header, repository context, diff/evidence and required blocks.
Keep genuine `CONTEXT_BUDGET_UNSATISFIABLE` fail-closed behavior and preserve 001C typed terminal-result semantics. Do not increase context windows or reduce safety margins to pass tests.
Tests: 8K/32K, large graph, large diff, fixed-overhead boundary, genuine unsatisfiable request, direct/enforce/milestone terminal behavior.

## 4 PRD027-SCORE-NORMALIZATION-001
Fix incomparable ranking domains between direct search and graph-expanded evidence.
Invariant: direct query evidence must not lose priority merely because graph scores use numerically larger values.
Use a deterministic comparable ranking strategy such as explicit evidence tiers, rank fusion or normalized ranks; no fixture-specific score constants.
Preserve PRD-027 corroborated expansion rule and embedding-only/keyword-only behavior.
Tests must prove:
* direct hits outrank secondary graph evidence under constrained budgets;
* useful graph evidence remains retrievable;
* real and CI certification precision >= 0.5;
* every existing recall target still passes;
* certification identity invalidates when ranking implementation changes.

## 5 INF-001 Pluggable Inference Runtime Framework
Implement after items 1–4 are stable. Do not implement full vLLM integration in 6.5.
Target architecture: `Workflow/Agents → InferenceService → InferenceRuntimePort → runtime adapters`.
Kriya owns routing, qualification, fallback, retry, budgets, authority, evidence, verification and run state. Runtime adapters own provider/runtime translation, model/runtime probing, served-context controls, capabilities and inference transport.

Required contract should cover:
* completion/chat request;
* streaming;
* structured/JSON mode;
* tool-call capability/protocol;
* reasoning controls;
* requested/effective context window;
* runtime/model fingerprint;
* tokenizer identity/capacity evidence;
* provider/runtime capabilities;
* cancellation/timeout/error normalization;
* health/connectivity/model discovery where required.

Deliver:
* provider-neutral `InferenceRuntimePort`;
* current Ollama/OpenAI-compatible behavior behind an adapter;
* deterministic fake/test adapter;
* adapter registry/factory selected from configuration;
* no provider-specific branching in workflow/agent orchestration;
* current qualification/evidence identity remains runtime-specific;
* same weights on different runtimes do not automatically share qualification;
* environment/capacity evidence remains distinguishable from functional qualification;
* adapter lookup/fingerprint/qualification lookup cached; no runtime probe per normal model call;
* abstraction overhead negligible and measured independently of inference.

Do NOT:
* add full vLLM production support yet;
* move Kriya policy into runtime adapters;
* weaken exact-runtime qualification;
* change current default models/routes;
* couple generic interfaces to Ollama field names such as `num_ctx`.

Tests:
* existing Ollama behavior parity;
* fake runtime contract suite;
* primary/fallback through abstraction;
* context propagation;
* reasoning/settings propagation;
* tools/JSON/streaming;
* timeout/cancel/error normalization;
* runtime fingerprint changes when runtime materially changes;
* same model weights + different runtime => distinct qualification identity;
* no provider-specific workflow branching.

Document the future `VllmRuntimeAdapter` extension point only; real vLLM implementation stays later unless separately approved.

## Commit/delivery discipline
Use coherent local commits per workstream: KNOWLEDGE, FALLBACK-CONTEXT, PROMPT-BUDGET, SCORE-NORMALIZATION, INF-001. No unrelated refactoring and no push.
For each item update defect/PRD handover, tracker, tests and decision record where architectural behavior changes.
New P2/P3 findings may be recorded without expanding 6.5. New P0/P1 correctness/security findings block closure.

## Final 6.5 verification
After all workstreams:
1. focused tests for every changed subsystem;
2. full `.venv/bin/pytest`;
3. relevant live-model tests;
4. retrieval/index E2E;
5. `context certify`;
6. exact model/runtime qualification where runtime identity or inference settings changed;
7. `doctor --production`.

Exit criteria:
* all six items VERIFIED or INF-001 explicitly COMPLETE for its scoped runtime-framework deliverable;
* `CERTIFIED=true`;
* precision >= 0.5 and all recall targets pass;
* primary/fallback exact runtime identities QUALIFIED;
* `model.qualification=PASS`;
* `context.recall_certification=PASS`;
* `PRODUCTION_READY=true`;
* no open P0/P1 defect remains.

Then stop, report final local HEAD/evidence/open P2+ backlog, and request approval before push or Batch 7.
