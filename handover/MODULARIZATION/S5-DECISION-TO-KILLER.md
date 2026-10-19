# S5 decision-to-killer table (correction unit S5, TYPE C: typed refusal of unsupported platforms at entry; OI-101)

Mutants: `~/kriya-modularization-tools/mutrun.py` v2, deselect as S1. L1 = tests/test_s5_platform_entry_refusal.py,
L2 = test_cli_registration, test_platform_workspace_lock_contract, test_kup_serve, test_platform_architecture_guard,
L3 = test_cli_smoke, test_e14_cli_characterization, test_platform_host_identity_contract.

Campaign (MEASURED): `20261019_151720_229d9d36` at 541eb28: 14 mutants, 14 killed at L1, VALID, 208 s. S5M10 (guard
moved to `invoke`) was killed by a signature crash rather than by the parsing-order pin, so a correct-signature variant
was applied by hand (M10b, `invoke(self, ctx)`): killed by test_the_refusal_comes_before_option_parsing and
test_the_cli_refuses_in_process_through_the_seam_with_the_stable_exit_code (MEASURED, working tree restored).

Reproduction (MEASURED, scratch `s235/s5/before_runxfail.txt`, `after.txt`): before, with Windows-family or unknown-platform
services composed through the seam, `kriya config|authority inspect|runs status|version|tools execute filesystem read`
exited 0 and opened the workspace kriya.yaml (audit hook), the KUP server served (exit 0 on EOF) and kriya-mcp ran;
after, every entry exits 69 with the typed code on stderr, empty stdout and no open/listdir/scandir/mkdir/Popen/sqlite
event under the workspace or the homes; darwin/linux unchanged (the kriya.yaml open is still observed there).

| decision | symbol | risk | killer test | mutation | result | evidence |
|---|---|---|---|---|---|---|
| the Windows family is refused | `PlatformServices.entry_refusal` | Kriya runs on Windows with POSIX-only policy | test_the_windows_family_is_refused_at_cli_entry_before_anything_is_read | S5M01 | KILLED L1 | campaign |
| an unnamed platform is refused (fail closed) | same | FreeBSD/unknown approximated as supported POSIX | test_a_platform_the_support_policy_does_not_name_is_refused_at_cli_entry | S5M02 | KILLED L1 | campaign |
| the family decides before the name | same | Windows on a POSIX test host or a misnamed Windows reported as "unknown" | test_the_windows_family_is_refused... (code) | S5M03 order swapped | KILLED L1 | campaign |
| exact platform values, no prefix/case approximation | `SUPPORTED_RUNTIME_PLATFORMS` | `linux2`/`Darwin` admitted | test_the_entry_decision_per_platform_name | S5M04 prefix match | KILLED L1 | campaign |
| the supported set is exactly linux/darwin | same | set widened | test_a_platform_the_support_policy_does_not_name... | S5M05 | KILLED L1 | campaign |
| stable exit code | `ENTRY_REFUSED_EXIT_CODE` | indistinguishable from an ordinary error | every refusal pin | S5M06 exit 1 | KILLED L1 | campaign |
| the seam carries the platform name | `compose(platform_name)` | unknown platforms cannot be decided/simulated | unknown-platform pins | S5M07 name dropped | KILLED L1 | campaign |
| the CLI asks before anything | `_EntryGuardedGroup.main` | the CLI runs | refusal pins | S5M08 guard removed, S5M09 plain group | KILLED L1 (x2) | campaign |
| ... before option parsing | same (`main`, not `invoke`/callback) | `--config` stat and `--version` run first | test_the_refusal_comes_before_option_parsing | S5M10 (crash) + M10b manual | KILLED L1 | campaign + manual |
| the KUP server refuses | `kriya.kup.serve.run` | the desktop host is served | test_the_kup_server_refuses_at_entry | S5M11 | KILLED L1 | campaign |
| kriya-mcp refuses | `kriya.mcp.server.main` | tools served to an MCP client | test_the_mcp_server_entry_refuses | S5M12 | KILLED L1 | campaign |
| operator-readable message | refusal on stderr | silent exit | refusal pins (code + "not supported" on stderr) | S5M13 | KILLED L1 | campaign |
| closed reason table | `ENTRY_REFUSAL_REASONS` | an untyped/extra code | test_the_refusal_table_is_closed_and_every_code_is_operator_readable | S5M14 | KILLED L1 | campaign |
