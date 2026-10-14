# MODEL ROUTING V1 - Stage 2: Task Bank Freeze

**TASK BANK = FROZEN** (2026-10-14). 20 tasks (10 Java / 10 Python). 3 CERTIFIED, 17 CERTIFIED-WITH-LIMITS, 0 REJECTED. No included task has EXACT/MATERIAL public-benchmark overlap.

- `BANK_MANIFEST_HASH` = `cbdb68031cc9e1e044c3fb51d1ddf3e7a8a52656d9b81a9849c3013b046d364d`
- `PARTITION_HASH` = `3df2ff4de78d11ca82a7818d3e4a2885af8df6ac344c621687e7e1db5cc6a2a6` (seed `MODEL-ROUTING-V1/stage2/partition/1`)
- `FINAL-HOLDOUT` seal = `0b96dcac8a05753e4b125e6b272de8d4b07edf65145a1dbcdfb78a026ee80dd4`
- Branch `model-routing/v1`, parent HEAD `c0b942d` (Stage 1). Kriya product code: diff against `main` outside `handover/MODEL-ROUTING-V1` is **zero**.

> **NO MODEL QUALITY RESULT WAS USED TO CONSTRUCT, REPLACE, SELECT, CERTIFY OR PARTITION THIS BANK.**
>
> **Public benchmark task overlap was checked. Model training exposure is unknown and is not claimed.**
> Only this is claimed: NO EXACT/MATERIAL OVERLAP WAS FOUND IN THE CHECKED PUBLIC BENCHMARK DATASETS (SWE-rebench, SWE-rebench-V2, SWE-bench-extra, SWE-Gym, SWE-bench, SWE-smith). Other benchmarks (for example SWE-bench-Live, SWE-PolyBench) were not checked.

No model was run on this bank. ROUTING-DEV was not run. FINAL-HOLDOUT is sealed and must not be opened for model-quality testing without explicit owner approval. The first ROUTING-DEV baseline cohort also needs owner approval.

## 1. Items the owner should look at first

1. **Three upstream PRs disclose AI assistance** (policy: retain and disclose): MR-P07 pluggy#758 (a `claude-projects-attribution` marker in the PR text), MR-P10 sqlparse#865 ("authored by an AI coding agent (Claude Code)"), and **MR-P16 pydantic-settings#826 ("Generated with Claude Code")**. MR-P16 is the third such task and was admitted during the final replacement pass under the same policy. Label: **UPSTREAM PR DISCLOSES AI ASSISTANCE**. This is a provenance flag only; it says nothing about any model's behaviour. If the owner prefers an AI-authorship-free bank, MR-P16 is the one to revisit (fallback: parse#223, not certified).
2. MR-P16 was needed because MR-P12 (click#3152) was blocked by independent review (section 6).
3. tomlkit#510 is a strong task that the unchanged certification tool REJECTED for a control-applicability reason; it is NOT in the bank (section 5).

## 2. Final task list

Columns: F2P/required tests, task type, localization, reference churn bucket (S/M/L), difficulty anchor, benchmark overlap class (worst across the six datasets), upstream AI flag, arm, reference accept+regress seconds.

| Task | Upstream | Lang/build | Certification | F2P/req | Type | Loc | Churn | Anchor | Overlap | AI | Arm | Eval s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MR-J01 | JSON-java#1046 | java/maven | CERTIFIED-WITH-LIMITS | 4/68 | bug_fix | multi_file | M | - | POSSIBLE RELATED | - | DEV | 7.2 |
| MR-J02 | commonmark-java#340 | java/maven | CERTIFIED-WITH-LIMITS | 24/24 | feature | multi_file | M | - | NO KNOWN OVERLAP | - | DEV | 10.0 |
| MR-J03 | gson#3098 | java/maven | CERTIFIED-WITH-LIMITS | 9/107 | bug_fix | multi_file | M | - | SAME REPO ONLY | - | DEV | 12.6 |
| MR-J04 | jackson-core#1665 | java/maven | CERTIFIED-WITH-LIMITS | 2/12 | bug_fix | multi_file | L | - | NO KNOWN OVERLAP | - | DEV | 31.5 |
| MR-J05 | javaparser#4727 | java/maven | CERTIFIED-WITH-LIMITS | 1/2 | bug_fix | multi_file | L | - | NO KNOWN OVERLAP | - | HOLDOUT | 37.1 |
| MR-J06 | jdbi#2681 | java/maven | CERTIFIED-WITH-LIMITS | 1/2 | bug_fix | single_file | M | ANCHOR | NO KNOWN OVERLAP | - | HOLDOUT | 28.7 |
| MR-J07 | mapstruct#2975 | java/maven | CERTIFIED-WITH-LIMITS | 1/1 | bug_fix | multi_file | L | - | NO KNOWN OVERLAP | - | HOLDOUT | 135.6 |
| MR-J08 | mybatis-3#3456 | java/maven | CERTIFIED-WITH-LIMITS | 1/2 | bug_fix | single_file | S | ANCHOR | NO KNOWN OVERLAP | - | HOLDOUT | 70.8 |
| MR-J09 | resilience4j#2209 | java/gradle | CERTIFIED-WITH-LIMITS | 1/14 | bug_fix | single_file | S | - | NO KNOWN OVERLAP | - | DEV | 16.3 |
| MR-J10 | resilience4j#2507 | java/gradle | CERTIFIED-WITH-LIMITS | 1/14 | bug_fix | single_file | M | ANCHOR | NO KNOWN OVERLAP | - | HOLDOUT | 9.9 |
| MR-P01 | attrs#1401 | python/python | CERTIFIED-WITH-LIMITS | 2/13 | bug_fix | single_file | L | ANCHOR | POSSIBLE RELATED | - | DEV | 9.6 |
| MR-P06 | mistune#427 | python/python | CERTIFIED | 3/957 | bug_fix | multi_file | S | - | SAME REPO ONLY | - | HOLDOUT | 2.4 |
| MR-P07 | pluggy#758 | python/python | CERTIFIED-WITH-LIMITS | 1/17 | build_config | single_file | S | ANCHOR | SAME REPO ONLY | AI-ASSISTED | DEV | 1.8 |
| MR-P08 | prettytable#429 | python/python | CERTIFIED | 1/205 | bug_fix | single_file | L | - | NO KNOWN OVERLAP | - | HOLDOUT | 1.7 |
| MR-P09 | pydantic-settings#767 | python/python | CERTIFIED-WITH-LIMITS | 1/185 | bug_fix | multi_file | S | - | NO KNOWN OVERLAP | - | HOLDOUT | 2.9 |
| MR-P10 | sqlparse#865 | python/python | CERTIFIED-WITH-LIMITS | 1/88 | bug_fix | single_file | S | ANCHOR | POSSIBLE RELATED | AI-ASSISTED | DEV | 2.5 |
| MR-P13 | marshmallow#2928 | python/python | CERTIFIED-WITH-LIMITS | 6/207 | feature | single_file | M | - | POSSIBLE RELATED | - | HOLDOUT | 2.5 |
| MR-P14 | jsonschema#972 | python/python | CERTIFIED-WITH-LIMITS | 1/33 | bug_fix | multi_file | M | ANCHOR | SAME REPO ONLY | - | DEV | 6.1 |
| MR-P15 | boltons#445 | python/python | CERTIFIED | 2/19 | bug_fix | single_file | M | - | POSSIBLE RELATED | - | DEV | 3.4 |
| MR-P16 | pydantic-settings#826 | python/python | CERTIFIED-WITH-LIMITS | 2/171 | bug_fix | multi_file | M | ANCHOR | NO KNOWN OVERLAP | AI-ASSISTED | HOLDOUT | 3.1 |

Total reference evaluator time over the 20 tasks: 395.7 s (accept + regress, one reference run each). Slowest: MR-J07 mapstruct 135.6 s.

Disclosures about composition: **two Gradle tasks (MR-J09, MR-J10) come from one repository (resilience4j)**; two Python tasks (MR-P09, MR-P16) come from pydantic-settings (9 distinct Python repositories for 10 tasks); **one task is build/config (MR-P07, pluggy packaging metadata)**; the other 19 are bug_fix (17) or feature (2: MR-J02, MR-P13).

## 3. Coverage (recomputed for the final 20)

| task type | Java | Python | DEV | HOLDOUT |
|---|---|---|---|---|
| bug_fix | 9 | 8 | 8 | 9 |
| feature | 1 | 1 | 1 | 1 |
| build_config | 0 | 1 | 1 | 0 |

| localization | Java | Python | DEV | HOLDOUT |
|---|---|---|---|---|
| single_file | 4 | 6 | 5 | 5 |
| multi_file | 6 | 4 | 5 | 5 |

| module structure | Java | Python | DEV | HOLDOUT |
|---|---|---|---|---|
| single_module | 3 | 10 | 7 | 6 |
| multi_module | 7 | 0 | 3 | 4 |

| reference churn | Java | Python | DEV | HOLDOUT |
|---|---|---|---|---|
| S | 2 | 4 | 3 | 3 |
| M | 5 | 4 | 5 | 4 |
| L | 3 | 2 | 2 | 3 |

| build system | Java | Python | DEV | HOLDOUT |
|---|---|---|---|---|
| maven | 8 | 0 | 4 | 4 |
| gradle | 2 | 0 | 1 | 1 |
| python | 0 | 10 | 5 | 5 |

| difficulty anchor | Java | Python | DEV | HOLDOUT |
|---|---|---|---|---|
| True | 3 | 5 | 4 | 4 |
| False | 7 | 5 | 6 | 6 |

| benchmark overlap class | Java | Python | DEV | HOLDOUT |
|---|---|---|---|---|
| NO KNOWN OVERLAP | 8 | 3 | 3 | 8 |
| SAME REPOSITORY ONLY | 1 | 3 | 3 | 1 |
| POSSIBLE RELATED TASK | 1 | 4 | 4 | 1 |

### Difficulty anchors (recomputed, pre-model, structural evidence only)

Anchors: MR-J06, MR-J08, MR-J10, MR-P01, MR-P07, MR-P10 (unchanged pre-model classification from earlier in Stage 2) plus **MR-P14** (reviewer: YES, moderate confidence; two production files, the error carries no type checker so one must be plumbed in or reconstructed, context descent uses max at top level and min inside context) and **MR-P16** (reviewer: YES; three traps confirmed by oracle rejects: the flag must come from the root source config not the sub-model, the field name must be appended not prepended, both config keys are needed and the default must stay off). MR-P13 and MR-P15 were judged NOT anchors. The former MR-P02 anchor label was **not** transferred to any replacement. No model behaviour informed any anchor label.

## 4. Funnel (full history, not rewritten)

Original Stage-2 funnel: **71 prepared / 65 probed / 26 certified / 20 initially selected / 6 certified reserves** (reserves: boltons#416, commons-codec#435, commons-numbers#136, commons-validator#397, humanize#329, python-tabulate#268; reserved for weak substance; none promoted; none was exposure-checked).

Exposure review: the targeted benchmark check (which had not covered SWE-rebench / SWE-rebench-V2 before) found **4 initially selected Python tasks with EXACT/MATERIAL TASK OVERLAP** (same repository, PR number, identical base SHA, identical patch file set; `exact_overlap_verification.json`):

| Removed task | Benchmark row | Dataset |
|---|---|---|
| MR-P02 click#2604 | `pallets__click-2604` | SWE-rebench-V2 |
| MR-P03 jinja#1664 | `pallets__jinja-1664` | SWE-rebench-V2 |
| MR-P04 keyring#695 | `jaraco__keyring-695` | SWE-rebench |
| MR-P05 markdown#1494 | `python-markdown__markdown-1494` | SWE-rebench-V2 |

They are classified `REJECTED_FROM_FINAL_BANK: EXACT_PUBLIC_BENCHMARK_OVERLAP`. This is an experiment-disjointness exclusion, not a certification-quality failure; their certification evidence is preserved (`bank/excluded/`, `frozen-metadata/excluded/`), and they are not reserves for the rest of Model Routing V1. (P03 was one of the three plain-CERTIFIED tasks and P02 a difficulty anchor.) Same-repository-only and possible-related classes are not exclusionary (owner rule).

First replacement pass: **25 metadata-screened -> 10 exact overlaps rejected before cloning -> 15 exposure-clean -> 8 examined -> 3 certified replacements, 1 strong task blocked by control applicability, others rejected for documented reasons.**
Examined: tomlkit#409 (environment: tests need the absent `toml-test` git submodule), tomlkit#510 (see section 5), marshmallow#2928 (CERTIFIED-WITH-LIMITS), click#3152 (CERTIFIED-WITH-LIMITS, later BLOCKED by review), jsonschema#972 (CERTIFIED-WITH-LIMITS), markdown#1272 and markdown#1629 (F2P=0: the upstream tests also pass on the unfixed base), parse#223 (viable, F2P=1, but a one-line fix; judged trivial, not certified).
The 10 exact overlaps rejected before cloning: click#2380, click#2555, click#2730, jinja#1782, jinja#2022, jinja#2026, jinja#2029, markdown#1440, parse#184, markupsafe#418 (SWE-smith only).

Final bounded pass (owner caps: screen <= 6 exposure-clean, inspect <= 4, certify <= 2): **8 metadata-screened -> 2 exact overlaps rejected before cloning (sqlparse#768 SWE-rebench, attrs#1117 SWE-rebench-V2) -> 6 exposure-clean -> 4 locally inspected (boltons#445, pydantic-settings#826, pydantic-settings#648, attrs#1253; the last two are probe-viable but were not certified) -> 2 fully certified (boltons#445 CERTIFIED; pydantic-settings#826 CERTIFIED-WITH-LIMITS), both reviewed and accepted.** The parse#223 fallback was **not** needed. The second certification (pydantic-settings#826) was used because MR-P12 was blocked by independent review after the first final-pass certification (boltons#445) had succeeded; it stayed inside the owner's caps (4 inspected, 2 certified). No candidate outside the screened set was looked at.

Final bank: **20 tasks** = 10 Java (unchanged) + 10 Python (MR-P01, P06, P07, P08, P09, P10, P13, P14, P15, P16). Replacement IDs are new (P13-P16); IDs P02-P05, P11 (tomlkit#510) and P12 (click#3152) are retired and never reused.

## 5. tomlkit#510: STRONG FUTURE CANDIDATE, BLOCKED_BY_CERTIFICATION_CONTROL_APPLICABILITY

Measured: 7 fail-to-pass tests, baseline REJECT x3, reference PASS x3. The authoritative tool verdict was REJECTED because the stock `unsatisfiable_requires_python` control rewrites a PEP-621 `[project]` table; tomlkit uses Poetry (`[tool.poetry.dependencies]`), so the control tree equalled the reference and correctly PASSED. A hand-built Poetry-equivalent control (`python = ">=99"`) was REJECTED by the unchanged evaluator ("candidate package does not build"), so the failure is control applicability, not task quality (diagnostic only; not a certification). Owner decision: **no owner override into the frozen bank; shared `bank_eval.py` not changed during this freeze.** Invariant kept: every included task was accepted by the same declared certification policy and tooling.
Future backlog item (NOT implemented): **TASK-BANK-CONTROL-APPLICABILITY-001** - certification controls such as `unsatisfiable_requires_python` should declare applicability by project/build metadata and support equivalent PEP-621 / Poetry / other packaging forms without changing their purpose.

## 6. Independent reviews

- **Corrected goals J03, J04, J08, P09, P01 and the four passing near-miss tasks** were reviewed earlier (records in `task-bank/reviews/`); owner decisions: J03 option (a) below; the other advisories are accepted limitations.
- **MR-J03 (gson#3098)** - evaluator-authored coverage correction. A fresh reviewer had found that upstream tests do not exercise an explicit goal requirement (promoted numeric keys consumed via nextInt/nextLong/nextDouble must update getPath/getPreviousPath). Owner option (a): ONE evaluator-authored hidden test `JsonReaderPromotedKeyPathTest` (label `EVALUATOR_AUTHORED_BEHAVIORAL_COVERAGE`, reason "UPSTREAM TEST COVERAGE DOES NOT FULLY EXERCISE AN EXPLICIT TASK REQUIREMENT"), registered in `oracle/meta.json` and `oracle/spec.json` (F2P 9, required 107); the goal was **not** narrowed. A fresh reviewer approved it (7/7). J03-only recertification: reference PASS/PASS x3, baseline REJECT x3, harmless PASS/PASS, tamper REJECT, near_miss_0..3 all REJECT (near_miss_2 and near_miss_3 passed before). Of the 14 individual reversed hunks, 10 REJECT and 4 still PASS (three `PEEKED_LONG`-path reversions the reviewer found unreachable for promoted names, and the `skipValue` "<skipped>" reversal that the goal does not require); owner disposition: legitimate alternatives, no further J03 tests. Final J03 state: CERTIFIED-WITH-LIMITS; J03 leak scan and digests recomputed at this freeze.
- **Replacement review, MR-P12..P15** (`task-bank/reviews/replacement_review_P12_P15.md`): **MR-P12 click#3152 BLOCK** (goal sentence "options with is_flag=False and no flag_value must continue to require an argument" is false at base and in the reference when no default is set; a one-token wrong variant `= True` passes accept 537/537 and regress 1313/1313; one-token change of the shape previously refused as python-tabulate#268). Removed; kept as evidence only. **P13 ACCEPT LIMITATION, P14 ACCEPT LIMITATION, P15 ACCEPT.**
- **Replacement review, MR-P16**: **ACCEPT LIMITATION** (192-row behaviour matrix; 9 hand-built wrong solutions). One goal-accuracy fix was applied after review at the reviewer's recommendation: the `SettingsError` sentence now says "(with `cli_exit_on_error=False`)". This amended goal text only; oracle, hidden tests and certification evidence are unchanged.

## 7. Certification limits and accepted limitations

Certification limits recorded by the tool (3 plain CERTIFIED have none):

| Task | Verdict | Limits |
|---|---|---|
| MR-J01 | CERTIFIED-WITH-LIMITS | 6 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J02 | CERTIFIED-WITH-LIMITS | baseline fails by compile/import break: the goal must state the public API (reviewer judgement); 1 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J03 | CERTIFIED-WITH-LIMITS | 4 base tests superseded by the fix and excluded from the regression set; 19 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J04 | CERTIFIED-WITH-LIMITS | 2 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J05 | CERTIFIED-WITH-LIMITS | 33 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J06 | CERTIFIED-WITH-LIMITS | 2 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J07 | CERTIFIED-WITH-LIMITS | 2 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J08 | CERTIFIED-WITH-LIMITS | no near-miss control (fewer than two executable production hunks, or none applied); 19 base tests fail or are skipped in this environment and are outside the regression set |
| MR-J09 | CERTIFIED-WITH-LIMITS | no near-miss control (fewer than two executable production hunks, or none applied) |
| MR-J10 | CERTIFIED-WITH-LIMITS | no near-miss control (fewer than two executable production hunks, or none applied); 1 base tests fail or are skipped in this environment and are outside the regression set |
| MR-P01 | CERTIFIED-WITH-LIMITS | 93 base tests fail or are skipped in this environment and are outside the regression set |
| MR-P06 | CERTIFIED | none |
| MR-P07 | CERTIFIED-WITH-LIMITS | no near-miss control (fewer than two executable production hunks, or none applied) |
| MR-P08 | CERTIFIED | none |
| MR-P09 | CERTIFIED-WITH-LIMITS | 22 base tests fail or are skipped in this environment and are outside the regression set |
| MR-P10 | CERTIFIED-WITH-LIMITS | no near-miss control (fewer than two executable production hunks, or none applied); 2 base tests fail or are skipped in this environment and are outside the regression set |
| MR-P13 | CERTIFIED-WITH-LIMITS | 2 base tests superseded by the fix and excluded from the regression set |
| MR-P14 | CERTIFIED-WITH-LIMITS | 630 base tests fail or are skipped in this environment and are outside the regression set |
| MR-P15 | CERTIFIED | none |
| MR-P16 | CERTIFIED-WITH-LIMITS | 31 base tests fail or are skipped in this environment and are outside the regression set |

Accepted limitations from independent review (owner: no changes, no endless polish):

- **MR-J02**: baseline fails by compile break; the goal states the public API (`omitSingleParagraphP(boolean)`).
- **MR-J03**: residual all-hunk passes (PEEKED_LONG branches; skipValue) as above.
- **MR-J04**: the malformed-byte clause of the goal is broader than the tested path (only the async parser is tested).
- **MR-J05**: the passing near-miss only restores a dead private method.
- **MR-J08**: `<if>` directly under `<choose>` is stricter than upstream.
- **MR-P01**: the fail-to-pass test asserts the full error text including `alias=None` (fragile); the goal's first sentence nearly states the diagnosis.
- **MR-P09**: the GCP companion hunk is not covered (its tests cannot be collected in the pinned environment).
- **MR-J08, J09, J10, MR-P07, MR-P10**: no near-miss control (fewer than two executable production hunks). Excluded environment-failing base tests: see the table (for example attrs 93, jsonschema 630 mostly JSON-Schema-Suite skips, pydantic-settings 22 and 31).
- **MR-P13**: `near_miss_1` PASSES - class A legitimate alternative (reverts only the `require_tld=False` dotless-host hunk, which the goal does not mention); `near_miss_0` is a crash artifact (NameError); the 2 tests labelled "superseded by the fix" are `test_invalid_datetime_deserialization[...]` whose IDs embed the wall-clock time (a mislabel, irrelevant to the goal). Case handling and the require_tld=False IDN case are not enforced.
- **MR-P14**: the goal clause "existing relevance rules keep their precedence" is not enforced (moving the new key first still passes); an `exceptions.py`-only fix also passes (multi-file not enforced); draft-specific type semantics are untested.
- **MR-P15**: none beyond the overlap class (stale comment in an upstream test only).
- **MR-P16**: case-insensitive handling of mixed-case field names, `cli_kebab_case=False` and AliasPath-only fields are untested; 31 base tests fail or are skipped (7 fail on `_field_name_for_signature() missing 'validate_by_alias'` because the venv has pydantic 2.14.1 while `uv.lock` pins 2.12.5, 4 collection errors for missing `pytest_examples` / `pytest_mock`, 20 skips); `near_miss_2` and `near_miss_3` are crash rejects (169/171 fail) while `near_miss_0` and `near_miss_1` are semantic rejects.

## 8. Benchmark exposure (final 20)

Method: Hugging Face datasets-server `/filter` (read-only) against six datasets; match = same repository AND (PR number suffix of the instance id, or base commit prefix, or fix SHA in the benchmark patch); POSSIBLE RELATED = same repository and a shared patch file. Cells: NONE = no known overlap, SAME-REPO = repository present only, RELATED = shared file, no PR/base/fix identity.

| Task | SWE-rebench | SWE-rebench-V2 | SWE-bench-extra | SWE-Gym | SWE-bench | SWE-smith |
|---|---|---|---|---|---|---|
| MR-J01 | NONE | RELATED | NONE | NONE | NONE | NONE |
| MR-J02 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-J03 | NONE | NONE | NONE | NONE | NONE | SAME-REPO |
| MR-J04 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-J05 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-J06 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-J07 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-J08 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-J09 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-J10 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-P01 | NONE | RELATED | NONE | NONE | NONE | NONE |
| MR-P06 | SAME-REPO | NONE | SAME-REPO | NONE | NONE | SAME-REPO |
| MR-P07 | NONE | SAME-REPO | NONE | NONE | NONE | NONE |
| MR-P08 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-P09 | NONE | NONE | NONE | NONE | NONE | NONE |
| MR-P10 | RELATED | NONE | RELATED | NONE | NONE | SAME-REPO |
| MR-P13 | RELATED | RELATED | NONE | NONE | NONE | SAME-REPO |
| MR-P14 | NONE | NONE | NONE | NONE | NONE | SAME-REPO |
| MR-P15 | RELATED | NONE | RELATED | NONE | NONE | SAME-REPO |
| MR-P16 | NONE | NONE | NONE | NONE | NONE | NONE |

**EXACT/MATERIAL TASK OVERLAP in the final bank = 0. UNKNOWN cells in the final matrix = 0** (the earlier MR-P08 x SWE-smith failure was cleared by the re-run). Earlier reliability note: the first exposure pass had many UNKNOWN cells from transient datasets-server errors; retries, exact-repository filters and a resume mode were added and only unresolved cells were re-queried (`overlap.pass1.json` kept). The earlier screening of the original 20 had not covered SWE-rebench or SWE-rebench-V2; that gap caused the four exact overlaps. POSSIBLE RELATED classes (MR-J01, P01, P10, P13, P15) are retained because issue, PR, base and fix identity differ.

## 9. Leak scan and package integrity

- **Solver-visible leak scan** (`leakscan.json`, rerun after the last change): every workspace is a single neutral commit with no remote and no upstream object; no needle (fix revision prefix, reference patch digest, "expected fix is in", "KNOWN_GOOD", "ref_prod", "ref_tree") appears in solver-visible text or in any goal; no goal names a reference file path. The only findings are 20 advisory goal-token matches: common English words (for example "letters", "branches", "alone", "component", "continuation", "modified") or public API / module names that the public issue also contains (for example `funcutils`, `HtmlRenderer`, `JsonReader`, `Jdbi`). None is a solution leak. J03 and the four replacements were scanned in the same run.
- **Package integrity** (`tools/verify_bank.py`, `INTEGRITY.json`): all 20 packages PASS: required files present; every digest in `task.json` recomputed from disk (goal, accept/regress scripts, spec, hidden tests, regression set, meta, reference patches, reference tree, workspace tree); workspace tree equals the candidate base tree; workspace git is one commit, clean, no remote; certification verdict is CERTIFIED or CERTIFIED-WITH-LIMITS and the 3 reference runs were stable; package set equals `final_ids.json`; 10 Java / 10 Python; no REJECTED task. Vendored evaluator tools are byte-identical to the repo tools:

| Tool | sha256 |
|---|---|
| bank_eval.py | 490c1ef35237ceff629148dfc7a05db29981a80970476fc4f78345ea3d3b016d |
| bank_tool.py | 6d12ed105243ec368dcf518cd5f445acd3383f27fd2a527caf985c9ba334845f |
| oracle_preflight.py | 7095ebc306657ead122c057870e157f1a4e95c84ca1f049497a1f01619082055 |

- **Incident (disclosed): stray bytecode.** The first integrity run found `__pycache__` bytecode inside the solver workspaces of MR-P13, P14 and P16 and in the evaluator reference trees of MR-P13 and P14. The most likely source is the independent reviewers' behaviour probes importing from the package trees. Only the `.pyc` files (and then-empty `__pycache__` directories) under those `ws/` trees and the newer-than-certification ones under those two `ref_tree`s were removed (targeted `find -delete`, no `rm -rf`); `finalize`, leak scan and integrity were re-run, and no oracle, hidden-test, spec or cert file was modified after certification (checked by modification time; hidden tests equal their sources except MR-J03's authored test). Old pre-certification bytecode in MR-P08's evaluator `ref_tree` was left as is.

## 10. Manifest, partition, seal

- `frozen/BANK_MANIFEST.json` (per-task table with TASK_ID, REPOSITORY, LANGUAGE, BUILD_SYSTEM, BASE/REFERENCE_REVISION, GOAL/PACKAGE/ACCEPT_ORACLE/REGRESSION_ORACLE/PREFLIGHT/REFERENCE_PATCH digests, CERTIFICATION_VERDICT/LIMITS, PUBLIC_PROVENANCE, DIFFICULTY_ANCHOR, BENCHMARK_OVERLAP_CLASS, AI_ASSISTED_UPSTREAM, EVALUATOR_RUNTIME_SECS; file digests; workspace and reference tree digests; tool and vendored-tool digests): `BANK_MANIFEST_HASH` = `cbdb68031cc9e1e044c3fb51d1ddf3e7a8a52656d9b81a9849c3013b046d364d`; file sha256 `50f3dea9e6ba83ca7eaff391c6023ae183a413aa9a02bbbc7124f58f8db5c827`. `frozen/MANIFEST.json` binds the manifest hash, the partition hash, the sealed FINAL-HOLDOUT section and the integrity / leak-scan file digests. Copies of the manifests and per-task `task.json`, goal, spec, meta and cert are in `task-bank/frozen-metadata/`.
- **Partition** (`freeze_bank.py partition`, only after the manifest existed): algorithm = exact minimiser of the L1 imbalance of feature counts between the two arms, subject to each language splitting 5:5 (all C(10,5) splits per language enumerated and combined), tie-break `sha256(seed | sorted dev ids)`; features = language, build system, task type, localization shape, module structure, churn bucket, difficulty anchor; seed `MODEL-ROUTING-V1/stage2/partition/1`; total L1 imbalance 6; `PARTITION_HASH` = `3df2ff4de78d11ca82a7818d3e4a2885af8df6ac344c621687e7e1db5cc6a2a6`; bound to `BANK_MANIFEST_HASH`. No model behaviour entered the partition (none exists).
  - **ROUTING-DEV (10):** MR-J01, MR-J02, MR-J03, MR-J04, MR-J09, MR-P01, MR-P07, MR-P10, MR-P14, MR-P15
  - **FINAL-HOLDOUT (10, sealed):** MR-J05, MR-J06, MR-J07, MR-J08, MR-J10, MR-P06, MR-P08, MR-P09, MR-P13, MR-P16

| Feature | DEV | HOLDOUT |
|---|---|---|
| anchor=False | 6 | 6 |
| anchor=True | 4 | 4 |
| build=gradle | 1 | 1 |
| build=maven | 4 | 4 |
| build=python | 5 | 5 |
| churn=L | 2 | 3 |
| churn=M | 5 | 4 |
| churn=S | 3 | 3 |
| lang=java | 5 | 5 |
| lang=python | 5 | 5 |
| loc=multi_file | 5 | 5 |
| loc=single_file | 5 | 5 |
| mod=multi_module | 3 | 4 |
| mod=single_module | 7 | 6 |
| type=bug_fix | 8 | 9 |
| type=build_config | 1 | 0 |
| type=feature | 1 | 1 |

- **Seal:** the FINAL-HOLDOUT ids, per-task package digests and goal digests are recorded in `MANIFEST.json` with seal digest `0b96dcac8a05753e4b125e6b272de8d4b07edf65145a1dbcdfb78a026ee80dd4`. FINAL-HOLDOUT must not be run against any model, and its oracle outputs must not be inspected for model quality, until the owner approves the final evaluation phase. Not file-system locked: the seal is by digest and policy.

## 11. Replay mechanics (status)

Complete (Stage 1 and earlier, no model calls): Planner / Developer-attempt / retry messages replay byte-identically on recorded BACKEND-READINESS V2 evidence; CI-6 is identified separately; production parsers and the edit engine reproduce the recorded changes; the V2 oracle runs on scratch copies; sealed V2 evidence is unchanged. The advisory FIX ANALYSIS carry-forward path is **not yet observed** and remains a Stage 3 item. The old V2 twelve tasks are historical only and are not part of this bank.

## 12. Authority incident and cleanup

During Stage 2 a single SEC-009 probe approval was once written to the default `~/.kriya/authority` instead of the campaign authority home. Only that exact file was removed afterwards. The campaign now runs with `KRIYA_AUTHORITY_HOME=~/kriya-m1-live/model-routing-v1/authority-home` and `KRIYA_QUALIFICATION_HOME=.../qual-home`. Verified: nothing created by this campaign remains in the default `~/.kriya/authority` (no file newer than the campaign start except the directory entry of the removal). No Kriya config, prompt, parser, retry budget, gate, qualification or routing code was touched.

## 13. Process notes (disclosed, not hidden)

- A leftover duplicate exposure-screen process from my own first launch overwrote the merged screen file once; the result was rebuilt from the intact first-pass copy (`replacement_screen.pass1.json`, merged copy kept).
- Venv provisioning for the replacement candidates (installing the declared build backend into the existing per-repo venv for marshmallow, jsonschema and boltons; runtime dependencies for jsonschema) was environment provisioning only; no shared tool was changed. The same `bank_tool probe --online` acquisition step is how the original venvs were built.
- A path-specific zsh quirk (a bare `=====` word) caused two harmless command errors; no effect on results.
- Reviewer subagents were instructed to copy trees to a scratch area and be read-only on the bank; the origin of the stray bytecode was not traced to a specific command (INFERRED: a reviewer probe imported from a package tree), so the instruction may not have been fully followed; the effect was checked and reverted as described.

## 14. Cost

Stage-2 active wall time is approximately **12-13 hours**: about 8 h through the first full certification (13 Oct 13:14 - 21:19: discovery and cloning about 1.9 h, preparation, tool development, oracle fixes and review corrections about 5 h, final certification about 1 h), about 1.5 h for the J03 correction, J03 recertification and the first exposure check, and about 3 h today (14 Oct, roughly 06:00 - 09:00) for the replacement passes, the two review rounds and the freeze. Evaluator cost per task is small (see the table); the full 20-task reference sweep is 395.7 s of evaluation. Estimated ROUTING-DEV baseline cohort: about 10 tasks x ~13.6 min of model time per run (about 2.3 h) plus model swaps; evaluator time is under ~3 min per task (mapstruct about 136 s).

## 15. Reproduction pointers

Tools (all in `handover/MODEL-ROUTING-V1/tools/`): `discover_prs.py`, `fetch_candidates.py`, `bank_tool.py` (prep / probe), `bank_eval.py` (regression-set / certify-pkg / evaluate-pkg), `oracle_preflight.py`, `freeze_bank.py` (build / finalize / leakscan / manifest / partition), `verify_bank.py`, `exposure_check.py`, `screen_candidates.py`, `replay_mechanics.py`. Experiment area: `~/kriya-m1-live/model-routing-v1/bank/` (frozen packages, excluded packages, caches, logs; not committed). Owner records in `task-bank/`: `candidate_ledger.json`, `replacement/`, `reviews/`, `evaluator-authored/MR-J03/`, `frozen-metadata/`.

READY FOR BASELINE ROUTING-DEV: **YES, technically** (bank frozen, manifest and partition recorded, no blocker open). **Owner approval is required before the first ROUTING-DEV baseline cohort.** No model has been run.
