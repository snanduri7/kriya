# Current state matrix — Code Intelligence R1 (slice 1 merged to main as `26ecc51`; slice 2 on `feature/code-intelligence-r1-integration`)

Status words: MEASURED / TRACED / INFERRED (handover/ENGINEERING_RULES.md). This file is updated as the
branch progresses; the "Action/result" column records what this branch did.

## 1. E-01…E-17 rebaseline at `eeae1ac` (review snapshot was `6e0d711`)

Retrieval/indexing commits since the snapshot: `c57d21f` (EMBEDDING-CONTRACT-001), `4a2c1ef`, `b029e7e`, `d31d25c`.

| Finding | Status at eeae1ac | Evidence | Action / result on this branch |
|---|---|---|---|
| E-01 `analyze --changed` deletes unchanged files | STILL OPEN | TRACED: deletion set = change-filtered `files_to_index` (analyzer.py deleted-files loop) | FIXED slice 2: deletions = (every path any index layer holds) − full walk; regression + mutation |
| E-02 Java symbols miss 30–60 % | STILL OPEN | TRACED: regex `_parse_java` (graph.py), fake spans `idx+5`/`idx+2`; no tree-sitter | FIXED slice 1: graph reads the tree-sitter model (methods+ctors 36.2 %→100 % on commons-lang) |
| E-03 graph adds noise | PARTIALLY CLOSED | PRD-027 corroborated seeds narrow the walk; extraction still regex | improved indirectly by E-02 |
| E-04 target member dropped by per-file packing | STILL OPEN | packing tiers unchanged | FIXED: slice 4 service API (T0 keeps the gold body 100 % vs 0 %); slice 2 `d1a05ea` wired it into the Developer known-target seam - exact current T0 with its own budget (skills cannot evict it) + package (header, sibling signatures, T1-T3) |
| E-05 failed query embedding → zero vector | CLOSED | `c57d21f` | — |
| E-06 no scale to 300k LOC | STILL OPEN | brute-force NumPy scan; MEASURED at 26ecc51: 10k/30k/45k chunks = 2.2/6.5/10.2 s per query, 337/1011/1517 MB | FIXED slice 2 `7324f41`: one cached float32 matrix per index generation - 45k chunks 609 ms first / 5.1 ms warm, 143 MB (= the matrix); structural locate p95 ≤ 42 ms (commons-lang), ≤ 61 ms fused (Kriya) |
| E-07 Ruby | DEFERRED | out of scope by owner decision | — |
| E-08 Spring XML namespaces | STILL OPEN | MEASURED: graph + chunker strip xmlns, fail on prefixed elements (2 of 5 petclinic Spring XML files), 12 beans / 0 refs / line-1 spans | FIXED slice 2 `1c70da0`/`5ac5913`: expat namespace-aware config model (beans, refs incl. p:/c:, scans, imports, profiles; properties/YAML keys) in graph, structural index, chunks and T3: 20 beans with real spans, 6 refs, 7/7 Spring XML files PARSED |
| E-09 `async def` not indexed | STILL OPEN | TRACED: graph.py `_parse_python` has no `AsyncFunctionDef` | FIXED slice 1 (`async def` in the graph; model carries `async`) |
| E-10 signatures tier drops fields | STILL OPEN | context_budget.py unchanged | ADDRESSED for the Developer target: the known-target package carries the enclosing fields (referenced first), constructors and annotations; generic graph-context tiers unchanged |
| E-11 huge chunks vs 2048 context | PARTIALLY CLOSED | truncation fixed by segmentation (`c57d21f`); chunk size/header bloat remain | later |
| E-12 index identity / refresh / relative memory | PARTIALLY CLOSED | embedding fingerprint carries versions; no parser identity; `_ensure_repository_indexed` only indexes an EMPTY graph (workflow.py) | FIXED for localization: graph manifest binds schema + tree-sitter + grammar + parser (+ PyYAML) versions + embedding fingerprint + repository revision; retrieval localizes over `current_view()` (index + in-memory overlay of files changed since indexing), never stale rows; vectors still refresh only with `kriya analyze` |
| E-13 `skills/` indexed as code | STILL OPEN | index walk filters by `.gitignore` only | FIXED slice 2: skill-package marker + configured skills/memory roots + `.kriya` excluded; a code dir named `skills` stays indexed |
| E-14 skill tag substring match | STILL OPEN | skill.py `tag in dep` | FIXED slice 2: `mentions_term` token-run equality at every skill-relevance site (fact_match, goal/name, library/version, manifest channel, skill extraction) |
| E-15 token estimates | STILL OPEN | `len//4`, 2.5 bytes/token | later |
| E-16 | — | no such finding in the register | — |
| E-17 bookkeeping | STILL OPEN | `file_metadata` rows never deleted; module chunk line range wrong | FIXED slice 2: `remove_file` deletes vectors+lexical+cache in one transaction; module chunk records first..last line |

## 2. Code Intelligence seam map (TRACED)

Enforce (`WorkflowController._run_structured_enforce`) and milestones (`MilestoneSequenceRunner`) do not
retrieve or pack context themselves: every work unit/subtask calls `WorkflowEngine.run_generation_workflow`
→ `attempt.run_attempt`. So "direct / enforce / milestones" share one implementation for every consumer
below, except enforce Planner grounding.

| Consumer | Production function(s) | direct | enforce | milestones |
|---|---|---|---|---|
| analyze / index refresh | `RepositoryAnalyzer.index_repository` (cli `analyze`; `workflow._ensure_repository_indexed` only when the graph is EMPTY) | same | same | same |
| Planner grounding | direct: `retrieve_graph_context` → `fit_planner_request`; enforce: `workflow_controller.build_planning_structural_evidence` (in-memory `DependencyGraph`) | graph_retrieval | own in-memory graph | via direct |
| work-unit re-query | `retrieve_graph_context` once per `run_generation_workflow` (workflow.py stage 3) | yes | per subtask | per milestone |
| compiler/runtime failure → member | `failure_grounding`/`attribution` give file:line → `context_source.resolve_member_hints_from_failure_location` → `member_boundaries_for` | shared | shared | shared |
| Developer context packing | `context_budget.build_known_target_context` / `build_code_context(_package)`; member ranges via `member_boundaries_for`; edit windows `edit_capability` via `member_boundaries_for` | shared | shared | shared |
| Reviewer surroundings | `review_context` (direct `extract_java_members` + `DependencyGraph.get_neighborhood`) | shared | shared | shared |
| Investigation verbs | `investigation.py`: `member_boundaries_for`, `DependencyGraph.find_symbol_locations`, `query_hybrid` | shared | shared | shared |
| Mutation-region authority | `semantic_region_authority` (direct `extract_java_members`) | shared | shared | shared |

**Conclusion.** One shared seam already exists for member structure: `member_boundaries_for` →
`language_adapters` (PRD-028 registry). It feeds Developer known-target packing, failure-line → member
localization, edit-capability windows and investigation. Java boundaries there come from the regex
`java_members.extract_java_members`, which Reviewer and region authority also call directly. The batch
therefore integrates by making `kriya/code_intel` the structural backend behind that seam (and behind
`extract_java_members`), not by wiring five callers. Symbol lookup (graph `symbols` table) is the second
seam (`DependencyGraph._parse_java` / `find_symbol_locations`). No engine convergence is needed.

## 3. Simplification baseline (MEASURED at `eeae1ac`)

| Measure | Value |
|---|---|
| production `kriya/` Python LOC (git-tracked) | 121,175 |
| workflow.py | 5,861 |
| attempt.py | 10,098 |
| workflow_controller.py | 6,739 |
| milestones.py | 1,721 |
| AppConfig leaf fields / booleans | 206 / 32 |

### Deletion ledger (this branch)

| Item | Replaced by | Status |
|---|---|---|
| `graph.py::_parse_java` regex parser (class/method/field/import regexes, line scan, fake `idx+5`/`idx+2` spans; ~140 lines) | `kriya/code_intel/parsing.py` structural model | DELETED (slice 1) |
| `graph.py` import of `JAVA_METHOD_SIGNATURE_CORE` | — | DELETED (slice 1) |
| substring tag matching (`tag in dep`, `tag in goal`, `lib in name`) at 5 sites | `skill.mentions_term` | REPLACED (slice 2) |
| analyzer's file-cache-only deletion loop | union of index layers − full walk | REPLACED (slice 2) |
| `java_members.py` regex/brace-depth scanner (`_DECL_RE`, `_annotations_for`, `_matching_close_brace_line`, type/annotation regexes; ~230 lines) | structural model | DELETED (slice 4) |
| `java_member_boundaries` primary-type-only limit | every type's methods/constructors (`Outer.Inner.m`) | REPLACED (slice 4) |
| `analyzer.chunk_file_with_metadata_headers` Java regex chunker (JAVA_METHOD_SIGNATURE_CORE line match + brace counting; ~70 lines) | `_structural_java_chunks` (every method/ctor of every type, with Javadoc) | DELETED (slice 2 `5ac5913`) |
| `analyzer` Spring XML chunker and `graph._parse_xml` (xmlns stripping + ElementTree + one-line regex fallback) | namespace-aware config model | REPLACED (slice 2 `1c70da0`, `5ac5913`) |
| legacy hybrid leg (`query_hybrid`) as the candidate generator | fused `CodeIntelligenceService.locate` (+ vector channel) in `retrieve_graph_context` | KEPT AS BOUNDED FALLBACK (slice 2 `0788114`): whole only without a structural index; otherwise only for files the structural index does not cover, in the similarity tier, reported per run (`retrieval.code_intelligence.legacy_only_files`). **Removal criterion (all must hold):** (1) fused retrieval production-wired - DONE; (2) no symbol/path/error regression vs the legacy leg on loc-N - DONE (every category better on all 5 repos); (3) behavior category materially improves - DONE on Kriya (0.275→0.625) and httpx (0.316→0.421), not on commons-lang (0.051→0.141, still low); (4) Developer receives T0 reliably - DONE (`d1a05ea`); (5) relevant integration tests green - DONE; (6) `legacy_only_files` telemetry from real runs shows the leg adds no value - NOT YET (needs production run history; build files/docs/other languages are exactly what it still contributes). Delete when (6) holds or when those file kinds get structure. |
| `graph.find_java_main_class` line regexes | structural model (top-level types + main signature) | NAMED DELETION TARGET |

## 4. Code Intelligence boundary

`kriya/code_intel/` — workflow code imports only the service/model, never tree-sitter or SQLite internals.

```
CodeIntelligenceService
    refresh(paths=None)            # structural index refresh, raw-digest bound
    find_symbol(name)              # qualified or simple, exact
    locate(query)                  # deterministic channels, fused, provenance per hit
    get_member(symbol_id)          # exact current bytes, digest-verified
    search(text)                   # FTS identifier channel
    build_context(targets, budget) # T0..T3 member-level package
```
