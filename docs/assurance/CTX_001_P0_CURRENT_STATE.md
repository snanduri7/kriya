# CTX-001 P0 — Current-State Investigation: Context Correctness & Scalability

**Status:** P0 COMPLETE AND CLOSED. Investigation + deterministic measurement by this investigation; the one deferred live-validation item (real embedding retrieval precision, §19-A) has since been executed by the user and folded in (§6.7). This investigation itself made zero production code changes, zero live model calls, zero full pytest runs — the one live run in this document (§6.7/§19-A) was executed by the user, against a script this investigation prepared and safety-verified but did not run.

**Baseline:** branch `milestone-decomposition`, HEAD `24d7c9846d8a24e5b2ee0986cf8fcb18e4926819` == `origin/milestone-decomposition`, tracked worktree clean at investigation start. MODEL-001 unmodified. RECV-002 untouched (out of scope).

**Method:** every claim below is grounded in either (a) a direct read of the executable production code at the cited `file:line`, or (b) a deterministic probe under `spikes/ctx_001_p0/` that calls that same, unmodified production code directly and measures its behavior. No architecture is inferred from names, comments, or docs — every "EXISTS"/"ABSENT" classification below was confirmed by tracing an actual call site or, where a call site could not be found, by an explicit grep showing zero call sites.

**Units caveat (applies to every token number in this document):** all token counts use Kriya's own `estimate_tokens()` (`kriya/workflow/context_budget.py:532-553`), a `len(text)//4` heuristic. Its own docstring concedes this undercounts real BPE tokenization for punctuation-dense code. Every "fits in 8K/16K/32K" claim below is stated **in Kriya's own units**, not real model tokens — treat the budget bands as directional, not exact.

---

## 1. Current Executable Context Flow

Traced from `kriya/cli.py::generate` (line 1498) → `WorkflowEngine.run_generation_workflow` (`kriya/workflow/workflow.py:723`), by following real call sites, not module names. Two structurally independent repository walks exist — this is the single most important structural fact in this investigation and it recurs throughout the rest of the document.

| # | file:function/class | produced artifact | consumer |
|---|---|---|---|
| 1 | `cli.py::generate` (1498) | resolved `goal`, `AppConfig`, `WorkflowEngine` | `WorkflowEngine.run_generation_workflow` |
| 2 | `workflow.py::_ensure_repository_indexed` (319) → `RepositoryAnalyzer.index_repository` (`analyzer.py:624`) | populates `dependency_graph.db` (symbols/relations) + `vector_index.db` (chunks/embeddings), **only when never indexed before**, **only if `autonomy.auto_index_missing_dependency_graph` is `True`** (default **`False`** — `default_config.yaml:232`) | Graph RAG retrieval (step 5) |
| 3 | `analyzer.py::RepositoryAnalyzer.analyze` (326) | `RepositoryModel` (languages, frameworks, architecture signature, coding-style heuristics) | Planner prompt (`workflow.py:1744` `plan_prompt += convention_prompt`… goal text), Architect prompt (`workflow.py:1848`) |
| 4 | `skills/skill.py::SkillEngine.from_config` (`workflow.py:1213`) | active skills' rules/instructions/examples, folded into `convention_prompt` | Planner, Architect, Developer (all three, via the shared `convention_prompt` accumulator) |
| 5 | `workflow.py` Graph RAG stage (1576-1663): `LocalVectorStore.query_hybrid` (`vector.py:522`) + `DependencyGraph.get_neighborhood` (`graph.py:430`) + `context_budget.py::build_code_context` (634) | `matched_files`, `related_files`, per-file `file_scores`, `graph_rag_context` string | folded into `convention_prompt` → Planner, Architect, Developer's first attempt |
| 6 | `workflow.py` learned-knowledge RAG (948-958): `vector_store.query_learned_knowledge` | `learned_rag_context` (fenced "Untrusted Reference Context") | appended to `convention_prompt` |
| 7 | `PlannerAgent.run` (`agent.py`, called at `workflow.py:1771`) | `plan` text (may contain fenced code) | Architect, Developer (plan reused directly on attempt 1 if it already contains every expected file — `attempt.py:4310-4325`) |
| 8 | `ArchitectAgent.run_with_file_list` (`workflow.py:1872`) | `design`, `architect_files` (deterministic target-file list) | Developer's `known_target_files`/`expected_files_upfront` |
| 9 | `worktree.py::create_git_worktree` (`workflow.py:1593`) | isolated `.kriya/worktree` copy of the workspace | every Developer write + compile/test gate for this run |
| 10 | `attempt.py::run_attempt` (3718) → `attempt.py::_brownfield_owner_contract_block` (119) | **exact, unskeletonized, current on-disk source** for every `known_target_files` entry that already exists, capped at 24,000 combined chars, injected into both `task_desc` and `active_code_context` | `DeveloperAgent.run_generation` (attempt 1 only) |
| 11 | `attempt.py::_run_developer_generation` (336) → `DeveloperAgent.run_generation`/`_fill_missing_content` (`agent.py:2061`/`1388`) | `files` (list of `{filepath, content}` or `{filepath, edits}`) | `apply_anchored_edits` / direct write into the worktree |
| 12 | `kriya/tools/validate.py::PolymorphicValidator` (constructed `attempt.py:3810`) | compile/test gate outcome | retry-loop decision (`retry_policy.py::decide_retry_action`) |
| 13 | on retry: `attempt.py` rebuilds `active_code_context` **from scratch** every time — `build_code_context(ctx.matched_files, ctx.related_files, ...)` called again (3862, 4199) + `_build_targeted_retry_prompt`/`_build_full_set_retry_prompt` (`retry_prompts.py`) reading `state.all_files_written` **fresh off `ctx.worktree_path`** | fresh `active_code_context`, `task_desc` | next Developer call |
| 14 | `ReviewerAgent.run` (`workflow.py:2654`, `3438`) | review text | approval gate / trace log |
| 15 | `kriya/core/trace.py::TraceLogger` | persisted run trace (plan, retries, gate outcomes, retrieved chunks, model hops) | `kriya traces` |

**Opt-in second pipeline (not part of the default flow above):** `kriya/workflow/workflow_controller.py::WorkflowController` (`workflow_controller.enabled: false` by default — `default_config.yaml:175`) runs an alternative, milestone-decomposition-aware orchestration that calls `ContextOrchestrator().build()` (`context_orchestrator.py:147`) → a structured, hashable `ContextPackage` (`context_package.py`) with real provenance, an honest non-silent `omitted[]` list, and — critically — **fresh on-disk content for every subtask's `planned_files`, read directly per subtask** (`workflow_controller.py:6733-6760`). This is a materially more principled context-construction mechanism than the default pipeline's raw string concatenation, but it is disabled by default and only reachable through `WorkflowController`, not `run_generation_workflow`. See §15/§18.

---

## 2. Capability Map

`EXISTS` / `PARTIAL` / `ABSENT` / `DEAD-UNWIRED`. Evidence file:line given for every row; PARTIAL states exactly what's covered and what isn't.

### Discovery / structure

| Capability | Status | Evidence |
|---|---|---|
| Repository discovery | **PARTIAL** | Two independent, non-shared walks. `analyze()` (`analyzer.py:326-405`): unconditional `os.walk`, hardcoded `ignore_dirs` set, **no `.gitignore` respected**, no caching, runs in full on every `generate` call. `index_repository()` (`analyzer.py:624-712`): respects nested `.gitignore` via `parse_gitignore`/`is_ignored` (292/306), and is mtime+hash cached — but only reachable via `auto_index_missing_dependency_graph` (default `False`) or the explicit `kriya analyze`/`learn` CLI. |
| Ignore rules | **PARTIAL** | `parse_gitignore`/`is_ignored` (`analyzer.py:292-317`) exist and are wired into `index_repository()` only (`analyzer.py:683,705,708`) — **zero call sites** inside `analyze()`, confirmed by grep. `analyze()`'s only exclusion is a hardcoded dir set (`analyzer.py:339-356`). |
| Module/package/language detection | **EXISTS** | `analyze()`'s `EXTENSION_MAP` (15) + `_detect_architecture`/`_detect_dependencies_and_frameworks` (`analyzer.py:407-584`), lockfile/manifest heuristics (pom.xml, package.json, requirements.txt, etc). |
| Build-system detection | **EXISTS** | `kriya/tools/validate.py::PolymorphicValidator` — real-marker detection (pom.xml/build.gradle → Java, Gemfile/Rakefile → Ruby, requirements.txt/pyproject.toml/setup.py/.py-file-presence → Python); anything else is `"unknown"`, not a silent Python fallback (per CLAUDE.md, confirmed structurally). |
| Symbol/type extraction | **PARTIAL** | `DependencyGraph.index_file` (`graph.py:184-227`) dispatches to `_parse_python`/`_parse_java`/`_parse_xml`/`_parse_ruby` only. Chunking/embedding coverage is far wider (`index_repository`'s `target_extensions = EXTENSION_MAP.keys() | {.xml}` — 16 languages), but **symbol/relation extraction is EXISTS for py/java/xml/rb only, ABSENT for js/ts/go/rs/cs/cpp/c/kt/swift/php despite those files being embedded as vector chunks.** |
| Member/method boundaries | **PARTIAL** | Java: `extract_java_members` (`kriya/analyzer/java_members.py`) — precise constructor/method inventory with exact line spans, purpose-built for A1 review/semantic-region authority. Python: `_python_declaration_ranges` (`context_budget.py:133-188`) — tokenize-based, precise, but internal to skeletonization, not exposed as a general member API. No equivalent for any other language. |
| Import/dependency graph | **EXISTS** (per-file, name-keyed) | `DependencyGraph.get_imports` (`graph.py:255`), `relations` table `type='imports'`. |
| Call relationships | **PARTIAL** | `relations` table `type='calls'`, keyed on **bare, unqualified symbol name** (`graph.py:229-253`, `get_callers`/`get_callees` join `r.source = s.name` with no file/class scoping). A common method name across two files is not disambiguated at the *storage* layer — the *retrieval* layer (`get_neighborhood`) mitigates this at the output stage only (see §4). |
| Inheritance/interface relationships | **PARTIAL → demonstrated gap for Python** | `_RELATION_WEIGHTS` (`graph.py:18-28`) defines `inherits`/`implements`/`extends` at weight 1.0 (the highest tier), and `_parse_java` emits them for Java `extends`/`implements` clauses. **`_parse_python` does not emit them at all** — confirmed empirically by S3 (§6.3): `StandardInvoiceCalculator(InvoiceCalculator)` produces zero `inherits`/`implements` relation row; only the `import` edge exists. A Python interface/implementation pair is invisible to the graph unless the implementation also *calls* something with a name matching the interface. |
| Cross-file/cross-module relationships | **PARTIAL** | Real for direct import/call edges (S3 confirmed caller→impl and test→caller). Not real for Python inheritance (above). Not qualified by package for common names (above). |

### Context

| Capability | Status | Evidence |
|---|---|---|
| Chunking | **EXISTS**, dual-mode | `chunk_file_with_metadata_headers` (`analyzer.py:47-230`, used by `index_repository` for embeddings) vs `chunk_file_syntactically` (`analyzer.py:232-270`, line-based `max_lines=100/150`, used only by `review_context.py:102` and internally at `analyzer.py:226` as `chunk_file_with_metadata_headers`'s own XML/generic fallback). Both live and both wired — **not** one dead, confirmed by grep call sites. |
| Structural chunking | **EXISTS for py/java/c-family** | `skeletonize_python` (`context_budget.py:191-259`) is `tokenize`-based, decorator-aware, declaration-boundary-precise. `skeletonize_braced_code` (383-529) is brace-depth + regex, comment/string-safe (via `_strip_java_comments_and_strings`). **Not line-based truncation** — this is a real, load-bearing correctness feature. |
| Token budgeting | **EXISTS**, real, per-model | `_reserve_graph_context_budget` (`context_budget.py:563-587`): `0.75 * model_context_window - tokens(skills_prompt) - tokens(learned_rag_context)`, floored at 1000; `_reserve_sibling_content_budget` (608-628): `0.15 * window`, floored at 500. Both scale per-model, addressing a documented 2026-08-07 real incident (fallback-model starvation). |
| Repository/module/package/file/member summary | **ABSENT** (repo-level) / **EXISTS but coarse** (file-level, via skeleton tiers) | No repository-, module-, or package-level *summary* artifact exists anywhere in the traced pipeline — `RepositoryModel` (analyze()) is facts (languages %, frameworks, architecture signature), not a semantic summary. File-level "summary" is the skeleton/signatures tier, which elides bodies wholesale, not a generated abstraction. No member-level summary of any kind. |
| Cumulative knowledge across stages | **PARTIAL** | `convention_prompt` is shared verbatim across Planner/Architect/Developer-attempt-1 (real accumulation). Across *subtasks* in the default pipeline: none observed (each `run_generation_workflow` call is independent). Under `workflow_controller.enabled=True` (opt-in): `established_file_context` carries prior-milestone output forward explicitly, with real provenance (`context_orchestrator.py:174-180`) — genuine cumulative knowledge, but gated off by default. |
| Targeted retrieval | **PARTIAL, precision now live-confirmed for the tested case** | Vector half: real RRF hybrid fusion (`vector.py:522-549`); real-embedding validation (§6.7) confirmed rank-1, 100%-relevant top-10, zero degradation across a 25× breadth increase for the one controlled fixture/query this investigation defines — not a general precision guarantee across arbitrary repos/queries. Lexical half: real FTS5/LIKE, **but phrase-matches the whole query as one literal consecutive-token string** (`query_lexical`, `vector.py:488-520`, `query_parts = f'"{clean_query}" OR ...'`) — single-term/identifier queries retrieve precisely (S3, §6.3), multi-word natural-language queries whose words aren't literally adjacent in source silently return zero hits. |
| Exact-source retrieval | **PARTIAL, two real but capped mechanisms** | (a) First attempt, existing target files: `_brownfield_owner_contract_block` (`attempt.py:119-149`) injects full current content, **capped at 24,000 combined chars across all target files, in Architect-list order, truncating mid-file (not omitting) once the cap is hit** — demonstrated in §6.4/§9 to both drop a member (F2) and drop entire files (F1). (b) Retries: `files_with_current_content`/`state.all_files_written`, read fresh from `ctx.worktree_path` (`retry_prompts.py`), unbounded per-file but budget-capped in aggregate (`_reserve_sibling_content_budget`). Neither mechanism covers an arbitrary `matched_files`/`related_files` entry that isn't a known target — those get whatever skeleton tier the budget allocator assigns. |
| Relevance ranking | **EXISTS** | RRF (vector.py:522-549) for retrieval; per-file weighted score (`file_scores`, `workflow.py:1618-1645`) for budget degradation ordering (`build_code_context`, `context_budget.py:704-741`). |
| Context deduplication | **ABSENT in the default pipeline** | No dedup mechanism found between `graph_rag_context` (workspace-path content) and the retry-time worktree-content injection — see F9/F10 (§5). `ContextOrchestrator`'s `seen` set (`workflow_controller.py:6740`) dedupes by path, but only within its own opt-in call. |
| Context provenance | **ABSENT in the default pipeline / EXISTS under `workflow_controller`** | The default `graph_rag_context` is an opaque concatenated string with no per-item source/trust metadata. `ContextPackage`'s `ContextItem` (`context_package.py`) carries `source_type`/`trust_level`/`reason` — real provenance — but only reachable via the opt-in `WorkflowController`. |
| Context freshness / mutation invalidation | **PARTIAL** | `dependency_invalidation.py::dependent_closure`/`invalidate_validated_revisions` (37 lines total) is real and wired (`attempt.py:266`), but scoped only to `state.validated_file_revisions` (the retry-package cache), not to `vector_index.db`/`dependency_graph.db`. The Graph RAG index itself is **not** invalidated or refreshed mid-run after a candidate write — see F9 (§5), demonstrated by direct code reading (not merely inferred). |

### Scalability

| Capability | Status | Evidence |
|---|---|---|
| Parsed-result / file-content reuse | **PARTIAL, per-layer, sharply divergent** | `index_repository()`: real mtime+hash incremental skip (`analyzer.py:761-784`), **S5-confirmed** (§8): a no-op re-run opens 0 files; a one-file change opens exactly 1 file (1.67% of a 60-file repo). `analyze()`: **zero caching of any kind** — S5-confirmed identical `opens`/`dirs_walked` regardless of whether anything changed, on every single `generate` call. |
| Token-count caching | **ABSENT** | `estimate_tokens` is O(len(text)) and re-invoked on every `build_code_context` call, every retry, with no memoization across calls (only a per-call `skel_cache` local dict, `context_budget.py:656-662`, scoped to one `build_code_context` invocation). |
| Repository-index reuse (Graph RAG) | **EXISTS but only within the incremental gate above** | Cold-build cost is real and, per §8, super-linear (see F19/P16). |
| Stage-to-stage / retry reuse of `build_code_context` output | **ABSENT** | `attempt.py` calls `build_code_context(ctx.matched_files, ctx.related_files, ...)` **fresh, from scratch, re-reading every matched/related file from disk and re-skeletonizing it**, on every retry (`attempt.py:3862`, `4199`) — confirmed by direct code reading, no caching layer between calls. |
| Incremental analysis | **PARTIAL** | Real for `index_repository()` (mtime/hash). Absent for `analyze()`. |

---

## 3. Context Ownership by Stage

| Stage | Owns relevance/context selection | Real exact-source guarantee |
|---|---|---|
| Repository Analysis | `RepositoryAnalyzer.analyze()` — facts only, no relevance judgment | N/A |
| Planner | receives `convention_prompt` (skills + graph_rag_context + learned_rag) built once, before Planner runs (`workflow.py:1218-1704`, folded in at 1744) | none beyond whatever Graph RAG retrieved |
| Architect | same `convention_prompt`, plus `plan` text | none beyond Graph RAG |
| Developer, attempt 1 | `active_code_context = ctx.skills_prompt + build_code_context(ctx.matched_files, ctx.related_files, ...) + learned_rag`, **plus** `_brownfield_owner_contract_block` appended for `known_target_files` that already exist (24,000-char combined cap) | real, but capped and order-dependent (§1 row 10, §5, §9) |
| Developer, retries | `active_code_context` rebuilt fresh; `_build_targeted_retry_prompt`/`_build_full_set_retry_prompt` inject `state.all_files_written` read fresh from `ctx.worktree_path`, **unbounded per-file** (budget-capped in aggregate via `_reserve_sibling_content_budget`) | real for files already written this run; unchanged Graph RAG skeleton for everything else |
| Reviewer | final `review_text` built from the accepted candidate + `convention_prompt` context (not independently re-traced in P0 — out of the critical context-correctness path since it runs after mutation, not before) | not investigated in depth (low P0 priority — Reviewer does not gate what code gets written, only what gets flagged) |
| Recovery / retry loop | `attempt.py::run_attempt`, `retry_policy.py::decide_retry_action` | see Developer-retries row above |

---

## 4. Graph RAG Assessment

**Node types:** `symbols` (filepath, name, type, start_line, end_line) and `files` (filepath, mtime, hash) — `graph.py:70-92`.
**Edge types:** `relations` (source, target, type, source_file) — `type ∈ {imports, inherits, implements, extends, calls, declares_bean, references_bean, annotated_with, injects}` (`graph.py:18-28`, only `imports`/`calls`/inheritance-for-Java actually populated per language, §2).
**Construction path:** `RepositoryAnalyzer.index_repository()` → `DependencyGraph.index_file()` → language-specific `_parse_*`.
**Persistence:** SQLite, `dependency_graph.db` (graph) + `vector_index.db` (embeddings/chunks/FTS5), both under `paths.memory`.
**Invalidation:** mtime-then-hash fast-path skip per file (`analyzer.py:761-784`); `clear_file()` re-derives a file's own rows on re-index. **No invalidation triggered by a candidate write mid-run** (§2, freshness row).
**Retrieval algorithm:** vector = brute-force NumPy cosine scan over **every** row in `vector_chunks` (`vector.py:384-425`, `SELECT ... FROM vector_chunks` with no `WHERE`, no ANN index) fused via Reciprocal Rank Fusion (k=60) with lexical FTS5/LIKE results (`vector.py:522-549`). Graph = bounded BFS (`get_neighborhood`, `graph.py:430-499`) with per-relation-type/hop-distance weighting, output capped at `max_results` but **traversal itself is not bounded per-hop** (a high-fan-out bare symbol name can expand the BFS queue significantly before the final cap is applied).
**Structural vs. lexical vs. semantic:** structural (symbols/relations) is real but name-keyed, not fully-qualified. Lexical is real, phrase-matched (see §2). Semantic (vector) is real in production; deterministic P0 measured its **cost** only (fake embeddings — §6 caveat), and a subsequent user-run live validation (§6.7) measured its **precision** for the one controlled fixture/query this investigation defines: rank 1, zero degradation, 100%-relevant top-10, at every S1 band.
**Symbol/member identity:** bare name, not qualified by file/class/package at the *storage* layer (mitigated at *output* only by `filepath` join + score cap).
**Cross-file/cross-module relationships:** real for import/call; absent for Python inheritance (§2, §6.3).
**Token budgeting relationship:** Graph RAG's own retrieval (`RetrievalLimits` — top_k/max_hops/max_results, `context_budget.py:26-60`) is entirely separate from `build_code_context`'s token budget — widening retrieval limits only means more candidates are *considered*, never more tokens *sent* (by design, per that dataclass's own docstring, confirmed by reading).
**Build/query cost:** cold build is super-linear in repository size (§8, F19 — root cause identified: unindexed `relations.source_file` column, `clear_file()` full-scans on every file). Query cost is linear in total indexed chunk count, confirmed empirically (§6.1) at ~1:1 with chunk count growth, regardless of how much of the repository is relevant to the query.
**Repeated build/query behavior:** the vector query itself is stateless-cheap (single `query_hybrid` call per `generate` run in the default pipeline — not repeated across retries, since `matched_files`/`related_files` are computed once and reused as inputs to `build_code_context`), but `build_code_context`'s own per-file disk-read-and-skeletonize work IS repeated on every retry (§2, §9).

**Classification: Graph RAG should be `PARTIALLY_REUSED` and `EXTENDED`, not replaced.** Graph RAG has demonstrated reusable foundations; replacement is not justified. Retrieval correctness and scalability require targeted extensions — and, as of §6.7/§19's completed live validation, real (not merely deterministic-cost) evidence now supports retrieval precision for the tested case. The retrieval algorithm (RRF + weighted BFS), the storage schema, and the mtime/hash incremental-indexing mechanism are real, working machinery that replacing would discard for no evidenced benefit. The concrete, evidence-backed gaps requiring extension are: (a) missing Python inheritance-edge extraction, (b) an unindexed column causing super-linear cold-build cost, (c) unqualified symbol names, (d) no per-attempt caching of `build_code_context`'s own output. All four named gaps are extensions to the existing component, not a redesign.

---

## 5. F1–F20 Classification

`PREVENTED` / `HANDLED` / `POSSIBLE` / `DEMONSTRATED` / `UNKNOWN`.

| # | Failure mode | Classification | Evidence |
|---|---|---|---|
| F1 | Relevant file omitted by prioritization/budget | **DEMONSTRATED** | §6.4 probe: 4 target files, 3rd/4th get zero owner-contract content once the 24,000-char cap is exhausted by the first (`attempt.py:134-140`, `remaining <= 0: break`). |
| F2 | Relevant member outside retained portion of a large file | **DEMONSTRATED** | Same §6.4 probe: the first file's owner-contract excerpt is a **prefix** (`source[:remaining]`, `attempt.py:136`) — a 27,650-char file with its relevant method placed near the end gets truncated before that method, confirmed by direct string-containment check. |
| F3 | Two relevant members far apart in one 2K–3K+ line file | **DEMONSTRATED** | §6.2 (S2) `far_apart` placement: at "full" tier both members' signatures+bodies are present; at "skeleton"/"signatures" tier both bodies are elided uniformly (Python `skeletonize_python`'s non-"full" branch collapses *every* `def` body, not just far ones — confirmed §6.2). |
| F4 | Implementation spans packages | **HANDLED** (import/call edges are real) with **PARTIAL caveat** (unqualified names — §2) | `graph.py:229-253`, `get_neighborhood` |
| F5 | Interface and implementation span modules | **DEMONSTRATED (Python)** / **HANDLED (Java)** | §6.3 (S3): Python `class Impl(Interface)` produces zero `inherits`/`implements` relation row. Java `extends`/`implements` regex-captured (`_parse_java`, not independently re-tested in P0 beyond the schema read, since S3's fixture is Python — see §11 caveat). |
| F6 | Change requires repository-wide caller knowledge | **PARTIAL / HANDLED for direct callers** | `get_callers` (`graph.py:229-241`) is real but name-keyed, not transitively bounded beyond `get_neighborhood`'s `max_hops`. |
| F7 | Planner sees symbol but Developer lacks exact implementation | **DEMONSTRATED** | Direct consequence of F2: Planner/Architect see the symbol (via `graph_rag_context`, possibly skeletonized), Developer's exact-source guarantee for that same file can be truncated away by the 24,000-char cap. |
| F8 | Lossy summary omits modification-critical invariant | **DEMONSTRATED (mechanism), POSSIBLE (an actual invariant loss)** | Skeleton/signatures tiers replace method bodies with `"..."` / `"{ ... }"` unconditionally (`context_budget.py:210-211`, `446-449`) — any invariant expressed only in a body (not the signature or a docstring) is mechanically dropped at those tiers for *any* file that degrades below "full", confirmed §6.2/Java cross-check (§10). Whether a *specific* run actually needed that invariant is task-dependent — not testable without a live model, hence POSSIBLE for the second half of this claim. |
| F9 | Context becomes stale after earlier subtask mutation | **DEMONSTRATED (mechanism), by direct code reading** | `build_code_context` is called with `ctx.workspace_path` (`attempt.py:3862`, `4199`) — the **original, pre-run workspace**, never updated until post-approval (§1 row 6 of the pipeline) — while `_build_targeted_retry_prompt`/`_build_full_set_retry_prompt` separately read `state.all_files_written` from `ctx.worktree_path` (`retry_prompts.py`, current, post-candidate-write content). `_target_exists()` (`attempt.py:113-116`) checks *both* roots, confirming the codebase's own awareness that they can diverge. A file that is both Graph-RAG-matched (stale workspace content, possibly skeletonized) and already written this run (fresh worktree content, full) appears in the SAME prompt at two different contents. |
| F10 | Duplicate/irrelevant context displaces required context | **DEMONSTRATED** | Same mechanism as F9 (two representations of one file in one prompt is itself irrelevant duplication competing for budget), plus §6.5 (S4): with real production-representative `file_scores` (confirmed always passed on attempt 1, `workflow.py:1657`), a target file scored low by the retrieval ranker loses its body at 8K budget while unrelated files at higher scores are retained. |
| F11 | Task succeeds with large context budget but relevant evidence disappears at smaller budget | **DEMONSTRATED** | §6.5 (S4) directly: `target_body_survives=false` at 8K, `true` at 16K/32K, under `with_scores_target_low` — reproducible, deterministic. |
| F12 | Chunk boundary splits semantic unit | **PREVENTED for declaration boundaries** | `skeletonize_python`/`skeletonize_braced_code` never split a `def`/`class`/method mid-declaration (tokenize- and brace-depth-based, §2) — but this is a different failure from F2/F3 (body *elision*, not boundary *splitting*). No evidence found of an actual mid-declaration split in either language's skeletonizer. |
| F13 | Unchanged files repeatedly parsed/read across stages | **DEMONSTRATED for `build_code_context`, PREVENTED for `index_repository`** | §9: every retry re-reads and re-skeletonizes every `matched_files`/`related_files` entry from disk (`attempt.py:3862`,`4199`), regardless of whether that file changed. `index_repository()` itself is mtime/hash-cached and does not re-read unchanged files (§8/S5). |
| F14 | Repository-wide work repeated for task-local operation | **DEMONSTRATED** | `analyze()` always walks the entire repository tree on every `generate` call regardless of task scope (§8, `analyze_opens_are_constant_regardless_of_change: true`). |
| F15 | Tokenization repeatedly recomputed for unchanged content | **DEMONSTRATED** | `estimate_tokens` has no cross-call cache (§2); called fresh inside every `build_code_context`/`_rank_and_trim` invocation. |
| F16 | Graph/index rebuilt unnecessarily | **PREVENTED** for the mtime/hash-cached path (S5); **N/A** — no evidence anywhere of a full graph rebuild being triggered by anything short of `--force` or a genuinely-changed file. |
| F17 | Small mutation triggers disproportionate repository re-analysis | **PREVENTED for `index_repository`, DEMONSTRATED for `analyze`** | S5 (§8): one file changed → 1 of 60 files reprocessed by `index_repository` (1.67%); `analyze()` reprocesses effectively 100% every time by construction (it has no change-detection at all). |
| F18 | Prompt/context grows across retries without bounded useful-information growth | **PARTIAL** | Retry prompts add a bounded fix-analysis/error-source block (scoped, capped) — not unbounded growth. But the *re-fetched* `build_code_context` output is recomputed identically each time (F13) rather than reused, which is waste, not growth. |
| F19 | Repository breadth increases deterministic preparation cost disproportionately | **DEMONSTRATED** | §6.1 (S1): cold `index_repository` time grows **247×** for a **25× file-count** increase — confirmed super-linear via `EXPLAIN QUERY PLAN` (§8): `relations.source_file` has no index, `clear_file()` (called on every `index_file()`) does `SCAN relations` — cost accumulates as O(N²) superimposed on the linear per-file parse/embed work. Root cause named, reproduced, documented, **not fixed** per P0 scope. `query_hybrid` retrieval time also scales ~linearly with total indexed chunk count (30.49× chunks → 33.32× query time at the same band), confirmed by direct code reading (`vector.py:384-425`, full-table scan) and empirical measurement together. `build_code_context` itself (given a fixed matched/related set) stays flat (~1.0× tokens, ~1.3-1.7× time) across the same 25× breadth growth — genuinely LOCAL to relevant-code size, a real strength. |
| F20 | Large-file size increases processing cost disproportionately | **PREVENTED (roughly linear, not disproportionate)** | §6.2 (S2): skeletonization time grows from 0.0002s→0.037s across 500→3500 lines (~7×), consistent with the file-size growth (~6×) — a single-pass structural scan, not quadratic. |

---

## 6. Fixture Design & Results

All fixtures generated deterministically under `spikes/ctx_001_p0/fixtures.py` (no hand-authored one-off files). Ground truth (MUST_CHANGE/MUST_PRESERVE/relevant members/required relationships) is defined independently of any LLM in the same module. Probe harness: `spikes/ctx_001_p0/probes.py`, calling real, unmodified `kriya/` production code directly. Raw JSON for every run is in `spikes/ctx_001_p0/results/`.

**Core fixture** (`fixtures.CORE_FILES`/`CORE_GROUND_TRUTH`): a 4-file Python billing package — `invoice_interface.py` (ABC), `invoice_impl.py` (`StandardInvoiceCalculator`, the true modification target), `caller.py` (`CheckoutService`, must stay untouched), `test_invoice.py`. Used across S1/S3/S4.

**Live-model-call safety note (important process finding):** building this harness surfaced that `index_repository()` unconditionally attempts a real LLM completion (`ConventionsExtractorAgent`, `analyzer.py:910-948`, gated only on `not os.path.exists(auto_skill_dir)`) and that per-chunk embedding goes through `OllamaEmbeddingClient.get_embeddings` (**plural**, `vector.py:73`) — a real `httpx` network call, structurally separate from the singular `get_embedding` used for the initial dimension probe. An early probe iteration patched only the singular method and made a real (failed, 404) network attempt before this was caught and fixed. The final harness (`probes.py::patched_embedding_client`) patches both methods, points `llm.base_url`/`embedding.base_url` at a deliberately unreachable address as defense-in-depth, and pre-creates the auto-skill directory so the LLM-call branch is never entered — all via the production code's own existing skip conditions, not a patch to `kriya/`. **This is itself a P0 finding worth carrying forward: `kriya analyze`/`index_repository()` is not purely deterministic — it has one real, unconditional (on first index) LLM call site as a side effect**, relevant to Q24.

### 6.1 S1 — Repository Breadth (`run_s1.py`, `results/s1_repository_breadth.json`)

Core (4 files) + deterministic filler (10 unrelated domains, template-generated) at bands small(60)/medium(260)/large(760)/very_large(1510 total files).

| band | files | files×norm | analyze time× | index cold time× | vector chunks scanned× | vector query time× | build_code_context time× | build_code_context tokens× |
|---|---|---|---|---|---|---|---|---|
| small | 60 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| medium | 260 | 4.33 | 1.2 | 8.01 | 5.07 | 5.54 | 1.36 | 1.0 |
| large | 760 | 12.67 | 2.33 | 65.32 | 15.24 | 16.95 | 1.33 | 1.0 |
| very_large | 1510 | 25.17 | 4.4 | **247.31** | 30.49 | 33.32 | 1.69 | 1.0 |

Interpretation: `build_code_context` cost is **LOCAL** to the fixed matched/related set — flat across 25× repository growth (real strength). `index_repository` cold-build and `query_hybrid` are **SUPER-PROPORTIONAL**/**PROPORTIONAL** respectively to total repository size — see §8/F19 for root cause. Vector-query retrieval **precision** could not be measured (fake embeddings carry no real semantic signal — target file never ranked in top-10, expected and explicitly not a correctness finding, see caveat below).

### 6.2 S2 — Large File Depth (`run_s2.py`, `results/s2_large_file_depth.json`)

Synthetic single-class Python files at 500/1000/2000/3000-line target bands × 3 placements (near_start/near_end/far_apart), relevant method logic held byte-identical across all combinations.

Key result (all bands, all placements): `body_survives_full=True`, `body_survives_skeleton=False`, `body_survives_signatures=False` — **the method body is eliminated at BOTH non-"full" tiers, not only "signatures"** (a correction to the intuitive assumption that "skeleton" preserves bodies — it does not, for Python: `skeletonize_python`'s non-"signatures" branch, `context_budget.py:247-251`, collapses every `def` body). Signature always survives at every tier.

Budget sweep (single file, `build_code_context` at 8K/16K/32K): at 589 real lines (~4,931-token full content), 8K already retains "full" tier (body survives). At 2339/3508 lines, 16K is **not** enough (body absent), 32K is. Cross-checked against Java (§10): identical elision behavior confirmed via `skeletonize_code` on a real Java method — this is a language-independent property of the skeletonization design, not a Python-only artifact.

### 6.3 S3 — Cross-Module Relationship (`run_s3.py`, `results/s3_cross_module.json`)

Core fixture's caller→impl→interface→test chain, indexed inside a 250-file repository.

- `caller.py → invoice_impl.py` ("imports+calls"): relation row **found** (`StandardInvoiceCalculator`, `calculate_total` both present as relation targets from `caller.py`'s `source_file`).
- `invoice_impl.py → invoice_interface.py` ("imports+implements"): relation row **NOT found** — only the plain `import` target (`core.billing.invoice_interface`) is recorded; `InvoiceCalculator` itself never appears as a relation target from `invoice_impl.py`. Confirms F5's Python gap directly.
- `test_invoice.py → caller.py` ("imports+calls"): relation row **found**.
- Lexical retrieval: single-term queries (`"StandardInvoiceCalculator"`, `"calculate_total"`) return precise hits on exactly the right files, zero filler noise. The two-word natural-language query `"invoice tax"` (words not literally adjacent in the source) returns **zero hits** — confirms the phrase-matching limitation in §2/§4.

### 6.4 F1/F2 direct demonstration (ad hoc probe, not a named S-fixture — reported here per §5)

4 target files, each 27,650 real chars (a 700-line synthetic file, relevant method placed near the end), passed to `_brownfield_owner_contract_block` unmodified. Result: file 1 gets a 24,036-char **prefix** that does **not** reach the relevant method (near the end); files 2–4 get **zero** owner-contract content at all. This is the single most decisive, deterministic (no LLM, no embeddings) demonstration in this investigation.

### 6.5 S4 — Context Pressure, multi-file allocation (`run_s4.py`, `results/s4_context_pressure.json`)

Core's 2 real matched files + 3 synthetic ~800-line "related" files, at 8K/16K/32K, under three scoring regimes. **Production always passes real `file_scores` on attempt 1** (`workflow.py:1657` — confirmed by reading, not assumed), so the score-aware regime, not the `no_scores` regime, is representative of the default pipeline's first attempt. Retries, however, call `build_code_context` **without** `file_scores` (`attempt.py:3862`,`4199` — no `file_scores=` argument at either call site) — an inconsistency: attempt 1 uses relevance-informed degradation, every retry falls back to the cruder categorical (related-degrades-before-matched) strategy.

| scoring regime | 8K target body survives | 16K | 32K |
|---|---|---|---|
| no_scores (categorical; retry-time behavior) | true | true | true |
| target scored LOWEST (adversarial-but-plausible real-ranker case) | **false** | true | true |
| target scored HIGHEST (ideal ranking) | true | true | true |

### 6.6 S5 — Incremental Change (`run_s5.py`, `results/s5_incremental_change.json`)

60-file fixture (core + 45 filler), indexed once, then re-run 5 times under controlled single-file mutations.

| run | opens | notes |
|---|---|---|
| index cold | 60 | full first-time index |
| index warm, nothing changed | **0** | pure mtime fast-path skip, every file |
| index after 1 file's content changed | **1** | 1.67% of the repository |
| index after mtime-only touch (same content) | 1 | mtime differs → falls through to hash check → hash matches → 1 read to confirm, correctly re-skipped from re-embedding |
| index after reverting to original content | 1 | correctly detected as a real change again |
| `analyze()`, run 3× with nothing changed | **identical opens/dirs_walked every time** | zero incremental behavior — always full cost |

**Caveat, stated plainly:** the vector-retrieval numbers in §6.1/§6.5 measure **cost**, not **precision** — embeddings are a fast, seeded deterministic pseudo-vector (`probes.py::_deterministic_vector`), never a real model call (P0 forbids live models). This is sufficient to measure `query()`'s O(N) scan behavior (a pure function of vector *count* and *shape*, not semantic content) but by itself says nothing about whether real embeddings rank the correct file highly. §6.7 below supplies the real-embedding evidence this caveat originally deferred, for the one controlled fixture/query this investigation defines; it does not generalize the claim to arbitrary repositories or queries.

### 6.7 Live Retrieval Validation (real embeddings, user-run — `run_live_retrieval_validation.py`, `results/live_retrieval_validation.json`)

Executed by the user after P0's own investigation completed (this investigation prepared and safety-verified the script but did not execute it — see §19 for the safety proof). Real `OllamaEmbeddingClient` against the user's configured endpoint (`nomic-embed-text:latest` @ `http://localhost:11434/v1`), same S1 fixture generator, same unmodified query (`fixtures.CORE_GROUND_TRUTH["goal"]`), all four bands.

| band | files | chunks | rank | score | top5 | top10 | index elapsed (s) | retrieval elapsed (s) |
|---|---|---|---|---|---|---|---|---|
| small | 60 | 295 | **1** | 0.016393 | true | true | 4.43 | 0.038 |
| medium | 260 | 1495 | **1** | 0.016393 | true | true | 22.52 | 0.078 |
| large | 760 | 4495 | **1** | 0.016393 | true | true | 70.47 | 0.186 |
| very_large | 1510 | 8995 | **1** | 0.016393 | true | true | 167.72 | 0.338 |

`RANK_STABILITY: {small: 1, medium: 1, large: 1, very_large: 1}`. `RETRIEVAL_PRECISION_DEGRADATION: NO` (zero top5/top10 loss between consecutive bands, target found in every band, rank range spread = 0).

**Result, stated precisely:** the real embedding model ranked `core/billing/invoice_impl.py` **first** at every band, with **zero rank movement** across a 25× increase in unrelated repository breadth. More strongly than the rank alone: at every band, **all 10** of the top-10 returned chunks belonged to the 4-file ground-truth core package (`invoice_impl.py` ×3 chunks, `invoice_interface.py` ×3, `caller.py` ×2, `test_invoice.py` ×2) — not one of the up to 8,995 unrelated filler chunks displaced a relevant chunk from the top 10 at any band. The identical score (0.016393... = exactly `1/(60+1)`, the RRF formula's own rank-1 constant) across all four bands is a mechanical property of Reciprocal Rank Fusion, not evidence of anything — RRF scores a rank position, not a magnitude, so an unchanged rank-1 finish produces an unchanged score by construction; this is *why* §8/§13's own instruction to judge degradation on rank/top-K rather than raw score was correct.

**Scope of this result, stated precisely (per the task that requested it): this validates real embedding retrieval ranking only**, for one controlled fixture and one query, against Kriya's own deterministically-generated filler content (realistic-shaped but synthetic). It does not validate Developer coding quality, F8 model behavior, MODEL-001, end-to-end CTX correctness, or context-budget correctness, and it does not by itself prove retrieval precision holds for an arbitrary real-world repository, an arbitrary goal phrasing, or a query whose vocabulary overlaps less cleanly with its target file's real content than this fixture's goal text does with `invoice_impl.py`. It resolves the one gap this investigation could not close deterministically (§4's semantic-retrieval caveat) for the case it actually tests, and gives no reason, on this evidence, to expect the F11 degradation mechanism (§5, §6.5 — real and reproducible under an adversarially-assigned low score) to be triggered by this specific retriever's real behavior on repository-breadth growth alone.

**Complementary cold-index timing note:** this real run's `index_elapsed_s` (4.43s → 167.72s, 25.17× files → ~37.9× time) includes real embedding-call network/inference latency, unlike §6.1/§8's deterministic-only measurement (which isolated Kriya's own overhead from embedding latency by construction, and found 247× time for 25× files). The two are not in tension: the real numbers here are consistent with a large, roughly per-chunk-proportional embedding-call cost component (chunks grew 30.49×, index time grew 37.9× — close to proportional) that, at these band sizes, partially masks the smaller but still-present O(N²) `clear_file()` scan cost identified in §8. That scan cost does not depend on embedding latency and would continue to compound at larger repository sizes regardless of embedding-call speed — the §8 root-cause finding and fix recommendation stand unchanged. Retrieval-only elapsed time (0.038s → 0.338s, ~8.8× for 30.49× chunks) is consistent with a roughly-constant real network round-trip floor (~28ms, estimated from the two extreme bands) plus a small linear-in-chunk-count scan cost (~34µs/chunk) — the same O(N) scan §4/§8 identify by direct code reading, now corroborated with real timing data once the fixed per-query network latency is accounted for.

---

## 7. Context-Budget Results

Summarized from §6.2/§6.4/§6.5. In Kriya's own token units (`estimate_tokens`, chars//4):

- A single file needs a budget roughly ≥ its own full-content token size to remain at "full" tier; below that, `build_code_context` degrades to "skeleton" (still elides bodies) then "signatures" — there is **no intermediate tier that keeps one member's body while eliding others in the same file**. Degradation granularity is **per-file**, not **per-member**.
- The 24,000-char `_brownfield_owner_contract_block` cap is independent of `build_code_context`'s own budget allocator, applies only to known target files on attempt 1, is consumed in Architect-list order (not relevance order), and truncates mid-file rather than omitting whole files cleanly.
- Score-aware degradation (attempt 1 only) can correctly protect a high-scored relevant file under pressure, or — if the upstream ranker under-scores the truly relevant file — actively drop it first. The mechanism is doing exactly what it's designed to do; the risk is entirely a function of upstream ranking accuracy, which P0 cannot evaluate without live embeddings.

---

## 8. Performance / Scalability Baseline

See §6.1 (S1) for the full breadth table and §6.6 (S5) for the incremental table. Headline numbers:

- **Cold `index_repository()` is super-linear in file count**: 25.17× files → 247.31× time. **Root cause, confirmed via `EXPLAIN QUERY PLAN`:** `dependency_graph.db`'s `relations` table has indexes on `source`/`target` (`graph.py:122-123`) but **not** on `source_file` (added later via bare `ALTER TABLE`, `graph.py:114`, no accompanying index). `DependencyGraph.clear_file()` (`graph.py:155-182`), called unconditionally at the top of every `index_file()` call (`graph.py:189`) — including for a file being indexed for the very first time, where the delete is guaranteed to affect zero rows — issues `DELETE FROM relations WHERE source_file = ? OR (...)`, which `EXPLAIN QUERY PLAN` confirms as `SCAN relations` (full table scan) against a table whose row count grows with every file already indexed. Net effect: indexing file *k* of *N* pays a scan proportional to the relations accumulated by files 1..k-1, summing to O(N²) superimposed on the otherwise-linear parse/chunk/embed work. This is a concrete, reproducible, named production inefficiency (not fixed, per P0 scope).
- `query_hybrid`'s vector half scans **every** row of `vector_chunks` on every call (`vector.py:384-425`, no `WHERE` clause, no ANN index) — confirmed both by code reading and by the near-1:1 empirical ratio between chunk-count growth (30.49×) and query-time growth (33.32×) in §6.1.
- `build_code_context` itself is **LOCAL** — flat cost regardless of repository breadth, given a fixed matched/related file set (§6.1). This is the one component of the pipeline that already satisfies the stated performance principle.
- `analyze()` pays full-repository `os.walk` cost on every single call, with **zero** caching, confirmed identical across 3 consecutive no-op runs (§6.6).
- Skeletonization is roughly linear in file size (§6.2, F20 PREVENTED).

---

## 9. Repeated-Work Analysis

| Transition | Repeated? | Classification |
|---|---|---|
| Repository Analysis → Planner/Architect | `analyze()` runs once per `generate` call (not per retry) — no *intra-run* repetition, but the *whole* repository walk is unconditional every run regardless of task locality | CHEAP/ACCEPTABLE per-call, LIKELY_REDUNDANT across successive runs on an unchanged repo (no caching exists to avoid it) |
| Planner → Architect | shares `convention_prompt` verbatim, no re-derivation | REQUIRED_FOR_CORRECTNESS (shared context is the correct behavior) |
| Architect → Developer attempt 1 | `build_code_context` called once (`workflow.py:1657`), `_brownfield_owner_contract_block` called once (`attempt.py`) | REQUIRED_FOR_CORRECTNESS |
| failed attempt → retry | `build_code_context(ctx.matched_files, ctx.related_files, ...)` re-executed from scratch (disk reads + skeletonization) on **every** retry, for the **same** `matched_files`/`related_files` list computed once before attempt 1 | **LIKELY_REDUNDANT for any matched/related file not itself modified this run** — CACHEABLE (skeletonization is a pure function of file content + tier + budget outcome; content is unchanged unless that specific file is also a Developer write target) |
| candidate mutation → verification/recovery | `PolymorphicValidator` re-runs compile/test fresh each attempt | REQUIRED_FOR_CORRECTNESS (compile/test results cannot be cached across a code change) |
| subtask N → subtask N+1 (default pipeline) | not observed — each `run_generation_workflow` invocation is independent; no cross-invocation cache found for `matched_files`/`graph_rag_context` | UNKNOWN whether milestone-decomposition callers reuse anything here — out of P0's traced critical path (see §15 for `WorkflowController`'s own, separate, opt-in handling) |

---

## 10. Incremental-Change Analysis

§6.6 (S5) is definitive and quantified: **`index_repository()` reprocesses only the changed file(s)** (1 of 60, 1.67%, on a single content mutation), confirming real incremental behavior at the Graph RAG/vector-index layer. **`analyze()` reprocesses the entire repository unconditionally on every call**, with no dependency on whether anything changed — confirmed by 3 consecutive identical-cost no-op runs. Since `analyze()`'s output (`RepositoryModel`) feeds the Planner/Architect prompt on every single `generate` invocation, this is a real, demonstrated F14/F17 instance, not merely a theoretical possibility. The Java skeleton-tier cross-check (§6.2, §10 note) confirms the body-elision behavior is language-independent, so this finding is not a Python-fixture artifact.

---

## 11. LLM-Context Amplification Analysis

Deterministic-only, no live model runs, per P0 constraint:

- `convention_prompt` (skills + graph_rag_context + learned_rag) is built **once** and shared verbatim across Planner, Architect, and Developer attempt 1 — no per-stage duplication of that block.
- On retry, the fix-analysis/error-source addition to the prompt is bounded (scoped to implicated files, capped retry-package size via `max_chars = max(6000, min(48000, context_window*1.5))`, `attempt.py:259`) — not unbounded growth (F18 PARTIAL, not DEMONSTRATED as unbounded).
- However, the **graph-RAG portion** of the prompt is **fully recomputed** (not cached, not diffed) on every retry — meaning a multi-retry run pays the disk-read + skeletonization cost of the same matched/related files repeatedly, even though the resulting *content* is very often byte-identical to the previous attempt's. This is real, measured waste (§9), even though it does not manifest as *prompt-size* growth (the token *count* stays the same each time — it's compute waste, not token-budget waste).
- Relevant-vs-irrelevant context ratio at the ground-truth level is directly measurable only where ground truth is known (the core fixture): §6.3/§6.4/§6.5 provide exact, reproducible relevant/irrelevant-survival outcomes under real production code.

---

## 12. Observed Correctness Failure Boundary

Demonstrated, reproducible, with real production code and known ground truth:

1. A brownfield multi-file repair whose combined target-file size exceeds 24,000 chars silently loses exact-source guarantees for later-ordered files entirely, and can truncate an earlier file's exact source before reaching its relevant method (§6.4).
2. Any file that must degrade below "full" tier (matched/related files under budget pressure, or a target file relying on the Graph RAG context rather than the owner-contract) loses **every** method body in that file, not just the irrelevant ones — degradation is per-file, not per-member (§6.2, §5 F3).
3. A retrieval ranker that under-scores the genuinely relevant file can cause that file to be the first to lose its body under budget pressure, precisely because the score-aware degradation mechanism is working as designed (§6.5, demonstrated with an adversarially-assigned score). The live-validated real embedding model (§6.7) did **not** produce an under-score for the one fixture/query tested — it ranked the relevant file 1st at every band — so this remains a real, reproducible mechanism rather than an observed real-world trigger; whether a real ranker under-scores a relevant file in practice is repository/query-dependent and not settled by one fixture.
4. Two different contents of the same file (stale workspace snapshot in the Graph RAG block, fresh worktree content in the retry-injection block) can appear in the same Developer prompt when a file is both Graph-RAG-matched and already written this run (§5, F9 — demonstrated by direct code reading).
5. Python's dependency graph does not represent inheritance/interface implementation at all — a change to an interface's contract has no automated signal connecting it to its Python implementations via the graph layer (§6.3; Java is materially better here, per the schema and `_parse_java`, not independently re-probed for the Java case in P0).

---

## 13. Observed Scalability Failure Boundary

1. Cold (first-time) repository indexing is super-linear in repository size, root-caused to a missing SQL index (§8) — real for any first `kriya analyze` or opt-in auto-index on a large, previously-unindexed repository.
2. Vector retrieval cost scales with **total indexed chunk count**, not relevant-code size, on every single `query_hybrid` call — no ANN index, full table scan every time (§4/§8).
3. `analyze()` pays full-repository traversal cost on every `generate` invocation regardless of task locality or whether the repository has changed since the last call (§6.6/§10).
4. `build_code_context` is repeated in full (disk reads + skeletonization) on every retry for the same input file list, with no cross-call cache (§9).
5. **Not** a scalability failure: `build_code_context`'s cost given a fixed matched/related set is flat across a 25× repository-breadth increase (§6.1) — the final context-assembly step already respects the "cost should track relevant-code size" principle; the failure boundary sits entirely upstream of it, in discovery/indexing/retrieval.

---

## 14. Existing Architecture Strengths

- Structural (not line-based) skeletonization for Python and the C-family (brace-depth, comment/string-safe) is a real, load-bearing correctness mechanism — declarations are never split mid-boundary.
- Real per-model token-budget scaling (`_reserve_graph_context_budget`/`_reserve_sibling_content_budget`), addressing a documented real production incident.
- Real, working mtime+hash incremental indexing at the Graph RAG/vector-index layer, confirmed empirically to reprocess only what changed.
- Real RRF hybrid fusion combining structural (graph BFS with relation-type/hop weighting) and semantic (vector) signals, with a genuine non-silent, never-more-than-designed budget allocator.
- `build_code_context`'s own cost is already decoupled from total repository size, given its inputs — the "shape" of a correct architecture already exists at this one layer; the gap is entirely in what feeds it and how often it's re-run.
- A materially more principled, provenance-tracking, non-silent-omission context-assembly component (`ContextOrchestrator`/`ContextPackage`) already exists in the codebase — it is simply not wired into the default pipeline (§1, §15).
- `dependency_invalidation.py`'s `dependent_closure` gives a real, small, correct transitive-invalidation primitive already in production use for the retry-package cache.

---

## 15. Minimum Missing Capabilities

Evidence-backed, in priority order:

1. **A member-level (not file-level) exact-source/skeleton mechanism.** Every existing skeletonization tier is per-file; there is no way to retain one method's body while eliding its siblings in the same file. `extract_java_members`/`_python_declaration_ranges` already provide the structural addressing needed — the missing piece is a *retrieval/context-construction* layer that uses member boundaries as the unit of inclusion, not the file.
2. **A cross-attempt cache for `build_code_context`'s own output** (or its per-file skeletonized components), keyed on (filepath, content-hash, tier) — directly addresses F13/F15/F18's compute waste without changing any correctness behavior.
3. **An indexed `relations.source_file` column** — directly resolves the F19/super-linear cold-index finding; this is the single highest-leverage, lowest-risk fix identified in the entire investigation (a schema/query change, not an architecture change).
4. **Python inheritance/interface relation extraction in `_parse_python`**, matching what `_parse_java` already does for `extends`/`implements`.
5. **Unification (or at least reconciliation) of the workspace-path vs. worktree-path context sources** to close F9/F10 — either always read from the worktree once it exists, or explicitly track which representation of a file is "current" and never present two representations of the same file in one prompt.
6. **A relevance-independent floor for known target files** in `build_code_context`'s score-aware degradation — currently a target file can still lose its body purely because the retrieval ranker under-scored it (F11); `_brownfield_owner_contract_block` already solves this for `known_target_files` specifically, but only within its own 24,000-char cap and only on attempt 1.

---

## 16. Is Hierarchical Context Justified?

**Partially, and only for specific levels — not as a wholesale redesign.**

- `Repository → Module → Package` levels: **not justified by P0 evidence.** No failure mode traced in this investigation was caused by a missing module/package-level summary; `analyze()`'s facts (languages, frameworks, architecture signature) already serve the one place that level of knowledge is actually consumed (Planner/Architect scene-setting), and no evidence suggests a coarser or finer aggregation would have changed any outcome.
- `File → Type/Class` level: **already effectively EXISTS** via the skeleton/signatures tiers (a real, structural, per-file-then-per-declaration representation) — not missing, just capped at 3 tiers with no mid-tier "some members full, others skeletonized."
- `Type/Class → Member → exact current source` level: **justified, directly evidenced.** This is the one level where §6.2/§6.4/§7 show a real, repeatable gap: today's architecture cannot retain one member's exact source while eliding its siblings within the same file. This is the most defensible, narrowly-scoped hierarchical addition the evidence supports — not a full 6-level hierarchy, one additional level of resolution below "file."
- Upward knowledge accumulation (`member facts → file → package → module → repository`): no evidence found that this direction is needed; every demonstrated failure was a *downward retrieval* problem (goal → member → exact source), not an *upward summarization* problem.

---

## 17. Is Incremental/Reusable Repository Intelligence Justified?

**Yes, for two specific, evidenced gaps — not a general "cache everything" mandate.**

1. **`analyze()`'s own repository walk** has zero incremental behavior today and is paid on every single `generate` call — this is the most unambiguous, cleanly-isolated case for incremental reuse: `RepositoryModel`'s facts (languages/frameworks/architecture signature) change only when the repository's *shape* changes, not on every invocation. `index_repository()`'s own mtime+hash mechanism (already built, already working, §6.6) is the obvious, in-codebase pattern to extend to `analyze()` rather than inventing a new one.
2. **`build_code_context`'s per-attempt recomputation** (§9) — reusable at the per-file, per-(content-hash, tier) granularity; nothing here requires new infrastructure beyond what `_reserve_graph_context_budget`'s own `skel_cache` local dict already demonstrates as a pattern, just persisted across the retry loop's lifetime instead of scoped to one call.

Not justified by P0 evidence: a general repository-wide "cache every artifact" layer, a new persistent structural store beyond what `dependency_graph.db`/`vector_index.db` already are, or a rebuild of Graph RAG's retrieval algorithm.

---

## 18. P1 Recommendation

Responsibilities, not a monolith. Every item states its disposition and the actual existing Kriya owner where reuse applies.

| Responsibility | Disposition | Owner / rationale |
|---|---|---|
| Member-level exact-source/context unit | **NEW COMPONENT JUSTIFIED**, built on existing structural extraction | `extract_java_members` (`java_members.py`) + `_python_declaration_ranges` (`context_budget.py`) already provide addressing; the new piece is a retrieval/assembly layer that treats a member (not a file) as the unit `build_code_context` degrades — genuinely new, but small, and reuses 100% of the existing parsing. |
| Cross-attempt context cache | **EXTEND EXISTING** | `context_budget.py`'s own `skel_cache` local-dict pattern, lifted to persist across `AttemptContext`'s lifetime (already exists as an object, `attempt.py:393`). No new storage layer needed — content-hash keying already exists via `graph.get_cached_hash`. |
| Indexed `relations.source_file` | **EXTEND EXISTING** | `graph.py:_init_db` — add one `CREATE INDEX IF NOT EXISTS`. Lowest-risk, highest-leverage item in this whole report. |
| Python inheritance/interface relation extraction | **EXTEND EXISTING** | `_parse_python` (`graph.py:505-561`, not read in full detail in P0 beyond confirming the gap — the AST module is already imported and used for other symbol extraction in the same function), mirroring `_parse_java`'s already-working `extends`/`implements` handling. |
| `analyze()` incremental reuse | **EXTEND EXISTING** | `index_repository()`'s own mtime/hash mechanism (`analyzer.py:761-784`) is the in-codebase pattern; `analyze()` currently has none. |
| Workspace-path/worktree-path reconciliation | **EXTEND EXISTING** | `_target_exists()` (`attempt.py:113-116`) already checks both roots — the fix is in `build_code_context`'s own call sites choosing a single source of truth once the worktree exists, not a new component. |
| Relevance-independent floor for known target files | **EXTEND EXISTING** | `_brownfield_owner_contract_block` already does exactly this for one specific call path; the gap is its own 24,000-char cap (item below) and that it isn't the general mechanism `build_code_context`'s score-aware degradation uses. |
| Non-silent, ordered, relevance-first truncation for the owner-contract cap | **EXTEND EXISTING** | `_brownfield_owner_contract_block` (`attempt.py:119-149`) — order by relevance/score instead of Architect-list order, and report omissions explicitly (mirroring `ContextOrchestrator`'s own `omitted[]` convention, item below) rather than silently truncating mid-file. |
| Context provenance / non-silent omission tracking for the default pipeline | **REUSE EXISTING** | `context_orchestrator.py`/`context_package.py` already implement exactly this (`ContextItem`, `make_omitted_entry`) — currently reachable only via the opt-in, default-off `WorkflowController`. Wiring the default `run_generation_workflow` path through (or borrowing) this same mechanism is reuse, not new design. |
| Graph RAG retrieval algorithm (RRF + weighted BFS) | **DO_NOT_BUILD / REUSE AS-IS** | No evidence justifies replacement — demonstrated reusable foundations, per §4's classification; retrieval correctness/scalability still need the targeted extensions listed there plus the live evidence in §19. |
| A new vector database / ANN index | **DO_NOT_BUILD in P1** without further evidence | The measured O(N) scan cost (§8) is real, but whether it matters in practice depends on realistic repository/chunk-count ranges not yet validated against a live embedding model — flag for live validation (§19) before committing to a new indexing technology. |
| Repository/module/package-level summarization | **DO_NOT_BUILD** | §16 — no evidence any traced failure was caused by its absence. |

---

## 19. Live Model Validation — A COMPLETE, B remains for a future pass

P0 itself made zero live model or embedding calls, per the task's own constraint. Two things P0's own deterministic work could not determine; one has since been resolved by a user-run live validation, the other remains explicitly out of scope for any P0/CTX-001-adjacent deterministic pass.

**A. Real embedding retrieval precision — RESOLVED, results in §6.7.** The fake-embedding harness (§6, caveat) measured cost correctly but could not say whether a real embedding model ranks the true-relevant file highly enough to survive score-aware degradation. The user ran the prepared, safety-verified script (`spikes/ctx_001_p0/run_live_retrieval_validation.py`) against their real, configured Ollama endpoint (`nomic-embed-text:latest`) across all four S1 bands, using the unmodified `fixtures.CORE_GROUND_TRUTH["goal"]` query throughout. Result: `core/billing/invoice_impl.py` ranked **1st** at every band (60/260/760/1510 files), **100%** of the returned top-10 chunks belonged to the 4-file ground-truth package at every band, and `RETRIEVAL_PRECISION_DEGRADATION: NO`. Full table, scoring mechanics, and precise scope statement in §6.7. Raw evidence: `spikes/ctx_001_p0/results/live_retrieval_validation.json`.

**Safety proof (verified by this investigation before handing the script off, not by executing the live run itself):** `python3 spikes/ctx_001_p0/run_live_retrieval_validation.py --safety-check` — zero network, zero embedding calls, AST-based import scan of the script's own source, confirms `llm.base_url` is forced unreachable, confirms the auto-skill directory that skips `index_repository()`'s own single completion/chat LLM-call site is pre-created for every band. This check passed; the live-embedding run itself was executed by the user, not by this investigation.

**B. Whether F8's "invariant loss" actually changes model output — still open, out of P0 scope.** Whether a real model, given a signatures-tier (body-elided) view of a dependency, actually produces an incorrect change vs. compensating correctly from the signature alone, is a model-capability question P0 explicitly must not answer (per the task's own Invariant 8/9 — don't use larger context or model-specific workarounds as the test). This belongs to a future `CTX-001`-adjacent live campaign that exercises the full Developer/Quality-Gates loop, not a context/retrieval-only P0.

---

## 20. Full Pytest

`FULL_PYTEST_REQUIRED: NO` — no production code was changed; nothing here requires regression validation. `spikes/ctx_001_p0/` is new, self-contained probe tooling with no changes to any file under `kriya/` or `tests/`.

---

## Acceptance Checklist

- Executable context path fully traced: §1 (yes; `context_projection.py`/`subtask_context_projection.py`/`review_context.py` characterized by role and call-graph position, not read line-by-line — see honesty note below)
- Capability map complete: §2 (yes)
- F1–F20 classified: §5 (yes, all 20)
- Graph RAG objectively assessed: §4 (yes)
- Token/context ownership established: §3, §7 (yes)
- S1–S5 fixtures/probes executed: §6 (yes, all 5, real production code, results in `spikes/ctx_001_p0/results/*.json`)
- Repository breadth / large-file / context-pressure / incremental-change measured: §6.1/§6.2/§6.5/§6.6 (yes)
- Repeated work identified: §9 (yes)
- Correctness and scalability gaps separated: §12/§13 (yes, kept in distinct sections throughout)
- Minimum missing capabilities identified: §15 (yes)
- P1 recommendation evidence-backed: §18 (yes, every row cites either a §-numbered finding or a specific file:line)
- Production code changes: **0** (confirmed — `git status` at close, §"Return" below)
- No live model run performed **by this investigation**: confirmed (§6 safety note documents a near-miss and the fix; §19-A's live retrieval run was executed by the user, against a script this investigation prepared and safety-verified via `--safety-check` — zero network/embedding calls — but did not itself execute)
- No full pytest performed by this agent: confirmed
- Live retrieval validation (§19-A): **COMPLETE** — user-executed, results incorporated at §6.7, all four S1 bands, rank 1 / zero degradation

**Honesty note on residual trace depth:** `context_projection.py` (157 lines), `subtask_context_projection.py` (133 lines), and `review_context.py` (1161 lines) were identified, their role established via `context_orchestrator.py`'s own docstring and grep-confirmed call sites (all three feed `WorkflowController`'s opt-in path or the Reviewer stage), but were not read in full line-by-line detail — the Reviewer stage runs after mutation/verification, not before, so it is outside the context-*correctness* critical path this investigation prioritized (deciding what code gets written), and the two `subtask_context_projection`/`context_projection` modules are both gated behind the same `workflow_controller.enabled=False` default already established for `ContextOrchestrator` itself. This is disclosed rather than silently omitted; a CTX-001 P1 pass on the opt-in milestone-decomposition control plane specifically should read these three in full before extending anything in that path.
