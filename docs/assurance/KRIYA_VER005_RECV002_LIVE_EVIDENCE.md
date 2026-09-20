# VER-005 / RECV-002 — E4 Live-Evidence Campaign (2026-09-13)

Bounded live-evidence campaign against production Kriya (real local model, real
repository/process execution) to determine whether VER-005 ("Python end-to-end
validation/generation correctness") and RECV-002 ("MA9 coordinated repair /
cross-subtask plan recovery") satisfy their E4 requirements. See
`KRIYA_PRODUCTION_RISK_REGISTER.md`'s VER-005 and RECV-002 entries for the
contracts this campaign tests against, and
`docs/assurance/KRIYA_PYTHON_CAPABILITY_SWEEP.md` for the pre-existing
capability-sweep gaps (multi-package topology: ABSENT; import/dependency
grounding: PARTIAL) this campaign specifically targets for VER-005.

## Baseline

- Branch `milestone-decomposition` @ `daf7fd9227ca47b81113bcba889a321723d8a7d1`
  (`daf7fd9`), matching the expected pre-campaign HEAD (STATE-001 closure).
- Repository worktree clean except pre-existing untracked clutter (per
  `feedback_ignore_untracked_files`) and the two unrelated modified files
  already present at session start (`kriya/workflow/review_context.py`,
  `tests/test_review_context.py`) — untouched by this campaign.
- No production code was changed by this campaign as part of the live runs
  themselves (see Findings/Disposition below for whether any change is
  warranted as follow-up).

## Environment

- OS: macOS 26.6.2 (Darwin 25.6.0), Apple Silicon.
- Python: 3.14.6 (`.venv`).
- Ollama: 0.33.3, `http://localhost:11434/v1`.
- Model (all roles — Planner/Architect/Developer/Reviewer/RunVerifier):
  `qwen3-coder:30b`, `num_ctx=32768`, `temperature=0.7` (Reviewer likewise
  0.7), `top_p=0.8`, `top_k=20`.
- Fallback chain: one entry, `qwen3.6:35b-a3b-q4_K_M`, `context_window=32768`,
  `reasoning=false`, `temperature=0.2` — not reached in any of the 3 runs
  (all completed on the primary model within the first attempt's model hop).
- `autonomy.mode`: human-in-the-loop, auto-approved via `-y`.
- `autonomy.max_consecutive_no_progress_attempts`: 2 (not exercised — no run
  reached a second full-set attempt).
- `autonomy.auto_index_missing_dependency_graph`: false (default) — Fixture 1
  and Fixture 2's first live run were never `kriya analyze`'d before
  generation; Fixture 2's second live run (Run 3) was `kriya analyze`'d first.
- No `autonomy.generation_time_budget_seconds` configured (confirmed via log:
  "Internal generation time budget: inactive") — no hard wall-clock budget;
  observed per-run wall-clock durations recorded below.
- `skills.load_global=false`, `skills.load_cwd=false`, `paths.skills="./skills"`
  in both fixtures' own `kriya.yaml` (CLAUDE.md's documented reproducible
  plain-Kriya configuration).
- Retry budget: `max(4, 1 + len(llm_chain))` = 4 full-set attempts.

## Fixtures

### Fixture 1 — Java, single-package (`~/kriya-live-validation/ver005-recv002-live-01/`)

Maven, Java 17, JUnit 5. `CustomerRecord` (record) + `CustomerValidator` with
two independent boolean methods: `isValid` (email format) and
`isPremiumEligible` (`.com`-domain based) — the MUST_PRESERVE target. 5
pre-existing JUnit tests, one (`dotComEmailIsPremiumEligible`) explicitly
protecting the preserved behavior. Baseline commit `2c0a8ab`.

### Fixture 2 — Python, multi-package (`~/kriya-live-validation/ver005-py-multipkg-live-01/`)

Two real top-level packages: `validation/` (`email_rules.py::is_valid_email`)
and `customer/` (`service.py`, importing `from validation.email_rules import
is_valid_email`, plus an independent `is_premium_eligible` — the MUST_PRESERVE
target). 8 pytest tests across `tests/test_email_rules.py` /
`tests/test_service.py`. Baseline commit `b5c582f`. Designed specifically to
exercise VER-005's own stated "multi-package topology" (ABSENT) and
"import/dependency grounding" (PARTIAL) gaps via a rename requiring
coordinated updates across the defining module, the calling module, and the
pre-existing tests.

## Live runs (3 of 3 permitted — see "Why a third run" below)

### Run 1 — Fixture 1, Java, single-package rename+add-check

- `run_id`: `20260913T225234-381bb19d` (trace `328d9fab`).
- Started: 2026-09-13T17:22:27Z. Duration: 46.6s. Attempts: 1 (0 retries).
- Goal: extend `isValid` to also require a non-blank `name`, without changing
  `isPremiumEligible`.
- Engineering route: initial `task/LOW/light` → escalated to `HIGH/heavy`
  (pom.xml touched → `build_system_change`/`dependency_change` flags fired;
  in fact the only pom.xml diff was a trailing-newline artifact — a real,
  if mildly oversensitive, escalation trigger, noted as incidental).
- `retrieved_chunks`: 0 (Fixture 1 was never `kriya analyze`'d) — Planner's
  own draft plan named a wrong path
  (`src/main/java/com/example/customer/validator/CustomerValidator.java`,
  with a spurious `validator/` segment) but the Developer/Architect stage
  self-corrected to the real path before writing; final `files_modified`
  was correct.
- Deterministic gate outcomes (trace DB, all `success: true`): `compile`,
  `goal_spec_compliance`, `regression_test` (full Maven `mvn test` run).
- Terminal result: `quality_gates_passed: true`. Reviewer text confirmed
  correctness and approval.
- **Final workspace diff** (uncommitted, applied from worktree to real
  workspace per the human-in-the-loop approval gate):
  ```java
  // isValid — CHANGED
  public boolean isValid(CustomerRecord customer) {
      return customer.email() != null && customer.email().contains("@")
          && customer.name() != null && !customer.name().trim().isEmpty();
  }
  // isPremiumEligible — BYTE-IDENTICAL, unchanged
  ```
  `pom.xml`: trailing-newline-only diff, no semantic change.
- **Independent verification (outside Kriya)**: `mvn test` re-run
  independently — 5/5 pre-existing tests pass (including the MUST_PRESERVE
  `dotComEmailIsPremiumEligible`). Pre-existing tests alone don't exercise
  the *new* blank/whitespace/null-name-rejection behavior (no baseline test
  name covers it), so a standalone `IndepCheck.java` was written and
  compiled/run against the real `target/classes` output, independently of
  Kriya's own verifier: `INDEPENDENT_ACCEPTANCE=PASS` for blank-name,
  whitespace-name, null-name rejection and valid-case acceptance.

**Verdict for this run**: clean, decisive, fully independently-confirmed E4
pass — real model, real candidate, real Maven compile+test boundary, real
terminal decision, atomicity/MUST_PRESERVE both held. Positive evidence for
VER-005's false-success-resistance contract on the Java stack (already the
strongest-covered stack per VER-001/002/003), and — since Language Scope for
VER-005 is explicitly Python — **not itself sufficient to close VER-005**.

### Run 2 — Fixture 2, Python, multi-package rename (repo not indexed)

- `run_id`: `20260913T225538-108275cc` (trace `4cda5ce6`).
- Started: 2026-09-13T17:25:25Z. Duration: 43.4s. Attempts: 1 (retry_strategy
  stopped early — see below).
- Goal: rename `is_valid_email`→`has_valid_email_format` across the defining
  module, its one caller, and all referencing tests; add an empty-local-part
  rejection case; preserve `is_premium_eligible`.
- `retrieved_chunks`: **0** — Fixture 2 had not yet been `kriya analyze`'d;
  no Graph RAG grounding was available to Planner/Architect/Developer.
- Result: `files_modified = ['tests/test_email_validation.py',
  'validation/email_validator.py', 'customer/customer_service.py']` — **all
  three are wrong, hallucinated filenames**, distinct from the real
  `validation/email_rules.py` / `customer/service.py` /
  `tests/test_email_rules.py` / `tests/test_service.py`. The real files were
  never touched (confirmed below).
- Deterministic gates on the *candidate's own (wrong-named) files*: `compile`
  succeeded, a 3-test `targeted_test` pytest run against the hallucinated
  test file succeeded (self-consistent but meaningless — it tested the
  wrong files against each other). The terminal gate — a runtime-verification
  step invoking `python3.14 customer/customer_service.py
  test_email_validation` — failed: `ModuleNotFoundError: No module named
  'validation'`, classified by Kriya as `VERIFICATION_INFRASTRUCTURE_FAILURE`
  / `attribution_tier: full_set` / `attribution_kind: INFRASTRUCTURE_DEFECT`
  ("production source-file attribution is intentionally disabled" for this
  category).
- `retry_strategy` log: *"Quality Gates stopped early - ...runtime command
  could not load its configured application entrypoint... Kriya stopped
  retrying early rather than burning its retry budget re-generating code
  that could never fix this - run `kriya doctor` to check your Java/Maven
  toolchain resolution."* — `retry.full_set_attempts: 1`, no second attempt,
  no RepairContract, no MUST_FIX/MUST_PRESERVE cycle. **No recovery was
  invoked.**
- Terminal result: `quality_gates_passed: false`. Reviewer text was
  optimistic about the *logic* ("the implementation itself appears correct
  for the requirements stated... The rejection is deterministic and not due
  to logic errors") but this optimism was never surfaced as approval —
  `review_included_in_approval: false`, and terminal `status` stayed
  failure. **Kriya did not report success despite an optimistic-sounding
  Reviewer verdict** — correct false-success resistance.
- **Independent verification (outside Kriya)**: `git -C <fixture2> diff` and
  `git status --short` — tracked-file diff is **empty**; the real
  `validation/email_rules.py`, `customer/service.py`,
  `tests/test_email_rules.py`, `tests/test_service.py` are byte-identical to
  baseline. The rejected candidate's wrongly-named files exist only as
  `.pyc` cache remnants under `.kriya/worktree/**/__pycache__/` (their `.py`
  source was written, executed, then removed before the worktree was left in
  its final state) — never copied to the real workspace. **Atomicity held**:
  rejected candidate never became accepted final state.

**Verdict for this run**: inconclusive for VER-005's positive breadth claim
(the candidate never actually exercised the real multi-package topology,
since generation never touched the real files) — but genuinely useful
negative-direction evidence (false-success resistance held even under an
optimistic Reviewer), and it surfaced the specific unresolved question
addressed by Run 3.

### Run 3 — Fixture 2, Python, multi-package edge-case-validation change (repo indexed first)

Justified as the campaign's permitted third run (task's own "a third live run
requires a specific unresolved evidence question from the first two"): Run 2
left one open, decisive question — was the wrong-filename candidate caused by
the *absence of Graph RAG grounding* (a confound, fixable by indexing first),
or does the same failure reproduce even when the model is correctly grounded
in the real files (a genuine defect)? `kriya analyze .` was run against
Fixture 2 first (7 files semantically indexed, confirmed via log:
"Discovered 7 files for semantic indexing... Semantic repository indexing
completed"), producing a real vector index and an auto-generated engineering
skill (`skills/auto-ver005-py-multipkg-live-01/`) — both left in place as
production Kriya artifacts of the indexing step, not hand-edited.

- `run_id`: `20260913T230532-ea0f24f7` (trace `58ef1a76`).
- Started: 2026-09-13T17:35:31Z. Duration: 45.9s. Attempts: 1 (same early
  stop as Run 2).
- Goal: extend `is_valid_email` with two new deterministic rejection cases
  (domain missing a `.`; domain containing consecutive dots), add covering
  tests, preserve `is_premium_eligible`. No execution/runtime language in the
  goal text (confirmed: `goal_requires_runtime_behavior(goal) == False` when
  evaluated directly against the exact goal string).
- `retrieved_chunks`: **5** (non-empty — real Graph RAG grounding present
  this time). `files_modified = ['validation/email_rules.py',
  'tests/test_email_rules.py']` — **the correct, real file paths.** The
  grounding confound from Run 2 is eliminated.
- Deterministic gates: `compile` succeeded; `targeted_test` — real `pytest`
  run against the real `tests/test_email_rules.py`, **7/7 passed** (the
  correct file, correctly extended, correctly tested). The terminal gate —
  runtime-verification step invoking `python3.14 tests/test_email_rules.py`
  directly as a script — failed with the **identical** error class:
  `ModuleNotFoundError: No module named 'validation'`, same
  `VERIFICATION_INFRASTRUCTURE_FAILURE` / `INFRASTRUCTURE_DEFECT`
  classification, same early-stop-no-recovery behavior
  (`retry.full_set_attempts: 1`).
- Terminal result: `quality_gates_passed: false`, despite `compile` and the
  real `pytest` run both passing on the correct files. Reviewer text again
  independently confirmed the logic was correct ("The core logic... appears
  correct... the package structure makes the entire submission
  non-executable") and again this was not surfaced as approval.
- **Independent verification (outside Kriya)**: worktree copies of all four
  real files (`validation/email_rules.py`, `customer/service.py`,
  `tests/test_email_rules.py`, `tests/test_service.py`) are byte-identical to
  the real workspace's own versions — i.e. the worktree candidate for the two
  touched files is available for inspection; real workspace files remain
  untouched (rejected candidate, same atomicity property as Run 2).

**Root cause, now decisively isolated**: with grounding confirmed present and
the correct real files correctly targeted, the deterministic `compile` and
`targeted_test` (pytest) gates both passed — the *candidate code was
correct*. The failure is entirely inside the runtime-verification step's
choice and invocation of a command. `RunVerifierAgent`'s own system prompt
(`kriya/agents/agent.py:2229+`) explicitly instructs: *"If there is no
runnable entrypoint at all worth verifying (a library, a config file, or the
goal doesn't describe observable behavior), set should_run to false"* and
*"Do NOT return a build-only command such as ... or a test command as proof
of observable runtime behavior."* Fixture 2 is exactly this case (pure
library code, no CLI/service entrypoint, goal text confirmed not to require
runtime behavior) — yet twice, on two different goals against two different
(one hallucinated, one real) file sets, the live model's `RunVerifierAgent`
judgment selected a test-shaped file as a `"finite_command"` runtime target
anyway. (The literal `should_run`/`reasoning` judgment payload is not
persisted in `traces.db`'s `run_events`/`evidence_records` — noted below as
an incidental observability gap — but `should_run=true` is a necessary
precondition of the code path that executed the command at all, per
`kriya/workflow/attempt.py:2875`: `if not judgment.get("should_run"): return`.)

Once that judgment fires, Kriya's execution layer runs the chosen file via a
bare `python3.14 <relative-path>` subprocess call. Python's own import
resolution for a directly-executed script only adds the *script's own
containing directory* to `sys.path[0]` — never the process's working
directory/repository root — so any script under `tests/` (or, in Run 2's
hallucinated case, `customer/`) that imports a sibling top-level package
(`validation`) fails with `ModuleNotFoundError` regardless of whether that
package's own content is correct. This is unrelated to `goal_requires_
runtime_behavior()` (confirmed by direct evaluation to return `False` for
Run 3's goal) — that function only gates the *stricter*, hard-required path
(`ctx.runtime_verification_required`); `RunVerifierAgent.judge()` itself is
invoked unconditionally on every attempt whenever `autonomy.
run_verification_enabled` is true (`kriya/workflow/attempt.py:2836`), and its
own `should_run` verdict is what actually gates execution (line 2875) —
independent of the deterministic goal-text heuristic. There is no
deterministic Kriya-side trigger bug; the trigger is squarely the judge
model's own (twice-reproduced) instruction-following gap.

## Why no fourth run

Both open questions from Runs 1-2 are now resolved by Run 3: (a) VER-005's
Python multi-package breadth gap is exercised and the real files are
correctly generated/tested when grounding is present; (b) the failure
reproduces identically with grounding present, isolating root cause to the
runtime-verification judge/execution path rather than generation-correctness
or Graph RAG grounding. Per the campaign's own "do not run repeated model
attempts merely for statistical confidence" instruction, a fourth run would
not answer a new question — it would only re-sample the same live-model
reliability gap. Capped at 3 (the permitted maximum given one specific
unresolved evidence question from the first two runs).

## RECV-002 evidence

No run reached the MA9 recovery path. Run 1 succeeded on the first attempt
(nothing to recover from). Runs 2 and 3 both failed with
`failure_category: environment_failure` /
`attribution_kind: INFRASTRUCTURE_DEFECT` / `attribution_tier: full_set`, and
Kriya's own `retry_strategy` deliberately declined to retry
("stopped retrying early rather than burning its retry budget re-generating
code that could never fix this"), saving a `--resume-id` checkpoint instead
of invoking RepairContract/MUST_FIX/MUST_PRESERVE machinery. This is a real,
useful finding in its own right — **the recovery path is structurally
unreachable whenever a failure classifies as an infrastructure defect**,
which is Kriya's own deliberate, correct design choice (regenerating code
cannot fix a verifier-infrastructure problem) but means this fixture shape
can never be used to observe RECV-002's live recovery cycle. A future
RECV-002 live campaign needs a first-attempt failure that Kriya classifies as
an ordinary candidate defect (a wrong compile/test outcome attributable to
the Developer's own code), not an infrastructure-classified one.

No adverse-recovery case, no MUST_FIX/MUST_PRESERVE live cycle, and no
no-progress-bound observation were obtainable from this campaign — the
pre-existing deterministic E2/E3 evidence (42+ tests in
`tests/test_workflow_controller_enforce.py`) remains the only evidence for
those specific properties. RECV-002 remains open for a differently-shaped
future live attempt; per the task's own explicit cap, this campaign does not
attempt a fourth run to manufacture one.

## Findings summary

1. **VER-005 false-success resistance: held, 3/3.** No run reported terminal
   success against a real deterministic failure, including two runs where the
   Reviewer's own text was optimistic about candidate correctness. Real
   compiler/test/runtime boundary, real terminal decision, in all 3 runs.
2. **VER-005 workspace atomicity: held, 2/2 rejected-candidate runs.**
   Rejected Python candidates (Run 2, Run 3) never left `.kriya/worktree/`;
   real workspace files stayed byte-identical to baseline in both cases.
3. **VER-005 concrete defect (spurious-failure direction): `RunVerifierAgent`
   reproducibly (2/2 observed instances, two different goals, both
   correctly-grounded-or-not) selects a test-shaped file as a runtime
   verification target for a pure-library Python goal with no runtime
   behavior described, contrary to its own system prompt's explicit
   instruction. Kriya's execution layer then invokes it via bare `python
   <path>`, which cannot resolve a sibling top-level package import for any
   multi-package layout — producing a `ModuleNotFoundError`-based
   infrastructure failure unrelated to candidate correctness, which
   `retry_strategy` correctly treats as unrecoverable-by-regeneration and
   therefore never retries.** This blocks a genuinely correct candidate
   (confirmed independently correct by real `compile`+`pytest` gates both
   passing in Run 3, and by the Reviewer's own text in both Run 2 and Run 3)
   from ever reaching terminal success for this fixture shape. Per the
   DEFECT RULE, the VER-005 closure claim is stopped; disposition is
   NEEDS_IMPLEMENTATION, not CLOSED.
4. **RECV-002: recovery path unreached in all 3 runs.** Run 1 needed no
   recovery; Runs 2-3 were both short-circuited by the infrastructure-defect
   classification before any RepairContract/MUST_FIX/MUST_PRESERVE cycle
   could engage. Disposition remains NEEDS_EVIDENCE — a real defect was not
   observed in the recovery machinery itself (it was never reached), so this
   is not a NEEDS_IMPLEMENTATION finding for RECV-002.
5. **Incidental**: Run 2's terminal log text ("run `kriya doctor` to check
   your Java/Maven toolchain resolution") is hardcoded Java/Maven-specific
   language surfaced on a pure-Python run — a narrow, cosmetic,
   architecture-neutral wording bug in the generic
   verification-infrastructure-failure message.
6. **Incidental**: the `RunVerifierAgent` judgment payload (`should_run`,
   `run_commands`, `command_source`, `reasoning`) is not persisted anywhere
   in `traces.db`'s `run_events`/`evidence_records` — only its downstream
   execution consequences are. This made root-causing Run 2/3 slower than
   necessary and is worth a future low-risk observability addition (append an
   evidence record for the judgment itself), but is not itself a correctness
   defect.
7. Run 1's Planner draft named a nonexistent path (spurious `validator/`
   path segment) that the Architect/Developer stage self-corrected before
   writing — the same broad "planner-stage path grounding can be shaky
   without indexing" theme as finding #3, but self-healing in this instance
   and not pursued further (out of scope; VER-005's own contract is about
   the terminal verification boundary, not planner-stage draft accuracy).

## Per-risk disposition

**Superseded for VER-005** — see "VER-005 implementation (2026-09-14)" and
its own "VER-005 disposition update" below: **CLOSED**, following the fix and
live revalidation described there. This section is kept as the original,
dated (2026-09-13) evidence-only record and must not be edited to match the
later outcome.

**VER-005 — Python end-to-end validation/generation correctness: NEEDS_IMPLEMENTATION (2026-09-13, superseded 2026-09-14).**
The false-success-resistance and atomicity properties this risk's E4 bar
cares about most directly are now live-confirmed (finding #1, #2) — do not
weaken or re-litigate those in a future pass. But a concrete, reproduced,
precisely-classified defect (finding #3) blocks CLOSED: a correct Python
library-only candidate cannot reach terminal success whenever the
`RunVerifierAgent` judge mis-selects a test file as a runtime target, which
happened in 2 of 2 opportunities to observe it live in this campaign. Keep
the two directions (false-success resistance: solid; spurious-failure via
run-verifier judge: real defect) separately tracked in the register — the
positive evidence does not offset the defect, and the defect does not erase
the positive evidence.

**RECV-002 — MA9 coordinated repair / cross-subtask plan recovery: NEEDS_EVIDENCE (unchanged).**
No defect was observed in the recovery machinery — it was structurally never
reached. The pre-existing deterministic E2/E3 evidence stands as-is. A future
live campaign needs a fixture/goal combination whose first-attempt failure
Kriya classifies as an ordinary (non-infrastructure) candidate defect.

## Production/test/doc changes

**Superseded** — see "VER-005 implementation (2026-09-14)" above for the
actual production/test/doc changes made in the follow-up implementation
pass. This section remains the original, dated (2026-09-13) evidence-only
record.

- Production code: **none** (as of 2026-09-13, this evidence-only pass). Per the DEFECT RULE ("do not immediately patch
  unless [the] change is narrow and architecture-neutral"): a candidate fix
  exists in outline (route test-shaped runtime targets through `pytest`/
  `-m`-qualified invocation, or set the subprocess `PYTHONPATH` to the
  repository root) but selecting and scoping it is verification-subsystem
  invocation-policy design — it affects which file shapes get which
  invocation strategy across both the Python and Java runtime-verification
  paths this same code serves — not a narrow patch. Left for a dedicated,
  separately-scoped follow-up, not attempted in this evidence-only session.
- Test/doc changes: this evidence document; a narrow deterministic
  reproducer test is the next step (see below), not yet added as of this
  commit — see NEXT_ACTION.
- Risk register: VER-005 and RECV-002 entries updated to reflect the
  dispositions above; no other risk rows touched.

## VER-005 implementation (2026-09-14) — deterministic Python runtime-target grounding

Closes finding #3 above. Full task: fix and close the spurious-failure defect
this campaign found, without redesigning verification architecture, without
weakening VER-006/deterministic gates, and with one live revalidation against
the real production model.

### Root-cause decomposition (Task 1)

Traced end to end: `RunVerifierAgent.judge()` result → target/command
selection → runtime-verification interpretation → language/project detection
→ command construction → cwd selection → environment construction →
subprocess execution → result classification → quality gate → terminal
decision.

- **(A) Invalid runtime target chosen by the verifier** — confirmed, the
  primary cause. A pure-library goal has no runnable entrypoint; the judge
  selected a test file anyway, contrary to its own system prompt.
- **(B) Valid target executed using the wrong invocation** — a secondary,
  independently real mechanism: even a genuinely correct application target
  living inside a package (not just this test-file case) would misresolve
  under bare `python <path>` if it imports a sibling top-level package, since
  bare-script mode only ever puts the *script's own* directory on
  `sys.path[0]`.
- **(C) Wrong working directory** — **ruled out**. `PolymorphicValidator.
  run_app_sequence()`/`run_app()` (`kriya/tools/validate.py`) already
  hardcode `cwd=self.workspace_path` unconditionally, for every command,
  independent of target path. Confirmed by direct code read; not itself a
  contributor to the defect. `-m` module invocation and this already-correct
  cwd are what interact to fix the ModuleNotFoundError below.
- **(D) Missing repository-root/module semantics** — confirmed, the second
  primary cause. Python's own `-m` machinery puts the *current working
  directory* on `sys.path[0]` instead of the script's own directory; nothing
  in Kriya ever used `-m` for a package-nested target, so this correct
  resolution mechanism was simply never engaged.
- **(E) Runtime verification incorrectly required for a library/non-runnable
  goal** — **ruled out**. `ctx.runtime_verification_required` (driven by
  `goal_requires_runtime_behavior()`) was `False` for both failing goals
  (confirmed by direct evaluation against the exact goal text); the defect is
  `RunVerifierAgent.judge()`'s own `should_run` verdict, not the deterministic
  required/optional heuristic.
- **(F) Multiple factors** — **the defect is A+D combined**: an invalid
  target (A) executed via an invocation mechanism (bare-script) that cannot
  resolve repository-root imports (D). Root cause is not merely `PYTHONPATH`
  — no environment variable was touched or needed; the fix is a target-
  validity decision (A) plus an invocation-mechanism decision (D), not an
  environment patch.

### Architectural rule maintained

```
LLM verification intent -> deterministic target validation -> deterministic invocation policy -> controlled execution -> deterministic evidence -> terminal decision
```

Mirrors the existing, already-shipped Java precedent exactly:
`ground_java_entrypoint_in_no_build_file_projects()` (`kriya/workflow/
file_resolution.py`) already implements this identical boundary for Java —
"never build a second execution architecture if one already exists" (this
task's own instruction). The new Python function,
`ground_python_runtime_target()`, is its sibling: same call sites, same
`None`-sentinel-means-"force should_run=False" contract, same "goal_explicit
is authoritative, never touched" rule, same "never guess when ambiguous"
posture.

**LLM contribution vs. deterministic ownership**, made explicit in code for
the first time this pass:
- The LLM (`RunVerifierAgent.judge()`) still proposes *what* to verify and
  *a* candidate target/command — unchanged.
- Kriya now deterministically decides, from real repository facts read fresh
  off disk every attempt (never from `judge()`'s own claims): whether the
  proposed target is an executable application target, a test artifact, a
  library file with no entrypoint, a directory, or nonexistent; whether a
  real, unambiguous substitute exists; and whether the correct invocation is
  module (`-m`) or bare-script form. `judge()`'s raw output is never executed
  directly — it passes through this validation/correction layer first, on
  every attempt, both call sites, no exception.

### New production code (`kriya/workflow/file_resolution.py`)

- `python_file_has_main_guard(content)` — real, top-level `if __name__ ==
  "__main__":` guard detection (the Python sibling of `DependencyGraph.
  find_java_main_class()`'s real-`main()`-method detection).
- `python_target_path_is_test_shaped(rel_path)` — filename-regex
  (`is_runnable_test_file`) **and** `tests/`/`test/` directory-segment
  detection (Task 3: "consider the tests/ hierarchy, not only filename
  regexes").
- `_extract_python_command_target(cmd)` — structural-only extraction of the
  argv token (or, for `-m`, the dotted module converted to its file-path
  shape) a python-interpreter command would execute; unifies bare-script and
  `-m` invocation shapes so both are validated identically (closes the gap a
  reviewing pass identified: a model could emit `python -m tests.test_x` as
  easily as `python tests/test_x.py`, the same violation in different
  clothing).
- `python_command_targets_test_path(cmd)` — the narrow, grounding-fact-free
  predicate the MANAGED_SERVICE path (below) uses.
- `_grounded_python_invocation(...)` — the actual invocation-POLICY decision:
  converts a bare-script target to `-m dotted.module` form *only* when every
  directory from the workspace root down to the file is a real Python
  package (contains `__init__.py`) — the exact condition under which `-m`
  puts the workspace root (not the script's directory) on `sys.path[0]`. A
  workspace-root script or one under a non-package directory is left as
  bare-script form, unchanged — zero behavior change for the common flat-
  script case.
- `ground_python_runtime_target(run_commands, command_source,
  all_python_files, package_dirs, entrypoint_files)` — the main correction
  function, called from both `kriya/workflow/attempt.py` call sites (and,
  narrowly, from `kriya/workflow/milestones.py`'s own third judge() call
  site — see Bypass sweep below). Returns `run_commands` unchanged when
  `command_source == "goal_explicit"` or the target is already grounded and
  correctly invoked; returns a corrected sequence (target substituted, and/or
  invocation converted to `-m` form) when exactly one real, unambiguous
  entrypoint exists to substitute; returns `None` — the same "force
  should_run=False" sentinel Java's own function uses — when the target is
  invalid and there is not exactly one unambiguous substitute (zero: a
  genuine library; more than one: genuinely ambiguous, never guessed).

Repository facts (`all_python_files`, `package_dirs`, `entrypoint_files`) are
resolved **repository-wide from the real filesystem** every attempt
(`kriya/workflow/attempt.py::_build_python_runtime_grounding()`/
`_collect_python_runtime_grounding_facts()`), never scoped to this run's own
`state.all_files_written` the way Java's `java_main_classes` map is — a
brownfield repository's entrypoint/package structure routinely predates the
current attempt (a pre-existing `main.py` never touched this run, a
pre-existing `validation/__init__.py`), so run-local scoping would silently
reintroduce a false-negative mirror of the same defect (a real, untouched
entrypoint rejected as "nonexistent"). Symlink-safe by construction:
`is_within_scope()`/`make_workspace_scope()` (`kriya/policy/filesystem.py` —
the same idiom `run_app_sequence()`'s own `javac -d` containment check
already uses) filter the walk itself, and — independently — a judge-proposed
absolute or `..`-escaping path can never become a member of the resulting
sets at all (the sets only ever contain paths the walk itself discovered
inside the root), so an out-of-workspace target is always treated exactly
like a nonexistent one, never resolved against the real filesystem.

### Call sites patched (Task 19 bypass sweep)

| # | Location | Target source | Fix applied |
|---|----------|---------------|-------------|
| 1 | `attempt.py::_execute_runtime_verification_directly` (verification-only subtask path) | `judge()` `finite_command` | `ground_python_runtime_target()`, mirrors the adjacent Java block |
| 2 | `attempt.py`'s mutating-path inline Quality-Gates block | `judge()` `finite_command` | `ground_python_runtime_target()`, mirrors the adjacent Java block |
| 3 | `attempt.py`'s self-correction micro-loop re-run (`run_app_sequence` call inside `run_self_correction_loop` resolution) | `resolved_run_commands`, derived from `judgment["run_commands"]` | **Inherits grounding** — `resolved_run_commands = [_resolve_run_command(cmd, ...) for cmd in judgment["run_commands"]]` reads the ALREADY-corrected value from call site #2; confirmed by direct code trace, no separate fix needed |
| 4 | `attempt.py::_validate_and_convert_managed_service_contract` (`MANAGED_SERVICE` path) | `judgment["managed_service"]["service_command"]` | `python_command_targets_test_path()` — test-shape **rejection only** (fails closed with `MANAGED_SERVICE_CONTRACT_INVALID`); no substitution attempted — there is no safe deterministic "run this other service instead" (unlike `FINITE_COMMAND`, which has one) |
| 5 | `milestones.py`'s own independent `judge()` call (captured for later replay) | `judge()` `finite_command` | `ground_python_runtime_target()` — Python-only; this path has no live evidence of the Java-equivalent defect and `_build_java_main_class_map`'s signature is `AttemptContext`-coupled, not plumbed through this call site — disclosed, not silently expanded |
| 6 | `milestones.py::replay_prior_milestone_verifications` (later re-execution of #5's captured command) | Persisted `run_state.verification_commands[id]` | **Inherits grounding** — replays whatever #5 already captured post-correction; confirmed by direct code trace |
| 7 | `PolymorphicValidator.run_app()` (single-command variant) | — | **Zero production call sites found** (`grep -rn "\.run_app("` across `kriya/` — empty) — not a live bypass |
| 8 | Java (`mvn`/`javac`/`java`) paths, all call sites | `judge()` | **Pre-existing, unmodified** — `ground_java_entrypoint_in_no_build_file_projects()` already covers this; re-verified unaffected (see Tests below) |

`UNVALIDATED_LLM_RUNTIME_TARGET_EXECUTION_PATHS = 0`,
`BARE_TEST_AS_APPLICATION_PATHS = 0` — every real production path that can
reach a subprocess `execve` for an LLM-proposed Python target now passes
through deterministic validation first (paths 3 and 6 by inheritance, not
independent re-implementation). The one honest asymmetry: path 4
(`MANAGED_SERVICE`) rejects a test-shaped target but never substitutes a
real one (no safe deterministic substitute exists for a long-running
service) — disclosed here, not claimed as identical treatment to
`FINITE_COMMAND`.

### Prompt-strengthening explicitly not treated as the fix (Task 6)

`RunVerifierAgent`'s system prompt already told the model not to do this
("Do NOT return a build-only command such as ... or a test command as proof
of observable runtime behavior") — confirmed live, twice, that the
instruction alone did not prevent the defect (Run 2, Run 3). No prompt text
was changed this pass. Enforcement is entirely post-model, deterministic, and
proven again in Run 4 below: the live model **still** proposed an invalid
target naturally (log: `2026-09-13 23:58:56 ... "Deterministic Python
runtime-target grounding: the run-verification judge's proposed target is
not a valid application runtime target ..."`), and the deterministic layer
caught it.

### False-negative / false-positive proof (Task 7)

- **False-negative prevention**: Run 4 below is the live proof — a genuinely
  correct candidate (real compile + real 7/7 pytest pass) is no longer
  blocked by a bad verifier-proposed target.
- **False-positive prevention**: `tests/test_ver005_python_runtime_target_
  grounding.py::test_bad_candidate_still_rejected_after_grounding` confirms
  grounding correction never launders a wrong candidate's output into a
  clean one — correction changes *which command runs and how*, never the
  pass/fail verdict itself, which stays owned entirely by the unmodified
  compile/test/grade pipeline. VER-006's own 19-test distrust-containment
  suite (`tests/test_ver006_distrust_containment.py`) — untouched by this
  change, re-run manually this pass, 19/19 still pass — remains the
  authoritative false-positive-protection evidence; this pass's own new test
  is a narrower, adjacent confirmation, not a replacement for it.

### Tests added

- `tests/test_ver005_python_runtime_target_grounding.py` (23 tests): pure
  function coverage (`python_file_has_main_guard`, `python_target_path_is_
  test_shaped`, `python_command_targets_test_path`); the exact E4 shape
  (library, test-file target → not applicable); a grounded-app fixture
  (bare-script guess corrected to `-m` form; the corrected form actually
  executes correctly through real, non-mocked `run_app_sequence()`; a
  genuinely bad candidate is still rejected); the full Task 11 adversarial
  set (nonexistent target with 0/1 entrypoints, out-of-workspace paths,
  symlink-escape containment, directory target, `__init__.py` target,
  malformed/no-target commands, ambiguous 2-entrypoint case, `goal_explicit`
  passthrough, `-m` test-module rejection, `-m` stdlib-module passthrough);
  cwd-independence (Task 12, real subprocess run after `os.chdir()` to a
  different directory); `MANAGED_SERVICE` test-shape rejection (bare-script
  and `-m` forms) and legitimate-target passthrough.
- `tests/test_workflow.py::test_run_attempt_disables_run_verification_end_
  to_end_for_python_test_shaped_target` (new) — the Python sibling of the
  existing Java end-to-end regression test, reproducing the exact E4 fixture
  shape through the REAL, unmocked `run_attempt()` mutating path (call site
  #2), asserting `run_app_sequence` is never called.
- `tests/test_milestones.py::test_run_milestones_grounds_python_verification_
  commands_before_capture_for_replay` (new) — proves call site #5 (the third,
  previously-undiscovered bypass) through the real `run_milestones()`
  orchestrator, asserting the bad command is never persisted for replay.
- `tests/test_polymorphic_validation.py::test_run_app_sequence_multi_
  package_test_file_target_hits_module_not_found` — **kept unmodified**
  (Task 18); one docstring line added noting prevention now lives upstream.
  Manually re-verified still passes (proves the underlying OS-level failure
  mode and its correct infrastructure classification are unchanged — this
  test documents the mechanism, not the absence of a bypass).
- Existing Java tests re-run manually, unaffected: 5 pure
  `ground_java_entrypoint_*` unit tests, plus both existing end-to-end Java
  regression tests through the two modified `attempt.py` call sites
  (`test_run_attempt_deterministically_corrects_java_entrypoint_end_to_end`,
  `test_verification_only_java_entrypoint_launch_failure_remains_
  infrastructure`).
- All 19 `tests/test_ver006_distrust_containment.py` tests re-run manually,
  unaffected.
- All 68 tests in `tests/test_milestones.py` re-run manually, unaffected.
- All 6 existing `MANAGED_SERVICE` contract tests re-run manually, unaffected.
- **No production code change was validated by self-testing alone** — see
  Run 4 below for live, real-model confirmation.

### Cosmetic wording fix (Task 17)

`kriya/cli.py`'s two identical hardcoded `"...to check your Java/Maven
toolchain resolution."` strings (in the `generate`/`fix` CLI's own
environment-failure reporting — the exact message class Run 2's terminal log
surfaced on a pure-Python run, finding #5 above) changed to `"...to check
your language toolchain resolution."` — narrow, wording-only, no decision
logic touched.

### Run 4 — live revalidation (1 of 2 permitted)

Fresh copy of Fixture 2 (`~/kriya-live-validation/ver005-py-multipkg-live-
02-run4/`, baseline `da43645`, re-derived from the same `b5c582f` tree via
`git archive` — no carried-over `.kriya/`, `skills/`, or prior-run logs, per
this pass's own advisor guidance to eliminate that variable), `kriya.yaml`
identical (`skills.load_global=false`, `skills.load_cwd=false`,
`paths.skills="./skills"`). `kriya analyze .` run first (7 files indexed,
fresh auto-skill generated), matching Run 3's own setup. Goal text **byte-
identical to Run 3**, copied verbatim from this document. Model unchanged:
`qwen3-coder:30b`. No steering of `RunVerifierAgent`'s target choice.

- `run_id`: `20260913T235820-4679c5ce` (trace `16edc152`).
- Started: 2026-09-13T23:58:12Z. Duration: 46.3s (`generation_metrics.
  total_wall_seconds`). Attempts: 1 (`retry.full_set_attempts: 0`).
- `files`: `['tests/test_email_rules.py', 'validation/email_rules.py']` —
  the correct, real paths (grounding present, as expected post-`analyze`).
- **`RunVerifierAgent.judge()` still naturally proposed an invalid target**
  (confirmed by the deterministic-grounding log line firing — the raw
  judgment payload itself remains unpersisted, the same disclosed
  observability gap noted in finding #6; its invalidity is proven
  necessarily by the correction firing at all): log, 2026-09-13T23:58:56Z:
  *"Deterministic Python runtime-target grounding: the run-verification
  judge's proposed target is not a valid application runtime target
  (test-shaped, nonexistent, or unguarded), and no unambiguous real
  entrypoint exists elsewhere in the repository to substitute - overriding
  should_run to False instead of executing an invalid target."*
  `should_run` forced to `False` — correct, since this fixture is a genuine
  library with zero real entrypoints anywhere (confirmed independently
  below).
- **Deterministic gate outcomes** (`traces.db`, all `success: true`):
  `compile`; `targeted_test` — real `pytest`, **7/7 passed** against the real
  `tests/test_email_rules.py`; `goal_spec_compliance`; `regression_test` —
  real `pytest`, **11/11 passed** (`tests/test_email_rules.py` +
  `tests/test_service.py`, confirming `is_premium_eligible`'s own test still
  passes). **No `run_verification` gate outcome at all** — correctly absent,
  matching the Java precedent's identical "nothing to run" behavior; runtime
  verification was never attempted, not attempted-and-passed.
- **Terminal result**: `quality_gates_passed: true`. Reviewer text confirmed
  correctness and was included in the approval (`review_included_in_
  approval: true`).
- **Independent verification (outside Kriya)**: `git diff --stat` — exactly
  the 2 expected files changed (`validation/email_rules.py`,
  `tests/test_email_rules.py`); `customer/service.py` byte-identical to
  baseline (confirmed via `git status --short` — no `M` for that file).
  Direct Python import and execution (no pytest available on the ambient
  system interpreter; a project-local venv resolution isn't necessary for
  this independent check) re-confirmed, outside Kriya entirely: `is_valid_
  email('user@example.com')==True`, `is_valid_email('user@localhost')==
  False`, `is_valid_email('user@example..com')==False`, plus the pre-existing
  cases (`''`, `None`, baseline valid) all correct; `is_premium_eligible`
  re-confirmed unchanged (`.com`→`True`, `.org`→`False`) — MUST_PRESERVE held.

**Verdict**: decisive on the first live attempt — both required outcomes
from this pass's own live-run protocol are satisfied simultaneously
(`should_run` correctly forced `False`, and compile+pytest evidence alone
carried a genuinely correct candidate to terminal success). No second live
run needed or attempted.

### VER-005 correction (2026-09-14, same day)

Found via the user's own independent `.venv/bin/pytest` run against the
commit that included Run 4 and the closure claim above — **not** caught by
this pass's own manual/standalone self-testing. 20 real test failures, all
in `tests/test_workflow.py`, all sharing one root cause.

**Root cause of the regression**: `python_file_has_main_guard()` (the
function backing `entrypoint_files` in `_build_python_runtime_grounding()`)
required a literal `if __name__ == "__main__":` guard to classify a Python
file as a real application entrypoint — a too-literal transliteration of
Java's own `find_java_main_class()` requirement (which genuinely does
require a `public static void main` method — Java has no other way to be
an entrypoint). Python has no equivalent required construct: **any**
top-level statement produces observable behavior when a file is executed
directly via `python <path>`, with or without a guard — the guard only
matters for distinguishing "this file is also meant to be imported as a
library," never for "is this file runnable at all." Every one of the 20
failing tests used a generated `app.py` whose entire content was a single
bare `print(...)` statement with no guard — an extremely common,
completely legitimate script shape — which the guard-only check
misclassified as "no runnable entrypoint," silently forcing
`should_run=False` and skipping runtime verification for goals that
explicitly needed it run and graded.

**Fix**: `python_file_has_main_guard()` renamed and rewritten as
`python_file_is_runnable_script()` (`kriya/workflow/file_resolution.py`),
now AST-based rather than regex-based: parses the file and inspects only
`tree.body` (true top-level statements, never nested inside a function/
class) — a file is runnable if that body contains anything beyond a
`def`/`class`/`import`/module-docstring/simple-constant-assignment (an
`Expr` call like `print(...)`, an `if`/`for`/`while`/`with`/`try`, or the
`__main__` guard itself — now just one specific case of "any other
top-level statement," not a special-cased regex). A pure library file
(only definitions/imports/docstring/constants at module level) is still
correctly classified as having no observable entrypoint — the distinction
this whole mechanism exists to make is preserved exactly; only the
over-strict guard-literal requirement was wrong.

One of the 20 failures needed a second, narrower fix beyond the rename:
`test_run_attempt_allowlist_subtask_still_executes_declared_runtime_
verification` (`tests/test_workflow.py`) marked `manage.py` as an
already-established file (`state.all_files_written = {"manage.py"}`)
without ever writing its content to disk. VER-005's own grounding
correctly reads real repository content from disk (worktree-first, per its
own design — see "New production code" above) — a name-only "established"
file with no backing content is indistinguishable from a genuinely
nonexistent one, and treating it as grounded anyway would have meant
trusting an unbacked claim, exactly what this whole mechanism exists to
stop doing. The test now writes realistic Django `manage.py` content
(matching what a real, earlier-completed subtask would actually have left
on disk) rather than the production code being weakened to tolerate a
content-less "established" file.

**Re-verification after the fix** (standalone harness, never
`.venv/bin/pytest` per this repository's standing quota-discipline
convention): all 23 tests in `tests/test_ver005_python_runtime_target_
grounding.py`; all 33 tests in `tests/test_workflow.py` that construct a
python-shaped `run_commands` (a superset of the original 20 failures, swept
broadly rather than spot-checked); all 68 tests in
`tests/test_milestones.py`; all 19 tests in `tests/test_ver006_distrust_
containment.py`; the 5 pure `ground_java_entrypoint_*` unit tests plus both
existing Java end-to-end regression tests through the two modified
`attempt.py` call sites; the original reproducer,
`tests/test_polymorphic_validation.py::test_run_app_sequence_multi_
package_test_file_target_hits_module_not_found` — **150 tests total, 0
failures.** Disposition unchanged by this correction: still **CLOSED**, the
underlying architecture (deterministic target validation → deterministic
invocation policy) was correct throughout; only the Python-specific
"what counts as runnable" predicate needed correcting.

### VER-005 disposition update

**CLOSED (2026-09-14).** All Task-level acceptance criteria met: runtime
applicability is now deterministic (Task 2); LLM-suggested execution targets
are validated post-model, not trusted (Task 3); test files cannot be blindly
executed as applications, confirmed for both bare-script and `-m` forms
(Task 6); Python invocation respects package/module semantics via the new
`-m` conversion (Task 4); library-only projects no longer receive fabricated
runtime targets (Tasks 2, 9, live-confirmed Run 4); a runnable Python app
still receives real runtime verification with the correct grounded invocation
(Task 10, live-confirmed via the app-fixture end-to-end test); invalid-target
handling is distinguished from candidate runtime failure in both direction
(Tasks 14, 15); cwd/environment remain exactly as deterministic and bounded
as before (Tasks 12, 13 — zero environment changes, cwd was already correct);
no false-success regression (VER-006 unaffected, re-confirmed); no
false-negative regression for the E4 fixture (Run 4); Java (VER-006)
unchanged and re-confirmed; security controls unchanged (SEC-001/003/005 not
touched; the new grounding walk reuses the existing symlink-safe containment
idiom); `UNVALIDATED_LLM_RUNTIME_TARGET_EXECUTION_PATHS = 0`,
`BARE_TEST_AS_APPLICATION_PATHS = 0` (bypass sweep table above); one real
local-model revalidation confirms the production path (Run 4); `UNOWNED = 0`,
`UNEXPLAINED = 0`.

**Closure statement**: Runtime-verification targets proposed by an LLM are
not directly executable authority. Kriya deterministically validates runtime
applicability, target role, and language/project invocation semantics before
execution; invalid/test-shaped targets cannot create spurious candidate
failure or bypass deterministic verification.

## Reproducibility

- Fixture 1: `~/kriya-live-validation/ver005-recv002-live-01/` (git repo,
  baseline `2c0a8ab`, kriya config `1dc17e5`), run log
  `kriya_run_output.log`, goal recorded in the Planner section of that log.
- Fixture 2: `~/kriya-live-validation/ver005-py-multipkg-live-01/` (git repo,
  baseline `b5c582f`), Run 2 log `kriya_run_output.log` (goal in
  `traces.db`'s `runs.goal` for `run_id=4cda5ce6`), Run 3 log
  `kriya_run3_output.log` / goal `kriya_run3_goal.txt` (`run_id=58ef1a76`).
  `traces.db` under each fixture's own `.kriya-logs/` retains the full
  structured trace (`run_events`, `evidence_records`, `gate_outcomes`,
  `generation_metrics`) for all 3 runs. Per repository convention, these
  fixture directories and their logs live outside `docs/assurance/` and are
  not committed to the Kriya repository itself — this document is the
  summarized, reproducible evidence artifact.
- Run 4 (2026-09-14, VER-005 implementation live revalidation):
  `~/kriya-live-validation/ver005-py-multipkg-live-02-run4/` (fresh git repo,
  baseline `da43645`, re-derived from `b5c582f`'s tree via `git archive`),
  goal `kriya_run4_goal.txt` (byte-identical to Run 3's), output log
  `kriya_run4_output.log`, `run_id=20260913T235820-4679c5ce`
  (trace `16edc152`), full structured trace in that fixture's own
  `.kriya-logs/traces.db`.
