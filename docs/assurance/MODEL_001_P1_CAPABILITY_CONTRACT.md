# MODEL-001 P1 — Production Model Capability Contract

Implemented 2026-09-18, on top of `515faee` (the MODEL-001 campaign evidence
commit, itself on `35a73e5`). Closes the specific gap that campaign
exposed: *"capability resolution/profile selection exists as
campaign/manual machinery rather than a production Kriya contract."*

**This is implementation + deterministic validation.** The 9-arm MODEL-001
campaign matrix was not rerun. Live validation and the full pytest suite are
user-owned — see the end of this document.

## Trace before design

An abstraction already existed: `kriya/core/model_capabilities.py`
(`ModelCapabilityError`, `ConformanceResult`, `GenerationProtocol`,
`validate_tool_call_sample`, `capabilities_for_model`,
`generation_protocol_for_model`). This work extends that module — it does
not introduce a parallel one.

Affected-path map, traced before any code was changed:

| Concern | Location |
|---|---|
| Capability type (`ModelCapabilities`) | `kriya/config/config.py:12-20` — `native_tool_calls`, `json_mode`, `reliable_multiline_json`, `streaming`, `max_tool_argument_chars`, `preferred_edit_protocol`. Docstring: "Measured local-model protocol capabilities, never inferred from API shape." |
| Primary model | `LLMConfig.model`/`LLMConfig.capabilities` (`config.py:22-70`) |
| Fallback chain | `LLMConfig` → `llm_chain: List[FallbackModelConfig]` (`config.py:577-599`), each entry carries its own `.capabilities` |
| Per-agent-role overrides | `AgentModelConfig` (`llm: Optional[LLMConfig]`, `llm_chain: List[FallbackModelConfig]`) × `AgentRolesConfig` (planner/architect/reviewer/run_verifier/skill_gap/spec_compliance), `config.py:600-621` |
| Model/client construction | `LLMClient.__init__` (`kriya/core/llm.py:88-94`) — `self.model = config.llm.model` |
| Escalation dispatch | `call_with_escalation()` (`kriya/agents/agent.py:208-274`) — passes `model_override=cand.model` from a role's own `role_llm`/`role_chain` into `llm.complete()` |
| Tool-call protocol / structured-output gate | `LLMClient.complete_with_tools()` (`kriya/core/llm.py:376-476`) — the ONE pre-existing consumer calling `capabilities_for_model()`, raising `ModelCapabilityError` when `native_tool_calls` is false |
| Edit-protocol selection | `DeveloperAgent.run_generation()` (`agent.py:2118-2122`, calls `generation_protocol_for_model`) → `_fill_missing_content()` (`agent.py:1604-1615`, reads `preferred_edit_protocol` to decide `repair_with_patch` vs `repair_with_full_file`) |
| Config load/merge/validation | `kriya/config/config.py::load_config()`; SEC-009 authority classification `kriya/config/authority.py` |
| Existing profiles/registries | **None** — this was the gap. |

**The two real, confirmed gaps found by this trace** (not hypothesized —
verified by reading `capabilities_for_model`'s own body):

1. `capabilities_for_model()` checked `config.llm.model` and
   `config.llm_chain` only. It never checked `config.agent_llms.<role>.llm`/
   `.llm_chain` at all — an agent-role override's own `.capabilities` field
   (which the schema already supports) was silently ignored, always falling
   through to the bare `ModelCapabilities()` class defaults regardless of
   what the role's config actually said.
2. Any model with no matching binding, or a binding whose `.capabilities`
   was never touched, silently received the bare `ModelCapabilities()`
   defaults — which happen to assume `native_tool_calls=True`/
   `json_mode=True`. That is an assumption baked into the dataclass, not a
   verified fact about any specific model. Nothing distinguished "measured
   safe" from "never checked."

`reliable_multiline_json` was traced to confirm it has zero behavior-changing
consumers anywhere in `kriya/` today (only threaded into `GenerationProtocol`
and never read by branching logic) — left in the profile type since it's
part of the existing schema and campaign evidence, but no new consumer was
invented for it (per "do not invent speculative capability dimensions").

## Architecture

```
configured model identity
        │
        ▼
resolve_model_capability_profile(config, model)   [kriya/core/model_capabilities.py]
        │
        ├─ 1. explicit validated override  (a config binding for THIS model
        │      whose .capabilities differs from the bare class defaults)
        ├─ 2. known production profile     (KNOWN_MODEL_PROFILES — hardcoded
        │      Python data, MODEL-001 campaign evidence, never a config field)
        └─ 3. safe fallback / fail closed  (UNVERIFIED_MODEL_CONSERVATIVE_PROFILE)
        │
        ▼
ResolvedCapabilityProfile { model, capabilities, source }
        │
        ├─→ LLMClient.complete_with_tools()   — native_tool_calls gate (existing)
        └─→ DeveloperAgent.run_generation()   — json_mode / preferred_edit_protocol (existing)
```

`capabilities_for_model()` and `generation_protocol_for_model()` are kept as
thin, unchanged-signature wrappers over `resolve_model_capability_profile()`
— the two pre-existing call sites needed zero changes to their own call
shape.

## Profile type

Reused `ModelCapabilities` (pydantic, `kriya/config/config.py`) as-is — no
new capability type was introduced. Added one small, additive dataclass,
`ResolvedCapabilityProfile(model, capabilities, source)`, to carry
provenance without changing what the two existing consumers already expect.

## Resolution precedence (final, as implemented)

1. **Explicit validated override.** A config binding exists for this exact
   model identity (primary `llm`, an `llm_chain` entry, or any
   `agent_llms.<role>.llm`/`.llm_chain` entry) whose `.capabilities` differs
   from bare `ModelCapabilities()` defaults in at least one field. Used
   as-is — pydantic has already validated its types/values.
2. **Known production profile.** `KNOWN_MODEL_PROFILES` — seeded only from
   live MODEL-001 campaign evidence (`spikes/model001_campaign/
   capability_profiles.json`, corrected reading) for `qwen3-coder:30b`,
   `qwen3.6:35b-a3b-q4_k_m`, `qwen3.5:9b`. Used when a binding exists for a
   known model but its own `.capabilities` was never touched — real evidence
   outranks an untouched default that happens to look the same.
3. **Safe fallback / fail closed.** `UNVERIFIED_MODEL_CONSERVATIVE_PROFILE`
   (`native_tool_calls=False`, `json_mode=False`,
   `reliable_multiline_json=False`, `streaming=True`,
   `preferred_edit_protocol="full_file"`) — for a model with no binding
   anywhere and no known evidence, or a binding with neither an explicit
   override nor known evidence. This is a deliberate behavior change (see
   Regression section) and is what makes `native_tool_calls` fail closed at
   the one consumer that already enforces it, and what makes edit-protocol
   selection prefer the safer full-file path for a genuinely unverified
   model, without inventing any new enforcement machinery.

**Known limitation, disclosed rather than engineered around:** "explicit"
detection is by value-equality against the bare class defaults. A user who
explicitly sets every field to exactly its own default value is
indistinguishable from a user who never touched the block at all. This is a
deterministic, honest tradeoff (documented, not silent) — the alternative
(tracking pydantic's `model_fields_set` through the full config-merge
pipeline) was judged out of scope for "smallest production-grade
mechanism"; revisit if it ever causes a real, observed problem.

## Known models

Seeded only from MODEL-001 campaign live evidence, exactly the three models
that campaign tested — no others:

| Model | native_tool_calls | json_mode | reliable_multiline_json | preferred_edit_protocol |
|---|---|---|---|---|
| `qwen3-coder:30b` | true | true | true | small_native_tools |
| `qwen3.6:35b-a3b-q4_k_m` | true | true | true | small_native_tools |
| `qwen3.5:9b` | true | true | true | small_native_tools |

## Model-name normalization

**Exactly one rule**: case-folding the full model string
(`_normalize_model_identity`). "qwen3.5:9B" and "qwen3.5:9b" are the same
identity (Ollama itself resolves tags case-insensitively — confirmed live
during the campaign). No stripping, no splitting on `:`/`-`, no prefix or
fuzzy matching. "qwen3.5:9b" and "qwen3.5:9b-mlx" (or "qwen3.5:35b-a3b")
remain distinct identities — tested explicitly (near-match tests below).

## Unknown models

No hardcoded whitelist. A model not in `KNOWN_MODEL_PROFILES` and not
explicitly configured still works — it gets
`UNVERIFIED_MODEL_CONSERVATIVE_PROFILE`, which the existing
`complete_with_tools()` gate correctly refuses tool-calling for
(`ModelCapabilityError`) and which the existing edit-protocol consumer
correctly treats as "prefer full-file." A user can unlock native tool-calling
or precise anchored edits for a genuinely new local model at any time by
setting even one explicit `.capabilities` field in their own config — no
source-code edit required.

## Overrides

Type/value validation is pydantic's own (`ModelCapabilities` field types,
including `max_tool_argument_chars`'s `ge=256` constraint) — an invalid
override (wrong type, out-of-range value) raises at config-construction
time, before resolution ever runs; nothing here silently repairs an invalid
value. Merge is deterministic (precedence above). Provenance is the
`ResolvedCapabilityProfile.source` field, always populated.

## Fallback / model chain

Resolution is per actual model identity at every tier — primary, each
`llm_chain` entry, and each `agent_llms.<role>`'s own `llm`/`llm_chain` are
looked up independently; nothing here assumes the primary model's profile
applies to any other binding. Kriya's existing escalation architecture
(`call_with_escalation`, the Developer quality-gate retry loop's own
fallback-model construction) is untouched — this package adds resolution
*correctness* underneath it, not a new escalation mechanism, and does not
remove or bypass MODEL-001's pinned-chain behavior from the campaign itself
(that pinning was a per-run *config choice*, not a code path this package
could or should have altered).

## Consumers

Both pre-existing consumers already call through the (now-corrected)
resolver with no call-shape change:

- `LLMClient.complete_with_tools()` — `native_tool_calls` gate.
- `DeveloperAgent.run_generation()` → `_fill_missing_content()` —
  `json_mode` (in the Developer fallback path,
  `generation_protocol.json_mode`) and `preferred_edit_protocol`.

No third consumer of capability-sensitive behavior was found in the trace
(`reliable_multiline_json` has none; `streaming` has one, already wired,
in `run_generation`'s `effective_stream_callback`).
**`UNRESOLVED_CAPABILITY_CONSUMER_PATHS = 0`.**

## Observability

`resolve_model_capability_profile()` logs once per distinct `(model,
source)` pair per process (`_ALREADY_LOGGED_RESOLUTIONS`, a module-level
dedup set) — the actual model identity, capability-profile source, and
every effective capability field, at INFO level, on `kriya.core.
model_capabilities`. Deliberately not per-call/per-token: resolution can be
invoked once per completion, and logging every one of those would violate
the "avoid noisy repeated logging" requirement. A run/agent-initialization
hook that logs once at Kernel/workflow start was considered and rejected as
unnecessary scope — the per-(model,source) dedup already gives exactly one
log line the first time any given binding is actually used, which is the
information that matters, without needing to plumb a new call through
every one of Kriya's many agent-construction sites.

## Security / authority

`ModelCapabilities`' own field set (`native_tool_calls`, `json_mode`,
`reliable_multiline_json`, `streaming`, `max_tool_argument_chars`,
`preferred_edit_protocol`) carries no filesystem, network, tool-approval,
command, or policy-bypass semantics — confirmed by a structural test
(`test_capability_profile_field_set_carries_no_authority_shaped_fields`)
asserting no field name matches an authority-shaped term list. This package
adds zero new fields to that type.

`llm`, `llm_chain`, and `agent_llms.<role>` are already unconditionally
`SECURITY_AUTHORITY`-or-conditionally-`REPOSITORY_SAFE`-classified under
SEC-009 (`kriya/config/authority.py::classify_field`,
`agent_role_field_classification`) — verified by reading that file, not
assumed. `KNOWN_MODEL_PROFILES` is hardcoded Python data, not a config
field at all, so it needs no new SEC-009 classification and cannot be
injected or widened by a repository config. This package makes zero changes
to `kriya/config/authority.py`.

## Relationship to the campaign spike

`spikes/model001_campaign/probe_capabilities.py` and
`capability_profiles.json` are **not imported by, and production Kriya does
not depend on, this package**. `KNOWN_MODEL_PROFILES`'s three entries were
transcribed from that frozen evidence by hand, redeclared as Python
constants in `kriya/core/model_capabilities.py`, and cross-checked again in
`tests/test_model_capabilities.py` (which redeclares the same expected
values independently, so the test suite fails loudly if the two ever
silently drift apart).

**Disclosed residual duplication**: the evidence lives in two places
(`spikes/model001_campaign/capability_profiles.json`, frozen, and
`KNOWN_MODEL_PROFILES`, production). Making the campaign spike consume the
production registry instead of its own frozen JSON was considered and
explicitly not done this pass — the spike's JSON is deliberately frozen,
timestamped evidence (the campaign's own STOP rule: "frozen before the
pilot and never revised after seeing any campaign run's outcome"), and
wiring it to read live from `kriya/core/` would blur that evidentiary
freeze. This is a disclosed, accepted duplication, not an oversight.

## Tests

`tests/test_model_capabilities.py`: 27 tests total (3 pre-existing,
unmodified in behavior; 24 new), covering all 20 required scenarios (several
scenarios share a test where the assertion is the same shape — e.g. known
M1/M2/M3 resolution via primary/llm_chain/agent_llms bindings respectively
also proves scenarios 10/11/12). `CROSS_MODEL_CAPABILITY_LEAK_PATHS = 0` and
`UNRESOLVED_CAPABILITY_CONSUMER_PATHS = 0` are proven by
`test_unknown_model_with_no_binding_gets_conservative_profile_not_m1`,
`test_near_match_model_name_does_not_receive_wrong_profile`,
`test_different_models_in_one_llm_chain_retain_distinct_profiles`, and the
consumer-path tests (14-17).

## Regression findings — real, disclosed, all resolved

Tracing adjacent tests (config, LLM client, tool calling, agent model
selection, edit protocols, policy/authority composition) surfaced **5 real
regressions**, all caused by the same root pattern: an existing test used
an unconfigured/default model name with untouched capabilities and
implicitly relied on the old silent-trust behavior this package exists to
remove.

- `tests/test_llm_extra.py`: 4 tests (`test_complete_with_tools_uses_
  fallback_extra_body_not_the_primarys`, `test_complete_with_tools_returns_
  tool_calls`, `test_complete_with_tools_handles_empty_tool_calls`,
  `test_complete_with_tools_malformed_arguments_does_not_crash`) — none are
  about capability gating; each now explicitly establishes the
  tool-calling-supported precondition it was implicitly assuming
  (`max_tool_argument_chars` bumped to a value that genuinely diverges from
  the class default, making the binding a real "explicit override" rather
  than an untouched, unverified one).
- `tests/test_agents.py::test_developer_explicit_patch_operation_overrides_
  locator_heuristic` — same pattern, for `preferred_edit_protocol`.

None of these tests were weakened — each fix strengthens the test's own
setup to actually establish the precondition its assertions depend on,
which the test was previously getting "for free" from the bug this package
fixes.

**8 additional failures found while tracing a broader set of DeveloperAgent-
adjacent test files** (`test_tool001_autonomous_tool_execution.py` ×7,
`test_workflow.py::test_django_test_command_bypasses_application_entrypoint_
infrastructure_classification` ×1) were confirmed **pre-existing and
unrelated** via `git stash` (identical failures with this package's changes
fully reverted) — several look Docker/pip/maven-network-dependent by name.
Out of scope for this package; not fixed, not hidden.

Focused-test result after all fixes:
`tests/test_model_capabilities.py` + `tests/test_llm_extra.py` +
`tests/test_agents.py` + `tests/test_config.py` +
`tests/test_llm_egress_policy_integration.py` +
`tests/test_repair_executor.py` + `tests/test_self_correction.py` +
`tests/test_workflow_execution_policy_config_wiring.py` +
`tests/test_sec009_p2_authority_approval.py` = **343 passed**. Broader
DeveloperAgent-adjacent sweep (30 files): **1714 passed, 8 pre-existing
failures (confirmed unrelated), 1 deselected.**

## Honest statement

This package makes model-capability resolution a deterministic, in-product
contract for the three models MODEL-001 evidenced, closes a real silent-
capability-leak gap (`agent_llms` was never checked) and a real
silent-trust gap (unverified models inherited permissive class defaults),
and fails closed at the one place that already enforced tool-calling
authority. It does **not** implement a general, live-probing capability
discovery mechanism (deliberately, per the task's own "prefer deterministic
configuration over runtime probing" rule) and does **not** make
`spikes/model001_campaign/`'s frozen evidence and this package's production
registry a single source of truth (disclosed duplication above). MODEL-001
stays `NEEDS_IMPLEMENTATION` until user-owned live validation and full
pytest both return green — see the campaign risk-register entry, updated
only after that evidence is reviewed.
