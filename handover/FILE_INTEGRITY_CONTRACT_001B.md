# FILE-INTEGRITY-CONTRACT-001B — closing two mutation-integrity bypasses

**Status:** implemented locally, awaiting owner review. Not pushed.

This is an append-only follow-up to FILE-INTEGRITY-CONTRACT-001 (`handover/FILE_INTEGRITY_CONTRACT_001.md`, whose closure record is unchanged). An independent review after that closure found the two gaps below. They were not part of the original closure evidence.

The architecture is unchanged:
- protocol and payload are separate trust boundaries;
- every Kriya-initiated repository mutation passes the authorized candidate writer;
- the tree verified is the tree committed.

Each fix restores one of these rules where an implementation path violated it.

Evidence: `evidence/file-integrity-contract-001b/`. Tests: `tests/test_file_integrity_contract_001b.py`.

## P0 — Planner prose became repository files

**Reproduced at `2e8b09f`** (`planner_bypass_prefix_2e8b09f.txt`). A greenfield run whose Planner reply carried a fenced shell line under a `### config.yaml` heading:
- wrote `config.yaml` = `export TOOL_HOME=/tmp/tool`;
- never called the Developer;
- reported `quality_gates_passed: true`.

**Root cause** (TRACED): on attempt 1, `attempt.run_attempt` called `file_resolution.extract_planner_code_blocks(ctx.plan, …)`. When every expected file had a fenced block near its heading, the blocks became the candidate files directly. The FILE-INTEGRITY-CONTRACT-001 audit classified filesystem write *sites*. These bytes went through the approved staged writer; only their *source* (Planner prose) was unauthorized, so a site audit could not see it.

**Fix:**
- The reuse path is removed: the extraction function, its plausibility checks and constants, the re-export, and the unused `planner_reuse_used_attempt1` metric.
- Every attempt's files come from the Developer generation call. No Planner heuristic or sanitizer replaces it.
- The test that pinned the bypass is inverted into a regression test: full Planner coverage still calls the Developer.

**Audit, strengthened** (`test_every_source_of_candidate_bytes_has_a_named_authority`): an AST check enumerates every assignment and append to `files` (what the staged writer commits) inside `run_attempt`. Each must map to a named authority:
- the Developer mutation intent;
- the deterministic API-contract owner restore (captured baseline);
- resume (the checkpoint's verified candidate);
- carried-forward candidate state;
- path normalization and de-duplication (no new bytes).

A new source fails the test until it is classified.

Also reviewed, and found to have explicit authority:
- the Architect returns a file list only;
- the Reviewer returns text only;
- self-correction writes in the Developer role through `AuthorizedFileWriter`;
- ownership-redirect restoration and candidate pom corrections are already classified write sites.

## P1 — a gate could create untracked repository content

**Reproduced at `2e8b09f`:**
- A compile gate created `extra.properties`: untracked, not ignored.
- The tests ran against it, the run passed, and the committed candidate did not contain it.
- The verified tree was not the committed tree.

**Root cause** (TRACED): `VerificationTreeBinding` recorded Git-*tracked* paths plus candidates only. The original closure deliberately treated every untracked file as non-content ("untracked/ignored build output is never repository content").

**Fix** (`file_integrity`):
- At bind time the binding snapshots the untracked, non-ignored paths, using Git's own exclude semantics (`git ls-files --others --exclude-standard`: `.gitignore`, `info/exclude`, `core.excludesFile`). Ignored output is never listed or hashed.
- After every gate, and before the next one, a path that is new since the snapshot and is not a candidate or Kriya-authorized write is a typed stop: `VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE`. It is reported as `failure_category: verification_tree_mutated` with diagnostics `gate`, `phase`, and `created: [{path, tracked: false, ignored: false}]`.
- Pre-existing untracked files are never attributed to a gate.

Two narrow exemptions, each with a named owner (measured: the strict rule alone flagged 123 `__pycache__/*.pyc` and 21 `build/*.class` across 39 suite tests whose fixture repos have no `.gitignore`):
1. **CPython's bytecode cache.** `*.pyc`/`*.pyo` inside `__pycache__/` (PEP 3147), derived from the tracked sources by any Python gate. A `.py` file in such a directory is still content.
2. **Kriya's own output directory.** The Java gate runs `javac -d build` and registers that directory with the binding. Only output Kriya designated is exempt; `buildx.txt` beside it is still content.

Maven `target/`, Gradle `build/` and similar are handled only by Git ignore semantics. A repository that does not ignore its build output fails closed, by design (demo preflight E3).

## Verification

- Focused tests: `tests/test_file_integrity_contract_001b.py` (12) and the provider truthfulness tests.
- The F-4 test that allowed an untracked, non-ignored `build.log` was changed to the new rule.
- Mutation: 15/15 killed across Parts A and B (`evidence/provider-contract-001a/mutation/mutation_ab.txt`).
- Full suite: see `evidence/file-integrity-contract-001b/full_suite.txt`.
