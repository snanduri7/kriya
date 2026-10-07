# QUALIFICATION-ARTIFACT-STABILITY-001: qualification evidence attributed across a mid-run artifact change

## Status

**FIXED** (2026-10-07) on `fix/qualification-artifact-stability` from main 272ef16, certified by the deterministic
suite below, ruff/pylint at zero and one full Python suite at the branch tip (counts in the batch report). Severity P1:
a qualification authority defect. Registry row `QUALIFICATION-ARTIFACT-STABILITY-001` (CLOSED).

## Observation (MEASURED)

During the Ollama 0.40.0 post-upgrade requalification (external evidence `~/kriya-m1-live/postupgrade-ollama040`,
`evidence/qwen36/artifact_mutation/`), Ollama's automatic model conversion rewrote the qwen3.6 tag from manifest
`b2e94121…` (blob `f5ee307a…`) to `0314ad24…` (blob `3f87bb0b…`) while `kriya model qualify` was alive: the server
log shows the manifest written at 15:02:52 IST and the new blobs born at 15:02:18/15:02:50; the API log shows no
`create`/`copy` call. The partial record `0fc7f3bb…` seals every case under the first fingerprint although the later
cases ran against the converted artifact. `kriya model status` then failed closed (the new digest resolved to
MISSING), which was correct; the record's own attribution was not.

## Producer (TRACED)

`kriya/core/model_qualification.py::run_qualification` (main 272ef16): the fingerprint is resolved once
(`resolve_configured_model_runtime(config, model, fresh=True)`), every case runs in a plain loop, and `build_record`
binds all results to that single fingerprint. No boundary re-observes the served identity.

## Root cause (CONFIRMED)

Discriminating check: the deterministic reproducer `test_the_qwen36_incident_shape_invalidates_the_qualification`
(three cases pass under artifact A, the tag then serves artifact B, the remaining cases attempt to run) and its
negative control `test_negative_control_without_the_guard_the_incident_is_attributed_to_artifact_a`: with the one
guard call site bypassed, the same scripted run seals four cases under artifact A although the fourth ran under B -
exactly the pre-fix behaviour. Competing explanations ruled out: the fingerprint itself is stable across loads and
unloads (32 external observations, one distinct identity, during the successful requalification), so the gap is the
absence of re-observation, not a volatile identity.

## Invariant and design

At every observable boundary of one run - start, immediately before each case, immediately after each case, close -
the served identity must equal the baseline the record is sealed under. The identity compared is the existing
authority, `ModelRuntimeFingerprint.digest` (PRD-013: artifact digest, weights blob, provider version,
renderer/parser, tokenizer, normalized server parameters, served capabilities, configured and effective windows,
adapter version, Kriya protocol; never timestamps, probe errors, memory, request ids or counters). No Ollama-specific
logic was added; a provider reload serving the same artifact is the same identity.

- `kriya/core/qualification_identity.py` (new, ~130 lines): `QualificationIdentityGuard` owns the baseline, the
  observations (`IdentityObservation`: index, boundary, capability, digest, exact, observed_at, probe_errors), the
  one comparison, and the sticky decision (`IdentityChange`): once another identity was observed, returning to the
  baseline does not restore trust and nothing is observed again.
- `run_qualification(..., identity_observer=None)`: the observer defaults to a fresh probe of the same binding; the
  single call site `_require_stable_identity` raises `QualificationArtifactChangedError` (reason prefix
  `ARTIFACT_CHANGED`, in the existing `QualificationError` family next to `RUNTIME_NOT_EXACT` and
  `QUALIFICATION_IDENTITY_UNVERIFIED`). A case's result is attributed only after its post-case observation matched;
  a case whose identity changed underneath it is dropped from the evidence. The exception carries `diagnostic`
  (status, model, the full identity evidence).
- `kriya model qualify`: prints the refusal and the diagnostic JSON to stderr, writes the diagnostic to `--out` when
  given, exits 1. Nothing is written to the qualification store.
- Records: additive field `identity_stability` (baseline digest, stable, every observation, cases completed, change)
  sealed by `build_record`; `record_is_current` refuses a record whose identity evidence does not say `stable: true`
  (defence in depth; Kriya never writes one). Records without the field (every historical record) are judged exactly
  as before: no schema or policy version bump.

## Verification

| Gate | Result |
|---|---|
| `tests/test_qualification_artifact_stability_001.py` (14): stable run; change before the first case, between cases, during a case, before the seal; reload with the same identity; volatile metadata; A->B->A sticky; aborted run cannot satisfy `assess` (CLI exit 1, `--out` diagnostic, empty store, MISSING for both artifacts); diagnostic/unstable record in the store refused; historical records unchanged; the incident shape; negative control; unknown boundary | 14/14 |
| Original symptom re-measured (reproducer): PASS; negative control proves the reproducer fails without the guard | PASS |
| Mutations applied to production code one at a time: start / before-case / after-case / close check removed (M1-M4), sticky decision removed (M5), comparison inverted (M6), `record_is_current` stability check removed (M7), verdict reported before the post-case check (M8) | 8/8 killed (M4: 3 tests, M5/M7/M8: 1 test each, M1-M3: 7, M6: 11) |
| Adjacent: qualification, runtime identity, provider contract, fallback window, INF-001 parity/port, network inventory | 425/425 |
| ruff / pylint | 0 / 0 |
| Full Python suite (`-n 8 --dist loadgroup`, default live exclusions) at the branch tip | batch report |

## Not in scope (recorded, unchanged)

- Exact macOS version and the Ollama 0.40 runner type (llamacpp/mlx) are informational evidence, not components of
  the qualification identity (the `kriya-m1-live` requalification report calls this QUALIFICATION-IDENTITY-GAP,
  PARTIAL). Closing artifact stability did not require a fingerprint redesign.
- No operator-visible structured event was added: the GUI/KUP surface (`ui/**`, `kriya/kup/**`, serializer
  contracts, host environment) is untouched by this fix.
- Subsystem note: `handover/claude-context/model-runtime-and-inference.md` lives untracked in the owner's canonical
  checkout and was not edited; the paragraph to add under "Qualification (PRD-014)" is the "Invariant and design"
  section above.
