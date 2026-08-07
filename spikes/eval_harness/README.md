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
4. `architect_eval.py` (2026-08-07) - a per-stage eval, not a full-pipeline
   one: runs only Planner -> Architect for each `goals.py` goal, in-process
   (no `kriya generate` subprocess, no worktree, no compile/test/run-
   verification), and checks whether `ArchitectAgent.run_with_file_list()`'s
   structured JSON file list (`kriya/agents/contracts.py`) validated, plus
   whether a goal establishing Maven/Gradle/Bundler actually got its build
   manifest listed. Seconds per goal instead of `run_harness.py`'s minutes,
   and isolates the Architect specifically instead of only ever getting
   indirect evidence about it through a full-pipeline pass/fail. Reuses
   `goals.py`'s shared fixture set - a regression here is directly
   comparable against a full-pipeline regression on the identical goal.
5. `developer_eval.py` (2026-08-07) - same shape, one stage further: runs
   Planner -> Architect -> `DeveloperAgent._resolve_step1_file_list()` (the
   same file-list contract generalized to Developer's own Step 1 - see
   `docs/design.md` sec 2.4) and reports which of its three paths resolved
   the list (`contract`/`fallback`/`none`), plus how much Developer's own
   list overlaps with Architect's. Stops before `_fill_missing_content()`
   (per-file content generation) and Quality Gates - measuring the
   file-list mechanism specifically, not overall Developer output quality.
   The template for doing the same to Reviewer once it gets a contract.

## Running it

Needs a live LLM/embedding endpoint reachable at the model(s) you pass (or
the `KRIYA_LIVE_LLM_MODEL`/`KRIYA_LIVE_EMBED_MODEL`/`KRIYA_LIVE_BASE_URL`/
`KRIYA_LIVE_FALLBACK_MODEL` env vars, defaulting to a local Ollama instance)
- **and, only for the `django_healthcheck_gap` goal, real internet access**,
since KnowledgeGuard looks up real PyPI release dates.

Each goal's generated `kriya.yaml` sets an explicit, fast, non-reasoning
`llm_chain` fallback (`--fallback-model`, default `deepseek-coder-v2:16b`) -
deliberately NOT left to inherit Kriya's own packaged `default_config.yaml`
chain (`deepseek-r1:32b`, a reasoning model). Confirmed live, 2026-08-06/07:
escalating to that reasoning model can single-handedly burn the whole
`--timeout-per-goal` budget on its own (individual completions took 2-5+
minutes each, on `python_task_tracker` and `ignite_qpid_person`), consistent
with the separately-cited finding that a reasoning model gave zero
correctness benefit for a fact-recall-class retry while being 13x slower.

```bash
.venv/bin/python spikes/eval_harness/run_harness.py \
    --model qwen3-coder:30b --embed-model embeddinggemma:latest

# Iterate on a single goal while developing:
.venv/bin/python spikes/eval_harness/run_harness.py --goal-id python_greeter

# After a batch finishes:
.venv/bin/python spikes/eval_harness/report.py --logs-dir spikes/eval_harness/runs/<batch>/logs

# Architect-only, no compile/test/run-verification - seconds per goal:
.venv/bin/python spikes/eval_harness/architect_eval.py --model qwen3-coder:30b

# Developer's Step 1 file-list query specifically, one stage further:
.venv/bin/python spikes/eval_harness/developer_eval.py --model qwen3-coder:30b
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

## A real bug this file's own runs used to trigger (fixed)

`runs/` is gitignored but NOT excluded from a bare `pytest`'s filesystem
walk - every batch leaves real generated code (`test_store.py`, etc.) sitting
under `spikes/eval_harness/runs/<batch>/workspaces/<goal>/...`, and pytest's
default `test_*.py` discovery doesn't care that a file lives outside `tests/`.
Confirmed live, 2026-08-07: running the suite after a `python_task_tracker`
batch failed collection entirely (`ModuleNotFoundError`, since the landed
file imports relative to ITS OWN project layout) before a single real Kriya
test ran. Fixed at the root in `pyproject.toml` (`testpaths = ["tests"]`)
rather than adding an ignore rule here that the next spike/harness output
directory would just need again.

A second one, found the same day: `_write_config()` used to unconditionally
`mkdir` an empty `skills/` directory in every goal's workspace before
generation ever started - dead scaffolding nothing in Kriya's own pipeline
actually needs (`SkillEngine.discover_and_load()` gracefully skips a missing
`paths.skills` directory, and every write path under it creates it on demand
via `os.makedirs(..., exist_ok=True)`). That empty directory was directly
responsible for a real hallucination: `RepositoryAnalyzer` reported it as a
`top_level_folder` purely by existing, and `django_healthcheck_gap`'s
Architect concluded a Django app (and a `manage.py`) already existed there
when neither did (see `docs/design.md` sec 2.4 for the fuller root-cause
writeup and the corresponding `RepositoryAnalyzer` fix). Removed the
unnecessary pre-creation here too - a genuinely fresh `kriya generate` run
outside this harness would never have hit this specific case in the first
place, since the directory wouldn't have existed at all.

A third, found 2026-08-07: `_write_config()` set `autonomy.web_lookup_enabled`
and `web_lookup_auto_approve` in every generated `kriya.yaml` from the very
first version of this file, but never `search.base_url` -
`kriya/config/config.py::SearchConfig` deliberately makes the two separate
switches (the intent, per that class's own docstring, is that flipping only
one "does nothing" - so a copy-paste can't silently enable outbound search),
so the retry-loop's error-triggered live-lookup gate
(`self.kernel.config.search.base_url`, `kriya/workflow/workflow.py`) was
false on every single harness run to date - `web_lookup_enabled: true` LOOKED
on but never actually fired. Fixed: a new `--search-base-url` flag
(`KRIYA_LIVE_SEARCH_BASE_URL` env override), defaulting to
`http://localhost:8080` - the same self-hosted SearXNG convention
`docs/user_guide.md` Section 4.6 already documents and was live-tested
against. Pass `--search-base-url ""` to go back to the old (inert)
configured-but-off behavior if you don't have a SearXNG instance running -
`search_web()` degrades safely either way (a no-op on an empty base_url, a
logged WARNING + empty results on a connection failure), so this default
never breaks a run without SearXNG running, it just makes the absence
visible in the log instead of silent.

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

### Batch 20260804-195517 (qwen3-coder:30b / embeddinggemma:latest, full 5-goal batch, 2400-3600s/goal) - live validation of the ecosystem invariant

Pass rate 3/5 traced rows (60%) - the best batch yet. The invariant is a
confirmed, real, partial win - honest result, not a full fix for the
whole class it was aimed at:

- **`django_healthcheck_gap`: fixed.** Went from Java/Spring Boot code (2
  consecutive prior batches) to a clean pass, attempt 1, 38.6s. Verified
  the actual generated content, not just the pass/fail signal: real,
  idiomatic Django - `from django.http import JsonResponse`,
  `from django.urls import path`, a correct `urlpatterns` list. The
  language/framework-substitution failure mode is gone for this goal.
  One cosmetic leftover: the files landed at
  `src/main/java/com/example/urls.py` (Java-Maven-style directory
  nesting on top of correctly-Python file content and extension) -
  harmless here since Python stack detection only needs a `.py` file to
  exist anywhere, not any particular path.
- **`python_task_tracker`: NOT fixed - same root cause persists despite
  the invariant explicitly naming it.** Still timed out (this run used
  3600s, triple the original limit) still writing
  `src/main/python/tasks/store.py` / `src/test/python/tests/test_store.py`
  - the exact Maven-style layout the invariant's own text explicitly
  calls out ("do not invent a Maven-style src/main/src/test directory
  layout for a goal that only ever asked for a flat layout"). Confirmed
  via the regression tests added with the fix that this instruction text
  really is present in this goal's prompt - this isn't a wiring bug, the
  model saw the instruction and didn't follow it for this specific
  pattern, on a multi-file goal, even though the same underlying model
  did follow the (arguably harder) instruction to write real Django
  instead of Spring on a simpler, single-file goal in the same batch.
- **Reframe, not a failure of the fix**: "ecosystem substitution" turned
  out to be two distinguishable sub-problems, not one - *which*
  language/framework to write (the invariant fixed this, confirmed) and
  *which directory layout convention* to use within the correct language
  (the invariant did not reliably fix this, at least not yet, at least
  not for a 4-file goal). Matches the durable lesson already on file:
  "not every 'the model got it wrong' case needs the same fix." Possible
  next angles, not yet decided: the layout habit may be a stronger prior
  needing more than one checklist line to override (matches cited
  research already on file about models struggling to override strong
  priors via in-context instructions alone), or the instruction may be
  landing less saliently in a longer, multi-attempt, multi-file prompt
  than in the Django goal's much shorter one - genuinely unclear which
  from one data point, would need more batches or a targeted comparison
  to know.
- **`ruby_word_count` and `ignite_qpid_person` behaved exactly as the
  last batch** - Ruby passed again (2nd consecutive pass, all three Ruby
  fixes holding), Ignite/Qpid hit the identical JVM Security Manager
  mistake a 4th batch running, circuit breaker correctly stopping it
  every time. Both confirm no regression from this session's changes.

### `python_task_tracker`, `--goal-id`-only re-runs (2026-08-07) - the layout-invention thread finally closes

Two single-goal re-runs, not full batches, tracking one goal through two
fixes in sequence.

**Run 1 (`20260807-141317`)**: first re-run since the `--fallback-model`
fix (README's own eval-harness-config section) - this goal had never
once completed across 5 prior batches, always timing out (1200-3600s).
This run finished in 166.7s, `quality_gates_exhausted`, 7 attempts. The
timeout is gone - confirmed, not inferred: `model_hops` shows a clean
escalation to `deepseek-coder-v2:16b` partway through with no
multi-minute stalls. But it still failed, and for the exact reason
flagged as unresolved in the 2026-08-04 batch above: all 7 attempts,
across BOTH models, wrote to `src/main/python/tasks/...` and hit
`ModuleNotFoundError: No module named 'tasks'` on every one, every
fix-analysis misdiagnosing it as a sys.path problem rather than a layout
problem. Conclusively answers the open question from the earlier entry:
this is a genuine prompting-ceiling case (`ECOSYSTEM_INVARIANT_HEADER`
already names this exact anti-pattern verbatim and still lost 7/7), not
a saliency/prompt-length issue. Fixed deterministically instead of via
more prompting: `PolymorphicValidator.run_tests()`'s Python sys.path
fallback now also covers `src/main/python`/`src/main`/`src/test/python`/
`src/test` when they exist on disk (`kriya/tools/validate.py`).

**Run 2 (`20260807-142704`)**: re-ran immediately after that fix. Compile
and targeted tests now pass cleanly on **all 7 attempts** (`collected 7
items ... 7 passed`, every time) - the `ModuleNotFoundError` is
completely gone, first time this goal has ever gotten past test
collection. But `run_verification` then failed on every attempt with a
genuine state-persistence problem:
```
add "Task 1"  -> Added task 1: Task 1
add "Task 2"  -> Added task 1: Task 2   (also id 1 - fresh process)
done 1        -> Task 1 not found
list          -> No pending tasks
```
Root cause: each `python cli.py <cmd>` in the inferred run-verification
sequence is a **separate process**. The original goal text asked for an
"in-memory" `TaskStore` with argparse CLI commands - self-contradictory
once exercised as separate shell invocations, since a genuinely
in-memory-only store can never see state an earlier process added,
regardless of how correct the generated code is. Not a Kriya bug and not
a model bug - the test suite passed precisely because it correctly
exercises `TaskStore` within one process, which is all "in-memory" can
mean. **Fixed by rewording the goal itself** (`goals.py`), not the
pipeline: now explicit that `TaskStore` persists to a JSON file between
CLI invocations (matching how a real CLI tool would need to work) while
staying a plain in-memory structure within any single process/test.

Net effect of this whole thread: two real, evidenced Kriya-pipeline fixes
(fallback-model timeout, layout-invention sys.path robustness) plus one
goal-wording fix, landing three fixes deep on a single goal that had
never once completed before this session.

**Run 3 (`20260807-143555`), re-run with the corrected wording**: clean
pass, `run_id 97786725`, first attempt, no fallback escalation, 70s total.
Compile, targeted test, run_verification, and regression test all passed.
`run_verification`'s own grader reasoning: "The CLI successfully added
tasks, listed them, marked one as done, and listed them again showing the
updated status. The task state persisted between invocations as
demonstrated by the load/save functionality." First clean pass this goal
has ever had - thread closed.

### `ruby_word_count` regression (2026-08-07) - same goal-wording bug class

Had passed reliably every batch since 2026-08-04 (three straight Ruby-
toolchain fixes holding). Failed for the first time this session
(`20260807-183715`, `quality_gates_exhausted`, 6 attempts) - confirmed
unrelated to anything shipped that day. Root cause, read directly from the
model's own first attempt: the goal asked for "a Gemfile (no external gems
needed)" while ALSO requiring RSpec tests - self-contradictory, since rspec
is itself an external gem. The model took the instruction literally
(`# Empty file - no external gems needed`), then flailed through several
increasingly confused fix attempts across 6 tries - including pinning
`gem 'bundler', '~> 2.0'` in its own Gemfile at one point, which then
collided with this machine's older system bundler (`1.17.2`) - without ever
reaching the actual fix. All of it downstream of the same original
contradiction, the same shape as `python_task_tracker`'s "in-memory" vs.
CLI-persistence bug above. Fixed by rewording the goal, not the pipeline:
"no external gems" now explicitly scopes to the library implementation
only, and the Gemfile is explicitly told to declare `rspec` for the test
suite. Not yet re-validated live.
