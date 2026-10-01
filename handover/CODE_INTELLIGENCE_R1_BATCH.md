# Code Intelligence R1 — autonomous 8-hour batch (owner instruction, 2026-10-01)

Saved verbatim-in-substance so a later session can resume from the file, not from memory.
Branch `feature/code-intelligence-r1` from main `eeae1ac`. Never merged in this batch.

## Authority
- add `tree-sitter`, `tree-sitter-java`, `tree-sitter-python` (production deps) and pin them in `requirements.txt`;
- download public GitHub benchmark repositories (outside the Kriya repo; record URL + SHA; never vendored;
  never needed at normal Kriya run time);
- create `kriya/code_intel/`, modify indexing/retrieval/context code, delete obsolete code only after its
  replacement is tested; coherent commits; push `feature/code-intelligence-r1`.
- Public network only for PyPI and public GitHub benchmark repos; no proprietary content sent out.
- Do not update macOS, Ollama, model tags or the production provider configuration. Do not merge.

## Stop only if
1. a change would weaken file integrity, mutation authority, containment, egress policy or verified-byte binding;
2. a new architecture is required outside the decisions below;
3. a dependency cannot be used safely/licensably or conflicts with the supported Python runtime;
4. the only way to pass tests is to weaken an invariant;
5. destructive repository/index corruption is discovered.

Lane-B discipline: reproducer/benchmark → implementation → focused tests → lint → benchmark delta → commit.
Full suite once near the end.

## Stages
1. Architecture/seam check (≤45 min): `handover/CURRENT_STATE_MATRIX.md` with the E-01…E-17 rebaseline,
   the seam map (analyze/index refresh, Planner grounding, work-unit re-query, compiler/runtime failure
   localization, Developer context packing, Reviewer surroundings × direct/enforce/milestones), a
   simplification baseline (LOC) + deletion ledger, and the `kriya/code_intel/` service boundary
   (`CodeIntelligenceService`: refresh, find_symbol, locate, get_member, search, build_context).
   Workflow code must not know tree-sitter/SQLite internals. No engine convergence.
2. Dependencies + benchmark harness: pinned deps, smoke test parsing one Java and one Python file;
   benchmark checkouts (Commons Lang, Spring Petclinic, Spring XML Petclinic, a medium Python project, Kriya);
   compact manifest (repo, URL, SHA, ~LOC, purpose). Two layers: `loc-N` (deterministic, no chat model,
   generator mining one/two-member commits, ~150–250 cases, categories symbol-named / behavior / error-test;
   baseline from current retrieval) and `ci-20` (small expensive end-to-end set).
3. Normalized structural symbol model (highest priority): durable id, language, kind, lookup key, path,
   raw digest, declaration/signature/body spans, parent, modifiers, annotations/decorators, parser identity,
   parse state PARSED / PARTIALLY_PARSED / UNSUPPORTED / PARSE_FAILED. Java (package, imports, classes,
   final, interfaces, records, enums, constructors, all visibilities, overloads distinguished, generics,
   multiline, fields, annotations, modifiers, nested types) and Python (modules, classes, def, async def,
   nested, methods, decorators, imports, class/module assignments, docstrings, module-qualified ids).
   Structural provenance only; JDTLS optional enrichment. Regression for E-02 and E-09; coverage on
   Commons Lang and Kriya.
4. Index correctness: E-01 (deletions from the full walk), E-17 (stale file_metadata, module chunk
   range), E-13 (explicit source/index policy, skills/knowledge not indexed as code), E-14 (normalized
   token equality, java ≠ javascript). Manifest identity: schema, tree-sitter + grammar versions,
   structural parser version, raw digests, embedding fingerprint; mismatch never silently reused.
5. BaselineIndex + CandidateOverlay (overlay shadows baseline; tombstones; raw-digest binding; compatible
   identity; discard = drop overlay; a baseline not matching the expected revision is never mutation
   authority). SQLite only — no graph DB, vector service, daemon or index server. Stop on a clean tested
   boundary if it cannot finish.
6. Deterministic retrieval first: qualified symbol, simple symbol, path, compiler file:line, stack-trace
   file:line → containing member, exact string, config/XML key, FTS/BM25 identifier, vectors as an extra
   channel. Exact evidence outranks similarity; deterministic fusion; provenance per result. No PPR, no
   mandatory LLM call, no self-confidence. loc-N: recall@1/@5 by goal type, false-confident rate, latency,
   before/after.
7. Member-level packing: T0 exact current member + header + imports + fields + ctor/signatures (path, raw
   digest, span, symbol id; the only mutation-authoritative source; never dropped; skills cannot evict it),
   T1 collaborator signatures, T2 tests, T3 XML/config/build. Record prompt tokens, prefill, gold body
   present, relevant share. Stable prefix before volatile T0/failure evidence.
   Integrate through the Stage-1 seam into 1–2 paths first: Developer known-target context and
   compiler/stack-trace → member localization. Old path may remain as fallback with a named deletion target.

## Explicitly deferred
PPR, full Java semantic resolution, mandatory JDTLS, all-role structured JSON, CI-6 LLM ambiguity resolver
(structured JSON is its prerequisite), plugin SDK, Build/LanguageAdapter, RunService, engine convergence,
GUI, Ruby, Gradle, macOS/Ollama updates, model qualification changes, giant manual curation, new governance.

## Gates and git
Focused tests + benchmark + targeted suite + ruff/pylint per slice; destructive index/schema behavior also
gets stale/deletion, transactional-failure and mutation tests. No evidence dossiers, no evidence-only
commits. Near the end: full pytest, ruff, pylint, `doctor --production`. Commits by slice; push after green
commits and at the end.

## Final report (one, when the owner returns)
1 commits pushed; 2 dependency versions; 3 benchmark repos + SHAs; 4 seam-map conclusion; 5 parser coverage
before/after; 6 E-01/E-17/E-13/E-14; 7 index/overlay status; 8 retrieval metrics before/after; 9 member
packing; 10 production integration; 11 code deleted/obsoleted; 12 tests/lint/suite/doctor; 13 next task;
14 true blockers.
