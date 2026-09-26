# CI-RUFF-001: Ruff CI restored to green

**Status:** READY_FOR_PYTEST_VERIFICATION (2026-09-26). Local commits, not pushed.

## Result

| | Findings |
|---|---|
| Before, at `59d5aa5` (Batch 5 VERIFIED) | **462** |
| Before, at the task start (after the quality-bar commits) | **469**: +9 I001 from the `_strict_doubles` import placement, −2 F821 fixed by the pylint gate commit |
| After | **0** (`ruff check .` exit 0) |

- **Ruff:** 0.16.0, config `pyproject.toml` `[tool.ruff]` (select E, F, I, B; ignore E501; three per-file ignores that predate this task). No rule, select or ignore was changed.
- **Baseline by rule, at the task start (469):** F401 233, I001 152, F841 25, F811 15, E741 10, B023 9, B007 6, B011 6, B905 6, B904 4, E402 2, E731 1.
- **Files:** 159 in total: kriya 218 findings, tests 235, spikes 15, benchmarks 1.

## Commits

| Commit | What |
|---|---|
| fbe00d7 | Guard before autofix: the 41 imports in `workflow.py` that ruff called unused, but that tests, `review_context.py` and a spike still import from `kriya.workflow.workflow`, marked as explicit re-exports (`name as name`) |
| 7c53a97 | Safe autofix (`ruff check . --fix`): F401 + I001 (+ F811 duplicate imports), 469 → 78. Two renamed re-exports it removed were restored (see below). |
| 2f8f211 | Production code by hand: B023 (9), B905 (5), B904 (4), B007 (3) |
| b3475f0 | Tests/spikes F841 (25), including one stale, never-asserted contract |
| 8e46f3f | Tests/spikes F811 (10), E741 (10), B011 (6), B007/B905 (3), E402, E731 → 0 |
| (this commit) | CI pins ruff (and pylint/astroid) to the lock-file versions; CLAUDE.md rule 1 now includes ruff; this handover |

## Guarding behaviour (step 4)

- **Re-exports.** A repository-wide check found the 41 `workflow.py` names still imported elsewhere; they were marked explicit before the autofix.
  - The first reference check used a regex. It missed two renamed re-exports in `milestone_completion.py` (`MILESTONE_CHECKPOINT_SELECTED`, `NO_COMPATIBLE_MILESTONE_CHECKPOINT`, imported by `test_prd008_s4c_milestone_resume`): a `)` in a comment cut its match short. `pytest --collect-only` caught it.
  - They were restored as separate import statements, each with its own `# noqa: E402,F401`, because a renamed import cannot use the `name as name` form. `ruff --fix` leaves them in place.
  - After that, an **AST resolver** replaced the regex. It resolves every `from kriya… import name` at any nesting depth (including imports inside functions, which collection cannot see) and every dotted `"kriya.*"` string patch target, against the live modules. Compared with HEAD before the autofix: **no name newly unresolved**.
- **Fixture imports (checked after review).** Every name the autofix removed from an import in `tests/`/`spikes/`/`benchmarks/` (47) was compared with the pytest fixtures defined in `tests/` at `fbe00d7`, including autouse and `usefixtures("…")`-only use. None was a fixture. The only removals outside kriya were stdlib `typing`/`unittest.mock` names.
- **All local imports.** A second resolver checks every absolute import of a repo-local module (kriya, plugins, `tests/` helpers such as `_milestone_proof_harness`, test-to-test imports, spike siblings), each file with its own directory, `tests/` and the root on `sys.path`. It gives an identical result at `4b85afc` (before the task) and now. The single entry, `kriya/mcp/server.py → mcp.server.mcpserver`, is a checker artifact: the file's own directory shadows the third-party `mcp` package. It predates the task.
- **Import order.** Every kriya module (185) and `plugins.core_tools` imports in a fresh interpreter, so there are no ordering cycles.
  - One comment that isort moved away from its imports (`attempt.py`, "annotation-only names") now names the imports it means.
  - Imports only used in annotations are kept: ruff counts annotation use as use, and they are needed at runtime for PRD-001's `get_type_hints`.
- **Side-effect, plugin, CLI and `__init__` imports.** None of the removed imports was a bare kriya module import or in an `__init__.py`. The removed stdlib imports (`asyncio`, `subprocess`, … in modules split out of `workflow.py`) had no references, and no test patches `module.subprocess` on those modules.
- **Production fixes** (2f8f211), each preserving behaviour:
  - **B023:** every closure is called within the same iteration (checked: `get_or_compute_derivation` calls `compute_fn` at once). Loop values are now bound as defaults.
  - **B905:** `strict=True` only where equal length holds by construction; `strict=False` where truncation is today's behaviour.
  - **B904:** `proposal_store` chains `from e`. `best_of_n` used to re-raise the original candidate failure inside the worktree-reset handler, which overwrote its context; it now raises after that handler and logs the reset error.
  - **B007:** `config.py`'s production-profile loops iterate keys. The sealed check is a predicate, so the unused value was not a missing comparison.
- **A finding that was a real test gap.** In `test_deterministic_failure_diagnostic::test_handle_attempt_failure_stops_the_loop_when_baseline_reproduces_failure`, `should_break` was computed and never asserted, and a comment claimed it was `False`. It is `True`: `decide_for_state()` returns `STOP_ENVIRONMENT` in the same call. The test now asserts `True`, and the comment is corrected.
- **`noqa` added:** only per-line `# noqa: F811 - pytest fixture` (10, fixture parameters injected by name, the repo's existing convention) and the two renamed re-exports above. No broad or per-file ignore.

## Verification so far (mine)

- `ruff check .` exits 0.
- pylint gate exits 0.
- `pytest --collect-only`: 5864/5886, the same as HEAD before the task.
- The AST resolver finds no new unresolved imports.
- **Targeted pytest runs:**
  - production-fix modules: 223/0;
  - F841 files: 473/0;
  - final group: 245/0.
- `tests/test_live_prd020_024_batch5.py` changed only by an equivalent loop rewrite. It is `live_model`, so it was not run.

## For the user

Full suite: `.venv/bin/pytest`. It is required here, because 148 files changed in the autofix.

## CI contract (step 6)

- `lint` installs **exactly the lock file's ruff** (`pip install "$(grep '^ruff==' requirements.txt)"`) and runs `ruff check .`. Any finding fails the job.
- Pinning matters: with the old floating `ruff>=0.6.0`, a new ruff release could add rules to the selected groups and turn the gate red with no code change.
- `static-check` pins pylint/astroid the same way.
- To upgrade ruff, recompile the lock and fix the new findings in the same change.
- CLAUDE.md rule 1 now requires both `pylint` and `ruff check .` to exit 0 before every commit. It also says to confirm an "unused" import isn't a re-export before letting `--fix` remove it.
- Note: `ci.yml` runs on pushes to `main`, on pull requests and nightly. A push to `milestone-decomposition` alone does not run it; a PR does.
