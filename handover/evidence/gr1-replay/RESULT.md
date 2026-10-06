# Graphify context-discrimination replay (owner-authorized experiment, 2026-10-06)

Not a Kriya change, not a Graphify run. Two independent single calls to the canonical Developer model; no retry, no
fallback; responses frozen before any evaluation; the external evaluator ran only on frozen candidates and was never
model input.

## Inputs (`run/manifest.json`)

- Model `qwen3-coder:30b-kriya-e52213655394`, artifact `sha256:141e95d7…`, Ollama 0.34.4, exact runtime record
  `0769fe62…` (the run's own). Wire settings unchanged: `ollama_native` `/api/chat`, temperature 0.7, top_p 0.8,
  top_k 20, num_ctx 32768, num_predict 16384, think false, truncate false, stream true. **No seed.**
- A = the sealed attempt-2 wire body, byte for byte: sha256 `c734c21c…` (provider-reported prompt tokens 8,111, the
  canonical attempt-2 count).
- B = A + one window, sha256 `4d052ead…` (8,345 prompt tokens). Only difference (checked before the calls): frozen
  engine.py lines 215-232 (`_csharp_collect_type_refs`' `generic_name` block; source sha256 `ac9b349a…`) in Kriya's
  exact-source window format, in line order before the 5356 window; section sha256 `41fa0448…`, 1,057 chars.

## Results

| | A (original request) | B (+ repository precedent) |
|---|---|---|
| Response | 1,095 tokens, stop, 42.6 s | 1,061 tokens, stop, 27.1 s |
| Protocol | valid (Kriya `parse_structured`: 2 edits) | valid (2 edits) |
| Anchors | inside the shown windows | inside the shown windows |
| Applicable / compiles | yes / yes | yes / yes |
| Field used on `generic_name` | `child_by_field_name("identifier")` (does not exist) | `child_by_field_name("name")` (does not exist; the precedent's first line) |
| None-guard | yes, falls back to raw text `Get<int>` | yes, falls back to raw text |
| Children iteration (the precedent's working part) | no | **no** |
| Unqualified call | hunk in the unreachable fallback (invocation has no `name` field) | correct new `generic_name` branch, but same field lookup |
| this-qualified call | attempted, inert | attempted, inert |
| Non-generic path | preserved | preserved |
| Reviewed acceptance | 2/5 (controls only) | 2/5 |
| `test_csharp_call_site_generic_args.py` | 6/6 | 6/6 |
| Evaluator regression | 76/76 | 76/76 |
| External evaluator | 2/5 (A, B, E fail) | 2/5 |

Both candidates are semantic failures that are behaviourally inert (no crash, no fix), not protocol failures, not
malformed, not byte no-ops. A Kriya run would stage either, pass regression, and fail acceptance for REQ-1/REQ-3
(no false success).

## Interpretation (owner matrix: A FAIL, B FAIL)

The precedent alone did not change the outcome. B read it (it adopted the precedent's field name and added the
structurally correct unqualified branch) but copied the lookup that returns `None` and omitted the children loop that
makes the precedent work. Note: the frozen precedent itself starts with a dead `child_by_field_name("name")` lookup
before its working fallback, so it is a mixed signal. The replay of A also differed from the canonical attempt-2
output (guarded vs crashing), which shows the sampling variance at temperature 0.7 with no seed: one call per arm
supports only LOW confidence.
