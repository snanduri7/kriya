# QUAL-CONFIG-001 - Externalize model qualification policy

Status: **CLOSED** (user adjacent suite 513 passed, 2 skipped; focused 75 passed; 13/13 mutations killed; lint zero). P1.

This change is a **REFACTOR, not a POLICY CHANGE**. Every default equals the value that was hard-coded before, and no qualification budget was changed for any model. The qwen3.8 budget question stays evidence-driven: see §6 and `handover/GRAPHIFY_ACCEPTANCE_REPORT.md` §7b.

## 1. Inventory (`kriya/core/model_qualification.py`, TRACED)

Origin of every budget: they were all introduced in `cb17a5c` (PRD-013..016, 2026-09-25). No rationale was recorded in the commit or in the PRD-013/014/016 handovers. Intent is therefore UNKNOWN, and the values are treated as undocumented policy, not as arbitrary.

Values unchanged across /3 (MEASURED): `git diff 6c163f2^ HEAD` shows none of these literals changed between the `kriya-qualification/3` bump and this change.

| Location (pre-change) | Literal | Used by | Classification | Configurable? | Reason |
|---|---:|---|---|---|---|
| `case_plain_completion` | 64 | answer budget | QUALIFICATION_POLICY | `cases.plain_completion.max_tokens` | output allowance, decides PASS/FAIL |
| `case_finish_reason_stop` | 256 | answer budget | QUALIFICATION_POLICY | `cases.finish_reason_stop.max_tokens` | same |
| `case_structured_json` | 256 | answer budget | QUALIFICATION_POLICY | `cases.structured_json.max_tokens` | same |
| `case_multiline_json` | 512 | answer budget | QUALIFICATION_POLICY | `cases.multiline_json.max_tokens` | same |
| `case_native_tool_calls` | 256 | tool-call budget | QUALIFICATION_POLICY | `cases.native_tool_calls.max_tokens` | same |
| `case_multiple_tool_calls` | 512 | tool-call budget | QUALIFICATION_POLICY | `cases.multiple_tool_calls.max_tokens` | same |
| `case_tool_argument_integrity` | 512 | tool-call budget | QUALIFICATION_POLICY | `cases.tool_argument_integrity.max_tokens` | same (the qwen3.8 finding) |
| `case_streaming_assembly` | 256 | answer budget | QUALIFICATION_POLICY | `cases.streaming_assembly.max_tokens` | same |
| `case_reasoning_behavior` | 2048 | answer budget | QUALIFICATION_POLICY | `cases.reasoning_behavior.max_tokens` | same |
| `case_full_file_raw_content` | 1024 | answer budget | QUALIFICATION_POLICY | `cases.full_file_raw_content.max_tokens` | same |
| `case_anchored_edit_protocol` | 1024 | answer budget | QUALIFICATION_POLICY | `cases.anchored_edit_protocol.max_tokens` | same |
| `case_malformed_output_recovery` | 512 | answer budget | QUALIFICATION_POLICY | `cases.malformed_output_recovery.max_tokens` | same |
| `case_cancellation_semantics` | 1024 | streamed request | QUALIFICATION_POLICY | `cases.cancellation_semantics.max_tokens` | resource limit |
| `case_cancellation_semantics` | 64 | health check after cancel | QUALIFICATION_POLICY | `…cancellation_semantics.health_check_max_tokens` | resource limit |
| `case_cancellation_semantics` | 120 s (`ctx.get("first_delta_timeout", 120)`) | wait for first delta | QUALIFICATION_POLICY | `…first_delta_timeout_seconds` | timeout, decides FAIL |
| `case_cancellation_semantics` | `< 10` s | settle bound | QUALIFICATION_POLICY | `…max_settle_seconds` | threshold |
| `case_context_capacity` | 64 | answer budget | QUALIFICATION_POLICY | `cases.context_capacity.max_tokens` | CONFIRMED: exhausted by reasoning (report §7b) |
| `_CAPACITY_HEADROOM_TOKENS` | 384 | target = window − headroom | QUALIFICATION_POLICY | `…context_capacity.headroom_tokens` | room for answer and template |
| `case_context_capacity` | `0.9 * target` | fill tolerance | QUALIFICATION_POLICY | `…context_capacity.min_fill_ratio` | threshold |
| `case_context_capacity` | 600 s | probe client timeout | QUALIFICATION_POLICY | `…context_capacity.request_timeout_seconds` | timeout |
| `BYTES_PER_TOKEN_MARGIN` | 0.9 | tokenizer floors → measured limits | QUALIFICATION_POLICY | `measurement.bytes_per_token_margin` | margin on recorded limits (used by dispatch) |
| `measured_limits` | `* 1.5` | `reasoning_tokens_max` | QUALIFICATION_POLICY | `measurement.reasoning_tokens_headroom` | margin on recorded limits |
| `case_output_truncation` | 16, `reasoning_override=False` | truncation probe | ALGORITHM_INVARIANT | no (commented) | a budget far below the requested output IS the test |
| `case_timeout_semantics` | 0.001 s timeout; 512 | timeout probe | ALGORITHM_INVARIANT | no (commented) | forces the timeout; the request never completes |
| `case_endpoint_error_semantics` | 16 | nonexistent model | ALGORITHM_INVARIANT | no (commented) | nothing is generated |
| `case_tokenizer_measurement` | 1 | prompt-usage probes | ALGORITHM_INVARIANT | no (documented) | only prompt usage is measured |
| `case_context_capacity` | 40/80 units, 1 token, temperature 0.0 | token-rate probes | ALGORITHM_INVARIANT | no (commented) | measurement construction; temperature 0 measures the server, not sampling |
| `CAPABILITIES`, `_BASE_REQUIREMENTS`, `_ROLE_REQUIREMENTS` | case lists | required cases per role | ALGORITHM_INVARIANT (authorization contract) | no | configuring these would let a config make a required case optional (explicitly out of scope) |
| `QUALIFICATION_POLICY_VERSION`, `QUALIFICATION_SCHEMA_VERSION` | /3, 2 | record versioning | ALGORITHM_INVARIANT | no | code/schema versioning |
| Prompts, `NOTE_TEXT`, tool schemas, `TOKENIZER_CORPORA`, expected answers | text | case content | ALGORITHM_INVARIANT | no | the assertion itself |
| `kriya/core/llm.py` `REASONING_MIN_MAX_TOKENS` 12288, `token_budget.DEFAULT_REASONING_ALLOWANCE_TOKENS` 2048 | — | LLMClient production dispatch | out of scope | no | runtime dispatch policy, not qualification; see report §7b for the tool-path parity finding |

No sample/retry counts exist in qualification: each case runs once. There are no other tolerance or byte-limit literals.

## 2. Design

**Config section.** `AppConfig.model_qualification: ModelQualificationConfig` (`kriya/config/config.py`) has `cases.<case>` and `measurement`. All models use `extra="forbid"`, positive and bounded fields (`gt=0`; ratios in `(0, 1]`; headroom ≥ 1).

**Defaults.** The defaults live only in the pydantic model. `default_config.yaml` documents the section but does not set it, because `load_config`'s one-level merge would otherwise drop the unlisted defaults. A partial case entry keeps that case's own defaults (`_partial_case_keeps_its_defaults`).

**Capability-aware budget.** `reasoning_max_tokens` (optional, per case) applies only when the inference identity qualified sends `reasoning: true` (`ctx["reasoning"] = settings.reasoning`). There is no model-name logic.

**Mechanics.** Cases read their budget through `_case_budget(ctx, case)`, and other bounds through `_policy(ctx)`. Each case's evidence gets `policy: {max_tokens, source: "model_qualification.cases.<case>.<field>", …}`.

**Identity binding.**
- `build_record(policy=)` stores `qualification_policy` (the effective values) and `qualification_policy_digest`: sha256 over a schema tag plus the canonical dump.
- `record_is_current`/`assess` take `policy_digest`. A different digest is STALE, with the reason "the qualification policy settings changed".
- `measured_limits_for` derives the digest from its `config`. Tokenizer floors only come from records under the same policy.
- `save_record` never merges environment evidence across policies.
- The record file key is unchanged (runtime + settings), so re-qualifying under a new policy replaces the record.

**Legacy records.** Records written before this change have no digest. `LEGACY_V3_POLICY_DIGEST` (`sha256:2aa1c552…`) is pinned in code and checked in a test against an explicit table of the /3 literals, not against the current defaults. Such a record therefore counts only while the effective policy equals the /3 values it was actually produced with.

**Callers.** Every production `assess` passes `policy_digest=policy_digest_for(cfg)`: doctor, `model status`, routing, release candidate, the model-transition profile, and the workflow's runtime record. A structural AST test enforces this.

**Authority.** `model_qualification` joins `mcp`/`static_analysis` as blanket SECURITY_AUTHORITY (`_BLANKET_SECURITY_TOP_KEYS`). An unapproved config setting it is denied by SEC-009 (tested through `load_config`).

**Visibility.**
- The doctor's `model.qualification` evidence carries `qualification_policy_digest` and `qualification_policy`.
- `kriya model qualify` prints the policy digest. For a non-PASS case it also prints the controlling values, their config path, the budget actually sent and the finish reason.

**Resume.** The new section sits in the resume `config` fingerprint remainder, like any new field, so a policy change also invalidates resume.

## 3. Verification

**Focused (run by Claude):** `tests/test_qual_config_001.py`, 51 passed.

**Mutations (run by Claude): 13 of 13 killed**, with every file restored hash-exact afterwards. The script is in the session scratchpad.
1. The configured budget ignored (back to the literal).
2. The staleness check omits the policy.
3. The reasoning override ignored.
4. The record not bound to its policy.
5. A production caller omits `policy_digest`.
6. Measured limits ignore the configured policy.
7. A partial entry loses its defaults.
8. Environment evidence merged across policies.
9. Capacity headroom back to the literal.
10. Capacity fill ratio back to the literal.
11. Cancellation timeout back to the literal.
12. Reasoning headroom back to the literal.
13. `model_qualification` not SECURITY_AUTHORITY.

**Static:** ruff 0 findings, pylint exit 0.

**Real records (MEASURED):**
- `kriya model status` on the PRD-036 config: every qwen3-coder and qwen3.6 role is still QUALIFIED. The pre-change records count under the default policy.
- On the Graphify qwen3.8 config the result is unchanged: `NOT_QUALIFIED`, `tool_argument_integrity: FAIL`.

**For the user (adjacent tier, not run by Claude):**
```bash
.venv/bin/pytest tests/test_qual_config_001.py tests/test_prd014_model_qualification.py tests/test_model_qual_identity_001.py \
  tests/test_qual_environment_identity.py tests/test_production_doctor.py tests/test_prd019_model_routing.py \
  tests/test_prd019_milestone_route_resume.py tests/test_prd017_fallback_transition.py tests/test_retry_inference_identity.py \
  tests/test_prd016_adaptive_budget.py tests/test_prd016_allocation.py tests/test_prd016_token_budget.py \
  tests/test_prd008_resume_fingerprints.py tests/test_fallback_context_window_001.py \
  tests/test_model_evidence_hardening_final.py tests/test_backlog_registry.py -q
```
Full pytest is deferred to the end of the qualification-repair batch, per the progressive-verification rule.

## 4. Closure

Close QUAL-CONFIG-001, and add its registry row as CLOSED (an OPEN P1 row fails `tests/test_backlog_registry.py` by design), once the user's adjacent run is green.

## 5. Files changed
- `kriya/config/config.py`: the `ModelQualificationConfig` models and the `AppConfig.model_qualification` field.
- `kriya/config/authority.py`: `_BLANKET_SECURITY_TOP_KEYS`.
- `kriya/core/model_qualification.py`: the policy helpers, digest and legacy digest, staleness binding, and cases driven by policy.
- `kriya/production_doctor.py`, `kriya/cli.py`, `kriya/core/model_routing.py`, `kriya/core/release_candidate.py`, `kriya/workflow/model_transition.py`, `kriya/workflow/workflow.py`: callers pass `policy_digest`. The doctor and CLI also got the evidence and output additions above.
- `kriya/config/default_config.yaml`, `docs/user_guide.md`, `CLAUDE.md`: documentation.
- `tests/test_qual_config_001.py`: new.

## 6. Next (step 12 of the task, NOT started)

- **The ladder has already run once** (report §7b). At 512 the one complete response failed the unchanged argument assertion, so "the first sufficient budget" for `tool_argument_integrity` is not established: that sample was complete at 512 and still wrong. Re-running the same ladder unchanged would be sampling for variance, which the rules forbid. A new attempt needs the user's explicit approval of a declared-N variance or attribution study.
- **`context_capacity`'s 64-token answer budget and 384-token headroom are a CONFIRMED harness mismatch for reasoning models.** Now that they are configuration, the fix is a policy choice for the user. One option: `reasoning_max_tokens` on `context_capacity` together with a headroom large enough for it, derived from measured reasoning use.
