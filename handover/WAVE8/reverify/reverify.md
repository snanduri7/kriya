# Independent re-verification of 23 downgraded ledger rows

Baseline: repo `~/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2`, HEAD 9f41876 (kriya/ and plugins/ = verified candidate b32976245f84), Python 3.14.6 from its `.venv`.
Isolation: every probe ran with `HOME`, `KRIYA_STATE_DIR`, `KRIYA_LOG_DIR`, `KRIYA_AUTHORITY_HOME`, `KRIYA_MCP_APPROVAL_HOME`, `KRIYA_QUALIFICATION_HOME`, `KRIYA_CERTIFICATION_HOME`, `KRIYA_STATIC_ANALYSIS_HOME`, `KRIYA_ADJUDICATION_HOME`, `KRIYA_INSTALL_DIR` pointed under `$SP` (`$SP/env.sh`), `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=<repo>`. `kriya.__file__` and `plugins.core_tools.__file__` printed the intended repo. Note: the state-path module is `kriya/core/state_paths.py` (not `kriya/config/`). `git status --porcelain --ignored` before and after the run is identical; no file under the real `~/.kriya` is newer than the run start; no file in any repo is newer than the run start. No pytest was run. Probe scripts: `$SP/p78.py`, `p80.py`, `p90.py`, `p153.py`.
Harm classes used below: AUTHORITY, SECURITY, EVIDENCE, CORRECTNESS (wrong decision or lost context), REPORTING, DURABILITY/HYGIENE, EFFICIENCY, TEST-HARNESS.

---
## OI-07 is_local_url and 203.0.113.0/24
1. Reproducer: `is_local_url` on `http://203.0.113.5`, `198.51.100.1`, `192.0.2.1`, `198.18.0.1`, `10.0.0.1`, `169.254.169.254`, `[fc00::1]`, `[2001:db8::1]`, `0.0.0.0`, `240.0.0.1` all return True; `100.64.0.1`, `192.88.99.1`, `8.8.8.8`, `[::ffff:8.8.8.8]` return False. Code: `kriya/policy/egress.py:49-85` accepts `is_loopback or is_private or is_link_local`; the module docstring (lines 28-32) defines it as "the local/private-address definition".
2. Verdict: INTENTIONAL (contract = private-network semantics, not "loopback only"). The documentation ranges are not routable on the public internet and are accepted for exactly the reason RFC1918 and link-local are; a hostile path would need the operator's own network to route them, which is already the accepted trust boundary. The discriminating check (does any public address pass) is negative: 8.8.8.8 and 4-in-6 public are refused.
3. Fix files if ever tightened: `kriya/policy/egress.py` only. No overlap with correction owners.
Side note: one probe line used `127.0.0.1.nip.io`, which made `socket.getaddrinfo` perform a DNS lookup (no connection). That is the only external touch.

## OI-68 approval.decision names no candidate digest
1. Traced: `_record_approval_decision` (`kriya/workflow/workflow.py:1666`) records `{outcome, triggers[, reason_code]}`. Only consumers of `approval.decision`: `kriya/metrics/derive.py:117` (outcome counts) and `kriya/metrics/evidence.py:49` (allowed detail keys). No gate reads it. Registry row `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001` (BACKLOG_REGISTRY.csv:271) is DEFERRED by a LATER owner decision (Wave-2 Step 9, 2026-10-29: "no new field if audit-only; revisit only if a consumer ever reads approval.decision to make a decision, or the trace must show WHICH bytes were approved"). The ledger text "owner APPROVED slice R4 (2026-10-19)" is older and is superseded.
2. Verdict: INTENTIONAL/FUTURE (owner deferral with an explicit revisit condition). The live gates bind the approval to bytes (held-gate guard, terminal commit binding), so no stale approval is applied. Residual: the ledger row still says OPEN/R4 and contradicts the registry (a ledger-consistency item). The evidence cannot show which bytes were approved: that is the accepted gap.
3. Fix files: `kriya/workflow/workflow.py`, `kriya/workflow/stage_apply.py`, `kriya/metrics/evidence.py` (allowlist). Not a correction owner (kriya/core/attempt_evidence is only a mirror of the event).

## OI-77 open findings replacement not order-aware, not checkpointed
1. Traced: `update_open_findings` (`kriya/workflow/open_findings.py`) always returns `[finding]` for an authoritative candidate failure, so `MAX_OPEN_FINDINGS = 3` can never be reached by accumulation. Called at `retry_strategy.py:550`. `git grep open_findings` shows no reference in `checkpoint.py`, `resume_support.py`, `resume_fingerprints.py`, `subtask_checkpoint.py`: the set lives only in `GenerationState` (`state.py:364`) and is lost on resume. Same replacement behavior reproduced by the OI-78 probe below.
2. Verdict: STILL_REAL, harm class CORRECTNESS (lost repair context, costs an extra attempt). No authority harm: findings are advisory prompt context; gates re-run on every candidate and every terminal gate is authoritative. Registry's own caveat (no measured causal path on a fresh non-resumed run) is honest; the ordering half is a deterministic property of the code, the resume half is TRACED.
3. Fix files: `kriya/workflow/open_findings.py`, `retry_strategy.py`, `checkpoint.py`/`resume_support.py`. No overlap.

## OI-78 semantic-safety rejection lost after no_progress_retry
1. Reproducer (`$SP/p78.py`): `update_open_findings([], Failure(type="unsafe_exception_masking", ...), 3)` gives `['unsafe_exception_masking']`; then `update_open_findings(that, Failure(type="no_progress_retry", source="orchestrator"), 4)` gives `['no_progress_retry']` and `is_candidate_finding("no_progress_retry")` is True (category UNCLASSIFIED, authority defaults to "authoritative"). The rendered "Still unresolved" block then contains only the orchestrator text. This contradicts the module docstring ("every other failure type ... leaves the set unchanged"). Producers of `no_progress_retry`: `repair_orchestrator.py:867`, `candidate_transaction.py:584`, both `source="orchestrator"`.
2. Verdict: STILL_REAL, CORRECTNESS. The guard itself holds (`candidate_transaction.py:1713-1721`, terminal `semantic_safety_refusal`), so no masking edit can be committed: no authority or security harm. The loss means the Developer request drops the prohibition and the model can repeat the masking (live S4_A v6f attempts 5, 7-11, per registry). The "authority/security" type in the ledger overstates the harm; "retry-context correctness" is accurate.
3. Fix files: `kriya/workflow/open_findings.py` (add `no_progress_retry`/orchestrator-sourced failures to the non-candidate set), `retry_package.py`. No overlap.

## OI-80 triage sends Kriya's own budget notice to the fallback model
1. Reproducer (`$SP/p80.py`): `attribute_failure(Failure(type="time_budget_exhausted", source="orchestrator", message="remaining_seconds=226.8 ..."), ["a.py","b.py"], 1, [], FakeLLM, ...)` makes exactly 1 `llm.complete` call, user prompt starting `=== Failure ===\nremaining_seconds=226.8 ...`, returns tier `full_set`. Reachability TRACED: `retry_strategy.py:600-612` sets `environment_failure` for this type, but no return precedes the `attribute_failure` call at `retry_strategy.py:1140` (only awk-scanned returns: none depend on `environment_failure`). `attribution.py:1225` gates triage only on `len(known_files) > 1`; the comment above it ("skips triage for a genuinely unresolvable failure") is not implemented.
2. Verdict: STILL_REAL, EFFICIENCY (a wasted ~20 s model call after the stop is decided; the sent text is Kriya's own notice plus candidate source excerpts to a model the run already uses; no authority/evidence harm, result is discarded).
3. Fix files: `kriya/workflow/attribution.py` or `retry_strategy.py`. No overlap.

## OI-82 managed-service env-state binding
1. Traced: `runtime_verification.py:1219` reads `validator.python_environment_error` for every `invalid_reason`. The only setter is `validate.py:1068` (resolver); reset at `validate.py:1050-1056` on each resolver return; consumers `validate.py:2125/2365/2398` all report typed and callers stop. Contract-shape refusals return before the resolver, so a value is only visible if an earlier gate in the same per-attempt validator set it and did not already stop.
2. Verdict: FAIL_CLOSED_RESIDUAL. Worst case is a typed environment stop with the `MANAGED_SERVICE_CONTRACT_INVALID` reason suppressed. No authority/evidence/durability harm; no reproducer of live reachability (registry asks for one). Not reproduced.
3. Fix files: `kriya/workflow/runtime_verification.py`, possibly `kriya/tools/validate.py`. OVERLAPS correction owners (runtime_verification.py, validate.py).

## OI-84 LEAK-SIGKILL-RECOVERY-GAP residual (proxy and per-run network)
1. Traced (no Docker run): `containment_oci.py:1684-1710` creates `kriya-acq-net-<id>` and `docker run -d kriya-acq-proxy-<id>` (no `--rm`); `owner_death_cleanup` is set only at lines 1592 and 1882 and contains only `docker rm -f <container_name>` (the `kriya-oci-` container). The proxy and network are removed only in the in-process `finally` path, so a SIGKILL strands them. `production_doctor.py:224-227` lists only `kriya-oci-*`, so a stranded `kriya-acq-*` is invisible to doctor. The effect-ownership half is fixed (`posix_process_control.py:43`, watchdog `posix_owner_watchdog.py:52`).
2. Verdict: STILL_REAL (residual half), DURABILITY/HYGIENE: a running allow-list squid container (`--cap-add SETUID/SETGID`, no ports/mounts) and a network survive indefinitely (the ledger's own instance lived 3 days and corrupted host Docker leak checks of later runs). No authority, evidence or workspace effect. Already re-prioritized P3 by the owner. Not reproduced here (no Docker).
3. Fix files: `kriya/tools/containment_oci.py`, `kriya/production_doctor.py`, `kriya/platform/*`. No overlap with the listed owners.

## OI-85 Windows filesystem/git fidelity
1. Traced: autocrlf facet fixed by W3-2: `kriya/platform/__init__.py:27-42` pins `core.autocrlf=false`, `core.eol=lf`, null attributes file in the canonical git argv. Windows is refused at every entry: `kriya/platform/services.py:76-80` `entry_refusal()` returns `PLATFORM_WINDOWS_UNSUPPORTED` when `family == WINDOWS`. Registry `PLAT-WINDOWS-FS-GIT-001` (BACKLOG_REGISTRY.csv:46) routes the remainder to the Windows platform phase.
2. Verdict: INTENTIONAL/FUTURE (no Windows provider exists; unsupported platform is a typed refusal, so no Windows run can reach the missing fidelity).
3. Fix files: `kriya/platform/*`, `kriya/repository/git.py`. Not an owner.

## OI-87 trace runs.attempts is a retry counter
1. Traced: `kriya/workflow/run_lifecycle.py:175` stores `attempts = retry_count + best_of_n_candidates_tried`, which is 0 for a run that succeeds on its first attempt; `kriya/cli.py:4940-4960` prints it under `ATTEMPTS`. UI no longer presents it as an attempt count (`presentation.ts` uses recorded retry facts). Metrics use per-role rows, not `runs.attempts`.
2. Verdict: STILL_REAL, REPORTING (a user-visible CLI column misstates attempts; no decision consumer found).
3. Fix files: `kriya/workflow/run_lifecycle.py`, `kriya/cli.py` (and trace schema if a new `attempts_made` is added). No overlap (kriya/application/run.py:282 is an unrelated `in_progress` marker).

## OI-90 "Runtime verification failed" for an expected non-zero exit
1. Traced the RECORDING first: `verifier_evidence.apply_runtime_disposition` (`verifier_evidence.py:558-611`): for a non-zero exit the goal declares (`_declared_exit_matches`) and an application step that started, `deterministic` stays None (reason `EXPECTED_NONZERO_EXIT_GROUNDED`), the grade stays passed, and the disposition `{deterministic: "NONE", deterministic_reason: ..., final: "PASS", ...}` is attached. `runtime_verification.py:1828-1836` then records the gate with `"success": True`, the real `steps` (exit_code 2) and `runtime_disposition`. So the recording is correct: gate.success=True AND the grounded disposition are both persisted. The projection `kriya/application/history.py:101` (`succeeded = gate.success is True and exit_code == 0 and not timed_out`) never reads `runtime_disposition` (grep `disposition` in history.py: none). Reproducer (`$SP/p90.py` plus a second run with the faithful gate shape including `runtime_disposition.final = PASS` and `deterministic_reason = EXPECTED_NONZERO_EXIT_GROUNDED`): `application_outcome(...)` returns `succeeded: False` in both.
2. Verdict: STILL_REAL, REPORTING/EVIDENCE: the projection contradicts a correctly recorded passed gate and its own grounded disposition, so the result screen says "Runtime verification failed" for a run whose authoritative verdict is pass. It does not change any verdict or commit.
3. Fix files: `kriya/application/history.py` (carry the disposition into the projection), then KUP schema/UI (`ui/kup/schema`, `presentation.ts`). The recording side needs no change, so none of `runtime_verification.py` / `kriya/kup/execution.py` is required unless the KUP envelope shape changes.

## OI-91 history snapshot stale on reopen
1. Traced: `ui/shared/src/cockpit/core/context.tsx:103-118` pins the newest published snapshot at start. The Runs page has an as-of stamp and an explicit "Refresh runs" button (`modules/runs/index.tsx:74`); the Project-home Recent runs card (`RecentRuns.tsx:17-45`) names the snapshot staleness ("as of its last history snapshot ... or after that snapshot, does not appear here") ONLY when the list is empty. With a non-empty list, a run recorded later by another process is absent with no note on that card. The registry plans a fix (S11), so the current behavior is not the contract. Vitest not run.
2. Verdict: STILL_REAL, REPORTING (TRACED, UI not executed): an incomplete history can be shown as current on the card, mitigated by the Runs page as-of + Refresh and by the digest-verified, explicitly pinned snapshot. No authority/evidence/commit effect. Same class as OI-87 and OI-90.
3. Fix files: UI only (`context.tsx`, `RecentRuns.tsx`). No overlap.

## OI-95 plan form: REFACTOR_BASELINE_MISSING
1. Traced: `plan_validation.py:1229-1245` rejects blank `refactor_baseline` for `kind=refactor`; first planner example is `"refactor_baseline": null` (`agents/agent.py:472`, `planning_request.py:356`); a typed repair hint exists (`planner_repair.py:546`). Registry reports 7/7 live occurrences (not re-measured).
2. Verdict: FAIL_CLOSED_RESIDUAL. The invalid plan is rejected before any mutation and repaired through a typed hint; cost is a planner repair cycle. No authority/evidence harm. Border case with OI-80 (both are avoidable-cost defects); the difference is that here the product refuses correctly.
3. Fix files: `kriya/agents/agent.py`, `planning_request.py`, `plan_validation.py`/`plan_schema.py`. No overlap.

## OI-103 launch records never pruned
1. Traced: `kriya/control/retention.py` contains no launch handling (grep `launch` in the file: none; it prunes run records, commit evidence and milestone sidecars). Launch records now live at `<authority home>/launches/<workspace identity>/<id>.json` (`human_decision_store.py:5-45`), outside any retention path.
2. Verdict: STILL_REAL, DURABILITY/HYGIENE: unbounded growth of small records (each holds a redacted command and notes) in the operator authority home; dangling pointers are handled (`found:false`). No correctness/authority break.
3. Fix files: `kriya/control/retention.py`, `kriya/control/human_decision_store.py` / `launch.py`. No overlap with `control_store.py` unless writes are routed through it.

## OI-107 leak-check false LEAKED on shared lines
1. Traced and probed: `kriya/core/attempt_evidence/leak_check.py:62-64` `hidden_only_units` subtracts only lines of operator-named public files, by WHOLE stripped-line equality, while detection (`check_run`) is substring (`unit in text`). Probe: hidden `assert calc(2,3) == 5` with the goal text `Example: assert calc(2,3) == 5` passed as `--public` still yields the hidden unit `assert calc(2,3) == 5` (the goal line differs as a whole line), so a goal sentence that EMBEDS a hidden line cannot be subtracted by naming goal.txt `--public`; the workaround only works for lines identical as whole lines. More subtraction can only remove hits, so the error direction is false-LEAKED; CLEAN still requires the positive control (a public unit found in a model-facing blob).
2. Verdict: FAIL_CLOSED_RESIDUAL (no false-CLEAN path found; tooling noise in the safe direction, and unlike the earlier worker's note the operator workaround is incomplete, which is why the registry fix, subtracting goal text and shown files by substring, is still wanted).
3. Fix files: `kriya/core/attempt_evidence/leak_check.py`, `kriya/cli.py`. OVERLAPS correction owner `kriya/core/attempt_evidence/`.

## OI-108 Electron native memory drift
1. Traced: `ui/standalone/src/main/soak.ts` reports `ceiling_check.pass` (550 MB) but `sustained_growth.slope_MB_per_hour_after_warmup` has no pass/fail threshold. Product drift is unmeasured (requires the 2-hour soak, not run).
2. Verdict: UNKNOWN for the product claim (a single soak on the final candidate decides). The harness gap (no slope threshold) is TRACED and real but TEST-HARNESS only. No authority/evidence harm.
3. Fix files: `ui/standalone/src/main/soak.ts`. No overlap.

## OI-110 ResourceWarning hygiene
1. Traced: `pyproject.toml:136-139` errors only on un-awaited coroutines and `PytestUnraisableExceptionWarning`. `grep -rn "with sqlite3.connect(" kriya plugins` = 0, in `tests/` = 44 (the idiom named by the registry is test-only; the context manager commits but does not close). The ~70 count is not re-measured.
2. Verdict: STILL_REAL as a TEST-HARNESS gap (leak detection blind to ResourceWarning). Whether any of the ~70 hides a product leak is UNKNOWN; deciding check: `pytest -W error::ResourceWarning` over the leaking test set and attributing each to product vs test code (full-suite run, not done here).
3. Fix files: tests and `pyproject.toml`. No overlap.

## OI-111 recovery does not reopen the upstream owner
1. Traced: `retry_strategy.py:1189-1230` sets `plan_scope_conflict` only for `DETERMINISTIC_ATTRIBUTION_TIERS` or `misdirected_edit` (PRV-11, deliberate); otherwise `retry_strategy.py:1440-1475` stops with typed `NO_AUTHORIZED_REPAIR_TARGET`. Which tier produced the implication in C2-S4_A is UNKNOWN (sealed store not available).
2. Verdict: FAIL_CLOSED_RESIDUAL (typed stop, no false success, per registry s1's exported candidate equals untouched base). Liveness/recovery quality, not authority.
3. Fix files: `kriya/workflow/retry_strategy.py`, `attribution.py`. No overlap.

## OI-112 terminal requirement failure has no repair re-entry
1. Traced: `terminal_gate_service.py:283-360`: terminal gates run once after the last unit; `original_requirements` failure sets `commit_eligible` False; no re-entry into repair exists in `plan_executor.py` or the service (grep). A bounded closure mechanism (`closure_attempts`) is separate.
2. Verdict: INTENTIONAL/FUTURE (a new recovery capability: owner decision 2026-10-19 describes it as new design, TR1; today fail-closed NOT_COMMITTED). The REQ-6 NBSP/EM-SPACE verifier symptom itself was not traced to a verifier bug: UNKNOWN.
3. Fix files: `kriya/workflow/terminal_gate_service.py`, `plan_executor.py`, possibly `requirement_closers.py` (OVERLAP with a correction owner if closers change).

## OI-113 enforce loop vs execute_plan convergence
1. Traced: `enforce_loop.py` (3,919 lines) has its own ControlState/resume/skip; the terminal decision still goes through `deps.TerminalGateService` (`enforce_loop.py:3497-3527`, `gate_report.commit_eligible`). Registry closure_evidence: Gate F audit class B.
2. Verdict: INTENTIONAL/FUTURE (architecture convergence PRD; one authority for commit eligibility).
3. Fix files: `kriya/workflow/enforce_loop.py`, `plan_executor.py`. No overlap.

## OI-115 false-premise disposition does not guard mutation
1. Traced: `REJECTED_FALSE_PREMISE` has no consumer outside `requirement_disposition.py`, CLI and authority option text; the module docstring defines a disposition as obligation removal only. Independent terminal/verification gates are the safety net; the registry reports D1 blocked the false SUCCESS.
2. Verdict: INTENTIONAL/FUTURE (scoped negative constraint is a new, owner-designed capability). Residual INFERRED only: a premise-named file whose behavior no test covers could be mutated and pass with the statement reported DISPOSITIONED (never verified).
3. Fix files: `kriya/workflow/requirement_disposition.py`, plan validation, `enforce_loop.py`. No overlap.

## OI-153 secret-bearing runtime command review
1. Reproducer (`$SP/p153.py`, 52 argv forms with a marker secret, `commands_carry_secret` plus `redact_value`): all of mysql `-p<pw>` / `--password`, sshpass `-p`, docker/podman login `-p` incl. `--config`/`-H` globals, npm `config set` / `set` / `-g` / `--location`, and the same behind sudo, env, `docker exec`, `kubectl exec --`, xargs, timeout, nice, ssh, `sh -c`, `bash -c`, `bash -lc`, `env -S`, absolute path, command, nohup: carry=True, secret absent from redacted output (0 wrapper leaks over 15 wrapper forms). Leaks (carry=False, secret unchanged): opaque-evaluator indirection (`M=mysql; $M -p..`, `my''sql`, `python -c`: lexical-only by owner decision), commands outside the closed table (`mysqldump`, `aws configure set`, `htpasswd -b`, `redis-cli -a`, `mongo -p`), and `mysql -vp<pw>` (bundled short options on a TABLE command; pinned as a documented limit at `tests/test_s2d_positional_secrets.py:254-260`; `redaction.py:236-240` `_mysql` matches only words starting `-p`). `grep RUNTIME_REVIEW_SECRET_PRESENT|RUNTIME_COMMAND_SECRET_UNREVIEWABLE kriya ui/kup tests` = no match. mysql is not installed here, so whether the real client accepts `-vp<pw>` is UNKNOWN (deciding check: run `mysql -vp<x> ...` against the real client or read its option parser; if it accepts the cluster, the owner's closure rule makes this a STILL_REAL bypass that may not be documented away).
2. Verdict for OI-153 proper: FAIL_CLOSED_RESIDUAL for the typed reason code being absent (the refusal is a fixed-text `approval_required` kind `runtime_command`, `cli.py:2953-2960`, `review.py:218-222`; nothing is disclosed), with UNKNOWN sub-items `mysql -vp<pw>` (above) and P4 (reviewed bytes equal executed bytes; not started). The ledger row contradicts itself on whether the typed code is approved or still pending an owner golden/KUP-schema decision. The closure condition "confirmed MEASURED wrapper bypass" is NOT met by anything I measured.
   ADJACENT FINDING (ADJ-1, separate from OI-153 so fixing one cannot close the other; the OI-144 flag-grammar family, recon chunk 5 NEW-1, independently reproduced): `curl --user=u:<pw>` and `curl --proxy-user=u:<pw>` give `commands_carry_secret = False` and `redact_value` leaves `<pw>` unchanged (`redaction.py:114` `_USER_FLAGS` is applied only to a separate value); `curl -uu:<pw> http://x` gives carry=True because the redactor wrongly hides the URL word while keeping `<pw>`. Consequence TRACED in `cli.py:2955-2957`: for the `-uu:` form the refusal path builds `shown = " ".join(redact_value(list(c)))`, which still contains `<pw>`, and passes it to `note_approval_required` and the terminal (so the persisted launch-record command and the terminal line would carry it; launch-record write not executed end to end: INFERRED); for the `--user=` forms review is OFFERED with the raw command (INFERRED persisted raw, same path). Verdict ADJ-1: STILL_REAL, SECURITY (secret disclosure on display/launch-record channels), needs its own ledger row.
3. Fix files: ADJ-1 -> `kriya/foundation/redaction.py` (OVERLAPS correction owner). OI-153 residual -> `kriya/policy/approval.py` (REFUSAL_CODES; golden `tests/golden/policy_approval/serialized_forms.json`) or `kriya/application/history.py` + `ui/kup/schema/history.schema.json` (BLOCKED_REASON_CODES), `kriya/cli.py`.

## OI-158 qualification completeness invariant
1. Traced and read: the premise (a partitioned NORMAL/GAP profile run leaving 2,744 tests unowned) is not code on this baseline: `git grep qualrun|PROFILE_COMPLETENESS` over py/sh/yml = no match (markdown only). The candidate b329762 was gated by a full census, not by profiles: `handover/evidence/PRD-036/rebuild-rc11-VERIFIED/hosted/certification-summary.json` records stage `collection` "PASS: 20204 selected / 45 deselected" and stage `pytest` "[shards] PASS: 0 failed, 20201 passed, 3 skipped, 0 errors across 4 shards (merge audit OK)"; the README lists gate criteria `hosted_mandatory_tiers_passed` and `hosted_ci_at_candidate` (gate-rc11.json VERIFIED 16/16). The merge audit proves union == census, shards disjoint, one result per node (`scripts/certification_shards.py:10-11,110-113`; ci.yml:183-190). `ci.yml:42,147` run plain `pytest -q`.
2. Verdict: DISPROVED for the candidate-level requirement ("NORMAL union GAP equals FULL with zero unowned nodes, machine-checked"): that is exactly what the VERIFIED gate evidence shows. Caveat: the SLICE-promotion practice that used preservation/adjacent profiles (OI-153/OI-84 verification notes) is a process matter of the modularization workflow, not product code; the advisory classifier and a GLOBAL_INVARIANT profile are an unbuilt process improvement (INTENTIONAL/FUTURE, QX1), not a coverage hole in what gates a candidate.
3. Fix files: none for the candidate gate; any slice-process tool would be new scripts. No overlap.

## OI-160 verification-surface completeness plan
1. Traced: plan/process item. One fact worth the owner's attention: `grep -in "npm\|node\|electron\|ui/" .github/workflows/ci.yml` shows no UI/Electron job (no `npm ci`, Vitest, typecheck), so those surfaces have no hosted gate on this baseline.
2. Verdict: INTENTIONAL/FUTURE (owner decision 2026-10-20, QX2; the inventory is a plan). The absence of a UI gate in CI is the thing the plan exists to close; it is a coverage gap, not a defect in a decision.
3. Fix files: `.github/workflows/ci.yml`, `scripts/`. No overlap.

---
## Summary

| id | verdict | overlaps a correction owner? | harm class | one-line reason |
|---|---|---|---|---|
| OI-07 | INTENTIONAL | no | none | private-address semantics in the egress module docstring; docs ranges unroutable; public addresses refused (no test pins the doc ranges) |
| OI-68 | INTENTIONAL/FUTURE | no | EVIDENCE (audit-only) | later owner deferral with revisit condition; ledger row stale vs registry |
| OI-77 | STILL_REAL | no | CORRECTNESS | replacement unconditional; open set not checkpointed |
| OI-78 | STILL_REAL | no | CORRECTNESS | reproduced: orchestrator `no_progress_retry` evicts the masking finding; guard still holds |
| OI-80 | STILL_REAL | no | EFFICIENCY | reproduced: 1 triage call on `time_budget_exhausted` |
| OI-82 | FAIL_CLOSED_RESIDUAL | yes (runtime_verification.py, validate.py) | none | stale value unreachable on traced paths; worst case typed env stop |
| OI-84 | STILL_REAL | no | DURABILITY/HYGIENE | proxy and net not in `owner_death_cleanup`; doctor blind to `kriya-acq-*` (TRACED, no Docker) |
| OI-85 | INTENTIONAL/FUTURE | no | none | autocrlf fixed; Windows refused with typed entry refusal |
| OI-87 | STILL_REAL | no | REPORTING | CLI ATTEMPTS = retry counter (0 on first-attempt success) |
| OI-90 | STILL_REAL | no (history.py; recording is correct) | REPORTING/EVIDENCE | reproduced: correctly recorded passed gate + grounded disposition projected as `succeeded: False` |
| OI-91 | STILL_REAL | no | REPORTING | Recent-runs card discloses staleness only when empty (UI not run) |
| OI-95 | FAIL_CLOSED_RESIDUAL | no | EFFICIENCY | validator rejects before mutation, typed repair hint |
| OI-103 | STILL_REAL | no | DURABILITY/HYGIENE | launch records have no retention at all |
| OI-107 | FAIL_CLOSED_RESIDUAL | yes (core/attempt_evidence/) | none | false-LEAKED only (whole-line subtraction vs substring detection; workaround incomplete) |
| OI-108 | UNKNOWN | no | TEST-HARNESS | product drift needs the 2 h soak; harness has no slope threshold |
| OI-110 | STILL_REAL (test-harness) | no | TEST-HARNESS | 44 test `with sqlite3.connect` leaks, 0 in product; product leaks among ~70 UNKNOWN |
| OI-111 | FAIL_CLOSED_RESIDUAL | no | none | typed stop, root cause UNKNOWN, PRV-11 by design |
| OI-112 | INTENTIONAL/FUTURE | possible (requirement_closers.py) | none | new capability; fail-closed NOT_COMMITTED |
| OI-113 | INTENTIONAL/FUTURE | no | none | architecture PRD; single commit-eligibility owner |
| OI-115 | INTENTIONAL/FUTURE | no | none | disposition contract = obligation removal; gates are the net |
| OI-153 | FAIL_CLOSED_RESIDUAL (+UNKNOWN: mysql -vp, P4) | yes (policy/approval.py, KUP schema if typed code added) | none | wrapper bypass NOT reproduced (0/15); typed code absent; mysql -vp parser unverified |
| ADJ-1 (new, from OI-153 probe) | STILL_REAL | yes (redaction.py) | SECURITY | `curl --user=u:pw`/`--proxy-user=` unredacted and review offered; `-uu:pw` hides the URL, keeps the pw |
| OI-158 | DISPROVED (candidate gate); slice-profile process = INTENTIONAL/FUTURE | no | none | rc11 gate evidence = full census 20204 + merge audit; no qualrun code |
| OI-160 | INTENTIONAL/FUTURE | no | none | plan item; note no UI job in CI |

Counts (23 ledger rows): STILL_REAL 8 (OI-77, 78, 80, 84, 87, 90, 91, 103) plus OI-110 as a test-harness STILL_REAL = 9; FAIL_CLOSED_RESIDUAL 5 (OI-82, 95, 107, 111, 153); INTENTIONAL/FUTURE 7 (OI-07, 68, 85, 112, 113, 115, 160); DISPROVED 1 (OI-158); UNKNOWN 1 (OI-108). Plus one new adjacent finding ADJ-1 (STILL_REAL, SECURITY). No STILL_REAL ledger row lets an unauthorized write, commit or approval happen; the only security-class harm is ADJ-1.

## Isolation disclosures
- One probe line used `127.0.0.1.nip.io`, so `socket.getaddrinfo` did a real DNS lookup (no connection opened).
- The first orientation command ran `ls -la ~/.kriya` with the REAL HOME, and the final check ran `find /Users/sriramnanduri/.kriya -newer ...`: both read only directory metadata of the real `~/.kriya`; no file there was read or written.
- The first `git status --short` in both Kriya-rebuild-v2 and Kriya-wave8-fixes ran without `--no-optional-locks`, so it may have refreshed `.git/index` (stat cache only); my before/after status comparison started after that and cannot show it. All later git calls used `--no-optional-locks`. Nothing was written inside any repository by any probe (before/after `git status --porcelain --ignored` identical; no repo file newer than the run start).
