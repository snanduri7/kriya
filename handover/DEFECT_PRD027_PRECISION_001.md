# PRD027-PRECISION-001: real-embedder certification misses the fixed precision target

**Status: P1, FIXED (Rule A, with the user's tighter fallback rules, 2026-09-27). The fix awaits pytest verification and the real-embedder `context certify`.** The diagnosis below is unchanged; the fix is described at the end.
PRD-027 stays **NOT_VERIFIED** until `kriya context certify` with the real embedder reports `CERTIFIED=true`.

## Observed
The user ran `kriya -c demo-03/config/generate-production.yaml context certify` on 2026-09-27 at Kriya HEAD e34e0ee. The raw output and a copy of the record are in `handover/evidence/BATCH6/prd027-precision/`.
- All 9 recall classes scored 1.0, each meeting its target.
- **Precision was 0.4808, against a fixed target of 0.5. CERTIFIED=false.**
- The Batch 6 live suite recorded the same numbers (`user-live-2` and `user-live-3`, `prd027_certification.json`). That test checks the mechanics only, so it passed; see "Result semantics" below.

Precision is computed over all 6 cases: relevant packaged files divided by all packaged files. That is 25/52. The target needs at least 26/52, so one file decided the result. The CI hashing embedder scores exactly 25/50 = 0.5000. PRD-027's handover had already disclosed that this margin was zero.

## Method
`evidence/.../diag_retrieval.py` re-runs the certification's own path, with the same code and inputs:
- `_index_repository`, then `retrieve_graph_context` with `DEFAULT_RETRIEVAL_LIMITS` (top_k 5, 2 hops, 30 neighbours) and the 10644-token reference budget;
- the real `nomic-embed-text:latest` model.

It also records every stage:
- the vector leg's top 20 chunks with their cosine similarities;
- the lexical leg's top 20 chunks (BM25 order);
- the fused RRF top 5, with each chunk's rank in each leg;
- the seeds;
- the combined neighbourhood (relation, hop, score);
- the neighbourhood reached from each matched file on its own;
- `file_scores`;
- the package, with each file's tier and label;
- the omissions.

It reproduces the live result exactly: 25/52 = 0.4808 with the real embedder (`diag_real_embedder.json`) and 25/50 with the hashing embedder (`diag_hashing_embedder.json`).

## Case: java-shop / junit-upgrade (precision 2/9 = 0.2222)
Goal: "Upgrade the junit-jupiter test dependency in the Maven pom.xml to version 5.10.2."
- **Golden:** `pom.xml` (build_metadata, full). **Acceptable:** `OrderServiceTest.java`.
- **Lexical terms:** upgrade, junit, jupiter, test, dependency, maven, pom, xml, version. The lexical leg matched only 2 chunks: `pom.xml` #1 and `OrderServiceTest.java` #2.

**Fused RRF top 5 (before expansion):**

| rank | chunk | RRF | vector rank (cosine) | lexical rank | label |
|---|---|---|---|---|---|
| 1 | pom.xml#0 | 0.03279 | 1 (0.7825) | 1 | GOLDEN |
| 2 | OrderServiceTest.java#0 | 0.02879 | **19 (0.4875)** | 2 | ACCEPTABLE |
| 3 | Order.java#0 | 0.01613 | 2 (0.5639) | – | FP, vector-only |
| 4 | DiscountPolicy.java#0 | 0.01587 | 3 (0.5377) | – | FP, vector-only |
| 5 | SeasonalDiscountPolicy.java#1 | 0.01562 | 4 (0.5375) | – | FP, vector-only |

The vector leg shows a clear gap: 0.78 for the top hit, then the rest of the corpus packed between 0.48 and 0.56. Chunks 3–5 are noise-level similarity. They are in the top 5 only because `query_hybrid` always fills `top_k` chunk slots, whatever their relevance.

**Expansion.** Seeds are every matched file's symbols plus its own path.
- `pom.xml` reaches nothing.
- **Each** of OrderServiceTest, Order, DiscountPolicy and SeasonalDiscountPolicy reaches the same 8 files on its own: the whole order and pricing cluster.

**Packaged (9, every file at full tier, nothing omitted):**

| file | label | entered via | score after expansion |
|---|---|---|---|
| pom.xml | GOLDEN | retrieval | 0.03279 |
| OrderServiceTest.java | ACCEPTABLE | retrieval (lexical #2) | 0.02879 |
| Order.java | FP | retrieval: embedding similarity, vector-only, rank 3 | 0.01613 |
| DiscountPolicy.java | FP | retrieval: embedding similarity, vector-only, rank 4 | 0.01587 |
| SeasonalDiscountPolicy.java | FP | retrieval: embedding similarity, vector-only, rank 5 | 0.01562 |
| PriceCalculator.java | FP | graph (dependency expansion, `imports`, hop 1) | 1.0 |
| TaxTable.java | FP | graph (dependency expansion, `imports`, hop 1) | 1.0 |
| OrderService.java | FP | graph (caller expansion, `calls computeDiscount`, hop 1) | 0.7 |
| OrderController.java | FP | graph (dependency expansion, `imports`, hop 2) | 0.5 |

The four graph false positives are reached from any one of the four weak seeds, including the acceptable OrderServiceTest.java on its own.

## Case: python-payments / requests-upgrade (precision 1/9 = 0.1111)
Goal: "Raise the minimum requests dependency version in pyproject.toml to 2.32."
- **Golden:** `pyproject.toml` (build_metadata, full). **Acceptable:** none.
- **Lexical terms:** raise, minimum, requests, dependency, version, pyproject, toml. The lexical leg matched only `pyproject.toml#0`.

**Fused RRF top 5 (before expansion):**

| rank | chunk | RRF | vector rank (cosine) | lexical rank | label |
|---|---|---|---|---|---|
| 1 | pyproject.toml#0 | 0.03279 | 1 (0.8033) | 1 | GOLDEN |
| 2 | config/settings.yaml#0 | 0.01613 | 2 (0.6287) | – | FP, vector-only |
| 3 | payments/rates.py#0 | 0.01587 | 3 (0.5968) | – | FP, vector-only |
| 4 | payments/rates.py#1 | 0.01562 | 4 (0.5811) | – | same file as rank 3 |
| 5 | tests/test_stripe_gateway.py#3 | 0.01538 | 5 (0.5739) | – | FP, vector-only |

The gap is the same: 0.80 for the top hit, then 0.63 and below, with the rest of the corpus at 0.54–0.57.

**Expansion.**
- `pyproject.toml` and `settings.yaml` reach nothing.
- `rates.py` reaches `currency.py`.
- `test_stripe_gateway.py` reaches checkout, currency, gateway, paypal_gateway and stripe_gateway.

**Packaged (9, every file at full tier, nothing omitted):**

| file | label | entered via | score after expansion |
|---|---|---|---|
| pyproject.toml | GOLDEN | retrieval | 0.03279 |
| config/settings.yaml | FP | retrieval: embedding similarity, vector-only, rank 2 | 0.01613 |
| payments/rates.py | FP | retrieval: embedding similarity, vector-only, ranks 3–4 | 0.01587 |
| tests/test_stripe_gateway.py | FP | retrieval: embedding similarity, vector-only, rank 5 | 0.01538 |
| payments/currency.py | FP | graph (caller/dependency, `calls`, hop 1), from rates.py and the test file | 0.7 |
| payments/gateway.py | FP | graph (caller expansion, `calls charge`, hop 1), from the test file | 0.7 |
| payments/paypal_gateway.py | FP | graph (caller expansion, `calls charge`, hop 1), from the test file | 0.7 |
| payments/stripe_gateway.py | FP | graph (caller expansion, `calls`, hop 1), from the test file | 0.7 |
| payments/checkout.py | FP | graph (dependency expansion, `imports`, hop 2), from the test file | 0.5 |

## Root cause
Every false positive in both cases traces back to **one weak seed that was treated as a confident one**. There are two ways this happens:

1. **Ranking** (the false positives among the retrieved files: 3 Java, 3 Python).
   - `query_hybrid` returns a fixed `top_k=5` chunks. `retrieve_graph_context` keeps every chunk with `score > 0`, and an RRF score is always above 0.
   - When the lexical leg matches only one or two chunks, the remaining slots go to vector-only chunks at noise-level cosine.
   - With k=60, a single-leg chunk at rank 2 scores 1/62 ≈ 0.0161, about half of a chunk ranked first by both legs (2/61 ≈ 0.0328). There is no floor, so it is still "matched".
2. **Expansion** (the false positives among the graph files: 4 Java, 5 Python).
   - Every matched file, weak or strong, seeds an unconditional 2-hop walk.
   - A related file's score is relation weight divided by hop. It does not depend on how strongly its seed matched the goal.
   - In repositories of this size, 2 hops from any source file covers its whole module.

**Not a cause:**
- **Deduplication.** No file is packaged twice.
  - Duplicate chunks do use up hybrid slots: `rates.py` takes 2 and OrderService takes up to 3 in other cases.
  - Deduplicating to file level would free those slots for more weak vector-only files, so it would **lower** precision. It is not the fix direction.
- **Budgeting.** Nothing is omitted, and every file is at full tier. The 10644-token budget does not bind for these repositories.
- **Global build/config addition.** Build and config files are not added globally. Their graph neighbourhoods are empty (they seed nothing), and they enter only through retrieval. Here `settings.yaml` is a vector-only noise hit.

**Retrieval limits (item 8).** No retrieval limit displaced a relevant item in any of the six cases. The 30-neighbour cap and the budget are both far from binding.

## A separate latent defect found here (not the cause, not folded into the fix)
**The score scales don't match.**
- `file_scores` mixes RRF values (≤ 0.033) for retrieved files with graph scores (0.5–1.0) for expanded files, with no normalization. So every graph-expanded file outranks every retrieved file.
  - In junit-upgrade the golden `pom.xml` scores 0.0328 and PriceCalculator scores 1.0.
- `_build_file_tiers` degrades, and `_omit_over_budget` drops, the **lowest** score first. When the budget binds, the query's own direct hits would therefore be degraded or dropped before their graph neighbours.
- It did not affect this measurement, because the budget does not bind.
- I recommend a separate defect record for it. Fixing it changes the tier and omission order, not precision.

## Candidate fixes (all general; none names a fixture, language or file)
**How the estimates were made.** `simulate_rules.py` replays the recorded data. It is an **estimate**: it takes the union of independent per-seed walks, while production runs one combined BFS with a shared visited set and the 30-result cap. The cap cannot bind in these 14-file repositories. The real numbers come only from running the changed code with both embedders.

**Definition used below.** A hit is *corroborated* if it ranks within `top_k` in **both** the vector and the lexical leg. `top_k` is the existing retrieval limit, not a new constant.

| rule | real embedder | hashing (CI) | recall |
|---|---|---|---|
| current | 25/52 = 0.4808 | 25/50 = 0.5000 | all classes 1.0 |
| **A**: expand the graph only from corroborated seeds; with no corroborated seed, keep today's behaviour. Weak hits are still shown, but don't expand. | 25/43 = **0.5814** | 25/46 = 0.5435 | unchanged (estimated) |
| **B**: when a two-leg hit exists, drop single-leg hits from the match set | 25/44 = 0.5682 | 25/43 = 0.5814 | unchanged (estimated) |
| **A+B** | 25/37 = 0.6757 | 25/43 = 0.5814 | unchanged (estimated) |

Per case under A with the real embedder: junit-upgrade becomes 2/5 and requests-upgrade becomes 1/4. The other four cases are unchanged.

### Risks
- **B:** it drops every vector-only hit whenever the lexical leg matches anything. That neutralizes the embedder for any goal that contains an identifier. A goal worded with synonyms, whose relevant file is found only semantically, would lose that file. **Every certification goal names identifiers, so this fixture cannot detect that loss.** B is not recommended.
- **A:** it has a milder form of the same risk. A relevant seed found only semantically is still shown, but loses its callers and dependencies (direct_caller and one_hop are 1.0-target classes). The fallback (no corroborated seed means expand from all, as today) covers goals with no lexical overlap at all. It does not cover a goal whose lexical leg hits only an unrelated file.
- **Margin:** A leaves the hashing (CI) run at 0.5435. That is above target but thin, and zero margin is what went wrong last time.

### Recommendation
**Rule A**, the smallest change that addresses the actual mechanism: weak seeds no longer expand, and nothing is removed from what retrieval found.
- **Implementation:** `query_hybrid` also reports each hit's per-leg rank (additive fields). `retrieve_graph_context` chooses the graph seeds from the corroborated hits. The hit set and the package assembly stay as they are.
- **Test doubles:** the mocked `query_hybrid` doubles in `tests/test_workflow.py` return hits without leg ranks. Those fall back to today's expand-from-all behaviour, so they are unaffected by design.
- **Tests:**
  - an offline regression test outside the fixture: a lexical leg that hits only an unrelated file while the vector leg finds the real seed. It pins the fallback and the recall behaviour of A.
  - a unit test for the corroboration rule;
  - the CI certification test, which must stay at or above target;
  - mutation checks.
- **Rerun:** the real-embedder `context certify`. The retrieval change changes `index_implementation_digest`, so the old record goes stale automatically. The pre-fix record is kept in the evidence directory.

Decision needed:
- **A** (recommended);
- **A+B** (more margin, with the recall risk above);
- another direction.

Should the score-scale defect be recorded separately (recommended) or fixed together with this one?

## Result semantics (approved, not retrieval logic)
The live test `test_live_prd027_certification_with_the_real_embedder` now records `live_status` and `certification_status` separately. It fails when `certified` is not true. **Until the fix lands, the live suite is correctly red on this case.**

## Closure logistics
- **Doctor never read the record.** It reported NOT_APPLICABLE because `run-production/memory` has no code index. Before the final doctor run, index the demo-03 workspace under the same config (`kriya -c ../../config/generate-production.yaml analyze .`, run from `workspace/repo`). Then `context.recall_certification` must be PASS, read from the new record.
- **qwen3.6 fallback.** The user re-qualifies the exact configured identity with `model qualify --model qwen3.6:35b-a3b-q4_K_M`, under the same config. If it does not qualify, stop and record the failure; the settings are not changed.

## Fix (user decision 2026-09-27: Rule A with tighter fallback rules; Rule B rejected; 0.5 target unchanged)
- **`LocalVectorStore.query_hybrid`.** Adds four annotations to every hit and changes neither the ranking nor the hit set:
  - `vector_rank` and `lexical_rank`: the hit's rank in each leg, or None when that leg did not return it. A vector hit counts only with a positive cosine.
  - `vector_valid_hits` and `lexical_valid_hits`: how many valid hits each leg has in its top_k.
- **`graph_retrieval.select_expansion_seeds`.** A pure function. It decides which files may start the graph walk, and returns a reason code:
  - **Both legs have valid top_k hits:** only hits ranked within top_k by both legs seed (`CORROBORATED_EXPANSION_SEED`). If there are none, nothing seeds (`NO_CORROBORATED_EXPANSION_SEED`). It never falls back to expanding every hit, and no similarity threshold is involved.
  - **Only one leg has valid hits:** that leg's top_k hits seed (`EMBEDDING_ONLY_EXPANSION_SEED` / `LEXICAL_ONLY_EXPANSION_SEED`).
  - **Neither leg has valid hits:** nothing seeds (`NO_VALID_RETRIEVAL_EVIDENCE`).
  - **A hit without leg ranks:** it can't be classified, so nothing seeds (`EXPANSION_SEED_PROVENANCE_UNAVAILABLE`).
  - **In every case,** every hit stays matched and packaged, under the normal ranking and budget.
- **`retrieve_graph_context`.** Seeds the walk from those files only. It exposes `expansion_seed_files` and `expansion_seed_reason`.
- **`run_generation_workflow`.** Records an ADVISORY `retrieval.expansion_seeds` run event with the reason code, the seed files and the matched files.
- **Tests** (`tests/test_prd027_precision_expansion_seeds.py`, 10). They use their own repository: the search index holds 3 files and the dependency graph holds 6, so files reached through the graph are unambiguous. They cover:
  - corroborated seed: its callers and dependencies are reached;
  - weak embedding-only hit while keyword hits exist: kept, but doesn't seed;
  - weak keyword-only hit while embedding hits exist: kept, but doesn't seed;
  - a genuine embedding-only query and a genuine keyword-only query: both still expand;
  - disagreement: hits kept and packaged, nothing seeds, the typed reason recorded;
  - the rule table;
  - the leg ranks `query_hybrid` reports;
  - the run event from a real `run_generation_workflow`, on a successful run and on a human-rejected run.
- **Mutations:** 8, all KILLED:
  - fall back to every hit when the legs disagree;
  - corroboration ignoring top_k;
  - seeding from every matched file;
  - counting a non-positive cosine as a valid vector hit;
  - the embedding-only branch seeding every hit;
  - seeding when leg ranks are missing;
  - the event kind;
  - the reported seed files.
- **Certification measured with the changed code** (`run_certification`, embedding-runtime placeholder, so not a production record). It matches the estimate exactly:

| embedder | precision | classes | per case |
|---|---|---|---|
| real (nomic-embed-text) | **0.5814** (25/43) | all 1.0 | junit-upgrade 2/5, requests-upgrade 1/4, the others unchanged |
| CI hashing | **0.5435** (25/46) | all 1.0 | charge-retry 7/7, requests-upgrade 1/4 |

- **Record identity.** `index_implementation_digest` covers `query_hybrid` and `retrieve_graph_context`, so the earlier 0.4808 record (kept in `evidence/BATCH6/prd027-precision/`) no longer matches the current identity. The user's `context certify` writes the new record.
- **Separate defect.** The score-scale mismatch is recorded as PRD027-SCORE-NORMALIZATION-001 (`handover/DEFECT_PRD027_SCORE_NORMALIZATION_001.md`, OPEN, P2). The Rule A work did not need it for correctness.
- **My bug, fixed in ff6dd3f.** `index_implementation_digest` did not cover the new seed rule (a71dc60). Since 3c1822d it had also missed both legs of the hybrid query (`LocalVectorStore.query`, `query_lexical`, `lexical_query_terms`). It now hashes both legs and the whole `graph_retrieval` module. The test fails for all 4 inputs without the fix.
- **Known limit, disclosed.** Seeds are chosen from the fused top_k hits. RRF can rank an uncorroborated chunk (for example vector rank 1, lexical rank 6) above a corroborated one (rank 5 in both). In a crowded query, a chunk in both legs' top_k can therefore fall outside the fused top_k. The run then records `NO_CORROBORATED_EXPANSION_SEED`, though a corroborated chunk existed outside the results. This fails safe: fewer seeds, and every hit is still packaged. Seeding from a chunk that isn't a search result would contradict "only results corroborated by both modalities may seed", so it is left as is.
