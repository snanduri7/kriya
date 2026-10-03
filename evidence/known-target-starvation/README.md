# KNOWN-TARGET-MULTI-TARGET-STARVATION-001 (P1) - evidence

Branch `fix/known-target-multi-target-starvation-r1`, from origin/main 0bea22f. Independent of CAGC.

## Observation (MEASURED)
CAGC-0 A/B matrix 2026-10-03, two runs (both arms run the same code here):
- spring-xml B r3: subtask s1 = [ClinicServiceImpl.java, tools-config.xml]. Known-target package: 5 member_exact
  units of ClinicServiceImpl; tools-config.xml omitted `budget_exhausted` (522 est. tokens); its edit capability
  had no operation -> `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`, no Developer request.
- invalidurl B r3: s1 = [_exceptions.py, _urls.py]; _urls.py omitted `budget_exhausted` (5443 est. tokens); same stop.
- Allocation window 8491 (model.transition); window reserve int(8491 x 0.12) = 1018; known-target limit floor
  min(1000, int(8491 x 0.15)) = 1000.

## Producer (TRACED)
`attempt.run_attempt` -> `_target_package_with_window_reserve` -> `context_budget.build_known_target_context`.
The limit is `_reserve_graph_context_budget(window, skills, reference, design, plan, graph)`; when graph context
fills the 0.60 pool it sits at the 1000 floor. A target not shown whole triggers the rebuild with the window
reserve held back -> limit 0. Only targets with grounded member hints had T0's protected room
(`exact_member_budget`); an unhinted target had only the shared limit, and the first target's admitted units
were charged against it too.

## Hypotheses and discriminating check
- H1 (closure report): the first target's member units consumed the budget.
- H2: the limit was already at its floor and the rebuild left 0; the unhinted target had no protected room.
- Check: `replay.py` re-runs `build_known_target_context` + the two-build rule on the preserved workspaces'
  bytes over every (limit, T0) combination consistent with the run. Pre-fix (`replay_prefix_0bea22f.json`): the
  recorded package (members only + second target budget_exhausted) is produced exactly for limits 1000-~1100,
  i.e. at the floor (H2); at larger limits the second target gets only a bounded excerpt (tier `skeleton`), which
  `shown_exact_texts` never treats as edit authority - the same stop. H1 contributes (members are charged to the
  shared limit) but is not sufficient alone.

## Root cause (CONFIRMED)
Allocation was sequential per target and only grounded members were protected: nothing guaranteed every planned
existing target a minimum authoritative editable unit before optional enrichment or before the shared limit ran
out.

## Fix
`build_known_target_context` in two passes:
1. every existing target's minimum authoritative unit (its first grounded member_exact unit, else its whole
   current source), admitted against T0's room as well as the shared limit (the rule members always had); a
   minimum that fits nowhere is omitted `minimum_authority_unfit` (typed capacity limit);
2. enrichment from what is left: further grounded members (T0 rule), the Code Intelligence member package or
   sibling signatures, a bounded excerpt for a target whose whole source did not fit.
Rendered grouped by target in rank order. Single-target behaviour unchanged; with no T0 budget the shared limit
alone applies, as before. Revision binding, authority checks, file integrity and the edit-capability rule are
untouched: an excerpt still grants no authority, and an infeasible capability still refuses typed
(`CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`) before any model call.

## Prediction (stated before the fix) and post-fix result
Predicted: in both reproductions every planned existing target gets non-empty authoritative editable context,
unless the mandatory minimum set itself cannot fit, which must be a typed capacity refusal.
- spring-xml r3 (`replay_postfix.json`): tools-config.xml shown **whole** in all 98 consistent (limit, T0)
  combinations; both targets editable. **PASS.**
- invalidurl r3: _urls.py has no grounded member, so its minimum is its whole source, 5443 est. tokens > T0's
  entire room (0.60 x 8491 = 5094) -> **typed capacity refusal**, now recorded `minimum_authority_unfit`. This is
  the stated exception, not starvation; with a second module that fits T0's room the same shape authorizes both
  (regression test).

## Verification
- Original symptom first: replay above (pre-fix reproduces, post-fix resolved / typed capacity).
- Regression `tests/test_known_target_multi_target_starvation_001.py` (13): end-to-end direct runs in the
  measured budget shape (allocation window 8491; graph context fills the pool; recorded member hints at the
  grounding seam) - the spring shape and the fitting httpx shape fail before the fix (Developer never called,
  `context_edit_protocol_unsatisfiable`), pass after; allocator-level tests for pass order, shared T0 room, rank
  rendering, the capacity omission and single-target equivalence. Pre-fix: 10 of 13 fail; the 3 that pass both
  ways are guards (single-target equivalence; the limit-1000/1400 cases).
- Mutation (`mutations.json`): 9/9 killed (T0 room, T0 charging, pass order x2, capacity reason, excerpt sizing,
  early skip, whole-file minimum, render order).
- Adjacent: context_source, code_intel_developer_t0, prd016_allocation, val001_g1_remediation, context_budget,
  dev_inv_001_investigation, context_edit_protocol_001, workflow, prompt_fit_role_chain_001,
  developer_prompt_fit_001: 1160 passed.
- Full suite (worktree code on PYTHONPATH, verified via `kriya.workflow.context_budget.__file__`): **8389 passed,
  0 failed** (origin/main collects 8376; +13 new).
- ruff: clean. pylint `kriya plugins/core_tools tests`: exit 0.

## Not done
No live run. The CAGC comparison is a separate experiment (benchmarks/cagc/PROTOCOL_v2.md on
bench/cagc-ab-protocol-v2) and is never evidence for or against this fix.
