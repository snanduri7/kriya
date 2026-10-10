# 05 — Implementation plan (sequence, ownership, tests, acceptance, first slice)

**Status:** design only. Nothing starts before the owner signs `01_README_AND_GATE.md` and, for Phase 1A, explicitly
authorizes adding read-only modules to the frozen product (3844540 → a new certified revision).
**Rework rule:** no cockpit surface is built on temporary plumbing. Every surface reads the contracts in `04` from the
start; the shell (Phase 2) is built only after Phase 1A/1B deliver those contracts.
**Standing rules apply unchanged:** operator-controlled mode (the operator runs pytest/gates), review-first, strict
doubles, pylint + ruff at zero, mutation tests for decision logic, one independent review before any merge or push,
no push without explicit approval, main-only branching (one workstream branch at most).

---

## 1. Dependency graph

```
Phase 0  design gate (this folder)                                   owner
   │
Phase 1A Kriya-side read façade (04 §4.1–4.10, §5, §6)              product change, authorized separately
   │   └─ slices 1A-1 … 1A-5 (below), each a certified revision candidate
Phase 1B shared UI types, capabilities, strict-read tests, fixtures   ui/kup, ui/shared, ui/test-host
   │
Phase 2  cockpit shell: toolbar, rail, Runs pane, Workspace, Settings, run header, trust strip, timeline tree, inspector levels
   │
Phase 3  Overview, Context, Plan, Changes, Validation, Attempts/Compare, Evidence, WHY, drawer (Build/Tests/Git/Raw, Model I/O)
   │
Phase 4  Knowledge / Models / Tools (each needs its own read ops; separate topic each)
Phase 5  Experiments (separate topic; harness evidence read contract)
Phase 6  optional explicit phase observability (04 §7; needs workflow.py unfrozen)   ── independent of 2–5
Phase 7  live observation (separate topic: ui-server / live transport)
Phase 8  controlled actions (separate topic: action contracts)
```

No phase depends on a later one. Phase 6 depends on nothing in the GUI and nothing in the GUI depends on it.

## 2. Phase 1A — Kriya-side read façade (product change, read-only)

**Module ownership (new files only; no edits to workflow, agents, policy, tools, control):**

| File | Role |
|---|---|
| `kriya/kup/evidence_ops.py` | `evidence.manifest/verify/records/blob/candidate/explain/compare` over `kriya/core/attempt_evidence/reader.py` only (invariant I-1) |
| `kriya/kup/observe_ops.py` | `observe.qualification` over `model_qualification.load_record`; `observe.authority` over `authority_approval` verify/inspect helpers |
| `kriya/kup/policy.py` | capability-set 1.1 constants (schema version stays integer 1): operations, error codes, limits, `sealed_store` consistency kind, capture/verification vocabularies, blob class table |
| `kriya/config/config.py`, `kriya/config/authority.py` | new field `evidence.attempt_recorder.content_visibility` (off/on, default off), classified SECURITY_AUTHORITY (the only config change; no workflow change) |
| `kriya/kup/cli_ops.py` | dispatch of the new operations; `cockpit.run` composition |
| `kriya/kup/inspect.py` | `capabilities()` features incl. `capability_set: "1.1"`; additive `enforce_run_id` column in list/detail |
| `kriya/cli.py` | `--json --kup-version 1` on `evidence manifest/verify/records/blob/candidate/explain/compare`, `model qualification-status`, `authority inspect`; `traces … --cockpit` |

Invariants to keep: existing CLI output byte-identical without the KUP flags (D-3 test); no new outbound client,
subprocess or filesystem write (inventory tests `test_prd012_network_inventory`, `test_platform_architecture_guard`,
`test_file_integrity_contract_001/001b` must pass unchanged); `kriya/workflow` never imports `kriya/kup`;
`SECURITY_AUTHORITY` gains exactly one field, `content_visibility`, classified before use; `tests/test_sec009_*` /
`test_config_authority` tripwires (unlisted field fails closed) must pass.

**Slices (each: implement → self rule-22 check → targeted tests → lint → local commit on the workstream branch):**

| Slice | Delivers | Key tests |
|---|---|---|
| 1A-1 | `policy` constants, `capabilities` features/limits, `evidence.manifest`, `evidence.verify` envelope. Lock-step edit sites for every new code/kind: `kriya/kup/policy.py`, `ui/kup/src/index.ts`, `ui/kup/schema/common.schema.json`; tripwires that must stay green: `tests/test_kup_cli.py` (every `ERROR_CODES` entry produced in-test; `OPERATIONS` set equality; `kup_versions == [1]`), `ui/kup/test/contract.test.ts` (`errors/<code>.json` per code); add a Python↔schema enum parity test | absent store, dot-prefixed dir, unsealed, every verify status, `evidence verify --json` without `--kup-version` byte-identical (`tests/test_kup_cli.py::test_text_mode_is_byte_identical_to_the_baseline_golden` pattern) |
| 1A-2 | `evidence.records` paging/filtering, cursor + `EVIDENCE_HEAD_CHANGED` | filters conjunctive, kind validation, truncated tail, RESPONSE_TOO_LARGE page, head change |
| 1A-3 | `evidence.blob` cap/range/digest/content policy + blob class table; `evidence.candidate` modes; the `content_visibility` config field | ref regex, unreferenced ref, cap, range, digest mismatch, each gated class refused with the field off, unclassified key always refused, class-table-covers-every-writer-key tripwire (mutation: flipping the field flips `evidence_content`; removing a class row fails the tripwire) |
| 1A-4 | `evidence.explain`, `evidence.compare` envelopes | NOT_APPLICABLE vs NOT_RECORDED preserved; compare refuses on unverified store |
| 1A-5 | `observe.qualification`, `observe.authority`, `cockpit.run` composition, `enforce_run_id` column | member error envelopes inside the map; redaction preserved; list/detail schema additive |

**Phase 1A acceptance:** all slices merged on the workstream branch; full suite green (operator); pylint/ruff zero;
one independent review of the whole diff; a certified revision recorded in the registry; `kriya doctor` unchanged.

## 3. Phase 1B — UI contract layer (no Electron features yet)

| Item | Owner | Tests |
|---|---|---|
| `ui/kup/schema/evidence.schema.json`, `observe.schema.json`; capabilities/common extended | ui/kup | `npm run generate -- --check` drift; `contract.test.ts` |
| golden fixtures from a sealed store the **test suite's recorder** writes (`tests/_kup_fixtures.py` extension) — never a live run | ui/fixtures + tests | `fixtures:serializer:check`; Java round trip |
| `ui/shared/src/model/evidence.ts`, `availability.ts` flags, `selection.ts` additive extension (02 §5; M1 fields/actions kept until parity retirement) | ui/shared | reducer tests for every transition in 02 §5 and §16 |
| `ui/standalone` argv entries, cache policy (04 §8), `evidenceContentVisible` setting | ui/standalone | argv allowlist test; cache never stores live_observation or blobs |
| evidence checker rules EVC-EVD-* | ui/kup tools | evidence_check tests |

Acceptance: `npm run check` and `KRIYA_PYTHON=… npm run check:integration` green; test host renders every panel under
every flag combination (fixtures only).

## 4. Phase 2 — cockpit shell

Toolbar, rail (Runs/Workspace/Settings), Runs pane with milestone grouping and status-literal chips, Workspace,
Settings, run header, trust strip (all fields of 02 §7), phase rail (DERIVED groups + sentinels), timeline tree (S2
primary, S1 degraded), inspector Summary/Evidence/Raw, focus mode, responsive rules, keyboard. Built on the shared
package; verified in the test host (fixtures) and then on a real store under the released M1 procedure
(`ui/docs/REAL_STORE_ACCEPTANCE_PLAN.md` pattern, operator-run).

Acceptance: 02 §19 items 1, 2, 5, 6, 7, 9, 10, 11, 12, 14; A11y walkthrough checklist updated and passed by the owner.

## 5. Phase 3 — evidence surfaces

Overview, Context, Plan, Changes (Mode A/B), Validation, Attempts + Compare, Evidence, WHY, drawer tabs. Each surface
ships with its loading/empty/not-recorded/unavailable/degraded/error states and its two-interaction reachability test.

Acceptance: 02 §19 items 3, 4, 8, 13; the M1 owner tasks (find a failed run, identify an omitted target, inspect its
evidence, compare recorded changes without raw logs) repeated on the cockpit with a real store.

## 6. Phases 4–8

Each is a separate topic folder with its own matrix rows, read contract and gate. Phase 6's contract is already
drafted in `04 §7` so that, when authorized, it is a producer-only change with no UI restructuring.

## 7. Exact first development slice (after the gate and the Phase 1A authorization)

**Slice 1A-1:** `kriya/kup/policy.py` v1.1 constants + `capabilities()` features + `evidence_ops.manifest/verify` +
`cli_ops` dispatch + `kriya evidence manifest` / `evidence verify --json --kup-version 1`.

Tests to write first (red before the code): `tests/test_kup_evidence_manifest.py`, `tests/test_kup_evidence_verify.py`
using `tests/_strict_doubles.strict_config` and a recorder store written by `AttemptEvidenceWriter` in `tmp_path`
(sealed, unsealed, dot-prefixed, chain-broken by editing one line, blob corrupted by truncating one `.gz`, sealed with a
truncated tail), plus the existing `tests/test_kup_cli.py` extended: `evidence verify --json` without `--kup-version`
byte-identical; the new codes produced in-test; `kup_versions == [1]` unchanged. Mutation controls: remove the
dot-prefix rule → test fails; return records for CHAIN_BROKEN → test fails; cache a live_observation → host test fails.

Hand-off to the operator for the first run: `.venv/bin/pytest tests/test_kup_evidence_manifest.py tests/test_kup_evidence_verify.py tests/test_kup_cli.py -q`
then `.venv/bin/pylint kriya plugins/core_tools tests` and `.venv/bin/ruff check .`.

## 8. Prerequisites checklist (before any code)

1. Owner signs the gate (`01` §5) PASS.
2. Owner authorizes the Phase 1A product change on top of 3844540 (new read-only modules; no workflow change).
3. Workstream branch name agreed (one branch; main-only policy).
4. Operator confirms the real-store procedure release still stands for GUI acceptance runs (it was released 2026-10-05
   for the documented M1 procedure).
5. Registry rows: one for the Phase 1A product change (target_scope, closure evidence = certified revision), one for
   Phase 6 as DEFERRED P3 with the `04 §7` contract as its design.
6. Two traces left open by the matrix (03 §4.6, §4.7) are done inside slice 1A-3 before the per-test and capacity
   surfaces are specified in Phase 3; neither blocks 1A-1.
