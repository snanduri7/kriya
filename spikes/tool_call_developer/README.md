# Spike: tool-call-per-file loop for the Developer stage

## Why

After the qwen3.6 Qpid+Ignite validation surfaced three retry-loop bugs (all
variations of "the model's batch JSON file-list response silently dropped
files"), the natural architectural question was: would a tool-call-per-file
loop (write_file/read_file tools, agent decides what to call and when) avoid
this bug class structurally, the way Claude Code / Cursor / similar agentic
tools do?

The single biggest flagged risk before writing any code: Kriya targets local
Ollama/OpenAI-compatible endpoints, not a hosted frontier API, and local
models' native tool/function-calling support is known to be inconsistent.
This spike tests that risk directly, on Kriya's own actual models, before
considering any real implementation - narrow scope per plan: one file, one
tool call, verify the mechanism works at all first.

## What this tests

For a single one-file coding task (a `Stack` class with push/pop/peek/
is_empty), against each of Kriya's two most relevant local models
(`qwen3-coder:30b` - Kriya's actual current Developer model - and
`qwen3.6:35b`), three approaches:

1. **tool_call**: expose a `write_file(filepath, content)` tool, let the model
   call it, read the result from `message.tool_calls`.
2. **tool_call w/ fallback**: same request, but if `tool_calls` comes back
   empty, defensively regex-parse `message.content` for a malformed
   write_file-shaped attempt - mirrors the same defensive-parsing philosophy
   `DeveloperAgent._extract_json_value` already uses for JSON (markdown-fence
   stripping, bracket-scanning) because local models are known to be
   inconsistent about strict output formats.
3. **json_mode**: Kriya's actual current mechanism - one completion,
   `response_format: json_object`, asking for `{"filepath": ..., "content": ...}`
   in prose.

Run: `.venv/bin/python spikes/tool_call_developer/run_spike.py`

## Findings

**Tool-calling reliability is NOT uniform across Kriya's own models - and is
worst for the model Kriya actually uses for Developer generation.**
`qwen3.6:35b` emitted a proper, correctly-structured `tool_calls` response
every time (3/3 runs). `qwen3-coder:30b` was inconsistent: sometimes proper
`tool_calls`, sometimes a malformed pseudo-XML dump
(`<function=write_file>\n<parameter=filepath>...`) shoved into `content` with
`tool_calls` left empty - confirmed live via both the OpenAI-compat
`/v1/chat/completions` endpoint AND Ollama's native `/api/chat` endpoint, so
this isn't an API-shim quirk, it's the model/template combination itself. A
defensive fallback regex parse CAN rescue the malformed case (content was
valid and correct every time it fired) - but that means a real implementation
would need the same kind of belt-and-suspenders parsing tool-calls already
needed for JSON, not a clean structural win.

**Speed: a real, repeatable ~2x win - but only for the model Kriya isn't
currently using for Developer.** Across 3 runs on `qwen3.6:35b`, tool_call
averaged ~16s vs. json_mode's ~32s for the identical task - consistently
faster, not noise. On `qwen3-coder:30b`, no meaningful difference either way
(~2.1-2.3s for both approaches).

**Correctness: no difference observed.** Both approaches produced valid,
syntactically-correct, requirement-meeting code in every run, for both
models. This spike doesn't yet test the actual multi-file list-completeness
scenario that motivated the investigation (a single file has nothing to
"drop") - that would need a real multi-file tool-call loop as a follow-on, not
just this mechanism check.

## Follow-up: is this a fixable Ollama config problem?

Checked directly (`ollama show qwen3-coder:30b --modelfile`) rather than
trusting a plausible-looking GitHub issue found by search: `qwen3-coder:30b`
already has its own dedicated `RENDERER qwen3-coder` / `PARSER qwen3-coder`
correctly selected - it is NOT misrouted to a generic/wrong parser. The
inconsistency is a bug in that specific, correctly-selected parser
implementation, not a routing/config gap - confirmed via further-targeted
search: ollama/ollama#11232 (parser mis-extracts even against correct model
output), **#14570 (the parser returns HTTP 500 outright when a tool call's
arguments get truncated - directly relevant here, since `write_file`'s
argument is an entire file's content, exactly the "large arguments" failure
mode)**, and a goose-project report (#6883) that `qwen3-coder` degrades to
XML-in-text once tool count exceeds ~5 (untested here - this spike only ever
exposed one tool). Also found ollama/ollama#16383: `qwen3.6`/`qwen3.5`'s
parser has its own known intermittent failures too, described as
"cumulatively painful for any agent issuing many tool-using calls" - so this
spike's clean 3/3 result for `qwen3.6` was very likely small-sample luck, not
proof of real reliability at scale.

**Tried the obvious workaround anyway: a custom `TEMPLATE`-only Modelfile for
`qwen3-coder`, bypassing the built-in `RENDERER`/`PARSER` entirely** (built
`FROM` the raw weights blob directly, not the `qwen3-coder:30b` tag, to
sidestep a separate known Ollama bug - #14560 - where a derived model
otherwise silently inherits its parent's `RENDERER`/`PARSER` regardless of
what `TEMPLATE` you specify). Result: **0/5 proper `tool_calls`** - strictly
worse than the built-in parser's ~50%, and the model's raw output itself
became *less* internally consistent (drifted between `<parameter=content>`
and bare `<content>` mid-response). Root cause: Ollama's template-only path
only auto-recognizes a small, fixed set of known legacy bracket conventions
(confirmed `devstral-small-2:24b`, also `TEMPLATE`-only with zero
`RENDERER`/`PARSER`, gets proper `tool_calls` via Mistral's `[TOOL_CALLS]`/
`[ARGS]` bracket syntax) - `qwen3-coder`'s real, correct native format
(`<tool_call><function=name><parameter=k>v</parameter></function></tool_call>`)
isn't one of those recognized conventions, so nothing auto-parses it without
dedicated parser code, which is exactly what the (buggy) built-in
`qwen3-coder` parser already is. No config-only escape hatch exists here.

## Bottom line

This is not a clean "yes, rebuild the Developer stage around tool calls," and
it's not a fixable configuration problem either. Tool-calling reliability
differs by model in a way that happens to be worst for Kriya's actual primary
Developer model, the gap is a genuine open upstream Ollama bug (not a routing
mistake we can correct), and a hand-rolled template bypass makes it worse, not
better. Adopting this architecture would need the same defensive-fallback-
parsing burden Kriya already carries for JSON, just applied to a second output
shape too, and the biggest measured efficiency win shows up specifically for
the slow reasoning model, not the fast code model Kriya defaults to. The
structural "can't silently drop files from a list" benefit from the original
proposal still holds in principle (this spike didn't test or refute it - it
only tested whether the underlying mechanism is trustworthy enough to build
on), but it would need to be built on top of a defensive dual-path parser from
day one, not assumed to be free - and that parser needs to keep working even
as the model's own raw-output format drifts, which this session's testing
shows it genuinely does.

## Re-test (2026-08-03): large tool-call arguments specifically

Motivated by a real retry-loop bug found live (see `docs/design.md` §2.3.4a's
"dilution bug" and follow-on entries) that raised the bigger question again:
is a tool-using agent loop the actual complete fix for this whole class of
retry-loop problem, worth real investment instead of more point-fixes? The
one gap this original spike never tested: the SPECIFIC failure mode
`ollama/ollama#14570` flagged as most relevant (a tool call's arguments
getting large) - the original spike's task was a tiny Stack class, nothing
like Kriya's real ~5-8KB Java file outputs.

`run_spike_large_args.py` re-runs the original small-task comparison
(like-for-like check against Ollama version drift - 0.32.5 as of this run)
AND a new large task (a multi-hundred-line Java file matching Kriya's real
Developer output shape), against three models: `qwen3-coder:30b`,
`qwen3.6:35b`, `devstral-small-2:24b`.

**Result: every model that was reliable on the small tool-call argument
failed completely on the large one.**

| Model | Small task, tool_call | Large task, tool_call |
|---|---|---|
| `qwen3-coder:30b` | fell back to content-parse (not proper `tool_calls`) | proper `tool_calls`, worked (1 sample - see caveat below) |
| `qwen3.6:35b` | proper `tool_calls` (matches original 3/3) | **FAILED** - no tool call, no fallback match, plain prose+markdown after 164s |
| `devstral-small-2:24b` | proper `tool_calls` (matches original 3/3) | **FAILED** - returned *nothing at all* (0 chars) after 94s |

`json_mode` (Kriya's actual current mechanism) succeeded in all 6 runs,
every model, every task size.

Only `qwen3-coder` succeeded on the large task, and this is the model with
the *worst* overall tool-calling reliability record from the original spike
- one sample, could easily be luck rather than a trend (same caveat the
original spike raised about `qwen3.6`'s clean 3/3 small-task result). Not
re-run multiple times to get a real rate - the qualitative result (large
arguments break tool-calling for the models that were otherwise reliable)
was decisive enough on its own to not need more samples for this decision.

**External corroboration, same day**: Qwen's own officially-recommended
serving stack (vLLM with `--tool-call-parser qwen3_coder`, not Ollama) has
open GitHub issues in the same class - tool calls "not correctly parsed,
remain in plain content" (vllm-project/vllm#22975), "large number of
errors" on recent releases (#26561) - so this is not something Kriya could
route around by switching inference backends. Aider (a real, shipping
agentic coding tool) independently arrived at the same conclusion for the
same underlying reason: it deliberately uses a text-based SEARCH/REPLACE
diff format instead of native tool-calling for edits - functionally the
same idea as Kriya's own pre-existing `apply_anchored_edits()`.

**Updated bottom line**: the tool-use agent loop is not viable today with
local models, for Kriya's actual workload shape (writing/editing real-sized
files) - confirmed fresh, not just inherited from the original spike, and
not fixable by picking a different model or a different inference backend.
This is a genuine, current, externally-corroborated infrastructure
constraint. Closing structural gaps in the existing json_mode-based
architecture (see `docs/design.md` §2.3.4a) is the actual highest-leverage
work available given that constraint, not a consolation prize for avoiding
the harder problem.

## Re-test (2026-08-13): small-argument tool-calling as a json_mode replacement

Motivated by a live bug this session: `kriya/workflow/attribution.py`'s `triage`
tier (a `json_mode` call asking the model to pick which candidate file caused a
compile failure) got back **empty** `content` from `gpt-oss:20b` -
`json.loads()` threw `Expecting value: line 1 column 1 (char 0)` - because the
model's real output went into Ollama's separate `reasoning` field, which
`LLMClient.complete()` never reads. Small-argument tool-calling (this file's
own `write_file(filepath, content)` boundary from the original spike, above)
looked like the structural fix, since a `tool_calls` block is a distinct field
from any reasoning channel by construction. `run_spike_small_arg_triage.py`
tests that directly: a realistic triage task (same "compiler's line isn't the
fix site" shape as the real `ignite_qpid_protocol` diagnosis-vs-diff bug this
session's `find_edits_ignoring_own_diagnosis()` was built for), 5 trials per
mechanism per model, against the three models actually in play today -
`qwen3-coder:30b` (positive control - already the target of the shipped,
opt-in `complete_with_tools()`/`self_correction.py` mechanism), `gpt-oss:20b`
(the model whose `json_mode` broke live), and `glm-4.7-flash` (untested
fallback-model candidate).

**Result table** (N=5 each):

| Model | tool_call | json_mode |
|---|---|---|
| `qwen3-coder:30b` | 5/5 correct, avg 33.6s | 5/5 correct, avg 38.7s |
| `gpt-oss:20b` | **0/5** proper `tool_calls` (2 timeouts, 3 fell back to plain-text answers - which were themselves correct), avg 33.1s | **5/5** correct, avg 13.9s |
| `glm-4.7-flash` | 3/5 correct (2 timeouts), avg 74.7s | 5/5 correct, avg 41.7s |

**This inverts the hypothesis.** `gpt-oss:20b`'s native tool-calling did not
rescue it - it never once produced a proper `tool_calls` block; the model
answered in plain prose instead, correctly, every time it didn't time out
(confirmed: its `content` field named `CacheConfig.java` with correct
reasoning in all 3 non-timeout trials). Meanwhile its `json_mode` path -
supposedly the broken one - succeeded 5/5, fast, in this run. `glm-4.7-flash`
also showed tool-calling as the *less* reliable and *slower* mechanism (60%
success, real timeouts up to 150s) vs. its own solid `json_mode` record.
`qwen3-coder:30b` is the one model where tool-calling is a clean, real
improvement (matches its already-shipped `complete_with_tools()` deployment).

**The gpt-oss `json_mode` discrepancy is a real, unresolved open question, not
noise to wave away**: this run's prompt is smaller/simpler than
`attribution.py`'s actual live `triage` prompt (fewer candidate files, no full
`known_files` list, no accumulated retry context). The live failure could be
prompt-size- or context-shape-dependent rather than a flat per-model
reliability constant - this spike does not yet test that, and shouldn't be
read as "gpt-oss's `json_mode` is fine after all."

**What DOES generalize, and is now evidenced rather than hypothesized**: in
every gpt-oss failure case (both the live one and this spike's 2 timeouts /
0 successful `tool_calls`), the model's own output - wherever it actually
landed (`content` on a `tool_calls` miss, `reasoning` on the live `json_mode`
miss) - contained the *correct* answer in readable prose. The fix that's
actually justified by evidence is not "migrate to tool-calling" (this data
argues against that, per-model) but the cheaper, model-agnostic defensive
read floated as a secondary layer in the original recommendation: when
`json_mode`'s `content` comes back empty/unparseable, fall back to scanning
whatever side-channel field the backend populated instead
(`reasoning`/`reasoning_content`) before declaring failure - because on this
evidence, the right answer is usually sitting right there.

**Updated bottom line**: tool-calling reliability is not just model-dependent
in the abstract (already known) - it materially varies per model in ways that
argue for keeping `json_mode` as the default and gating tool-calling
per-model (`qwen3-coder:30b` only, for now, matching what's already shipped),
rather than adopting tool-calling as the general replacement for the small-
argument `json_mode` call sites this thread set out to fix. The higher-
leverage, lower-risk fix for the actual gpt-oss bug is hardening
`LLMClient.complete()`'s `json_mode` failure path with a reasoning-field
fallback, not a tool-calling migration.

## Re-test (2026-08-13): reproducing the real triage prompt shape, exactly

The previous re-test's `json_mode` result for `gpt-oss:20b` (5/5 correct) did
not match the real live failure (empty content). `run_spike_real_triage_shape.py`
closes that gap by calling `LLMClient.complete()` directly - not a hand-rolled
httpx approximation - with the real `_TRIAGE_SYSTEM_PROMPT`, real
`skeletonize_code()` output over 5 realistic candidate files, and, critically,
the real `max_tokens_override=300` `_tier_triage()` actually passes. Reading
`complete()`'s own code surfaced the leading hypothesis first: `max_tokens =
max(base_max_tokens, 12288) if is_reasoning else base_max_tokens` - and the
eval harness's `llm_chain` fallback entry sets `"reasoning": False` for
`gpt-oss:20b` ("Explicit, fast, non-reasoning fallback"). If gpt-oss silently
reasons regardless of that flag (as the prior re-test's `tool_call` misses
already showed - 1006-2292 chars of reasoning text landing somewhere Kriya
never classified as "reasoning"), a 300-token budget with no floor could run
out mid-reasoning, before the model ever reaches the JSON.

**3 trials each, 3 conditions, all against `gpt-oss:20b`:**

| Condition | Result |
|---|---|
| (a) Real live shape: `reasoning=False`, `max_tokens=300` | **3/3 EMPTY response** - exact reproduction. Usage line confirms it: `300 output tokens` used, every time, hitting the cap with nothing committed to content. |
| (b) `reasoning=True` (triggers the 12288 floor), `max_tokens=300` | 3/3 parsed successfully - no crash - but **3/3 picked the wrong file** (`App.java`, the compiler-reported line, not the real fix site `CacheConfig.java`) |
| (c) `reasoning=False`, `max_tokens=2000` (budget raised directly, flag untouched) | 3/3 parsed successfully - no crash - **2/3 correct**, 1/3 wrong (same wrong-file pattern) |

**Root cause is now precisely pinned down, not just hypothesized: this is a
token-budget-exhaustion bug, not a json_mode-incompatibility bug.** `gpt-oss`
spends its entire completion budget on silent internal reasoning before
committing anything to the JSON `content` field - confirmed deterministically
(3/3, every run hitting exactly 300/300 output tokens with empty content) -
and this happens regardless of Kriya's own `reasoning` config classification,
because that flag doesn't actually control whether the *model* reasons, only
whether Kriya's own code treats it as one (temperature/`<think>`-stripping/the
floor). Both tested fixes (flip the flag, or just raise the budget directly)
eliminate the crash 6/6 - the crash itself is a solved, evidenced problem.

**What's NOT solved by either fix**: correctness on the "compiler's line isn't
the real fix site" question itself - both conditions still picked the wrong
file some or all of the time (0/3 and 1/3 respectively). Sample size is small
(N=3 per condition, don't over-read the exact ratio), but the qualitative
signal holds: giving the model room to finish reasoning stops it from
crashing, it does not make gpt-oss reliably reason its way to the deeper root
cause over the more obvious surfacing site. That is a separate, already-known,
already-addressed-elsewhere problem - it's exactly why
`kriya/workflow/attribution.py`'s `self_diagnosis` tier is ranked ABOVE
`triage` in the first place (see [[project_kriya_error_resolution_gaps]]): a
locator/triage result that leads to one failed fix attempt is weaker evidence
than the model's own stated disagreement with that target on a confirmed
repeat. Triage getting this wrong sometimes is an argument for that tier
ordering already existing, not a new gap.

**Actual bottom line, now evidence-complete**: the fix that's justified is
neither the tool-calling migration nor a reasoning-field-fallback parse -
it's simpler than both. Raise `_tier_triage()`'s `max_tokens_override` above
300 (2000 tested clean; the real JSON answer itself is only a few dozen
tokens, the budget just needs enough headroom for whatever a model reasons
through first), or - more robustly, since the live config's `reasoning: False`
classification for gpt-oss turned out to be an unreliable predictor of whether
the model actually reasons - stop gating `complete()`'s reasoning-model
token floor behind that flag for `json_mode` calls specifically. Either is a
small, low-risk, precisely-targeted fix, not an architecture change.
