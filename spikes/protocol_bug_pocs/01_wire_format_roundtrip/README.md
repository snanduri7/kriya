# POC 1: wire-format encode/decode round trip

**What's being tested:** can the model write a `Protocol` + `ProtocolParser` pair
that correctly round-trips a binary header through a non-standard-width field
(`dataLength`), with no Ignite/Qpid/Maven complexity around it at all.

**Goal given to the model** (see `build_prompt()` in `run_poc.py` for the exact
text): write `Protocol` (fields + getters + content-based `equals`/`hashCode`)
and `ProtocolParser` (`encode`/`decode` implementing an exact byte layout:
1-byte protocolVersion + 1-byte softwareVersion + N-byte big-endian dataLength
+ 4-byte big-endian time + raw body bytes).

**Why this one is hard:** `dataLength` is 3 bytes wide (matching the real goal)
or 5 bytes wide (the generalization test). Neither width has a matching
`ByteBuffer` primitive (`put`/`putShort`/`putInt`/`putLong` are 1/2/4/8 bytes),
so the model has to hand-roll byte-shifting arithmetic for that one field, and
keep encode()'s shifts and decode()'s shifts exactly symmetric.

**Grading:** objective, not self-reported. Each trial's generated
`Protocol.java`/`ProtocolParser.java` gets dropped into a real, minimal Maven
project alongside a fixed test driver (`Main.java`, written here, not
generated) that constructs a `Protocol`, encodes it, decodes it, and checks
every field against the original by direct getter comparison (not the model's
own `equals()`, which is a separate, related-but-different correctness
question). `mvn compile exec:java` actually runs it. Outcomes:

- `PASS` - round trip byte-correct.
- `ROUNDTRIP_FAIL` - compiled and ran, but a field didn't survive the round trip (the real bug shape).
- `COMPILE_OR_RUNTIME_ERROR` - didn't even compile/run.
- `MISSING_FILES` - the response didn't produce both files in the expected format.

**Run:**
```bash
.venv/bin/python run_poc.py --trials 5              # both widths (3, 5), 5 trials each
.venv/bin/python run_poc.py --trials 5 --widths 3    # only the real goal's width
```

**Reading the result:** if the pass rate is similarly low at both widths, the
bug is a general property of "hand-rolled non-standard-width binary fields",
not something specific to 3 bytes - meaning nothing about Kriya's retry-loop
fixes needs to change if the real protocol spec changes field widths (they're
generic recovery mechanisms, not shape-specific ones - see
`docs/kriya_backlog_and_lessons.md`'s 2026-08-14 "targeted-budget exhaustion"
entry). If 5-byte is meaningfully easier or harder, that's a genuinely new
finding worth digging into further.
