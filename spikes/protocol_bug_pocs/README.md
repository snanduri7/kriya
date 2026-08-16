# Protocol bug POCs

Five POCs, all pulled out of the `ignite_qpid_protocol` eval-harness goal - not
the full 3-layer goal, not through Kriya's full Planner/Architect/Developer/
Reviewer pipeline, just direct `LLMClient.complete()` calls per trial. `01`-`04`
each isolate exactly ONE of the four recurring first-attempt bug categories
found across 11 live runs of that goal this session (see
`docs/kriya_backlog_and_lessons.md`'s 2026-08-14 entries); `05` recombines them
incrementally to test what isolation alone can't.

| Folder | What it tests | Seen live in |
|---|---|---|
| `01_wire_format_roundtrip/` | `Protocol`/`ProtocolParser` encode-decode mismatch, alone | `a-3`, `attribution-fix-validation-5`, `attribution-fix-validation-7` |
| `02_ignite_resource_lifecycle/` | Ignite node started but never `.close()`d, alone, A/B explicit-vs-implicit wording | `a-1`, `a-4`, `attribution-fix-validation-6` |
| `03_jms_bytes_message_api/` | Wrong `javax.jms.Session.createBytesMessage(...)` call shape, alone | `attribution-fix-validation-2` |
| `04_cache_generics_typing/` | `var`-inferred Ignite cache handle instead of explicit generics, alone, A/B explicit-vs-implicit wording | `attribution-fix-validation-3` |
| `05_incremental_composition/` | The SAME four requirements recombined one layer at a time (mirroring the real goal's own Layer 1/2/3 structure), to find where density starts to matter | (all of the above, combined) |

## Why isolate them like this

The full goal is dense (three layers, ~9 files, Ignite + Qpid + JMS + a hand-rolled
binary protocol all in one). When a bug shows up in a live run, it's hard to tell
whether the model is failing because the specific requirement got lost in all that
surrounding text, or because it's a genuinely hard/unknown sub-task regardless of
how it's asked. Stripping each one down to a single, standalone, clearly-worded
task and running it N times gives a clean signal: a low pass rate here means the
task is hard *in isolation*, not just noisy inside a big goal.

`02` and `04` go further and A/B test goal wording directly - each has an
"explicit" variant (matching the real goal's own strong, explicit wording) and
an "implicit" variant (the same task with that specific warning removed
entirely) - to directly answer "does making the goal more explicit actually
help, or is this an execution problem no amount of wording fixes."

`01` and `05` are the only ones that actually compile and run the generated
Java for the wire-format piece (objective grading - does the round trip
actually work - rather than a pattern match) since it's an arithmetic-
correctness bug, not an instruction-following one. `02`/`03`/`04` (and `05`'s
`ProtocolApp.java` checks) grade via a fast static check (reusing Kriya's own
`IgniteUnclosedResourceCheck` for the Ignite-close piece) since compiling
would need real Ignite/Qpid dependencies on the classpath and doesn't change
what's being measured (an instruction-following pattern, not arithmetic).

## Findings so far (2026-08-14 batches)

- **`01`**: 20/20 PASS across two batches (5 trials × 2 widths, twice) - the
  wire-format round trip is NOT a hard sub-task for this model in isolation,
  at either the real goal's 3-byte width or a changed 5-byte width. (Diversity
  note: at temperature 0.2 - the real deployed setting - the model converges
  on a small handful of distinct implementations rather than 5 fully
  independent draws; all distinct implementations seen so far passed.)
- **`02`**: `explicit` 8/8 PASS, `implicit` 0/8 PASS - a clean, decisive
  result. The explicit warning is the ENTIRE difference between never closing
  Ignite and always closing it, when isolated.
- **`03`**: 8/8 PASS - the JMS `BytesMessage` API is not a knowledge gap in
  isolation either, even with the real goal's own level of ambiguity (no
  API-shape hint given).
- **`04`**: `explicit` 8/8 PASS, `implicit` 8/8 PASS - unlike `02`, the
  explicit instruction is redundant here; the model already defaults to
  explicit generics on the cache handle whether told to avoid `var` or not.

**Reading across all four**: 5 of 6 isolated conditions tested are a clean
100%. The lone exception (`02`'s implicit variant) fails 100%, not partially -
a real "default behavior" gap, not noise. Since every condition that includes
the real goal's own explicit wording is 100% in isolation, yet the same
wording still fails 27-38% of the time per requirement in the full live goal,
isolation rules out "the wording is unclear" and "the sub-task is hard" as
explanations - which is exactly the gap `05` was built to probe directly, by
recombining the same validated wording incrementally instead of jumping
straight to the full goal's density.

## Running

Each folder is fully standalone - `cd` into it and run its `run_poc.py`
directly. All five default to `qwen3-coder:30b` @ `http://localhost:11434/v1`,
temperature 0.2 - the same primary model/settings the real eval harness
batches use (edit the `MODEL`/`BASE_URL`/`TEMPERATURE` constants at the top of
a script to point at a different model). No Kriya config file is needed - each
script calls `kriya.core.llm.LLMClient` directly, bypassing the full
generation pipeline entirely, same pattern as `spikes/tool_call_developer/`'s
existing POCs.

```bash
cd spikes/protocol_bug_pocs/01_wire_format_roundtrip && .venv/bin/python run_poc.py --trials 5
cd spikes/protocol_bug_pocs/02_ignite_resource_lifecycle && .venv/bin/python run_poc.py --trials 8
cd spikes/protocol_bug_pocs/03_jms_bytes_message_api && .venv/bin/python run_poc.py --trials 8
cd spikes/protocol_bug_pocs/04_cache_generics_typing && .venv/bin/python run_poc.py --trials 8
cd spikes/protocol_bug_pocs/05_incremental_composition && .venv/bin/python run_poc.py --trials 5
```

(Run from the repo root instead if you prefer - each script resolves its own
imports relative to its own file location, not the current working directory.)

Only `01` and `05` write generated code to disk (`runs/<timestamp>/...`,
gitignored) - they're the ones with real compile/execute grading, so the
generated `Protocol.java`/`ProtocolParser.java` (and `05`'s `ProtocolApp.java`)
plus the Maven project they were built in are worth being able to inspect
afterward. `05` additionally persists every trial's exact prompt, raw LLM
response, and a `verdict.json`, plus a plain-text `run.log` of everything
printed - see its own `README.md`. `02`/`03`/`04` only print a per-trial line
and a summary tally (no generated code to inspect - the grading regex runs on
the in-memory response and nothing about the code itself is otherwise
interesting to keep). Nothing here writes back into Kriya itself or the eval
harness - these are pure read-only investigations. See each subfolder's own
`README.md` for the exact goal text used and how grading works.
