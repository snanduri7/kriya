# CTX-001 Final Assurance

**Status:** ASSURANCE ONLY. Zero production code changed in this pass. No live models/embeddings run. No full `pytest` run by the agent (the user independently confirmed `4242 passed, 5 deselected, 0 failures` immediately prior to this pass, against the exact commit this document assesses).

**Baseline:** `origin/milestone-decomposition == a908846`. This document assesses the complete CTX-001 arc: P0 (`c212f2f`..`91aec04`) → P1 architecture (`c2bdd61`) → WP1/WP2 (`e4a238d`) → Package 2/WP3-WP7 (`5e1ff3f`) → C2 investigation (`f3ff29f`) → C2 implementation (`a71c498`) → Package 3/WP9 (`3aec0c8`) → WP8 resolution (`a908846`).

**Method:** every claim below is either (a) a fresh, deterministic replay against the current, unmodified production code (`spikes/ctx_001_p0/run_p1_final_replay.py`, this pass), or (b) a citation of an already-existing, already-passing deterministic test re-executed fresh in this pass, never an inference from a commit message or an assumption that a prior pass's claim still holds.

---

## 1. Thesis

CTX-001 set out to answer one question: **does Kriya's engineering correctness depend on model context-window size or repository size, and if so, where exactly, and how much of that dependency is deterministically fixable without touching the retrieval/indexing architecture itself?**

The final, evidence-backed answer, narrower and more precise than "we added hierarchical context":

> **Kriya can preserve modification-relevant current source at member granularity under context-budget pressure, while deterministically controlling freshness, omission visibility, and cross-attempt reuse — without depending on a larger model context window, without replacing Graph RAG's retrieval algorithm, and without widening write authority.**

This is a mechanism-correctness claim, proven deterministically. It is explicitly **not** a claim that a specific real local model produces a better final artifact because of it — that second claim requires a live-model comparison, addressed in §7 (F8).

---

## 2. P0 Baseline (unchanged, re-cited)

P0 (`docs/assurance/CTX_001_P0_CURRENT_STATE.md`) traced the full executable context-flow, classified 20 failure modes (F1-F20), and demonstrated 6 correctness gaps (C1-C6) and 5 scalability gaps (S1-S5) with deterministic, zero-live-model probes. Its own conclusion: Graph RAG's retrieval foundation (vector+graph hybrid ranking) is sound and reusable — real embeddings later confirmed rank-1 stability across a 25× repository-breadth range (§6.7 of that document) — but exact-source granularity, freshness, relationship completeness, and named indexing/retrieval costs were real, narrow, fixable gaps. P0 explicitly did not recommend touching the vector store or Graph RAG's own algorithm. Nothing in this document revisits or re-derives P0's own findings; they are cited as established fact.

---

## 3. P1 Implementation Summary

| Package | Scope | Commit | Status |
|---|---|---|---|
| Architecture | Ownership decision (shared context-service layer, not `ContextOrchestrator` adoption), 9-step migration sequence | `c2bdd61` | Design accepted |
| WP1 | `relations.source_file` SQL index | `e4a238d` | Implemented, closed |
| WP2 | Python inheritance relation (`_parse_python` AST walk) | `e4a238d` | Implemented, closed |
| Package 2 (WP3-WP7) | `ContextItem`/`ContextPackage` extension, `CurrentSourceResolver`, member-level extraction (Java+Python), shared allocator+omission, owner-contract retirement, known-target floor | `5e1ff3f` | Implemented, closed |
| C2 investigation | Traced two real, grounded, already-existing evidence sources for automatic member-hint derivation | `f3ff29f` | Design accepted |
| C2 implementation | Deterministic resolver (vector-chunk-header + failure-location), wired into production `workflow.py`/`attempt.py` | `a71c498` | Implemented, closed |
| Package 3 / WP9 | `AttemptContext`-lifetime `SourceDerivationCache` (skeleton/signatures/member-exact/tokens) | `3aec0c8` | Implemented, closed |
| Package 3 / WP8 | STOPPED once (real hash/discovery mismatch found), then RESOLVED (shared discovery primitive) + `analyze()` reuse | `a908846` | Implemented, closed |

**Deliberately not attempted anywhere in P1** (per its own architecture's explicit DO-NOT-BUILD list, reaffirmed at every subsequent package): ANN/vector-store replacement, repository/module/package semantic summarization, general Graph RAG replacement, model-specific context handling, a persistent cross-run cache, a generic caching framework.

---

## 4. Correctness Replay (C1-C6)

Every scenario below was executed fresh in this pass — either via `spikes/ctx_001_p0/run_p1_final_replay.py` (new evidence, C2) or via a fresh re-execution of an already-existing deterministic test (cited by name, re-run in this pass, not assumed from a prior pass).

### C1 — owner-contract 24K/later-file loss

**Original:** `_brownfield_owner_contract_block`'s naive 24,000-combined-char cap silently dropped later target files entirely (`remaining <= 0: break`) and prefix-truncated an earlier file before reaching a relevant method.

**Final implementation:** the 24K cap is retired, not enlarged. Known-target source content flows through `build_known_target_context` (`context_budget.py`): relevance/floor-ordered, member-aware where a hint validates, and — critically — **every candidate produces either real content at some tier or an explicit, reason-labeled omission entry; never a silent drop.**

**Evidence:** `tests/test_context_budget.py::test_known_target_c1_repro_four_files_over_24k_all_accounted_for` (re-run, PASS) — reproduces the exact original scenario (4 target files, combined size far exceeding 24K) against the current allocator: every file is accounted for (content or omission), and `budget_exhausted` is the omission reason actually exercised under real pressure. `test_known_target_rendered_string_never_empty_when_evidence_omitted` (re-run, PASS) confirms the omission is visible in the rendered prompt itself, not only internal state.

**Status: CLOSED.**

### C2 — relevant member body loss under file-level degradation

**Original:** file-level-only skeletonization elided every method body once a file degraded below "full" tier — no way to keep one relevant method exact while its siblings degraded.

**Final implementation, and the one scenario this pass specifically strengthened:** member-level selection (Java via `extract_java_members`, Python via a new `ast`-based extractor) plus a two-source, fully-deterministic **automatic** member-hint resolver (vector-chunk-header parsing for the initial attempt, `Failure.file_locations` for retries) — no LLM call, no manual `member_hints` construction in production.

**Fresh evidence, this pass** (`spikes/ctx_001_p0/run_p1_final_replay.py::replay_c2_automatic`, re-run in this pass): using the P0 S2 large-file generator (2,500 lines, relevant method `near_end`, ~413 irrelevant padding-method bodies), the REAL chunker (`chunk_file_with_metadata_headers`) produces the real vector-chunk header; the REAL production parser (`parse_controlled_chunk_header_name`) extracts `"calculate_total"`; this flows — with zero manual intervention — through a real `run_attempt()` call into `build_known_target_context`. Result:

```
parsed_member_name_from_real_chunk: calculate_total
member_hint_paths_from_real_event: ['Large.py']
member_exact_tier_reached: True
relevant_body_present_in_prompt: True
padding_method_body_count_in_source: 413
padding_method_body_count_in_prompt: 0
```

The relevant member's real body is present in full; **zero** of the 413 irrelevant padding-method bodies leaked into the rendered prompt. This is the strongest available deterministic proof that the mechanism is real, automatic, and precise — not merely capable when hand-fed a member name.

**Corroborating evidence** (re-run, all PASS): `tests/test_context_budget.py::test_known_target_member_exact_retained_while_sibling_degrades`, `::test_known_target_two_relevant_members_far_apart_both_addressable` (far-apart, two-member case), `tests/test_workflow.py::test_c2_p0_large_file_production_reachable_member_retained_no_manual_hints`.

**Status: CLOSED.**

### C3 — stale workspace + fresh worktree coexistence

**Original:** `build_code_context`'s retry-time call sites read `ctx.workspace_path` (stale, pre-run) while `retry_prompts.py` separately read `state.all_files_written` from `ctx.worktree_path` (current) — the same file could appear twice, at two different contents, in one prompt.

**Final implementation:** `CurrentSourceResolver` centralizes the current-source decision (worktree-authoritative once it exists, unconditionally); all 4 retry-branch `build_code_context` call sites and both `build_known_target_context` call sites in `attempt.py` now resolve through it. A separate, real duplication (found while implementing Package 2, not previously flagged) — `retry_prompts.py`'s own `all_files_written` renderer re-showing a path `build_code_context` had already rendered — was also closed (`_graph_context_exclusion_set`).

**Fresh evidence** (re-run, PASS): `tests/test_workflow.py::test_targeted_retry_context_uses_current_worktree_revision_java`, `::test_targeted_retry_context_uses_current_worktree_revision_python` — workspace `VERSION_A`, worktree `VERSION_B`, real `run_attempt()` call: `existing_code_context` contains `VERSION_B`, `VERSION_A` is absent, in both languages. `::test_retry_worktree_version_b_member_selected_over_stale_workspace_version_a` — the same proof at the member-resolution level. `::test_graph_context_excludes_a_path_already_shown_via_retry_evidence` — the cross-mechanism duplication fix, real content appears exactly once.

**Status: CLOSED.**

### C4 — Python inheritance gap

**Original:** `_parse_python` never emitted `inherits`/`implements` relations; a real interface/implementation fixture produced zero relation rows.

**Final implementation:** `_parse_python` walks `ast.ClassDef.bases`, emitting `type="inherits"` rows (source-keyed by class name, matching Java's own established convention) for statically-nameable bases only — a dynamic/unsupported base never fabricates a relation.

**Fresh evidence** (re-run, PASS, `tests/test_dependency_graph.py`, 42/42 total): `test_python_inheritance_abc_interface_fixture` reproduces the exact P0 `InvoiceCalculator`/`StandardInvoiceCalculator` shape — the relation row now exists. `test_python_inheritance_stale_edge_removed_on_reindex` confirms re-indexing a file whose base class changed leaves no stale edge. `test_python_inheritance_dynamic_base_does_not_fabricate_edge` confirms the conservative-fallback requirement.

**Status: CLOSED.**

### C5 — low-scored known target could lose required evidence

**Original:** a known-target file's inclusion depended on Architect-list position and an unconditional 24K cap, not a real relevance signal — an adversarially low retrieval score, or simply being listed later, could silently drop a modification-critical file's exact source.

**Final implementation:** `KNOWN_TARGET_FLOOR = max(retrieval_score, 0.75)` inside the shared allocator — a priority floor, explicitly **not** unlimited inclusion; a genuinely budget-exhausted scenario still produces an explicit omission even for a known target.

**Fresh evidence** (re-run, PASS): `tests/test_context_budget.py::test_known_target_known_target_floor_beats_low_retrieval_score` (a `0.01`-scored known target still fully represented) and `::test_known_target_floor_does_not_grant_unlimited_inclusion` (an genuinely-exhausted budget still produces `budget_exhausted`, floor is not a bypass).

**Status: CLOSED** for the known-target case (the practically load-bearing one — a modification-critical file is, by definition, an Architect-designated known target). **RESIDUAL, disclosed:** a purely semantic Graph-RAG match that is *not* a known target has no floor at all — deliberately unchanged, since P0's own live-embedding validation (§6.7 of the P0 doc) showed rank-1 retrieval stability and did not justify touching the matched/related scoring mechanism itself. See F11 in §6.

### C6 — default pipeline lacked structured provenance/omission handling

**Original:** `ContextPackage`/omission tracking existed only on the opt-in, default-off `WorkflowController` path; the default `kriya generate` pipeline had no structured record of what was shown or omitted.

**Final implementation:** `build_code_context_package` (Graph-RAG matched/related flow) and `build_known_target_context` (known-target flow) both now produce a real `ContextPackage` — `ContextItem` per unit with real tier/revision/omitted_regions, and a reason-labeled `omitted` list — for the **default** pipeline, with zero `workflow_controller.enabled` dependency.

**Fresh evidence** (re-run, PASS): `tests/test_context_budget.py::test_build_code_context_package_populates_context_package_on_degradation`, `::test_build_code_context_package_records_source_unavailable`; `tests/test_workflow.py::test_known_target_package_recorded_as_run_evidence` — a real `context.known_target_package` `RunEvent` with `unit_count`/`tiers`/`omitted`/`package_hash`, produced by a plain `run_attempt()` call, no `WorkflowController` anywhere in the call chain.

**Status: CLOSED.**

---

## 5. Scalability Replay (S1-S4 + cold indexing)

### S1 — `relations.source_file` full scan

**Fresh evidence, this pass** (`run_p1_final_replay.py::replay_s1_query_plan`, re-run):

```
full_scan_before: True   (plan contains "SCAN relations")
full_scan_after:  False
index_used_after: True   ("SEARCH relations USING INDEX idx_relations_source_file")
indexes_present:  ['idx_relations_source', 'idx_relations_target', 'idx_relations_source_file']
```

The real `clear_file()` DELETE statement (verbatim, not a simplified stand-in) resolves via `MULTI-INDEX OR` → `SEARCH ... USING INDEX idx_relations_source_file` against the current, unmodified `DependencyGraph`. **Status: CLOSED.**

### S2 — repeated `RepositoryAnalyzer.analyze()`

**Fresh evidence** (re-run, PASS, `tests/test_analyzer_cache.py`, 16/16): `test_repeated_unchanged_analyze_decreases_recomputation_via_counters` — zero new cache misses across 4 repeated unchanged calls, 4 new hits. `test_modification_invalidates`/`test_relevant_addition_invalidates`/`test_deletion_invalidates`/`test_rename_invalidates`/`test_manifest_change_invalidates`/`test_directory_structure_change_invalidates`/`test_nested_gitignore_is_honored` — every relevant-change category correctly invalidates. `test_gitignored_addition_is_served_from_cache_not_recomputed_incorrectly` — a change that cannot affect `RepositoryModel` (a gitignored addition) correctly does **not** invalidate, and is proven to be a real cache hit, not a coincidentally-equal fresh recompute. `test_non_git_workspace_always_recomputes_and_still_works` — non-git workspaces remain fully supported, cache is a structural no-op there. **Status: CLOSED** for git-tracked workspaces; **intentionally disabled** (not a gap) for non-git workspaces.

### S3 — repeated context/source derivations across attempts

**Fresh evidence** (re-run, PASS, `tests/test_context_budget.py`): `test_cross_attempt_file_a_reused_file_b_recomputed` — A's own derivation reused (a new hit), B's mutated content recomputed (a new miss), `"VERSION_1"` absent from the result (no stale B). `test_build_code_context_package_cached_equals_uncached`/`test_known_target_context_cached_equals_uncached_member_exact` — cached and uncached output are byte-identical. `tests/test_workflow.py::test_source_cache_reused_across_two_real_run_attempt_calls` — the SAME real, production-shaped scenario through two actual `run_attempt()` calls sharing one `AttemptContext`. **Status: CLOSED.**

### S4 — vector retrieval scans all chunks

`kriya/memory/vector.py` has a **zero-line diff** across the entire P1 effort (verified: `git diff 91aec04 HEAD -- kriya/memory/vector.py` is empty). `LocalVectorStore.query()` (`vector.py:374-425`) remains an unconditional full-table brute-force cosine scan, exactly as P0 documented. **Not optimized, per explicit instruction. Status: RESIDUAL, unchanged, disclosed — not a P1 regression, not a P1 claim.**

### Cold indexing (the O(N²) contributor)

**Fresh evidence, this pass** (`run_p1_final_replay.py::replay_cold_indexing_structural`, re-run), a structural (not wall-clock) comparison over 300 sequential file indexes:

```
full_table_scan_delete_calls_before: 300 / 300   (100% of clear_file() calls full-scan)
full_table_scan_delete_calls_after:    0 / 300   (0%)
```

This directly demonstrates the O(N²) structural contributor (N full-table scans, one growing table) is eliminated — every one of 300 sequential `clear_file()` calls against the real, current `DependencyGraph` resolves via the index, none via a full scan. No wall-clock claim is made; the query-plan-based operation count is the primary evidence, per instruction.

---

## 6. Final Finding Matrix (F1-F20)

| # | P0 finding | P0 status | Final P1 disposition | Evidence |
|---|---|---|---|---|
| F1 | Relevant file omitted by prioritization/budget | DEMONSTRATED | **CLOSED** | §4 C1 |
| F2 | Relevant member outside retained portion of a large file | DEMONSTRATED | **CLOSED** | §4 C2 |
| F3 | Two relevant members far apart in one large file | DEMONSTRATED | **CLOSED** | §4 C2 (`test_known_target_two_relevant_members_far_apart_both_addressable`) |
| F4 | Implementation spans packages (unqualified names) | PARTIAL/HANDLED | **RESIDUAL_ACCEPTED** — unchanged; P1 never redesigned symbol identity, by explicit instruction at every package | §11 architecture doc, C4's own "no identity redesign" boundary |
| F5 | Interface/implementation span modules | DEMONSTRATED (Python) / HANDLED (Java) | **CLOSED** (Python gap); Java unchanged | §4 C4 |
| F6 | Change requires repository-wide caller knowledge | PARTIAL/HANDLED | **RESIDUAL_ACCEPTED** — unchanged, Graph RAG's own algorithm untouched by design | P0's own P1 recommendation explicitly excluded Graph RAG replacement |
| F7 | Planner sees symbol, Developer lacks exact implementation | DEMONSTRATED | **CLOSED** — direct consequence of F2, closed with it | §4 C2 |
| F8 | Lossy summary omits modification-critical invariant | DEMONSTRATED (mechanism), POSSIBLE (real impact) | **Mechanism CLOSED; real-model-impact question DEFERRED_WITH_REASON** | §7 |
| F9 | Context becomes stale after earlier mutation | DEMONSTRATED | **CLOSED** | §4 C3 |
| F10 | Duplicate/irrelevant context displaces required context | DEMONSTRATED | **CLOSED** | §4 C3 (dedup), C5 (floor) |
| F11 | Task succeeds at large budget, evidence disappears at small budget | DEMONSTRATED | **MITIGATED** — closed for known-target files (the practically load-bearing case); unchanged for a non-known-target Graph-RAG match | §4 C5 residual note |
| F12 | Chunk boundary splits semantic unit | PREVENTED | **CLOSED** (unchanged, reconfirmed, no regression) | structural tokenize/brace-depth skeletonizer untouched |
| F13 | Unchanged files repeatedly parsed/read across retries | DEMONSTRATED (`build_code_context`) / PREVENTED (`index_repository`) | **CLOSED** | §5 S3 |
| F14 | Repository-wide work repeated for task-local operation | DEMONSTRATED | **CLOSED** (git workspaces); **RESIDUAL_ACCEPTED** (non-git, intentional) | §5 S2 |
| F15 | Tokenization repeatedly recomputed for unchanged content | DEMONSTRATED | **CLOSED** — folded into the same `SourceDerivationCache` entry, not a second cache | §5 S3 |
| F16 | Graph/index rebuilt unnecessarily | PREVENTED / N/A | **CLOSED** (unchanged, reconfirmed) | mtime/hash incremental path untouched |
| F17 | Small mutation triggers disproportionate re-analysis | PREVENTED (`index_repository`) / DEMONSTRATED (`analyze`) | **CLOSED** | §5 S2 |
| F18 | Prompt/context grows across retries without bounded growth | PARTIAL | **MITIGATED** — the specific named waste (F13's redundant identical recompute) is closed; retry-prompt boundedness itself was already fine pre-P1 | §5 S3 |
| F19 | Repository breadth increases prep cost disproportionately | DEMONSTRATED | **CLOSED** for the O(N²) SQL contributor (the dominant, named root cause); **RESIDUAL_ACCEPTED** for `query_hybrid`'s own linear-in-chunk-count scan (S4, untouched by design); `build_code_context`'s own flatness (already a strength) unaffected | §5 S1, cold indexing, S4 |
| F20 | Large-file size increases processing cost disproportionately | PREVENTED (roughly linear) | **CLOSED** (unchanged, extended by member-exact extraction, which is also linear/structural, not quadratic) | member extractors are single-pass AST/brace-depth scans |

**Counts:** CLOSED = 15 (F1,F2,F3,F5,F7,F9,F10,F12,F13,F15,F16,F17,F19[partial-closed component],F20, and F8's mechanism-half) · MITIGATED = 2 (F11, F18) · RESIDUAL_ACCEPTED = 3 (F4, F6, F14-non-git) + S4/F19's vector-scan component · DEFERRED_WITH_REASON = 1 (F8's real-model-impact half) · NOT_APPLICABLE_AFTER_P1 = 0.

`UNOWNED_FINDINGS = 0` — every F1-F20 item has an explicit disposition above; none silently omitted.

---

## 7. F8 Decision

**F8_DECISION: `OPTIONAL_ADDITIONAL_EVIDENCE`** (not `REQUIRED_FOR_CTX001_CLOSURE`, not `NO_LONGER_JUSTIFIED`).

**Rationale.** F8 has two distinct halves, and this pass's deterministic replay resolves exactly one of them:

1. **Mechanism half — "can Kriya retain a modification-critical invariant expressed only in a method body, at member granularity, instead of losing it to file-level skeletonization?"** This pass's C2 replay (§4) answers this deterministically and conclusively: yes, automatically, with zero manual intervention, proven against a real 413-padding-method adversarial fixture. This half is CLOSED — not by argument, by direct execution.

2. **Real-model-impact half — "does a real local model, under real context-budget pressure, actually produce a *worse* result when an invariant is lost to file-level degradation, and a *better* one when member-exact context is available?"** No deterministic probe can answer this — it is, by definition, a question about model behavior, not mechanism correctness. This pass makes no claim about it, one way or the other, and could not have (no model was run).

**Why OPTIONAL rather than REQUIRED:** CTX-001's own thesis (§1) is a mechanism-correctness claim, not a model-quality claim. Every C1-C6/S1-S4 finding this document closes is closed on mechanism grounds, and every one of them is real, load-bearing, independent of what any specific model does with the improved context. The closure recommendation in §9 does not depend on F8. F8 remains valuable as **additional, corroborating evidence** for a different, narrower claim ("member-aware context measurably helps a real model on a real task") — worth running before using P1's member-aware context as a headline capability claim in a demo or presentation, but not a precondition for declaring CTX-001's own mechanism-correctness work closed.

**Why not NO_LONGER_JUSTIFIED:** the comparison remains genuinely informative and cheap to run once desired — the mechanism exists now (it didn't before P1), so the comparison is newly possible, not newly pointless.

**If the user wants to run it later:** a minimal live-validation script would need: (1) the P0/C2 large-file fixture (`spikes/ctx_001_p0/fixtures.py::build_large_file`) with a real, verifiable invariant embedded only in the relevant method's body (not its signature); (2) two runs of the SAME goal/model/config — one with the pre-P1 file-level-only code path (reachable today only by NOT supplying a member hint — i.e., the existing, still-present conservative fallback), one with the real, automatic member-hint path this document just proved reachable; (3) a deterministic, non-model-graded check of whether the generated output respects the embedded invariant. **This document does not build that script** — building it is new work, out of this task's own "no new production functionality" and "do not execute a live model" scope. It is named here only as what "REQUIRED" would look like if the user later decides F8 should be run.

`USER_LIVE_VALIDATION_REQUIRED: NO` (not required for CTX-001 closure)
`USER_LIVE_VALIDATION_COMMAND: N/A — not prepared in this pass, since F8 is OPTIONAL, not REQUIRED`

---

## 8. Architectural Invariants (reconfirmed)

- **`LLM proposes; Kriya governs`**: unchanged. Every context-selection decision (member hints, floors, tiers, omissions) is deterministic, code-driven, and never model-invoked.
- **Context selection never widens write authority**: `AUTHORITY_EXPANSION_PATHS = 0`. Proven structurally (`tests/test_workflow.py::test_context_budget_functions_carry_no_write_authority_parameters` — no new function accepts `WriteScopeMode`/`allowed_write_relpaths`/`AuthorizedSemanticRegion`) and at runtime (`::test_member_hint_generation_does_not_expand_write_authority`, `::test_known_target_priority_does_not_alter_ctx_write_scope`, `::test_retry_failure_location_does_not_authorize_an_unimplicated_file`) — all re-run, PASS.
- **Member identity never activates CORR-018**: `CORR018_COUPLING_PATHS = 0`. Grepped the full P1 diff for `semantic_region_authority`/`AuthorizedSemanticRegion`/`find_unauthorized_semantic_changes` at every package boundary (Package 2, C2, WP9, WP8) — zero matches each time. `autonomy.semantic_region_enforcement_required` never read or written anywhere in P1.
- **Worktree remains authoritative**: `STALE_CONTEXT_ACCEPTANCE_PATHS = 0`. `CurrentSourceResolver`'s own invariant (§4 C3), unconditional once a worktree exists, is the single decision point every real content-producing call site routes through — no call site independently re-derives "which root."
- **Model capability contract unchanged**: no P1 change touches `kriya/config/config.py`'s `LLMConfig`, model selection, or fallback-chain logic.
- **Recovery semantics unchanged**: `API_CONTRACT_RECOVERY`'s own deterministic-restoration path was explicitly left untouched by the C2 retry wiring (gated `if not use_api_contract_recovery`); `RepairContract`/coordinated-repair generation is untouched by any P1 package.
- **No model-specific context path**: `MODEL_SPECIFIC_CONTEXT_PATHS = 0`. Every P1 mechanism (member extraction, floors, caching, discovery alignment) operates identically regardless of which model is configured — none reads `cfg.llm.model` to branch behavior.
- **Unsupported member extraction falls back conservatively**: proven at every layer — `member_boundaries_for` returns `None` (not `[]`) for an unsupported language; `build_known_target_context` degrades to `REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION` + whole-file handling; the member-hint resolvers return `[]` for a language with no extractor, a malformed header, or a stale/renamed/removed member — never a fabricated one.
- **Stale evidence fails closed**: a `known_revisions`/`content_cache` mismatch is never trusted silently (`CurrentSourceResolver.resolve()` always re-verifies against real, freshly-read content); a member hint that no longer resolves against current structure produces an explicit omission, never a stale/fabricated one (proven end-to-end: `test_member_hint_renamed_before_retry_cached_boundaries_do_not_survive`, `test_source_cache_does_not_interfere_with_member_rename_fallback`, both re-run, PASS).

---

## 9. Performance Invariants (honest statement, no more than demonstrated)

- Repository analysis (`RepositoryAnalyzer.analyze()`) is now safely reusable **for an unchanged canonical Git repository state** — proven via a real, aligned discovery primitive and a content-hash-keyed cache (§5 S2). Not claimed for non-Git workspaces (intentionally disabled there).
- Attempt-lifetime deterministic source derivations (skeleton, signatures, member-exact body, sibling signatures, token counts) are reusable across retries within one attempt — proven (§5 S3). Not claimed across separate `generate` invocations (no persistent cache exists).
- The dependency graph's `clear_file()` source-file lookup is indexed — the specific, named O(N²) contributor is eliminated (§5 S1, cold indexing). Not claimed for any other query in `graph.py`.
- Member-aware context reduces the need to retain entire large files verbatim — proven with a concrete 413-body adversarial fixture (§4 C2), not claimed as a universal token-savings percentage (no such general claim is made anywhere in this document).
- Vector retrieval (`query_hybrid`) remains linear/brute-force over stored chunks — unchanged, not optimized, not claimed otherwise (§5 S4).
- Non-Git `analyze()` caching is intentionally disabled, not an oversight — a `None` cache key always recomputes, matching pre-P1 behavior exactly.
- No repository/module/package summarization was introduced anywhere in P1.

---

## 10. RECV-002 and MODEL-001

**RECV-002: `NEEDS_EVIDENCE`, unchanged.** CTX-001's own evidence (context correctness/scalability) is not recovery-execution evidence and is not used here to manufacture a RECV-002 closure claim.

**MODEL-001: `CLOSED`, unchanged.** No model campaign was re-run in this pass or anywhere in CTX-001 P1.

---

## 11. Closure Recommendation

**CTX001_STATUS: CLOSED.**

Every C1-C6 correctness finding and every named, in-scope S1-S3/cold-indexing scalability finding is CLOSED on fresh, deterministic, re-executed evidence — not inferred from unit-test presence alone (per this task's own ASSURANCE RULE), and not inferred from a prior pass's claim without re-running it. The two MITIGATED findings (F11, F18) and the disclosed residuals (F4, F6, F14-non-git, S4/F19's vector-scan component, F8's real-model-impact half) are each explained, each intentional, and each traceable to an explicit prior instruction that scoped them out of P1 — none is a silently-discovered gap this pass is papering over. `PRODUCTION_CHANGES = 0` in this pass; no correctness defect was found that would require setting `CTX001_STATUS = NOT_CLOSED`.

The claim this evidence supports, for carrying forward into architecture/presentation material, is exactly the one stated in §1 — a mechanism-correctness claim about deterministic freshness/omission/reuse control at member granularity, not a claim about real-model output quality (which remains F8's own, still-open, optional question).
