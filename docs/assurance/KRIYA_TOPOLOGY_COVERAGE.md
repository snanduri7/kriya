# Kriya Topology Coverage Matrix

R1 Deliverable 2. This document distinguishes **capability implemented** from **topology executably validated**. A production code path existing for a shape is not sufficient to mark that shape `VALIDATED` — every `VALIDATED` row below points to an executable test (grep-verified to exist under the exact name cited) or a specific frozen P1–P7 production-validation scenario, and every claim is written as narrowly as the evidence actually supports.

## Validation-level definitions

- **UNIT_ONLY** — the enforcing function called directly with hand-built/mocked inputs; no real file I/O or parsing of the target domain.
- **INTEGRATION** — the enforcing function called directly, but with real file content, real parsing, and real I/O exercised (e.g. a real `pom.xml` parsed, real `.class` files on disk); not routed through the full `WorkflowController`/generation orchestration, and in some cases (noted per-entry) *is* the real production entrypoint with no separate orchestration boundary above it to additionally cross.
- **VERTICAL** — drives the real orchestration entrypoint (`WorkflowController.execute()`, `run_generation_workflow()`, or the real retry/repair loop) end to end, in this repository's own test suite.
- **PRODUCTION_PRV** — a real, frozen P1–P7 production-validation run (a live LLM-driven run against a real external repository) actually exercised this shape.

Multiple levels apply to several rows; each is listed explicitly rather than collapsed to one label.

---

## 1. Maven build/module topology

### TOP-MVN-001 — Single-module Maven project, root `target/classes` is the compile target

**Topology:** A Maven project whose `pom.xml` declares no `<modules>` block (default, implicit `packaging=jar`), where the candidate `.java` files' compiled output is expected directly under `<workspace_root>/target/classes`.

**Evidence:** `test_java_compile_check_catches_maven_false_positive_when_nothing_actually_compiled`, `test_java_compile_check_trusts_maven_success_when_something_really_compiled` (`tests/test_polymorphic_validation.py`) — bare `<project></project>` pom, no `<modules>`. Also `PRODUCTION_PRV`: P1 (spring-ignite-demo), P2, P3 — all single-module Maven Java 17/21 projects, each with a real, completed live run.

**Production Path Exercised:** `PolymorphicValidator.run_compile_check()` (`kriya/tools/validate.py`), root-`target/classes` branch.

**Validation Level:** INTEGRATION, PRODUCTION_PRV

**Relevant Invariant:** None of `KRIYA_INVARIANT_CATALOG.md`'s 19 entries is scoped to this baseline case specifically (`INV-BUILD-001` is scoped to the *reactor* case below) — this topology predates and is a precondition for that invariant, not itself audited as a P1–P7 historical defect.

**Status:** VALIDATED

**Note on origin:** the false-positive test above is a real regression test, but from an *earlier* incident (`ignite_qpid_protocol` milestone 3/4, 2026-08-22) outside the P1–P7 production-validation track — recorded accurately rather than folded into the P7 narrative.

---

### TOP-MVN-002 — Flat, statically-declared multi-module Maven reactor; owning-module compile output, not root

**Topology:** A Maven reactor whose root `pom.xml` has `packaging=pom` and a `<modules>` block directly listing child module directory names (e.g. `<modules><module>core</module><module>repository</module><module>api</module></modules>`). Compile verification must check each candidate `.java` file's *owning declared module's own* `target/classes`, and must not require or expect the aggregator root's own `target/classes` to exist (a real Maven aggregator produces none there).

**Could a materially different topology satisfy this wording while bypassing the evidence?** No — the wording is scoped to a *direct*, *flat*, *statically-declared* `<modules>` list, matching exactly what `get_pom_reactor_modules()` (`kriya/tools/validate.py`) parses (`root.find("modules")`, one level, no profile or nested recursion — see `TOP-MVN-005`/`TOP-MVN-006` below for what this explicitly does not cover).

**Evidence:** `test_reactor_compile_check_succeeds_when_owning_modules_have_real_classes` (`tests/test_polymorphic_validation.py`) — root `packaging=pom`, 3 declared modules, aggregator's own `target/` confirmed absent (`assert not (tmp_path / "target").exists()`), candidates' owning modules (`core`, `repository`) have real `.class` output. `PRODUCTION_PRV`: P7 attempt 3 — a real, live LLM-driven run against `modular-app`, a genuine 3-module Maven reactor, completed successfully (`P7_PASS: True`, real `mvn` invocation, not mocked).

**Production Path Exercised:** `get_pom_reactor_modules()` + `PolymorphicValidator.run_compile_check()`'s reactor branch (`kriya/tools/validate.py`).

**Validation Level:** INTEGRATION (only `subprocess.Popen` mocked in the test suite; real `pom.xml` XML parsing, real filesystem `target/classes` checks), PRODUCTION_PRV (P7 attempt 3, real `mvn`)

**Relevant Invariant:** `INV-BUILD-001`

**Status:** VALIDATED

---

### TOP-MVN-003 — Compile-output verification scans every owning module in the change, not just the first

**Topology:** A single change whose candidate `.java` files span **two or more distinct** declared reactor modules; compile verification must check each distinct owning module's own output independently and report the specific module(s) actually missing compiled output, not stop after the first module checked.

**Could a materially different topology bypass this wording?** No — this is narrower than `TOP-MVN-002` on purpose: a candidate set confined to a single module would not exercise the "keep checking every distinct owning module" loop at all.

**Evidence:** `test_reactor_compile_check_scans_every_owning_module_not_just_the_first` (`tests/test_polymorphic_validation.py`) — candidates span `core` (real output) and `repository` (no output); asserts the loop correctly identifies `repository` specifically, not whichever module happens to be checked first. Also exercises `PolymorphicValidator._java_reactor_modules_missing_compiled_output()` directly. `test_reactor_compile_check_stale_unrelated_module_classes_do_not_grant_false_success` further confirms a *third*, unrelated module's stale `.class` output cannot satisfy the check for a module that genuinely owns the only candidate.

**Production Path Exercised:** `PolymorphicValidator._java_reactor_modules_missing_compiled_output()` (`kriya/tools/validate.py`).

**Validation Level:** UNIT_ONLY (direct call to the internal scanning method) + INTEGRATION (the same shape is also driven through the public `run_compile_check()` entrypoint in the same test)

**Relevant Invariant:** `INV-BUILD-001`

**Status:** VALIDATED

---

### TOP-MVN-004 — Multi-module reactor with cross-module interface/implementation resolution

**Topology:** A Maven reactor (same flat, direct-`<modules>` shape as `TOP-MVN-002`) where a Java `interface` is declared in one module (e.g. `core/.../ports/UserService.java`) and a class in a **different**, dependent module implements it via an explicit `implements` clause and `import` (e.g. `repository/.../UserServiceImpl.java implements UserService`).

**Could a materially different topology bypass this wording?** No — the evidence is specifically for an `implements`-clause-plus-`import` resolution path. This does **not** generalize to interface resolution via other mechanisms (a field type, a generic bound, a method return type with no `implements` clause) — none of those shapes appear in the cited test.

**Evidence:** `test_p7_reproduction_structural_evidence_resolves_interface_module_boundary` (`tests/test_workflow_controller_enforce.py`) — `_seed_p7_shaped_repo()`'s real files confirm `UserService.java` (interface, `core` module) → `UserServiceImpl.java` (`implements UserService`, `repository` module); asserts the edge resolves in `edges[_IMPL_PATH]` and the companion class→DTO edges (already working) did not regress. `PRODUCTION_PRV`: P7 preflight and attempt 3 both exercised this exact real repo shape live.

**Production Path Exercised:** `build_planning_structural_evidence()`'s class/interface declaration regex (`kriya/workflow/workflow_controller.py`).

**Validation Level:** INTEGRATION (direct call to `build_planning_structural_evidence()` against real seeded files — this function has no separate orchestration boundary above it in the sense `TOP-MVN-002` describes; it is itself the real per-round call the enforce loop makes), PRODUCTION_PRV

**Relevant Invariant:** `INV-PLAN-005`

**Status:** VALIDATED

---

### TOP-MVN-005 — Maven profile-activated modules (negative space, real implementation gap)

**Topology:** A reactor whose child modules are declared inside a `<profiles><profile><modules>...</modules></profile></profiles>` block, active only under a specific Maven profile, rather than in the root `<modules>` block directly.

**Implementation indication:** `get_pom_reactor_modules()`'s own body (`kriya/tools/validate.py`) parses exactly one direct child element, `root.find(f"{ns}modules")` — it does not look inside `<profiles>` at all. A profile-only module would be invisible to this function and would fall through to being treated as "not a reactor," the same as a genuinely single-module project.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** A profile-gated module's compiled output would never be checked, and (depending on which profile is active during a real Maven invocation) could produce either a false compile-success or a spurious "reactor not detected" fallback to the single-module root-`target/classes` check (`TOP-MVN-001`), which would almost certainly fail incorrectly against a real profile-driven multi-module layout.

---

### TOP-MVN-006 — Nested Maven reactors (negative space, real implementation gap)

**Topology:** A reactor where one of the declared child modules is *itself* a reactor aggregator with its own `<modules>` block (a module-of-modules layout).

**Implementation indication:** `get_pom_reactor_modules()` returns only the immediate `<module>` text values from the root `pom.xml`; it never recurses into a child module's own `pom.xml` to discover a further level of nesting.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** A leaf module two levels deep in a nested reactor would never be recognized as an "owning module" by `_java_reactor_modules_missing_compiled_output()`, producing the exact class of false compile-success `INV-BUILD-001` exists to prevent — for a topology one level deeper than what that invariant's own evidence covers.

---

### TOP-MVN-007 — Multiple independent (unrelated) reactor roots in one workspace

**Topology:** A single Kriya workspace containing two or more separate Maven projects, each with its own root `pom.xml`, that do not form one unified reactor (as opposed to one reactor with multiple modules).

**Implementation indication:** `PolymorphicValidator` is constructed against exactly one `workspace_path` and reads exactly one `pom.xml` at that root; there is no code path that discovers or reasons about a second, sibling reactor root elsewhere in the same workspace.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** A candidate file belonging to the second, undiscovered project's own reactor would be evaluated against the first project's `pom.xml`/module list entirely, an unrelated and incorrect basis.

---

## 2. Java structural evidence and ownership

### TOP-JAVA-001 — Constructor-instantiation and method-call structural resolution (baseline, pre-P7)

**Topology:** A test file resolving a structural edge to a production class via `new ClassName()` constructor instantiation or a uniquely-resolvable method call — the resolution paths that existed before the P7 interface fix.

**Evidence:** `test_structural_evidence_resolves_imports_calls_and_constructor_instantiation` (`tests/test_workflow_controller_enforce.py`) — real files, asserts import resolution (a non-candidate import produces nothing), method-call resolution (`service.find` → `CustomerService.java`), and constructor-instantiation resolution (`new CustomerController()`) all resolve real edges.

**Production Path Exercised:** `build_planning_structural_evidence()` (`kriya/workflow/workflow_controller.py`).

**Validation Level:** INTEGRATION

**Relevant Invariant:** `INV-PLAN-001`, `INV-PRESERVE-002` (both consume this same edge-resolution mechanism as their evidentiary basis)

**Status:** VALIDATED

---

### TOP-OWN-001 — Test file structurally references an unowned, unmodified production file (brownfield gap detection)

**Topology:** A test file (via any resolution path in `TOP-JAVA-001`) references a production file that no subtask in the plan owns for modification, and the goal genuinely requires that file to be modified — a true missing-producer gap.

**Evidence:** `test_missing_grounded_production_artifact_flags_the_exact_omitted_file` (`tests/test_workflow_controller_enforce.py`); `PRODUCTION_PRV`: P1's own `b12e58e` incident (the inverse false-positive direction) and every subsequent P-run's planning phase.

**Production Path Exercised:** `find_missing_grounded_production_artifacts()` (`kriya/workflow/workflow_controller.py`).

**Validation Level:** INTEGRATION, VERTICAL (`test_enforce_flags_missing_grounded_production_artifact_and_exhausts_on_repair`, real `WorkflowController.execute()`)

**Relevant Invariant:** `INV-PLAN-001`

**Status:** VALIDATED

---

### TOP-OWN-002 — Test file structurally references an unowned production file the goal does not require changing (preserved dependency)

**Topology:** Same structural shape as `TOP-OWN-001`, but the goal does not require the referenced file to change — a legitimate brownfield dependency, expressed via `preserved_references`.

**Evidence:** `test_missing_grounded_production_artifact_is_silent_when_target_declared_preserved`, `test_missing_grounded_production_artifact_acceptance_records_satisfied_preserved_reference` (`tests/test_workflow_controller_enforce.py`); `PRODUCTION_PRV`: P2 run 8, P3 attempt 2 (`Department.java` generalization), both real completed live runs.

**Production Path Exercised:** `find_missing_grounded_production_artifacts()`'s PRESERVE branch.

**Validation Level:** INTEGRATION, VERTICAL (`test_enforce_preserved_reference_acceptance_and_terminal_integrity_gate_a_real_run`), PRODUCTION_PRV

**Relevant Invariant:** `INV-PRESERVE-002`, `INV-PRESERVE-003`

**Status:** VALIDATED

---

### TOP-OWN-003 — Existing, already-planned downstream test file as a scope-denial-grounded merge target

**Topology:** A plan splits a production change (subtask A) from updating a pre-existing, already-planned pinned test that pins the *old* behavior (subtask B, `depends_on=[A]`). Subtask A's own regression gate cannot pass without editing the test, which is outside A's write scope; the denial must ground into merging B's ownership into A.

**Evidence:** `test_handle_attempt_failure_scope_denial_with_real_existing_test_owner_is_grounded` (`tests/test_workflow.py`); `test_enforce_grounds_stale_pinned_test_scope_denial_and_merges_to_success` (`tests/test_workflow_controller_enforce.py`); `PRODUCTION_PRV`: P2 run 1 (the original incident) and P2 run 8 (the eventual passing run, same shape).

**Production Path Exercised:** `_failure_from_validated_scope_denial()` → `revise_plan_for_grounded_scope_owner()`.

**Validation Level:** INTEGRATION, VERTICAL, PRODUCTION_PRV

**Relevant Invariant:** `INV-RECOVERY-001`, `INV-PLAN-002`

**Status:** VALIDATED

---

### TOP-OWN-004 — Test-only owning module (negative space)

**Topology:** A Maven module whose entire purpose is tests — its own declared reactor module producing only a test-classes output, with no `src/main/java` of its own — as the *owner* of a candidate file, rather than a `src/test/java` directory co-located inside a module that also has production sources.

**Implementation indication:** Every reactor fixture and every P1–P7 real repository used a conventional `src/main/java` + `src/test/java` split inside the *same* module. No test or frozen scenario constructs a module whose sole declared purpose is tests.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** `_java_reactor_modules_missing_compiled_output()`'s own compiled-output check looks at `target/classes` (main output), not `target/test-classes` — a module whose only real output is test classes could be misjudged as never having compiled anything.

---

## 3. Runtime and managed-service topology

### TOP-RUNTIME-001 — Packaged-artifact managed service: prepare, launch, HTTP readiness, unauthenticated HTTP probe, shutdown

**Topology:** A managed service whose `service_command` launches a packaged artifact (`java -jar <path>`), where the artifact does not yet exist and must be built by a preparation step before launch; readiness is polled via plain HTTP GET against a declared port/path; the pass/fail probe is a second plain (unauthenticated) HTTP GET expecting a specific status code; the process is torn down with a bounded shutdown timeout after the probe completes.

**Could a materially different topology bypass this wording?** No — "unauthenticated" is explicit and load-bearing: no test or frozen scenario constructs an `Authorization` header or any credential-bearing probe. This wording must not be read as "any HTTP-based managed service."

**Evidence:** `test_p6_reproduction_missing_jar_is_prepared_then_service_launches_and_probes`, `test_preparation_runs_before_service_start_and_before_readiness_polling`, `test_preparation_failure_prevents_service_launch`, `test_readiness_waits_for_actual_availability_before_probing` (`tests/test_service_runtime.py`) — the spawned process itself is real (`_TrackingController` wraps the real `ProcessController.start_managed`, a genuine OS subprocess), a real HTTP server script serves `/health`, and a real socket-level HTTP probe is made against a real bound port; only the **build step** is scripted (a fake `mvnw` producing a fake jar file, not a real Maven/Spring Boot build). `PRODUCTION_PRV`: P6 run 2, a real live LLM-driven run against `spring-petclinic-rest` — a genuine Spring Boot application, real Maven package, real launched JVM process, real HTTP probe.

**Production Path Exercised:** `run_managed_service_verification()` (`kriya/tools/service_runtime.py` / `kriya/workflow/attempt.py`'s managed-service branch).

**Validation Level:** INTEGRATION (real subprocess + real HTTP, scripted build step), PRODUCTION_PRV (real Maven + real Spring Boot, in the frozen P6 run)

**Relevant Invariant:** `INV-RUNTIME-001`

**Status:** VALIDATED — narrowly: this validates *one* Spring Boot–shaped, single-process, unauthenticated-HTTP-health-check service. It must not be read as "all managed services validated" (see `TOP-RUNTIME-002`–`004` below for the specific boundaries this does not cover).

---

### TOP-RUNTIME-002 — Authenticated HTTP probing (negative space)

**Topology:** A managed service whose readiness or pass/fail probe requires an `Authorization` header, a bearer token, a session cookie, or any other credential.

**Implementation indication:** No test in `tests/test_service_runtime.py` constructs a credentialed request; `ReadinessSpec`/`ProbeSpec` as exercised carry only `port`/`path`/`expected_status`. Whether the underlying spec schema *supports* an auth field was not investigated as part of this evidence-only audit (that would require inspecting `kriya/tools/service_runtime.py`'s schema definitions, a step this deliverable deliberately does not extend into design-intent territory beyond what tests prove).

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** A real service requiring authentication on its health/readiness endpoint would report `READINESS_TIMEOUT` or a probe failure indistinguishable from a genuinely broken service.

---

### TOP-RUNTIME-003 — Multi-process managed runtime (negative space)

**Topology:** A runtime verification requiring more than one cooperating process (e.g. an application server plus a database or message broker it depends on) launched and torn down together.

**Implementation indication:** Every managed-service test and the P6 frozen scenario launch exactly one `ManagedProcess`. No test constructs or tears down a second, dependent process.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** Unclear whether `run_managed_service_verification()`'s single-process lifecycle model could even represent a multi-process dependency graph; this is a real open question, not merely an untested instance of an already-supported shape.

---

### TOP-RUNTIME-004 — Non-Maven / non-jar service launch shapes (negative space)

**Topology:** A managed service launched via a non-Maven-packaged command — a directly-invoked interpreter script, a Docker container, a `gradle bootRun`-style command, etc.

**Implementation indication:** `TOP-RUNTIME-001`'s PREPARE-phase evidence is specifically for a `-jar` target requiring `mvn package`/`mvnw package`. No test exercises a PREPARE step for any other artifact/launch shape.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** A launch command whose readiness genuinely depends on a build/preparation step this codebase doesn't recognize as needing preparation would be launched prematurely, reproducing `TOP-RUNTIME-001`'s own historical P6 defect for a different artifact shape.

---

## 4. Language/stack breadth

### TOP-REPO-001 — Java/Maven brownfield repositories (the only topology carried through a full live production-validation run)

**Topology:** A real, pre-existing (brownfield, not Kriya-generated) Java repository built with Maven, ranging from a single-module Spring/Ignite-style project (P1–P5) to a genuine 3-module Maven reactor (P7), plus one real Spring Boot service (P6).

**Evidence:** `PRODUCTION_PRV` only — P1 (spring-ignite-demo), P2–P5 (same repo, progressively harder dimensions), P6 (spring-petclinic-rest), P7 (modular-app). All seven were real, live, LLM-driven runs, not synthetic fixtures.

**Production Path Exercised:** The full `run_generation_workflow()`/`WorkflowController.execute()` pipeline, end to end, for a real goal against a real repository.

**Validation Level:** PRODUCTION_PRV

**Relevant Invariant:** All 19 catalog entries were discovered against this topology family.

**Status:** VALIDATED (for Java/Maven specifically)

**Explicit non-generalization:** No P1–P7 run, and no test in this repository's own suite, drives the full generation pipeline end to end against a Python, Ruby, JavaScript/TypeScript, or Kotlin repository. `PolymorphicValidator._detect_stack()` (`kriya/tools/validate.py`) supports Python/Java/Ruby detection at the *unit* level (`test_python_compile_check` exists), but no full `PRODUCTION_PRV` run, and no `VERTICAL` test in this suite, exercises the complete pipeline against a non-Java repository. Kriya's own claimed language support is broader than what this specific audit validates — this row deliberately does not extend the claim.

---

### TOP-REPO-002 — Gradle projects (negative space, real implementation gap)

**Topology:** A repository built with Gradle (single- or multi-project).

**Implementation indication:** `PolymorphicValidator._detect_stack()` recognizes only `"python"`, `"java"`, `"ruby"`, or `"unknown"` — there is no Gradle-specific branch anywhere in `run_compile_check()`/`run_tests()`. A Gradle-only repository (no `pom.xml`, no Python/Ruby markers) would be classified `"unknown"`. Per this codebase's own documented contract (`CLAUDE.md`), an `"unknown"` stack makes `run_compile_check()`/`run_tests()` return `success: True` while stating plainly that nothing was actually validated — **not** a silent false success framed as a real pass, but also not a real compile/test gate. The command *classifier* (`deterministic_verification_kind()`, `kriya/workflow/verification_authority.py`) does recognize `gradle test`/`gradle check`/`gradlew` tokens for *runtime-verification command classification* — a materially different, narrower code path than the stack-detection/compile-gate machinery this row is about.

**Evidence:** NONE — no test constructs a Gradle project and drives `run_compile_check()`/`run_tests()`/a full generation run against it.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** A Gradle repository's compile/test gates would silently no-op (honestly labeled as such, per the stack-detection contract) rather than actually validating generated code — a real efficiency/correctness gap if a user pointed Kriya at one today, distinct from the topology simply being "untested."

---

### TOP-REPO-003 — Mixed-language modules within one repository (negative space)

**Topology:** A single workspace containing modules in more than one language (e.g. a Java backend module alongside a Python tooling module).

**Implementation indication:** `_detect_stack()` returns one stack string for the entire workspace, computed once. There is no per-module or per-candidate-file stack resolution anywhere in `PolymorphicValidator`.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

**Risk if assumed supported:** Whichever language's markers are detected first/dominate governs compile/test-gate behavior for the *whole* workspace, including files belonging to the other language's module — a real gap, not merely an untested instance of an already-general mechanism.

---

### TOP-REPO-004 — Generated sources / annotation-processor output (negative space)

**Topology:** A Java module whose compiled output includes classes produced by an annotation processor or another source-generation step, rather than solely from hand-written `.java` files under `src/main/java`.

**Implementation indication:** Not specifically investigated beyond what the compile-check tests exercise — `_java_reactor_modules_missing_compiled_output()`'s check is a bare "does `target/classes` contain any `.class` file for this module," which does not distinguish generated from hand-written output either way. Recorded as genuinely unknown rather than asserted as a gap, since no code path was found that would specifically mis-handle this case, but also none was found that specifically handles it.

**Evidence:** NONE.

**Status:** NOT_VALIDATED

---

## Validated Topologies

| ID | Topology | Evidence | Validation Level | Relevant Invariant | Status |
|---|---|---|---|---|---|
| TOP-MVN-001 | Single-module Maven, root `target/classes` | `test_java_compile_check_*_when_*compiled` | INTEGRATION, PRODUCTION_PRV | — | VALIDATED |
| TOP-MVN-002 | Flat multi-module reactor, `packaging=pom` root, owning-module output | `test_reactor_compile_check_succeeds_when_owning_modules_have_real_classes` | INTEGRATION, PRODUCTION_PRV | INV-BUILD-001 | VALIDATED |
| TOP-MVN-003 | Multiple owning modules scanned, not just first | `test_reactor_compile_check_scans_every_owning_module_not_just_the_first` | UNIT_ONLY, INTEGRATION | INV-BUILD-001 | VALIDATED |
| TOP-MVN-004 | Cross-module interface `implements` resolution | `test_p7_reproduction_structural_evidence_resolves_interface_module_boundary` | INTEGRATION, PRODUCTION_PRV | INV-PLAN-005 | VALIDATED |
| TOP-JAVA-001 | Import/call/constructor structural resolution | `test_structural_evidence_resolves_imports_calls_and_constructor_instantiation` | INTEGRATION | INV-PLAN-001, INV-PRESERVE-002 | VALIDATED |
| TOP-OWN-001 | Test references unowned, genuinely-required production file | `test_enforce_flags_missing_grounded_production_artifact_and_exhausts_on_repair` | INTEGRATION, VERTICAL | INV-PLAN-001 | VALIDATED |
| TOP-OWN-002 | Test references unowned, not-required (preserved) production file | `test_enforce_preserved_reference_acceptance_and_terminal_integrity_gate_a_real_run` | INTEGRATION, VERTICAL, PRODUCTION_PRV | INV-PRESERVE-002, INV-PRESERVE-003 | VALIDATED |
| TOP-OWN-003 | Existing downstream pinned test as scope-denial merge target | `test_enforce_grounds_stale_pinned_test_scope_denial_and_merges_to_success` | INTEGRATION, VERTICAL, PRODUCTION_PRV | INV-RECOVERY-001, INV-PLAN-002 | VALIDATED |
| TOP-RUNTIME-001 | Packaged-jar service: prepare/launch/HTTP-readiness/unauth-probe/shutdown | `test_p6_reproduction_missing_jar_is_prepared_then_service_launches_and_probes` | INTEGRATION, PRODUCTION_PRV | INV-RUNTIME-001 | VALIDATED (narrow — see entry) |
| TOP-REPO-001 | Java/Maven brownfield repos, full pipeline | P1–P7 frozen runs | PRODUCTION_PRV | all 19 catalog entries | VALIDATED (Java/Maven only) |

## Unvalidated / Partially Validated Boundaries

| Boundary | Current Implementation Indication | Evidence | Status | Risk if Assumed Supported |
|---|---|---|---|---|
| TOP-MVN-005 Profile-activated modules | Real gap — `get_pom_reactor_modules()` never reads `<profiles>` | NONE | NOT_VALIDATED | Profile module's output never checked; possible fallback to wrong single-module check |
| TOP-MVN-006 Nested reactors | Real gap — no recursion past one `<modules>` level | NONE | NOT_VALIDATED | A leaf module two levels deep is never recognized as an owning module |
| TOP-MVN-007 Multiple independent reactor roots | Real gap — one `workspace_path`, one `pom.xml` read | NONE | NOT_VALIDATED | Second project's candidates checked against the first project's module list |
| TOP-OWN-004 Test-only owning module | Untested combination; compiled-output check looks at `target/classes` only | NONE | NOT_VALIDATED | A module whose only real output is test-classes could be misjudged as never compiled |
| TOP-RUNTIME-002 Authenticated probing | Untested; spec schema not investigated | NONE | NOT_VALIDATED | Credentialed health checks indistinguishable from a broken service |
| TOP-RUNTIME-003 Multi-process runtime | Untested; single-process lifecycle model only | NONE | NOT_VALIDATED | Unknown whether the model can even represent this shape |
| TOP-RUNTIME-004 Non-jar/non-Maven launch shapes | Untested; PREPARE phase evidence is jar/mvn-specific | NONE | NOT_VALIDATED | Premature launch before an unrecognized build step, reproducing the P6 defect class for a different artifact |
| TOP-REPO-002 Gradle | Real gap — no Gradle branch in stack detection/compile-gate | NONE | NOT_VALIDATED | Compile/test gates silently no-op (honestly labeled, but non-functional) for a real Gradle repo |
| TOP-REPO-003 Mixed-language modules | Real gap — one stack string per workspace | NONE | NOT_VALIDATED | Wrong language's compile/test gate applied workspace-wide |
| TOP-REPO-004 Generated/annotation-processor sources | Unknown — not specifically investigated either direction | NONE | NOT_VALIDATED | Unknown |

---

## Cross-check against the invariant catalog

Every topology-sensitive invariant in `KRIYA_INVARIANT_CATALOG.md` was checked against this matrix for scope alignment — none was found to claim a broader topology than its evidence supports:

- `INV-BUILD-001` (Maven reactor compile-output) → `TOP-MVN-002`, `TOP-MVN-003`. The invariant's own wording ("in a genuine multi-module Maven reactor") is already appropriately scoped; this matrix narrows it further only by making the flat/direct/`<modules>`-only boundary explicit.
- `INV-PLAN-005` (interface indexing) → `TOP-MVN-004`. Confirmed the invariant does not overclaim beyond the `implements`-clause resolution path actually tested.
- `INV-PRESERVE-002`/`INV-PRESERVE-003` (preservation acceptance/terminal-integrity) → `TOP-OWN-002`. Confirmed evidence uses a real structural fixture, not a hand-waved edge dict.
- `INV-PLAN-001` (grounding) → `TOP-OWN-001`, `TOP-JAVA-001`.
- `INV-RECOVERY-001`/`INV-PLAN-002` (scope-denial grounding/merge) → `TOP-OWN-003`.
- `INV-RUNTIME-001` (managed-service prepare) → `TOP-RUNTIME-001`. Confirmed the invariant's own wording does not claim authenticated probing or multi-process support — it was already appropriately narrow.

No disagreement was found between the invariant catalog's claims and the topology evidence underlying them.

---

## Totals

- Topology dimensions identified: **20** (10 `VALIDATED`, 10 `NOT_VALIDATED`; 0 `PARTIALLY_VALIDATED` — every validated row had evidence sufficient for its own, narrowly-worded claim, and every gap found was a clean absence of evidence rather than partial/helper-only coverage)
- P1–P7 topologies mapped: **7/7** production-validation runs each map to at least one `VALIDATED` row (P1→TOP-MVN-001/TOP-OWN-001/TOP-REPO-001; P2→TOP-MVN-001/TOP-OWN-002/TOP-OWN-003/TOP-REPO-001; P3→TOP-MVN-001/TOP-OWN-002/TOP-REPO-001; P4→TOP-REPO-001; P5→TOP-REPO-001; P6→TOP-RUNTIME-001/TOP-REPO-001; P7→TOP-MVN-002/TOP-MVN-003/TOP-MVN-004/TOP-REPO-001)
- Topology-sensitive invariants cross-referenced: **6** of the catalog's 19 (`INV-BUILD-001`, `INV-PLAN-005`, `INV-PRESERVE-002`, `INV-PRESERVE-003`, `INV-PLAN-001`, `INV-RECOVERY-001`, `INV-PLAN-002`, `INV-RUNTIME-001` — 8 IDs across 6 topology-bearing rows; the remaining 11 catalog invariants concern plan-repair-loop/obligation-lifecycle mechanics that are not topology-specific in the sense this matrix addresses)

No production code, MA8, or MA9 was modified to produce this document. No new tests were written to fill any cell above — every `NOT_VALIDATED` row is recorded as a boundary, not a task.
