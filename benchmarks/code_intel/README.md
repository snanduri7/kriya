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
- `loc_bench.py` — the loc-N localization benchmark (generator + evaluator); see its docstring.

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
