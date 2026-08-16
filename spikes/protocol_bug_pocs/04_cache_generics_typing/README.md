# POC 4: Ignite cache generics typing (`var` vs. explicit type)

**What's being tested:** does the model declare an Ignite cache handle with
explicit generics when asked to, with no protocol/wire-format/Qpid complexity
around it at all.

**Goal given to the model** (see `_BASE_TASK` in `run_poc.py`): a single method
that gets/creates a cache, stores one `Integer -> String` entry, reads it back.
Two variants layered on top of that same base task:

- **`explicit`** - adds the real goal's own requirement almost verbatim: the
  cache reference MUST use an explicit `IgniteCache<Integer, String>` type,
  never `var`.
- **`implicit`** - the identical base task with that requirement removed
  entirely. No mention of typing, `var`, or generics at all.

**Why this A/B split matters:** same reasoning as POC 2 - the real goal
already forbids `var` explicitly, and it still showed up once
(`attribution-fix-validation-3`, `var cache = ignite.getOrCreateCache(...)`
inferring the raw `IgniteCache<Object, Object>` type, breaking every later
typed use site). If `explicit` and `implicit` pass at a similar rate here,
that's more evidence this class of bug is about model execution under a long,
dense goal, not about the specific wording used.

**Grading:** static regex check - `FAIL` if the cache variable is declared with
`var`, `PASS` if declared with an explicit `IgniteCache<...>` type.

**Run:**
```bash
.venv/bin/python run_poc.py --trials 8                        # both variants, 8 trials each
.venv/bin/python run_poc.py --trials 8 --variants implicit    # just the implicit variant
```
