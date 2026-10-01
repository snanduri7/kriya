# PRD-028 Coding Agent Handover: Dynamic Mutation Authority Escalation and Language Adapter Contract

## Status
**VERIFIED (Batch 6 closure, 2026-09-27).** Pytest: the 001C subset (1856) and full suite (6245) @ e34e0ee, and the PRD027 subset and full suite @ a04e8ac. Live: `handover/evidence/BATCH6/user-live-4`, every case LIVE_EXERCISED, 32K QUALIFIED qwen3-coder preflight. Production: `doctor --production` PRODUCTION_READY=true. Earlier status: READY_FOR_PYTEST_VERIFICATION. This is part of the Batch 6 stop.

## Source identity
- Base revision: 3c1822d (PRD-027).
- Final revision: the PRD-028 commit (see `git log --grep PRD-028`).
- Directives: handover/BATCH6_DIRECTIVES.md, the PRD-028 section.

## Suspected P0 defect: the outcome
**Candidate/worktree text granting pristine-source authority: not confirmed in a harmful form. A provenance gap was confirmed and fixed.**
- **What was verified.**
  - The worktree is created once per run (`workflow.py` `create_git_worktree`) and is not reset between attempts.
  - Retry member hints (`_resolve_retry_member_hints`), DEV-INV `inspect_member` and D1's exactness check all resolve worktree-first, so on a retry they read this run's own candidate.
- **Why that is correct for the edit target.** `apply_anchored_edits` requires each SEARCH block to match worktree content exactly once. Resolving from the pristine workspace would break every retry on an already-written file.
- **Baseline-claiming consumers, audited.** None reads these records as repository truth:
  - PRD-023 `find_brownfield_public_api_changes` compares candidate against baseline originals.
  - `derive_semantic_authority_for_run` reads `workspace_path`.
  - Pre-plan grounding uses `CurrentSourceResolver(workspace_path, None)`.
  - D1 is current-source authority for the edit target, and is left unchanged on purpose. A full-set retry shows the whole candidate, which is the baseline plus authorized edits, all visible. Tightening D1 to demand a pristine revision would turn every such retry into an operation-contract loop.
- **The gap.** Candidate-derived member records were indistinguishable from pristine evidence: `trust_level="repository"`, with no origin.
- **Repro test:** `test_candidate_member_is_granted_as_candidate_never_as_pristine` and `test_retry_member_hint_records_candidate_origin_for_an_already_written_file`. A member present only in the candidate now records `source_origin=CANDIDATE` and `member_in_pristine=False`, and the baseline claim is computed only from the workspace.
- **Why not on `ContextItem`.** Provenance is carried on the new authority records and events, not on `ContextItem`: adding a `ContextItem` field would change context-package hashes that feed checkpoints and resume fingerprints.

## Scope implemented
- **Req 1 (adapter contract).** `kriya/workflow/language_adapters.py`: `Capability` (symbol_identity, member_boundaries, references, exact_source, editable_region, verification_hooks), `CapabilityStatus`, `LanguageAdapter`, and a registry.
  - Java: all capabilities supported, except PARTIAL references.
  - Python: `editable_region` UNSUPPORTED, because semantic regions are Java-only; PARTIAL references.
  - Everything else is UNSUPPORTED.
  - `member_boundaries_for` keeps its signature and delegates to the registry. This is not a generic switch or a plugin framework.
- **Req 2 (Kriya-decided, read-only escalation).** `kriya/workflow/authority_escalation.py`: `request_member_authority`, `grant_member_hints`, `expansion_is_current`.
  - Wired into the retry member-hint path, which is always on, and into the DEV-INV `inspect_member` merge, which is opt-in.
  - Only GRANTED members become hints or edit authority; the control plane decides, not the model.
- **Req 3 (records).** Each record holds:
  - the exact member, the language and the capability;
  - the source revision, the pristine revision and the origin;
  - `member_in_pristine`, from the baseline only;
  - the evidence the request rests on;
  - the outcome and reason code;
  - `mutation_boundary=authorized_write_scope` and `in_write_scope`.
  Records go to `GenerationState.authority_expansions` and the `authority.expansion` event, which is persisted in `traces.db` run events. They are revalidated on revision change.
- **Req 4 (fail closed).** An unsupported language, missing source, absent member or ambiguous overload is INDETERMINATE, never a whole-file upgrade.
  - Context expansion is not mutation-scope expansion: an out-of-scope request is INDETERMINATE and the scope object is unchanged (asserted).
  - DENY_ALL grants nothing.
  - Out-of-scope investigation evidence is still shown read-only.
- **Req 5 (Java/Python first, status exposed).** Capability status appears in telemetry (every record) and in doctor. `semantic.precision_boundary` (owner PRD-028) reports the capability table and the workspace languages. It is required and failing only when `semantic_region_enforcement_required` is set and the workspace holds a language without `editable_region`; otherwise it is WARN and not required, as before.
- **Not changed** (per the directive and CORR-018): `semantic_region_enforcement_required` and DEV-INV stay default-off.

## Files changed
- **Production:**
  - `kriya/workflow/language_adapters.py` (new);
  - `kriya/workflow/authority_escalation.py` (new);
  - `kriya/workflow/context_source.py` (delegation);
  - `kriya/workflow/attempt.py` (`_escalation_authorized_paths`, `_record_authority_expansions`, retry + DEV-INV wiring);
  - `kriya/workflow/state.py` (`authority_expansions`);
  - `kriya/production_doctor.py` (the precision boundary check).
- **Tests:** `tests/test_prd028_authority_escalation.py` (21); `tests/test_production_doctor.py` (+3).
- **Live:** `tests/test_live_prd025_029_batch6.py::test_live_prd028_member_authority_is_revision_bound_and_in_scope`.
- **Docs:** `docs/design.md` §4.2a.

## Tests run by coding agent (targeted)
| Command | Passed | Failed |
|---|---:|---:|
| `.venv/bin/pytest -q tests/test_prd028_authority_escalation.py` | 21 | 0 |
| `.venv/bin/pytest -q tests/test_val001_g1r3_retry_context.py tests/test_dev_inv_001_investigation.py tests/test_d1_operation_mode_authority.py tests/test_context_source.py tests/test_java_members.py` | 218 | 0 |
| `.venv/bin/pytest -q tests/test_production_doctor.py` | 63 | 0 |

**Mutation checks.** All 11 were killed:
- the scope check;
- origin forced to PRISTINE;
- `member_in_pristine` forced True;
- ambiguity ignored;
- source-revision revalidation;
- baseline-revision revalidation;
- granted-only filtering;
- the DENY_ALL scope;
- the DEV-INV merge of ungranted members;
- an unknown extension treated as Java;
- the retry path ignoring the gate.

## Static/lint/architecture checks
ruff: All checks passed. pylint: exit 0.

## Live test additions
- Required: YES.
- The live case: a real repair of one method in a 160-method Python module, with DEV-INV enabled.
- **Deterministic trigger (468039d).** The first compile of the model's changed `ledger.py` fails once at `apply_fee`'s line, in PolymorphicValidator's own Python error shape. The retry must then request member authority for `Ledger.apply_fee`, whatever the model wrote.
- **Asserted:**
  - the request is GRANTED from `retry_member_hints`;
  - it is in the write scope;
  - its origin is CANDIDATE, since it was resolved against the real candidate;
  - `member_in_pristine` is true;
  - every recorded expansion is in scope and revision-bound.
- Verified offline with a mocked model before commit: attempt 2 recorded exactly that record.
- **Skip.** The test skips only if the model never wrote a changed `ledger.py`, in which case the retry path is unreachable. The evidence file then records `NOT_LIVE_EXERCISED`, never verification.

## Known limitations / residual risks
- Revalidation (`expansion_is_current`) is exercised at the decision point and in tests. Hints are recomputed every attempt, so a grant is never carried stale into a later attempt's hints. A long-lived consumer of `state.authority_expansions` must call it.
- `references` is PARTIAL for both languages: the graph edges are name-based, not type-resolved.
- **Narrowing.** DEV-INV evidence becomes edit authority only for the call's `known_target_files`; all five `_run_developer_generation` call sites pass them. Where that list is empty, the evidence is shown read-only. Before PRD-028, any inspected member was merged as authority.

## Verification-agent handoff
Run the Batch 6 focused command, then the full suite, then the live `-k prd028` case.
