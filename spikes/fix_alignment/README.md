# Fix-alignment spike

Throwaway-feeling, not wired into `kriya/cli.py` - same posture as
`spikes/eval_harness/`.

## Question this answers

Twice this session, independently, on unrelated bugs, the retry loop showed
the same pattern: the model's own FIX ANALYSIS text correctly named the exact
root cause, but the SEARCH/REPLACE edit it produced right after didn't
implement it - either leaving the reported line unchanged (caught live by
Layer 1, `find_edits_ignoring_reported_line()`) or fixing something adjacent
instead. Both times the diagnosis was right and the execution wasn't.

This asks: **how often does that actually happen, is it consistent across
different bug shapes, and does a cheap, single-call prompt nudge measurably
reduce it?** Not a guess - real repeated LLM calls against real, byte-faithful
captured bugs, scored mechanically.

## The two fixtures (`fixtures.py`)

Both are reconstructed from real, live-captured Kriya runs, not invented:

- **`buffer_capacity`** - `ProtocolParser.java`'s `encode()`, pulled byte-faithful
  from `spikes/eval_harness/runs/20260808-001428/logs/ignite_qpid_protocol.stdout.log`.
  Writes `dataLength` via `putInt` (4 bytes) and `time` via `putLong` (8 bytes)
  when the wire format needs 3 and 4 bytes respectively - the real
  `java.nio.BufferOverflowException` this produced, and the real stack trace
  (`ProtocolParser.java:23`), are used verbatim as `error_context`.
- **`incompatible_types`** - `PersonApp.java`'s cache read, reconstructing the
  real pattern from `ignite_qpid_person` (run `20260807-193054`): `var cache =
  ignite.cache(CACHE_NAME)` (raw/erased generics) followed directly by
  `Person person = cache.get(1);` - the real `incompatible types:
  java.lang.Object cannot be converted to com.example.Person` error.

Both fixtures' line numbers are verified programmatically against their own
`error_context` locators before use (see the assertions each fixture's own
docstring references) - not eyeballed.

## What each call does (`run_alignment_test.py`)

For each fixture x condition x repetition, calls
`DeveloperAgent.run_generation()` for real (the actual production method, not
a reimplementation), with `known_target_files` set to trigger the same
targeted-retry code path a real Quality Gate failure would, `prior_error_context`
set to the fixture's real error text, and `error_source_context` built via a
**real call to `_build_error_source_context()`** against the fixture's content
on disk (a temp file) - byte-identical to what a real retry would show, no
manual line-window arithmetic.

Two conditions:
- **`baseline`** - today's exact production prompt, unmodified.
- **`nudge`** - the same prompt plus one added sentence (via the new,
  purely-additive `extra_fix_instruction` parameter on `run_generation()`/
  `_fill_missing_content()` - defaults to `""`, zero effect on every other
  caller): *"Before writing SEARCH/REPLACE, re-read your own FIX ANALYSIS
  above. Your REPLACE text MUST implement exactly what you just diagnosed..."*

For every response: parses `analysis`/`edits` via the real
`DeveloperAgent._split_fix_analysis_edit()`, applies the edit via the real
`apply_anchored_edits()` (an anchor-match failure is its own recorded outcome,
not a crash), then scores two independent things against the fixture's own
mechanical checks:

- **`diagnosis_correct`** - does the FIX ANALYSIS text name the actual root
  cause (checked via keyword matching specific to each fixture, not an LLM
  judge - kept deterministic and free).
- **`execution_correct`** - does the *resulting file*, after applying the
  edit, actually fix it (e.g. no more `putInt(...DataLength)`/`putLong(...Time)`
  for the buffer fixture; an explicit cast or generic declaration for the
  types fixture) - checked against the real post-edit content, not the diff.

The gap between these two is exactly the phenomenon under study: how often
`diagnosis_correct=True, execution_correct=False`.

## Running it

```bash
.venv/bin/python spikes/fix_alignment/run_alignment_test.py \
    --model qwen3-coder:30b --repetitions 10
```

40 real LLM calls total at the default (2 fixtures x 2 conditions x 10 reps).
Narrower runs while iterating:

```bash
.venv/bin/python spikes/fix_alignment/run_alignment_test.py \
    --fixtures buffer_capacity --conditions baseline --repetitions 3
```

Prints a live per-call tag (`DIAG-OK`/`diag-no` x `FIX-OK`/`fix-no`/`apply-fail`/`no-edit`)
and a summary table, then writes full results - including every raw model
response, for manual spot-checking - to a timestamped JSON file under `runs/`
(gitignored, matching `eval_harness/runs/`).

Deliberately meant to be launched in your own terminal, not run turn-by-turn
from inside a chat session - same reasoning as `spikes/eval_harness/`'s own
README: every in-session check-in on a long-running call costs quota even
when just watching it finish.

## Findings

(Not yet run. This section gets filled in after the first real batch.)
