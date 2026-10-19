# S2b decision-to-killer table (correction unit S2b, TYPE C: redaction completeness + history display; OI-98, OI-131, OI-134 check)

Mutants: `~/kriya-modularization-tools/mutrun.py` v2 (git archive of HEAD 76f3669, sha256 manifest before/after; deselect
`not test_engine_runtime_verification_trace and not test_post_check_passes_on_the_reference`). L1 =
tests/test_s2b_redaction_completeness.py + tests/test_s2_redaction_owner.py; L2 = test_application_services,
test_launch_approval, test_kup_runtime_command_review (never reached).

Campaign (MEASURED): `20261019_210115_1298fd5b` at 76f3669: 27 mutants, 27 killed at L1, 0 survived, VALID (tree_diff
empty, no straggler), 109 s.

Reproduction (MEASURED, scratch `s2b/{before,after}/`): `probe.py` (51 shapes through redact_text / redact_value) 38 leaking
before -> 2 after (both documented limits, below); `channels.py` (8 shapes through each channel) before: authority inspect,
authority approve, log file handler, KUP error envelope, CLI error text, KUP history.detail (GUI payload and its
application_outcome), history.list, history.prompt, `kriya traces` table = 10/11 channels leaking (the durable approval
record held no value); after: 0/11; the stored trace row RAW before and after. Characterization f4e8665 + 7b9da2f: 48
strict-xfail flips, `--runxfail` 48 failed on the unfixed bytes (`before/runxfail2.txt`), all pass after f016e4d.
A further MEASURED defect of the S2 owner found on the way: the value pattern `\S+` ate the closing quote and the rest of a
JSON text (`{"command": ["java", "--token=***REDACTED***` - structure and non-secret text lost) and a query string's
other parameters; fixed (bare values stop at whitespace, a quote, a backslash, `& ; ,`).

| decision | symbol | risk | killer test | mutation | result |
|---|---|---|---|---|---|
| any Authorization scheme is hidden | `_AUTHORIZATION_HEADER` | `Authorization: Token x` / Digest params shown | ...hidden_and_the_rest_kept[auth-token-scheme, auth-digest] | S2bM01 rule removed | KILLED L1 |
| the scheme word is hidden too (S2 pin form) | `_authorization` | form drift / scheme disclosed | test_s2 ...free_text[bearer] | S2bM02 single marker | KILLED L1 |
| Cookie / Set-Cookie | `_COOKIE_HEADER` | session cookie shown | ...[cookie, set-cookie] | S2bM03 removed | KILLED L1 |
| private-key PEM body | `_PRIVATE_KEY_PEM` | key material shown | ...[pem, pem-escaped-newlines] | S2bM04 disabled | KILLED L1 |
| a CUT PEM block (no END) | `_PRIVATE_KEY_PEM` `\Z` | truncated output leaks the key | ...[pem-cut] | S2bM05 `\Z` removed | KILLED L1 |
| scheme-relative `//user:pw@` | `_SCHEME_RELATIVE_USERINFO` | password shown | ...[scheme-relative] | S2bM06 removed | KILLED L1 |
| argv flag + separate value | `_FLAG_VALUE` | `--password x` shown | ...[argv-space, argv-curl-user] | S2bM07 removed | KILLED L1 |
| `-u user:pw` keeps the user | `_flag_value` user branch | password shown / user lost | ...[argv-curl-user] | S2bM08 branch off | KILLED L1 |
| name<sep>value by the word rule | `_redact_pairs` | `DB_PASSWORD=` etc. shown | test_s2 runtime-review pin (first of many) | S2bM09 not called | KILLED L1 |
| `authorization` only as the header name | `_is_secret_name` | reason codes / prose hidden (readability) | test_text_without_a_secret_stays_byte_identical[SOURCE_CHANGED_..., CONTRACT CHANGE ...] | S2bM10 / S2bM27 narrowing removed | KILLED L1 (x2) |
| a bare value stops at `&` | `_VALUE` | other query parameters lost | ...[query-keeps-other-params] | S2bM11 `&` dropped | KILLED L1 |
| a quoted value is hidden whole | `_VALUE` single-quoted | `'a b'` tail shown | ...[single-quoted-spaces, spaced-eq-quoted] | S2bM12 alternative disabled | KILLED L1 |
| quotes are kept | `_hidden_value` | quoting/structure changes | same | S2bM21 bare marker | KILLED L1 |
| a json.dumps text is redacted structurally | `_redact_json_document` | blob and parsed form disagree; env/argv in a blob shown | test_the_history_detail_blob_and_its_parsed_section_say_the_same_thing | S2bM13 route off | KILLED L1 |
| only an exact json.dumps text is re-serialized | `_redact_json_document` round-trip check | non-secret JSON reformatted | ...byte_identical[`{"command":["java"],  "n": 1}`] | S2bM14 check removed | KILLED L1 |
| list: the element after a secret flag | `_redact_list` | `["--token", "x"]` shown | ...[json-argv-in-string], structure_is_hidden[argv-token] | S2bM15 flag tracking off | KILLED L1 |
| only DASH flags pair (names lists stay) | `_flag_name` | `env_keys: ["API_TOKEN", "OTHER"]` hides OTHER | ...[names-list] (x2) | S2bM16 dash check removed | KILLED L1 |
| `PassWord` is one word | `is_secret_key` plain split | regression of the S2 case-insensitive text rule | test_a_mixed_case_secret_name_stays_hidden | S2bM17 split removed | KILLED L1 |
| `ssh_key` | `_SECRET_LAST_PAIRS` | key shown | ...[ssh-key] | S2bM18 removed | KILLED L1 |
| GitHub fine-grained token | `_SHAPED_SECRETS` | token shown | ...[github-fine-grained] | S2bM19 removed | KILLED L1 |
| URL userinfo ends at a quote | `_URL_USERINFO` | redaction spans two quoted strings | ...byte_identical[`url='http://h',user='a@b'`] | S2bM20 stop-set reverted | KILLED L1 |
| OI-131: the detail projection is redacted | `HistoryService.detail` | GUI shows the argv secret | test_the_gui_receives_a_history_detail_with_every_secret_hidden | S2bM22 raw | KILLED L1 |
| history pages: the goal | `_display_page` | GUI list shows a secret goal | test_the_history_list_and_the_traces_table_hide_a_secret_in_the_goal | S2bM23 raw | KILLED L1 |
| history.prompt | `HistoryService.prompt` | GUI shows the stored prompt's secret | test_the_gui_receives_a_history_prompt_with_every_secret_hidden | S2bM24 raw | KILLED L1 |
| traces table: redact BEFORE the cut | `cli.traces` | a cut quote leaves the value unrecognised | test_the_traces_table_redacts_before_its_40_character_cut | S2bM25 cut first | KILLED L1 |
| launch review reads the argv WHOLE | `build_pending_runtime` | `--token x` offered for review (GUI shows it) | test_a_runtime_command_carrying_a_secret_argument_is_not_offered_for_review | S2bM26 element-wise | KILLED L1 |

Governed controls (the unfixed-bytes result of each): 27 readable texts byte-identical after (incl. `max_tokens=4096`,
`--token-file`, `python -u`, `curl --user alice`, `//just/a/path`, certificate PEM, `git@host:repo`, digests); on the
unfixed bytes 25 were identical and 2 were NOT (MEASURED, `before/readability_pins_old.txt`, two more S2 defects fixed
here): `CONTRACT CHANGE NEEDS YOUR AUTHORIZATION: the change ...` lost "the", and `url='http://h',user='a@b'` became
`url='http://***REDACTED***@b'` (the userinfo rule ran across the closing quote into the next string); 8 readable structures identical before and
after; never-mutates (before: pass; after: pass); idempotence over a generated 102-text corpus x {text, value} (before:
pass; after: pass); the stored row stays RAW after a history.detail read (before and after); the history blob equals its
parsed section (before and after); the durable approval record holds no value (before and after); `PassWord` stays hidden
(before: pass by S2's case-insensitive text rule; after: pass - it FAILED once during the correction, own slip caught before
any commit, now pinned and mutant S2bM17).

Known limits (documented in the owner docstring and user guide, not leaks of a recognised shape): an UNQUOTED secret value
containing whitespace, a quote, a backslash or `& ; ,` is hidden only up to that character; a header kept as a two-element
list (`["Authorization", "Token x"]`) carries no flag or key to recognise (a name-pair rule would hide `env_keys` names).
