# POC 3: JMS `BytesMessage` API usage

**What's being tested:** does the model call the real `javax.jms.Session`
API correctly when asked to send a `byte[]` as a JMS message, with no
protocol/wire-format/Ignite complexity around it at all.

**Goal given to the model** (see `USER_PROMPT` in `run_poc.py`): a single
method that sends `data` as a JMS `BytesMessage` via a given `Session` and
`MessageProducer`. Deliberately worded at the SAME level of ambiguity the
real goal actually used ("send the encoded bytes as a JMS BytesMessage to a
queue") - no API-shape hints, since over-specifying the correct API here would
test something easier than what actually happened live.

**The real bug** (`attribution-fix-validation-2`, found live once):
`session.createBytesMessage(data)` - passing the bytes as if `createBytesMessage`
took an argument. It doesn't; the real API is two calls:
`session.createBytesMessage()` (no arguments) then `message.writeBytes(data)`.

**Grading:** static regex check for the correct two-call shape - `PASS` only if
`createBytesMessage()` is called with zero arguments AND `.writeBytes(` appears
somewhere after it.

**Why no A/B variant here (unlike POCs 2/4):** this isn't an
instruction-following bug (the real goal never said anything specific about
this API's shape either way) - it's a candidate knowledge gap. The question
this POC answers is whether it's genuinely repeatable (worth adding as a
`skills/qpid` rule - the mechanism Kriya already has for known API gotchas,
same as the existing `defaultAlias` rule) or a one-off that isn't worth
building anything for.

**Run:**
```bash
.venv/bin/python run_poc.py --trials 8
```
