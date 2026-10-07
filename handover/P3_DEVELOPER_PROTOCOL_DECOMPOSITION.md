# P3: Developer/protocol rejections, decomposed (investigation only)

**Branch:** `feature/lr-r1-p3-inv`, from `be34772` (FS-1A/B certified lineage).
**Production code changed:** NO.
**Labels:** MEASURED (from sealed M1 stores or a test run), TRACED (read in code), INFERRED, CONFIRMED.

## 1. Sources and method

- **Primary (MEASURED).** The ten LR-R1 live runs that have M1 attempt-evidence stores: R1 T1–T5 and R2 T1–T5
  (`~/kriya-m1-live/state/attempt-evidence/<run>`, read-only). Each store has raw Developer requests and responses,
  parses, diagnoses, capabilities and retry deltas.
  - Scripts under `handover/evidence/p3/` (read-only over the stores):
    - `p3_census.py`: one row per Developer attempt;
    - `p3_anchor_analysis.py`: each rejected SEARCH checked against the target file and against the exact request
      sent;
    - `p3_stitch_analysis.py`: each rejected SEARCH decomposed into runs of the real file.
  - Outputs: `p3_census.json`, `p3_anchor_analysis.json`, `p3_stitch_analysis.json`.
- **Secondary.** The frozen 80-run census (`~/kriya-cagc-v2/REVIEW/06a_*`).
  - It has counts and message prefixes only; raw prompts and responses were not recorded there, so causes in it are
    UNKNOWN.
  - Both mechanisms behind P3-A (the planned-source section and the mandatory-only authority rule) are present at the
    80-run commit `61a867f` (TRACED: `git merge-base`). A shared cause is therefore possible, but only INFERRED.

## 2. Per-family table (10 M1 runs; 45 Developer attempts; 42 Developer-side model calls)

| Family | Attempts / calls (M1) | Runs | Models | Before model call? | Model saw enough exact context? | Retry added info? | 80-run records / runs | Producer layer | Class |
|---|---|---|---|---|---|---|---|---|---|
| ANCHOR_NOT_IN_FILE | 10 / 10 | 5 | qwen3-coder 10 | no | 6 stitched: yes for each unit, no for the gap between units. 4 altered: yes | 6 PRESENT, 4 n/a (attempt 1). Stitched: retry blocked (next row) | 18 / 16 | model (stitching, altering), induced by context packaging (separate member units) | A (6 stitched are packaging-induced) |
| ANCHOR_CONTEXT_NOT_ESCALATED | 4 / **0** | 2 | none (refused) | **yes** | n/a | **no**: same capability digest | 16 / 8 | retry policy + authority (loci) | **C** |
| ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT | 5 / 5 | 4 | qwen3-coder 4, qwen3.6 1 | no | **4/5: the whole current file was in the request sent**; 1/5 (R2-T5 s2a1) the anchor was never shown | 3 PRESENT | 13 / 12 | authority (decided before the request fit, mandatory text only) | **4 D**, 1 A (correct refusal) |
| prose contamination | 3 / 3 | 1 | qwen3-coder 1, qwen3.6 2 | no | yes | PRESENT, but unwinnable | 0 | parser/check (scans the whole post-edit file) | **D** (B: no candidate can pass) |
| ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED | 1 / 3 | 1 | qwen3.6 3 | no | n/a | n/a | (WHOLE_FILE_AUTHORITY_REJECTED 2 / 2) | Developer file-list step widened the targets beyond the write scope and offered full-file mode | **B** |
| structural corruption | 1 / 1 | 1 | qwen3-coder 1 | no | window cut mid-method ("lines 98-176 [merged]") | PRESENT | — | model, induced by context packaging | A (B-induced) |
| CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE | 1 / 0 (run terminal) | 1 | none | **yes** | no locus for an additive goal (class-level localization only) | n/a | 1 / 1 | authority (no insertion locus source) | **B** |
| INVALID_EDIT_PROTOCOL | 0 in M1 | 0 | — | no | UNKNOWN | — | 19 / 12, mostly "text after the last protocol block" | model (protocol adherence) | A (INFERRED: no raw response recorded) |
| CONFLICTING_DEVELOPER_RESPONSE | 0 in M1 | 0 | — | no | UNKNOWN | — | 4 / 4 | model | A (INFERRED) |

**Totals (MEASURED).** P3 families account for 25 of the 45 attempts (56%) and 22 of the 42 Developer-side calls.

**By class:**
- **Kriya-caused, 13 attempts:**
  - B, C or D: ANCHOR_OUTSIDE 4 D + NOT_ESCALATED 4 C + prose 3 D;
  - B: UNSATISFIABLE 1 + MUTATION_SHAPE 1.
- **Model-caused (class A), 12 attempts:**
  - ANCHOR_NOT_IN_FILE 10, of which 6 are packaging-induced stitches;
  - structural 1;
  - the correct ANCHOR_OUTSIDE refusal 1.
- **Disagreement with the framing (evidence above).** P3 is mostly not "model reliability". In the M1 sample a
  majority of P3 attempts are Kriya-side, and none of the fixes below weakens parsing or authority.

**Notable class-A detail (CONFIRMED).**
- R2-T2 s1 a1 and a4 failed because the model wrote ASCII `'` where the base docstring has U+2019 (`element’s`).
- Model behaviour: not fixed here, and the matcher is not loosened.

## 3. Causal chains (MEASURED, TRACED)

### P3-A: anchor authority ignores text the request really sent
- **Live (R2-T4 s1 a4).**
  - The retry request carried the whole current `ArrayFill.java` verbatim in `=== Planned File Current Source ===`.
  - The model copied a block from it that is real and current.
  - `_authorize_anchors` refused it: ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT.
- **Same shape in three other runs.** R1-T3 s1a2, R2-T2 s1a3 and R2-T5 s2a2, all retries.
- **Why retries only (TRACED).** `_planned_source_context` excludes a target only on attempt 1, as a known target.
- **Producer (TRACED).**
  - `attempt._decide_edit_capabilities` runs before `DeveloperRequestFit` builds the request.
  - It counts only `existing_code_context` minus every optional section, because "an optional section may be
    trimmed".
  - That rule fixed a real bug: the Graphify replay, where the fit cut the section to about 95 tokens
    (`test_only_mandatory_text_counts_as_shown_source`).
  - It over-corrects: a section the fit kept verbatim is real exact source the model received, yet it still
    authorizes nothing.

### P3-B: a stitched anchor never escalates
- **Live (R2-T4 s1 a1).**
  - The SEARCH joins the shown member units `fill(int[],int)` (190–195) and `fill(long[],long)` (205–210), dropping
    the Javadoc at 196–204. Result: ANCHOR_NOT_IN_FILE.
  - `_remember_anchor_loci` → `locate_search_text`: the block is not one contiguous run, so the loci are the
    block's own unique lines (190, 205). Both are already inside shown spans.
  - `build_edit_capability` therefore opens no window, and the capability digest is unchanged (`e8a94bc8dc`).
  - `_decide_edit_capabilities` refused attempts 2 and 3 before any call (ANCHOR_CONTEXT_NOT_ESCALATED). The run had
    to switch models.
  - Only at level 2 (attempt 5) did a merged 180–213 span show the gap.
- **R1-T3 s2.** Identical: a2 stitched, a3/a4 refused unsent (digest `3a482015fc`).
- **What is lost.** The one deterministic fact the failure carries: which lines lie between the stitched parts.

### P3-C: prose check judges the base file
- **Live (R2-T2 s1 a2/a5/a6).** Three different, correct candidates for the `depth` property were each rejected as
  SOURCE PROSE CONTAMINATION at "line 146: This method is deprecated…".
- **The flagged line is in the unchanged base file** (base line 137; CONFIRMED by running the check on the base).
- **Producer (TRACED).** `attempt._reject_explanatory_prose` → `file_resolution.find_explanatory_prose_contamination`
  scans every line of the post-edit file. Baseline text is in scope at the edit call site (`orig_text`) but is not
  used.

### Lower-ranked, recorded with evidence
1. **P3-D (B): no insertion locus for an additive goal.** R1-T4 `BooleanUtils.countTrue`.
   - Localization grounds only the class, so there are no loci, no spans and no feasible operation.
   - It is a terminal stop before any model call; one run was lost entirely (80-run: 1).
2. **P3-E (B): the Developer file-list step widens targets beyond the write scope.**
   - R2-T2 s1 a7: the File List Planner returned `tests/test_cssselect2.py` for a subtask whose scope is `tree.py`.
   - The Developer was then asked for a full-file replacement of it in `REPAIR_WITH_FULL_FILE` mode, and the
     response was refused for lacking authority.
   - Kriya offered an operation it then rejected: a CONTEXT-EDIT-PROTOCOL-001 invariant breach.
3. **P3-F (A, B-induced): a merged window ends mid-member.** R1-T3 s2a8.
4. **INVALID_EDIT_PROTOCOL "text after the last block".**
   - 80-run 19 records in 12 runs; 0 in M1.
   - The cause needs a recorded raw response, so it is BLOCKED on evidence until a live M1 run produces one.

## 4. Ranking (impact × frequency × wasted calls × deterministic fixability)

| Rank | Unit | Impact | Frequency | Waste | Fixability |
|---|---|---|---|---|---|
| 1 | **P3-A** | Valid anchors refused on retries; burns retries and forces model switches | **4/10 runs**, both models; 80-run 12 runs (INFERRED) | 4 calls + 4 attempts (M1) | deterministic; bind authority to the final sent request |
| 2 | **P3-B** | Retries refused unsent; forced model switch; the needed fact never shown | 2/10 runs; 80-run 8 runs NOT_ESCALATED | 4 attempts at 0 calls + 6 failed calls with no recovery path | deterministic, small (gap loci) |
| 3 | **P3-C** | A file becomes unmodifiable; terminal for the subtask | 1/10 runs; 80-run 0 | 3 calls, both models | deterministic, trivial (diff-scoped) |
| 4 | P3-D | Terminal stop before inference | 1/10; 80-run 1 | whole run | deterministic; needs an insertion-locus design |
| 5 | P3-E | 2 wasted calls, invariant breach | 1/10; 80-run 2 | 2 calls | deterministic |

## 5. Implementation units (not implemented)

### P3-A: decide anchor authority on the request actually sent
- **Defect.** An anchor in exact current-revision source that the dispatched request carried verbatim is refused,
  because authority is computed before the request fit, from mandatory text only. Class D.
- **Reproducer.** `tests/_p3_reproducers.anchor_outside_retry`. It runs the real `_run_developer_generation` with a
  real `DeveloperAgent` (scripted transport), the real `_planned_source_context` and `_developer_optional_sections`,
  and the real `_authorize_anchors`. Inputs are the live ArrayFill bytes and the live a4 SEARCH/REPLACE.
  - Pinned in `tests/test_p3_developer_protocol_reproducers.py::test_p3a_*`: refused although the sent request
    holds the whole file and the anchor. Passes today.
  - Required in `handover/evidence/p3/test_p3_required_behaviour.py::test_p3a_an_anchor_in_exact_current_source_the_request_carried_is_authorized`.
    Fails today.
  - Negative control: an anchor never sent stays refused. Passes today and must keep passing.
  - The existing `test_only_mandatory_text_counts_as_shown_source` (trimmed section → no authority) must also stay
    green.
- **Fix boundary.**
  - The Developer records the final fitted user prompt it dispatched for each target: `DeveloperAgent`'s Developer
    request path, at `fit_developer_request` / `DeveloperRequestFit.fit`.
  - `attempt._authorize_anchors` (via `_current_edit_capability`) extends the capability with spans for exact
    current-revision text of the target found verbatim in that dispatched prompt. This is the same byte check
    `_decide_edit_capabilities` already applies to mandatory text ("whole current file present verbatim… checked
    on the bytes").
  - Unchanged:
    - the operations offered;
    - full-file authority (D1's alone);
    - optional text that the fit trimmed, which still authorizes nothing.
- **Mutation plan.**
  1. Count the pre-fit optional text, not the dispatched text: the trimmed-section test must fail.
  2. Accept any anchor present in the file: the never-sent negative control must fail.
  3. Ignore the revision of the dispatched text: needs a stale-revision test.
  4. Grant full-file authority from dispatched text: a D1 test must fail.

### P3-B: a stitched anchor escalates with the lines between its parts
- **Defect.** A rejected SEARCH that is the concatenation of two or more contiguous runs of the real file yields loci
  only inside spans already shown. The capability is unchanged, so the retry is refused unsent
  (ANCHOR_CONTEXT_NOT_ESCALATED) and the missing lines are never shown. Class C; the stitch itself is class A.
- **Reproducer.** `tests/_p3_reproducers.stitched_anchor_retry`. It runs the real `_decide_edit_capabilities`,
  `_authorize_anchors`, `_record_edit_protocol_failure` and `_remember_anchor_loci`, in the apply path's own order.
  Inputs are the live ArrayFill bytes, the live member units 190–195 and 205–210, and the live a1 SEARCH.
  - Pinned: `test_p3b_*` (passes today).
  - Required: `test_p3b_the_retry_shows_the_lines_between_the_stitched_parts` (fails today). After the fix:
    - the anchor is still ANCHOR_NOT_IN_FILE;
    - the second capability differs;
    - every line 196–204 is covered by exact current bytes.
  - Negative control (existing): `test_an_unchanged_capability_after_an_anchor_miss_is_a_typed_no_progress_stop`
    must stay green.
- **Fix boundary.** `edit_capability.locate_search_text`, used by `attempt._remember_anchor_loci`.
  - When a block decomposes into two or more contiguous file runs, add the lines strictly between consecutive runs
    as loci.
  - Optionally name the runs and the gap in the ANCHOR_NOT_IN_FILE retry message instead of "fabricated or stale":
    the same evidence, typed.
  - No change to the matcher, the protocol or authority.
- **Mutation plan.**
  1. Drop the gap loci: the required test must fail (digest unchanged).
  2. Add the gap loci for a non-stitched altered block: the existing per-line tests
     (`test_a_block_quoted_more_than_once_still_localizes_line_by_line`, `test_a_single_line_keeps_the_per_line_rule`)
     must fail.
  3. Accept the stitched anchor: the `reason == ANCHOR_NOT_IN_FILE` assertion must fail.

### P3-C: only candidate-written lines can be prose contamination
- **Defect.** The explanatory-prose check flags a line of the unchanged base file, so every candidate for that file
  is rejected whatever the model writes. Class D/B.
- **Reproducer.**
  - `tests/_p3_reproducers.prose_on_unchanged_line`: the real `_reject_explanatory_prose` on the live a2 candidate.
  - `prose_end_to_end`: the real direct workflow; live base `tree.py`; the live a2 Developer response verbatim;
    gates stubbed green. Every attempt from the 3rd on is `prose_contamination`, and the change is never applied.
  - Pinned: `test_p3c_*` (passes today).
  - Required: `test_p3c_a_line_of_the_unchanged_base_is_not_candidate_contamination` and
    `test_p3c_the_live_response_is_accepted_end_to_end` (fail today).
  - Negative controls: prose the candidate adds is still rejected end to end, and a prose line in a new file is still
    flagged. Both pass today and must keep passing.
- **Fix boundary.**
  - `file_resolution.find_explanatory_prose_contamination` gains the baseline content and judges only lines whose
    count in the candidate exceeds their count in the baseline.
  - `attempt._reject_explanatory_prose` and both call sites pass it: the edit path at `attempt.py:8347` (`orig_text`)
    and the full-file path at `:8489` (the current file, or None for a new file).
- **Mutation plan.**
  1. Remove the baseline subtraction: the required test fails.
  2. Subtract by line identity, not count (a duplicated prose line passes): needs a duplicate-line test.
  3. Treat a new file as all-baseline: the new-file negative control fails.
  4. Skip the check entirely: the added-prose control fails.

## 6. Status

```text
P3 ROOT CAUSES (ranked)   P3-A authority decided before request fit (D); P3-B stitched anchor never escalates (C);
                          P3-C prose check on unchanged base lines (D); then P3-D no additive locus (B),
                          P3-E file-list widens targets (B), P3-F window cut mid-member (A/B), INVALID_EDIT_PROTOCOL (A, BLOCKED: no raw response)
P3 REPRODUCERS            READY: tests/test_p3_developer_protocol_reproducers.py (5 pass today);
                          handover/evidence/p3/test_p3_required_behaviour.py (4 required fail today, 2 controls + isolation pass)
P3 PRODUCTION CODE CHANGED NO
```

Run the required-behaviour file with:

```text
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p3/test_p3_required_behaviour.py
```
