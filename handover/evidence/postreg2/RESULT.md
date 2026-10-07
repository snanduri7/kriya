# POST-REG-R2 GRAPHIFY PRIMARY-PROFILE COMPARISON

Kriya **56ae8d3** (REG-R2 implementation; certified by e815cdd; clean non-editable install `venv-postreg2`, build info
dirty=false, dependencies identical to venv-gr1 and venv-postreg). Graphify base 67f99bd (tree 975f0667, content
bba7689c, maintainer fix absent). Frozen goal / contract / acceptance / B3 / auto-skill b4a91e1a (auto-skill model
calls 0). One fresh run per arm, serially, Arm A first, frozen and sealed before Arm B; no setting changed between
arms; the earlier POST-REG-R1 Arm-A run 20261007T071630-e21ca9b2 was neither reused nor resumed.

Pre-run (both arms, MEASURED): fresh workspaces; configs = sources (A 4ebd97b4 / B 7e595207) with only the three
paths lines changed; read-only qualification views byte-identical to the POST-REG-R1 views; `kriya model status`
QUALIFIED for every role identity, fingerprints and settings digests identical to POST-REG-R1 (qualification reused,
not re-run); per-arm SEC-009 approvals (A 5f90f8d7, B c404ee53), field sets identical to POST-REG-R1 apart from paths;
REG-R1/R2 smoke NO REGRESSION (217 PRE_EXISTING, 2 replays each, arm workspaces untouched); pre-run check 28/28 MATCH
for each arm. Indexing: 422 files indexed, 1 EMBEDDING_INPUT_TOO_LONG (analyze exit 1) in both arms, identical to
POST-REG-R1 - a shared pre-existing condition.

| | Arm A - qwen3-coder (canonical) | Arm B - qwen3.8 (matched) |
|---|---|---|
| Run id | 20261007T091832-d5607f08 | 20261007T093122-df65c5d3 |
| Profile | qwen3-coder:30b-kriya-e52213655394 (fp 0769fe62), ollama_native, T 0.7, reasoning off, ctx 32768; fallback qwen3.6; Planner qwen3.6 | Developer qwen3.8:27b (fp b9acd699, matched settings); other roles qwen3-coder explicit_agent_llms (fp 375715b7); fallback qwen3.6; Planner qwen3.6 |
| Terminal | FAILURE - `context_edit_protocol_unsatisfiable` (stop_environment, NO_PROGRESS), s1, nothing applied | **SUCCESS** - s1 and s2 quality gates passed, COMMITTED (applied to the workspace) |
| Wall | 512 s (+211 s analyze) | 1196 s (+210 s analyze) |
| Attempts | 9 | 2 |
| Model calls | 10: Developer 7 (qwen3-coder 6, fallback qwen3.6 1), planning 1, localization 1, review 1 | 13: Developer 2 (qwen3.8), run verifier 2, spec compliance 2, reviewer 2, planning 2, localization 2, requirement verification 1 |
| Developer calls / fallback calls | 7 / 1 | 2 / 0 |
| Candidate changes | 7: 2 STAGED, 5 REFUSED (4 ANCHOR_NOT_IN_FILE, 1 diagnosis_mismatch) | 2 STAGED (engine.py; tests/test_csharp_call_site_generic_args.py), 0 refused |
| Best staged candidate (external) | 0/5 (both 0/5, acceptance 0/5, regression 76/76) | 5/5 (acceptance 5/5, regression 76/76) |
| Final external | 2/5 (= untouched base) | **5/5** (A-E PASS), ACCEPTANCE PASS |
| Regression 76 | 76/76 | 76/76 |
| Full-regression gate | never reached | 2 decisions, both non-blocking: s1 216 PRE_EXISTING + 1 **FLAKY_PREEXISTING** (timing test; flake rate NOT_ASSESSED); s2 216 PRE_EXISTING + 1 RESOLVED_FAILURE (timing test passed) |
| REG-R2 stability replays | 0 | 4 (2 per decision context) |
| B2 / B3 | not reached | acceptance gate PASS (B3 owner approval REQ-1/REQ-3 loaded), original requirements PASS, terminal obligations PASS, static analysis PASS |
| M1 | VERIFIED, sealed | VERIFIED, sealed (161 records) |
| Genuine success | NO | **YES** |

Stop rules: Arm A - Kriya NO_SUCCESS and no frozen candidate at external 5/5 (best 0/5): neither FALSE SUCCESS nor
FALSE NEGATIVE; Arm B ran as planned. Arm B - Kriya SUCCESS with external 5/5: genuine, not a false success.

REG-R2 in production (MEASURED, Arm B s1): the full-regression gate saw the same load-dependent timing test flip
(baseline FAIL, candidate FAIL / AssertionError, same-context replays FAIL then PASS - 217/217/216 failing) and
classified it FLAKY_PREEXISTING, non-blocking - the case that rejected the correct POST-REG-R1 Arm-A candidate.

Comparison (one run per arm; INFERRED beyond the measured rows): on this task, under these frozen profiles, qwen3.8
(Developer) produced a correct fix in 2 attempts and Kriya applied it; qwen3-coder failed this time on edit-protocol
mechanics (anchors not in file, then no satisfiable edit context) and never staged a correct candidate - although its
earlier POST-REG-R1 run did produce a 5/5 candidate. Single samples do not establish reliability or a success-rate
difference. Confidence: LOW.

Repository copy: the two largest Arm-A logs are gzipped here (`*.log.gz`); `frozen/MANIFEST.sha256` digests the
originals kept in ~/kriya-m1-live/evidence/postreg2. Scripts are the POST-REG-R1 scripts with names and revision
changed, the smoke script reading REG-R2 record keys, and the scorer's per-site parse fixed.
