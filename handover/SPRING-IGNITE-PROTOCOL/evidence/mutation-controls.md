# Mutation / negative controls (outcomes TRANSCRIBED from the commands run on 2026-10-16; the mutants were scratch copies, never committed)

## qualify_spring_ignite.py runtime (host mode, desktop-app environment) - mutant of the reference -> failed checks
| Mutant | Failed checks |
|---|---|
| wrong printed value (`dataLength + 1`) | `expected_result_line_exact` |
| `System.exit(3)` after the line | `gate_success`, `exit_code_0` |
| `ignite-spring` dependency removed | compile failure: every runtime check |
| Ignite bypassed (Main prints the line from the decoded object) | `ignite_node_started`, `ignite_node_stopped`, `protocol_class_registered_with_ignite_marshaller` |
| a String put into the cache instead of the Protocol | `protocol_class_registered_with_ignite_marshaller` only (the output is identical) |
| **SURVIVES (reviewer-found):** `get` result discarded, `fromCache = decoded` | none: the runtime checks prove the put, not the read-back (documented in QUALIFICATION.md) |

## verify_demo.sh --scenario 04 --phase post (after the hardening) -> FAIL lines
| Mutant | Result |
|---|---|
| reference | only the (unrelated) "tracked tree is clean" line fails while the checkout is dirty |
| Ignite-less Main that prints the line | FAIL: no Ignite start/stop lines; FAIL: marshaller registry names no Protocol |
| String in cache | FAIL: marshaller registry names no Protocol |
| leftover `ignite/` directory in the project | FAIL: an ignite/ work directory is left |
| every test `@Disabled` | FAIL: executed no tests or skipped some |
| no `src/test` | FAIL: no test under src/test/java |
| workspace byte-identical before and after the check | yes |

## tests/test_demo_v2_spring_ignite.py - assertion-level mutants (each failed at least one test)
`knowledge.offline_mode: false`; a Maven command in the goal; reset copying the reference into the workspace; reset skipping the skill digest guard; the reference without `cache.put`; a vacuous `assertEquals(true, true)` round trip.
