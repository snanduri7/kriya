# VAL-001 G0 Qualification: Graphify as the long-running brownfield validation repository

Status: EVIDENCE-ONLY. No Kriya or Graphify production/test code was modified to produce this
document. No Kriya generation, no local model, no Ollama, no embedding call was executed.

Kriya baseline: `10b5523` (verified against `git rev-parse HEAD` on this working tree at the time
of writing: `10b5523da43fb9f7f908f60bc66b8490766c6cc2`).

Repository under evaluation: https://github.com/Graphify-Labs/graphify

## 1. Frozen revision (Task 1)

Cloned read-only via `git clone` into a session scratchpad directory outside this repository
(`/private/tmp/.../scratchpad/graphify_qualification/repo`), never inside the Kriya working tree.
No Git hooks were installed (a plain `git clone` installs none by default, and none were added).
No build, install, or post-checkout script was run against the clone.

| Field | Value |
|---|---|
| `GRAPHIFY_BASE_COMMIT` | `26b02b5e3430e4ab85dd7e72c7b98836d8e65c48` |
| `GRAPHIFY_BRANCH` | `v8` (this is `origin/HEAD`, i.e. GitHub's configured default branch; `main` also exists in the remote but is a separate, older line of history — not relitigated here, `v8` is reported at face value as the actual default) |
| `GRAPHIFY_COMMIT_DATE` | 2026-09-16T20:41:38+01:00 |
| `GRAPHIFY_NEAREST_TAG` | `v0.9.63` (`git describe --tags` = `v0.9.63-1-g26b02b5`, i.e. HEAD is exactly 1 commit past the `v0.9.63` release tag) |

Commit count on `v8`: 1,810.

## 2. Methodology (applies to Tasks 2–4)

One deterministic script (`measure.py`, retained in the session scratchpad, not committed —
evidence-only per the STOP conditions) reads exclusively `git ls-files` output at the frozen SHA
above, classifies every tracked file, and computes physical line counts (raw `\n` count per file,
blanks and comments included — no `cloc`/`tokei` was available in this environment, so this is
disclosed as the counting rule rather than silently assumed). No content outside the frozen clone
was read; nothing was installed; nothing was executed from the repository itself.

**Classification rule (stated explicitly, not left implicit):**

- **PRODUCTION** = `graphify/**/*.py` — the actual installed Python package (87 files). This is
  the only bucket counted as "production source" for Task 2/3 purposes.
- **AUX_TOOLING** = `tools/skillgen/**/*.py`, `scripts/*.py` (5 files) — real Python, but
  developer/build tooling (a skill-bundle generator, a demo-path script), reported separately and
  **not** folded into PRODUCTION_LOC, to avoid inflating the number that the test suite's
  `graphify` import surface actually exercises.
- **TEST** = `tests/*.py` (290 files) — pytest-collected test modules.
- **TEST_FIXTURE** = `tests/fixtures/**` (114 files, any extension) — single small sample files in
  ~40 different languages, used to exercise the tree-sitter extractors. Not test code, not
  production code.
- **EXAMPLE** = `worked/**` (36 files, any extension) — demo/output corpora committed to show
  Graphify's own output on sample inputs, including a **literal excerpt of the third-party httpx
  library** (`worked/httpx/raw/*.py`) and small synthetic demo files. Excluded from production and
  test LOC entirely — this is vendored/example content by the task's own exclusion rule, not
  Graphify's own code.
- **DOC_GENERATED** = every tracked `*.md` (347 files: README/CHANGELOG/docs, plus the
  skill-bundle markdown under `graphify/skills/`, `graphify/always_on/`, and
  `tools/skillgen/expected/`, which are build artifacts of `tools/skillgen` committed for
  distribution, not hand-authored source). Excluded from LOC.
- **OTHER** = everything else tracked (json/yml/toml/lock/images/license/project-metadata files
  such as `.sln`/`.csproj`). Excluded from LOC, listed in the extension table for completeness.

No generated/vendor/build/cache/`.git` content was counted as production source. `.gitignore`
confirms the repository does not track `venv/`, `__pycache__/`, `dist/`, `build/`, `.egg-info/`,
or similar build output — there was nothing vendored to accidentally include.

## 3. Physical size (Task 2)

| Metric | Value |
|---|---|
| Tracked files (total) | 902 |
| Source files (any programming-language extension, whole repo) | 491 |
| — of which Python | 398 |
| — of which non-Python | 93 (across ~35 other extensions — largely single-file, single-language test fixtures; see §7) |
| Production source files (`graphify/**/*.py`) | 87 |
| Test files (`tests/*.py`) | 290 |
| Test fixture files (`tests/fixtures/**`, any language) | 114 (113 text, 1 binary `.dmi`) |
| Auxiliary tooling files (`tools/skillgen`, `scripts`) | 5 |
| Example/demo corpus files (`worked/**`) | 36 |
| Production LOC (`graphify/**/*.py`, physical lines) | 70,124 |
| Test LOC (`tests/*.py`, physical lines) | 94,782 |
| Test fixture LOC (`tests/fixtures/**`, text files) | 2,583 |
| Auxiliary tooling LOC | 1,626 |
| Example corpus LOC (text files only) | 72,271 — **of which 68,269 is generated `graph.json`/`graph.html` output artifacts (dominated by `worked/rsl-siege-manager/graph.json` alone at 57,397 lines) and only 4,002 is real sample source/notes (`.py`/`.md`)**; do not read the 72,271 aggregate as "72K lines of code" |
| Directories (whole repo, holding a tracked file) | 63 |
| Directories under `graphify/` | 18 |
| Directories under `tests/` | 17 |
| Max source file overall (any bucket) | `worked/rsl-siege-manager/graph.json`, 57,397 lines — a **generated example graph artifact**, not source code |
| Max **production** file | `graphify/extract.py`, 8,089 lines |

Production-file LOC thresholds (PRODUCTION bucket only, 87 files):

| Threshold | Count |
|---|---|
| > 500 LOC | 32 |
| > 1,000 LOC | 14 |
| > 2,000 LOC | 11 |
| > 5,000 LOC | 2 |
| > 10,000 LOC | 0 |

**20 largest production source files:**

| PATH | LANGUAGE | LOC |
|---|---|---|
| graphify/extract.py | Python | 8089 |
| graphify/extractors/engine.py | Python | 6955 |
| graphify/cli.py | Python | 4793 |
| graphify/extractors/resolution.py | Python | 3698 |
| graphify/llm.py | Python | 3544 |
| graphify/serve.py | Python | 2617 |
| graphify/detect.py | Python | 2588 |
| graphify/build.py | Python | 2414 |
| graphify/install.py | Python | 2375 |
| graphify/watch.py | Python | 2374 |
| graphify/callflow_html.py | Python | 2051 |
| graphify/cache.py | Python | 1782 |
| graphify/export.py | Python | 1354 |
| graphify/dedup.py | Python | 1220 |
| graphify/hooks.py | Python | 954 |
| graphify/reflect.py | Python | 882 |
| graphify/prs.py | Python | 770 |
| graphify/analyze.py | Python | 769 |
| graphify/__main__.py | Python | 755 |
| graphify/extractors/sql.py | Python | 720 |

## 4. 5K+ file qualification (Task 3)

Two production files exceed 5,000 LOC. Both were inspected structurally with `ast` (top-level
function/class spans, not a heuristic).

### `graphify/extract.py` — 8,089 lines, 109 top-level members, 0 top-level classes

- Structure is almost entirely function-oriented (procedural), not class-based.
- Largest member: `extract()`, 1,461 LOC (lines 6554–8014) — the single orchestration entry point
  that dispatches to all 30 language extractors. This sits in the **last third** of the file.
- Nine other independently-named members in the 140–223 LOC range
  (`extract_xaml`, `_resolve_csharp_member_calls`, `_resolve_objc_member_calls`,
  `_resolve_swift_member_calls`, `_merge_csharp_partial_class_nodes`,
  `_resolve_typescript_member_calls`, `_resolve_python_member_calls`,
  `_resolve_cpp_member_calls`, `_extract_parallel`), distributed earlier in the file.
- Member distribution across the file: 56 top-level members start in the first third, 28 in the
  middle third, 25 in the last third — genuine members occur near start, middle, **and** end, not
  clustered in one region.
- Multiple independently editable members: yes — 109 distinct top-level functions, most under 100
  LOC, several 100–220 LOC, one 1,461-LOC monolith at the tail.
- Test exercise: **strong**. 161 of the 290 test files import `graphify.extract`/`from graphify
  import extract`; `tests/test_extract.py` alone (4,689 LOC) defines 227 `def test_` functions
  that call `extract()` directly.

### `graphify/extractors/engine.py` — 6,955 lines, 97 top-level members, 0 top-level classes

- Also function-oriented. Largest member: `_extract_generic()`, 3,394 LOC (lines 3425–6818) — the
  shared tree-sitter walk/dispatch core used across most language extractors. This single function
  is **~49% of the file's own line count**.
- Second-largest: `_js_extra_walk`, 316 LOC. Eight more members in the 70–160 LOC range.
- Member distribution: 69 of 97 top-level members start in the first third of the file, 23 in the
  middle third, only 5 in the last third — the file is front-loaded with many smaller members, then
  dominated by the one 3,394-LOC function for the remainder. This is a materially different (more
  lopsided) shape than `extract.py`.
- Multiple independently editable members: yes (97 distinct functions), but concentrated
  authorship attention on one function dwarfs the rest more than in `extract.py`.
- Test exercise: **strong but more indirect**. Only 1 test file (`tests/test_extractors_registry.py`)
  imports `graphify.extractors.engine` directly; the bulk of coverage reaches `_extract_generic`
  transitively through `graphify.extract.extract()`, which is exactly what `tests/test_extract.py`'s
  227 tests already exercise per-language.

**Strongest G2 candidate: `graphify/extract.py`.** It has the more balanced start/middle/end
member distribution, a larger absolute member count, and the most direct test-import surface (161
files) — a more representative "large real production file with many independently editable
members" shape than `engine.py`, whose test/import surface is concentrated through one indirection
layer.

`G2_5K_FILE_AVAILABLE = YES` (2 qualifying files; no file needed to be manufactured or concatenated
to reach this — both are genuine, pre-existing, actively-maintained production files).

## 5. Structural complexity (Task 4)

Major subsystems under `graphify/` (18 directories, 87 production `.py` files):

- **CLI/entrypoint**: `__main__.py`, `cli.py` (dispatch), `install.py` (platform-specific
  skill/hook installers).
- **Extraction** (30 per-language tree-sitter extractors + shared engine): `extractors/` — a real
  subpackage (`extractors/__init__.py` present), containing `engine.py` (shared walk/dispatch),
  `base.py`/`models.py` (shared types), `resolution.py` (cross-file resolution passes), and one
  file per language (`apex.py`, `bash.py`, `csharp.py`, `dart.py`, `elixir.py`, `fortran.py`,
  `go.py`, `julia.py`, `pascal.py`, `powershell.py`, `razor.py`, `rust.py`, `sql.py`, `zig.py`, …).
- **Cross-language resolution** (top-level, outside `extractors/`): `resolver_registry.py` (a
  `LanguageResolver` registry with 2 concrete implementations found), `symbol_resolution.py`,
  `csharp_dispatch.py`, `pascal_resolution.py`, `ruby_resolution.py`, `markdown_resolution.py`,
  `cross_repo_calls.py`, `cross_repo_types.py`.
- **Graph assembly / state**: `build.py` (node/edge assembly, dedup coordination), `dedup.py`,
  `cluster.py`, `global_graph.py`, `cache.py` (per-file extraction cache), `multigraph_compat.py`.
- **Export/reporting**: `exporters/` subpackage (`base.py`, `html.py`, `graphdb.py` — a real
  `exporters/__init__.py` package), `export.py`, `report.py`, `tree_html.py`, `callflow_html.py`,
  `wiki.py`.
- **Server/API boundary**: `serve.py` — MCP stdio + HTTP server exposing graph-query tools; imports
  `security.py` and `build.py` directly.
- **Incremental/watch**: `watch.py`, `hooks.py` (git post-commit/post-checkout hook integration) —
  `hooks.py` imports `watch.py`'s `_rebuild_code`/`_apply_resource_limits` directly.
- **Config/detection**: `detect.py` (file discovery, type classification), `paths.py` (single
  source of truth for the output directory).
- **Ingestion surfaces**: `manifest.py`/`manifest_ingest.py`, `mcp_ingest.py`, `scip_ingest.py`,
  `google_workspace.py`, `prs.py` (PR/git integration), `transcribe.py`.
- **LLM backend abstraction**: `llm.py` (3,544 LOC — the third-largest production file; the
  provider-selection logic exercised by `test_anthropic_custom_endpoint.py` /
  `test_openai_custom_endpoint.py` / `test_ollama.py`, all of which assert config/URL behavior via
  `monkeypatch`, never a live network call).
- **Security**: `security.py` (SSRF-guarded HTTP handlers — real class-based subclassing of
  `http.client`/`urllib.request` internals, one of the few genuine inheritance hierarchies in the
  codebase).

Class usage overall: 35 `class` definitions across all of `graphify/` (mostly small
dataclass-like/exception/handler types — `AffectedHit`, `MinHash`, `FileType(str, Enum)`,
`ToolError(Exception)`, the SSRF-guard handler hierarchy in `security.py`). The two 5K+ files
(`extract.py`, `engine.py`) have **zero** top-level classes — see the disclosed limitation in §8.

**5 verified cross-file engineering paths** (confirmed via direct `import` grep, not inferred):

1. `__main__.py` → `install.py` / `cli.py` → (lazy) `extract.py` → `extractors/engine.py` →
   `build.py` → tests: `test_extract.py`, `test_build.py`.
2. `hooks.py` → `watch.py` (`_rebuild_code`, `_apply_resource_limits`) → `detect.py`/`paths.py` →
   `cache.py` → tests: `test_hooks.py`, `test_hook_chain_survives_skip.py`, `test_watch.py`.
3. `serve.py` → `security.py` (SSRF guards) + `build.py` (`edge_data`/`edge_datas`) → `paths.py` →
   tests: `test_serve.py`, `test_serve_http.py`, `test_security.py`.
4. `install.py` (skill installer) ↔ `tools/skillgen/gen.py` (generates `graphify/skills/*.md` from
   `tools/skillgen/fragments/`, CI-verified via the `skillgen-check` job) → tests: `test_install.py`,
   `test_skillgen.py`.
5. `resolver_registry.py` (`LanguageResolver` base) → language-specific resolvers
   (`csharp_dispatch.py`, `pascal_resolution.py`, `ruby_resolution.py`) → `extractors/resolution.py`
   → tests: `test_csharp_type_resolution.py`, `test_pascal_resolution.py`, `test_ruby_resolution.py`.

## 6. Test baseline (Task 5)

No dependency install, no test collection, and no live network/API call was executed. Everything
below is read directly from `pyproject.toml`, `.github/workflows/ci.yml`, and grep-based static
inspection of test source — never run.

| Field | Value |
|---|---|
| Supported Python versions | `requires-python = ">=3.10"` (`pyproject.toml`); CI matrix tests `3.10, 3.12, 3.13, 3.14` |
| Package/install mechanism | `uv` (astral-sh), lockfile `uv.lock` committed (1.02 MB); CI: `uv sync --all-extras --frozen` |
| pytest configuration | `[tool.pytest.ini_options]`: `testpaths = ["tests"]`; `norecursedirs` explicitly excludes `worked`, `scripts`, `.github`, `dist`, `build`, and 3 unrelated external corpora names |
| **Documented CI test command** | `uv run --frozen pytest tests/ -q --tb=short` (from `.github/workflows/ci.yml`'s `test` job) |
| Approx. test count by static collection | **4,908** `def test_`/`async def test_` definitions across the 290 files in `tests/*.py` (counted via source scan, not live `pytest --collect-only`; actual collected-test count will be higher due to 152 `@pytest.mark.parametrize` usages expanding some of these into multiple cases) |
| Integration/network/model-dependent tests | Exactly **1** file requires an external service: `tests/test_falkordb_integration.py`, which self-documents "no-op in the default CI" via `pytest.importorskip("falkordb")` plus a live-reachability probe — it needs `docker run falkordb/falkordb:latest` to do anything, and skips cleanly otherwise. 13 other files use `pytest.importorskip` for optional parser/DB dependencies (tree-sitter grammars, `psycopg`, etc.), same skip-clean pattern. Files whose names look network-related on first pass (`test_anthropic_custom_endpoint.py`, `test_openai_custom_endpoint.py`, `test_llm_backends.py`) were inspected directly: they only assert default config constants (e.g. `BACKENDS["claude"]["base_url"] == "https://api.anthropic.com"`) via `monkeypatch`-controlled env vars — **no live HTTP call in any of the three**. |
| Other CI jobs | `skillgen-check` (verifies generated skill markdown matches its fragment source — no test framework, a generator round-trip check); `security-scan` (bandit + pip-audit, `continue-on-error: true`, non-blocking) |

This is a clean, well-isolated suite: the documented CI command is deterministic, install is
declarative and lockfile-pinned, and — per PolymorphicValidator's own stack markers in
`kriya/tools/validate.py` — the presence of `pyproject.toml` at repo root plus 398 `.py` files
means Kriya's stack auto-detection will correctly classify this repository as Python, not
`"unknown"`.

## 7. Historical task mining (Task 6)

Mined entirely from local `git log` on the frozen clone — no GitHub API call, no issue-tracker
access. Ground-truth commits were inspected only for **SHA, author, date, subject line, and
changed-file list via `git show --stat`** — never diff hunks or patch bodies, so no maintainer fix
content has been exposed to this document or to any future Kriya/model input. Conventional-commit
discipline (`fix(scope): ... (#NNNN)`) made this straightforward; 1,810 commits were available to
search.

| ID | TYPE | TITLE | ISSUE/PR | PRE_FIX_COMMIT | GROUND_TRUTH_COMMIT | FILES_CHANGED_BY_MAINTAINER | APPROX_DIFFICULTY | WHY_USEFUL | ACCEPTANCE_EVIDENCE |
|---|---|---|---|---|---|---|---|---|---|
| H1 | bugfix | Resolve unqualified C# generic call sites, `Get<int>(...)` | #3406 | `67f99bd0` | `5d09dce4` | `graphify/extractors/engine.py` (+test) | NARROW | Single-file, single-language, small diff (27 lines prod) inside the 6,955-LOC engine.py — cleanest isolated-defect shape | `tests/test_csharp_generic_callsites.py` (new, 76 lines) |
| H2 | perf/bugfix | Remove quadratic scan in TS import-type normalization | #3359 | `98d62e23` | `1dcb1e1f` | `graphify/extract.py` (+test) | NARROW–MEDIUM | Real fix inside the 8,089-LOC `extract.py`; performance-shaped defect (not just wrong output but wrong complexity) | `tests/test_ts_import_type_arguments.py` (new, 38 lines) |
| H3 | bugfix | Dedup: pick the richer duplicate as survivor, keep losers' fields | #3372 | `1dc0dc94` | `5e6c2be0` | `graphify/dedup.py` (+test) | MEDIUM | Subtle merge/precedence logic bug, single file, requires understanding dedup's survivor-selection invariant | `tests/test_dedup_survivor_richness.py` (new, 98 lines) |
| H4 | bugfix | Report: make headline numbers agree with themselves | #3148 | `97e4275c` | `fce26fc9` | `graphify/report.py` (+test) | MEDIUM | Self-consistency invariant bug (a report field must match its own derived totals) — logic-only, no parsing/extraction touched | `tests/test_report_gap_thresholds.py` (new, 84 lines) |
| H5 | feature/bugfix | Recover T-SQL bracket-named, `CREATE OR ALTER`, and PROC routines | #3164 | `b88e1640` | `8b3f1911` | `graphify/extractors/sql.py` (+test) | MEDIUM | Large single-file diff (308 lines prod, 526 lines test) — real depth within one extractor, good "is this actually hard" calibration point below CROSS_FILE | `tests/test_multilang.py` (526 new lines) |
| H6 | bugfix | Resolve Python type reference stubs across imports | #3252 | `02957036` | `b2825e05` | `graphify/extract.py`, `graphify/extractors/resolution.py` (+test) | CROSS_FILE | Genuine 2-file fix spanning the orchestrator and the resolution subsystem for the same defect. **Dependency note: PRE_FIX_COMMIT `02957036` is H10's own GROUND_TRUTH_COMMIT** — H6 starts from the exact state H10 produces. Kept in the table as evidence of a real cross-file fix, but demoted to bench-strength (not selected into the G1–G6 campaign below) specifically because of this overlap: replaying H6 and H10 in the same campaign would make one presuppose the other's outcome | `tests/test_extract.py` (+184 lines) |
| H7 | bugfix | A `graphify` skip must not terminate the whole git hook chain | #2986 | `7f87c3b0` | `b09c839e` | `graphify/hooks.py` (+2 tests) | CROSS_FILE (behavioral) | Single prod file but the defect is about hook-chain *semantics* across the installed-hook boundary — acceptance requires reasoning about control flow, not just local logic. History-independent of H10/#3223 | `tests/test_hook_chain_survives_skip.py` (new, 110 lines) + `tests/test_hooks.py` (updated) |
| H8 | bugfix (multi-issue) | Prune newly-ignored files; stop external-annotation/local-class conflation | #2495, #2504 | `b8d60973` | `2320f2ad` | `graphify/detect.py`, `graphify/extract.py`, `graphify/extractors/engine.py`, `graphify/extractors/resolution.py`, `graphify/watch.py` (+2 tests) | CROSS_SUBSYSTEM | 5 production files spanning detection, extraction, resolution, and the incremental-watch subsystem, fixing 2 related issues in one commit — the strongest CROSS_SUBSYSTEM candidate found | `tests/test_java_type_resolution.py` (+168), `tests/test_watch.py` (+245) |
| H9 | bugfix (cross-cutting) | Give AST INFERRED edges a rubric confidence_score | #2813 | `74abbfa3` | `3bbf420b` | `graphify/export.py`, `graphify/extract.py`, `graphify/extractors/engine.py`, `graphify/extractors/resolution.py`, `graphify/symbol_resolution.py` (+3 tests) | CROSS_SUBSYSTEM (comprehension-heavy) | Requires understanding one *concept* (confidence scoring) as it threads through extraction → resolution → symbol resolution → export; not a single localized bug but a rubric applied consistently across the pipeline | `tests/test_inferred_confidence_rubric.py` (new, 115 lines), `tests/test_confidence.py`, `tests/test_symbol_resolution.py` |
| H10 | bugfix, 2-commit sequence | `definition_file` portability: first attempt incomplete, required a follow-up commit | #3223 | `33362d96` | `02957036` (final) | Reported per-commit, not cumulative (no combined diff was computed): commit 1 `3e5944b0` ("make `definition_file` portable, like its sibling `source_file`") touched `graphify/build.py`, `graphify/cache.py`; commit 2 `02957036` ("normalize `definition_file` on the watch and direct-extract paths"), parent = commit 1, touched `graphify/extract.py`, `graphify/watch.py` | RECOVERY_CANDIDATE | Same tracked issue (#3223) required two sequential maintainer commits — the first closed the bug in `build.py`/`cache.py` but left a gap in `extract.py`/`watch.py` that the second commit closed. A real, naturally-occurring "first fix was incomplete" case — exactly the RECOVERY shape, not manufactured. **Note: H6's own PRE_FIX_COMMIT is this row's GROUND_TRUTH_COMMIT (`02957036`)** — see H6's row | `tests/test_definition_file_portability.py` (commit 1, +89), `tests/test_build.py`/`test_languages.py`/`test_watch.py` (commit 2, +92 combined) |

10 candidates total (within the requested 8–12 range), spanning all 5 difficulty categories. H6 and H10 share a history dependency (documented in both rows above); the G1–G6 selection in §8 uses H10 for G6 and H7 (history-independent) for G3, so the campaign itself contains no such dependency.

## 8. Proposed provisional campaign (Task 7)

All six selections below are drawn directly from the Task 6 table — no synthetic requirement was
introduced to fill a slot.

- **G1 (narrow real brownfield defect)** → **H1** (`5d09dce4`, C# generic call-site resolution).
  Smallest, most isolated real defect found: one file, one language extractor, a 27-line
  production diff with a focused 76-line test as acceptance evidence.
- **G2 (5K+ file/member task)** → **H2** (`1dcb1e1f`, TS import-type quadratic-scan fix inside
  `graphify/extract.py`). Chosen over an out-of-history synthetic task specifically because it is a
  real historical fix that lands inside the 8,089-LOC file identified as the strongest G2 candidate
  in §4 — satisfies the task's own "only if naturally available" constraint by construction.
- **G3 (cross-file change)** → **H7** (`b09c839e`, git-hook-chain-must-survive-a-skip). Chosen over
  H6 specifically because H6's baseline commit (`02957036`) is H10/G6's own ground-truth commit —
  selecting H6 here would make G3 presuppose G6's outcome. H7 is history-independent of every other
  selected task, fixes a genuine cross-component *behavioral* defect (a skip in one hook must not
  abort the rest of the installed hook chain), and has a focused 110-line test as acceptance
  evidence. H6 remains available as a bench-strength CROSS_FILE candidate (§7) for a future round
  that does not also run G6.
- **G4 (larger multi-file/cross-subsystem change)** → **H8** (`2320f2ad`, 5 production files across
  detection/extraction/resolution/watch, 2 linked issues). The largest, most structurally spread
  fix found in the sampled history.
- **G5 (difficult repository-comprehension task)** → **H9** (`3bbf420b`, confidence-score rubric
  threaded across 5 files). Chosen for G5 specifically because correctness here depends on
  understanding one cross-cutting *concept* consistently, not on locating one bug — the
  comprehension load is the point, not the file count.
- **G6 (potential natural recovery task)** → **H10** (`#3223`, two-commit sequence). A real,
  naturally-occurring case where the maintainers' own first fix was incomplete and needed a
  follow-up commit — this is the one candidate that inherently tests recovery from an
  under-scoped first attempt, without any artificial injection.

Every one of G1–G6 originates from real Graphify history; none was invented to fill a category.

## 9. Kriya compatibility check (Task 8)

No Kriya generation was run. This section identifies likely stress points only, based on static
inspection of the frozen Graphify clone against Kriya's already-closed capabilities — no fixes are
proposed here per the task's own instruction.

- **CTX-001 member-level context**: strong, realistic stress case. `extract.py`'s `extract()`
  (1,461 LOC) and `engine.py`'s `_extract_generic()` (3,394 LOC, ~49% of its file) are exactly the
  "one dominant member in a large file" shape CTX-001's member-hint/skeletonization work targets.
  `extract.py`'s more balanced 109-member, start/middle/end-distributed shape is a materially
  different (arguably easier) stress case than `engine.py`'s front-loaded-then-one-giant-function
  shape — both are worth exercising separately if G2 work later touches `engine.py` too.
- **Large repository discovery**: **does not naturally stress this at whole-repo scale.** Graphify
  is 902 tracked files total, 491 source-language files, 87 production files — well under the
  5,000-file regime named in the objective's purpose statement. This is a real, disclosed gap
  against that specific axis (kept separate from the file-*size* axis in §4, which Graphify does
  satisfy).
- **Graph-RAG retrieval / cross-file reasoning**: strong stress surface. `extract.py` alone imports
  ~30 per-language extractor modules; `resolver_registry.py`'s `LanguageResolver` dispatch pattern
  and the 5 verified cross-file paths in §5 give real multi-hop retrieval targets.
- **Python inheritance/import relationships**: **import** relationships are rich (the 30-module
  extractor fan-in from `extract.py` alone). **Inheritance** relationships are comparatively thin:
  only 35 `class` definitions exist in the entire `graphify/` package, and the two 5K+ files
  (`extract.py`, `engine.py`) — the most likely G2 targets — have **zero** top-level classes each;
  the codebase's extraction core is procedural, not OOP. A class-inheritance-focused stress test
  would need to target `security.py` (the one real subclassing hierarchy found) rather than the
  large files.
- **Duplicate/bare symbol-name pressure**: low at the module-top-level within `extractors/` — all
  30 `extract_<language>()` entry points have unique names, and a full duplicate-name scan across
  `extractors/*.py` top-level functions found zero collisions. Not exhaustively checked at the
  nested-closure level inside `extract()`/`_extract_generic()` — that would need a deeper pass than
  this qualification performed (disclosed scope limit, not a finding either way).
- **Incremental analysis**: real, dedicated subsystem (`cache.py` — per-file extraction cache;
  `watch.py` — file-change-triggered incremental rebuild), independently exercised by
  `test_incremental.py`, `test_incremental_mtime_collision.py`, `test_partial_cache.py`. Good stress
  surface for Kriya's own incremental-analysis path.
- **Current-worktree freshness**: no direct architectural overlap with Kriya's own worktree model;
  Graphify's git-hook integration (`hooks.py`) is a *consumer* pattern (post-commit/post-checkout
  triggers), not something Kriya's worktree freshness logic would need to reason about specially.
- **Deterministic compile/test/runtime verification**: strong fit. `pyproject.toml` present at
  root, `.py` files present throughout — PolymorphicValidator will correctly detect Python, not
  `"unknown"`. The documented CI command (`pytest tests/ -q --tb=short`) is exactly the shape
  Kriya's `run_tests` gate already expects, and the 1 genuinely network-dependent test file
  self-skips cleanly without any Kriya-side special-casing needed.

## 10. Limitations (stated explicitly, not softened)

1. **Not a 5,000+ tracked-file repository.** 902 tracked files total; even the broadest
   "any-language source file" count is 491. If VAL-001's purpose statement's bullet #2 ("real 5K+
   source-file behavior") is read as a whole-repository file-count requirement, Graphify does not
   meet it. It does meet the file-*size* axis (§4: 2 production files > 5,000 LOC) — these are
   different properties and are not conflated here.
2. **Thin class/inheritance material in the largest files.** The two candidate G2 files are 100%
   procedural (0 top-level classes each). A future task specifically targeting Python
   inheritance-graph reasoning should not be built on `extract.py`/`engine.py`; `security.py` is the
   better target for that axis, but it is a much smaller file (not in the 5K+ tier).
3. **`engine.py`'s test coverage of `_extract_generic` is indirect.** Only one test file imports
   `graphify.extractors.engine` directly; the bulk of its coverage is reached transitively through
   `extract()`. This is disclosed because it affects how "does this test suite exercise this file"
   should be interpreted for any future G-task built on `engine.py` specifically.
4. **Nested-closure-level symbol-name-collision pressure was not exhaustively audited** inside the
   two giant functions (only top-level, file-scoped names were checked). Genuinely unknown either
   way — not claimed as a finding.
5. **`worked/` contains a literal excerpt of a third-party library (httpx).** This was excluded from
   all LOC/file counts as required, but is noted here in case a future contributor mistakes it for
   Graphify's own code when browsing the tree.
6. **History mining used only the local clone's `git log`**, not a live GitHub Issues/PR API call
   (deliberately, to avoid any external-service dependency beyond the initial clone). Issue/PR
   numbers in the table above are exactly as recorded in each commit's own subject line; they were
   not independently cross-checked against the GitHub issue tracker.

## 11. Qualification decision

`GRAPHIFY_QUALIFICATION = QUALIFIED_WITH_LIMITATIONS`

Rationale: Graphify is a real, actively maintained (1,810 commits, latest commit 2026-09-16),
substantial (70,124 production LOC across 87 files), Python-native (pyproject.toml + uv-managed,
tested against 4 Python versions in CI) repository with genuinely complex multi-subsystem
structure (30 language extractors, a cross-language resolver registry, incremental caching,
CLI/server/hook entrypoints) and an exceptionally rich, cleanly-attributable commit history
(conventional-commit format with issue references throughout, making historical task mining
straightforward and evidence-backed rather than speculative). It has 2 real production files over
5,000 LOC with multiple independently editable, test-exercised members, and a deterministic,
network-free, install-declared test baseline (4,908 statically-counted test functions, 1 exception
that self-skips cleanly). All of Tasks 1–8's evidence requirements were obtainable without
installing dependencies, running any test, or contacting any external service beyond the initial
clone.

The qualification is **"with limitations"** rather than unconditional because of the two disclosed
gaps in §10 that a reader should weigh before committing to specific G-task file choices: the
repository does not reach 5,000+ tracked files at the whole-repository scale, and its largest
files are 100% procedural (no class/inheritance material), which narrows what a G2-class task built
on those specific files can validate about Kriya's own inheritance-relationship handling.

---

**MODEL-001 remains CLOSED. CTX-001 remains CLOSED. RECV-002 remains NEEDS_EVIDENCE. F8 remains
OPTIONAL_ADDITIONAL_EVIDENCE.** Nothing in this document touches, reopens, or depends on
reinterpreting any of those statuses.
