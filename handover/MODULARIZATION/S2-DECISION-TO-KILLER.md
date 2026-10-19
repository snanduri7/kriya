# S2 decision-to-killer table (correction unit S2, TYPE C: one redaction owner; OI-98, SEC-CLI-URL-CREDENTIAL-REDACTION-001)

One row per redaction decision S2 introduces or moves to the owner. Mutants were run with
`~/kriya-modularization-tools/mutrun.py` v2 (git archive of a fixed revision, sha256 manifest before/after; deselect
`not test_engine_runtime_verification_trace and not test_post_check_passes_on_the_reference`). L1 = tests/test_s2_redaction_owner.py,
L2 = test_policy_telemetry, test_sec009_p2_authority_approval, test_launch_approval, test_kup_project_context,
test_kup_config_approval, L3 = test_prd012_egress, test_kup_runtime_command_review, test_production_doctor,
test_logging_location, test_authority_port.

Campaign (MEASURED): `20261019_145226_09d3ba44` at dfacfa4: 34 mutants, 32 killed at L1, 2 at L2, 0 survived, VALID
(tree_diff empty), 146 s. S2M13 was killed only at L2 (a KUP path-too-long message): the L1 readability pin was
strengthened afterwards ("Basic authentication is not configured"), and the mutant re-applied by hand fails it (MEASURED).

Reproduction (MEASURED, scratch `s235/s2/{before,after}/repro.txt`): before, `authority inspect`, `authority approve`
(declined and accepted), `config`, `doctor`, `doctor --production --json`, the console/file log and a configuration
validation error each printed `alice:s3cretPW@` (and the embedding/search passwords); the three old redactors returned
userinfo, `API_KEY`, `auth_token`, nested `Authorization` and `%40` passwords in clear. After: every channel leaks
nothing; the three functions no longer exist (ImportError). The approval artifact held no value before or after.

| decision | symbol | risk | killer test | mutation | result | evidence |
|---|---|---|---|---|---|---|
| URL userinfo is hidden | `redact_text` `_URL_USERINFO` | `user:pass@` shown | test_authority_inspect_never_prints_url_credentials | S2M01 rule removed | KILLED L1 | campaign |
| userinfo runs to the LAST `@` of the authority | `_URL_USERINFO` class | unencoded `@` in a password leaks its tail | test_every_secret_bearing_value_is_hidden_in_the_authority_display[at-in-password] | S2M02 `[^\s/?#@]` | KILLED L1 | campaign |
| Bearer credentials | `_BEARER` | header token shown/logged | test_the_log_handlers_never_write_url_credentials | S2M03 removed | KILLED L1 | campaign |
| Basic credentials | `_BASIC` | base64 user:pass shown | test_every_secret_shape_is_hidden_in_free_text[basic] | S2M04 removed | KILLED L1 | campaign |
| Basic prose stays readable | `_BASIC` lookahead | "Basic setup" redacted (usability) | test_text_without_a_secret_is_returned_unchanged (strengthened); test_kup_project_context path-too-long | S2M13 lookahead removed | KILLED L2 (campaign), L1 after strengthening (manual) | campaign + manual |
| credential-named key=value | `_KEY_VALUE_SECRET` | `--token=x` shown; launch review offered | test_a_plain_runtime_command_stays_reviewable_and_a_token_argument_does_not | S2M05 removed | KILLED L1 | campaign |
| key name kept, plural too | `_KEY_VALUE_SECRET` group 1 | `tokens:` becomes `token:` (old scrubber bug) | test_every_secret_shape_is_hidden_in_free_text[kv-plural] | S2M14 plural outside the group | KILLED L1 | campaign |
| known token shapes | `_SHAPED_SECRETS` | AWS/sk-/gh tokens shown | ...free_text[sk], [aws] | S2M06 loop emptied | KILLED L1 | campaign |
| secret keys by WORD, not substring | `is_secret_key` | `max_tokens` hidden / misses | test_values_without_a_secret_stay_readable...[token-counts] | S2M07 substring | KILLED L1 | campaign |
| camelCase split | `is_secret_key` `_CAMEL` | `dbPassword` shown | ...[mixed-case-keys], test_a_secret_bearing_key_is_recognized_by_word[dbPassword] | S2M08 no split | KILLED L1 | campaign |
| two-word names | `_SECRET_LAST_PAIRS` | `api_key` shown | test_api_key_and_every_mcp_env_value_stay_hidden... | S2M09 pairs removed | KILLED L1 | campaign |
| env is a credential container | `redact_value` `_CONTAINER_KEYS` | env values shown | test_structures_hide_secret_keys_and_containers... | S2M10 removed | KILLED L1 | campaign |
| falsy values are shown (unset reads unset) | `_hide` | empty credential shown as REDACTED | test_api_key_and_every_mcp_env_value_stay_hidden... | S2M11 always REDACTED | KILLED L1 | campaign |
| every string leaf is checked | `redact_value` str branch | URL under a plain key shown | test_authority_approve_never_prints_url_credentials | S2M12 removed | KILLED L1 | campaign |
| `kriya config` goes through the owner | `cli.config` | dump in clear | test_the_config_command_never_prints_url_credentials | S2M15 raw dump | KILLED L1 | campaign |
| doctor shows the endpoint redacted | `cli.doctor` | Base URL in clear | test_doctor_never_prints_url_credentials | S2M16 raw | KILLED L1 | campaign |
| authority inspect/approve display | `describe_pending` | value in clear | test_authority_inspect_never_prints_url_credentials | S2M17 raw | KILLED L1 | campaign |
| every Kriya log handler redacts | `_open_file_handler`, `configure_logging`, `RedactingFormatter.format` | secret in kriya.log / terminal | test_the_log_handlers_never_write_url_credentials, test_the_console_log_never_writes_url_credentials | S2M18, S2M19, S2M20 | KILLED L1 (x3) | campaign |
| CLI error text | `_user_error_text` | pydantic `input_value` echoed | test_a_configuration_error_message_never_echoes_url_credentials | S2M21 | KILLED L1 | campaign |
| KUP error envelope / host refusal | `kup.inspect.error_envelope`, `kup.serve._refusal` | secret in a GUI payload | test_a_kup_error_envelope_never_carries..., test_a_kup_host_refusal_never_carries... | S2M22, S2M23 | KILLED L1 (x2) | campaign |
| redact BEFORE the 300-char cut | `application.configuration._state` | a cut userinfo loses its `@` and leaks | test_the_kup_configuration_refusal_redacts_before_its_300_character_cut | S2M24 cut first | KILLED L1 | campaign |
| production report evidence + refusal | `production_doctor._check`, `probe_llm_runtime` | endpoints/password in the JSON report | test_the_production_report_never_carries_url_credentials | S2M25, S2M26 | KILLED L1 (x2) | campaign |
| egress record | `EgressDecision.to_dict` | destination userinfo recorded | test_policy_records_never_carry_url_credentials | S2M27 | KILLED L1 | campaign |
| launch review refuses what redaction would alter | `build_pending_runtime` | a credential-bearing command offered for approval | test_a_runtime_command_carrying_url_credentials_is_not_offered_for_review, test_a_plain_runtime_command_stays_reviewable... | S2M28 check blinded | KILLED L1 | campaign |
| provider / egress / routing messages | `LLMClient.complete_result`, `OllamaEmbeddingClient._enforce_egress`, `_http_json`, `pin_served_configuration`, `Router._ensure_fitted` | password in an exception (shown, logged, recorded as probe evidence) | test_egress_violation_messages..., test_provider_probe_errors..., test_a_served_model_pin_refusal..., test_the_routing_error... | S2M29-S2M33 | KILLED L1 (x5) | campaign |
| telemetry summaries | `policy.telemetry._summarize` | decision record carries a secret | test_policy_telemetry::test_build_decision_record_scrubs_target_and_network_target | S2M34 | KILLED L2 | campaign |
| one owner, no delegate/re-export | the three removed names; no `"***REDACTED***"` / credential regex elsewhere in kriya | a second redactor drifts again | test_there_is_one_redaction_owner | (structural tripwire) | - | test |

Not mutated (no decision of their own): the second `complete_with_tools_result` egress message (identical text, same
fix; covered by review), `cli` live-lookup prompt lines (search.base_url display, same owner call).
