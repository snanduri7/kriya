# FILE-INTEGRITY-CONTRACT-001 — Kriya never silently alters file bytes

Baseline: product `1200824` (HEAD `4b28bef`, evidence-only on top). Architecture decision: KAD-063 (EXTEND of KAD-035/036).

## Invariants

1. Kriya never silently alters file bytes outside the exact authorized mutation.
2. The exact bytes committed are the exact bytes that passed terminal verification (KAD-036, strengthened).

## Design

```
RAW MODEL RESPONSE
  -> response protocol parser      kriya/agents/response_protocol.py (DeveloperAgent.parse_file_payload)
  -> typed intent                  DeveloperResponse(kind=file|edits|no_change|invalid, reason_code)
  -> byte-preserving edit engine   kriya/workflow/file_integrity.py (FileSnapshot, apply_line_block_edits)
  -> authorized staged writer      AuthorizedFileWriter -> commit_revision_grounded_batch (raw bytes + raw base revision)
  -> candidate bytes               GenerationState.candidate_digests (the candidate manifest's digests)
  -> candidate binding             attempt._require_worktree_matches_candidate, before the FIRST gate of an attempt
  -> verification-tree binding     file_integrity.VerificationTreeBinding (tracked content + candidate),
                                   re-checked AFTER every validator gate and before the terminal binding
                                   (added in the closure pass, F-4; the original text said "before every gate",
                                   which was never literally true)
  -> verification binding          verification_binding.bind_candidate (unchanged, now over raw revisions)
  -> transactional commit          terminal_commit.commit_terminal_candidate
  -> committed == verified check   terminal_commit.committed_digest_mismatch (VERIFIED_COMMIT_DIGEST_MISMATCH)
```

- **The protocol is separate from the payload.** The parser removes only a wrapper that encloses the whole payload: one outer fence, or the multi-file JSON envelope. It never edits anything inside the payload. A response that does not match its protocol is refused with a typed reason code, and the existing operation-contract retry asks the model again.
- **`sanitize_generated_content` is deleted.** Its five heuristics are gone:
  - trailing `FILE CONTENT:` truncation;
  - gutter stripping;
  - largest-fence extraction;
  - XML comment repair;
  - recursive envelope sanitizing.
- **Two protocols produce the same typed intent.**
  - `autonomy.developer_response_protocol: legacy_strict` is the default. Its markers are recognized only as exact full lines at column 0.
  - `structured` uses `<<<KRIYA:FILE|EDIT|NO_CHANGE path="...">>>`, `<<<KRIYA:SEARCH>>>`, `<<<KRIYA:REPLACE>>>` and `<<<KRIYA:END_FILE|END_EDIT>>>` sentinels.
  - The setting is SECURITY_AUTHORITY under SEC-009.
  - `structured` is not production-qualified yet; the qualification cases exercise `legacy_strict`.
- **Typed mutation.** A parsed response is a `DeveloperResponse`, and the engine applies `AnchoredReplace(operation_id, search, replace)`.
  - A whole-file replacement is a `FILE` intent. It creates the file when the snapshot does not exist.
  - Insertion, delete and create operations were not added, because no protocol produces them (see Residual risk).
- **Edit engine.** `load_snapshot` reads the raw bytes once. The file's revision is `sha256(raw)`.
  - The text view is strict UTF-8, with or without a BOM, in one line-ending convention.
  - Every snapshot proves `encode(text) == raw` before it may be mutated.
  - Anchors are runs of complete lines, split on `"\n"` only.
  - Matching is exact first. If there is no exact match, it retries ignoring leading and trailing spaces/tabs per line; blank lines still match blank lines one for one, and a replacement is re-indented by one consistent shift.
  - Zero matches, several matches, overlaps, an empty SEARCH and an inconsistent indentation shift are all refused typed.
  - Every edit locates in the same source before anything is spliced.
- **Newlines.** Model line breaks are expressed in the target file's own convention, which is the only way an LF-based engine can keep a CRLF file CRLF. A lone CR is payload. A whole-file replacement keeps the file's final-newline presence or absence. A new file keeps the payload's own convention, and mixed endings are refused.
- **Encoding policy.**
  - UTF-8 and UTF-8 with a BOM are supported.
  - Anything else gets `UNSUPPORTED_TEXT_ENCODING`, and the file is left untouched.
  - Mixed CRLF/LF files, and files the text view cannot reproduce byte for byte, get `MIXED_NEWLINE_UNSUPPORTED`.
  - Symlinks get `SYMLINK_TARGET_UNSUPPORTED`.
  - All three are deterministic stops (`file_integrity_unsupported`), not retries.
  - Latin-1 is not supported, and no other encoding support is claimed.
- **Revisions are raw bytes everywhere mutation authority lives:**
  - `read_file_revision`;
  - every `StagedFileWrite.expected_base_revision` in the attempt;
  - the direct terminal batch (`state.all_original_raw`);
  - self-correction `apply_patch`;
  - the static-analysis scope and operator scan;
  - RunRecord persistence;
  - commit evidence schema 3 (`candidate_revision` and `result_revisions` are byte digests).

  Context-freshness decisions read `read_shown_text`. For an undecodable file it returns `"raw:" + digest`, which can never equal a decoded view.
- **Deterministic post-processing.**
  - Package-declaration stripping is deleted.
  - Four `pom.xml` corrections are now one candidate mutation step, `attempt._apply_candidate_pom_corrections`: Maven source-root coverage, exec.mainClass, JDK-incompatible JVM flags, and the exec executable pin. The last two are now pure text transforms in `toolchain.py`.
  - The step runs before the first gate of an attempt, only when the candidate itself wrote `pom.xml`. It writes through the authorized writer and records a `candidate.deterministic_transformation` event with both digests.
  - The JVM-flag and executable-pin corrections used to run between the compile/test gates and runtime verification, which was a mutation after verification. That call site is removed.
- **Worktree sync.**
  - Parsing uses `git status --porcelain=v1 -z` through `worktree.git_status_entries`: NUL-delimited, with rename/copy original paths and `os.fsdecode` names. Paths with spaces, tabs, newlines or non-UTF-8 bytes arrive exactly.
  - Symlinks are copied as links, modes are preserved, deletions and renames are applied.
  - Every synced path is compared byte-exactly afterwards.
  - Failures raise `WorktreeSyncError(WORKTREE_SYNC_FAILED | WORKTREE_CONTENT_MISMATCH)`, and both `create_git_worktree` callers already fail closed on that.
  - `snapshot_untracked_files` uses the same parser.
- **Abort restore without isolation.** This path writes back the exact original bytes, never a decoded copy.

## Issue table

| ID | Classification | Root cause | Generic reproducer (pre-fix, MEASURED) | Fix | Test | Mutation | Status |
|---|---|---|---|---|---|---|---|
| 1 | CONFIRMED | `_strip_markdown_fences` picked `max(fences)` for any text containing a fence | README reduced to `'x = 1'`; module docstring with a column-0 fence reduced to the example | whole-payload single outer fence only; otherwise `AMBIGUOUS_FILE_RESPONSE_PROTOCOL` (Markdown targets: fences are content) | `test_issue1_*` | largest-block restored → KILLED | FIXED |
| 2 | CONFIRMED | `_GUTTER_*_RE` applied to all generated content | YAML `200:`/`404:` keys, dict keys, doctest `>>>`, `>> 8)` shift line all rewritten | gutter stripping deleted; an echoed gutter fails typed `ANCHOR_NOT_FOUND` | `test_issue2_*`, `test_issue6_gutter_echo_*` | broad gutter regex → KILLED | FIXED |
| 3 | CONFIRMED | `_fix_xml_comment_double_hyphens` applied to every file | `"<!-- a -- b -->"` rewritten in Java/Python/properties | deleted; invalid XML is left to the structural gate | `test_issue3_*`, parametrized payload test | XML rewrite → KILLED | FIXED |
| 4 | CONFIRMED | `_TRAILING_FILE_CONTENT_RE` (prose-prefix, case-insensitive) truncated payload | doc line `File content: ...` truncated the file; docstring line truncated through the real split→sanitize chain | exact full-line marker, exactly once; payload opaque | `test_issue4_*` | loosened marker → KILLED | FIXED |
| 5 | CONFIRMED | `_NO_CHANGE_NEEDED_RE` matched anywhere, case-insensitive | a source comment dropped the whole fix (file and edit forms) | column-0 `NO CHANGE NEEDED` protocol line only; combined with a mutation → `CONFLICTING_DEVELOPER_RESPONSE` | `test_issue5_*` | loosened → KILLED | FIXED |
| 6 | CONFIRMED | `^[ \t]*SEARCH:` IGNORECASE regex markers | YAML `search:` became a second edit; docstring `Search:` truncated the replacement | state-machine parser, exact lines, typed `INVALID_EDIT_PROTOCOL`/`CONFLICTING`/`AMBIGUOUS`; structured sentinel protocol | `test_issue6_*`, `test_structured_*`, fuzz | loosened regex → KILLED | FIXED |
| 7 | CONFIRMED | text-mode reads with `errors="replace"`, `splitlines()`, `"\n".join` | CRLF→LF whole file; `\xe9`→U+FFFD; final newline lost; FF/U+2028 became line breaks | `FileSnapshot` + engine; strict encodings; per-file convention | `test_issue7_*`, corpus round trip | errors=replace / splitlines / CRLF / final newline → KILLED | FIXED |
| 8 | CONFIRMED | `current_content.count(search)` substring match | `it = 1` rewrote `limit = 10` to `limit = 20` | complete-line blocks | `test_issue8_9_*` | substring anchor → KILLED | FIXED |
| 9 | CONFIRMED | blank-line-collapsing fallback, unindented replacement, empty SEARCH `continue` | empty SEARCH silently dropped; indentation destroyed; `TabError` | exact then indentation-tolerant unique block; blank lines significant; `EMPTY_SEARCH_BLOCK`, `INDENTATION_STYLE_MISMATCH`, `OVERLAPPING_EDITS` | `test_issue8_9_*`, `test_issue9_*` | empty/duplicate/overlap/indent → KILLED | FIXED |
| 10 | CONFIRMED | `read_file_revision` digested the lossy decode | `x\xff` ≡ `x\xfe`; CRLF ≡ LF | raw-byte revisions (see Design) | `test_issue10_*`, `test_prd008_commit_state_gate` (schema 3) | lossy revision / digest bypass → KILLED | FIXED |
| 11 | CONFIRMED (real `WorkflowEngine`, mocked model) | fixers `open(..., "w")` on worktree files outside the candidate batch | compile gate verified pom `7fa328…`, committed pom `2da925…` | candidate-only, authorized, evented pom corrections before the gates; package stripping deleted; mid-verification JVM corrections moved before the gates; worktree == staged digests before gates; post-commit re-read | `test_issue11_*`, `test_worktree_bytes_must_equal_*`, `test_verified_bytes_equal_committed_*`, `test_a_candidate_changed_after_verification_*` | direct pom write / post-verification mutation / equivalence off → KILLED | FIXED |
| 12 | CONFIRMED | `git status --porcelain` without `-z`; quoted paths stripped of `"` only; failures only warned | `café.txt`, `tab\there.txt` uncommitted changes never reached the sandbox | NUL-safe parser, byte-exact post-sync check, fail closed | `test_issue12_*` | non-NUL parsing / no post-check / non-fatal failure → KILLED | FIXED |

Also found and fixed in this batch:
- **JVM-flag stripping and exec-executable pinning.** Both `toolchain.py` functions mutated `pom.xml` between the compile/test gates and runtime verification. That is a mutation after verification, and they were not named in the report.
- **Abort restore.** The unisolated abort restore wrote decoded text back into the real workspace.

Own defects found and fixed before any commit:
- `FileSnapshot.encode` of a CRLF payload into a CRLF file produced `\r\r\n`.
- An operator-precedence error in the qualification case port.

## Pre-fix and post-fix measurement

- Pre-fix output: `evidence/file-integrity-contract-001/pre_fix/repro_prefix_1200824.txt`.
  - It shows 26 corruptions, plus #1b, #4b and #11.
  - #11 was run twice. With `App.java` at the root, widening is refused (same). With `app/App.java` it diverged.
  - The scripts are stored verbatim as `.py.txt`.
- Post-fix replay on the same inputs: `post_fix/repro_postfix.txt`. 32/32 matched the predicted typed or byte-identical result, and #11 showed `same`.

## Direct-write audit (§31)

Tripwire: `tests/test_file_integrity_contract_001.py::test_every_filesystem_write_site_is_audited`. It requires every new write site in `kriya/` and `plugins/` to be classified here and counted there.

| Class | Sites |
|---|---|
| AUTHORIZED_CANDIDATE_WRITE | `edit_safety` staged writer / grounded file write; `attempt` staged batch, restoration writes, `_apply_candidate_pom_corrections`; `self_correction.apply_patch` (via `AuthorizedFileWriter`); `terminal_commit` (commit + recovery `control/recovery.py`) |
| SAFE_NON_SOURCE (Kriya state/evidence/config, outside candidate sources) | `.kriya/` control/checkpoints/proposals/milestones/commit evidence; `~/.kriya` authority, MCP approvals, qualification, certification, routing, runtime fingerprints, state paths; `metrics`, `static_analysis` waivers/service store; `production_doctor` probes; `skills`/`knowledge` stores (`skills/skill.py`, `knowledge/staging.py`, `workflow/skill_extraction.py`, `tools/knowledge.py`); `memory/memory.py`; `policy/approved_sources.py`; `cli.py` (`.kriya/last_prompt.md`, operator `--out`/`--output` files, skills approve) |
| SAFE_TEMP / SANDBOX | `static_analysis/scope.py` snapshot roots; `context_certification` fixture repos; `deterministic_failure_diagnostic`, `regression_attribution`, `resume_fingerprints` temp overlays; `tools/validate.py` classpath temp; `tools/lsp.py` data dir and private JDTLS project mirror (Kriya-owned temp; JDTLS is never rooted at, and never writes, the candidate - D1, 2026-10-01); `worktree.py` sandbox lifecycle (create/reset/sync/remove) |
| AUTHORIZED_RUN_ARTIFACT | `workflow_controller.quarantine_abandoned_plan_files` (byte-preserving move of run-created files into `.kriya/`); `workflow.py` unisolated abort restore (now exact original bytes) |
| AUTHORIZED_TOOL_WRITE | `plugins/core_tools` filesystem `write`: explicit tool action under ExecutionPolicy / TOOL-001; autonomous use writes the plan candidate, which the terminal gate binds by raw bytes |
| BYPASS (fixed) | pom widening + exec.mainClass + package stripping in `attempt.py`; JVM-flag strip + exec pin in `toolchain.py`; lossy abort restore in `workflow.py` |
| UNKNOWN | none |

`kriya/workflow/attempt.py`, `toolchain.py`, `file_resolution.py`, `self_correction.py` and `agents/agent.py` contain no direct file write at all; a structural test enforces this.

## Error taxonomy

- **New typed codes:**
  - `AMBIGUOUS_FILE_RESPONSE_PROTOCOL`, `INVALID_EDIT_PROTOCOL`, `CONFLICTING_DEVELOPER_RESPONSE`, `MODEL_EDIT_PROTOCOL_INVALID`
  - `EMPTY_SEARCH_BLOCK`, `ANCHOR_NOT_FOUND`, `ANCHOR_AMBIGUOUS`, `OVERLAPPING_EDITS`, `INDENTATION_STYLE_MISMATCH`
  - `UNSUPPORTED_TEXT_ENCODING`, `MIXED_NEWLINE_UNSUPPORTED`, `SYMLINK_TARGET_UNSUPPORTED`, `SOURCE_CHANGED_SINCE_AUTHORIZATION`
  - `WORKTREE_SYNC_FAILED`, `WORKTREE_CONTENT_MISMATCH`, `VERIFIED_COMMIT_DIGEST_MISMATCH`
- **Reused, not duplicated:**
  - `ANCHOR_NOT_IN_FILE`, now defined once in `file_integrity`.
  - `POST_VERIFICATION_MUTATION` is the existing `VERIFIED_CANDIDATE_EVIDENCE_STALE` binding refusal.

## Behaviour changes a user can see

- A response with prose around a fence, a fenced dump after an edit, a same-line marker, or a marker written in prose case is now refused and retried instead of guessed.
- A non-Markdown file whose own content has a column-0 ``` line cannot be delivered through the legacy raw-content protocol. The structured protocol carries it opaquely.
- A second edit anchored in the first edit's output is refused, because all edits locate in the same source.
- A repository-owned `pom.xml` is no longer corrected behind the gates. The compile gate reports the real layout problem instead.
- Qualification policy `/5`: every `/4` record is STALE until requalified.

## Test changes to pre-existing tests

Each was changed because the behaviour it asserted is the heuristic this contract removes, not to obtain green:
- `tests/test_agents.py`
  - 40 sanitizer and legacy-splitter tests were removed. Each has a typed counterpart in the new module: payload verbatim, exact-line markers, fenced-block wrapper, gutter echo fails typed, trailing dump refused, same-line marker refused, multi-pair parsing, no-change conflict.
  - `_strip_markdown_fences` tests were renamed to `_strip_json_protocol_fences`. That is JSON protocol only, with unchanged behaviour.
  - The dangling-SEARCH test now asserts `INVALID_EDIT_PROTOCOL`.
- `tests/test_workflow.py`
  - The batch-JSON "sanitizes" tests now assert byte-verbatim writes and a typed gutter failure.
  - The package-strip end-to-end test now asserts the Developer's bytes are kept. Its 4 unit tests were deleted with the function.
  - The JVM-flag unit tests use a pure-transform adapter.
  - The blank-line, ambiguity and chained-edit anchor tests assert the typed refusals.
- `tests/test_full_file_final_newline.py`: the fence is removed at the agent boundary (`parse_file_payload`), and `keep_final_newline_state` replaces the deleted helpers.
- Version tripwires: qualification `/4`→`/5` (two files), commit evidence schema `2`→`3` (strengthened with the candidate revision byte digest).

## L2 adjacent-suite run (user, 2026-09-30): 14 failed / 2969 passed

Triage. Every failure was classified before changing anything.

| Failure | Classification | Action |
|---|---|---|
| 5 × `test_agents` newline (`'...\n' == '...'`) | own defect: the parser appended a framing newline to unwrapped fence / legacy `FILE CONTENT` payload | parser keeps framing out of content; the write restores the file's own final-newline state |
| `test_run_attempt_accepts_trailing_slash_allowlist...` | own defect: an EMPTY original file was treated as "no final newline" and the model's newline stripped | `keep_final_newline_state`: an empty file has no convention (new parametrized case) |
| `test_workflow_surfaces_toolchain_warning...`, `...checks_toolchain_only_once...` (4/5 calls vs 3) | own defect (efficiency): the pre-verification JVM-flag transform spawned `check_java_toolchain()` every attempt | check the pom for a known flag first; toolchain only when one is present |
| `test_prd008_recovery::...failed_rollback...` (`OSError: restore failed`) | own defect: the grounded single-file write reused the batch rollback primitive `_atomic_write_bytes`, so an injected rollback failure broke ordinary control writes | the grounded write uses `atomic_write_file(content_bytes=)` |
| 3 × coordinated-repair tests (`ANCHOR_NOT_FOUND`) | fixture used mid-line substring anchors (`System.exit(1);`, `testMain`), the exact issue-#8 behaviour | fixtures use whole-line anchors; every assertion unchanged |
| `test_workflow_gutter_prefixed...` (my test) | wrong expected code: the anchor-authority gate refuses first (`ANCHOR_NOT_IN_FILE`) | assertion names the real producer |
| `test_worktree_locations_are_decided_only_by_worktree_py` | line-number pin shifted by the `operator_scan.py` import change | line numbers updated |

Re-run of all 14 plus both new modules: passed. Mutations re-run: 24/24 killed (`post_fix/mutation_run2.txt`). One `RuntimeWarning` (AsyncMock never awaited) in the allowlist test is pre-existing (MEASURED at `4b28bef`).

## Verification (product commit `eeda5c8`)

| Gate | Result | Evidence |
|---|---|---|
| L0 original reproducers, post-fix | 32/32 predicted typed/byte-identical results; #11 verified pom == committed pom | `post_fix/repro_postfix.txt` |
| L1 new module | 263 passed (pytest, 4.2 s) | `tests/test_file_integrity_contract_001.py` |
| L1 mutation | 24/24 killed, run twice (before and after the L2 fixes) | `post_fix/mutation_run1.txt`, `mutation_run2.txt` |
| L2 adjacent suites (user) | 14 failed / 2969 passed; triaged above; all 14 re-run and passing | this document |
| L3 full pytest (user, `ulimit -n 256`) | 7769 passed / 1 failed / 72 deselected, 162 warnings. The failure (`test_repair_executor::test_generic_repair_loop_retests_only_the_targeted_test`) is a mid-line substring anchor fixture (`return 0`), issue #8; the fixture was changed to a whole-line anchor and re-run (passed). **The full suite has not been re-run at `eeda5c8` itself.** | session |
| Static | `ruff check` (tracked tree) 0, `pylint kriya plugins/core_tools tests` 0 | session |
| L4 requalification (policy /5, Ollama 0.34.4) | qwen3-coder 18 PASS / 0 FAIL / 1 UNAVAILABLE; qwen3.6 15/0/4 (tool cases not applicable); every role QUALIFIED | `live/qualify_*.json`, `live/model_status.txt` |
| L4 `doctor --production` (successor config, sha256 667831f0...) | `production_ready: true`; the same five WARN checks as the PROVIDER-CONTRACT-001 closure | `live/doctor_production.json` |
| §35 live protocol usability (24 calls, 3 trials per cell) | `structured`: 12/12 valid, 6/6 edits applied, both models. `legacy_strict`: 11/12 valid (one qwen3.6 REPLACE-without-SEARCH, refused `INVALID_EDIT_PROTOCOL`), one qwen3-coder valid `NO CHANGE NEEDED` (model decision). Payload verbatim in 24/24. Small sample, not qualification. | `live/protocol_35.jsonl` |

The qualification and doctor runs used the working tree that became `eeda5c8`, plus the one test-only fixture change (`live/HEAD_at_run.txt`). No Graphify run was started.

## Residual risk

- Encodings other than UTF-8 are refused, not supported.
- `InsertBefore`/`InsertAfter`/`DeleteFile` are not implemented, because no protocol produces them.
- `structured` is implemented and tested deterministically but not qualified. Its live usability is measured separately (§35) and is not the production default.
- Context-item revisions built by the context modules still digest their text view. They are freshness evidence, not mutation authority.
  - Mutation always binds to the snapshot's raw revision at staging time, and the commit re-checks it.
  - An undecodable file never matches: its shown revision is prefixed `raw:`.
  - The one difference a text-view match cannot see is a valid UTF-8 file changing only its line-ending convention after it was shown. The engine then applies the edit to the current file and writes it in the current convention. That is not corruption, but it is also not detected as "changed since shown".
- Legacy recovery evidence (schema 1) compares text revisions to raw digests. Any file other than valid UTF-8 LF classifies FOREIGN (fail closed).

# Closure pass (F-1..F-4, S-1, S-2)

Baseline `c372e0a`. Backup branch `backup/file-integrity-c372e0a`. Bundle `~/kriya-backups/file-integrity-c372e0a.bundle` (sha256 `18a12608…`, incremental on `4b28bef`, verified). Nothing pushed.

| ID | Finding | Classification | Fix | Tests | Status |
|---|---|---|---|---|---|
| F-1 | Legacy framing has no payload terminator: prose after REPLACE/FILE CONTENT becomes payload (valid `.properties`/YAML) | CONFIRMED, P0 | The production protocol is the sentinel protocol `kriya_sentinel_v1`: explicit end markers, text after the last block is `INVALID_EDIT_PROTOCOL`. Legacy is compatibility-only, refused under `runtime_profile: production` (`RESPONSE_PROTOCOL_NOT_PRODUCTION`) and by doctor `model.response_protocol`. No parser fallback. | `test_f1_*`, `test_no_fallback_*` | FIXED |
| F-2 | The sentinel parser appended a newline implicitly | CONFIRMED | Explicit grammar: every FILE line is newline-terminated; `<<<KRIYA:END_FILE no_final_newline>>>` is the only no-final-newline form (`DeveloperResponse.final_newline`). Existing files: Existing File Convention Policy. | `test_f2_*` | FIXED |
| F-3 | Direct mode re-raised a typed sync failure as a generic `RuntimeError`; enforce mode reported it as `STRUCTURED_PLAN_UNAVAILABLE` | CONFIRMED (traced) | Direct returns a typed terminal result (`reason_codes`, `failure_category`, trace row). Enforce reports `failure_type: WORKTREE_SYNC` with the reason code. | `test_f3_*` (copy failure, source changed during sync, byte mismatch; both modes) | FIXED |
| F-4 | The candidate was checked only before the first gate; a gate could rewrite tracked content before later gates ran | CONFIRMED (traced) | `VerificationTreeBinding` (git-tracked content + candidate) re-checked before and after every validator gate (`_verification_gate` decorator: compile, tests, runtime verification, pom validate, classpath inspection; also on a gate's exception path) and before the terminal binding. A change found before a gate is reported `phase: before` ("detected before the X gate"), never attributed to that gate, and the gate does not run on it. Typed stop `VERIFICATION_GATE_MUTATED_TRACKED_FILES` (`verification_tree_mutated`). Kriya's own writes use `authorize`. Untracked/ignored output is excluded. Enforce: the named-test gate. | `test_f4_*` (candidate, unrelated tracked, deleted, mode change, compile/tests/runtime gates, exception path, build output, touch, real engine at the detecting gate, a between-gates change caught before the next gate, terminal re-check) | FIXED |
| F-4a | Found while implementing F-4: `run_app` wraps its command in `except Exception`, which would have turned the tree stop into an ordinary result | CONFIRMED (traced; own design gap, never committed) | The check sits in the gate decorator, outside every internal handler (and on the exception path) | `test_f4_every_validator_gate_*[runtime_verification]` | FIXED |
| S-1 | Blank edge lines in a sentinel SEARCH | MEASURED: 0 of 26 SEARCH blocks (both models, both protocols) | Decision: no normalization; SEARCH stays exact | `test_s1_*` pins the behaviour | DECIDED |
| S-2 | Protocol `path=` normalization | — | `normalize_protocol_path`: repo-relative POSIX; absolute, escaping, NUL and backslash paths refused; must equal the requested target | `test_s2_*` | FIXED |
| — | Structured payload with fence-like content | — | Opaque between sentinels | `test_structured_payload_carries_fence_like_content_verbatim` | VERIFIED |

## Qualification identity and results

- Policy `/6` bound the Developer response protocol identity into the policy digest (`policy_digest_for`) and the record (`developer_response_protocol`). The two protocol cases run the configured protocol.
- `/6` results (historical, preserved in `closure/live/qualify_v6_*`):
  - qwen3-coder 18/0/1 QUALIFIED.
  - **qwen3.6 NOT_QUALIFIED, `full_file_raw_content` FAIL.** The response was a valid, verbatim sentinel FILE that parsed and defined `slugify`, but carried no invented fenced example.
- Classification: case-design defect. The `/6` case required the model to *invent* a fenced docstring example. §23 measured both models omitting it under both protocols (0/8), while supplied fence content is preserved (8/8). Owner decision: correct the case.
- `/7` supplies the exact fence-bearing file and requires it back byte for byte through the configured protocol. `/6` records stay historical and do not count under `/7`.
- `/7` results (`closure/live/qualify_v7_*`, each run once):
  - qwen3-coder 18 PASS / 0 FAIL / 1 UNAVAILABLE.
  - qwen3.6 15 / 0 / 4 (the tool cases do not apply).
  - Every role is QUALIFIED.
- The instruction-following behaviour (invented docstring fence) is kept as non-gating evaluation evidence, not a qualification case. Adding a case would change the qualification policy schema and every policy digest, and the measurement already exists (§23).

## Generic protocol evaluation (§23)

`closure/live/protocol_eval_23.jsonl`. It used 7 tasks (Java, Python, `application.properties`, YAML and `pom.xml` edits; new Markdown and Python files) × 2 protocols × 2 models × 2 trials = 56 calls, through the real `DeveloperAgent` prompts.

| | Valid protocol | Refusals | Edits applied | Payload verbatim |
|---|---|---|---|---|
| sentinel, qwen3-coder | 14/14 | — | 8/10 (2 × `INDENTATION_STYLE_MISMATCH`, Java) | 14/14 |
| sentinel, qwen3.6 | 14/14 | — | 10/10 | 14/14 |
| legacy, qwen3-coder | 5/14 | 9 × `MODEL_EDIT_PROTOCOL_INVALID` (same-line `SEARCH:   x`) | 1/1 | 5/5 |
| legacy, qwen3.6 | 9/14 | 4 × `MODEL_EDIT_PROTOCOL_INVALID`, 1 × `INVALID_EDIT_PROTOCOL` | 5/5 | 9/9 |

- The Java `INDENTATION_STYLE_MISMATCH` is **UNKNOWN**. A one-call diagnostic capture of the same cell applied cleanly (`closure/live/diagnostic_java_indent.json`). The raw text of the two original refusals was not recorded (an evaluation-harness gap). Refusal is the engine's intended behaviour for an inconsistent indentation shift; a false refusal is not excluded. It is not changed on inference.

## Production promotion

The sentinel protocol is the default (`autonomy.developer_response_protocol: structured`) after every gate held:
- deterministic, fuzz and property tests;
- `/7` on both pins;
- the evaluation above;
- `doctor --production` `production_ready: true`, with `model.response_protocol` and `model.qualification` PASS (`closure/live/doctor_production_v7.json`).

## Mutation evidence

43/43 killed (final run `closure/mutation_closure_r2.txt`, after the pre-gate check and the harness fixes; the first run, 41/41, is kept in `closure/mutation_*.txt`):
- the original 24;
- 15 closure mutations: prose after the end marker, protocol identity omitted, a `/6` record reused under `/7`, implicit newline, candidate-only tree check, skipped post-compile and post-test checks, no check before a gate, no check on a gate's exception path, path escape, sentinel→legacy fallback, REPLACE edge trimming, both F-3 paths, skipped terminal re-check;
- 4 `/7` case mutations: drop exactness, bypass protocol parsing, accept a dropped fence, accept trailing prose.

One closure mutation first survived (the compile validator without the binding was still stopped, but only later, at the terminal re-check). The engine test now asserts the detecting gate, and kills it. A redundant `/7` check (`final_newline is True`, implied by byte equality under the grammar) was removed rather than kept as dead logic. Files: `closure/mutation_*.txt`.

## Legacy residuals (compatibility-only)

The legacy markers:
- have ambiguous end-of-payload semantics;
- cannot represent final-newline intent;
- refuse legitimate non-Markdown files with a column-0 fence;
- refuse same-line markers.

They are not production-equivalent and get no further heuristics.

## Demo and repository preparation

Hidden `pom.xml` repairs no longer exist, so a demo repository must build before Kriya starts. Inspect line endings and encodings first with `git ls-files --eol` and `file -I <path>`. Non-UTF-8 or mixed-newline files that a demo would edit are refused typed (`UNSUPPORTED_TEXT_ENCODING` / `MIXED_NEWLINE_UNSUPPORTED`), and should be known in advance.

## Tests changed in this pass

- Tests that script legacy/raw Developer answers now either pin `legacy_strict` explicitly (legacy-behaviour tests) or render the same intent in the requested protocol (`tests/_protocol_responses.py::as_requested`: `_scripted_run`, `_edit_protocol_harness`, prd016, prd017).
- Pins moved:
  - qualification `/5`→`/7` (two files);
  - doctor check list (+`model.response_protocol`);
  - qualification digest tests (protocol-bound);
  - full-file case fixtures (the supplied `/7` file).
- Scripted answers in the requested protocol, everywhere:
  - `tests/conftest.py` autouse fixture `_scripted_developer_answers_speak_the_requested_protocol` translates scripted per-file Developer answers through the `DeveloperAgent._complete_file` seam (opt out: marker `developer_answers_verbatim`; never applied to `live_model`).
  - `ChaosRuntime` (PRD-032 subprocess-safe harness) translates Developer replies the same way; `requested_file()` reads the structured directive (`for 'x' ONLY`) as well as the legacy one.
  - Fidelity rule: `as_requested` reproduces exactly what the legacy parser for that request did. A raw answer to a repair request (the contract's analysis-required line, i.e. legacy `repair_protocol`) was INVALID under legacy (no FIX ANALYSIS) and is passed through unchanged, never promoted to a FILE block. An earlier version of the helper promoted it, which changed 3 prompt-fit/goal-dedup runs from `quality_gates_exhausted` to `no_progress` (measured: legacy 7 Developer requests vs structured 2, `operation_authority.rejected` on the promoted FILE). Harness defect, own, fixed before commit.
- Tamper injection points (PRD-032 D10/E02, `test_candidate_verification_binding`): a candidate changed right after static analysis is now caught by the verification-tree binding before the terminal regression (earlier layer, covered by `test_f4_a_candidate_changed_between_gates_is_caught_before_the_next_gate_runs`). The commit-time verified-candidate binding keeps its own coverage through `_chaos_harness.inject_before_terminal_commit`, which changes the candidate after every gate and immediately before the terminal batch is materialized - the only window that binding alone guards. (A change made after `final_writes` is materialized never reaches the workspace: the commit writes the verified bytes.)
- `test_agent_contracts` unsafe-path test is parametrized: legacy keeps its historical pass-through pin; structured refuses `../outside.py` at parse time (S-2).
- `test_worktree_canonical_root` line pin `production_doctor.py:596→597` (the doctor ID list grew by one line; same read-only `git worktree list`).
