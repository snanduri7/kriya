# Backlog Closure 6.5: summary

- **Directive:** handover/BACKLOG_6_5_DIRECTIVES.md (user, 2026-09-27).
- **Scope:** six items in this order: KNOWLEDGE-READPATH-001 → FALLBACK-CONTEXT-WINDOW-001 → PROMPT-BUDGET-FIT-001A+B → PRD027-SCORE-NORMALIZATION-001 → INF-001 (scoped framework). Plus any P0/P1 found on the way.
- **Status:** IN PROGRESS. All commits are local, based on a7916f1. No push.

## Commits
| Commit | Slice |
|---|---|
| a314d45 | Fix (my f3707c4): the Developer never received `reference_context` |
| f136d7e | KNOWLEDGE-READPATH-001: one learned-knowledge store and one reader |
| 12c1b0a | FALLBACK-CONTEXT-WINDOW-001: the budgeted context window is the one requested |
| a828a67 | Fix (my 12c1b0a): `test_llm_extra` still expected a request body without the context window |
| 8892e3c | PROMPT-BUDGET-FIT-001A/B: one fixed-overhead-aware section budget for Planner and Reviewer requests |
| (next) | Fix (my a314d45): learned reference overflowed Developer requests at small windows |

## Per item
- **KNOWLEDGE-READPATH-001 (P1).** FIXED, awaiting pytest. See handover/DEFECT_KNOWLEDGE_READPATH_001.md, "Fix".
- **FALLBACK-CONTEXT-WINDOW-001 (P2).** FIXED, awaiting pytest. See handover/DEFECT_FALLBACK_CONTEXT_WINDOW_001.md.
  - demo-03 request bodies and runtime digests are unchanged.
  - A binding without an explicit option gets a new runtime digest and needs `kriya model qualify`.
- **PROMPT-BUDGET-FIT-001A/B (P2).** FIXED, awaiting pytest. See handover/DEFECT_PROMPT_BUDGET_FIT_001.md, "001A + 001B fix".

## Findings recorded on the way
- **Developer reference fit (own regression from a314d45/f136d7e). FIXED in its own commit.** With learned knowledge present, 8K REPAIR-mode Developer requests were refused, because the reference was reserved but never trimmed. `developer_reference` now trims it to the graph pool.
  - The regression test fails at 8892e3c with 4 Developer refusals, and passes with the fix, matching the terminal state of the same run without learned knowledge.
  - 2 mutations run, both killed.
- **DEVELOPER-PROMPT-FIT-001 (P2, OPEN).** The Developer's fixed text at 8K exceeds its preferred room. Not in 6.5 scope.
