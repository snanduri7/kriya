# kup-evidence-check: offline evidence-consistency checker (matrix-safe batch 5, 2026-10-04)

`ui/kup/tools/evidence_check.mjs` checks the structural consistency of explicitly supplied KUP v1 JSON records and
reports data-quality problems. It never changes a Kriya verdict, reconstructs missing evidence or infers a failure
cause, a timeline, success or qualification. Its classifications are observations about records, not workflow gate
verdicts.

## Usage

```
cd ui
npm run evidence-check -w @kriya-ui/kup -- --out <dir> [--overwrite] <file.json>...
# or: node kup/tools/evidence_check.mjs --out <dir> [--overwrite] <file.json>...
```

- Inputs: one or more KUP envelope files named explicitly (any operation; error envelopes included). Directories are
  never read; there is no store discovery, database access, snapshot acquisition, Kriya invocation or network access.
- Outputs, into the explicit `--out` directory: `evidence-check.json` (diagnostics, coverage, rule registry,
  unsupported checks, summary) and `evidence-check.md` (the same, readable). An existing output is refused without
  `--overwrite`; an input is never overwritten. Output is deterministic (sorted keys, stable order, no clock).
- Exit codes: `0` completed, no actionable diagnostic (informational only); `1` completed, actionable diagnostics
  written (a malformed input is one of them and stays visible in coverage and diagnostics); `2` usage or output-write
  failure, nothing written.

## Diagnostics

Each diagnostic carries: `rule_id` (stable, registered), `classification` (`structural_error`, `consistency_conflict`,
`unresolved_reference`, `ambiguity`, `informational`), `detects` (`malformed_input`, `contradiction`,
`incomplete_coverage`), `file`, `pointer` (RFC 6901, resolvable in the input unless `observed.kind` is `absent`),
`observed` (`absent` / `null` / `value`; long text is not copied, the pointer keeps it reachable), `related` pointers
with their observed values (other files included), a plain `explanation` and the rule's `basis` (the schema or
serializer the invariant is written in). Absent fields, explicit nulls and unavailable sections are kept distinct.

## Rule registry

Every rule the tool can emit is in `RULES` (exported) and printed into every output with its classification, what it
detects, applicability, required inputs and evidence basis. Families: `EVC-ENV-*` envelope and payload validity (the
generated validators), `EVC-ID-*` documented identity equalities (source vs consistency snapshot id, directory name,
payload id, consistency kind per operation, `metadata_differs`, workspace `run_active`/`exit_code`, capabilities
version), `EVC-SEC-*` section availability vs data and `fields.<col>` vs `<col>`, `EVC-EVT-*`/`EVC-TOK-*` the
serializer event shape and the documented `prompt_composition` domains, `EVC-UNK-001` unknown fields (preserved,
uninterpreted), `EVC-REF-*` documented references, `EVC-DUP-*` duplicate explicit ids in their documented scope,
`EVC-AMB-*` ambiguity, `EVC-GATE-001`/`EVC-GATE-002` gate records outside the writer inventory (informational) and conflicting result fields (ambiguity), `EVC-ORD-001` page order (informational: the schema documents no order), `EVC-VER-001`
digest format, `EVC-CONF-*` one scoped identity recorded differently across files, `EVC-INFO-001` the same run in
different snapshots (progression; never a contradiction).

Checks without a documented invariant are listed under "Not checked" in every output (`UNSUPPORTED_CHECKS`) and never
performed: cursor decoding (opaque by contract), event attempts vs `run.attempts`, gate_outcomes fields,
evidence_records linkage, generation_metrics/model_hops/failure_report shapes, chronology, vocabularies beyond the
schema enums, prompt text, digests against files, `member_ids`.

## Findings established while building it (MEASURED / TRACED against this checkout)

1. **`attribution.evidence_ids` has no documented target.** The KUP schema documents it as an array of strings and
   defines no namespace; `kriya/workflow/evidence.py::EvidenceRecord.to_dict` serializes `kind, source, attempt,
   payload, sensitivity, created_at` and no identifier, and `kriya/kup/inspect.py::history_detail` persists no
   attribution at all. The `evidence_records[*].evidence_id` fields in `ui/fixtures/generate.mjs` are fixture-invented.
   The checker therefore reports every recorded `evidence_ids` as `EVC-REF-001` "cannot resolve from supplied inputs"
   and never matches them against fixture `evidence_id` fields.
2. **Gate outcome shape** (RESOLVED in the contract-alignment batch, 2026-10-04; inventory in `GATE_OUTCOME_SHAPES.md`).
   Every production writer records `attempt, type, success (boolean), output`; `Failure.to_gate_outcome` adds the
   attribution fields, successful literals add per-site fields. The fixture generator used to emit `gate, passed,
   reason_code`, which the Inspector, run_report and run_compare read. All three now read the writers' fields (result =
   `success` only, conflicting result fields reported as ambiguous), the fixtures carry serializer-produced records
   (`ui/fixtures/serializer_gates.py`), and the checker reports shape coverage as `EVC-GATE-001` (informational) and
   conflicting result fields as `EVC-GATE-002` (ambiguity). KUP still defines no gate record, so per-type semantics stay
   uninterpreted.
3. **Fixture `workspace.status`** (RESOLVED, same batch). `kriya/kup/cli_ops.py::_workspace_status` records `exit_code` 0
   only for status `CLEAN` (3 for `RUN_ACTIVE`, else 1) and `assessment = RecoveryAssessment.to_dict()`; the generated
   fixture used to record `NO_RECOVERY_REQUIRED` with exit code 0 and an invented assessment, which `EVC-ID-007` reported.
   It now records a CLEAN assessment in the serializer's shape and is clean.

## Limitations

- Only the named files are read: a reference that resolves nowhere in them is "cannot resolve from supplied inputs",
  never a statement about the store.
- Rules rest on the KUP v1 schemas and the serializers named in the registry; nothing is inferred from field names.
- Fixture-only verification so far: no real-store record has been checked (D-9 holds).
