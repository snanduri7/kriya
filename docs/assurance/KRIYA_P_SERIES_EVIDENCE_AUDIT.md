# P1–P8 Raw Production Evidence Audit

Every P-run's actual latest `RESULT.md` was read directly this pass (not
recalled from session memory), at
`~/kriya-live-validation/PRVS/kriya-prv-harness-1.0.1/production-validation/P<n>/results/<latest-timestamp>/`.
`RAW EVIDENCE UNAVAILABLE` applies to none of the eight — all located.

**The single most important finding: P-runs are not evidentially equal.**
P1–P5 show `Kriya exit code: 0` and a clean, goal-scoped diff, which is
real, solid evidence Kriya's own deterministic pipeline succeeded — but
their `RESULT.md`'s own "Manual Follow-Up" checklist (compile/test
confirmation, scope-touch confirmation) is a static template with no
field in the raw artifact confirming a human ever completed it. P6 and P7
are categorically different: both contain **explicit, independently-
computed acceptance evidence recorded directly in the artifact** —
real observed-vs-expected data (P6) or three independently-graded
verification layers plus a computed `P7_PASS: True` field (P7) — neither
self-graded by Kriya. P8 is the strongest: I ran it myself this session,
directly observed the JSON, the diff, and ran independent verification
myself in real time, not from an artifact after the fact.

| P | Target repo | Goal (one line) | Kriya terminal result | Deterministic gate result | Independent/manual verification | Files changed | Kriya modified before/during? | Risk(s) actually exercised | E4 qualifying? | Limitation |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | `spring-ignite-demo` (baseline `887f179`) | Migrate Ignite 3.1.0→2.18.0, Java 21 | exit 0, wall 979.1s | Kriya's own gates passed (implicit in exit 0 + clean diff) | **Not evidenced in the raw artifact** — manual compile/test/console-output checklist present but unticked in the file itself | `EmployeeRepository.java`, `EmployeeService.java` | No (this session's own record: `b12e58e`/`887f179` fixes landed *before* this run, per project memory — not re-verified against Kriya's own git log this pass) | `CORR-017`, `TOP-MVN-001`, `TOP-OWN-001`, `TOP-REPO-001` | **Partial** — Kriya's own deterministic-gate evidence is solid; full task-correctness confirmation is not independently provable from this artifact alone | Manual follow-up not confirmed in-artifact |
| P2 | `spring-ignite-demo` (baseline `2f201ea`) | Cap `giveRaise` salary, preserve untouched-department employees | exit 0, wall 1721.5s | Same as P1 | Same limitation as P1 — unticked checklist | `EmployeeService.java`, `EmployeeServiceTest.java` | Real Kriya fixes landed across this scenario's own earlier attempts (`b6685cb` and others, per project memory) — this final run is post-fix | `CORR-005`, `CORR-011`, `CORR-002`/`CORR-003` (`TOP-OWN-003`) | Partial, same reasoning as P1 | Same |
| P3 | `spring-ignite-demo` (baseline `7b36172`) | Add location-uniqueness check, preserve id-uniqueness | exit 0, wall 991.0s | Same | Same limitation | `DepartmentRepository.java`, `DepartmentService.java`, `DepartmentServiceTest.java` | Real fix (`4768db1`, `goal_requires_runtime_behavior()`) landed pre-run per memory | `CORR-012` (`INV-GOAL-001`), `CORR-008`/`009` | Partial | Same |
| P4 | `spring-petclinic-rest` (baseline `8526976`) | Sort `findAllPetTypes()` by name, preserve HTTP contract | exit 0, wall 998.7s | Same | Same limitation | `ClinicServiceImpl.java`, `ClinicServiceImplPetTypeOrderingTests.java` | Real fix (`3b4e4ec`, `include_response_construction_owners()`) landed pre-run per memory | `CORR-017`, `TOP-REPO-001` — **now resolved, see §"P4" below** | Partial | Same |
| P5 | `spring-petclinic-rest` (baseline `6e4ea86`) | Add `prettytime` dependency, `Pet.getAgeDescription()`, strict pom.xml preservation | exit 0, wall 1354.2s | Same | Same limitation | `pom.xml`, `Pet.java`, `PetTests.java` | Real fix (`f070f25`, preserved-references reinforcement) landed pre-run per memory | `CORR-007` (`INV-PRESERVE-001`), `CORR-008`/`010` | Partial | Same |
| P6 | `spring-petclinic-rest` (baseline `7952781`) | Sort `findAllVets()` by lastName, proven via a real running process | exit 0, wall 1544.7s | Same, **plus explicit fields**: `Kriya's own log shows real verification commands executed: True` | **Independent, non-Kriya-self-graded**: a separate external process built+launched+queried+shut down the real app, recorded observed order `['Carter', 'Douglas', 'Jenkins', 'Leary', 'Ortega', 'Stevens']` against expected — matched. `independent_runtime_probe_order_correct` field present | `pom.xml`, `ClinicServiceImpl.java`, `VetServiceOrderingTests.java` | Real fixes (`e073d47`, `e790c1a`) landed pre-run per memory | `VER-002` (`INV-RUNTIME-001`/`002`) | **Yes — genuine E4** | None material |
| P7 | `modular-app` (baseline `7010d81`) | Extend `UserService`/`UserServiceImpl` cross-module, preserve reactor topology | exit 0, wall 998.9s | Same | **Independent, three layers, none self-graded**: Layer 1 (compile-time contract probe) PASSED, Layer 2 (independent Mockito behavioral test) PASSED, Layer 3a/3b (`mvn -pl repository -am test` + `mvn test` from root) both exit 0. Explicit `P7_PASS: True` field. Preservation contract: zero forbidden/unexpected path changes | `UserService.java`, `UserServiceImpl.java`, `UserServiceImplTest.java` | Real fix (`13a73b0`, Maven reactor-aware compile-check) landed pre-run per memory | `VER-001` (`INV-BUILD-001`), `CORR-015` (`INV-PLAN-005`), `TOP-MVN-002`/`003`/`004` | **Yes — genuine E4** | None material |
| P8 | `spring-boot-application-example` (baseline `6f7ed5a`) | Add case-insensitive `usernamePrefix` filter to driver-search, compose with existing filters | exit 0, wall 935.5s | Full pytest 57/57 confirmed **by me, directly, this session** | **Independent, run by me in real time**: 5 real authenticated HTTP requests against the actually-running app (positive/case-insensitive/no-prefix/composed/no-match cases), not from an artifact after the fact | `DriverController.java`, `DriverControllerTest.java` | **No — zero Kriya source changes**, confirmed via `git diff HEAD --stat` before and after | `CORR-017`, `REPO-001`/`002`, `OBS-001` | **Yes — the strongest E4 of the eight** | None |

## P4 — risk mapping resolved

P4's goal (`ClinicServiceImpl.findAllPetTypes()` ordering, HTTP-contract
preservation) exercises the same general claim `CORR-017` already
represents (baseline correctness on a real brownfield task, this time
with an explicit API-contract-preservation dimension) and the same
topology `TOP-REPO-001` already covers (confirmed directly from
`KRIYA_TOPOLOGY_COVERAGE.md`'s own totals table, which states
`P4→TOP-REPO-001`). **No new risk was created for P4** — its
preservation-under-refactor dimension is not materially distinct from
what `CORR-008`–`010` (`INV-PRESERVE-002`/`003`/`004`) already represent
either, so P4 is folded as additional corroborating evidence for
`CORR-017`/`TOP-REPO-001`, not a new row.

**P4 RISK MAPPING: RESOLVED** — mapped to `CORR-017`, `TOP-REPO-001`; no
new risk created, per the explicit instruction not to manufacture one
merely for coverage-count purposes.

## Net effect on `CORR-017`

P6 and P7's independently-graded PASS results, plus P8's own
personally-re-verified evidence, are **three materially distinct**
qualifying scenarios for the same general risk `CORR-017` represents:
different repositories (`spring-petclinic-rest` / `modular-app` /
`spring-boot-application-example`), different task shapes (runtime-order
sort with an external probe / cross-module interface extension with a
3-layer independent test / query-filter composition), different
topologies (single-module runtime-verified / multi-module reactor /
single-module). This is genuine E5 by the strict definition — **upgraded
from the prior pass's conservative E4**, this time earned through actual
independent-evidence confirmation rather than assumed. P1–P5's own
evidence remains real and solid (E3-level: Kriya's own deterministic
gates demonstrably ran and passed) but is not independently re-verified
at the same strength as P6/P7/P8, and is not needed to reach E5 once
three qualifying, materially-distinct, independently-graded instances
exist.

## P-SERIES RAW EVIDENCE AUDIT: 8/8
