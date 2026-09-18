# CTX-001 P1 — Architecture: Exact Context & Incremental Repository Intelligence

**Status:** ARCHITECTURE/DESIGN ONLY. No production code changed. No live models/embeddings run. No full pytest run.

**Baseline:** `origin/milestone-decomposition == 91aec04`. CTX-001-P0 CLOSED and frozen; this document extends it, does not re-derive it.

**Method:** every design decision below is grounded in a direct read of the cited `file:line` in the current, unmodified codebase — never inferred from a class or function name. Where two candidate designs were plausible, the discriminator is stated explicitly (§1).

---

## 1. Executive Decision

**Primary ownership question, resolved: the default pipeline adopts neither `ContextOrchestrator` nor its full call contract. It consumes a new, shared, lower-level context service that `ContextOrchestrator` itself is refactored to sit on top of, as a peer, milestone-scoped consumer — not an owner.**

The discriminator is in `ContextOrchestrator.build()`'s own signature (`context_orchestrator.py:147-163`): `milestone: Optional[MilestoneV2]`, `control_state: ControlState`, `contract_entries`, `artifact_entries`, `carried_forward_criteria`. These are `WorkflowController`/milestone-decomposition concepts, not generic context concepts — its only production caller is `workflow_controller.py:_build_context` (line 6733), itself only reachable when `workflow_controller.enabled=True` (default `False`, `default_config.yaml:175`). Adopting `ContextOrchestrator` into the default `run_generation_workflow` path would require either (a) fabricating milestone/control-state objects for a plain `kriya generate` call that has neither, or (b) widening `ContextOrchestrator.build()`'s contract until it no longer means what its own docstring says it means. Both violate the task's own instruction: "We must avoid coupling default generation to milestone orchestration merely to reuse context primitives."

By contrast, `ContextItem`, `ContextPackage`, `make_context_item`, `make_omitted_entry`, `build_context_package` (`context_package.py`), and `FileProjection`/`project_implementation_source` (`context_projection.py`) carry **zero** milestone semantics — they are plain data shapes and pure projection functions. These become the shared layer. `ContextOrchestrator` keeps its milestone-specific `select_context_strategy`/spec-slice/contract-and-artifact-entry logic, but its own `_rank_and_trim` (`context_orchestrator.py:121-143`) is retired in favor of calling the shared budget/omission resolver described in §4-§8, so there is exactly one trimming/omission implementation in the codebase, not two independently-maintained ones.

This resolves §1's evidentiary question precisely: `GENERIC_CONTEXT_RESPONSIBILITY` = the context-unit shape, current-source resolution, member-level projection, budget/degradation, provenance/omission recording, and rendering. `WORKFLOW_CONTROLLER_RESPONSIBILITY` = deciding a retrieval *strategy* from `(kind, weight)`, milestone spec slices, established-file carryover across milestones, and contract/artifact registry summaries — all of which *feed* the shared layer as inputs, never re-implement its mechanics.

---

## 2. Existing Ownership Map

| Component | file:line | What it actually owns today |
|---|---|---|
| `ContextOrchestrator` | `context_orchestrator.py:146-215` | Milestone-decomposition retrieval *strategy selection* + candidate-to-`ContextPackage` assembly. Only caller: `WorkflowController` (opt-in). |
| `ContextPackage`/`ContextItem` | `context_package.py` | Generic, milestone-agnostic structured context snapshot with real provenance/trust/hash + non-silent omission list. Zero milestone coupling in the type itself — the coupling lives entirely in `ContextOrchestrator`, the one caller that populates it today. |
| `WorkflowController` | `workflow_controller.py` | Milestone/subtask orchestration, `_build_context` (context-building glue for that path only), `ContextOrchestrator` construction. Opt-in (`workflow_controller.enabled=False` default). |
| `run_generation_workflow` | `workflow.py:723` | The default pipeline. Builds `convention_prompt` (a bare string) directly via `build_code_context` + string concatenation — no `ContextPackage` involved anywhere in this path today. |
| `build_code_context` | `context_budget.py:634-743` | File-level skeletonization-tier degradation over a fixed `matched_files`/`related_files` list, into ONE rendered string. Called from `workflow.py` (attempt 1) and `attempt.py` (every retry, rebuilt from scratch each time). |
| `_brownfield_owner_contract_block` | `attempt.py:119-149` | Attempt-1-only, `known_target_files`-only, naive 24,000-combined-char prefix cap in Architect-list order, injected into **both** `task_desc` and `active_code_context`. |
| `RetryPackage`/`build_retry_package` | `retry_package.py` | Retry-only, but already does what A3 needs: 70/30 target/reference budget split, per-file allocation, explicit `omitted_files` list with reason, **head+tail (not prefix-only) truncation with an explicit marker**, real per-file SHA-256 revision via `content_revision`/`known_revisions`. |
| `FileProjection`/`project_implementation_source` | `context_projection.py:18-79` | The actual bounded, revision-aware, non-silent-truncation projector `RetryPackage` calls per file. `ProjectionLevel` enum has 4 members; only `FULL`/`IMPLEMENTATION_EXCERPT` are ever produced anywhere in the codebase (confirmed by grep — `SIGNATURES`/`SUMMARY` are **dead, unwired**). |
| `DependencyGraph` | `graph.py` | Symbol/relation SQLite store. `_parse_python` (505-561) extracts imports/calls via `ast`, **never** class-base-list (`ast.ClassDef.bases`) — confirmed absence in P0 (C4). |
| `RepositoryAnalyzer.analyze` | `analyzer.py:326-405` | Full, uncached `os.walk` + facts extraction (`RepositoryModel`), run on every `generate` call regardless of change (P0 S2). |
| `AttemptContext` | `attempt.py:393-567` | The retry loop's read-only closure: `workspace_path`, `worktree_path`, `matched_files`, `related_files`, `architect_files`/`expected_files_upfront`, `skills_prompt`, plus **already-separate** authority fields (`write_scope_mode`, `authorized_semantic_regions`) — confirms the authority/context field separation this design must preserve (§14) already exists structurally. |
| Retry context builder | `retry_prompts.py` (`_build_targeted_retry_prompt`/`_build_full_set_retry_prompt`) | Reads `state.all_files_written` fresh from `ctx.worktree_path` — already correctly worktree-sourced, unlike `build_code_context`'s own retry-time calls (`attempt.py:3862`,`4199`), which still pass `ctx.workspace_path` (the F9 mechanism, P0 §5). |
| `dependency_invalidation.py` | `dependency_invalidation.py` (37 lines) | Real, small, generic `dependent_closure`/`invalidate_validated_revisions` primitive, already reused by `_retry_package_for_attempt` (`attempt.py:266`). Scoped to `state.validated_file_revisions`, not to `vector_index.db`/`dependency_graph.db`. |
| `worktree.py::create_git_worktree` | `worktree.py:297-399`, called `workflow.py:2315` | Runs **once**, before the retry loop, after attempt-1's Graph RAG retrieval (`matched_files`/`graph_rag_context` built at `workflow.py:1649/1657`, strictly before line 2315). On success, always returns a real, distinct sandbox path (`.kriya/worktree` or a scoped snapshot for a nested-repo workspace) — on failure it raises and aborts the run; there is no silent `worktree_path == workspace_path` fallback at this call site. |

---

## 3. Generic Context vs. WorkflowController Responsibilities

| Responsibility | Owner |
|---|---|
| Context-unit shape (file/type/member identity, tier, hash/revision, provenance, omission reason) | **GENERIC** — `ContextItem`, extended (§4) |
| Current-source-of-truth resolution (workspace vs. worktree) | **GENERIC** — new `CurrentSourceResolver` (§7) |
| Member-level exact-source selection | **GENERIC** — new `ContextUnitSelector`, per-language strategy (§6) |
| Budget allocation + degradation ordering | **GENERIC** — extends `build_code_context`'s existing per-file scoring, generalized to per-unit (§4-5) |
| Omission recording | **GENERIC** — `make_omitted_entry`, extended (§8) |
| Rendering units into one prompt string | **GENERIC** — one renderer, all callers converge on it (§5) |
| Retrieval strategy selection from `(ChangeKind, ExecutionWeight)` | **WORKFLOW_CONTROLLER** — `select_context_strategy` stays where it is |
| Milestone spec slice, established-file carryover across milestones, contract/artifact registry summaries | **WORKFLOW_CONTROLLER** — these are genuinely milestone-shaped inputs, not generic context mechanics |
| Graph RAG retrieval itself (`query_hybrid`, `get_neighborhood`) | **GENERIC**, already shared (`workflow.py`'s retrieval stage is the one and only production retrieval call site; `WorkflowController` consumes its output as `raw_rag_context`, per `context_orchestrator.py`'s own "REUSE BOUNDARY" docstring, unchanged by P1) |
| Write authority (`WriteScopeMode`, `AuthorizedSemanticRegion`) | **NEITHER** — orthogonal, see §14 |

---

## 4. Target Context Data Model

**`ContextItem` is EXTENDED, not replaced** (per the task's own instruction, and per §2's finding that it already carries zero milestone coupling). New fields, all optional with backward-compatible defaults so every existing construction site (`context_orchestrator.py`, tests) keeps working unchanged:

```python
@dataclass(frozen=True)
class ContextItem:
    # --- existing fields, unchanged ---
    path: str
    content: str
    reason: str
    source_type: str
    trust_level: str
    score: Optional[float] = None
    content_hash: str = ""

    # --- new, all optional/defaulted: additive, not breaking ---
    type_id: Optional[str] = None          # e.g. "StandardInvoiceCalculator" - None for a whole-file unit
    member_id: Optional[str] = None        # e.g. "calculate_total" - None for a type- or file-level unit
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    tier: str = "full"                     # reuses context_budget.py's existing "full"/"skeleton"/"signatures" vocabulary (§4a)
    is_exact: bool = True                  # False whenever tier != "full" OR the unit is a bounded excerpt (head+tail projection)
    revision: str = ""                     # content_revision()/SHA-256 of the file this unit was cut from, at read time
    omitted_regions: bool = False          # True when this unit's own content is itself a bounded excerpt (project_implementation_source's own flag)
```

**`tier` reconciliation:** `context_budget.py`'s `_TIER_STEPS = ("full", "skeleton", "signatures")` (bare strings) and `context_projection.py`'s `ProjectionLevel` enum (`FULL`, `IMPLEMENTATION_EXCERPT`, and the dead `SIGNATURES`/`SUMMARY`) are two independently-evolved, partially-overlapping vocabularies. Since `SIGNATURES`/`SUMMARY` are confirmed dead (zero producers anywhere in the codebase), `tier` on `ContextItem` standardizes on `context_budget.py`'s three-value string vocabulary plus one new value, `"member_exact"` (§6), and `ProjectionLevel` is retired in favor of it (§17) — a compatibility mapping (`FULL→"full"`, `IMPLEMENTATION_EXCERPT→"skeleton"` with `omitted_regions=True` distinguishing a bounded excerpt from a structural skeleton) keeps `FileProjection`'s existing callers (`RetryPackage`) working through the transition.

**`ContextPackage` is unchanged in shape** — `relevant_files: Tuple[ContextItem, ...]` already holds whatever `ContextItem` becomes; no `ContextPackage`-level schema change is required, only what populates it.

**`make_context_item` is EXTENDED** (new optional kwargs, same "one real constructor path, hash always computed from real content" discipline) — never hand-constructed elsewhere.

---

## 5. Target Executable Flow

```
goal + repository state
  → retrieval candidates (UNCHANGED: query_hybrid + get_neighborhood, workflow.py's existing retrieval stage — P0 confirmed rank-1 stability, no change justified, §5 of P0)
  → ContextUnitSelector (NEW, §6): for each candidate path, decide file-level vs. member-level addressing
  → CurrentSourceResolver (NEW, §7): resolve exact current content for each unit from the correct root
  → ContextBudgetAllocator (EXTENDS build_code_context's existing per-item scoring, generalized from per-file to per-unit, §4-5)
  → omission recording (EXTENDS make_omitted_entry, §8)
  → ContextPackage (UNCHANGED shape, §4)
  → ONE renderer → one prompt string, consumed identically by Planner/Architect/Developer (§5a)
```

**§5a — the rendering convergence point, explicitly, because this is the highest risk to `DUPLICATE_CONTEXT_SUBSYSTEMS_PROPOSED = 0`.** Today, `build_code_context`'s return value becomes `convention_prompt` (`workflow.py:1663`), consumed by Planner (`1744`), Architect (`1848`), and Developer's `ctx.skills_prompt`-derived `active_code_context`. If P1 gave only the Developer path structured units while Planner/Architect kept consuming the legacy string, that would be a **fifth** parallel representation, not a removal of any existing one. **Design requirement, stated as a hard constraint:** `build_code_context` is EXTENDED to become the one unit-producing-and-rendering path for the default pipeline — it internally builds `ContextItem`s (via the new selector/resolver/allocator), then renders them to the identical string shape it produces today (`"=== Codebase Semantic Reference Context ==="`/`"=== Bounded Neighborhood Dependency Context ==="` blocks) for Planner/Architect/Developer alike. No caller-visible signature change; the difference is entirely internal (member-aware selection instead of whole-file-only, explicit omission tracking instead of silent degradation). `RetryPackage`/`_brownfield_owner_contract_block` converge onto the SAME `ContextItem` production path (§9), differing only in which candidates they select and which budget they're allocated, not in how a unit is produced or rendered.

---

## 6. Member-Level Source Design (A1)

**Language strategy, explicit, conservative:**

| Language | Member boundary source | Body-extent source | A1 status |
|---|---|---|---|
| Java | `extract_java_members` (`java_members.py`) — already returns precise line spans per constructor/method | Same (already end-to-end) | **FULL member-exact support** |
| Python | `ast.parse` + `ast.FunctionDef`/`AsyncFunctionDef`/`ClassDef` nodes, using `end_lineno`/`end_col_offset` (Python 3.8+, already the `ast` module `analyzer.py` and `graph.py::_parse_python` both already import and use for other purposes) | Same `ast` walk — **not** `context_budget.py::_python_declaration_ranges`, which returns only the *declaration header* span (`end_line, colon_column, kind, indent`, keyed by first-decorator line — confirmed by reading `_python_declaration_ranges`, `context_budget.py:133-188`), sufficient for the skeletonizer's own header-preservation need but **not** for A1's "retain this member's full body" requirement | **NEW, small extension required** — a `member_id → (start_line, end_line)` extraction function using `ast`, parallel to but distinct from `_python_declaration_ranges` |
| Any other language (JS/TS/Go/Rust/C#/C/C++/Ruby/Kotlin/Swift/etc.) | None exists today | None | **Conservative fallback: file-level tiers exactly as today.** No member claim is made; `type_id`/`member_id` stay `None` on every `ContextItem` for these languages, `tier` is drawn only from `{"full","skeleton","signatures"}`, never `"member_exact"`. This is a **no behavior change** for these languages, stated explicitly rather than silently implied. |

**Required capability, restated precisely as a data-flow rule:** for a file where the `ContextUnitSelector` determines exactly one member is relevant (from `DependencyGraph.get_symbols_for_file`/`get_neighborhood`'s own already-real symbol identity, or from `known_target_files` + a caller-supplied member hint), it emits:
- one `ContextItem` for that member, `tier="member_exact"`, `is_exact=True`, full body content, `member_id` set;
- for every OTHER member in the same file, either omit them entirely (cheapest) or emit signature-only `ContextItem`s (`tier="signatures"`) for cross-member context — decided by the same budget allocator, not hardcoded;
- declaration boundaries remain structural (reuses `extract_java_members`/the new Python `ast` walk — never a byte-offset or line-count heuristic).

This directly closes P0's C2/F2/F3: a file no longer has to degrade uniformly — the allocator can keep one member `full`/`member_exact` while every sibling in the same file degrades independently.

---

## 7. Freshness / Source-of-Truth Design (A2)

**Deterministic rule (the "current-source invariant" the acceptance criteria require to be explicit):**

> The current-source root for a run is whatever `create_git_worktree()` (`worktree.py:297`) returned for that run. Before that call completes, `workspace_path` is current (nothing has been written yet — this is exactly the window attempt-1's Graph RAG retrieval runs in, `workflow.py:1649/1657`, strictly before worktree creation at `workflow.py:2315`). After that call completes, the returned root (`worktree_path`, stored on `AttemptContext.worktree_path`) is current for the remainder of the run, without exception, for every context consumer. `workspace_path` must never be re-read for file *content* after this point.

This is stated as "whatever `create_git_worktree` returned," not "worktree_path ≠ workspace_path," because `create_git_worktree` has one failure mode (an exception, which aborts the whole run — `workflow.py:2312-2320`) and no silent same-path fallback at this call site; the invariant holds unconditionally for every run that reaches attempt 1.

**Concrete fix this closes (C3/F9, P0 §5):** `build_code_context`'s retry-time call sites (`attempt.py:3862`, `attempt.py:4199`) currently pass `ctx.workspace_path`. Under this design they pass `ctx.worktree_path` instead — the `CurrentSourceResolver`'s one job is to centralize this choice so no future call site can independently re-derive the wrong root. `retry_prompts.py`'s `_build_targeted_retry_prompt`/`_build_full_set_retry_prompt` already read from `ctx.worktree_path` correctly and need no change — they become the reference implementation the resolver generalizes.

**Revision representation:** every `ContextItem` carries `revision` (SHA-256 via the existing `content_revision()`, `edit_safety.py`, already used by `RetryPackage`'s `known_revisions` threading and `state.validated_file_revisions`). The `CurrentSourceResolver` accepts an optional `known_revisions: Dict[str, str]` (reusing, not duplicating, `state.validated_file_revisions`'s existing role) so a caller that already hashed a file moments earlier in the same attempt doesn't pay a second hash.

**Invariant enforcement ("one prompt must never contain conflicting revisions of the same source unit without explicit intentional provenance"):** the `ContextUnitSelector` deduplicates by `path` before unit production — if a path appears in both the Graph-RAG-matched candidate set and the `state.all_files_written`/known-target set, exactly ONE `ContextItem` is produced for it, sourced from the current-source root (§7's rule), never two. This is the direct fix for F9/F10's "two different contents of the same file in one prompt" — deduplication happens at the unit-production layer, before rendering, not after.

---

## 8. Provenance + Omission Design (A4)

**`ContextItem`/`ContextPackage`/`make_omitted_entry` become the shared representation for the default pipeline too** — not a new type. Every material omission/degradation this design introduces is recorded via `make_omitted_entry(path, rank, reason, estimated_tokens)`, extended with an optional `member_id` (mirrors §4's `ContextItem` extension) so a member-scoped omission is distinguishable from a whole-file omission.

**Omission reason vocabulary (extends, does not replace, today's implicit reasons):**

| Reason | When produced | Existing precedent |
|---|---|---|
| `budget_exhausted` | allocator ran out of budget before this unit fit at any tier | `_rank_and_trim`'s existing `"exceeds context token budget"` (`context_orchestrator.py:141`) |
| `body_elided` | unit degraded to `skeleton`/`signatures` tier — body dropped, not the whole unit | new — today this is silent (P0 F8) |
| `unsupported_structural_extraction` | language has no member extractor (§6's fallback table) — file-level only, explicitly labeled as such | new |
| `stale_revision_rejected` | a candidate's cached/matched revision no longer matches the current-source root's real content, and re-resolving it was out of budget | new — see §12 for when this can occur under caching |
| `lower_relevance` | this unit's score/priority lost to another unit under budget pressure | today's implicit categorical/score-based degradation (`build_code_context`'s existing tier-degradation loop) |
| `source_unavailable` | file no longer exists / unreadable at the current-source root | today's silent `except Exception: logger.debug(...)` swallow in `build_code_context` (`context_budget.py:642-643`) |

**Internal evidence vs. prompt rendering, kept distinct (per the task's explicit instruction not to necessarily expose every internal omission to the LLM):** the full `omitted` list lives on `ContextPackage`/the run trace (`TraceLogger`, already the existing sink for this class of evidence) for audit/debugging. The RENDERED prompt string gets only a bounded summary line when omissions occurred (e.g., "N additional file(s)/member(s) omitted from this context — see run trace"), mirroring `RetryPackage.render_context()`'s own existing `"=== Additional files omitted from retry evidence budget ==="` block (`retry_package.py:66-70`) — that block is the pattern to generalize, not a new invention.

---

## 9. Owner-Contract Migration (A3)

**Disposition: `_brownfield_owner_contract_block` becomes a compatibility-preserving thin wrapper that internally delegates to the shared unit-production path, retiring its own bespoke truncation logic.** It does not "disappear" outright in one step, because it also writes into `task_desc` (the instruction stream), not only `active_code_context` — a real, separate responsibility this design must not lose.

**The split, stated explicitly (closing the silent-regression risk identified before writing this document):** `_brownfield_owner_contract_block` today does two things in one function: (1) emits the **instruction text** ("AUTHORITATIVE BROWNFIELD OWNER CONTRACT... preserve package/module identity, existing public type names, constructors, and public method signatures...", `attempt.py:141-147`) and (2) emits the **source content** (the per-file `"=== EXISTING OWNER: {filepath} ==="` + prefix-truncated excerpt). Under P1:
- The instruction text stays exactly where it is today, in `task_desc` — a short, fixed, always-emitted block (its own token cost is negligible and constant, not budget-managed), unconditional whenever `known_target_files` contains any existing file.
- The source content is replaced by real `ContextItem`s, produced through the SAME path §6/§7/§8 describe (member-aware where the language supports it, `CurrentSourceResolver`-sourced, budget-allocated, explicitly omitted rather than silently prefix-truncated) — i.e., this becomes one more *candidate set* (known target files) feeding the shared allocator, not a separate 24,000-char-capped loop.

**Why this converges onto `RetryPackage`'s pattern rather than reinventing:** `RetryPackage`/`project_implementation_source` (`retry_package.py`, `context_projection.py`) ALREADY implement every required behavior A3 asks for — per-file budget allocation (70/30 split generalizes to N-way scored allocation), explicit `omitted_files` with reason, head+tail (not prefix-only) truncation with an explicit marker, real revision hashing. The only gaps are: (a) it's retry-only today, never called on attempt 1; (b) it orders by `sorted(set(all_files))` (alphabetical), not relevance/score; (c) it has no member-level unit. P1 closes all three by making `known_target_files` on attempt 1 flow through the SAME `build_retry_package`-style allocator (relevance-ordered via the existing `file_scores` mechanism already computed for Graph RAG matches — extended to also score known-target files, defaulting to a floor priority per §10), instead of `_brownfield_owner_contract_block`'s own separate, worse-designed loop.

**Required behavior, mapped:**
- Member-aware where possible → §6.
- Relevance/authority aware → §10 (A5).
- Explicit omissions → §8, via the generalized allocator instead of `remaining <= 0: break`.
- No silent mid-file truncation → `project_implementation_source`'s existing head+tail+marker behavior, already better than today's prefix-only cut — reused, not reinvented.
- Deterministic budget behavior → one allocator (§4-5), not a second independently-tuned cap.

**Do NOT solve by making the cap larger** (explicit task instruction, honored): the 24,000-char number itself is retired, not raised — the real fix is per-unit, relevance-ordered, budget-integrated allocation, which naturally scales with the model's actual context window (reusing `_reserve_graph_context_budget`'s existing per-model scaling, §10) instead of a hardcoded constant.

---

## 10. Known-Target Budget Semantics (A5)

**Design:** a known target file (from `architect_files`/`expected_files_upfront`, or a retry's `state.all_files_written`) gets a **priority floor**, not unlimited/unconditional inclusion. Concretely: in the generalized per-unit allocator (§4-5, the same score-sorted degradation `build_code_context` already does at `context_budget.py:721-728`, just per-unit instead of per-file), a known-target unit's effective score is `max(retrieval_score, KNOWN_TARGET_FLOOR)` — a constant floor high enough that a known target degrades only after every non-target candidate has already degraded to its own floor, but not so high that ten known targets can each claim the entire budget regardless of size. This directly answers the task's own constraint: *"A retrieval score must not silently remove modification-critical exact source... but target status must NOT mean unlimited context."*

This is the mechanism that resolves P0's F11 finding (§6.5 of the P0 doc: an adversarially-low retrieval score could push a target file's body out at 8K budget) **without** contradicting P0's live-validation finding (§6.7: the real embedding model never actually mis-ranked the tested fixture) — the floor is a defense against a real, demonstrated mechanism, not a response to an observed real-world failure; it costs nothing when the ranker is accurate (the floor is a no-op whenever `retrieval_score >= KNOWN_TARGET_FLOOR` already) and only matters when it isn't.

**C5's "order/cap dependent" problem is closed the same way:** floor-then-score-sort replaces `_brownfield_owner_contract_block`'s Architect-list iteration order entirely — allocation order becomes a function of (floor, score), never list position.

---

## 11. Python Inheritance Extension (A6)

**Minimal, targeted extension of `_parse_python` (`graph.py:505-561`) — `DependencyGraph`'s schema is UNCHANGED.** The `relations` table already has a `type` column and `_RELATION_WEIGHTS` already defines `inherits`/`implements` at weight 1.0 (`graph.py:18-28`) — these are populated by `_parse_java` today and simply never emitted by `_parse_python`. The fix: walk `ast.ClassDef.bases` (and `ast.ClassDef.keywords` for `metaclass=ABCMeta`-style declarations) for every class definition already being visited for other symbol extraction in the same function, and emit one `relations` row per base, `type="inherits"`, `source=<class name>`, `target=<base class name or dotted base expression's simple name>`.

**Qualified/disambiguated identity, addressed explicitly per the task's instruction:** base-class names in `ast.ClassDef.bases` can be a bare `Name` (`class Foo(Base):`) or a dotted `Attribute` (`class Foo(module.Base):`). Emit the RIGHTMOST simple name in both cases (`Base`), matching the existing `relations` table's own unqualified-name convention (`graph.py:229-253`'s `get_callers`/`get_callees` already join on bare `name`) — this is a **consistency** decision (matches existing storage precision), not a claim that this closes the "unqualified names" scalability/precision gap named separately in P0's Graph RAG assessment. That gap (a common base-class name colliding across files) is unresolved by this narrow fix and is not claimed to be.

**Explicitly not in scope (per the task's own boundary):** multiple inheritance diamond resolution, MRO computation, cross-module type resolution for a dotted base import, or any general Python semantic analysis. This extension answers exactly one question — "does this class's own AST declare an `inherits` edge" — the same granularity `_parse_java`'s `extends`/`implements` regex already answers for Java, no more.

---

## 12. Incremental / Reuse Design (B2, B3, B4)

### B2 — `analyze()` reuse

**Not a blind RepositoryModel cache.** `RepositoryModel`'s output (`analyzer.py:326-405`) is determined by: the file-list/extension distribution (`os.walk` + `EXTENSION_MAP`), the top-level directory set, build-manifest **file contents** (`_detect_dependencies_and_frameworks` reads pom.xml/package.json/etc.), and a coding-style sample (`_detect_coding_style`). None of these change on an ordinary source-body edit; most of them change on a file add/delete/rename or a manifest edit.

**Cache key: reuse `compute_workspace_content_hash` (`checkpoint.py`), not a new digest mechanism** (per the task's "prefer reuse of existing repository-state mechanisms" instruction) — this is the SAME whole-workspace content-hash `run_generation_workflow` already computes once per run for checkpoint-drift detection (`workflow.py:997`, `checkpoint_content_hash`). **Important correctness note, addressed explicitly:** `run_generation_workflow` is re-entered per milestone under `WorkflowController`, and an earlier, already-applied milestone can have mutated the workspace between calls — so the cache cannot be a bare process-lifetime memo keyed on nothing; it must be keyed on the hash value itself (computed fresh, cheaply, each call — `compute_workspace_content_hash` is already a required per-run cost today, not new overhead) and invalidated whenever that hash changes, persisted alongside `dependency_graph.db`/`vector_index.db` under `paths.memory` (same DB, a new small `repository_model_cache` table, or a JSON sidecar — implementation detail for the work package, not an architectural fork). This gives real cross-run reuse (the common case: two `generate` calls against an unchanged repo) and correct invalidation across milestone boundaries (the hash differs the moment a prior milestone's write lands) without inventing a new invalidation mechanism.

### B3 — Cross-attempt context reuse

**Cache key: `(source identity, revision, tier)`**, exactly as the task's candidate key proposes, derived precisely as: `source identity` = `(path, type_id, member_id)` (the same identity triple §4's `ContextItem` extension carries), `revision` = the SHA-256 `content_revision()` already computed once per read (§7), `tier` = the rendered tier string (§4). **Invariant, restated as an implementation rule:** the cache is a pure function `(identity, revision, tier) → rendered content`; a lookup miss on any part of the key (including a revision change) is a correctness-safe cache miss, never a stale hit — this makes "never reuse context for a source revision different from the current worktree revision" true by construction, not by discipline.

**Scope: `AttemptContext`-lifetime only** (i.e., a plain Python dict living on `AttemptContext`, populated and read across the retry loop's own iterations, discarded when the attempt loop ends) — NOT persisted to disk, NOT shared across separate `generate` invocations. P0's evidence (§9 of the P0 doc) only demonstrates waste *within* a single run's retry loop (`build_code_context` re-reading/re-skeletonizing the same unchanged files on every retry); there is no P0 evidence that cross-RUN reuse of rendered context is safe or valuable (a different run's `matched_files`/`related_files`/budget can legitimately differ), so this design scopes the cache to exactly the lifetime the evidence supports, per the task's "scope cache to the narrowest useful lifetime unless evidence supports persistence" instruction.

### B4 — Token/skeleton reuse

**Folded into the SAME cache as B3, not a second cache.** `estimate_tokens`'s own cost is cheap (a `len()` call) but is invoked repeatedly on the same skeletonized string across a retry loop; since the unit-production path already produces one canonical rendered string per `(identity, revision, tier)` key, its token count is naturally memoized alongside it — no independent token-count cache is introduced (avoids the "cache proliferation" the task explicitly warns against). This directly generalizes `context_budget.py`'s own existing `skel_cache` local dict (`context_budget.py:656-662`, today scoped to one `build_code_context()` call) by lifting its lifetime from "one call" to "one attempt."

---

## 13. SQL-Index Migration (B1)

**Smallest possible migration: one `CREATE INDEX IF NOT EXISTS idx_relations_source_file ON relations(source_file)` statement**, added to `DependencyGraph._init_db()` (`graph.py:64-125`) immediately after the existing `ALTER TABLE relations ADD COLUMN source_file TEXT` (`graph.py:113-116`), following the exact same idempotent, no-migration-framework pattern the other four indexes at `graph.py:120-123` already use.

- **Backwards compatibility:** `CREATE INDEX IF NOT EXISTS` is a no-op against a database that already has the index (none do today) and does not require any data migration — existing rows already have `source_file` populated for every post-migration write (`graph.py:189` unconditionally calls `clear_file` then re-inserts with `source_file` set) or `NULL` for pre-migration rows (already-documented fallback path, `graph.py:104-112`), and an index over a column containing `NULL`s is valid SQLite.
- **Startup/index cost:** building this index on an EXISTING large `dependency_graph.db` (i.e., the first time a repository already indexed under the current schema is opened post-P1) costs one `CREATE INDEX` pass over the existing `relations` table — a one-time O(N log N) cost, paid once per database, not per `generate` call (`sqlite3` persists the index in the `.db` file itself).
- **Correctness:** eliminates exactly the `SCAN relations` behavior P0's `EXPLAIN QUERY PLAN` demonstrated (P0 §8) for `clear_file()`'s own `DELETE FROM relations WHERE source_file = ? OR (...)` query — no query-shape change required, the query already filters on `source_file` first in its `WHERE` clause; only the missing index needs to exist.
- **Isolation from context architecture:** this is a pure schema/index change to `graph.py`, with zero interaction with `ContextItem`/`ContextPackage`/the unit-production path — it can be implemented, tested, and shipped completely independently of every other item in this document (see §18, this is step 1 for exactly this reason).

---

## 14. Authority Boundary

**Explicit invariant, traced and preserved, not merely asserted:** `AttemptContext` already carries context fields (`matched_files`, `related_files`, `skills_prompt`, `worktree_path`) and authority fields (`write_scope_mode: WriteScopeMode`, `authorized_semantic_regions: List[AuthorizedSemanticRegion]`, `allowed_write_relpaths`, `protected_relpath`) as **structurally separate fields on the same object** (`attempt.py:393-567`) — this separation already exists today and P1 must not blur it.

**What P1 changes:** which files/members the model *sees* (context selection, §4-§10). **What P1 must never change:** which files/regions the model is *authorized to write* (`kriya/policy/filesystem.py::AuthorizedFileWriter`, `WriteScopeMode`, `AuthorizedSemanticRegion`/`find_unauthorized_semantic_changes`, `semantic_region_authority.py`). Concretely:
- `ContextUnitSelector`/`CurrentSourceResolver`/the budget allocator are **read-only** with respect to authority — they never read `write_scope_mode` or `authorized_semantic_regions` to decide what to INCLUDE, and they never write to either.
- The known-target priority floor (§10) is driven by `architect_files`/`expected_files_upfront` (Architect's own DESIGN output — what the plan says should change), never by `allowed_write_relpaths`/`authorized_semantic_regions` (POLICY's own enforcement input — what the model is ALLOWED to change). A file can be "known-target, high context priority" and still be write-denied by policy at write time, or vice versa — the two lists are read from different owners and never merged.
- A member-level `ContextItem` (`type_id`/`member_id` set) is a **context** addressing unit only. It is never passed to `AuthorizedFileWriter` or interpreted as a write-scope grant — a member being shown in full does not authorize a write to that member; that decision remains entirely `WriteScopeMode`'s (or CORR-018's, when active — §15).

---

## 15. CORR-018 Boundary

CORR-018 (`semantic_region_authority.py`) already states its own governing invariant verbatim: *"FILE WRITE AUTHORITY != SEMANTIC CHANGE AUTHORITY... This module decides, for a file already inside write scope, which SOURCE REGIONS may materially change."* (`semantic_region_authority.py:3-10`). P1's member-level context design is a **third**, still-distinct axis: which source regions the model *sees*, independent of both file-write-authority and semantic-change-authority.

**Reuse boundary, explicit:** P1's Python member extraction (§11 is for inheritance edges; the member-body extraction described in §6 is a new, small `ast`-based function) may share the SAME underlying `ast`/structural-parsing utilities CORR-018's own Java path uses conceptually (`extract_java_members`'s line-span precision, `stable_member_key`'s identity-key SHAPE) — but P1 introduces **no dependency** from the context layer onto `AuthorizedSemanticRegion`, `RegionType`, `stable_member_key`, or any CORR-018 module. A `ContextItem`'s `member_id` field (§4) is a plain, locally-scoped string for context addressing/dedup only — it is never compared against, derived from, or fed into `find_unauthorized_semantic_changes` or any `authorized_semantic_regions` list. `autonomy.semantic_region_enforcement_required` (CORR-018's own default-`False` gate) is untouched by every item in this document; P1 does not flip it, read it, or change its meaning.

---

## 16. C1–C6 / S1–S5 Mapping

| Finding | Owning component (post-P1) | Proposed behavior | Deterministic acceptance test |
|---|---|---|---|
| **C1** — 24K owner-contract cap truncates/omits | Shared allocator (§9, §4-5), replacing `_brownfield_owner_contract_block`'s own loop | Relevance-ordered, per-unit budget allocation; explicit omission via `make_omitted_entry`, never a silent `break` | Reproduce P0's §6.4 probe (4 files, combined >24K) against the new allocator: every file gets EITHER real content at some tier OR an explicit omission entry with `reason="budget_exhausted"` — assert zero files are silently dropped with no trace |
| **C2** — file-level-only degradation elides all bodies | `ContextUnitSelector` (§6) | Member-exact unit for the relevant member, independent tier for siblings, for Java/Python; explicit file-level fallback (no false precision claim) elsewhere | S2-style fixture (far-apart placement): assert the relevant member's `ContextItem.tier == "member_exact"` while a sibling member is independently `"signatures"` in the same rendered output |
| **C3** — stale workspace + fresh worktree same-file conflict | `CurrentSourceResolver` (§7) | Single current-source root per run, resolved from `create_git_worktree`'s own return value; dedup at unit-production time | Fixture: a file both Graph-RAG-matched and in `state.all_files_written`; assert exactly one `ContextItem` for that path, content matching `worktree_path`, not `workspace_path` |
| **C4** — Python inheritance missing from graph | `_parse_python` extension (§11) | Emit `type="inherits"` relation rows from `ast.ClassDef.bases` | Reproduce P0's §6.3 (S3) fixture: assert a `relations` row now exists for `StandardInvoiceCalculator → InvoiceCalculator`, `type="inherits"` |
| **C5** — known-target protection is order/cap-dependent, separate mechanism | Priority floor inside the shared allocator (§10) | `max(retrieval_score, KNOWN_TARGET_FLOOR)`, one mechanism, no separate cap | Reproduce P0's §6.4/§6.5 probes: known-target inclusion no longer depends on Architect-list position; a low-scored known target still clears the floor |
| **C6** — default path lacks provenance/omission tracking `ContextPackage` already has | `build_code_context` extended to populate `ContextPackage` internally (§5, §8) for the default pipeline too | Every default-pipeline `generate` call gets a real `ContextPackage`/omission list, not just the opt-in `WorkflowController` path | Assert `ContextPackage.omitted` is non-empty and reason-labeled whenever a real degradation occurred in a plain `kriya generate` run (no `workflow_controller.enabled` needed) |
| **S1** — missing `relations.source_file` index, super-linear cold index | One `CREATE INDEX` (§13) | Index added at `_init_db()` | Reproduce P0's §8 `EXPLAIN QUERY PLAN` probe: assert `SEARCH relations USING INDEX idx_relations_source_file` replaces `SCAN relations`; reproduce P0's S1 cold-index timing probe and assert sub-quadratic scaling |
| **S2** — `analyze()` full walk every call | Content-hash-keyed `RepositoryModel` cache (§12, B2) | Reuse `compute_workspace_content_hash`; skip `analyze()`'s own walk on a hash hit | Reproduce P0's §6.6 (S5) `analyze()` probe: assert `opens`/`dirs_walked` are near-zero on a cache-hit re-run, and correctly non-zero after a file add/delete or manifest edit |
| **S3** — retry-time `build_code_context` reread/reskeletonize | `(identity, revision, tier)` cache (§12, B3) | Cache hit for any matched/related file unchanged since its last render this attempt | Reproduce P0's repeated-work finding (P0 §9): assert `opens`/skeletonization-invocation counts drop to near-zero on a retry where `matched_files`/`related_files` content is unchanged |
| **S4** — token estimation recomputed for unchanged content | Folded into the same cache (§12, B4) | Token count memoized alongside rendered content | Assert `estimate_tokens` call count for an unchanged unit is exactly 1 across N retries, not N |
| **S5** — vector scan is O(total chunks) | **No P1 change** — recorded as residual risk (§22) | N/A | N/A (explicitly out of scope per task instruction) |

`UNOWNED_P0_FINDINGS = 0` — every C/S item above has an explicit owner.

---

## 17. Component Disposition Table

| Component | Disposition | Rationale |
|---|---|---|
| `ContextOrchestrator` | **EXTEND** (its `_rank_and_trim` is retired in favor of calling the shared allocator; `select_context_strategy`/milestone-specific assembly stays) | Stays the WorkflowController-scoped consumer, per §1's ownership decision |
| `ContextPackage` | **REUSE** (shape unchanged) | Already generic; §4 |
| `ContextItem` | **EXTEND** (additive optional fields) | §4 |
| `build_code_context` | **EXTEND** (becomes the unit-producing + rendering path for the default pipeline; external signature/output string shape unchanged) | §5, closes the duplicate-subsystem risk |
| `_brownfield_owner_contract_block` | **RETIRE its source-content logic into a thin wrapper; keep its instruction-text emission as-is** | §9 |
| `RetryPackage`/`build_retry_package`/`FileProjection`/`project_implementation_source` | **EXTEND** (becomes the reference implementation the default pipeline's attempt-1 path also calls, via the shared allocator; `ProjectionLevel`'s dead `SIGNATURES`/`SUMMARY` retired in favor of `ContextItem.tier`) | §4, §9 |
| `DependencyGraph` | **EXTEND** (`_parse_python` gains inheritance-edge emission, §11; schema unchanged except §13's index) | §11, §13 |
| `RepositoryAnalyzer.analyze` | **EXTEND** (gains a content-hash-keyed cache wrapper; internal walk logic unchanged) | §12 |
| `AttemptContext` | **EXTEND** (gains the B3/B4 per-attempt cache as a new field; existing fields, including the authority ones, untouched) | §12, §14 |
| Retry context builder (`retry_prompts.py`) | **REUSE** (already correctly worktree-sourced; becomes the pattern §7 generalizes, not itself changed) | §7 |
| `dependency_invalidation.py` | **REUSE** (`dependent_closure` pattern referenced, not duplicated, for B3's cache-key correctness) | §12 |

`DUPLICATE_CONTEXT_SUBSYSTEMS_PROPOSED = 0` — every disposition above is REUSE or EXTEND of an existing component; no NEW context-representation type is proposed (the only genuinely new code is: the `ContextUnitSelector`/`CurrentSourceResolver` orchestration functions, which are new *functions*, not new *representations* — they produce and consume the existing/extended `ContextItem`).

---

## 18. Migration / Implementation Sequence

The task's suggested ordering is **corrected** from code evidence: the SQL index (originally listed as step 8) has zero dependency on anything else in this document and is the cheapest, lowest-risk, most independently-verifiable change available — it ships first, not eighth. Current-source resolution (§7) must land before member-level selection (§6) and before owner-contract migration (§9), since both of the latter need a correct "which root do I read from" answer to avoid silently reintroducing C3 in new code. Python inheritance (§11) has no dependency on anything else and can land any time after step 1.

| Step | What | Files/modules | Behavior change | Compatibility requirement | Deterministic test | Rollback boundary |
|---|---|---|---|---|---|---|
| 1 | SQL index (B1, §13) | `graph.py::_init_db` | None visible; cold-index performance only | `CREATE INDEX IF NOT EXISTS` — always safe | `EXPLAIN QUERY PLAN` assertion + S1 timing re-run | Drop the index statement; zero data migration to reverse |
| 2 | Python inheritance extraction (A6, §11) | `graph.py::_parse_python` | New `relations` rows only (`type="inherits"`); no existing row changed or removed | Additive; `clear_file()`'s existing re-index-on-change behavior already handles it | S3-fixture assertion (§16, C4 row) | Revert the `_parse_python` diff; existing rows unaffected since nothing else reads `inherits` yet |
| 3 | `ContextItem` schema extension (§4) | `context_package.py` | None visible yet — new fields unused until step 4+ | All-optional-defaulted fields; every existing `make_context_item` call site unchanged | Existing `ContextOrchestrator` tests continue passing unmodified | Revert the dataclass diff; no data persisted in the new shape yet |
| 4 | `CurrentSourceResolver` (A2, §7) | new module (e.g. `kriya/workflow/context_source.py`) + `attempt.py`'s two `build_code_context` retry call sites | Retry-time Graph RAG context now reads `ctx.worktree_path` instead of `ctx.workspace_path` | Behavior-visible: fixes C3/F9 for real — flag this as the one step in this sequence that changes retry-time prompt content, not just internals | Reproduce the C3 dedup test (§16); a targeted regression test asserting retry context now matches worktree content for a file the current attempt already wrote | Revert the two call-site diffs; `ctx.workspace_path` is still valid, just stale-prone again |
| 5 | Member-level selection, Java + Python (A1, §6) | new `ContextUnitSelector` (same module as step 4, or a sibling), `java_members.py`/new Python `ast` helper (both REUSE/small-extend) | `build_code_context`'s internal candidate set can now include member-scoped units for these two languages; unsupported languages unchanged | Internal to `build_code_context`; external string-output shape unchanged | C2 fixture (§16) | Feature-flag the selector's member-mode; fall back to today's whole-file skeletonization path unconditionally |
| 6 | Shared allocator + omission recording (§4-5, §8) | `context_budget.py` (extends `build_code_context`'s existing per-file scoring loop to per-unit) | Degradation granularity changes from per-file to per-unit; `ContextPackage.omitted` now populated for the default pipeline | Rendered string shape for Planner/Architect/Developer preserved (§5a's hard constraint) | C1, C6 fixtures (§16); byte-for-byte prompt-shape diff test against a fixture with no degradation (regression guard) | Revert to file-level-only scoring; omission list simply stays empty (safe degrade) |
| 7 | Owner-contract migration (A3, §9) | `attempt.py::_brownfield_owner_contract_block` becomes a thin instruction-text-only emitter; known-target source content routes through step 6's allocator | Attempt-1 known-target content now budget-allocated/member-aware instead of 24K-prefix-capped | Instruction text (package/API-preservation wording) unchanged and still lands in `task_desc` | C1, C5 fixtures (§16) | Revert to the original combined function; no data-shape dependency on later steps |
| 8 | Known-target priority floor (A5, §10) | shared allocator (same module as step 6) | Known-target units get `max(score, floor)` | Purely additive scoring rule | C5 fixture (§16) | Set floor to 0 (no-op) to revert behaviorally without reverting code |
| 9 | `analyze()` incremental reuse (B2, §12) | `analyzer.py::analyze`, new cache keyed on `compute_workspace_content_hash` (`checkpoint.py`, REUSED) | `analyze()` skipped on a cache hit | Cache miss always falls back to today's full walk — never a correctness risk, only a performance one | S2 fixture (§16) | Disable the cache lookup; `analyze()` always runs (today's behavior) |
| 10 | Cross-attempt context cache (B3/B4, §12) | `AttemptContext` gains a new cache field; `build_code_context`'s per-unit render path checks it first | Retry-time rendering skips re-read/re-skeletonize/re-count for unchanged units | Cache keyed on `(identity, revision, tier)` — a miss is always safe (falls through to today's full render) | S3/S4 fixtures (§16) | Remove the cache field/lookup; behavior reverts to always-render (today's behavior, just slower) |

Each step is independently testable and independently revertible, per the task's own decomposition requirement. Steps 1-2 have zero interaction with anything context-related and can ship, be reviewed, and be validated in complete isolation. Steps 3-10 are ordered so that no step depends on a LATER step's output.

---

## 19. Deterministic Acceptance Strategy

Every C/S row in §16 already names its own deterministic test — summarized here as the overall strategy:

- **No live models/embeddings anywhere in acceptance testing.** Every test in §16/§18 is either a pure code-path assertion (schema, call-site routing, index plan) or a deterministic fixture reproduction, reusing `spikes/ctx_001_p0/fixtures.py`'s existing generators (per the same "do not duplicate fixture logic" discipline P0 itself followed) — no new fixture vocabulary is introduced where an existing P0 fixture already exercises the same shape.
- **Regression guard, explicit:** a "no visible degradation" fixture (e.g., a small repository where every candidate fits at `full` tier under the configured budget) must produce byte-identical rendered `convention_prompt` output before and after this migration — proving the internal refactor genuinely preserves external behavior for the common case, not just for the specific bugs being fixed.
- **§9's instruction-text split is independently testable:** assert `task_desc` still contains the "AUTHORITATIVE BROWNFIELD OWNER CONTRACT" instruction text for a known-target-existing-file scenario, decoupled from any assertion about `active_code_context`'s content — a test that only checked the combined output could pass while silently losing one half of the original behavior.
- **Existing test suites named for reference (not run by this architecture pass):** `tests/test_workflow.py`, `tests/test_subtask_executor.py`, `tests/test_workflow_controller_enforce.py`, and any `context_orchestrator`/`context_package`/`context_budget`-named test modules — the actual regression baseline for an eventual implementation pass, per this repository's own quota-conscious convention of handing full pytest runs to the user.

---

## 20. Performance Acceptance Metrics

Relative structural targets, derived from P0's own measured baselines (`spikes/ctx_001_p0/results/*.json`), not invented SLAs:

| Metric | P0 baseline | P1 target |
|---|---|---|
| `clear_file()`'s relations DELETE query plan | `SCAN relations` (full table scan, confirmed via `EXPLAIN QUERY PLAN`) | `SEARCH relations USING INDEX idx_relations_source_file` — no full scan, for any repository size |
| Cold-index time scaling (S1 bands, 25.17× files) | 247× time (super-linear, root-caused to the above) | Sub-quadratic; directionally back toward the ~25-38× range S1's OWN chunk/embedding-proportional cost already establishes as the honest floor (P0 §6.7's real-embedding run: 37.9× time for 25.17× files, once the O(N²) component is removed) |
| `analyze()` opens/dirs_walked on a no-op re-run | Identical to a cold run every time (zero incremental behavior, P0 S5) | Near-zero on a cache hit (content-hash unchanged); full walk only on an actual cache miss |
| `build_code_context` opens/skeletonization-invocations across N retries with unchanged matched/related content | O(N) — full re-read + re-skeletonize every retry (P0 §9) | O(1) amortized — one real render per unique `(identity, revision, tier)`, cache hits for every subsequent retry |
| Context-assembly cost vs. repository breadth, given a fixed selected-relevant-unit set | Already flat/LOCAL (P0 §6.1 — the one component already meeting this bar) | Preserved unchanged — this is a "do not regress" target, not a "do not regress" aspiration; the byte-identical-output regression guard (§19) is the enforcement mechanism |
| Vector retrieval scan cost | O(total indexed chunks) (P0 §4/§8) | **Unchanged** — explicitly out of scope (S5, §22) |

---

## 21. Explicitly Rejected Architecture

Per the task's own "DO NOT BUILD" list, and reaffirmed by this design pass finding no contradicting evidence for any of them:

- Repository/module/package semantic summarization — no C/S finding traces to its absence (P0 §16 already reached this conclusion; unchanged).
- A new vector database or ANN index — P0's live validation (§6.7 of the P0 doc) showed rank-1 stability across the full tested breadth range; the real, measured cost (37.9× time for 25.17× files, once index-time embedding latency is isolated from the now-fixed O(N²) component) does not, by itself, justify a new indexing technology. Recorded as a residual risk (§22), not a rejected-without-consideration item.
- Graph RAG replacement — §1/§2's ownership analysis and P0's own Graph RAG classification (`PARTIALLY_REUSED`/`EXTENDED`) both hold; nothing in this design pass required touching `query_hybrid`/`get_neighborhood`'s own algorithms.
- A general cache-everything framework — §12 (B2/B3/B4) deliberately scopes every cache to the narrowest lifetime the evidence supports (per-run for `analyze()`, per-attempt for cross-attempt reuse) and folds token/skeleton reuse into the SAME cache rather than proliferating independent ones.
- Model-specific context handling, a larger-context-window workaround, or a prompt-only workaround for missing deterministic context — none of C1-C6/S1-S5 are addressed by any of these in this design; every fix is a deterministic code-path change.
- A new orchestration framework — §1 explicitly rejects adopting `ContextOrchestrator`'s own orchestration contract into the default pipeline; no new orchestration layer is introduced anywhere in this document, only a shared context-unit production/rendering path consumed by existing orchestration.

---

## 22. Residual Risks

- **Vector scan cost (S5) remains O(total indexed chunks)** — real, measured, unresolved by this design. If a future repository/query profile demonstrates this actually degrading real retrieval-serving latency beyond what §20's table implies is acceptable, that would be new evidence warranting reconsideration — not assumed here.
- **P0's live retrieval-precision evidence (rank-1, zero degradation) is one fixture, one query.** This design's Graph-RAG-untouched decision rests on that evidence; a materially different real-world repository/query shape could behave differently. No P1 item depends on this risk being false, but it is the load-bearing assumption behind §21's "do not touch the vector store" rejection.
- **The Python member-body `ast` extension (§6) is new code with no P0 empirical validation yet** (P0's own Python member-elision evidence was at the file/skeleton-tier level, not at a hypothetical member-exact tier that doesn't exist in production today) — its own deterministic acceptance test (§19) validates structural correctness (right line range for a given member), not yet integrated end-to-end token-budget behavior at scale; that integration is exactly what the implementation work packages (§23) and their own deterministic tests are for.
- **The content-hash-keyed `analyze()` cache (§12/B2) introduces the first persistent cross-run cache in the default pipeline's Repository Analysis stage.** Its invalidation rule (any workspace-content-hash change invalidates it wholesale) is deliberately conservative — correct, but not maximally granular (a change to one unrelated file anywhere in the repo still invalidates the whole cached `RepositoryModel`, even though most of that model's fields wouldn't have changed). This is accepted as the safe, evidence-supported starting point (P0 named `analyze()`'s TOTAL absence of caching as the gap, not its lack of fine-grained invalidation) — finer-grained invalidation is a future refinement, not required by any C/S finding.
- **F8 remains genuinely open and is not advanced by this document** — whether losing a body invariant from context actually changes real model output is unaffected by an architecture design pass that runs no models. See §24/F8_STATUS.

---

## 23. Implementation Work Packages

Directly derived from §18's sequence, grouped into independently-shippable packages:

| Package | Steps | Scope | Estimated coupling to other packages |
|---|---|---|---|
| **WP1 — SQL index** | §18 step 1 | `graph.py` only | None — ships alone |
| **WP2 — Python inheritance** | §18 step 2 | `graph.py::_parse_python` only | None — ships alone |
| **WP3 — Context-unit schema** | §18 step 3 | `context_package.py` only | Prerequisite for WP4-WP7; no visible behavior change alone |
| **WP4 — Current-source resolver** | §18 step 4 | new module + 2 call sites in `attempt.py` | Depends on WP3; first behavior-visible fix (C3/F9) |
| **WP5 — Member-level selection** | §18 step 5 | new selector + Java/Python extractors | Depends on WP3, WP4 |
| **WP6 — Shared allocator + omission** | §18 step 6 | `context_budget.py` | Depends on WP3, WP5 |
| **WP7 — Owner-contract migration + known-target floor** | §18 steps 7-8 | `attempt.py` | Depends on WP6 |
| **WP8 — `analyze()` incremental reuse** | §18 step 9 | `analyzer.py` | Depends only on WP1-independent, reuses `checkpoint.py` unchanged — can ship any time after WP1/WP2, in parallel with WP3-WP7 |
| **WP9 — Cross-attempt context cache** | §18 step 10 | `AttemptContext` + `context_budget.py` | Depends on WP6 (needs the unified render path to cache against) |

`UNOWNED_P0_FINDINGS = 0`, `DUPLICATE_CONTEXT_SUBSYSTEMS_PROPOSED = 0`, `UNRESOLVED_SOURCE_OF_TRUTH = 0` (§7), `UNRESOLVED_AUTHORITY_COUPLING = 0` (§14), `UNEXPLAINED_ARCHITECTURE_GAPS = 0`.

---

## 24. F8 Status

**F8 remains deferred, unchanged from P0's own disposition.** This architecture pass ran no models and makes no claim about whether member-aware exact context (once WP5-WP7 are implemented) actually changes real model output versus today's file-level degradation. The future validation the P0 doc and the user's own acceptance message both named stands as specified: **current file-level degradation vs. P1's member-aware context, same task/model/config** — this is post-implementation adversarial evidence, explicitly not a P1 architecture blocker, and not something this design document advances beyond recording it as the intended future comparison.

---

## 25. WP5 Production Member-Hint Integration — Investigation Addendum (2026-09-17)

**Status: investigation only, no code changed.** Package 2 correctly disclosed `member_exact` as a proven, tested capability with no production caller supplying `member_hints` yet. This addendum traces exactly what grounded member identity already exists in the executable pipeline before context construction, and records the design decision for a future, still-unimplemented integration pass.

**Decision: D — two independent, complementary, already-grounded evidence sources should feed one small member-hint resolver.** Not A alone (evidence is real but requires a small deterministic extraction step, not a bare pass-through) and not E (no larger design change is required — every STOP condition below cleared).

**Trace, stage by stage:**

| Stage | Member identity present? | Form |
|---|---|---|
| Repository analysis (`analyzer.py::analyze`) | No | File/framework facts only, no symbols |
| Vector indexing (`analyzer.py::chunk_file_with_metadata_headers`) | **Yes — currently discarded downstream** | For Python/Java, each chunk is already scoped to ONE class or ONE method, with a literal `"Method: {name}"`/`"Class: {name}"` header line baked into the chunk's own `text`, and real `start`/`end` line numbers computed via `ast.end_lineno` (Python) or brace-matching (Java) — **but `add_document()`'s call site (`analyzer.py:835-842`) only persists `text`/`embedding`/`chunk_index`, never `start`/`end`.** The member NAME still survives as parseable text inside `text` itself; the precise line range does not survive past indexing. |
| `LocalVectorStore.query_hybrid` | **Yes — currently discarded at the call site** | Each hit dict already carries `chunk_index` and the full chunk `text` (with its `"Method:"/"Class:"` header). `workflow.py`'s retrieval stage (`workflow.py:1609-1617`) reads only `filepath`/`score`/a 300-char preview for logging, then collapses everything to `matched_files_list` (bare file paths) — the member name embedded in `text` is read into memory and then thrown away. |
| `DependencyGraph.symbols` | Partial, lower precision | Real member NAMES exist (Python: real `end_lineno`; Java: `_parse_java`'s method rows use a hardcoded `end_line = idx + 2`, not brace-matched — NOT precise enough for member-exact extraction on its own). No enclosing-type column, so a bare method name can't be qualified into `"Type.method"` without re-parsing. Used today only to seed `get_neighborhood`'s BFS (which files are related), never to select a member within a matched file. |
| `get_neighborhood` results | No new signal beyond the above | Same bare symbol names, used for file-level relatedness scoring only. |
| Planner | No | Free prose plus (sometimes) full code blocks; `extract_planner_code_blocks` extracts whole-file content, never a method name. |
| Architect | No | The ONLY structured, programmatically-parsed output is `{"files": [...]}` — a flat file list (`kriya/agents/contracts.py::parse_file_list`). The system prompt asks for "class/interface/method signatures" in the free-form Markdown design, but nothing parses that prose into structured member identity anywhere in the codebase today. **A known-target file's member is genuinely unknown from Architect output alone.** |
| Retry failure evidence | **Yes — real, structured, already populated** | `Failure.file_locations: List[FileLocation]` (`failure.py:89-93`, `filepath`/`line`/`col`) is populated today by `extract_error_source_locations` for real compiler/test failures. A `(file, line)` pair maps deterministically onto "which member's line range contains this line" via a pure range-containment check against `member_boundaries_for(path, current_content)` — zero guessing, reuses Package 2's own extractor unchanged. |
| `build_code_context_package`/`build_known_target_context` | N/A (consumer) | Already accepts `member_hints: Dict[str, str]` (known-target path only, today's Package 2) — the resolver's own output shape. |

**EXISTING_MEMBER_EVIDENCE, summarized:** VECTOR_METADATA = present, currently discarded at two points (persistence AND query-consumption). DEPENDENCY_GRAPH = present but lower-precision/unqualified, secondary at best. GRAPH_RAG (the retrieval stage as a whole) = collapses to file level immediately, same root cause as vector metadata. PLANNER = none. ARCHITECT = none (file-level only). RETRY_EVIDENCE = present, real, structured. LSP = see below.

**LSP classification: `NOT_APPLICABLE`.** `kriya/tools/lsp.py::JdtlsClient` implements exactly one capability — `check_file` (compile-diagnostics grounding, Java-only, retry-loop-only, opt-in on `jdtls` being found on `PATH`). It has no `documentSymbol`/`definition`/`workspace-symbol` method at all; `"lsp_reference"` is a declared-but-never-produced `CONTEXT_SOURCE_TYPES` entry (grepped — zero real producers), the same "dead vocabulary" pattern P1 already retired for `ProjectionLevel.SIGNATURES`/`SUMMARY`. `triage.py:539-541` independently documents that a real LSP workspace-symbol search was considered and explicitly rejected elsewhere in this codebase as "a meaningfully heavier dependency" — corroborating that adding LSP symbol capability now would be new LSP architecture, not reuse, and is out of this investigation's scope by the task's own STOP condition.

**Design outline (not implemented):** a new, small, pure resolver — `resolve_member_hint(path, content, *, chunk_text=None, failure_line=None) -> Optional[str]` — that (a) extracts a candidate member NAME either by parsing the controlled `"Method: X"`/`"Class: X"` header line from a matched chunk's own `text` (a format this codebase already writes, not free text), or by finding which `member_boundaries_for(path, content)` range contains `failure_line`; then (b) validates the candidate against `member_boundaries_for(path, content)` (current, fresh content — never the possibly-stale indexed copy) and returns the member_id ONLY if it resolves to a real boundary. Any non-match (renamed/removed/ambiguous/unsupported language) returns `None`.

**HINT_CONFIDENCE: no scoring framework introduced — a binary validated/not-validated gate is sufficient and correct.** Because step (b) above always re-validates against the CURRENT file's real structural boundaries (not the source's own claimed identity), every hint that survives is equally trustworthy by construction — there is no meaningful "partial confidence" state to model, and inventing one would contradict the task's own "do not create a scoring framework unless actual existing evidence requires one" instruction. `HINT_PROVENANCE` (`"vector_chunk_header"` vs `"failure_location"`) is worth recording for observability/trace purposes only, never for ranking or arbitration.

**Fallback (unchanged from Package 2, no new code needed):** `build_known_target_context`'s existing `REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION` path already IS the correct "no trustworthy hint" behavior — a hint that fails validation degrades to file-level handling exactly as a member hint for an unsupported language does today. This investigation found no need to invent a new fallback path.

**KNOWN_TARGET_BEHAVIOR:** Architect output alone gives no member identity for a known target. A known-target file only gets a real hint when it ALSO happens to be a Graph-RAG matched/related file this run (a common, expected overlap — the file the goal is about is usually also semantically matched) — in which case the vector-chunk-derived hint could be correlated onto it BEFORE Package 2's own dedup exclusion (`_graph_context_exclusion_set`) removes it from the matched/related candidate set. When no such overlap exists, file-level known-target behavior is retained, unchanged — never invented.

**RETRY_BEHAVIOR:** `Failure.file_locations` can supply a real member hint for a targeted retry's implicated file(s) without changing retry authority at all (the hint only affects which part of an already-permitted file is shown in full vs. degraded — never which file is targeted or writable).

**STOP conditions, all cleared:** no new authority semantics (hints stay read-only, feeding only the existing `member_hints` parameter); no CORR-018 change; no Planner/Architect contract redesign (Architect's existing `{"files": [...]}` contract is untouched — this reads FROM Graph-RAG/failure evidence, not from Architect); no new LLM classification stage (pure string-parsing + range containment); no Graph-RAG replacement (`query_hybrid`'s return shape is already exactly what's needed, unchanged); no new persistent index (`chunk_index`/`text` are already persisted — nothing new to write, only something already-returned to stop discarding); scope stays narrow (one new small resolver function + two narrow wiring call sites: `workflow.py`'s retrieval stage, and the retry branches' `retry_package`/`Failure` handling in `attempt.py`).

**C2_STATUS: `MECHANISM_FIXED_PRODUCTION_INTEGRATION_PENDING`.** The allocator/extractor mechanism (Package 2) is proven and deterministically tested. A concrete, narrow, fully-evidenced integration path now exists (this addendum) but is **not implemented** — production `kriya generate` calls still do not supply `member_hints` today. This is not `ARCHITECTURE_GAP` (no larger design change is needed) and not yet `FULLY_PRODUCTION_REACHABLE` (nothing wires it in yet).

---

### 25.1 Implementation Refinement (2026-09-17) — C2 closed, `FULLY_PRODUCTION_REACHABLE`

The integration outlined above was implemented in full, on top of this addendum, with two design points refined beyond what §25 originally sketched:

- **`member_hints`'s value type widened additively**: `Dict[str, Union[str, Sequence[str]]]` (was `Dict[str, str]`) — a bare string still works unchanged (100% backward compatible with every existing Package-2 call site/test), and a list/tuple carries multiple validated member_ids for the same path. Required by the Java-overload case below and by "multiple grounded members in one file" (e.g. `far_apart` S2 placements).
- **Java overload ambiguity: retain all real candidates, never guess.** A bare name (`"format"`) that resolves to more than one real boundary (two overloads sharing that name) produces ONE deduplicated `member_id` in the hint list; `build_known_target_context` then expands that single `member_id` back out to EVERY real boundary sharing it (`context_source.py::boundaries_matching_member_id`) and emits a member-exact `ContextItem` for each — never an arbitrary `next()`-style first pick. This is the conservative option the parent task itself named as acceptable.
- **Failure-location resolution: most-specific containing boundary wins.** A line is often inside both a method's own range and its enclosing class's range at once; `resolve_member_hints_from_failure_location` picks the smallest-span containing boundary deterministically (the method, not the class) — a compiler/test line number is a far more precise signal about the method than the class.
- **Retry wiring scoped to the plain targeted-retry branch only** (not `fallback_targeted`/`missing_files`/the full-set-retry branch, and not `API_CONTRACT_RECOVERY`, which has its own separate, already-tested deterministic evidence model). A `_retry_package_for_attempt(..., exclude=...)` parameter (new, additive) prevents the member-exact block from duplicating what `RetryPackage`'s own rendering already shows for the same path — filtering `all_files` rather than `target_files`, since the latter would silently re-trigger `build_retry_package`'s own `failure.likely_files` fallback.
- **A known-target path's member is NEVER invented from target status alone** — `_resolve_known_target_member_hints`/`_resolve_retry_member_hints` (both `attempt.py`) only ever look up a path already present in `known_target_files`/`state.last_implicated_files` in the already-grounded evidence dict; a `FileLocation` naming an un-implicated file is ignored entirely (never authorizes a new file).

Production changes: `kriya/workflow/context_source.py` (new resolvers: `parse_controlled_chunk_header_name`, `member_ids_matching_name`, `boundaries_matching_member_id`, `resolve_member_hints_from_chunk_header`, `resolve_member_hints_from_failure_location`), `kriya/workflow/context_budget.py` (`build_known_target_context`'s multi-member loop), `kriya/workflow/workflow.py` (retrieval-stage candidate-name parsing, threaded via a new `AttemptContext.retrieval_member_hints` field), `kriya/workflow/attempt.py` (the two correlation/resolution functions and their two call sites). `MEMBER_LEVEL_CAPABILITY = IMPLEMENTED`, `AUTOMATIC_PRODUCTION_MEMBER_SELECTION = IMPLEMENTED`, `C2 = FULLY_PRODUCTION_REACHABLE`.

---

## 26. Package 3 (WP8/WP9) — Investigation + Implementation Addendum (2026-09-18)

**WP8 (`RepositoryAnalyzer.analyze()` reuse): STOPPED before any implementation, per the task's own key-validation-first requirement.** A concrete, reproduced, deterministic probe (no live models, plain git + filesystem operations) demonstrated `compute_workspace_content_hash()` staying byte-identical while `RepositoryAnalyzer.analyze()`'s own legitimate output changed:

```
hash_before == hash_after   (True)
languages_before = {}                    languages_after = {'Python': 100.0}
total_files_before = 1                   total_files_after = 2
```

**Root cause: `analyze()`'s `os.walk` has no `.gitignore` awareness at all** — it excludes only a small, hardcoded `ignore_dirs` set (`.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `.pytest_cache`, `build`, `dist`, `.egg-info`, `eggs`, `bin`, `obj`, `skills`, `memory`, `logs` — `analyzer.py:339-356`), while `compute_workspace_content_hash()`'s own `git add -A` step honors the workspace's real `.gitignore` (`checkpoint.py:141`). A file added under a directory that a project's own `.gitignore` excludes, but that isn't also in that hardcoded set (a custom `generated/`, `reports/`, or similar project-specific ignore pattern — not a rare/contrived case, just not one of Kriya's own bookkeeping directories), is invisible to the hash but still walked, counted, and sampled by `analyze()` — a real, reproducible under-invalidation gap, not a theoretical one. **`UNSAFE_ANALYZE_CACHE_PATHS` would be nonzero for any cache keyed on this hash alone** — per the task's own explicit STOP condition, no cache was built. `analyze()` itself was not modified (changing its own ignore semantics is a correctness fix to a different, pre-existing, undocumented component - out of WP8's reuse-only scope, and not attempted here).

**WP9 (AttemptContext-lifetime source-derivation reuse): IMPLEMENTED, independent of the WP8 finding** (WP9's cache key is a per-file `content_revision()`, entirely unrelated to `compute_workspace_content_hash`'s own repo-wide gitignore-aware tree - the architecture's own §12 already scoped these as two independent mechanisms).

- **One cache object** (`context_source.py::SourceDerivationCache`), attached to `AttemptContext.source_cache` (`field(default_factory=SourceDerivationCache)`, one instance per real run, never persisted, never shared across runs) — two internal maps, not two independent caches: `content_cache` (mtime-fast-pathed raw reads, shared with `CurrentSourceResolver`'s own new optional `content_cache` constructor parameter) and `_derivations` (keyed by the exact `(path, member_id, tier, revision)` tuple the architecture's own B3 design named, `member_id=None` for whole-file derivations).
- **`CurrentSourceResolver` extended, not duplicated**: its `resolve()` method now shares one module-level `_cached_read()` primitive with `build_code_context_package()`'s own file reads — the same mtime-then-hash two-tier pattern `DependencyGraph`'s own `index_repository()` incremental logic already established (reused, not invented), with mtime used ONLY as a reason to skip a read, never as proof of identical content on its own (a mtime change always falls through to a real read and a freshly-computed real revision).
- **Skeletonization/member-extraction/sibling-signatures are cached** (pure functions of content+tier/line-range, safe by construction); **the bounded-excerpt whole-file fallback (`_fit_whole_file`) is deliberately NOT cached** — its own output depends on the caller's `remaining_tokens` at call time, not just content+tier, so caching it under this key scheme would silently violate cached-vs-uncached semantic equivalence (disclosed in that function's own docstring, not a silent gap).
- **Overload cache-key collision found and fixed while implementing**: two distinct Java overload bodies sharing one `member_id` would otherwise collide onto the same cache entry; the cache key used for `get_or_compute_derivation` folds in the boundary's own line range (`f"{member_id}:{start}-{end}"`) as a cache-key-only discriminator — the `ContextItem`'s own real `member_id` field is unaffected.
- **Concurrency**: no lock introduced. Confirmed by inspection (zero `asyncio.gather`/`create_task`/`ThreadPoolExecutor`/`threading.Thread` anywhere touching `AttemptContext` or context construction) that every cache-populating call happens from one sequential asyncio task with no concurrent access to the same `AttemptContext`; every compute function is fully synchronous (no `await` between a cache check and its store), making the check-compute-store sequence atomic by construction.
- **Wired into all 6 real attempt.py call sites** (4 retry-branch `build_code_context` calls, 2 `build_known_target_context` calls) plus both hint-resolution helpers' own `CurrentSourceResolver` construction — `cache=None` remains the default everywhere, so every pre-Package-3 caller/test keeps its exact byte-identical behavior unchanged.

`ANALYZE_REUSE = NOT_IMPLEMENTED (STOPPED — see WP8 finding above)`, `ATTEMPT_CONTEXT_REUSE = IMPLEMENTED`, `UNSAFE_ANALYZE_CACHE_PATHS = 0` (nothing shipped), `STALE_CONTEXT_CACHE_PATHS = 0`.

---

## 27. WP8 Blocker Resolution (2026-09-18)

**Canonical repository-state decision: A/B (they converge here) — `index_repository()`'s own existing discovery primitive (`parse_gitignore()`/`is_ignored()`, real `.gitignore` content, nested-`.gitignore`-aware) is canonical; `analyze()` was bypassing it with its own separate, smaller, non-`.gitignore`-aware `ignore_dirs` set. Reused, not redesigned.**

**Discovery semantics, before → after:**

| Path | Before | After |
|---|---|---|
| `RepositoryAnalyzer.analyze()` | Own hardcoded `ignore_dirs` set + blanket dot-prefix exclusion only — no `.gitignore` read at all | Same `ignore_dirs`/dot-prefix exclusion (additive, unchanged — Kriya's own `skills`/`memory`/`logs` bookkeeping-exclusion incident is still covered), **plus** the same `nested_gitignore_patterns_for()`/`is_ignored()` primitive `index_repository()` already used |
| `index_repository()` | Its own inline nested-`.gitignore` accumulation loop | Identical behavior, now calling the SAME extracted `nested_gitignore_patterns_for()` function `analyze()` also calls — one shared primitive, not two independently-evolved copies |
| `compute_workspace_content_hash()` | Real git `git add -A` (honors real `.gitignore` via git's own engine) — unchanged | unchanged |

**Residual, disclosed limitation**: `is_ignored()` is an `fnmatch`-based approximation of real `.gitignore` semantics (no negation-pattern (`!pattern`) support; `**` behaves like a single `*` — which happens to still match across path separators in `fnmatch`, covering the common recursive-glob case by construction rather than by design). This is a **pre-existing** limitation of `index_repository()`'s own already-shipped discovery (unchanged by this package, not newly introduced) — `analyze()` now inherits exactly this same, already-accepted precision, not a worse one. The only cache-safety-relevant direction is under-exclusion (`is_ignored()` says "not ignored" for something real git ignores); empirically, `is_ignored()`'s own extra `system_ignores` set and blanket dot-prefix rule make it, if anything, MORE aggressive than real `.gitignore`, and negation's own failure mode (treating a negated file as still-ignored) is an OVER-exclusion — safe direction (a hash change still correctly triggers a cache miss; `analyze()`'s own output for that file is a pre-existing, unrelated correctness question, not a caching-safety one).

**Non-git workspace semantics**: `compute_workspace_content_hash()` returns `None` for a non-git root (unchanged, pre-existing fail-closed behavior). `RepositoryAnalyzer.analyze()`'s new `_analyze_cache_key()` treats `None` as "never cache" — every call against a non-git workspace recomputes fresh, byte-identical to pre-WP8 behavior. Proven: `test_non_git_workspace_always_recomputes_and_still_works`.

**Cache identity**: `(root_path, compute_workspace_content_hash(root_path))`, module-level dict in `kriya/analyzer/analyzer.py` (`_ANALYZE_CACHE`) — in-process only, no disk write, no cross-process persistence (a fresh `kriya` CLI invocation starts with an empty dict). Scoped this way (not per-`RepositoryAnalyzer`-instance, which is constructed fresh on every real call site and would make a narrower cache a no-op) because the real, P0-documented waste is repeated `analyze()` calls **within one long-running process** — a milestone-decomposed run's own multiple `run_generation_workflow()` calls, or a `kriya repl` session's multiple `generate`s.

**Cache output safety**: every cache hit returns `cached_model.model_copy(deep=True)` (pydantic v2) — never the shared stored instance. Verified no existing consumer mutates a `RepositoryModel` (grepped the full codebase for a field assignment outside `analyzer.py`'s own construction path — zero), and added `test_cached_model_is_not_a_shared_mutable_instance` as a structural guarantee regardless.

**Security**: no widening. `analyze()`'s walk only ever produces aggregate statistics (language %, file counts, folder names, a cheap indentation-character sample) — never raw file content beyond what it already read pre-WP8 — so tightening its exclusions (this change is exclusion-ADDITIVE, never removes an existing exclusion) can only ever keep MORE out of the `RepositoryModel` that reaches Planner/Architect prompts, never less. `AUTHORITY_EXPANSION_PATHS = 0`, `UNEXPLAINED_SECURITY_EXPANSIONS = 0`.

**Verification**: 16 new deterministic tests (`tests/test_analyzer_cache.py`) — the original unsafe-cache case reproduced-then-closed, the full required invalidation matrix (modification/addition/deletion/rename/manifest/directory-structure/nested-`.gitignore`), two independent git roots never cross-contaminating, non-git fallback, cache-output immutability, semantic equivalence (cached `model_dump()` == fresh `model_dump()`), performance counters, and direct discovery-primitive convergence with `index_repository()`'s own walk (via the shared primitive directly — **deliberately never calling `index_repository()` itself**, which has an unconditional real embedding call and, absent an existing auto-skill directory, a real LLM completion call — this task's own "no live models/embeddings" boundary). `ANALYZE_REUSE = IMPLEMENTED`, `CANONICAL_REPOSITORY_STATE = ESTABLISHED`, `ANALYZE_STATE_IDENTITY = SAFE`, `UNSAFE_ANALYZE_CACHE_PATHS = 0`, `DISCOVERY_SEMANTIC_DIVERGENCES = 0`, `CACHE_DEPENDENT_REPOSITORY_MODEL_DIFFERENCES = 0`.
