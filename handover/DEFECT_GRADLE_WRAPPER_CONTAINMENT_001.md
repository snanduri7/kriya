# GRADLE-WRAPPER-CONTAINMENT-001: the Gradle wrapper could not start in containment and its trace was typed as a compilation failure

## Status
**FIXED** (2026-10-08) on `fix/backend-reliability-closure` from main f757e6b. Severity P2. Registry row CLOSED.
Discovered by the BACKEND-READINESS-001 blind cohort (T3 JavaHamcrest); fixed in BACKEND-RELIABILITY-CLOSURE-002
(evidence `~/kriya-m1-live/backend-reliability-closure-002/defects/gradle-containment/`).

## Observation (MEASURED, cohort T3)
Every gate ran `./gradlew` in `gradle:8-jdk8` with JAVA_TOOL_OPTIONS -Duser.home=/kriya/tmp, no Gradle cache mounted,
network denied; the wrapper tried to download gradle-8.10.1-bin.zip and failed with UnknownHostException
services.gradle.org at org.gradle.wrapper.Install.forceFetch; the adapter typed it "Gradle compilation failed", the
retry strategy opened a new failure family per attempt (budgets reset), escalated to the fallback model, five attempts,
a correct final candidate rejected. The host wrapper cache held the exact distribution
(~/.gradle/wrapper/dists/gradle-8.10.1-bin/e90i968nv55tch01zkse4avv3, extracted, `.zip.ok` marker, no zip).

## Producer (TRACED)
- No Gradle acquisition phase existed: `gradle.py` called `_run_cmd_with_timeout` directly, no `dependency_cache_path`,
  so `_toolchain_cache_mount`'s `/home/gradle/.gradle` never mounted anything, and nothing set GRADLE_USER_HOME.
- `services.gradle.org` / `plugins.gradle.org` were not in `acquisition_registry_hosts`.
- Attribution: any non-zero exit was "Gradle compilation failed" -> `compile` failure; `classify_environment_failure`
  has no Gradle wrapper marker; `retry_policy` saw an ordinary code failure.

## Fix (owner-decided order: Kriya-managed cache -> verified host seed -> bounded acquisition -> offline execution)
- `validate._run_gradle_cmd` (Maven's two-phase twin): host mode pass-through; contained: the Kriya-managed Gradle home
  `<state>/dependency-cache/gradle/<workspace key>` mounted at `/kriya/cache/gradle` (GRADLE_USER_HOME set only when a
  Gradle home is mounted); `seed_gradle_distribution_from_host` copies the project's declared wrapper distribution from
  the host user's cache only with its identity verified (Gradle's own md5-base36(url) directory, the `.zip.ok`
  completion marker, the extracted `gradle-<version>/lib`; a declared distributionSha256Sum must match the zip, else
  refused; provenance recorded on the gate result); one offline run (`--offline --no-daemon`), on a missing
  distribution/plugin/dependency ONE registry-scoped acquisition running the same tasks (never unrestricted), one more
  offline run. A wrapper that still cannot start -> `environment_reason_code GRADLE_DISTRIBUTION_UNAVAILABLE`; a
  dependency still missing -> `GRADLE_ACQUISITION_INCOMPLETE:` marker (repair-eligible, like Maven's).
- Containment: the registry-scoped setup script exports GRADLE_OPTS and JAVA_TOOL_OPTIONS with the proxy system
  properties (how to reach a host only; which hosts exist stays the firewall+proxy ACL); `gradle`/`gradlew` commands
  get the Gradle image and mount in the non-identity path.
- Config: `services.gradle.org`, `plugins.gradle.org` join `acquisition_registry_hosts` (exact hosts, no wildcards).
- Attribution: `attempt._stop_on_environment_gate_result` raises the existing `verification_infrastructure_failure`
  (STOP_ENVIRONMENT) for a gate result carrying `environment_reason_code`, at every compile/test/regression gate site.
Residual, not fixed here: T3's `gradle/versioning.gradle` runs `git describe --tags`; under the gitfile mask of a worktree
that may fail at configuration time - to be measured by the T3 rerun and classified then.

## Verification
- `tests/test_gradle_wrapper_containment_001.py` (17): wrapper properties + Gradle's own hash (MEASURED value);
  verified host seed, every unverified shape refused (no marker, no extracted version, wrong hash, declared checksum
  without a zip, mismatching checksum), checksum verified; classification of the measured wrapper trace, offline
  misses and ordinary failures; host pass-through; offline success never acquires and mounts the managed home; one
  registry-scoped acquisition then the offline run decides; a wrapper still failing is the environment outcome through
  the adapter; a still-missing dependency is the marker; the adapter's test path; the typed infrastructure stop through
  `handle_attempt_failure` (stops, no compile family); containment env/proxy plumbing; exact approved hosts; real
  containment (gradle:8-jdk17, a Maven Central dependency: DENIED miss -> REGISTRY_ONLY acquisition -> DENIED compile).
- Mutations (`mutations.txt`): 7/7 killed (no acquisition, wrapper failure untyped, attribution helper silent, seed
  without marker, checksum unverified, GRADLE_USER_HOME dropped, acquisition offline).
- Adjacent: Maven acquisition, capability adapters, SEC-002/005/009, registry-scoped unit, PRD-011, failure grounding,
  runtime Maven acquisition, resource authority, PRD-012 inventory, architecture guard, failure reporting: 331 passed
  (one SEC-009 default-list expectation updated with the same justification). ruff + pylint 0.
