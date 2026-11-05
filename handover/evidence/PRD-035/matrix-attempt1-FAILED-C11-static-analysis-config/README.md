# PRD-035 matrix attempt 1 - FAILED 10/11 (C11), preserved failed evidence

Attempt 1 of the single PRD-035 C1-C11 matrix, run by the owner on 2026-11-05 (28 min 37 s) with
`KRIYA_RELEASE_CONFIG=~/.kriya/operator/graphify-qwen3coder-native-production.yaml scripts/certify_model.sh certification-out/prd035-matrix-attempt1`.
It is historical failed evidence and is never overwritten. The attempt count for PRD-035 is now **1**.

## Result (MEASURED, `artifact/model-certification.json`)
Tier `target_production`, identity QUALIFIED and consistent. C1-C10 **PASSED**; C11 **FAILED** (`static_analysis_unavailable`).
Per case wall seconds / fallback transitions: C1 223.5/0, C2 154.9/0, C3 103.6/0, C4 138.7/0, C5 150.1/0, **C6 234.6/1** (the corrected spy observed the Developer
requests and the fallback transition happened), C7 175.9/0 (contained OCI compile+test), C8 157.0/0, C9 70.8/0, C10 108.5/0, C11 70.9/0.

The revision binding worked as designed: `artifact/revision-binding.json` bound the report to commit `69015c6e...`, tree `8a50c054...`, `kriya/` tree
`850b2a17...`, `plugins/` tree `2e970496...`, config sha256 `076e48ac...`, runtime fingerprint `c3df7a69...`, settings `sha256:482067b2...`, environment
`sha256:79b6104a...`, case-set v1. The verifier REFUSED the certification (`report_not_certified_on_the_target_tier`, `not_all_eleven_cases_passed`,
`record_not_certified`) - correctly, because C11 failed. A FAILED record was stored (`artifact/certification-record-FAILED.json`); a later run for the same key replaces
that file, so it is preserved here first.

## Classification (before any change): TEST/HARNESS DEFECT - not a model, provider, environment or product defect
- Observed: C11 stopped at the static-analysis gate with `STATIC_ANALYSIS_UNAVAILABLE ... reason codes CONTAINMENT_UNAVAILABLE`, before any file was committed
  (`commit_results: []`; the hidden acceptance test `1 error` only because nothing was written).
- Producer (TRACED): `SemgrepAdapter._containment_ready()` (`kriya/static_analysis/adapters/semgrep.py`) refuses when `providers.semgrep.image` is unset ("no host
  fallback"). S8 (commit `6492c78`, 2026-10-27) deleted the host provider; Semgrep is OCI-only with a digest-pinned image. C11's config (written 2026-09-27) sets
  only `version` and `rule_packs`, never `image`.
- Discriminating check (MEASURED offline, no model, no container: `analysis/c11_repro.py.txt`, output `analysis/c11_repro.output.txt`): C11 exactly as written ->
  `containment is required but providers.semgrep.image is not set (no host fallback)`; C11 + the pinned image -> READY; adding oci containment explicitly changes
  nothing (the OCI backend is already the default here); oci containment without the image -> still refused. The missing image is the single cause.
- Competing explanations ruled out: Docker unavailable (C7 and the container-based cases passed, the pinned image is present locally); model output (the model's
  compile passed, the failure is before any model-dependent verdict); SEC-009 (the run used the production config, bindings only); the C6/revision-binding changes
  (unrelated code paths).
- Why it was missed: `live_target` cases are excluded from every automated tier, and the PRD-032 chaos tier (E08) was updated for S8 but PRD-035 C11 was not.
- Product code: unaffected. `kriya/` is byte-identical to the PRD-034 certified tree.

## Proposed correction (not yet made; awaiting owner approval)
Harness-only: C11 sets `providers.semgrep.image` to the digest-pinned image already pinned in `tests/certification/pinned_images.txt` and used by the chaos tier,
via a shared helper with an offline control (the old config is refused, the corrected one is READY, a mutation removing the image fails) and a check that the pin
equals `tests/certification/pinned_images.txt`. No assertion, scenario or threshold changes; the case becomes closer to production. A new matrix is then required: the
certification record and verifier need ONE full 11-case report on one commit.
