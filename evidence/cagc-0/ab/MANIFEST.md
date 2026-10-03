# CAGC-0 whole-feature A/B manifest (KRIYA_CAGC v0.7 §13.1, §13.1.1)

Prepared 2026-10-03 12:02 IST. Status: **awaiting SEC-009 owner approval** (0 of 20 arm workspaces approved).

| Arm | Kriya revision | tree | venv |
|---|---|---|---|
| A | 0bea22f800553374dc3507a951d83cbe4366a0f6 | e6f5c4c493f4b251a315cdec6282e8fc97c5eeac | /Users/sriramnanduri/kriya-cagc-ab/A/venv (installed, build_provenance embedded, clean) |
| B | b7cd737f4e218a91d43f7a43e718426413a93626 | d3d43eadd48c550db371bfcf80f2d1f5ee71e214 | /Users/sriramnanduri/kriya-cagc-ab/B/venv (installed, build_provenance embedded, clean) |

Arm A = CAGC branch base (origin/main 0bea22f). Arm B = feature/cagc-r1 code head b7cd737.

## Per-arm roots (isolated)

- **A**: KRIYA_STATE_DIR=/Users/sriramnanduri/kriya-cagc-ab/A/state (run history, context-recall certification, Maven caches), KRIYA_LOG_DIR=/Users/sriramnanduri/kriya-cagc-ab/A/logs, KRIYA_AUTHORITY_HOME=/Users/sriramnanduri/kriya-cagc-ab/A/authority, KRIYA_STATIC_ANALYSIS_HOME=/Users/sriramnanduri/kriya-cagc-ab/A/static-analysis, KRIYA_MCP_APPROVAL_HOME=/Users/sriramnanduri/kriya-cagc-ab/A/mcp-approvals, paths.memory=/Users/sriramnanduri/kriya-cagc-ab/A/memory/<task>, paths.skills=/Users/sriramnanduri/kriya-cagc-ab/A/skills (copy of the operator skills); env: /Users/sriramnanduri/kriya-cagc-ab/A/env.sh
- **B**: KRIYA_STATE_DIR=/Users/sriramnanduri/kriya-cagc-ab/B/state (run history, context-recall certification, Maven caches), KRIYA_LOG_DIR=/Users/sriramnanduri/kriya-cagc-ab/B/logs, KRIYA_AUTHORITY_HOME=/Users/sriramnanduri/kriya-cagc-ab/B/authority, KRIYA_STATIC_ANALYSIS_HOME=/Users/sriramnanduri/kriya-cagc-ab/B/static-analysis, KRIYA_MCP_APPROVAL_HOME=/Users/sriramnanduri/kriya-cagc-ab/B/mcp-approvals, paths.memory=/Users/sriramnanduri/kriya-cagc-ab/B/memory/<task>, paths.skills=/Users/sriramnanduri/kriya-cagc-ab/B/skills (copy of the operator skills); env: /Users/sriramnanduri/kriya-cagc-ab/B/env.sh

## Shared read-only

- KRIYA_QUALIFICATION_HOME = /Users/sriramnanduri/.kriya/qualifications (default; 19 record files, content digest 8b2a7b92bfea44fd at preparation).
- Ollama and its pinned models; operator template ~/.kriya/operator/provider-contract-v5-production.yaml (sha256 eec4072a4f1509d2c5d5a2b3a766285dea938691d259354dd4ca13983fa53bdd); every arm config differs from it in exactly the 3 paths.* lines.
- model_runtime fingerprint: requires an approved config to read; recorded by each arm before its first run, and the comparison is void if A and B differ.

## Workspaces and configs (20 approvals needed)

| Arm | Task | Base | Workspace | Config | Config sha256 |
|---|---|---|---|---|---|
| A | java-behavior-accents | 49d2835c987a | /Users/sriramnanduri/kriya-cagc-ab/A/ws/java-behavior-accents | /Users/sriramnanduri/kriya-cagc-ab/A/config/java-behavior-accents.yaml | af069091c770b1ec2986e9c1ca7aa91e9d0010446b72129621503f61793beeb5 |
| A | java-symbol-chop | 5cba51c7e97b | /Users/sriramnanduri/kriya-cagc-ab/A/ws/java-symbol-chop | /Users/sriramnanduri/kriya-cagc-ab/A/config/java-symbol-chop.yaml | a194ead3252643bbbdeb069a0866e7cd564b480925c9a7d4cb15763d7064546a |
| A | java-symbol-fraction | 5cba51c7e97b | /Users/sriramnanduri/kriya-cagc-ab/A/ws/java-symbol-fraction | /Users/sriramnanduri/kriya-cagc-ab/A/config/java-symbol-fraction.yaml | a29e6bba33ebcd96c70183b7b16e5287a93ae789c5070fcd2c0566c0afb0dea8 |
| A | python-behavior-maxsplit | f8e972e9a5f2 | /Users/sriramnanduri/kriya-cagc-ab/A/ws/python-behavior-maxsplit | /Users/sriramnanduri/kriya-cagc-ab/A/config/python-behavior-maxsplit.yaml | c37367f3830c9cdf872045a850b3d0c226a7a4b43a1bbaa812b44053f546b024 |
| A | python-error-invalidurl | 3de191518046 | /Users/sriramnanduri/kriya-cagc-ab/A/ws/python-error-invalidurl | /Users/sriramnanduri/kriya-cagc-ab/A/config/python-error-invalidurl.yaml | 647df4c73855434284db43b4370bcb5d6506e73db878e96d85cd8e6623883e76 |
| A | python-symbol-ichunked | b6bb1eb19add | /Users/sriramnanduri/kriya-cagc-ab/A/ws/python-symbol-ichunked | /Users/sriramnanduri/kriya-cagc-ab/A/config/python-symbol-ichunked.yaml | c9f703a2eec9a7ac5c32def417e39538e8dbfb553893fc486ad35b29ca45add9 |
| A | python-symbol-one | 0054e02c381f | /Users/sriramnanduri/kriya-cagc-ab/A/ws/python-symbol-one | /Users/sriramnanduri/kriya-cagc-ab/A/config/python-symbol-one.yaml | 9b76c69238ae9d3cf8d29bf1a4f944d278fce3cfc2481251ba95a4d972a94396 |
| A | python-symbol-zipb | b0004939d7b9 | /Users/sriramnanduri/kriya-cagc-ab/A/ws/python-symbol-zipb | /Users/sriramnanduri/kriya-cagc-ab/A/config/python-symbol-zipb.yaml | cbf9b5f33eb7bab5cd6678ccdedd0aedc1d2ed290dad98991cbe9eb7d085af8b |
| A | spring-boot-pagesize | 500158f73241 | /Users/sriramnanduri/kriya-cagc-ab/A/ws/spring-boot-pagesize | /Users/sriramnanduri/kriya-cagc-ab/A/config/spring-boot-pagesize.yaml | debdd32f2c0445a145909a83224c4b426d984b86bba1eefe13b931274f66b475 |
| A | spring-xml-pettypes-cache | 09351b3ee0bd | /Users/sriramnanduri/kriya-cagc-ab/A/ws/spring-xml-pettypes-cache | /Users/sriramnanduri/kriya-cagc-ab/A/config/spring-xml-pettypes-cache.yaml | d8453721d3d09eecbe69492b4e2e75b2f2473fdedd3372b50195f9d55435cee2 |
| B | java-behavior-accents | 49d2835c987a | /Users/sriramnanduri/kriya-cagc-ab/B/ws/java-behavior-accents | /Users/sriramnanduri/kriya-cagc-ab/B/config/java-behavior-accents.yaml | 58c7f08f7da70558213807c28379ae760e6b3db2c9951e963780c393abddf2e5 |
| B | java-symbol-chop | 5cba51c7e97b | /Users/sriramnanduri/kriya-cagc-ab/B/ws/java-symbol-chop | /Users/sriramnanduri/kriya-cagc-ab/B/config/java-symbol-chop.yaml | 79e36194f0c760b596f380f89e7c821472556c6d0dd07542a3c1cd85d6e768d1 |
| B | java-symbol-fraction | 5cba51c7e97b | /Users/sriramnanduri/kriya-cagc-ab/B/ws/java-symbol-fraction | /Users/sriramnanduri/kriya-cagc-ab/B/config/java-symbol-fraction.yaml | 06053f0aabcf5cc3065f7a641bd8e7d4c6ffc61fc138f01ddc79a7ef25a79099 |
| B | python-behavior-maxsplit | f8e972e9a5f2 | /Users/sriramnanduri/kriya-cagc-ab/B/ws/python-behavior-maxsplit | /Users/sriramnanduri/kriya-cagc-ab/B/config/python-behavior-maxsplit.yaml | 8b86d953aea5b5f334d274b08c4364cc3efbd72e0a44d71b3b3b808dbfa20546 |
| B | python-error-invalidurl | 3de191518046 | /Users/sriramnanduri/kriya-cagc-ab/B/ws/python-error-invalidurl | /Users/sriramnanduri/kriya-cagc-ab/B/config/python-error-invalidurl.yaml | 83ead0c13c1d6d284294ee5806d99aa2bcd562fa02947daff6caf313abbc65be |
| B | python-symbol-ichunked | b6bb1eb19add | /Users/sriramnanduri/kriya-cagc-ab/B/ws/python-symbol-ichunked | /Users/sriramnanduri/kriya-cagc-ab/B/config/python-symbol-ichunked.yaml | a09c78216cc5b2cfb760da28c79e670c0b6a652011caae294570dbcda41534a9 |
| B | python-symbol-one | 0054e02c381f | /Users/sriramnanduri/kriya-cagc-ab/B/ws/python-symbol-one | /Users/sriramnanduri/kriya-cagc-ab/B/config/python-symbol-one.yaml | 9230b6aa8da7e866f4b4d555e58ad8b8a804f964610c3defa72cb21754145b33 |
| B | python-symbol-zipb | b0004939d7b9 | /Users/sriramnanduri/kriya-cagc-ab/B/ws/python-symbol-zipb | /Users/sriramnanduri/kriya-cagc-ab/B/config/python-symbol-zipb.yaml | fb35ce5b0087a8ec020e202737b54661058ef38bc15280b5de731f90fa5e5b2d |
| B | spring-boot-pagesize | 500158f73241 | /Users/sriramnanduri/kriya-cagc-ab/B/ws/spring-boot-pagesize | /Users/sriramnanduri/kriya-cagc-ab/B/config/spring-boot-pagesize.yaml | 9048f53a6042500a7eb885e32e7d87077a8c31ef0eca1b66bc48fb1e923ba564 |
| B | spring-xml-pettypes-cache | 09351b3ee0bd | /Users/sriramnanduri/kriya-cagc-ab/B/ws/spring-xml-pettypes-cache | /Users/sriramnanduri/kriya-cagc-ab/B/config/spring-xml-pettypes-cache.yaml | 467256f9ce3dd4980a3ac151955641a26f83b6f67725759abe2e5433ec49eb3e |

## Owner action

Review one diff per arm (only paths.* differ), then run `/Users/sriramnanduri/kriya-cagc-ab/approve_all.sh` (each arm workspace's
`kriya authority approve --confirm` under that arm's own KRIYA_AUTHORITY_HOME). Nothing is copied or synthesized.

## Run plan after approval

Per task, in order A, B, B, A (`/Users/sriramnanduri/kriya-cagc-ab/run_ab.sh <arm> <task> <label>`); each replicate starts from the task base.
Tasks: commons-lang (java-symbol-fraction, java-behavior-accents, java-symbol-chop), spring-petclinic (spring-boot-pagesize),
spring-framework-petclinic (spring-xml-pettypes-cache, held-out PetTypesCacheJudgeTests), more-itertools (python-behavior-maxsplit,
python-symbol-one, python-symbol-ichunked, python-symbol-zipb), httpx (python-error-invalidurl). Maven caches: one snapshot per
task copied identically into both arms (commons-lang tasks share the project's). Python project venvs are created per workspace by each arm.
