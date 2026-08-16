# POC 2: Ignite resource lifecycle (`.close()`)

**What's being tested:** does the model close an embedded Ignite node it
started, with no protocol/wire-format/Qpid complexity around it at all.

**Goal given to the model** (see `_BASE_TASK` in `run_poc.py`): a single class
that starts Ignite via `Ignition.start("ignite-config.xml")`, puts one entry in
a cache, prints "Done". Two variants layered on top of that same base task:

- **`explicit`** - adds the real goal's own warning almost verbatim: closing
  is mandatory, an unclosed node keeps background threads alive indefinitely,
  "this is a real defect, not a false alarm."
- **`implicit`** - the identical base task with that warning removed entirely.
  No mention of closing, background threads, or resource lifecycle at all.

**Why this A/B split matters:** the real goal already uses the `explicit`
wording, and the model still forgets to close Ignite on roughly 3 out of 11
live runs of the full goal this session. If `explicit` and `implicit` pass at
a similar rate here too, that confirms the goal wording isn't the lever - this
is a model execution/attention habit that persists even when the instruction
couldn't be clearer, which is exactly why Kriya's `IgniteUnclosedResourceCheck`
(a deterministic pre-flight check, not a prompt tweak) is the right fix
already shipped for it. If `explicit` clearly outperforms `implicit`, that's
new evidence worth reconsidering.

**Grading:** reuses Kriya's own real `IgniteUnclosedResourceCheck`
(`kriya/workflow/static_checks.py`) directly - not an approximation of it, the
literal same check the real retry loop runs, so a `PASS` here is provably
identical to what would NOT trigger a static-check retry live.

**Run:**
```bash
.venv/bin/python run_poc.py --trials 8                        # both variants, 8 trials each
.venv/bin/python run_poc.py --trials 8 --variants implicit    # just the implicit variant
```
