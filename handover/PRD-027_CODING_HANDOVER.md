# PRD-027 Coding Agent Handover: Context Recall Certification Suite

## Status
READY_FOR_PYTEST_VERIFICATION. This is part of the Batch 6 stop.

## Source identity
- Base revision: the PRD-026 follow-up (2989636).
- Commits:
  - 7c07a74: the retrieval extraction, behaviour unchanged;
  - 8aa6d26: the retrieval defect fixes found by the suite;
  - 3c1822d: PRD-027 (2/2), certification, CLI, doctor, tests and docs;
  - f5f72ed: hardening (superseded in part by 2982f1c);
  - 2982f1c: correction, the identity holds only retrieval inputs (see Req 5).
- Directives: handover/BATCH6_DIRECTIVES.md, the PRD-027 section.

## Scope implemented
- **Req 1 (fixtures).** `kriya/workflow/context_recall_fixtures.py` has two repositories (Java shop, Python payments) with 6 cases. They cover every required class:
  - same-class member;
  - sibling implementation;
  - interface contract;
  - test precedent;
  - build metadata;
  - configuration;
  - direct caller;
  - one-hop and two-hop dependency.
  Each class has at least 2 golden items, and distractor files let precision be measured.
- **Req 2 (golden items).** Each golden item has a required precision (full / skeleton / signatures). Graph RAG packages carry exactly these tiers; member-exact is the known-target path, not Graph RAG.
- **Req 3 (measured before any LLM).** `run_certification` indexes and retrieves with no chat-model call (asserted by patching `LLMClient` to raise). Misses are typed: `NOT_RETRIEVED`, `BUDGET_EXHAUSTED`, `TIER_INSUFFICIENT`, `SOURCE_UNAVAILABLE`.
- **Req 4 (thresholds).** Fixed, version-controlled targets: `CLASS_RECALL_TARGETS` (1.0 for the must-have classes, 0.5 for wider context) and `PRECISION_TARGET` = 0.5. They are pinned by a test. They were set before measuring and were not changed after.
- **Req 5 (recorded result).** The record is written under `<state>/context_certification/<identity-digest>.json`, outside the workspace, atomically. The identity holds only what decides retrieval:
  - the suite version and the Kriya version;
  - the fixtures digest;
  - the index/retrieval/chunker implementation source digest;
  - the embedder kind and embedding model;
  - the exact embedding runtime digest;
  - the embedding dimensions, probed from the embedder and matched against the index (`indexed_embedding_dimensions`; an index without exactly one dimension fails closed);
  - every production retrieval-limit policy and the context tier policy;
  - a fixed certification graph budget (`CERTIFICATION_GRAPH_BUDGET_TOKENS`).
  The chat model is not part of it. Retrieval makes no chat inference, so changing or requalifying the chat model keeps the certification current, and `certify` runs with the chat endpoint unavailable.
  - Tests:
    - `test_embedding_dimension_change_stales_the_certification`;
    - `test_index_or_retrieval_implementation_change_stales_the_certification`;
    - `test_retrieval_limit_policy_change_stales_the_certification`;
    - `test_a_different_embedding_runtime_is_a_stale_certification`;
    - `test_chat_model_change_alone_keeps_the_certification_current`;
    - `test_chat_qualification_or_window_is_never_consulted`;
    - `test_certify_runs_with_the_chat_endpoint_unavailable`;
    - `test_an_index_without_a_single_dimension_fails_closed`.
- **Directive (CI vs production).** CI uses `DeterministicHashingEmbedder`; its reports are marked `deterministic_hashing` and can never satisfy the production status. `kriya context certify` uses the configured embedder and refuses an unprovable runtime.
- **Directive (doctor).** `context.recall_certification` is pinned. It reads the record and never runs the benchmark; a test patches `run_certification` to raise.
  - Required only when a code index exists at `paths.memory`: that is the exact condition under which Graph RAG runs.
  - NOT_APPLICABLE otherwise.
  - Missing, stale, tampered or failing records are a FAIL; an unprovable runtime is UNAVAILABLE.

## Pre-change measurement and the defects it exposed (committed separately in 8aa6d26)
The first CI-embedder measurement, taken before the fixes: every class 0/N. Once the dimension identity was corrected the suite measured non-zero, and it showed:
- configuration 0/2 and build 1/2 (files not indexed);
- Python caller, one-hop and two-hop 0/1;
- interface and test precedent 1/2.
Five defects, each with a regression test, and each caught when its fix is reverted:
1. **Embedding dimension not passed** at the Graph RAG and DEV-INV `search_code` query sites. `query_hybrid` defaults to 768, so any other embedding model silently degraded code retrieval to lexical-only.
2. **`query_lexical` phrase-matched the entire query**, so the lexical leg of hybrid retrieval never matched a natural-language goal. It now OR-s the goal's identifiers and their camel/snake parts, minus function words, BM25-ranked; the LIKE fallback does the same.
3. **The graph never reported callers.** Calls and imports are file-sourced in both parsers, and the symbol join only reports definers. The graph also never walked a matched file's own dependencies or a reached definer's relations (so no two-hop dependency).
4. **`get_neighborhood` capped raw duplicate rows before de-duplication**, so distinct files were crowded out.
5. **Configuration and non-Maven build descriptors were not indexed.** Added: `.properties .yaml .yml .toml .gradle .kts .cfg .ini`. JSON is excluded on purpose.

**After the fixes (CI embedder)**, every class meets its target (all 2/2 or 4/4), and precision is 0.5, exactly at target. The precision is limited on the config and build cases because graph expansion pulls in the rest of the code. That is disclosed as it is, not tuned.

## Files changed
- **Production:**
  - `kriya/workflow/graph_retrieval.py` (new, the extracted stage);
  - `kriya/workflow/workflow.py` (calls it);
  - `kriya/workflow/context_certification.py` and `context_recall_fixtures.py` (new);
  - `kriya/memory/vector.py` (lexical terms);
  - `kriya/analyzer/graph.py` (walk);
  - `kriya/analyzer/analyzer.py`: indexed extensions, `embedding_client=`, `generate_conventions_skill=`;
  - `kriya/workflow/attempt.py` (search_code dimension);
  - `kriya/cli.py` (`kriya context certify`);
  - `kriya/production_doctor.py` (the check).
- **Tests:**
  - `tests/test_prd027_retrieval_defects.py` (8);
  - `tests/test_prd027_context_certification.py` (18);
  - `tests/test_production_doctor.py`: pinned ID; `paths.memory` isolated in the fixture; 3 new tests.
- **Live:** `tests/test_live_prd025_029_batch6.py`, the two `prd027` cases.
- **Docs:**
  - `docs/user_guide.md` §3.1.1;
  - `docs/design.md`: the Graph RAG section, the PRD-027 bullet, and a stale "retrieval stays inside workflow.py" sentence corrected;
  - `CLAUDE.md`: the Graph RAG pipeline step.

## Disclosed behaviour changes
- **Retrieval results change.** More related files are found (callers, dependencies, config), and the lexical leg now matches. Downstream prompts may carry different, larger neighbourhood context within the same token budget; the budget allocator still bounds it.
- **Extraction failure path.** An exception mid-retrieval no longer leaves a partial `retrieved_chunks` observability prefix. The run-level outcome is identical.
- **Existing `doctor --production` test fixtures** now pin `paths.memory` to a temp directory. The CWD-relative default had been picking up this repository's own `memory/vector_index.db`.

## Found, not fixed (outside PRD-027 scope)
- **KNOWLEDGE-READPATH-001 (P1)**: learned knowledge is written but never read. See handover/DEFECT_KNOWLEDGE_READPATH_001.md (affected commands and tables, expected semantics, and the rule that learned text must never reach the authoritative goal).
- A local edit that passed the model identity there was reverted, because it changed nothing.

## Tests run by coding agent (targeted)
| Command | Passed | Failed |
|---|---:|---:|
| `.venv/bin/pytest -q tests/test_prd027_retrieval_defects.py tests/test_prd027_context_certification.py` | 26 | 0 |
| `.venv/bin/pytest -q tests/test_production_doctor.py` | 60 | 0 |
| graph, RAG, vector, indexing, analyzer, review-context, DEV-INV and context-source suites (11 files) | 295 | 0 |
| the analyzer, ask and vector suites (7 files) | 41 | 0 |
| `tests/test_workflow.py -k "graph_rag or hybrid_match or retrieval or grounding or build_code_context or neighborhood"` | 13 | 0 |

**Mutation checks** (every one killed):
- 7 retrieval-fix reverts;
- 9 certification/doctor/CLI mutations. Three initially SURVIVED (the precision gate, the identity-tamper check, the CLI exit code); a test was added for each.
- One mutation batch was invalid ("no tests ran": zsh did not word-split the test-file list). It was caught and rerun properly.

## Static/lint/architecture checks
ruff: All checks passed. pylint: exit 0.

## Live test additions
- Required: YES.
- `test_live_prd027_certification_with_the_real_embedder`: runs the suite with the real embedding model (`KRIYA_LIVE_EMBED_MODEL`, default `nomic-embed-text:latest`) and persists the production record. Whether it certifies is recorded as evidence and is not assumed by the test.
- `test_live_prd027_developer_prompt_receives_golden_evidence`: a real generate run on the Java fixture, indexed with the real embedder. The Developer's own first prompt must name OrderService, DiscountPolicy and OrderController.
- To produce the production record for doctor, the user also runs: `kriya -c <your kriya.yaml> context certify`.

## Known limitations / residual risks
- The benchmark is small (2 repositories, 6 cases, 2–4 golden items per class). Recall per class moves in steps of 0.5; it is a floor, not a statistical estimate.
- Certification covers the Graph RAG stage (the Developer's first-attempt semantic context). The known-target member-exact context and the planning candidate lists are separate mechanisms and are not certified here.
- Precision is at the target (0.5), with no margin, under the CI embedder.
- The certified graph budget is a fixed constant, not the production chat model's own allocation. Certification proves what retrieval delivers within that budget. A deployment whose chat window allocates a smaller graph pool may degrade more files at prompt assembly, and that degradation is not certified.
- Seeding the graph walk with each matched file follows every call in that file. On a real repository, common method names can fill the 30 neighbourhood slots (one entry per file, ranked by relation weight and hop). The small fixtures cannot show that precision loss; the live certification with the real embedder is the measurement to watch.
- `test_an_embedding_model_runtime_can_be_proven_exact` drives the real PRD-013 probe with an embedding-shaped endpoint and shows the runtime identity is provable. So `kriya context certify` and the doctor check do not block every indexed deployment.

## Verification-agent handoff
Run the Batch 6 focused command, then the full suite, then the live `-k prd027` cases, then `kriya context certify` with the production config.
