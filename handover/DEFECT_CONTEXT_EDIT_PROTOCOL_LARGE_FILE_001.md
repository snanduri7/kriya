# CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001: a localized change in a file larger than the context budget was refused for want of a locus

## Status
**FIXED** (2026-10-07) on `fix/backend-reliability-closure` from main f757e6b. Severity P2. Registry row CLOSED.
Discovered by the BACKEND-READINESS-001 blind cohort (T2 jsoup, T6 commons-csv); fixed in BACKEND-RELIABILITY-CLOSURE-002
(evidence `~/kriya-m1-live/backend-reliability-closure-002/defects/large-file-edit/`; trace from the sealed stores of both runs).

## Observation (MEASURED, from the recorded runs)
T6 (CSVFormat.java, 3369 lines, 32646 tokens): known-target package = one `skeleton` unit, omissions `minimum_authority_unfit`
(whole file 32646 tokens) and `body_elided`; `member_hint_paths []`; localization adopted the FIELD `CSVFormat.EXCEL`
(declaration line 1091, exact, margin 7.0) - but `context.edit_capability` recorded `loci []`, `spans []`, `operations []`,
`budget_chars 3368`; the Developer was never called (CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE). A radius-8 window at line 1091 is
558 chars. T2 (Element.java, 2211 lines) had the same shape with loci [], but its plan targeted the wrong file (the defect
lives in Node.absUrl / StringUtil.resolve; localization pointed at Jsoup.parse): the typed stop was correct there, and that
planned-target/localization mismatch is a separate item (PLAN-TARGET-LOCALIZATION-MISMATCH-001, registry).

## Producer (TRACED)
`attempt._edit_capability_loci` knew only three locus sources: code quoted in the goal (none: the goal quoted no code
verbatim), the last failure's lines (none on attempt 1) and earlier rejected anchors (none). The adopted localization
target's declaration line (code-intel `Symbol.declaration.start_line`, bound to `FileStructure.source_digest`) never
reached the capability decision; member hints exist only for callables (`CALLABLE_KINDS`), so a field is never one.
Separately, a grounded member whose body exceeds T0's room is omitted with its id but without its lines.

## Fix
- `graph_retrieval.GraphRetrievalResult.retrieval_symbol_loci`: path -> [(declaration line, digest of the current
  bytes)] of every direct structural candidate of ANY kind; `adopt_decision` puts the adopted targets' loci first.
  Threaded through the workflow into `AttemptContext.retrieval_symbol_loci`.
- `context_package.make_omitted_entry` records `start_line`/`end_line`/`revision` for an omitted grounded member
  (lines only, never text); `attempt._record_omitted_members` keeps them per path on
  `state.known_target_omitted_members` at both package sites.
- `attempt._edit_capability_loci` adds the symbol loci whose digest equals the file's current raw digest (at most
  `MAX_SYMBOL_LOCI_PER_FILE` = 2 per file, adopted first) and the omitted members' first lines whose revision equals the
  current shown revision. Everything after that is unchanged: `build_edit_capability` cuts byte-exact windows within
  the existing budget, anchors are authorized only inside the windows, the radius escalates on an anchor miss, and
  whole-file authority stays D1's. A stale digest or revision yields no locus (the typed refusal, never a window at
  shifted lines).

## Verification
- `tests/test_context_edit_protocol_large_file_001.py` (6): real code-intel retrieval records the field's digest-bound
  line and the adopted target leads; the measured refusal (zero loci) then one exact window (operations anchored_edit
  only, full_file False, window < 40 lines, the whole file absent from the request, anchors outside the window refused);
  a stale digest or a file changed after retrieval authorizes nothing; at most two candidates per file share the
  budget and another path's locus is ignored; an omitted grounded member records its boundary and becomes a locus
  (revision-bound); an anchor miss widens the window at the same locus.
- Mutations (`mutations.txt`): m1 symbol loci unused -> 2 failed; m2 digest binding dropped -> 1 failed; m3 boundary
  not recorded -> 1 failed; m4 adopted target no longer leads -> survived the first test version, killed after the test
  was strengthened (1 failed); m5 revision check on omitted members dropped -> 1 failed.
- Adjacent: edit-protocol, P3-A/B/D, multi-target starvation, code-intel retry T0, D1 operation authority, Developer
  protocol reproducers, code-intel construction/config, PRD-016: 193 passed. ruff + pylint 0.
