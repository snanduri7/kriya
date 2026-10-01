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
