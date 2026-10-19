# S20 decision-to-killer table (OI-134 I-1, OI-152; STOPPED at the first golden move)

Scope delivered:
- I-1: the traces.db row is redacted at write by its one writer.
- OI-152: the endpoint display rule.

The held corrections (I-3, I-9, I-11, I-12) have committed strict-xfail pins and no production code; see
S20-PREFLIGHT.md and the stop addendum.

Mutation campaigns (`~/kriya-modularization-tools/mutrun.py` v2, sandbox = git archive, VALID):
- **20261020_043144_1e34e9c0** (rev 23c1187): 19/19 KILLED at L1. L1 was the S20 file plus the S2, S2b and S2c files;
  64 s.
- **20261020_043433_8c90280c** (rev 46260dd): 19/19 KILLED at L1. L1 was the S20 file ONLY, so every mutant is killed
  by S20's own pins; 55 s.

The first campaign exposed one own test weakness, fixed in 46260dd. S20M15 (an in-place rewrite of the caller's
`gate_outcomes`) was killed only by an S2b test, because the S20 non-mutation pin compared against the module constant
`GATES` that the same mutant had already rewritten in an earlier test. The marker check also prefix-matched marker 1
inside marker 13. The pin now compares against frozen text, and the marker match is unambiguous.

| decision (what must hold) | code | mutant | killer (S20 file, campaign 2) |
|---|---|---|---|
| a credential in the stored file list is hidden | trace.py `files_str = redact_text(...)` | S20M01 drop | `test_the_trace_row_stores_no_secret[files_modified]` |
| ... in the goal | `goal = redact_text(goal)` | S20M02 drop | `[goal]`, `test_the_trace_row_holds_the_redacted_values` |
| ... in the rendered prompt | `prompt_rendered = redact_text(...)` | S20M03 drop | `[prompt_rendered]`, `..._holds_the_redacted_values` |
| ... in retrieved chunks | `chunks_json` | S20M04 drop | `[retrieved_chunks]` |
| ... in active skills | `skills_str` | S20M05 drop | `[active_skills]` |
| ... in gate outcomes (argv, output) | `gates_json` | S20M06 drop | `[gate_outcomes]`, `..._holds_the_redacted_values` |
| ... nested, not only top level | `gates_json` | S20M14 shallow pass | `[gate_outcomes]`, `..._holds_the_redacted_values` |
| ... in model hops | `hops_json` | S20M07 drop | `[model_hops]` |
| ... in run events | `events_json` | S20M08 drop | `[run_events]` |
| ... in evidence records | `evidence_json` | S20M09 drop | `[evidence_records]` |
| ... in generation metrics | `generation_metrics_json` | S20M10 drop | `[generation_metrics]` |
| ... in the failure report | `failure_report_json` | S20M11 drop | `[failure_report]` |
| ... in a milestone plan's validation failures | `log_milestone_plan` | S20M12 drop | `[milestone_plans.validation_failures]` |
| ... in a milestone plan's topology | `log_milestone_plan` | S20M13 drop | `[milestone_plans.repository_topology]` |
| the writer never mutates what the caller passed | `log_run` | S20M15 in-place rewrite | `test_the_trace_writer_never_mutates_what_the_caller_passed` |
| an unset endpoint is shown as "" (not b'') | redaction.py `safe_endpoint` | S20M16 drop | `test_the_endpoint_display_keeps_ipv6_brackets_and_port_zero[None-]` |
| an IPv6 host keeps its brackets | `safe_endpoint` | S20M17 drop | `[http://[::1]:11434/v1-...]`, `[...2001:db8...]` |
| ... for every IPv6 form (not only `::`) | `safe_endpoint` | S20M19 `"::" in host` | `[http://[2001:db8:1:2:3:4:5:6]:1/-...]` |
| an explicit port 0 is shown | `safe_endpoint` | S20M18 `if parts.port` | `[http://h:0/v1-http://h:0/v1]` |

Pins that run before AND after S20 (governed behaviour; a correction that touched these surfaces would fail them):
- **Content-addressed bytes stay exact (Q1).** `test_the_evidence_content_blob_and_its_digest_stay_the_exact_recorded_bytes`
  (the blob equals the raw content, `content_digests` equals its sha256, and the store VERIFIES), and
  `test_a_scripted_run_writes_its_trace_row_and_keeps_the_exact_content_blobs`.
- **The approval identity and the sidecar bind the raw bytes (OI-146).**
  `test_the_review_identity_and_the_sidecar_stay_bound_to_the_raw_bytes`.
- **OI-153 positional form.** `test_a_positional_secret_stays_as_offered_for_review`.
- **No-op on non-secret text.** `test_a_trace_row_without_secrets_is_byte_identical_to_the_unredacted_serialization`;
  `test_the_trace_row_keeps_its_non_secret_structure`.

Negative controls on the unfixed bytes. At the characterization commit 0de9ac2, `--runxfail` made all 14 flips fail
(MEASURED). At 46260dd the 6 held flips fail on their secret comparison (MEASURED, `--runxfail`):
- envelope `{S}20` in records.jsonl;
- the manifest stamp "1";
- launch `{S}31`;
- launch commands raw;
- ledger note raw;
- diagnostics `S` present.

The original symptom as a permanent test: `test_a_scripted_run_keeps_no_planted_secret_in_its_trace_row`, which runs
the real pipeline and greps the traces.db bytes. It failed on 0de9ac2 (strict-xfail) and passes from ee6e9f6.
