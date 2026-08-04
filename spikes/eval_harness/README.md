# Eval harness spike

Throwaway-feeling, not wired into `kriya/cli.py` - same posture as
`spikes/version_b_routing/`. Not yet proven to be a "run this forever" tool;
if it earns its keep it can graduate to a first-class `kriya evals` command
later, but shipping that now would add packaging weight before the value is
proven.

## Question this answers

Three days of live validation on one app (Ignite+Qpid+Person messaging)
surfaced six real Kriya bugs - a great return early on, but diminishing:
depth on a single app tells you about that app, not about Kriya generally.
This asks the broader question: **across a small but genuinely diverse set
of goals, what actually fails, and how often - ranked by category, not just
whatever the last run happened to hit?** That ranking is what decides what
to fix next, including whether the failure-grounding abstraction (the other
half of the current architecture initiative) is even worth its cost before
building it.

## How it works

1. `goals.py` - a small, hand-curated set of goals, each picked for a
   specific failure-category hypothesis (see each `Goal.hypothesis`) rather
   than being another Ignite/Qpid variant. Expected to grow after the first
   real batch shows which categories are under-represented.
2. `run_harness.py` - for each goal, sets up an isolated git-initialized
   workspace + `kriya.yaml`, shells out to the real `kriya generate <goal>
   -y` (fully unattended - matches `on_approval`/`on_skill_gap`/
   `on_web_lookup`'s auto-skip behavior under `-y` in `kriya/cli.py`), and
   points every goal's `paths.logs` at one shared directory so the whole
   batch lands in a single `traces.db`. Writes a `summary.txt` you can paste
   back without needing to dig through raw logs.
3. `report.py` - reads that batch's `traces.db` and prints pass rate plus a
   breakdown by `status`/`failure_category` (the column
   `kriya/core/trace.py` now persists - see the architecture-initiative
   Part 1 work this spike depends on) and a per-run detail table.

## Running it

Needs a live LLM/embedding endpoint reachable at the model(s) you pass (or
the `KRIYA_LIVE_LLM_MODEL`/`KRIYA_LIVE_EMBED_MODEL`/`KRIYA_LIVE_BASE_URL`
env vars, defaulting to a local Ollama instance) - **and, only for the
`django_healthcheck_gap` goal, real internet access**, since KnowledgeGuard
looks up real PyPI release dates.

```bash
.venv/bin/python spikes/eval_harness/run_harness.py \
    --model qwen3-coder:30b --embed-model embeddinggemma:latest

# Iterate on a single goal while developing:
.venv/bin/python spikes/eval_harness/run_harness.py --goal-id python_greeter

# After a batch finishes:
.venv/bin/python spikes/eval_harness/report.py --logs-dir spikes/eval_harness/runs/<batch>/logs
```

**Run this yourself, in your own terminal - don't ask an assistant session
to run or poll it.** A full batch (five goals, one of them the known-hard
multi-file Ignite+Qpid app) can run well past half an hour; watching that
turn by turn from inside a chat session burns usage quota for zero benefit
over just letting it finish and pasting back `summary.txt`/the `report.py`
output afterward.

`runs/` (this spike's own batch output - workspaces, per-goal logs,
`traces.db`, `summary.txt`) is scratch, regenerated on every invocation -
already covered by a `.gitignore` entry.

## Known, deliberate limitations (not bugs)

- **`human_rejected` can never appear in harness data.** `-y` auto-approves
  every human-approval gate by design (`kriya/cli.py`'s `on_approval`) - an
  unattended runner structurally cannot exercise a human declining. That
  category only ever shows up in real interactive usage.
- **The knowledge-gap goal produces two trace rows, not one.** Under the
  default `--knowledge-policy warn` plus `-y`, the CLI auto-confirms after
  the first (blocked) call and re-runs - so `traces.db` gets a
  `knowledge_gap` row from the gate, then a normal `success`/`failure` row
  from the confirmed retry. `report.py`'s "Total rows" note calls this out;
  it's real signal (the gate fired AND what happened after), not a logging
  bug.
- **Taxonomy is coarse on purpose.** Today's categories
  (`environment_failure`, `quality_gates_exhausted`, `knowledge_gap`,
  `human_rejected`) don't yet distinguish *which* quality gate failed
  (compile vs. test vs. runtime-verification). Decided explicitly: see the
  real distribution from a first batch before deciding whether finer
  splitting is worth the added plumbing.

## Findings

### Batch 20260804-115621 (qwen3-coder:30b / embeddinggemma:latest, 5 goals, 1200s/goal)

Pass rate 1/4 traced rows (25%) - 2 of 5 goals timed out before completing a
single trace row. Real, mixed signal on the first ever live run:

- **`python_greeter` passed clean, attempt 1.** Healthy baseline.
- **`ignite_qpid_person` confirmed the environment-failure circuit breaker
  working live**, not just in mocks: the model wrote
  `-Djava.security.manager=allow` into `pom.xml` (Java 17+ rejects that
  flag), Kriya correctly stopped after 1 attempt instead of burning the
  full retry budget re-generating code that could never fix an environment
  issue. Also hit `knowledge_gap` first (Ignite/Qpid goal text matches
  `COMMON_ALIASES`), auto-confirmed under `-y`/`warn` as expected.
- **Real Kriya bug found: Ruby tests can never pass.**
  `ruby_word_count` exhausted its full retry budget (6 attempts, ~17 min)
  on `bundler: command not found: rspec`. The final generated
  `lib/word_count.rb` was correct, clean Ruby - the blocker is
  `PolymorphicValidator.run_tests()`'s ruby branch
  (`kriya/tools/validate.py:371-383`), which runs `bundle exec rspec`
  directly and never runs `bundle install` first. A fresh sandbox never has
  gems installed, so any Ruby goal using RSpec (the idiomatic way to test
  Ruby) is structurally unable to pass regardless of what the model writes.
  Exactly the "zero coverage outside unit tests" gap `goals.py` predicted -
  a mocked test never exercises a real bundler environment. **Fixed same
  session**: `run_tests()`'s ruby branch now runs `bundle install` first
  whenever a real `Gemfile` is present (skipped when the project only has a
  bare `Rakefile`/`*.gemspec`, since `bundle install` has nothing to act on
  there), 3 new regression tests, confirmed failing pre-fix. Re-validated
  live in the next batch (20260804-144646 below) - it surfaced a second,
  different bug in the same symptom class, now also fixed.
- **Unresolved: `django_healthcheck_gap` produced Java/Spring code for a
  Python/Django goal.** The goal text never mentions Java/Spring/Maven, but
  the Developer wrote `DjangoHealthCheckView.java` using Spring's
  `MockMvc`/`ResponseEntity`. The `Spring Boot` skill did get *loaded* off
  disk for this run (expected - the global skill library always loads
  regardless of relevance), but not root-caused beyond that at the time.
  See the 20260804-151655 batch entry below for the correction: this is
  NOT skill-content bias - confirmed model-side.
- **Harness tuning: 1200s/goal was too short.** 2 of 5 goals
  (`python_task_tracker`, `django_healthcheck_gap`) hit the timeout
  mid-retry, not stuck - genuinely still working. `python_task_tracker`
  was mid-attempt-5 on a real `ModuleNotFoundError` caused by the model
  inventing a Maven-style `src/test/python/tests/...` layout for a goal
  that only ever asked for a flat `tasks/`/`tests/` layout. Next batch
  should raise `--timeout-per-goal` well above 1200s for multi-file goals.
- **Forensics gap noticed while investigating this batch**: failed
  quality-gate attempts leave no persisted record of the actual generated
  content anywhere (checkpoints only save on a `developer_success` stage
  gate, i.e. after compile+targeted-tests pass) - only the compiler's error
  text survives. Made root-causing the recurring Ruby "unexpected
  tXSTRING_BEG" syntax error (seen 3 times across attempts, always at line
  4) impossible after the fact from stdout alone. Worth keeping in mind for
  the failure-grounding abstraction (item 1): a `Failure` object that
  carries (or references) the actual content that failed, not just the
  tool's error text, would make exactly this kind of investigation
  possible without needing DEBUG logging turned on in advance.

### Batch 20260804-144646 (qwen3-coder:30b / embeddinggemma:latest, partial re-run: ruby_word_count + ignite_qpid_person only, post-refactor validation)

Live validation of both the `bundle install` fix and the unified
failure-grounding refactor (item 1). Both goals still failed, but for two
completely different (and individually informative) reasons - neither is a
regression from either change:

- **`ignite_qpid_person`: identical JVM Security Manager crash, same
  circuit-breaker behavior as the first batch.** The model wrote
  `-Djava.security.manager=allow` into `pom.xml` again (a different random
  attempt, same class of mistake), and Kriya stopped after exactly 1 attempt
  again. Confirms the failure-grounding refactor didn't regress the
  environment-failure fast-stop path - real live parity, not just passing
  mocks. (The recurring mistake itself is model-side; a
  `skills/ignite-java17/rules.txt` rule against that flag is a plausible
  follow-up, not yet done.)
- **`ruby_word_count`: the `bundle install` fix worked, but exposed a
  second, different bug in a different mechanism.** The targeted-test gate
  (`PolymorphicValidator.run_tests()`, the code path fixed in the previous
  batch) passed - `bundle install` ran, `bundle exec rspec` succeeded.
  Runtime Verification then independently inferred its own run command
  (`RunVerifierAgent.judge()`) and picked a bare `rspec spec/word_count_spec.rb`
  - never routed through Bundler at all, since that inference path doesn't
  go through `run_tests()`. Failed immediately: `Failed to execute: [Errno 2]
  No such file or directory: 'rspec'` (system-wide `rspec` was never
  installed - only available via `bundle exec` in this project's local
  bundle). `environment_failure`'s "missing executable" classifier correctly
  caught it and stopped after 1 attempt. **Fixed same session**:
  `_resolve_run_command()` (`kriya/workflow/workflow.py`) now prefixes
  `bundle exec` onto an inferred `rspec`/`rake` command whenever a real
  `Gemfile` exists - same deterministic-ground-truth pattern already used
  there for the Maven exec:java/exec:exec correction, just for a different
  toolchain. 3 new regression tests, confirmed failing pre-fix.
  **Re-validated live (batch 20260804-145911)**: `ruby_word_count` now
  passes clean on attempt 1 (34.9s) - both Ruby fixes confirmed working
  end-to-end, not just against mocks.
- **Lesson reinforced**: the exact same underlying gap (gems never
  installed) can resurface through more than one independent code path -
  `PolymorphicValidator.run_tests()`'s fixed invocation and
  `RunVerifierAgent.judge()`'s free-form command inference are structurally
  different mechanisms that both happened to need the same fix. Matches the
  durable lesson already on file: "not every 'the model got it wrong' case
  needs the same fix" - here it's the reverse shape, two different
  mechanisms needing the *same* fix, discoverable only by re-running live
  after the first fix, not by inspecting the first fix's own code in
  isolation.

### Batch 20260804-151655 (qwen3-coder:30b / embeddinggemma:latest, full 5-goal batch, 2400s/goal)

First full batch since both Ruby fixes landed. Pass rate 1/5 traced rows
(20%) - one clean pass, one expected model-side environment failure, one
new real Kriya bug found+fixed, one confirmed-recurring unresolved finding,
one goal still timing out even at double the previous limit.

- **`python_greeter` passed clean again.** Stable baseline across all 3
  batches so far.
- **`ignite_qpid_person`: identical JVM Security Manager crash, third batch
  in a row.** Same `-Djava.security.manager=allow` mistake, same
  circuit-breaker stopping it after 1 attempt. Purely model-side at this
  point (3/3 batches) - a `skills/ignite-java17/rules.txt` rule against
  that flag is a real, cheap candidate fix, not yet done.
- **Real Kriya bug found: `bundle install` itself can fail non-interactively
  on an unmodified macOS system Ruby.** `ruby_word_count` regressed from
  its previous clean pass (34.9s, attempt 1) to `quality_gates_exhausted`
  (7 attempts, ~22.7 min) - not a regression in the fix itself, a different
  failure mode the fix's own `bundle install` call newly exposed. This
  machine's default Ruby is macOS's bundled system Ruby (2.6.0, no
  rbenv/rvm), whose gem directory (`/Library/Ruby/Gems/2.6.0`) is
  permission-protected - `bundle install` needed `sudo` to write there and
  failed with `Bundler::SudoNotPermittedError` since there's no interactive
  terminal to prompt. The model correctly diagnosed nothing (there was
  nothing wrong with its own Gemfile/code) and burned its whole retry
  budget uselessly editing a Gemfile that could never fix a host permission
  issue. **Fixed same session**: `bundle install --path vendor/bundle`
  installs gems into a project-local, sandbox-writable directory instead of
  the host Ruby's own gem path - portable across Bundler 1.x/2.x, and the
  path choice persists via `.bundle/config` so the later `bundle exec`
  call needs no extra plumbing. Updated regression test, confirmed failing
  pre-fix. **Re-validated live**: `ruby_word_count` passes again -
  three real, independent Ruby-toolchain bugs found and fixed across three
  live batches (missing `bundle install`, an inferred command bypassing
  Bundler, and `bundle install` itself needing a non-system gem path), each
  invisible to the fully-mocked test suite.
- **Confirmed recurring (2/2 batches): `django_healthcheck_gap` still
  produces Java/Spring code for a Django/Python goal - and the skill-bias
  hypothesis is now DISPROVEN, not just unconfirmed.** Same pattern as the
  first batch - `HealthCheckController.java` using
  `@RestController`/`@GetMapping`, compile-failing on missing Spring
  dependencies never declared anywhere. Checked `traces.db`'s persisted
  `active_skills` column directly for this run (ground truth, not the
  "Loaded skill" log lines, which only reflect the always-on discovery
  phase - every skill in the global library gets read off disk regardless
  of relevance): **empty string.** Zero skills activated for this run - the
  `Spring Boot` skill's only tag (`spring-boot`) never appears in the goal
  text, and the fresh repo has no dependencies to fact-match against, so
  `kriya/workflow/workflow.py`'s `is_relevant` gate (goal-text/tag match or
  repo-fact match - `workflow.py:1785-1817`) correctly excluded it. No
  skill content of any kind reached this prompt. The Java/Spring drift is
  therefore a pure model-side pattern-completion bias, unrelated to Kriya's
  skill system - a different and more useful conclusion than "unresolved,"
  since it rules out a whole category of fix (skill tuning) and points at
  the Developer's own prompt instead (see the next Findings entry below).
- **`python_task_tracker` still timed out, even at 2400s (double the first
  batch's 1200s).** Same root cause as batch 1, now confirmed on a second
  independent run: the model invented a Maven-style
  `src/main/python/tasks/...` / `src/test/python/tests/...` layout for a
  goal that only ever asked for a flat `tasks/`/`tests/` structure -
  structurally unresolvable by `PolymorphicValidator.run_tests()`'s Python
  src-layout support (`workspace_path`/`workspace_path/src` only), so every
  retry fails identically regardless of code correctness, no amount of
  extra timeout would let it succeed. Deliberately **not fixing the path-
  resolution side**: unlike the Ruby fixes, there's no clean ground-truth
  signal here (a Gemfile definitively proves Bundler is needed; there's no
  equivalent "this nested layout is definitely what was intended" signal)
  - extending path resolution to cover an invented convention would be
  guessing at which wrong pattern to special-case. Given the Django
  finding above ruled out skill-content bias as the mechanism, this is the
  same underlying class of problem (the model substituting a different
  ecosystem's convention for the one actually requested) and the same
  fix category likely applies - see below.
- **Root-cause hypothesis for both findings above: no explicit instruction
  anywhere tells the Developer to stay in the goal's stated language/
  framework.** Matches an already-established, already-validated pattern
  from this project's own history: "passive reference material is never
  enough - the model needs explicit checklists/instructions" (confirmed
  repeatedly for dependency-preservation, wrong-import, and other classes
  of mistake).

### Ecosystem-preservation invariant (implemented, not yet live-validated)

User reviewed a written fix plan before any code was touched, then approved
implementation. Added `ECOSYSTEM_INVARIANT_HEADER` /
`_build_ecosystem_invariant_block()` (`kriya/workflow/workflow.py`) - a
standing checklist instruction (always present, not reactively triggered by
a failure), computed once per run from the repo analyzer's already-detected
`frameworks` (named explicitly when present, generic wording when the repo
is fresh) and threaded through all three prompt builders
(`_build_targeted_retry_prompt`, `_build_full_set_retry_prompt`,
`_build_missing_files_retry_prompt`). Covers attempt 1 too - it flows
through the same `_build_full_set_retry_prompt` attempt 1 already uses -
and reaches every real LLM call site (batch-JSON and per-file
`_fill_missing_content` alike), since they all embed the same
`task_description` string; no changes needed in `agent.py`.

Found and fixed a real pre-existing gap while implementing: the
dependency-preservation checklist this was modeled on
(`required_dependencies_prompt_block`) was only ever wired into the
full-set builder - `_build_targeted_retry_prompt` and
`_build_missing_files_retry_prompt` never carried it. The ecosystem
invariant deliberately covers all three, so a targeted or missing-file
retry mid-run gets the same protection as a fresh full-set attempt.

5 new regression tests (2 pure-function unit tests on
`_build_ecosystem_invariant_block`, 3 integration tests confirming the
invariant reaches attempt 1, a targeted retry, and a missing-files retry),
confirmed failing pre-fix (`ImportError`, since the function didn't exist
yet), full suite green (552 passed), ruff clean.

**Not yet live-validated.** A mocked test can only confirm the instruction
text is present in the prompt - never that the model actually obeys it.
The real test is the next full batch: does `django_healthcheck_gap`
produce real Django code, and does `python_task_tracker` stop inventing a
Maven-style layout? Run by the user in their own terminal, same as every
other batch in this file.
