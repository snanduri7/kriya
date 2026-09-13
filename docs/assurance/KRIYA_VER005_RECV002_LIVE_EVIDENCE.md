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

**VER-005 — Python end-to-end validation/generation correctness: NEEDS_IMPLEMENTATION.**
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

- Production code: **none**. Per the DEFECT RULE ("do not immediately patch
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
