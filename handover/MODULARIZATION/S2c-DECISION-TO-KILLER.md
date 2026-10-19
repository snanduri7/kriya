# S2c decision-to-killer table (correction unit S2c, TYPE C: OI-144 redaction residuals, OI-145 one endpoint rule, OI-146 raw-content surfaces)

Mutants: `~/kriya-modularization-tools/mutrun.py` v2 (git archive of HEAD 5dbf6b6, sha256 manifest before/after; deselect
`not test_engine_runtime_verification_trace and not test_post_check_passes_on_the_reference`). L1 =
tests/test_s2c_redaction_residuals.py + tests/test_s2b_redaction_completeness.py + tests/test_s2_redaction_owner.py; no L2.

Campaign (MEASURED): `20261020_021315_0b0465c5` at 5dbf6b6: 25 mutants, 25 killed at L1, 0 survived, VALID (tree_diff
empty, no straggler), 116 s.

Reproduction (MEASURED, scratch `s2c/{before,after}/`): `probe.py` (35 secret cases, 32 readable controls) 32 leaking
before -> 9 after (the 8 OI-144 (4) positional cases, open owner question, and `MYSQLPWD=` - not a real variable name,
left out with PWD/PASSWD on purpose); readable controls altered 2/32 before and after (the recorded `pwd=`/`PWD=`).
`channels.py` (markers 1-3 = OI-144 (1)-(3), 4-7 = (4), 8 = an S2b-covered `--token` argv) before: 15/15 channels
leaking; after: markers 1-3 leak in NO channel, markers 4-7 unchanged everywhere (open question), marker 8 no longer
reaches `launch.status` (the launch record on disk keeps it: S20). `records_probe.py`: evidence.records carried an
envelope-payload secret before, not after. Characterization 1d1a3e0: 41 strict-xfail (33 expected-to-flip + 8 OI-144 (4)
open-question pins), `--runxfail` 41 failed on the unfixed bytes (`before/runxfail.txt`); after the corrections the 33
flips pass, the 8 open-question pins stay strict-xfail.

| decision | symbol | risk | killer test | mutation | result |
|---|---|---|---|---|---|
| compound ALL-CAPS key (structure) | `is_secret_key` -> `_is_compound_secret` | `{"PGPASSWORD": x}` shown | ...compound_all_caps_secret_key_is_hidden_in_a_structure, ...name_is_hidden[json] | S2cM01 off | KILLED L1 |
| compound ALL-CAPS name (text, flag) | `_is_secret_name` | `PGPASSWORD=x psql` shown | ...compound_all_caps_secret_name_is_hidden[pgpassword, export-pgpassword] | S2cM02 off | KILLED L1 |
| suffix only, never infix | `_is_compound_secret` | `TOKENIZER=`, `PASSWORDLESS=` hidden | test_text_without_a_secret_stays_byte_identical[MAXTOKENS=5, TOKENIZER=bpe] | S2cM03 infix | KILLED L1 |
| the compound rule reads the name as written, not the plural-stripped form | `_is_secret_name` | `MAXTOKENS=5` hidden | ...byte_identical[MAXTOKENS=5] | S2cM04 forms | KILLED L1 |
| PWD / PASSWD are not compound suffixes | `_COMPOUND_SUFFIXES` | `OLDPWD=`, `NOPASSWD:` hidden | ...byte_identical[OLDPWD=..., NOPASSWD: ALL] | S2cM05 added | KILLED L1 |
| Slack tokens | `_SHAPED_SECRETS` | `xoxb-...` shown | ...vendor_token_shape_is_hidden[slack-bot, slack-user] | S2cM06 removed | KILLED L1 |
| the shape table is closed | `_SHAPED_SECRETS` | silent widening (npm body) | test_the_token_shape_and_compound_name_tables_are_closed | S2cM07 widened | KILLED L1 |
| `sk-` vendor group only (no generic kebab) | `_SHAPED_SECRETS` | `sk-learn-preprocessing-...` hidden | ...byte_identical[sk-learn-preprocessing-pipeline-v2] | S2cM08 generic | KILLED L1 |
| encoded URL userinfo | `_ENCODED_URL_USERINFO` | `next=https%3A%2F%2Fu%3Apw%40h` shown | ...percent_encoded...[nested-url, nested-url-lower] | S2cM09 off | KILLED L1 |
| output keeps the bytes as written (never decoded) | `redact_text` encoded sub | a decoded scheme / rewritten bytes | ...percent_encoded...[nested-url, nested-url-lower] | S2cM10 unquote | KILLED L1 |
| the encoded authority ends at `%2F` | `_ENCODED_URL_USERINFO` | an encoded `%40` in a PATH read as userinfo | ...byte_identical[...redirect=https%3A%2F%2Fh%2Fa%40b] | S2cM11 dropped | KILLED L1 |
| an escaped name character joins the name | `_NAME_SEPARATOR` `_NAME_ESCAPE` | `api%5Fkey=x` shown | ...percent_encoded...[encoded-name-underscore, encoded-name-and-separator] | S2cM12 off | KILLED L1 |
| a delimiter escape never joins a name | `_NAME_ESCAPE` | `token%3Dabc%3Dxyz` un-redacted (hidden before S2c) | test_the_measured_cost_of_the_new_rules_is_fail_closed[token%3Dabc%3Dxyz] | S2cM13 `%3D` joins | KILLED L1 |
| a name may start after a delimiter escape | `_NAME_SEPARATOR` lookbehind | `%3Faccess_token%3Dx` shown | ...fail_closed[x%3Dpassword%3Dsecret] | S2cM14 off | KILLED L1 |
| names are unquoted for the word test | `_redact_pairs` | `%70assword=x` shown | ...percent_encoded...[encoded-name, encoded-name-and-separator] | S2cM15 off | KILLED L1 |
| OI-145: the skill display keeps its own wording | `_safe_source` -> `safe_endpoint(unparseable=)` | byte change of a GUI field | test_the_skill_source_display_keeps_its_bytes[human_url:http://h:99999/v1] | S2cM16 default | KILLED L1 |
| OI-145: the one rule drops userinfo | `safe_endpoint` | credential shown | test_s2 ...endpoint_canonicalizers... | S2cM17 netloc | KILLED L1 |
| survey: launch.status redacted | `RunService._launch_view` | a refused `--token` command shown in the GUI | test_the_launch_status_the_gui_receives_hides_a_recorded_secret | S2cM18 raw | KILLED L1 |
| survey: evidence.records redacted | `EvidenceService._page` | an envelope payload secret shown | test_the_evidence_records_the_gui_receives_hide_a_secret_in_a_record_payload | S2cM19 raw | KILLED L1 |
| OI-146 strict scope: the review REASON is display text | `ReviewService.change_review` | model text quoting a secret shown | test_the_change_review_reason_is_redacted_while_its_diff_stays_raw | S2cM20 raw | KILLED L1 |
| OI-146: the approval DIFF is never redacted | `ReviewService.change_review` | reviewed bytes != approved bytes | test_the_approval_diff_is_served_raw_and_byte_faithful_and_persisted_only_as_digests | S2cM21 redact diff | KILLED L1 |
| terminal prompt reason redacted | `_terminal_approval` | reason secret printed | test_the_terminal_approval_prompt_redacts_its_reason_not_its_diff | S2cM22 raw | KILLED L1 |
| OI-146: the terminal prompt's diff lines stay raw | `_terminal_approval` | the person approves bytes they did not read | test_the_terminal_approval_prompt_shows_the_diff_raw | S2cM23 redact | KILLED L1 |
| `--yes` reason line redacted | `_terminal_approval` | reason secret printed | ...prompt_redacts_its_reason_not_its_diff | S2cM24 raw | KILLED L1 |
| OI-146: content_visibility on = the recorded bytes | `EvidenceService.content` | the opt-in surface silently altered | test_content_visibility_on_serves_the_recorded_bytes_raw[change, call] | S2cM25 redact | KILLED L1 |

Governed controls (green on the unfixed bytes AND after; MEASURED at 1d1a3e0 and at HEAD): 36 readable texts
byte-identical (incl. `TOKENIZER=`, `MAXTOKENS=`, `PASSWORDLESS=`, `PGPASSFILE=`, `OLDPWD=`, `NOPASSWD: ALL`,
`PREAUTHORIZATION:`, `npm_config_registry`, `sk-learn-...`, `hf_hub_download`, `https://h/%75ser:pw@x` and its decoded
form, a docker digest ref, encoded queries without a secret); 3 readable structures; `pwd=`/`PWD=` hidden (recorded,
unchanged); encoded authority userinfo already hidden; never-mutates; idempotence over a generated 78-text corpus x
{text, value}; the endpoint display rule's 24 bytes pins x 2 callers and 8 skill-source pins (plus the scratch
differential: 126 inputs identical before/after); the approval diff raw, byte-faithful, the launch record and decision
document holding no diff byte, the sidecar holding it, reading logs nothing; content_visibility on = raw (x2), off and
default WITHHOLD (x4); an opt-in content read writes and logs nothing. The cost pins added with the correction
(`NOTOKEN=`, `TOPSECRET=`, `MAXTOKEN=` hidden: readable on the unfixed bytes - the price, deliberately flipped;
`token%3Dabc%3Dxyz` hidden before AND after - the un-redaction guard; `x%3Dpassword%3Dsecret` shown before, hidden after).

## OI-146 trace: where review diffs and opt-in content live (TRACED at 5dbf6b6, MEASURED where marked)

| what | producer (file:line) | persisted? | raw? | governed by |
|---|---|---|---|---|
| review-diff sidecar `<ws>/.kriya/control/launches/<launch>.review-diff-<review>.json` | control/launch.py:202-216 `_store_review_diff` -> control/persistence.py:151-162 | YES (control document, ownership-stamped) | YES, by design: re-verified against the recorded per-file digests and candidate digest before serving (application/review.py:144-166) | the approval surface itself; read only by `change_approval.review`; not the evidence store (persistence.py:151 docstring), not exported; retention OI-104 (S7). Whether OBS-003 governs it: S20 |
| pending review in the launch record | control/launch_approval.py:89-100 `build_pending` | YES | NO diff: candidate digest, per-file `diff_sha256` + counts, decision; the `reason` text (truncated) IS stored raw (:98) | identity, as the owner prefers; the raw reason is S20's |
| decision document | control/launch_approval.py:174 `decide` -> persistence.py:110 | YES | NO (ids, candidate digest, workspace identity, decision; MEASURED: no diff byte) | - |
| `approval.decision` RunEvent (traces.db run_events, attempt-evidence mirror) | workflow/workflow.py:1638-1645 | YES | NO (outcome + triggers; no candidate digest either - that gap is OI-68, R4) | - |
| approval log line | workflow/stage_apply.py:358-361 | log | first line of the reason only, through the log redactor | S2 |
| terminal prompt | cli.py `_terminal_approval` | no | diff lines raw (surface), reason redacted (S2c) | OI-146 |
| `approval_required` details of a refused runtime review | cli.py:2836 -> control/launch.py:127 | YES (launch record) | YES (MEASURED marker 8: `run.sh --token <secret>` stored raw; the reason too) | display fixed in S2c (`launch.status`); stored copy -> S20 (Class A, reproducer `s2c/channels.py`) |
| candidate content `candidate.change` before/after/diff, model `messages`/`wire_body` | core/attempt_evidence/scope.py:735, :920 (writer blobs) | YES at the DEFAULT capture `full` | YES (MEASURED `s2c/store_probe.py`: gzip blob, `reader.blob` returns the secret; content_visibility gates SERVING only) | OBS-003 / OI-134 -> S20 (Class A). Not a copy of the approval diff: the recorder's own capture of the same candidate bytes |
| opt-in served content | kup/evidence_ops.py:184 policy check -> application/evidence.py:310 | NO (a read; MEASURED: state tree unchanged, nothing logged) | YES when on; WITHHELD when off/default | OI-146 |

Result: no raw approval-diff or content_visibility OUTPUT is copied into another persisted surface (the review diff
persists once, as the surface's own bound copy). Two persisted raw-credential findings for S20 (Class A, not fixed
here): the launch record's `approval_required` / `declined_decisions` details and reasons, and the evidence store's
candidate/model content at the default capture.

Known limits after S2c (documented in the owner docstring and the user guide): OI-144 (4) positional secrets (open
owner question); an unquoted value containing whitespace / a quote / `& ; ,` (S2b); a header kept as a `[name, value]`
list (S2b); `MYSQLPWD=`/`SSHPASS=` (PWD/PASS are not compound suffixes: `OLDPWD`, `BYPASS`, `COMPASS`); an IPv6 endpoint
shown without brackets by the display rule (bytes pinned; readability finding).
