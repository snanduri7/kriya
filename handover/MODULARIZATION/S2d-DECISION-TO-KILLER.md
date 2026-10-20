# S2d decision-to-killer table (correction unit S2d, TYPE C: OI-153 positional-secret argv handling, was OI-144 part 4)

Owner decisions (2026-10-20): (b) grammar-aware redaction over a CLOSED supported-command table at the one redaction owner,
command-specific and exact; (c) the runtime-command review fails closed for a command whose exact bytes would expose a
recognized secret; the operator-authorized paths keep what they may run and redact their outputs. OI-153 stays OPEN (part
(B), S20 I-9: the launch record's other raw notes).

Mutants: `~/kriya-modularization-tools/mutrun.py` v2, deselect `not test_engine_runtime_verification_trace and not
test_post_check_passes_on_the_reference`. L1 = tests/test_s2d_positional_secrets.py + test_s2c_redaction_residuals.py +
test_s2b_redaction_completeness.py + test_s2_redaction_owner.py; no L2.

Campaigns (MEASURED): `20261020_091916_9df9e7a9` at 6fa4d11: 34 mutants, 33 killed at L1, 1 survived (S2dM15), VALID
(tree_diff empty, no straggler), 177 s. S2dM15 strengthened in 8b7d20f (`npm config get <credential key> <key>` readable)
and re-run: `20261020_092252_b09c6dac` at 8b7d20f: KILLED L1, VALID. Total 34 distinct mutants, 34 killed.

Reproduction (MEASURED, scratch `s2d/{before,after}/`): `channels.py` (markers 1-6 = mysql, sshpass, docker login, npm
_authToken, podman login attached, mariadb; 7 = a `--token` argv, S2b-covered) before: 22/24 channels leaking - every
text/structure channel, the terminal prompt of `kriya generate` (positional: shown raw and approved; `--token`: shown
REDACTED and the raw bytes approved - the S2c display fix made it unfaithful), the legacy prompt of `kriya proposal execute`
(same), the desktop review OFFERED for all six positional forms (pending reviews recorded raw, `runtime_command.review` and
`launch.status` serving them), `approval_required` persisted raw (marker 7). After (b7e4dd5 and later): 0/24; no review
offered; the terminal and legacy prompts are not asked; the launch record holds no marker. The 8 S2c strict-xfail pins
(`--runxfail`: 8 failed at 7e7aff7) flip; the S2d characterization 7b8000b: 100 strict-xfail + 264 governed, `--runxfail`
100 failed on the unfixed bytes.

| decision | symbol | risk | killer test | mutation | result |
|---|---|---|---|---|---|
| mysql: only an ATTACHED value | `_mysql` offset | one character of the password shown | s2c ...hidden_in_text_and_argv[mysql-p] (exact bytes) | S2dM01 offset 3 | KILLED L1 |
| mysql: `-P` is the port | `_mysql` `startswith("-p")` | `mysql -P3306` hidden | ...readable_look_alike...[mysql -P3306 -u root shop] | S2dM02 case-folded | KILLED L1 |
| sshpass `-p` takes the NEXT word too | `_SSHPASS_OPTIONS["p"]` | `sshpass -p <pw>` shown | s2c ...[sshpass-p] (both pins) | S2dM03 attached-only | KILLED L1 |
| sshpass options end at the wrapped command | `_getopt` break | `ssh -p 22` hidden | ...hidden_in_the_text...[sshpass-next] | S2dM04 scan past operand | KILLED L1 |
| attached value offset | `_getopt` | the `p` letter kept/value cut | ...[sshpass-attached] | S2dM05 | KILLED L1 |
| a no-value flag continues the cluster | `_getopt` | `-vp <pw>` shown | ...[sshpass-cluster] | S2dM06 break | KILLED L1 |
| `-P prompt` consumes its value | `_SSHPASS_OPTIONS["P"]` | `-P assword: -p <pw>` misread | ...[sshpass-after-prompt-option] | S2dM07 P no value | KILLED L1 |
| docker/podman: only `login` | `_registry_login` | `docker run -p 8080:80` hidden | ...readable...[docker run -p 8080:80 nginx] | S2dM08 + run | KILLED L1 |
| a `-u` value is a user name | `_registry_login` values | `-u -pete` hidden | ...readable...[docker login -u -pete ...] | S2dM09 | KILLED L1 |
| `--password-stdin` carries none | `_registry_login` | stdin flag hidden | s2c launch_status (governed shapes) and readable rows | S2dM10 `--p` | KILLED L1 |
| pflag `-p=<pw>` keeps `-p=` | `_registry_login` | `=` lost / byte change | ...[docker-login-equals] | S2dM11 | KILLED L1 |
| a global option's value is skipped | `_subcommand` | `docker --config /c login -p` shown | ...[docker-global-option] | S2dM12 | KILLED L1 |
| npm `c` alias (MEASURED deref) | `_NPM_CONFIG` | `npm c set` shown | ...[npm-c-password] | S2dM13 | KILLED L1 |
| npm key: its last `:` segment | `_npm` | `//host/:_auth <pw>` shown | ...[npm-scoped-auth] | S2dM14 | KILLED L1 |
| npm: only `set` | `_npm` | `npm config get k1 k2` hidden | ...readable...[npm config get ... registry] | S2dM15 + get | SURVIVED, then KILLED L1 after 8b7d20f |
| `name=` prefix (`cmd=mysql`) | `_command_name` | log key=value form shown | ...[json-list-after-a-name, key-value-log] | S2dM16 | KILLED L1 |
| a path's base name | `_command_name` | `/usr/bin/mysql` shown | ...[mysql-absolute-path] | S2dM17 | KILLED L1 |
| a command ends at a shell operator | `_secret_positions` | `mysql ... | less -ppattern` hidden | ...readable...[... | less -ppattern] | S2dM18 | KILLED L1 |
| a value spelled like a command is still the value | `_secret_positions` +1 | `sshpass -p docker` shown | ...[sshpass-password-named-like-a-command] | S2dM19 | KILLED L1 |
| `;` ends a command | `_redact_commands` stops | `; less -ppattern` hidden | ...ends_at_a_shell_separator...[;] | S2dM20 | KILLED L1 |
| a line break ends a command | `_redact_commands` stops | next line's `-p...` hidden | ...ends_at_a_shell_separator...[\n] | S2dM21 | KILLED L1 |
| quotes/brackets around a word | `_split_word` | Python/JSON list form shown | ...[python-repr, json-list-after-a-name] | S2dM22 | KILLED L1 |
| a quoted value with spaces is hidden whole | `_quoted_value_end` | `-p 'a <pw>'` tail shown | ...[quoted-value-with-space] | S2dM23 | KILLED L1 |
| an unclosed quote runs to the line end | `_closing_words` | rest of the line shown (own bug b7e4dd5) | ...[quote-not-closed-on-the-line], linear-cost pin | S2dM24 | KILLED L1 |
| the text hook | `redact_text` | every text channel leaks | s2c pins (text) | S2dM25 removed | KILLED L1 |
| the argv hook | `_redact_list` | every structured channel leaks | s2c pins (argv) | S2dM26 off | KILLED L1 |
| (c) the authority rule | `policy.approval.commands_carry_secret` | review offered / prompt shown altered | s2c launch_status, s2d terminal/launch tests | S2dM27 False | KILLED L1 |
| (c) a plain callback is not asked | `policy.approval.ask` | `kriya proposal execute` shows redacted, runs raw | ...legacy_prompt_is_not_asked[mysql, token-flag] | S2dM28 off | KILLED L1 |
| (c) the refusal records REDACTED commands + fingerprint | `cli._runtime_command_decision` | raw command persisted | s2c launch_status, ...inside_a_launch... | S2dM29 raw | KILLED L1 |
| (c) the refusal records the operator message, not the reason | `cli._runtime_command_decision` | raw reason (quotes the command) persisted | same | S2dM30 subject.reason | KILLED L1 |
| (c) the terminal is not asked | `cli._runtime_command_decision` | prompt shows redacted, runs raw | ...terminal_prompt_refuses...[mysql, ...] | S2dM31 off | KILLED L1 |
| operator `--yes` unchanged | `cli._runtime_command_decision` | operator path refused | ...operator_yes_still_runs...[all] | S2dM32 refuse yes | KILLED L1 |
| a review recorded raw before S2d is not served | `ReviewService.runtime_command_review` | raw commands served | ...recorded_raw_before_s2d...[mysql, token-flag] | S2dM33 off | KILLED L1 |
| the desktop review is not recorded | `build_pending_runtime` | pending review recorded raw | s2 ...token_argument_does_not, s2c/s2d not_offered pins | S2dM34 check removed | KILLED L1 |

Governed controls (green on the unfixed bytes AND after; the rows added in 6fa4d11/8b7d20f too, as they need no grammar): 36 readable look-alikes byte-identical as text and argv, 3 separator/line-break controls
(`mysql -P 3306`, bare `mysql -p`, `docker login --password-stdin`, `docker run -p`, `docker compose -p`,
`docker-compose -p`, `podman run -p`, `ssh -p 22`, `scp -P`, `psql -p 5432`, `curl -p`, `ls -p`, `npm config set
registry`, `npm config get <credential key>`, `npm set init-author-name`, `npm run build -- -p`, `sshpass -f/-e/-P/-d`,
`cat /etc/mysql/my.cnf`, `ls -p /usr/bin/mysql`, `./scripts/mysql-backup.sh -p 5`, `mysqldump -P`, `mysql:8` image ...);
the name and flag rules' bytes (`--password <pw>`, `--password=<pw>`, `_authToken=<pw>`, the recorded `mysql --password
<db>` over-redaction); never-mutates; idempotence over a generated 228-text corpus x {text, value}; the operator paths
(`--yes` approves the exact fingerprint; `guardrails` mode and a goal-authored command are not asked; the fingerprint is
the digest of the exact bytes); a plain runtime command still offered and served; four documented limits pinned SHOWN.

Own bugs (each fixed, tested with a check that fails without the fix, committed separately): a69899b (an unclosed quoted
value scanned the rest of its line: 200 KB took 30.5 s; MEASURED), b7e4dd5 (such a value showed the rest of its line),
6d6953a (an unknown sshpass option stopped the scan and showed the password).

## What the launch record persists (MEASURED, `s2d/{before,after}/channels.txt`, the launch record file)

| path | before (7e7aff7) | after |
|---|---|---|
| positional secret, desktop launch | review OFFERED: `pending_approval` / `approval_history` hold the raw argv (markers 1-6) | not offered; `approval_required` = {kind `runtime_command`, reason RUNTIME_COMMAND_SECRET_REFUSAL, details {commands: REDACTED, command_fingerprint: sha256 of the exact bytes}} |
| S2b `--token` argv, desktop launch | not offered; `approval_required` details.commands and reason RAW (marker 7) | same record shape as above: redacted + fingerprint, no raw reason |
| an unreviewable command set (none to show) whose reason quotes a secret | reason RAW | unchanged, RAW (S20 I-9); `launch.status` projects it redacted |
| change-review reason, declined decisions, ended error | RAW | unchanged (S20 I-9, I-3, OI-134) |

Left to S20 I-9: every OTHER free-text field of the launch record (`approval_required.reason` of the non-secret refusal
paths, `declined_decisions`, the change review's `reason`, `ended.error`) - the persisted-representation half of OI-153 (B).

## STOP: the typed reason code (no closed table can take it without a golden or KUP-schema move)

Production is fail-closed without it (the refusal is typed by WHERE it is recorded - `approval_required.kind
runtime_command` with the fixed refusal text and `command_fingerprint` - and a required check ends
REQUIRED_RUNTIME_VERIFICATION_DECLINED, an existing code). Measured semantic diffs (scratch `s2d/stop/`):

1. `kriya/policy/approval.py REFUSAL_CODES += "RUNTIME_COMMAND_SECRET_UNREVIEWABLE"` -> `tests/golden/policy_approval/
   serialized_forms.json` key `constants`: the last list gains `"RUNTIME_COMMAND_SECRET_UNREVIEWABLE"` (only that key moves;
   `test_persisted_and_serialized_forms_are_byte_identical_to_the_pre_move_golden` fails). Note: REFUSAL_CODES is the
   `kriya launch-decision` refusal vocabulary (a refused DECISION), not a refused OFFER.
2. `kriya/application/history.py BLOCKED_REASON_CODES += "RUNTIME_COMMAND_SECRET_UNREVIEWABLE"` (+ a typed
   `verification_infrastructure_failure` at `_approve_runtime_commands`, mirroring MANAGED_SERVICE_APPROVAL_UNSUPPORTED) ->
   `ui/kup/schema/history.schema.json` `blocked_reason_code.enum` + description, the generated `ui/kup/generated/
   {validators.mjs, ts/kup.ts, java/kup/BlockedReasonCode.java}`, `ui/shared/src/cockpit/core/presentation.ts`
   RUNTIME_STOP_WORDS (Python tests alone stay green: 85 passed - the schema is the contract that moves).
