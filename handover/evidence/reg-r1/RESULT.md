# REG-R1 — PYTEST WHOLE-OUTPUT REGRESSION AUTHORITY (OBS-4): investigation result

No model calls. No Graphify generation. Kriya a049c02 not modified. Reproducer = Graphify base 67f99bd + the preserved
qwen3.8 staged candidate engine.py e47eacba… (run 20261006T231821-fb31caad).

## Phase 1 — reproduction (MEASURED)

`reg_r1_repro.py` calls a049c02's own `PolymorphicValidator.run_tests()` — the exact call of the live baseline capture
and the live POST gate — each run in a FRESHLY created git worktree at the live-shaped path (BASE: `<root>/.kriya/worktree`,
CANDIDATE: sibling `<root>/.kriya/worktrees/candidate-reg-r1`, both gitfiles, as live). Recording wrappers pass arguments and
results through unchanged. Runtime: OCI `python:3.12-slim` (image not digest-pinned by Kriya), network denied, cpu 240 s,
mem 4096 MB, Docker 27.5.1 linux/arm64; each run built a fresh `.kriya/venv` whose 35 distributions equal the live
venvs' exactly.

| Run | exit | completeness | cases | failed / passed / skipped | stdout sha256 | stderr | gate output (= stdout+LF+stderr) |
|---|---|---|---|---|---|---|---|
| BASE-1 | 1 | COMPLETE | 5370 | 217 / 4896 / 257 | ebed3967… | empty | c0382889…, 887,108 B |
| BASE-2 | 1 | COMPLETE | 5370 | 217 / 4896 / 257 | c064da15… | empty | 274d1f21…, 887,109 B |
| CANDIDATE-1 | 1 | COMPLETE | 5370 | 217 / 4896 / 257 | 3974db4f… | empty | d7874ada…, 887,147 B |
| CANDIDATE-2 | 1 | COMPLETE | 5370 | 217 / 4896 / 257 | 94c393d0… | empty | 61ebf4dc…, 887,108 B |

(Live: baseline 887,129 B, POST 887,108 B; live raw bytes were not retained.)

## Phase 2 — a049c02 comparator (MEASURED, `reg_r1_compare.py`, `comparator_matrix.json`)

| Pair | whole-output (level 1) | fingerprints | per-test (level 2) | new | changed | missing (aggregate drop) | blocking |
|---|---|---|---|---|---|---|---|
| BASE-1 vs BASE-2 | CHANGED_FAILURE | 72ecc79d… → b4fe311b… | 217 PRE_EXISTING_FAILURE | 0 | 0 | no | **YES** |
| CANDIDATE-1 vs CANDIDATE-2 | CHANGED_FAILURE | 27e12615… → ea5e121c… | 217 PRE_EXISTING_FAILURE | 0 | 0 | no | **YES** |
| BASE-1 vs CANDIDATE-1 | CHANGED_FAILURE | 72ecc79d… → 27e12615… | 217 PRE_EXISTING_FAILURE | 0 | 0 | no | YES |
| BASE-2 vs CANDIDATE-2 | CHANGED_FAILURE | b4fe311b… → ea5e121c… | 217 PRE_EXISTING_FAILURE | 0 | 0 | no | YES |

The pristine base produces a blocking CHANGED_FAILURE against itself. Blocking reason in every pair: `level1:CHANGED_FAILURE` only.

## Phase 3 — exact difference (MEASURED, `reg_r1_diffcat.py`, `diff_categories.json`)

Identical category profile for all four pairs (44 differing raw lines; 0 uncategorized "other"):

| Category | raw lines | survives Kriya's `normalize_failure_text`? |
|---|---|---|
| random identifier: object memory addresses (`<… object at 0xffff…>`) | 40 | no (covered by `0x[0-9a-f]{4,}`) |
| timing: measured durations/ratio inside one failing test's assertion message (`scaling looks super-linear: … (3.3x/3.5x/3.8x for 2x input)`, `assert 0.1488… < (0.0447… * 3)`) | 2 | **yes** (bare floats and `3.3x` not covered) |
| environment chatter: container `HOSTNAME` (random container id) and container env key order, printed in one failing test's traceback locals (`env = {...}`) | 1 | **yes** |
| summary duration (`in 59.56s`) | 1 | no |

Owners of the surviving lines (both already failing on the pristine base):
`tests/test_ts_import_type_arguments.py::test_ts_normalizer_scales_linearly_on_large_files` (timing assertion) and
`tests/test_watch_manifest_location.py::test_built_at_commit_comes_from_the_target_repo` (`git init` failure; traceback prints
the subprocess env). Producer of the env difference: the container runtime (Kriya passes an ordered allowlist).
Candidate-caused differences: **none** — base-vs-candidate shows exactly the base-vs-base categories.

## Phase 4 — per-test failure signatures (MEASURED, from each run's own per-invocation JUnit XML)

Identity/outcome (`reg_r1_signatures.py`): every pair — 217 failing in both, NEW 0, MISSING 0, ADDED 0, PASS→FAIL 0,
FAIL→PASS 0, error transitions 0.

Signature depth (`reg_r1_sigdepth.py`, after Kriya's own normalization), number of failing tests whose signature differs:

| Pair | S0 id+outcome | S1 +exception type | S2 +message | S3 +crash frame | S4 +all frames | S5 +whole body |
|---|---|---|---|---|---|---|
| BASE-1 vs BASE-2 | 0 | 0 | 1 | 1 | 1 | 2 |
| CANDIDATE-1 vs CANDIDATE-2 | 0 | 0 | 1 | 1 | 1 | 2 |
| BASE-1 vs CANDIDATE-1 | 0 | 0 | 1 | 1 | 1 | 2 |
| BASE-2 vs CANDIDATE-2 | 0 | 0 | 1 | 1 | 1 | 2 |

S2–S4 changed test: the timing test (its message itself is nondeterministic). S5 adds `test_built_at_commit…` (env repr).
Base vs candidate: IDENTICAL 216 / CHANGED 1 at S2 (the timing test) — the same count and the same test as base vs base.

## Phase 5 — classification

**A — WHOLE_OUTPUT_NONDETERMINISM**, CONFIRMED: the a049c02 comparator blocks pristine-vs-pristine (2/2 self pairs) for
the same reason it blocked base-vs-candidate; no difference attributable to the candidate exists at any signature depth.
Not B (no candidate-caused change in any pre-existing failure). Not D.

Live stop correct: **NO.** The mechanism is reproduced deterministically with the live runtime; the live raw bytes were
not retained, so that the live delta consisted of the same categories is INFERRED (live sizes 887,129/887,108 B in the same
band; live level 2 = 217/217 PRE_EXISTING).

Root cause (TRACED, a049c02): `validation_baseline.build_validation_outcome` fingerprints a pytest run by its WHOLE
combined output (Maven uses the Surefire Results summary, comparison v2); `classify_baseline_delta` makes level-1
CHANGED_FAILURE blocking on its own; `workflow.run_generation_workflow` then finds no confirmed test ids
(`confirm_ambiguous_regressions` only replays NOT_COMPARABLE) and no compile attribution → `_full_regression_unattributed`
→ REGRESSION_UNATTRIBUTED (`stop_environment`). Any suite with a failing test that prints run-varying text not covered by
the narrow volatile-token list blocks every candidate.

## Phase 6 — authority design (PROPOSED, not implemented)

Implementation was NOT started: the owner's stop condition "architectural decision not specified" applies. The specified
minimum signature (type + normalized message + body) is itself nondeterministic on the pristine base for one test (S2
row above), so the specified design would still block this exact case; resolving it requires choosing how failure-message
volatility is established. Options for the owner:

1. **Structured per-test authority (needed in every option).** For pytest, when PRE and POST both carry a COMPLETE
   FS-1A per-invocation JUnit report (`test_execution.completeness == COMPLETE`, collected count consistent), compare per
   test from the report, not console text: identity sets (missing → NEWLY_SKIPPED_OR_NOT_EXECUTED, blocking), outcome
   transitions (PASS→FAIL/ERROR → NEW_FAILURE), FAIL→FAIL by signature. The whole-output fingerprint becomes diagnostic only
   when this evidence is complete on both sides; otherwise today's behaviour (level 1 blocking, fail closed) is kept.
   Requires persisting the PRE structured cases in the baseline (checkpoint schema + `BASELINE_COMPARISON_VERSION` 3).
2. **Signature volatility — the decision:**
   - 2a. Message-level signature, no volatility handling: deterministic change detection kept, but this case still blocks (fix does not resolve OBS-4).
   - 2b. **Volatility established from evidence (recommended):** a FAIL→FAIL signature change is replayed in isolation on pristine twice (or the
     baseline captures the suite twice); a test whose signature varies on unchanged pristine code is "signature-volatile" and compared at
     S1 (id+outcome+type) only; a test whose pristine signature is stable and differs on the candidate stays CHANGED_FAILURE (blocking).
     Costs replay time only when a signature changes; no detection weakened for stable tests.
   - 2c. Number-insensitive message normalization: resolves this case cheaply but hides numeric-only changes of real failures (a weakening).
3. **Evidence retention (observational):** today the gate keeps only counts and report digests; raw output and the
   decision inputs are discarded (this investigation could not read the live bytes). Retain content-addressed, compressed
   raw stdout/stderr, the JUnit report, the normalized comparison artifact, the per-test comparison and the blocking reason
   for every blocking regression decision, bounded per run; recording must not alter the decision.

Invariant to adopt once 2 is decided: *complete, structured per-test evidence outranks the aggregate output
fingerprint; aggregate-only differences are diagnostic; missing or incomplete per-test evidence fails closed.*
Maven/Surefire: unaffected (already summary-based; not touched).

## Artifacts

`reg_r1_repro.py`, `run-reg-r1.sh`, `reg_r1_compare.py`, `reg_r1_diffcat.py`, `reg_r1_signatures.py`, `reg_r1_sigdepth.py`;
`runs/` (raw stdout/stderr per command, gate outputs, JUnit XML, meta with argv/profile/result, venv inventories, pre/post
worktree state, docker image list), `normalized/`, `signatures/`, `comparator_matrix.json`, `diff_categories.json`,
`live/` (live venv inventories).
