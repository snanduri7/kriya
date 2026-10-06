# FS-1C2 / B2-min: executable acceptance evidence for BEHAVIOR claims (DESIGN ONLY)

**Status:** B2-a (Python, flat layouts) IMPLEMENTED on `feature/lr-r1-b2a` after owner authorization - see
`handover/FS1C2_B2A_IMPLEMENTATION.md` (the runner is acceptance-specific: §4's launcher concern does not apply because
Kriya's own runner appends the project root itself; the ordinary test gate is unchanged). B2-b, B2-c, B3: design only,
not authorized. The text below is the original design.
**Labels:** MEASURED, TRACED, INFERRED, PROPOSED.

## 1. What B2 must provide

FS-1C1 leaves every BEHAVIOR claim UNVERIFIED unless a producer in `BEHAVIOR_CLOSURE_METHODS` records it
(`record_requirement_claim(..., BEHAVIOR, method="acceptance_oracle")`). B2 is that producer. Its authority rule:
the acceptance oracle exists before candidate generation, comes from the user/operator, is immutable to the candidate,
is never produced by a model from prose, and is executed deterministically on the exact candidate.

## 2. Proposed shape (PROPOSED)

1. **Input.** An operator file passed with the goal: `kriya generate --acceptance <file>` (and milestone/enforce
   equivalents). Not a fenced block in the goal: a file is reviewable, diffable and its digest is simple to bind.
   The CLI copies it into a Kriya control path (`.kriya/acceptance/<run>/`, unreachable by candidate writes) and
   records its sha256 before planning starts.
2. **Format (Python first).** A pytest module whose test functions carry the requirement they prove:
   ```python
   import pytest

   @pytest.mark.kriya_requirement("REQ-1")
   def test_freeze_at_epoch():
       from freezegun import freeze_time
       import datetime
       with freeze_time(0):
           assert datetime.datetime.now() == datetime.datetime(1970, 1, 1)
   ```
   The requirement id must exist in the derived requirement set (closed set, as `validate_plan` already enforces
   for Planner citations); an unknown id refuses the file before generation.
3. **Binding.** The acceptance file digest joins the requirement set's identity (a closure binds goal digest +
   acceptance digest + candidate evidence id + report digest), so a changed file never reuses evidence.
4. **Execution.** At the same two sites as the named-test closure (direct pre-apply boundary, enforce terminal), run
   the file with the candidate's validator through FS-1A (`TestExecutionReport`), under the C0 trust surface of the
   acceptance file's own path (conftests, runner config, declared dependencies equal to base, before and after).
5. **Judgment.** Every case marked for REQ-n present and passed in a COMPLETE fresh report and the runner succeeded
   → `record_requirement_claim(REQ-n, BEHAVIOR, method="acceptance_oracle", detail={file digest, cases, report})`.
   Anything else → the claim stays UNVERIFIED (a failing case may be recorded as deterministic counter-evidence:
   VIOLATED - owner decision).
6. **A1 under B2.** `freeze_time(0)` and `freeze_time(86400.5)` as two marked cases; the live helper-only candidate
   fails both (measured independently: TypeError), so REQ-1's BEHAVIOR stays open and the run is blocked; a correct
   candidate passes both, C0 proves preservation, mutation scope proves REQ-2 → genuine SUCCESS.

## 3. Pieces already in place

FS-1A report binding and parsing; FS-1C0 trust surface, base export and post-run re-check; FS-1C1 claims,
per-claim records and the composite closure; the two closure sites; SEC-009 / control-path write denial.

## 4. Why it is not bounded (MEASURED / TRACED) - the reason for stopping

- **Import layout.** The acceptance file must import the candidate package, but Kriya's pytest launcher removes the
  cwd from `sys.path`. MEASURED on inflect (importlib import mode, no `tests/__init__.py`): a test file run alone
  cannot import its own package. A file placed in `.kriya/acceptance/` has neither a package parent nor the project
  root on `sys.path` in any import mode, so B2 needs a Kriya-controlled way to put the project root (or `src/`) on
  the path for that run only - a change to the shared launcher (`PipBuildAdapter.run_tests`) that every Python gate
  uses and that FS-1A's output-identity guarantees pin.
- **Plugin / config interaction.** The acceptance run must apply the repository's own pytest configuration (rootdir,
  `pythonpath`, plugins) for the package to import as the project's own tests do, yet must not let candidate-controlled
  configuration decide it (C0 surface). Each layout (flat, `src/`, namespace, importlib mode) needs its own
  measured handling.
- **JVM.** A Java acceptance class must be compiled against the candidate's main classes and test classpath and run by
  Surefire/Gradle with a selector, from a location outside `src/test` (which the candidate may write and which C0
  treats as surface). That is a new build-integration path for Maven and Gradle, not a reuse.
- **Plumbing.** The input must reach the direct, milestone and enforce paths, checkpoints/resume (digest binding),
  requirement derivation, the Planner/Architect prompts (so plans do not fight the oracle), result reporting and
  `doctor`; and its SEC-009 classification decided (an operator file is user authority, like the goal).

## 5. Proposed staging (for review)

1. **B2-a (Python, flat and `tests/`-package layouts only):** input + binding + execution + judgment, with an
   explicit refusal (`ACCEPTANCE_LAYOUT_UNSUPPORTED`) for other layouts. A1 sentinel becomes closable.
2. **B2-b:** `src/` layouts and importlib mode (launcher path control, measured per layout).
3. **B2-c:** Maven/Gradle acceptance classes.
4. **B3:** human-bound acceptance tests (approval artifact outside the workspace, digest-bound; TOOL-002 pattern).

Each stage: reproducer first, mutation, full suite; no stage closes BEHAVIOR from candidate-written tests, model
output, or a pre-existing regression test.
