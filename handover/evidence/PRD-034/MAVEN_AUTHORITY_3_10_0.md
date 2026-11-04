# PRD-034 - the Maven 3.10.0 authority (owner ruling D, 2026-11-04)

Trigger: hosted run 41812207261 (HEAD 680f3d9) - the demo-v2 Maven pair failed because two different Mavens were in one path (see `hosted-run-41812207261-NOT_CERTIFIED/ROOT_CAUSE_TABLE.md`).
Ruling: ONE authoritative host Maven, Apache Maven 3.10.0, provisioned independently of the runner image; Option A; Maven 3.9.9 equivalence is no longer a PRD-034 requirement.
Status: implemented and verified on Linux; NOT pushed, NOT dispatched (owner hold).

## 1. The seam used
There was no resolver in code to reuse (MEASURED: no `MAVEN_HOME`/`M2_HOME` handling anywhere in `kriya/`, `scripts/`, `tests/`); the existing canonical mechanism was the CI step "Install pinned Maven" (a `curl | tar` of 3.9.9 with no checksum, onto `GITHUB_PATH`) plus whatever `mvn` the PATH resolved. Reused: the environment variable `MAVEN_HOME` (standard) and the controlled PATH; added the same kind of pinned-provisioning tool the repo already has for the frozen Maven repository and wheelhouse (`certification_maven_repo.py`, `certification_python_wheels.py`), not a resolver/factory:

| Piece | File |
|---|---|
| pin (version, banner, URL, size, SHA-512, archive root) | `tests/certification/maven_toolchain.json` |
| `provision` / `verify` (stdlib only) | `scripts/certification_maven_toolchain.py` |
| CI provisioning step -> `MAVEN_HOME` + `GITHUB_PATH` | `.github/workflows/ci.yml` (certification job) |
| certification stage `maven_toolchain` (mandatory; `pytest` is UNAVAILABLE without it; authority's bin goes first on PATH; recorded in `environment.json` as `maven_authority`) | `scripts/certify.sh`, `scripts/certification_summary.py` |
| demo-v2 helpers `resolve_maven_authority` / `require_maven_authority` / `app_path` / `authority_mvn` (absolute path) | `demo-v2/scripts/_common.sh` |
| call sites: `qualify_spring_ignite.sh` (all 5 `mvn`), `verify_demo.sh` (scenario 00 and 04 post, 3 sites), `greenfield_prereqs.sh`, `qualify_spring_ignite.py::_mvn_test` | `demo-v2/scripts/` |
| test gate: `_HAS_OFFLINE_STACK` keys on `MAVEN_HOME` (a set-but-invalid `MAVEN_HOME` is a FAIL, never a skip) | `tests/test_demo_v2_spring_ignite.py` |

`verify` refuses (exit 1, with the reason): no directory, no provisioning marker (this is what excludes the runner image's `/usr/bin/mvn` even though it reports 3.10.0), marker != pin, tree not byte-identical to the provisioned one (sha256 over sorted path/exec-bit/sha256), a symlink in the tree, a banner other than the pinned one, or a `Maven home:` that is not the directory itself (a wrapper around another installation). `provision` verifies size and the pinned SHA-512 BEFORE extraction, rejects `..`/absolute/foreign-root members and links, proves `mvn -v` reports the pinned banner, never overwrites.

Not touched (as ruled): `ui/standalone/src/main/child_env.ts` (product code). The scripts' PATH keeps the desktop app's fixed base for every other tool; only `mvn` is the authority's (its directory is put in front and it is called by absolute path).

## 2. Apache archive and SHA-512 (MEASURED 2026-11-04)
- URL: `https://archive.apache.org/dist/maven/maven-3/3.10.0/binaries/apache-maven-3.10.0-bin.tar.gz` (HTTP 200), 9,979,885 bytes.
- SHA-512 `908b1501bfb420bf7c8affb855534a9c407fd6099367bfb9f2f2dcb8e9799102bffb84518cde74c679bd76870247c6528683abdd620581bffa90f95d92d175aa` - equal to the Apache-published `.sha512` file AND to the sha512 computed over the downloaded archive at pin time. Provisioning verifies the PINNED value, not one fetched at run time from the same host as the archive.
- `mvn -v`: `Apache Maven 3.10.0 (c43a36b8d67be7e0805a411bc0898af1a51f5472)`.
- Provisioned FROM Apache (download path, no `--archive`) as a clean Linux user: PASS.

## 3. Maven consumer census (before any mirror change)
| Consumer | Maven it uses | Affected by the authority? |
|---|---|---|
| demo-v2 scenario 04 post-check (`verify_demo.sh`), qualification (`qualify_spring_ignite.sh/.py`), scenario 00 post-check, greenfield prereqs | host `mvn` from a fixed PATH | YES - routed through the authority |
| jvm-tier host-Maven tests (88 in the census, incl. `real_toolchain` 5) | host `mvn` from the test process PATH (certify.sh now puts the authority first) | MEASURED differential (section 10): no effect outside the demo-v2 pair |
| contained acquisition / mutant qualification / b2c etc. | the Maven INSIDE the pinned images | NO - separate identity (3.9.16, section 4) |
| frozen certification repository `maven_repository.manifest.json` (1773 files, digest `c557384d...`) | served as the registry mirror to CONTAINED Maven; also the source of the new ~/.m2 closure | content extended (section 6) |
| host `~/.m2` (offline) | was seeded ONLINE from Central by `seed_offline_maven.sh` with 3.9.9 (unattested) | replaced: seeded from the frozen repo |
| `REFERENCE_DIGEST` in `demo-v2/fixtures/spring-ignite-protocol/fixture.env` | digest of the reference project dir | changed with the POM (`b20bd773...` -> `626d5510...`); only consumers: `fixture.env`, `verify_demo.sh`/`qualify` (read it), `tests/test_demo_v2_spring_ignite.py` (reads it) |
| manifest digest `c557384d...` literal | no test or golden pins it; only evidence documents and certify.sh's printed stage line | historical evidence keeps the digest of the run it describes |

## 4. Container Maven identities (MEASURED, `docker run --network none --entrypoint mvn <image> -v`)
All three pinned images report `Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)`:
`maven:3.9-eclipse-temurin-8@sha256:d8b1b22e...`, `maven:3.9-eclipse-temurin-17@sha256:42a3ac39...`, `maven:3.9-eclipse-temurin-21@sha256:a972570b...`.
Not rebuilt or forced to 3.10.0. `certify.sh` now records them in `environment.json` as `container_maven` (separate supplied-toolchain identities). The product's own image contract is the 3.9 line (`tests/test_prd011_toolchain_identity.py` requires `maven 3.9` for those images - so that test, which parses an image's banner, was deliberately NOT changed to 3.10.0: changing it would break the image contract, not remove 3.9.9 noise).

## 5. Plugin versions (MEASURED under Maven 3.10.0; `clean test` + `compile exec:exec`, fresh repository)
Executed lifecycle plugins: clean **3.5.0**, resources **3.5.0** (resources and testResources), compiler 3.11.0 (already pinned), surefire 2.22.2 (already pinned), exec 3.1.0 (already pinned). `jar`/`install` are NOT part of those lifecycles and are not pinned. Pinned explicitly in `demo-v2/fixtures/spring-ignite-protocol/reference/pom.xml`: `maven-clean-plugin` 3.5.0, `maven-resources-plugin` 3.5.0 (the versions Maven 3.10.0 itself binds - chosen from the final 3.10.0 contract, not to preserve 3.9.9). Control `test_the_reference_pom_pins_every_lifecycle_plugin_the_certified_path_runs`.
Measured consequence: with the explicit pins the image Maven (3.9.16) adds ZERO files to a repository already populated by 3.10.0 (977 -> 977), and a jar+pom-only closure of 266 files builds, tests and runs offline under BOTH Mavens.

## 6. Frozen content - CHANGED
The existing frozen repository did NOT contain what the explicit POM needs under 3.10.0: of the 266 jar/pom files of the closure, 153 were already frozen (0 byte differences), **113 were missing** (clean/resources 3.5.0, surefire 2.22.2, their plugin parents and dependencies). Added through the existing legitimate process (`certification_maven_repo.py extend`: each file downloaded WITH Central's published SHA-1 and kept only if they agree), attested.
- OLD: 1773 files, content digest `c557384d52b7d3f196b3a79c3477a0441068717117dfd4560948d417bd25dec3`, attested.
- NEW: 1999 files (+113 artifacts/poms, +113 sidecars), content digest `8b12496df2f87eaf89b0f40375b3881dd3ccb7634829e699b0d5806d9874337d`, attested; 0 entries removed, 0 changed.
- A first extension (digest `c33af232...`) was DISCARDED and is HISTORICAL FAILED EVIDENCE ONLY - never an authority (it is not in the repository's manifest): the acceptance `fetch` on a clean machine refused it, because `extend` kept Central's raw `.sha1` text for `plexus-utils-1.0.4.pom` (`hash  /home/projects/maven/...`, outside the closed renderings). Tool defect (registry `CERT-MAVEN-REPO-EXTEND-RAW-SIDECAR-001`): `extend` now freezes the closed bare rendering; control test fails with the old behaviour. The manifest above was rebuilt with the fixed tool and a fresh `fetch` from Central PASSED.
- The host `~/.m2` is now seeded from the frozen repository (`scripts/seed_offline_maven.sh` + `tests/certification/demo_v2_m2_closure.txt`, 266 paths), byte-compared, then proven offline with the authority. Maven is never run online by the seed.

## 7. Acceptance on Linux (clean user `cert`, Ubuntu 24.04 aarch64, Java 17.0.20.1, exact-lock venv; ambient `/usr/local/bin/mvn` = 3.9.9 is the decoy and sits in the scripts' fixed base PATH)
| Check | Result |
|---|---|
| authority provisioned FROM Apache; `mvn -v` | exactly `Apache Maven 3.10.0 (c43a36b8...)`; `verify` rc 0 |
| frozen repository: fetch from the manifest, verify `--require-attested` | PASS, digest `8b12496d...` |
| `seed_offline_maven.sh`: 266 files copied, offline `clean test` + `compile exec:exec` | PASS (no network used) |
| the two previously failing demo-v2 post-check tests | **2 passed** (decoy 3.9.9 present) |
| whole `tests/test_demo_v2_spring_ignite.py` minus the docker mutants test | 34 passed |
| the same two tests with only loopback up, no other interface, DNS failing (`unshare -n`) | **2 passed** - no public-network fallback possible or used |
| `MAVEN_HOME` = the ambient 3.9.9 install | post-check FAILS: `... is not the authoritative Maven: REFUSED: ... no provisioning marker` |
| `MAVEN_HOME` = a copy of the SAME 3.10.0 bits without the marker (runner-image style) | post-check FAILS (refused) |
| required plugin removed from `~/.m2` (maven-clean-plugin 3.5.0) | FAILS CLOSED: `Cannot access central ... in offline mode and the artifact ...maven-clean-plugin:jar:3.5.0 has not been downloaded` |
| plugin restored | passes again |
| decoy `mvn` first on PATH and inside the fixed base | the resolved mvn is the authority's (`tests/test_prd034_maven_authority_001.py`, real bash) |
| toolchain fingerprint | `environment.json`: `maven` (authority banner), `maven_authority` (home, version, archive SHA-512, tree digest), `container_maven`; summary line "authoritative host Maven" |

Original symptom (pre-fix, MEASURED): `Maven 3.10.0 -o clean test` against a 3.9.9-seeded repository -> `maven-clean-plugin:3.5.0 ... has not been downloaded ... offline mode` (hosted message verbatim).

## 8. Controls (`tests/test_prd034_maven_authority_001.py`, 28 tests; real provisioning/verification code against fake Mavens, real bash)
wrong archive digest / wrong size / corrupted-but-valid tarball -> refused before extraction (nothing left behind); wrong version at provisioning and at verify (also when every byte of the tree is unchanged); missing Maven; ambient Maven reporting 3.10.0 (no marker); a marker copied onto an ambient Maven; altered tree; link to another installation; a Maven that runs another installation; hostile members (`..`, absolute, foreign root, links); decoy in PATH and in the fixed base; unset/invalid `MAVEN_HOME` in the shell helpers (never falls back to the decoy; `authority_mvn` returns 127); pom pins; workflow/certify.sh/summary wiring; no active surface claims 3.9.9.
Mutations (12) all killed: skip SHA-512 check; skip marker; skip tree digest; skip `Maven home` check; skip banner check (verify and provision); `authority_mvn` through PATH; authority not first in `app_path`; unset `MAVEN_HOME` not refused; unpinned clean plugin; hostile member accepted; links accepted.

## 9. Mac consequence (no fallback, stated)
On the owner's Mac the Maven-running demo-v2 checks now stop without a valid `MAVEN_HOME`; Homebrew's Maven is not used. One-time: provision the authority (`provision DIR`) and `export MAVEN_HOME=DIR`; seed `~/.m2` with `seed_offline_maven.sh` after fetching the frozen repository. Documented in `demo-v2/OWNER_RUNBOOK.md`.

## 10. Blast-radius differential for the 88 jvm-tier tests (MEASURED, Linux container, tree 680f3d9, each variant seeded and run from an empty ~/.m2)
Same 88 node ids (`tests/test_b2c_jvm_acceptance.py` x53, `test_d3_runtime_prerequisite.py` x8, demo-v2 x4, `test_direct_plan_real_maven.py` x3, ...):
| Variant | Host Maven first on PATH | Result |
|---|---|---|
| baseline | 3.9.9 | 85 passed, 1 failed, 2 skipped |
| candidate | **3.10.0** | 84 passed, 2 failed, 2 skipped |
The failures are only the demo-v2 post-check tests: the baseline tree has no repo-root `.venv` and no baseline tag in this container clone (environment artifacts of that scratch tree, the same class as the earlier lock-file group), and under 3.10.0 the second post-check test additionally fails exactly as hosted - the fixed PATH still resolved the container's ambient 3.9.9 against a 3.10.0-seeded repository (the hosted failure in reverse). All 84 other jvm-tier tests (including the 53 b2c, `real_toolchain` direct-plan tests) pass identically under 3.10.0: switching the host Maven to 3.10.0 does not affect any consumer outside demo-v2. The 2 skips are the registry-mirror tests (frozen repository not provisioned in that scratch run) in both variants.

## 11. Further Linux results
- Contained mutant qualification (`test_every_cache_get_mutant_of_the_reference_is_killed_and_the_control_passes`; host leg = authority 3.10.0, contained legs = pinned image Maven 3.9.16 on the closure-seeded `~/.m2`, network denied): **1 passed**.
- Git-maintenance fixtures and adjacent suites on Linux git 2.56.0 (`test_s3_execution_views`, `test_kup_workspace`, `test_fs1c*`, `test_oi142_gate_view`, `test_prd034_*`, `test_cert_private_fixture_001`, `test_python_support_contract_001`): 309 passed, 1 failed - `test_a_real_pip_refuses_...` because the container venv's bundled pip 24.0 does not evaluate Requires-Python for a local project under `--dry-run --python-version`; with pip 26.3 the file passes (7/7), and hosted run 41812207261's Test 3.12-3.14 jobs passed it. Environment characterization only (owner ruling 2026-11-04: not a dispatch blocker; the supported certification environments exercise the packaging contract and the control passes with a supported pip; if the final hosted run reproduces a packaging failure it is re-opened from measured hosted evidence; the Requires-Python controls are neither widened nor weakened).
- Python 3.12.13 and 3.13.13 exact-lock venvs (Mac): `test_prd034_maven_authority_001`, `test_prd034_maven_repo`, `test_prd034_certification`: 86 passed on each.
