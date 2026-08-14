# POC 5: incremental goal composition

**What's being tested:** the 4 isolated POCs (`01`-`04`) each found their
requirement passes at or near 100% when it's the ONLY thing asked for - yet
the same explicit wording, embedded in the real 3-layer `ignite_qpid_protocol`
goal, still fails 27-38% of the time per requirement across 11 live runs.
Isolation and the full goal are two extreme data points. This POC fills in
the middle: build the SAME goal back up one layer at a time, using the exact
same explicit wording already validated in `01`-`04` (nothing rephrased), and
measure at which point (if any) the pass rate starts dropping from the 100%
isolated baseline.

**The three steps, mirroring the real goal's own Layer 1/2/3 structure:**

- **Step 1** (`goal1`): `Protocol` + `ProtocolParser` only - identical task to `01_wire_format_roundtrip`.
- **Step 2** (`goal1` + `goal2`): step 1 + a `ProtocolApp` entry point that starts/closes Ignite AND stores the round-tripped `Protocol` in an explicitly-typed cache - both requirements folded into ONE paragraph, matching how the real goal states them together in its own Layer 2.
- **Step 3** (`goal1` + `goal2` + `goal3`): step 2 + routing the encoded bytes through a JMS `BytesMessage` send before caching, matching Layer 3.

**Grading, every sub-requirement checked independently at every applicable
step** (not just the newest one) - so a later step can reveal an EARLIER
requirement quietly degrading under the added density, not just whether the
newest requirement itself is hard:

- `roundtrip` - `Protocol.java`/`ProtocolParser.java` are extracted and actually compiled + run against the same fixed `Main.java` driver `01` uses. Real execution, not a pattern match.
- `ignite_closed` (step ≥ 2) - `ProtocolApp.java` checked with Kriya's own real `IgniteUnclosedResourceCheck`.
- `cache_typed` (step ≥ 2) - `var` vs. explicit `IgniteCache<...>` on the cache declaration.
- `jms_api` (step 3) - the `createBytesMessage()` + `writeBytes()` shape.

`ProtocolApp.java` itself is never compiled (would need a real `ignite-core`
dependency on the classpath for no grading benefit over the static checks
above) - only `Protocol.java`/`ProtocolParser.java`/`Main.java` are.

**Everything is written to disk, not just printed** - per trial: the exact
`prompt.txt` sent, the raw `response.txt`, every `extracted/` file (whatever
path the model actually used, package-qualified or not), the full
`maven_project/` that got compiled and run, and a `verdict.json`. Each step
also gets a `summary.json`, and the whole run gets a plain-text `run.log`
capturing everything that was printed - unlike `02`/`03`/`04`, nothing here
depends on the terminal still being open afterward.

**Run:**
```bash
.venv/bin/python run_poc.py --trials 5                  # all 3 steps, 5 trials each
.venv/bin/python run_poc.py --trials 5 --steps 2         # just step 2
```

**Reading the result:** compare each step's per-requirement pass rate against
its own isolated baseline in `01`-`04`. A drop appearing at step 2 or 3 (but
not step 1) would be direct, controlled evidence that goal DENSITY - not the
requirement's own wording or difficulty - is what degrades reliability, since
every word of every requirement is identical to what already scored 100% in
isolation. Look inside a specific `trial_N/` folder for a FAIL to see exactly
what the model wrote and compare it against the same requirement's passing
runs in `01`-`04`.
