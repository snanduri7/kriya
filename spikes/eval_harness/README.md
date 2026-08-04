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
  there), 3 new regression tests, confirmed failing pre-fix. Not yet
  re-validated live - the next batch run should confirm `ruby_word_count`
  actually passes now, not just that the unit tests pass.
- **Unresolved: `django_healthcheck_gap` produced Java/Spring code for a
  Python/Django goal.** The goal text never mentions Java/Spring/Maven, but
  the Developer wrote `DjangoHealthCheckView.java` using Spring's
  `MockMvc`/`ResponseEntity`. The always-loaded global skill library did
  include a `Spring Boot` skill for this run (expected/documented
  behavior, not itself a bug), but whether that irrelevant content in the
  prompt actually caused the language mix-up, versus the model
  independently defaulting to a familiar Spring pattern for "a view that
  returns JSON," is not established from one data point - would need a
  controlled comparison (same goal, global skills stripped) to know which.
  Flagged, not root-caused.
- **Harness tuning: 1200s/goal was too short.** 2 of 5 goals
  (`python_task_tracker`, `django_healthcheck_gap`) hit the timeout
  mid-retry, not stuck - genuinely still working. `python_task_tracker`
  was mid-attempt-5 on a real `ModuleNotFoundError` caused by the model
  inventing a Maven-style `src/test/python/tests/...` layout for a goal
  that only ever asked for a flat `tasks/`/`tests/` layout - plausibly (not
  confirmed) the same kind of cross-goal bias as the Spring/Django finding
  above, given how Java/Maven-heavy the always-loaded global skill library
  currently is. Next batch should raise `--timeout-per-goal` well above
  1200s for multi-file goals.
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
