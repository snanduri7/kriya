# Python Capability Sweep (DE-01)

Every capability below was checked against current source directly this
pass (file/line cited where a specific mechanism was found), not inferred
from Java evidence and not inferred from PRV-17's single successful run
alone. Where PRV-17's real result is the only evidence, that is stated
explicitly and scoped to only the capabilities it actually exercised —
one successful Django generation does not become blanket E4 Python
support.

**Correction carried over from the prior two passes, made explicit here
because it changes several rows below:** `kriya/tools/validate.py`'s
`_pyproject_dependencies()` (line 131) and its call site inside
`_resolve_python_interpreter()` (lines 370–378) show that pyproject.toml
dependency installation is a **real, wired, working mechanism**, directly
tied to a documented fix ("PRV-17, 2026-09-03"). The prior passes' "VER-004
confirmed defect" was me reading a docstring describing the *historical*
problem this code exists to fix, as though it described current behavior
— the same evidentiary error this whole exercise exists to catch,
committed against source-code comments rather than PRV results this time.
Caught and corrected here, not silently carried forward.

| # | Capability | Implementation | Evidence | Notes |
|---|---|---|---|---|
| 1 | Repository discovery | PRESENT | E1 | `.py`→`"Python"` marker, `kriya/analyzer/analyzer.py:16` |
| 2 | Project/stack detection | PRESENT | E2 | `_detect_stack()`, real logic; PRV-17 exercised it end to end successfully |
| 3 | Build/dependency metadata recognition | PRESENT | E2 | `requirements.txt` and `pyproject.toml` both recognized |
| 4 | Dependency extraction | PRESENT | E1 | `_pyproject_dependencies()` real, parses PEP 621 `[project.dependencies]`; no dedicated unit test found this pass exercising it directly (extraction logic itself untested in isolation, though its *effect* is exercised via #5) |
| 5 | Dependency installation | **PRESENT — corrected from "DEFECTIVE" in the prior two passes** | E2 | `_resolve_python_interpreter()` wires extraction → `_ensure_project_venv()`; PRV-17's real result shows a generated `pyproject.toml` declaring Django, with `Quality Gates: PASSED` — could only pass if Django was actually installed and importable, indirect but compelling production confirmation; no dedicated unit test isolates this path |
| 6 | Environment/venv assumptions | PRESENT | E2 | Real venv-creation code; PRV-17 exercised it |
| 7 | Symbol extraction | PRESENT | E1 | stdlib `ast` module used natively in `kriya/analyzer/analyzer.py`/`graph.py` — arguably more mature in this one respect than the tree-sitter-dependent Java path, since it needs no external grammar dependency; no dedicated Python-symbol-extraction test found this pass |
| 8 | Import/dependency grounding (structural evidence for planning) | **PARTIAL** | E0/E1 | The obligation/grounding *framework* (`find_missing_grounded_production_artifacts()`, etc.) is language-neutral, but its structural-evidence *input*, `build_planning_structural_evidence()`, is documented (via `TOP-MVN-004`) as resolving Java `implements`-clause/import syntax specifically — no grep evidence this pass confirms equivalent Python resolution (e.g. Python inheritance/import shapes) exists |
| 9 | Context construction (RAG/graph/vector) | PRESENT (generic) | E1 | Language-agnostic architecture by design; no Python-specific quality test found |
| 10 | Planning | PRESENT (generic) | E2 | Prompt-based, language-agnostic; PRV-17 produced a real, sensible multi-file Django plan |
| 11 | Preservation/architecture checks | **PARTIAL** | E0/E1 | Same caveat as #8 — shares the structural-evidence dependency |
| 12 | Authorized writes | PRESENT | E4 | Fully language-neutral (path-based), identical mechanism already proven at E4 for Java |
| 13 | Static/syntax validation | **PRESENT — newly confirmed this pass, not previously credited** | E2 | `kriya/tools/validate.py` (~line 672–686): real `compile(source, f, "exec")` gate per `.py` file, structurally parallel to the Java `javac`-based check, catches real `SyntaxError`s with line numbers |
| 14 | Package/build validation (beyond syntax) | N/A / not directly applicable | — | Python has no separate "build" step analogous to Maven compile; not a gap so much as a different-shaped problem, not scored |
| 15 | Test discovery | PRESENT | E2 | Real `python -m pytest` invocation machinery in `validate.py`; PRV-17 exercised it |
| 16 | Test execution | PRESENT | E2 | Confirmed via PRV-17's real `Quality Gates: PASSED` |
| 17 | Regression detection (full-suite rerun) | PRESENT (generic) | E1 | Same `run_tests()` path as targeted; not independently exercised at scale for Python |
| 18 | Recovery attribution | PRESENT (generic) | E1 | `kriya/workflow/attribution.py`'s actual logic contains no Java-specific branching found by grep — the `.java` references present are incident-citation comments, not code logic; not specifically exercised for a Python failure-recovery cycle this pass |
| 19 | Retry/repair | PRESENT (generic) | E1 | Language-neutral orchestration, same reasoning as #17/#18 |
| 20 | Runtime verification | **PARTIAL** | E1 | `kriya/tools/service_runtime.py` explicitly anticipates `python manage.py runserver` as a real launch command (line 383, listed alongside `mvn spring-boot:run` as an equally-valid example) — a genuinely positive finding; but the PREPARE-phase (build-before-launch) evidence remains Maven/jar-specific per `TOP-RUNTIME-004`, though Python apps often need no build step at all, which may make this gap matter less in practice than it does for Java |
| 21 | Terminal correctness | PRESENT (generic) | E1 | Language-neutral |
| 22 | Safe termination | PRESENT (generic) | E2 | `kriya/tools/process.py`'s OS-level process-group lifecycle is language-neutral, already well-exercised |
| 23 | Single-package/project topology | PRESENT | E2 | PRV-17 directly demonstrated this (one Django project, 11 files) |
| 24 | Multi-package topology | **ABSENT (untested)** | E0 | Never demonstrated for Python anywhere in available evidence |

## What this changes in the main register

- `VER-004` (Python dependency/build-metadata handling, previously
  `NEEDS_IMPLEMENTATION`) is **retired as a separate defect row** — the
  defect it named does not currently exist in source. Its content is
  folded into `VER-005`.
- `VER-005` (Python end-to-end validation correctness) is upgraded to
  reflect real, multi-point confirmed capability (items 1–7, 9–10, 13,
  15–17, 21–23 above are all genuinely `PRESENT`, most at E1–E2), not the
  blanket-pessimistic picture the prior two passes carried. Disposition
  stays `NEEDS_EVIDENCE` — real capability exists across most of the
  pipeline, but no single clean, fully-confirmed E4 production pass
  exists yet (PRV-17 remains `NEEDS_REVIEW`, one scope-creep manual check
  short of that).
- Items 8/11 (structural evidence, Java-syntax-shaped) and item 24
  (multi-package topology) are the two genuinely uncertain/absent findings
  this sweep surfaces that were not previously named with this precision
  — worth carrying into any future Python-focused KRP-006/022 work as
  concrete, source-grounded starting points, not vague "Python is
  unproven" hand-waving.

## PYTHON CAPABILITY SWEEP COMPLETE: YES
