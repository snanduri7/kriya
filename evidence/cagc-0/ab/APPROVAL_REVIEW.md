# CAGC-0 A/B: configuration reviewed for SEC-009 approval

Verified 2026-10-03 12:13 IST before any approval (nothing approved, no authority record copied).

- 20 workspaces = 10 tasks x 2 arms; every workspace clean, no remote, HEAD = its task base = the bench matrix task HEAD.
- Arm A: installed kriya commit 0bea22f, tree e6f5c4c, dirty False. Arm B: code commit b7cd737, tree d3d43ea, dirty False. Evidence head 0dcbeeb.
- Every config differs from v5 (sha256 eec4072a4f1509d2c5d5a2b3a766285dea938691d259354dd4ca13983fa53bdd) in exactly paths.skills / paths.state / paths.memory, all inside its own arm.
- Arm-local: KRIYA_STATE_DIR (run history, context-recall certification, Maven caches), KRIYA_LOG_DIR, paths.memory (per task), KRIYA_AUTHORITY_HOME, KRIYA_STATIC_ANALYSIS_HOME, KRIYA_MCP_APPROVAL_HOME (MCP not configured in v5: not applicable). Arm state holds only the seeded Maven caches; no certification, index, authority or log record exists yet.
- Repository/index-bound certification: context-recall certification lives under each arm's KRIYA_STATE_DIR; none exists in either arm.
- Shared read-only: KRIYA_QUALIFICATION_HOME = /Users/sriramnanduri/.kriya/qualifications (content digest 8b2a7b92bfea44fd); static-analysis rule pack ~/.kriya/operator/prd036/semgrep-rules/java-security.yml and the pinned semgrep image (from v5).
- Model identity (Ollama 0.34.4, /api/tags digests): Developer/primary qwen3-coder:30b-kriya-e52213655394 141e95d786b871df; Planner qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a b2e941213e244ca5; embedding nomic-embed-text:latest 0a109f422b47e3a3. Kriya's model_runtime fingerprint needs an approved config: recorded per arm before the first run; any A/B difference voids the comparison.

## Config digests (sha256)

    af069091c770b1ec2986e9c1ca7aa91e9d0010446b72129621503f61793beeb5  A/config/java-behavior-accents.yaml
    a194ead3252643bbbdeb069a0866e7cd564b480925c9a7d4cb15763d7064546a  A/config/java-symbol-chop.yaml
    a29e6bba33ebcd96c70183b7b16e5287a93ae789c5070fcd2c0566c0afb0dea8  A/config/java-symbol-fraction.yaml
    c37367f3830c9cdf872045a850b3d0c226a7a4b43a1bbaa812b44053f546b024  A/config/python-behavior-maxsplit.yaml
    647df4c73855434284db43b4370bcb5d6506e73db878e96d85cd8e6623883e76  A/config/python-error-invalidurl.yaml
    c9f703a2eec9a7ac5c32def417e39538e8dbfb553893fc486ad35b29ca45add9  A/config/python-symbol-ichunked.yaml
    9b76c69238ae9d3cf8d29bf1a4f944d278fce3cfc2481251ba95a4d972a94396  A/config/python-symbol-one.yaml
    cbf9b5f33eb7bab5cd6678ccdedd0aedc1d2ed290dad98991cbe9eb7d085af8b  A/config/python-symbol-zipb.yaml
    debdd32f2c0445a145909a83224c4b426d984b86bba1eefe13b931274f66b475  A/config/spring-boot-pagesize.yaml
    d8453721d3d09eecbe69492b4e2e75b2f2473fdedd3372b50195f9d55435cee2  A/config/spring-xml-pettypes-cache.yaml
    58c7f08f7da70558213807c28379ae760e6b3db2c9951e963780c393abddf2e5  B/config/java-behavior-accents.yaml
    79e36194f0c760b596f380f89e7c821472556c6d0dd07542a3c1cd85d6e768d1  B/config/java-symbol-chop.yaml
    06053f0aabcf5cc3065f7a641bd8e7d4c6ffc61fc138f01ddc79a7ef25a79099  B/config/java-symbol-fraction.yaml
    8b86d953aea5b5f334d274b08c4364cc3efbd72e0a44d71b3b3b808dbfa20546  B/config/python-behavior-maxsplit.yaml
    83ead0c13c1d6d284294ee5806d99aa2bcd562fa02947daff6caf313abbc65be  B/config/python-error-invalidurl.yaml
    a09c78216cc5b2cfb760da28c79e670c0b6a652011caae294570dbcda41534a9  B/config/python-symbol-ichunked.yaml
    9230b6aa8da7e866f4b4d555e58ad8b8a804f964610c3defa72cb21754145b33  B/config/python-symbol-one.yaml
    fb35ce5b0087a8ec020e202737b54661058ef38bc15280b5de731f90fa5e5b2d  B/config/python-symbol-zipb.yaml
    9048f53a6042500a7eb885e32e7d87077a8c31ef0eca1b66bc48fb1e923ba564  B/config/spring-boot-pagesize.yaml
    467256f9ce3dd4980a3ac151955641a26f83b6f67725759abe2e5433ec49eb3e  B/config/spring-xml-pettypes-cache.yaml

## The diff, per arm (every task of an arm differs only in the memory leaf)

Arm A, spring-xml-pettypes-cache:

    23,25c23,25
    <   skills: /Users/sriramnanduri/.kriya/operator/provider-contract-v3/skills
    <   state: /Users/sriramnanduri/.kriya/operator/provider-contract-v3/state
    <   memory: /Users/sriramnanduri/.kriya/operator/provider-contract-v3/memory
    ---
    >   skills: /Users/sriramnanduri/kriya-cagc-ab/A/skills
    >   state: /Users/sriramnanduri/kriya-cagc-ab/A/state
    >   memory: /Users/sriramnanduri/kriya-cagc-ab/A/memory/spring-xml-pettypes-cache

Arm B, spring-xml-pettypes-cache:

    23,25c23,25
    <   skills: /Users/sriramnanduri/.kriya/operator/provider-contract-v3/skills
    <   state: /Users/sriramnanduri/.kriya/operator/provider-contract-v3/state
    <   memory: /Users/sriramnanduri/.kriya/operator/provider-contract-v3/memory
    ---
    >   skills: /Users/sriramnanduri/kriya-cagc-ab/B/skills
    >   state: /Users/sriramnanduri/kriya-cagc-ab/B/state
    >   memory: /Users/sriramnanduri/kriya-cagc-ab/B/memory/spring-xml-pettypes-cache
