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
