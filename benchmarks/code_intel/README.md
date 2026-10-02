# Code Intelligence benchmarks

Benchmark repositories are **test data**, cloned outside the Kriya repository (default `~/kriya-bench/`),
never vendored and never needed when Kriya runs. Re-create them at the pinned commits:

```bash
mkdir -p ~/kriya-bench && cd ~/kriya-bench
git clone https://github.com/apache/commons-lang.git && git -C commons-lang checkout 4ee346e59eecccdaefbdd74ac53698da4a1f348a
git clone https://github.com/spring-projects/spring-petclinic.git && git -C spring-petclinic checkout 500158f732419217507c7656904b8e6aa1bcc0d6
git clone https://github.com/spring-petclinic/spring-framework-petclinic.git && git -C spring-framework-petclinic checkout 09351b3ee0bd5aec2d480c0280e84700978f56d3
git clone https://github.com/encode/httpx.git && git -C httpx checkout b5addb64f0161ff6bfe94c124ef76f6a1fba5254
```

## Manifest

| Repository | URL | Commit | ~LOC | Purpose |
|---|---|---|---|---|
| Apache Commons Lang | https://github.com/apache/commons-lang | `4ee346e59eecccdaefbdd74ac53698da4a1f348a` | 208k Java | large Java library: parser coverage, overloads, generics; loc-N mining (10k commits) |
| Spring Petclinic | https://github.com/spring-projects/spring-petclinic | `500158f732419217507c7656904b8e6aa1bcc0d6` | 4.3k Java | Spring Boot (annotations, JPA) |
| Spring Framework Petclinic | https://github.com/spring-petclinic/spring-framework-petclinic | `09351b3ee0bd5aec2d480c0280e84700978f56d3` | 4.3k Java + 9 XML | Spring XML configuration |
| httpx | https://github.com/encode/httpx | `b5addb64f0161ff6bfe94c124ef76f6a1fba5254` | 18k Python | medium Python project, 212 `async def` |
| Kriya | this repository | branch HEAD | 121k Python | Python regressions (2,140 `async def`) |

## Tools

- `coverage.py <repo>...` — structural coverage of the tree-sitter model vs the pre-R1 regex extractors; no model calls.
- `loc_bench.py` — loc-N, the cheap localization benchmark (generator + evaluator; `--index` adds the vector
  channel with the configured local embedding model); see its docstring. ci-20 is the separate, expensive
  end-to-end benchmark.
- `pack_bench.py` — member packing vs the old per-file packer.
- `vector_bench.py` — E-06 vector query cost at 10k/30k/45k chunks.
- `ci6_bench.py` — the CI-6 ambiguity-only model call, live (production config and model).

## Structural coverage (MEASURED, 2026-10-01)

Ground truth = the tree-sitter structural model. "Graph" = what `DependencyGraph` stores.

| Repo | Graph types before → after | Graph methods+ctors before → after | `java_members` vs model (primary type) |
|---|---|---|---|
| commons-lang | 570/1139 (50.0 %) → 100 % | 3954/10933 (36.2 %) → 100 % | 9293/9361 (99.3 %) |
| spring-petclinic | 44/50 (88.0 %) → 100 % | 90/195 (46.2 %) → 100 % | 100 % |
| spring-framework-petclinic | 55/58 (94.8 %) → 100 % | 129/236 (54.7 %) → 100 % | 100 % |
| httpx (Python functions) | 922/1134 (81.3 %) → 100 % | — | — |
| Kriya (Python functions) | 10723/12863 (83.4 %) → 100 % | — | — |

"After" is 100 % by construction (the graph now reads the model). The independent cross-check is the
older hand-written `java_members` scanner agreeing on 99.3 % of commons-lang primary-type callables.
Every Kriya and httpx file parses `PARSED`; commons-lang has one `PARTIALLY_PARSED` file.

Parse time: commons-lang 629 files in 1.1 s, Kriya 730 files in 1.6 s (tree-sitter parse + extraction).

## Dependency note

`tree-sitter` is pinned `>=0.25.2,<0.26`: 0.26.0 on CPython 3.14 segfaults at interpreter exit after
`Node.start_point`/`end_point` are read across several trees (5/5 runs; 0.25.2: 0/5). The structural
parser computes lines from byte offsets and never reads `Point`s; `tests/test_code_intel_structure.py`
guards the pin with a subprocess repro.

## loc-N localization (MEASURED, 2026-10-01, member level, HEAD of each repo)

`loc_bench.py <repo>`: mined one/two-member commits (goal = commit subject) plus 30 seeded synthetic
compiler-error / stack-trace / traceback cases per repo. "Old" = the pre-R1 retrieval lexical leg (chunks + BM25,
chunk → member by its `Method:`/`Class:` header); the vector leg needs a live embedding model and is not measured
here. commons-lang was the development set for the fusion weights; the other four are held out.

| Repo (cases) | recall@5 new / old | recall@1 new / old | symbol-named r@5 new / old | behavior r@5 new / old | error/test r@5 new / old | false-confident@1 | p95 latency |
|---|---|---|---|---|---|---|---|
| commons-lang, dev (230) | **0.643** / 0.361 | 0.465 / 0.265 | 0.826 / 0.508 | 0.145 / 0.164 | 0.721 / 0.163 | 0.135 | 29 ms |
| spring-petclinic (42) | **0.905** / 0.714 | 0.810 / 0.452 | 1.0 / 1.0 (n=1) | 0.667 / 0.556 | 0.969 / 0.750 | 0.0 | 4 ms |
| spring-framework-petclinic (38) | **0.868** / 0.553 | 0.816 / 0.342 | 1.0 / 1.0 (n=1) | 0.5 / 0.5 | 0.909 / 0.545 | 0.0 | 4 ms |
| httpx (95) | **0.674** / 0.421 | 0.484 / 0.200 | 0.765 / 0.235 | 0.410 / 0.308 | 0.897 / 0.615 | 0.011 | 7 ms |
| Kriya (119) | **0.521** / 0.353 | 0.412 / 0.059 | 0.680 / 0.360 | 0.341 / 0.341 | 0.585 / 0.358 | 0.008 | 34 ms |

Synthetic error cases (file:line, stack frames, tracebacks): recall@1 = 1.0 on all five repos (the pre-R1
failure-line → member path: 0.93–1.0 when the file is already known; the old lexical leg 0.07–0.53).
Cold structural refresh: commons-lang 3.8 s (629 files), Kriya 6.4 s; warm (no changes) 0.03 s.
"false-confident@1" = the top hit rests on exact evidence but is not gold (on commons-lang mostly a sibling
overload of the named method). Mined commit subjects that share no word with their gold files are counted, not
scored (commons-lang 51 of 251).

Not met yet: the ≥90 % recall@5 target holds for error/stack goals and the Petclinics, not for free-text
goals on large repos (commons-lang 0.64, Kriya 0.52). Behavior goals ("fix week-year formatting") are the gap;
they are where the vector leg and a structured LLM ambiguity step (CI-6, deferred) would add evidence.

## Slice 2 (MEASURED 2026-10-02, branch `feature/code-intelligence-r1-integration`)

### loc-N corpus

724 cases, generated automatically (mined one/two-member commits, subject = goal, gold re-found at HEAD;
plus 30 seeded synthetic compiler-error / stack-trace / traceback cases per repo); nothing hand-curated.
Categories: symbol-named, behavior (free text), error/test, and `hygiene` - code-tidying subjects ("Add final
modifier", "Remove unnecessary else") that name no behavior and so carry no localization signal for any
system (deterministic phrase list; a 30-case review of commons-lang "behavior" found ~25 such). Hygiene is
counted, not scored as behavior. Unlocatable subjects (no shared word with the gold files) are skipped and
counted: commons-lang 105.

| Repo | cases | symbol | behavior | error/test | hygiene |
|---|---|---|---|---|---|
| commons-lang (dev set) | 430 | 233 | 78 | 54 (30 synthetic) | 65 |
| spring-petclinic | 42 | 1 | 5 | 32 (30) | 4 |
| spring-framework-petclinic | 38 | 1 | 4 | 33 (30) | 0 |
| httpx | 95 | 17 | 38 | 39 (30) | 1 |
| Kriya (at `dbada33`) | 119 | 25 | 40 | 53 (30) | 1 |

### Localization before → after (member level; after = production fused retrieval with the vector channel)

Before = the pre-batch production retrieval leg: `query_hybrid` (vector + BM25 RRF) over the regex-chunked
index, chunk → member by header. After = `retrieve_graph_context`'s fused Code Intelligence query
(deterministic channels + vector channel over the structurally-chunked index). Same cases, same query
embeddings (nomic-embed-text, served locally). Latency is per query, warm (E-06 matrix cache).

| Repo | category | n | recall@1 after / before | recall@5 after / before | p50 / p95 ms (after) |
|---|---|---|---|---|---|
| commons-lang | all | 430 | 0.384 / 0.181 | **0.558** / 0.267 | 21.7 / 40.5 |
| commons-lang | symbol | 233 | 0.549 / 0.318 | **0.815** / 0.446 |  |
| commons-lang | behavior | 78 | 0.026 / 0.0 | **0.141** / 0.051 |  |
| commons-lang | error/test | 54 | 0.611 / 0.056 | **0.667** / 0.111 |  |
| commons-lang | hygiene | 65 | 0.031 / 0.015 | 0.046 / 0.015 |  |
| spring-petclinic | all | 42 | 0.786 / 0.381 | **0.833** / 0.643 | 6.8 / 7.7 |
| spring-petclinic | behavior | 5 | 0.2 / 0.2 | 0.2 / 0.2 |  |
| spring-petclinic | error/test | 32 | 0.938 / 0.438 | **0.969** / 0.719 |  |
| spring-framework-petclinic | all | 38 | 0.842 / 0.316 | **0.895** / 0.553 | 6.9 / 7.8 |
| spring-framework-petclinic | behavior | 4 | 0.25 / 0.25 | 0.75 / 0.75 |  |
| spring-framework-petclinic | error/test | 33 | 0.909 / 0.333 | **0.909** / 0.545 |  |
| httpx | all | 95 | 0.474 / 0.211 | **0.695** / 0.432 | 12.2 / 17.4 |
| httpx | symbol | 17 | 0.588 / 0.176 | **0.765** / 0.471 |  |
| httpx | behavior | 38 | 0.105 / 0.053 | **0.421** / 0.316 |  |
| httpx | error/test | 39 | 0.795 / 0.385 | **0.949** / 0.538 |  |
| Kriya | all | 119 | 0.454 / 0.252 | **0.664** / 0.420 | 41.8 / 60.3 |
| Kriya | symbol | 25 | 0.56 / 0.16 | **0.88** / 0.40 |  |
| Kriya | behavior | 40 | 0.25 / 0.15 | **0.625** / 0.275 |  |
| Kriya | error/test | 53 | 0.566 / 0.358 | **0.585** / 0.528 |  |

Petclinic symbol (n=1) rows omitted from the table (1.0 after on both). Synthetic error cases: recall@1 = 1.0
on every repo. Per-channel diagnosis (gold rank by deterministic / BM25 / vector / fused, every case) is in the
`--out` JSON. The weakest layer found and fixed: Java vector chunks came from the one-line method regex (only
57 % of commons-lang behavior-gold members had a chunk of their own, none carried Javadoc); structural chunks
(`5ac5913`) raised the old leg's commons-lang symbol recall@5 0.446 → 0.803 on its own. Fusion variants
(vector weight 0/10/20/30, rank decay 3/10) were compared on the dev set and confirmed on the held-out repos;
none dominates, the principled default (10 / 10) is kept. Remaining gap: commons-lang behavior (0.141) - most
of its commit subjects describe the change, not the behavior (sample in the loc-N output).

### E-06 vector query cost (`vector_bench.py`, dim 768, fingerprinted query)

| chunks | before: first / steady query, traced peak | after: first / steady, peak |
|---|---|---|
| 10,000 | 2,265 / 2,200 ms, 337 MB | 128 / 1.1 ms, 32 MB |
| 30,000 | 6,536 / 6,551 ms, 1,011 MB | 464 / 3.7 ms, 95 MB |
| 45,000 | 10,212 / 9,932 ms, 1,517 MB | 609 / 5.1 ms, 143 MB |

Real indexes in this benchmark: commons-lang 12,575 chunks, Kriya 17,981. No vector service, no new dependency.

### Spring XML / configuration (E-08, pinned spring-framework-petclinic)

Old graph parser: 12 beans (single-line `<bean id>` only), 0 bean references, failures on 2 of 5 Spring XML
files. New: 7 Spring XML files PARSED (incl. test config), 20 beans with exact spans, 6 bean references
(`ref`, `p:x-ref`, nested `<ref>`), 5 component scans (incl. `jpa:repositories`), 4 imports/placeholders, 24
properties/constructor args, 8 property keys; spring-petclinic application*.properties: 22 keys with profiles.

### CI-6: deterministic ambiguity threshold and the ambiguity-only call

Calibration on all 724 loc-N cases (fused): "clear" = top-1 rests on exact evidence and leads top-2 by ≥ 10
→ 233 cases (32 %), 4.7 % false-confident (11; 10 = a goal naming a public method whose fix is in a helper it
calls). Every synthetic error case is clear (150/150, none wrong). Flat for margins 10-40.

Live (`ci6_bench.py`, production config, pinned qwen3-coder 30B, ollama_native schema-constrained output):

| Repo | ambiguous (needs the model) | calls measured | top-1 before → after the decision | median prompt tokens | median s |
|---|---|---|---|---|---|
| commons-lang | 74.7 % | 60 | 0.367 → 0.483 | 986 | 3.6 |
| Kriya | 73.9 % | 40 | 0.25 → 0.25 | 954 | 4.5 |
| httpx | 65.3 % | 30 | 0.267 → 0.300 | 756 | 3.8 |
| spring-petclinic | 28.6 % | 10 | 0.3 → 0.3 | 1,017 | 4.0 |
| spring-framework-petclinic | 21.1 % | 8 | 0.25 → 0.375 | 1,024 | 5.8 |

Pooled: 148 calls, top-1 0.304 → 0.365. Explicit/error-driven goals (synthetic) never call the model.

## Member packing (MEASURED, `pack_bench.py`, budget 2,000 tokens, mined loc-N gold members at HEAD)

| Repo | cases (file > budget) | gold body present: old per-file packer | member packing (T0) | median tokens old / T0 | T0 over budget |
|---|---|---|---|---|---|
| commons-lang | 200 (181) | 9.5 % (0 % when file > budget) | 100 % | 40 / 697 | 12 |
| httpx | 65 (61) | 6.2 % (0 %) | 100 % | 666 / 664 | 0 |
| Kriya | 89 (85) | 4.5 % (0 %) | 100 % | 31 / 2,167 | 45 (members themselves > 2,000 tokens) |
| spring-petclinic | 12 (0) | 100 % | 100 % | 1,112 / 586 | 0 |

T0 is never trimmed: an over-budget target is reported, not cut.
