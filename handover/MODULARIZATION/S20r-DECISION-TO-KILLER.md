# S20r decision-to-killer table (S20 remainder, TYPE D: OI-134 I-3, I-9, I-11, I-12; OI-153 part B)

Owner decisions (2026-10-20): the golden scope is approved EXACTLY. Two lines, `redaction_policy_version` "1" -> "2", in
`tests/golden/kup/application/evidence_manifest.json` and `evidence_manifest_from_trace.json`. I-3 is redacted at write in
`AttemptEvidenceWriter`. I-9 stores a redacted representation, plus the digest where an identity must stay authoritative.
I-11 and I-12 are redacted at write and are digest-neutral. Content blobs and digest-bound stores are not touched (OI-154,
S20c).

## Golden proof (MEASURED, scratch `s20r/gold`, `s20r/golden_semantic.txt`)

- **Regeneration.** `KRIYA_WRITE_KUP_GOLDEN=1` was run on the single node
  `test_application_services.py::test_every_read_side_operation_answers_the_pre_extraction_envelope`, twice. The
  snapshots of `tests/golden` after each run are byte-identical (`diff -r` empty).
- **Semantic diff.** A JSON walk of all 358 files under `tests/golden`, HEAD against the working tree, found exactly 2
  differences:
  - `evidence_manifest.json` `.data.manifest.redaction_policy_version` '1' -> '2';
  - `evidence_manifest_from_trace.json` the same field.

  `git diff --numstat tests/golden ui` showed 1/1 for each of the two files.
- **Schema.** No KUP schema change. `ui/kup/schema/evidence.schema.json` types the field as `string`, with no enum or
  const.
- **Golden predictor.** It ran after every ingress (51 golden-reading test files, plus the touched suites). Results:
  - I-3: 3,395 passed, plus 1 harness artifact (see below).
  - I-9: 3,844 passed.
  - I-11/I-12: 5,669 passed.
  - The semantic diff after I-9, I-11 and I-12 was 0.

Harness artifact (CONFIRMED, not a product defect):
`tests/contracts/test_config_resolution_contract.py::test_kup_configuration_forms_are_byte_identical_to_the_pre_d3_golden`
fails whenever TMPDIR is the session scratch path. It fails the same way at 6fd5933 (stash) and with the change; with
the default TMPDIR it passes, both alone and in the gate.

## Over-redaction census (MEASURED on the unfixed bytes, scratch `s20r/instr`)

A pytest plugin wrapped every I-3, I-9, I-11 and I-12 producer. It logged each leaf where `redact_value(x) != x`.
- **Run.** 135 test files that write attempt evidence, launch records, decisions or diagnostics: 5,449 passed, plus
  the harness artifact above.
- **Changed leaves.** 27, and every one is a PLANTED secret of an S20, S2c or S2d test.
- **Unplanted payload keys.** None changes. This includes `max_tokens`, `*_digest`, `reason_codes`, paths and argv
  without a secret flag, and every key that `explain`, `candidate_export`, `decisions` or `retention` reads.

## Mutants

`~/kriya-modularization-tools/mutrun.py` v2, campaign `20261020_115248_b0f64436` at 98c14b1:
- 31 mutants, 31 KILLED at L1, 0 survived;
- VALID: tree_diff empty, no straggler;
- 139 s.

L1 was `tests/test_s20_evidence_redaction.py`, `test_s2d_positional_secrets.py` and `test_s2c_redaction_residuals.py`.

| decision | code | mutant | killer |
|---|---|---|---|
| the envelope is stored redacted | writer `"payload": redact_value(dict(payload))` | M01 drop | `test_the_evidence_envelope_stores_no_secret` |
| ... on a copy (the caller's object is never mutated) | same | M02 in-place update | `..._keeps_its_readable_fields_and_the_caller_payload` |
| the chain binds the STORED line | `self._prev = model.digest(line)` | M03 hash the raw payload | `..._content_blob_and_its_digest...` (campaign); `test_a_freshly_written_chain_verifies...` also fails on M03 (MEASURED directly on a git-archive sandbox) |
| the stamp names the applied policy | `REDACTION_POLICY_VERSION = "2"` | M04 "1" | `test_the_evidence_manifest_names_the_applied_policy` (+ the two goldens) |
| content blobs keep the exact bytes | `data = model.as_bytes(value)` | M05 redact content | `..._content_blob_and_its_digest_stay_the_exact_recorded_bytes` |
| the gap error is redacted BEFORE the cut | `redact_text(str(error))[:500]` | M06 cut first | `test_the_gap_record_and_the_seal_store_no_secret` |
| ... at all (the append redaction alone leaks the cut prefix) | same | M07 drop | same |
| the seal's degraded reasons are redacted | `[redact_text(r) ...]` | M08 drop | same |
| approval_required reason | `launch.py` note | M09 drop | `test_a_note_s2d_does_not_write_is_stored_redacted`, `..._before_it_is_cut` |
| approval_required details | same | M10 drop | `test_a_note_s2d_does_not_write_is_stored_redacted` |
| ... redact, then cut | same | M11 cut first | `test_each_launch_note_is_redacted_before_it_is_cut` |
| declined reason / details / order | `decline_decision` | M12, M13, M14 | `test_the_launch_record_stores_no_redactable_secret`, `..._holds_the_redacted_free_text`, `..._before_it_is_cut` |
| ended.error / order | `finish` | M15, M16 | same three |
| change review reason / order | `build_pending` | M17, M18 | same three |
| `reason_sha256` binds the EXACT reason (OI-146) | `_sha(reason)` | M19 redacted | `test_the_review_identity_and_the_sidecar_stay_bound_to_the_raw_bytes` |
| `review_id` binds the EXACT reason | `review_identity(..., reason)` | M20 redacted | same |
| `reason_truncated` describes the exact reason | `len(reason) > REASON_MAX_CHARS` | M21 redacted length | `test_each_launch_note_is_redacted_before_it_is_cut` |
| a pre-S20r raw record is still projected redacted | `application/run.py _launch_view` | M22 drop | `test_s2d...::test_a_note_still_recorded_raw_is_projected_redacted_by_launch_status` |
| the ledger line is redacted | `append_to_file` | M23 drop | `test_the_decision_ledger_file_stores_no_secret` |
| the in-memory Decision stays the caller's | `record()` | M24 redact in memory | same |
| diagnostics texts / order | `_stored_text` | M25, M26 | `test_the_planning_diagnostic_stores_no_secret`, `..._redacted_before_it_is_bounded` |
| parsed plan / approved plan / validation errors / ownership / repository evidence | `persist_planning_attempt_diagnostic` | M27-M31 | `test_every_structured_field_of_the_planning_diagnostic_is_stored_redacted` |

Governed controls (green before AND after):
- non-secret payload byte-identical;
- non-secret launch notes, diagnostics and ledger line stored as given;
- the stamp-1 fixture (bytes from the writer at 6fd5933): it verifies, reads raw, is served redacted by
  `evidence.records`, and its manifest is served with "1";
- the content blob and `content_digests` stay exact;
- the review identity and sidecar stay raw-bound.

Negative controls: `--runxfail` at b7c6ba3 made all 11 S20r characterization flips fail, each on its secret comparison
(scratch `s20r/runxfail_char.txt`). The I-12 structure test was added later and fails on the pre-I-12 file
(`AssertionError: ('parsed_plan', 54)`).

## What the stores hold after S20r (MEASURED, scratch `s20r/repro`, `s20r/e2e`)

**Ingress reproducer (real producers, one marker per channel).**
- Before: 13 of 18 markers persisted raw.
- After: 2 remain, both by design or routed:
  - the review-diff sidecar (OI-146);
  - the tool-output content blob (I-4, OI-154).
- Every redacted note holds `***REDACTED***`.
- The store verifies VERIFIED with stamp "2".

**Real scripted run (part A, capture full and digest_only).**
- records.jsonl and traces.db hold no marker.
- Markers remain only in content blobs (I-4), verification contracts (I-5), checkpoints (I-6) and the RunRecord (I-7).
  All are OI-154 / S20c.
