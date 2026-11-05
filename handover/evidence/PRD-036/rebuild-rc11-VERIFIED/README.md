# PRD-036 on the rebuilt baseline - candidate `prd-036-rc11` - gate VERIFIED

Executable candidate: commit `b32976245f84018fa732072012bc302d4349b7ac` (tree `48f9343acd5b5d95ca2c3d25365a06f322b5aa18`, `kriya/` `850b2a17...`, `plugins/` `2e970496...`), tag `prd-036-rc11` (annotated, object `567f5e7e...`), branch `arch/containment-rebuild-v2`.
Candidate digest `85d18ecdac428a7eb1e319e01987fd52fd1d7d432b723919616dce0e585fac3c` (release identity + operator config content/security-set/rule-pack digests + every role's model identity + environment + case set + canary authority `1788f26e76de402b97d711743093acd87ba4eb52faacdf261fac39b357d2c644`).
This supersedes the earlier rc9 line recorded in `evidence/PRD-036/` (candidate `edce0229...`, pre-rebuild baseline). That evidence is unchanged and is NOT evidence for this revision.

## Sequence (all MEASURED from the files here)
1. **Canary attempt 2** (attempt 1 FAILED `VERIFICATION_AUTHORITY_REQUIRED`; its raw evidence is kept privately, unchanged): `canary/canary.json` verdict PASS, 22 checks x 2 runs, none failed; runs `20261105T135015-e7a4e284`, `20261105T141517-7d8243e8`; `canary-authority-check.json` and `release-candidate-check.json` both CURRENT. Raw run directories are fixture-derived and stay under `~/kriya-private/prd036-rc10/` (never committed).
2. **Hosted CI at the tag** (one dispatch): run 42000651013, head `b329762`, every job success except the two live-model jobs skipped by design (`hosted/run-42000651013.json`). Summary `HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED`, `problems: []`, pytest 20201 passed / 3 skipped / 0 failed (`hosted/certification-summary.json`, sha256 `182dde36...`).
3. **Private F9b gate** (run once, clean tree at b329762): PASS, evidence digest `ef50aa3af2c4635c1dfccdc6054ca1a78800525f3501a2a8aed5663512efe0ad`; `verify` -> `CERTIFIED`, `problems: []`, hosted_revision b329762 (`f9b/final.json`). Digest-only; no fixture content.
4. **Release streak 3/3** on the unchanged candidate (`trials/trial-{1,2,3}`, `candidate/streak-85d18ecd.json`): each a complete 11/11 (32m16s, 28m56s, 27m05s), each `BOUND_CERTIFIED` by `scripts/verify_model_certification.py`, each preceded by a CURRENT candidate check. Report digests `3af3ea4d...`, `313036ca...`, `2d7fc260...`.
5. **Final gate** `scripts/prd036_gate.py` -> `VERIFIED` (`gate-rc11.json`): 16/16 criteria pass (canary, canary_authority_bound, deterministic_certificate_final, certificate bound to revision/tree/config, private_gate_bound_to_candidate, hosted_mandatory_tiers_passed, scanner_execution_proved, release_stage_hosted, production_doctor_ready, live_streak, hosted_ci_at_candidate, no_open_p0_p1, evidence_only_since_candidate, tracked_tree_clean).

## Not claimed
- `refs/kriya/verified` was at `7eddc5d6e255...` when this was written; it has NOT been moved. Moving it to `b329762` needs the owner's explicit compare-and-swap approval.
- The doctor stage inside the hosted run reports `profile.production` failed under `packaged_defaults` mode (recorded, not gating; unchanged behaviour). The production doctor on the release configuration was PRODUCTION_READY before and after each canary run (gate criterion `production_doctor_ready`).
- The certification record key does not name the Kriya revision (accepted by owner ruling); the revision-bound verdict is each trial's `revision-binding.json`.
- Published-history exposure of fixture-derived evidence from the earlier line remains an owner disposition item (registry CERT-HISTORICAL-FIXTURE-EVIDENCE-EXPOSURE-001).
