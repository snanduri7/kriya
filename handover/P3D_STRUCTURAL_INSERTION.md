# P3-D — Structural new-member insertion authority

Branch `feature/lr-r1-p3d` (worktree `~/kriya-wt/p3d`), based on the certified B3 lineage (`eadb7d0`; executable code
identical to `5ab9d69`). Not pushed, not merged. No live model was run for any part of P3-D.

| Commit | Content |
|---|---|
| `76cc1e4` | Reproducer only (no production change): real NumberUtils.java fixture, frozen A3 goal, harness target parameter |
| `6ddbeea` | P3-D implementation + tests + CLAUDE.md |
| `bcb0c1d` | two tests killing the run-1 mutation survivors (the certified executable revision) |
| (evidence) | this report, mutation/suite/reproducer outputs |

## 1. The A3 failure, traced (questions 1-7)

Observation (MEASURED, live A3 run `eadb7d0` traces; reproduced deterministically in `76cc1e4`):
`context.edit_capability` for `NumberUtils.java` had `spans: []`, `loci: []`, `budget_chars: 4072`, operations `[]`
-> `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE` with 0 Developer requests. The known-target package carried the skeleton tier
only (full source omitted as `minimum_authority_unfit`, 15,197 estimated tokens).

1. **Representation of a planned new member** — none existed (TRACED: `attempt._edit_capability_loci`). Loci came only
   from goal-quoted code found verbatim in the file, failure lines and rejected-SEARCH loci. A quoted *new* signature is
   by definition not in the file, so it localized nothing.
2. **Why an existing anchor was required** — TRACED: `build_edit_capability` makes ANCHORED_EDIT feasible only with
   exact spans covering every locus (`anchored = bool(spans) and not uncovered_loci`), and `_authorize_anchors` /
   `EditCapability.anchor_status` accept a SEARCH only inside those spans. No locus -> no span -> no operation; FULL_FILE
   needs D1 authority, which a 15k-token file cannot have within the window.
3. **Who knows the owner** — Code Intelligence: localization grounded the class `NumberUtils`; the structural model
   (`parse_text`) has every type with its body span and every member with `parent_id`.
4. **Can the parser establish the boundary** — yes (MEASURED on NumberUtils): class body span lines 34-1696, bytes
   1223..60789, the byte at `end-1` is `}`; 89 direct members (67 methods, 21 fields, 1 constructor); the last member
   ends on line 1695. A probe with nested classes, braces in strings/comments and a second top-level class parsed with
   the correct boundaries.
5. **Protocol change required** — **NO**. The existing anchored SEARCH/REPLACE edit expresses an insertion when the
   SEARCH is a unique exact carrier ending at the boundary and the REPLACE is that carrier plus the new member. What was
   missing was *authority*, not syntax; the new authority is checked on the bytes the edit produces.
6. **Smallest carrier** — the whole lines from the anchor member's end through the line of the boundary, at least 2,
   extended upward until the run occurs exactly once in the file (bounded at 12 lines). For A3: lines 1695-1696
   (`    }` + `}`), 7 characters.
7. **Root cause** — CONFIRMED: no authority representation existed for adding a member to an existing type whose source
   cannot be shown in full; the capability had nothing to offer, so the run stopped correctly but unnecessarily. The
   discriminating check is the reproducer: same shape, only the locus added -> constructible (section 3).

## 2. Design (`kriya/workflow/insertion_locus.py`)

`InsertionLocus(path, revision, language, owner_symbol_id, owner_lookup_key, tier, gap_start, gap_end, start_line,
end_line, carrier, planned_members, provenance)` + `digest` (version, path, revision, owner, tier, gap, lines, carrier
sha256, provenance). Zero-width: "the candidate may ADD bytes in [gap_start, gap_end]".

- **Need** — `planned_new_members(goal, existing)`: identifiers before `(` in the user's goal's quoted code, not
  declared by the owner. Goal = `ctx.grounding_goal or ctx.goal` (the user's words, never the plan or a model).
- **Owner** — the single localization-grounded type, else the single type the goal names, else the file's top-level
  type named after the file; more than one at any level = refused (ambiguous). V1 kinds: class, interface, record;
  enum/annotation types typed unsupported; Python typed unsupported (`Capability.STRUCTURAL_INSERTION`: Java PARTIAL,
  Python UNSUPPORTED).
- **Tier** — `after_grounded_member` (after the last grounded direct member of the owner) else
  `owner_closing_delimiter` (after the last direct member; the closing brace is the parser's body end, verified to be
  `}`; no brace search). The gap is the whitespace run right after the anchor: never inside a comment, a neighbour or
  past the delimiter.
- **Developer context** — the carrier rendered as `[insertion_locus]` exact source with an explicit ADD-only
  instruction (owner, planned member, "every existing line stays byte-identical; no imports"); the owner signature,
  imports and nearby signatures come from the existing skeleton known-target package. All of it is subject to P3-A:
  authority requires the carrier in the request actually sent.
- **Acceptance** — `EditCapability.anchor_status` returns `INSERTION_ONLY` for a SEARCH found only in the carrier (an
  ordinary span still wins). `_authorize_anchors` then applies that edit alone and calls `verify_insertion`:
  1. file revision == locus revision, else `STALE_INSERTION_LOCUS`;
  2. owner symbol id still present and enclosing the gap; carrier lines unchanged, else `STALE_INSERTION_LOCUS`;
  3. carrier in a dispatched request, else `INSERTION_LOCUS_NOT_SENT`;
  4. pure insertion (one contiguous run added, nothing removed/changed) whose possible offsets meet the gap, else
     `INSERTION_OUTSIDE_LOCUS`;
  5. re-parse: PARSED, every original declaration kept, at least one new declaration, all new ones under the owner,
     else `INSERTION_STRUCTURE_CHANGED`.
  Success records `context.structural_insertion_authorized` (AUTHORITATIVE). A refusal is the ordinary anchor-failure
  retry path (ValueError), never a write.
- **Not persisted** — each Developer invocation resolves its own locus from the bytes it reads; `build_edit_capability`
  drops a locus of another revision. Full-file authority stays D1's alone.

## 3. A3 original symptom (no model) — `handover/evidence/p3d/`

| | pre-fix (`76cc1e4`) | post-fix (`6ddbeea`) |
|---|---|---|
| Developer requests | 0 | 1 |
| operations | `[]` | `[anchored_edit]`, full_file False |
| result | `context_edit_protocol_unsatisfiable` | quality gates passed, clamp applied as a pure insertion |

Post-fix measurements (`a3_reproducer_postfix_6ddbeea.txt`): file 60,790 bytes / 1,696 lines, revision `c015dad6…`
(= the live A3 revision); full-file estimate 15,197 tokens; window 2 lines / 7 chars; tier `owner_closing_delimiter`;
owner `org.apache.commons.lang3.math.NumberUtils`; locus revision == file revision: True; carrier in the fitted request
sent: True; whole file in the request: False; one `context.structural_insertion_authorized` event.

## 4. Controls 1-18 (`tests/test_p3d_structural_insertion.py`, 33 tests)

| # | Control | Test |
|---|---|---|
| 1 | large class + new method | `test_1_a3_large_class_new_method_is_constructible_without_the_whole_file`, `test_1_pre_fix_shape_has_no_feasible_operation_without_the_locus` |
| 2 | small class, same mechanism | `test_2_small_class_same_mechanism`, `test_grounded_member_gives_tier_after_grounded_member` |
| 3 | existing-member edits stay anchored | `test_3_existing_member_edit_uses_the_ordinary_anchored_path`, `test_ordinary_span_wins_over_the_carrier` |
| 4 | ambiguous owner refused | `test_4_ambiguous_owner_refused`, `test_owner_by_file_name_when_goal_and_grounding_are_silent` |
| 5 | stale revision | `test_5_stale_revision_refused` |
| 6 | owner changed | `test_6_owner_identity_changed_refused`, `test_carrier_digest_mismatch_refused` |
| 7 | carrier trimmed from the final request | `test_7_carrier_not_in_the_sent_request_refused` (2 cases) |
| 8 | model-invented location | `test_8_model_invented_location_refused` |
| 9 | another class in the same file | `test_9_insertion_into_another_class_of_the_same_file_refused` |
| 10 | outside the owner body | `test_10_outside_the_owner_body_refused` |
| 11 | nested-class boundaries | `test_11_nested_class_boundaries` |
| 12 | braces in strings/comments | `test_12_braces_in_strings_and_comments` |
| 13 | annotations/generics/records/interfaces; enum/annotation type/Python typed unsupported | `test_13_*`, `test_python_is_typed_unsupported` |
| 14 | no neighbouring modification | `test_14_neighbouring_member_modification_refused`, `test_deleting_or_insertion_without_a_new_member_refused` |
| 15 | imports separately authorized | `test_15_imports_need_their_own_authority` |
| 16 | resume/later invocation never reuses a stale locus | `test_16_stale_locus_is_never_reused` |
| 17 | P3-B unchanged | `tests/test_p3b_stitched_anchor_loci.py` (non-interference, section 6) |
| 18 | P3-C unchanged | `tests/test_p3c_prose_candidate_delta.py` (non-interference, section 6) |

Also: `test_insertion_never_grants_full_file_authority`, `test_planned_new_members_*`, `test_graphify_preflight_signal`.

Disclosed nuance (control 10): text appended after the owner's final `}` whose own last bytes are `}\n` produces the same
bytes as an insertion just before that brace (inside the gap); the byte check cannot tell the two apart, and the
re-parse refuses it (`INSERTION_STRUCTURE_CHANGED`: the new declarations are outside the owner). Both are typed refusals.

## 5. Mutation

`handover/evidence/p3d/p3d_mutation_campaign.py` (one fresh clone per mutant; KILLED iff the test set fails). 20 mutants:
the 10 owner targets (raw final-brace search, ignore owner identity, ignore revision mismatch, ignore carrier-digest
mismatch, allow neighbouring replacement, accept a locus not in the final request, insertion in a different class, reuse
a stale locus, full-file authority from insertion, fallback to full-file context) and 10 of P3-D's own decision points.

- Run 1 @ `6ddbeea`: 18 KILLED, 2 SURVIVED (`declaration-loss-accepted`, `unparsable-result-accepted`). Both checks are
  reachable: an insertion at the gap that comments out a following one-line declaration, and an insertion with a syntax
  error that tree-sitter's error recovery still extracts. Two tests added (`bcb0c1d`); each was confirmed to fail with its
  mutant applied.
- Run 2 @ `bcb0c1d`: **20/20 KILLED**. Logs: `p3d_mutation_run1_6ddbeea.txt`, `p3d_mutation_run2_bcb0c1d.txt`.

## 6. Non-interference, lint, full suite

- Non-interference @ `bcb0c1d` (P3-A/B/C + P3 reproducers, P2, FS-1/FS-1A, FS-1C/C0/C1, B2-a, B2-COV, B2-c, B3, M1
  incl. I-2, P1, P4, P5, CONTEXT-EDIT-PROTOCOL-001, P3-D): **748 passed, 0 failed** (`non_interference_bcb0c1d.txt`).
- Adjacent @ `6ddbeea` (edit protocol, P3-A/B/C, PRD-028 adapters, file integrity 001/001B, Code Intelligence T0):
  467 passed.
- ruff: all checks passed. pylint `kriya plugins/core_tools tests`: exit 0.
- Full suite @ `bcb0c1d` (absolute PYTHONPATH, `-n 8 --dist loadgroup`): **9156 passed, 0 failed, 0 errors** in 546.7s
  (`full_suite_bcb0c1d.txt`; B3 was 9123 + 33 new).

## 7. Graphify preflight

`structural_insertion_readiness(path, required)`: **YES** for Java when a new member is required (class/interface/record
owners; an enum or annotation-type owner is still refused per file), **NO** for Python, **NOT_REQUIRED** when the task
adds no member. Graphify signal for the A3 shape: **YES**.

## 8. Recorded separately

- **ENV-JVM-COLD-CACHE-1** (environment/tooling, not a Kriya defect, not fixed here): during the A3 live run's baseline
  acquisition a cold-cache Maven/JVM step exited 137 (MEASURED: SIGKILL; out-of-memory is INFERRED, not measured); the
  authoritative offline retry passed. Probable owner (INFERRED): container/JVM resources on the host. Evidence:
  `handover/evidence/b3/a3-live/run/A3_RESULT.md`. Deferred: no Kriya defect established; it needs a cold-cache
  memory measurement, which is environment work outside P3-D.
- Still deferred from earlier increments: PLAN-R1, B3-UX-1, negative model authority, FS-1A residual.

## 9. Remaining uncertainty

- Live model behaviour on the rendered insertion instruction is unmeasured (no live run in P3-D).
- V1 covers Java class/interface/record owners and one insertion region per file per invocation; an insertion plus an
  edit of an existing member works only when the existing member has its own ordinary exact span.
- Imports a new member needs are not authorized by the locus (by design); a member needing a new import fails closed
  until import authority exists.
