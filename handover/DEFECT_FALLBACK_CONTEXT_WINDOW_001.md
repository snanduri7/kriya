# FALLBACK-CONTEXT-WINDOW-001: the budgeted context window was not the one sent

## Status
FIXED (Backlog 6.5, 2026-09-27). Awaiting the user's pytest run.
- Found: 2026-09-27, handover/evidence/BATCH6/final-gate-3/README.md.
- Classified: P2/triage.

## The defect
A binding's `context_window` drove Kriya's budgeting:
- `_dispatch_budget` fell back to it when no served window was known;
- `allocation_window` and routing used it too.

The runtime, though, received a window only through an explicit `extra_body.options.num_ctx` (or a PRD-016 tier expansion). So a binding that declared only `context_window` was budgeted at that value but served at the provider default: Ollama's 2048/4096, or `OLLAMA_CONTEXT_LENGTH`. The packaged default qwen3.6 chain entry is one such binding. Prompts could be silently truncated.

The runtime fingerprint also recorded no requested window. For an unverified runtime it copied the requested window into `effective_context_window`, a served window it never observed. Mocked runs, unpulled models and non-Ollama servers were therefore labelled `served_num_ctx`.

## The fix: one definition, in the adapter seam
Two new functions in `kriya/core/model_runtime.py`:
- **`requested_context_window(extra_body, context_window)`**: the explicit provider option wins deterministically; otherwise the declared `context_window`.
- **`request_extra_body(extra_body, context_window)`**: the body as sent. It returns the body itself when it already carries the window, or a copy with the window added in the runtime's form.

**Where it is used:**
- **Both LLMClient choke points** (`complete_result`, `complete_with_tools_result`) apply `request_extra_body` with the binding's declared window, including when the caller passes `extra_body_override`. The fingerprint, the budget and the request all read that one body.
- **`resolve_configured_model_runtime`** looks up the model's declared window through `_binding_for`. The embedding model has no chat binding and gets no window, so the certification identity is unchanged.
- **The other consumers** use `requested_context_window`:
  - `allocation_window`;
  - routing's `_served_window`;
  - `qualification_config`, which is what the qualification cases send;
  - qualification's capacity-case window;
  - doctor's primary probe.
- **Tripwire:** only the seam and LLMClient call `configured_context_window`.

**Evidence is truthful:**
- `effective_context_window` is set only from probe evidence.
- Budget labels:
  - `served_num_ctx`: the served window;
  - `runtime_capped`: served is less than requested (for example the trained length), logged with both numbers;
  - `config_declared`: the unverified requested window.

**Conflicts are deterministic.** When an explicit option differs from an explicitly declared `context_window`, the option is requested and budgeted everywhere. It is logged when LLMClient starts and listed in doctor's `model.runtime_fingerprint` evidence (`context_window_overrides`). A config that sets only the provider option has no conflict.

**Unchanged:**
- The genuine `CONTEXT_BUDGET_UNSATISFIABLE` refusal.
- The PRD-016 tier expansion, which still overrides the window for one request.
- The inference-settings digest, which excludes the window (tested).

## Identity impact (records the user may owe)
- **demo-03 production:** unchanged. Both bindings already send `num_ctx 32768`, equal to their declared window, so `request_extra_body` returns the same body (tested). The runtime digests and the QUALIFIED records (primary ea90552d, fallback fc063b9e) still apply. The user's `doctor --production` run confirms this.
- **Any other binding without an explicit option** (for example the packaged default qwen3.6 chain entry) now requests its declared window. Its runtime digest changes, so an earlier qualification record for it reads STALE and needs a `kriya model qualify` run.
- `MODEL_PROTOCOL_ADAPTER_VERSION` is not bumped: no record made under an explicit window changes.

## Scope left to INF-001
There is one adapter today (Ollama). The window option is sent through the seam regardless of provider, exactly as explicit `options` blocks already were. Selecting an adapter per runtime, including providers without a per-request window, is INF-001's deliverable.

## Tests
`tests/test_fallback_context_window_001.py` (17 tests):
- primary and fallback;
- explicit option, implicit window, an override body;
- conflict, and no conflict when the window is undeclared;
- a genuine oversize refusal;
- an unverified fingerprint, an exact served window, a runtime cap;
- the five-consumer digest-consistency test (dispatch, configured, transition, routing, qualification);
- the embedding runtime requests no window;
- the inference identity is unchanged;
- the demo-03 shape;
- the qualification cases send the window;
- doctor's requested window;
- the tripwire.

**Mutations:** 11 of 12 killed. The survivor was a redundant `request_extra_body` in the qualification case context, which I removed. A mutation of the real guard, `qualification_config`, is killed.

**Related existing suites** (`prd013/014/016/017/018/019`, doctor, qualification identity, production fallback identity): 379 passed, run as small targeted checks.
