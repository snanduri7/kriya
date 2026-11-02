# PRD-034 deterministic Maven provisioning - status after implementation (2026-11-02)

Owner rulings: S1 (default-off operator-owned mirror) with the content-authority constraint; 10-hour autonomous window. Companion documents: `MAVEN_DETERMINISM_TRACE.md`
(why no existing seam), `MAVEN_CENTRAL_429_EVIDENCE.txt`, `EXTERNAL_DEPENDENCY_SWEEP.md`, `maven-closure/` (raw discovery evidence).

## 1. What exists (IMPLEMENTED, tested; product change)

| Item | Where | Evidence |
|---|---|---|
| Config seam `autonomy.acquisition_registry_mirror` (default `None`, closed URL grammar, host must be in `acquisition_registry_hosts`) | `kriya/config/config.py` | `tests/test_prd034_registry_mirror.py` (28 tests) |
| SEC-009 classification (SECURITY_AUTHORITY); resume fingerprint (containment dimension); doctor evidence | `authority.py`, `resume_fingerprints.py`, `production_doctor.py` | exact-approval / changed-value / repo-cannot-introduce tests |
| Trusted setup renders one standard Maven `<mirror>` (`id` and `mirrorOf` = `central`); `--add-host host.docker.internal:host-gateway` on the proxy only for that host | `kriya/tools/containment_oci.py`, `containment.py::ContainmentProfile.registry_mirror`, `validate.py`, `dependency_execution.py` | MEASURED: unset script sha256 `29168f2a...` byte-identical to the commit before the seam; authority id `c03193c18d29` unchanged |
| Frozen repository tool (`build`/`verify`/`attest`/`extend`/`fetch`/`serve`/`record`) | `scripts/certification_maven_repo.py` | `tests/test_prd034_maven_repo.py` (13 tests) |
| certify.sh stages `maven_repository` (attested + byte-exact, served for the run, pytest UNAVAILABLE otherwise) and `maven_served` (what was fetched; 0 bytes-differ refusals); manifest digest bound into the summary | `scripts/certify.sh`, `certification_summary.py` | structural tests in `tests/test_cert_tmp_growth_001.py` |
| Harness hook: under certification every contained **Maven resolution** goes through the mirror and Central is removed from the proxy ACL (direct `CONNECT` proxy probes are untouched) | `tests/conftest.py::_certification_maven_mirror` (env `KRIYA_CERT_MAVEN_MIRROR`, a test-harness input exported by certify.sh) | discovery run |
| Hosted provisioning of the same manifest with the same tool | `.github/workflows/ci.yml` step "Provision the frozen certification Maven repository" | structural test |

Defects found and fixed on the way (mine): the mirror `id` must be `central` (MEASURED: another id makes every acquired artifact unusable offline, `MAVEN_ACQUISITION_INCOMPLETE`);
the renderer raised `IndexError` on a value without a scheme (now a typed refusal; a test case found it); the first harness hook was too broad and 4 proxy-mechanism tests that
`CONNECT` to the real registry host failed with 403 (HARNESS defect; narrowed to Maven resolutions).

## 2. Required proofs (owner ruling section 8)

| | Proof | Status |
|---|---|---|
| A | unset = byte-equivalent settings/script, authority id, default | **MEASURED PASS** (digest pinned) |
| B | mirror host not in `acquisition_registry_hosts` -> refused (config + OCI backend); changed value without a new SEC-009 approval -> refused; exact approval -> accepted | **PASS** (9 targeted mutants, all killed) |
| C | empty ephemeral cache, contained Maven, standard resolver, mirror, offline execution succeeds | **PASS** (`tests/test_prd034_registry_mirror_real.py` against the frozen **candidate**) |
| D | Central unreachable (not even in the ACL) + complete mirror -> passes; proxy denied-host record names no Central request | **PASS** (same) |
| E | one required artifact removed, empty cache -> deterministic acquisition failure, resolver told 404, nothing reaches Central | **PASS** (same; the control drops the artifact the project actually fetched) |
| F | same coordinates, altered bytes -> refused at server start and at every served request; recomputed sidecar does not help | **PASS** (`test_prd034_maven_repo.py`) |

## 3. The artifact closure (MEASURED, 136 tests, Central denied, recorder over the pool)

* Population: every `jvm`/`real_toolchain` census test plus the docker tests of the files that name the registry (`maven-closure/discovery-population-node-ids.json`; the 5 Gradle-container tests excluded - not Maven). 131 passed, 5 failed (classified below).
* **1,275 distinct files requested and served**: 189 jars, 447 poms, 636 checksum sidecars, 3 metadata files = **63 MB** (candidate manifest `tests/certification/maven_repository.manifest.json`, content digest `de49aab5e613...`). Each carries coordinates, path, SHA-256, provenance and a family purpose; per-test attribution comes from the serve log's `node` field in the canonical run.
* The pool is DISCOVERY material only: the candidate's bytes are `UNVERIFIED` (636/636 files match their own stored sha1 sidecar - internal consistency, not authority). **`attested: false`**, so `certify.sh` refuses to run on it (`verify --require-attested`).
* **30 artifacts were requested and are absent from the pool** (18 jars, 12 poms; `tests/certification/maven_repository.missing.txt`; mostly Doxia/site-plugin modules that `dependency:go-offline` resolves). Maven tolerated the 404s in every test except one (below).

## 4. The five discovery-run failures (classified before any rerun)

| Test | Class | Disposition |
|---|---|---|
| `test_containment_oci::test_registry_scoped_allows_authorized_https_connect`, `::..._concurrent_authorities_do_not_cross_grant`, `test_prd012_egress_docker::test_production_package_manager_stays_registry_scoped`, `test_sec005_...::test_real_maven_command_through_shelltool_reaches_authorized_registry` | HARNESS (my hook removed Central from the ACL of tests whose purpose is to prove that exact ACL against the real host) | hook narrowed to Maven resolutions; the four pass (re-run once after the correction) |
| `test_dependency_execution::test_maven_two_phase_acquire_then_offline_execute` | MISSING ARTIFACTS (`dependency:go-offline` needs the 30 absent files) | OPEN: needs `extend` from Central |

`MAVEN-B2C-THROWS-ACQUISITION-FAILURE-001`: the exact `[throws]` reproducer (and its `wrong` sibling and the 001b greenfield test) **passed from an empty cache through the frozen mirror served by the tool**
(3 passed, 1,264 requests all 200, zero 404, zero 409), and it passed in the discovery run. The original failure was an acquisition step that "exited 1" against live Central;
it has not reproduced under a deterministic source. Classification: ENVIRONMENT (live-Central acquisition), supported by measurement, not proven in the strong sense (the original
log carried no network detail and the failure was seen once).

## 5. What is BLOCKED and the exact next actions

1. **BLOCKED_ON_CENTRAL_BOOTSTRAP** (not owner approval, not a code gap): Central still answers HTTP 429 (checked 14:27Z), so `attest` and `extend` cannot run. Exact sequence once it answers (`curl -s -o /dev/null -w '%{http_code}' https://repo.maven.apache.org/maven2/junit/junit/4.13.2/junit-4.13.2.pom` = 200):
   ```
   cd ~/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2 && source .venv/bin/activate
   python scripts/certification_maven_repo.py extend tests/certification/maven_repository.manifest.json ~/kriya-cert-maven/repo-candidate tests/certification/maven_repository.missing.txt
   python scripts/certification_maven_repo.py attest ~/kriya-cert-maven/repo-candidate tests/certification/maven_repository.manifest.json
   python scripts/certification_maven_repo.py verify ~/kriya-cert-maven/repo-candidate tests/certification/maven_repository.manifest.json --require-attested
   ```
   `attest` compares every artifact/pom with the SHA-1 Central publishes and sets `attested`; a mismatch refuses and names the file. Then commit the manifest (a docs/evidence-class change; the manifest digest is the new repository identity).
2. SEC-009: see the owner handoff (the final config digest is not frozen until the repository is attested and the executable is final).
3. The sweep rows 3-6 (PyPI, release stage, Gradle, apt) remain explicit PRD-034 blockers (`EXTERNAL_DEPENDENCY_SWEEP.md`).

## 6. FINAL state (2026-11-03) - supersedes section 5 (the Central bootstrap block is lifted)

* Repository: `tests/certification/maven_repository.manifest.json`, **1,773 files, `attested: true`, content digest `c557384d52b7d3f196b3a79c3477a0441068717117dfd4560948d417bd25dec3`**. It was extended in iterative rounds from Central only (each added jar/pom compared with Central's published SHA-1; `extend` refuses a mismatch) because a failing test stops at the first missing artifact. The earlier 1,653/1,275-file sets are NOT final.
* Final validation (one complete run over the union of the 136 Maven, 89 pip/Gradle and registry-mirror-real populations, empty caches, Central and PyPI denied): **227 passed in 1h31m**, 36,668 mirror requests (36,605 x 200; 63 x 404 = 60 checksum sidecars + the three artifacts that tests deliberately request and expect to be absent), 0 x 409, PyPI index 322 requests all 200.
* Broad non-docker regression on the same tree: **19,454 passed, 1 skipped** (19,455 ids; 36m57s); ruff 0, pylint 0; performance probe PASS in a fresh temp root (engine slice 77 s, listing 4.6 s; the same slice took 296 s with 201 s of listing in a polluted ambient temp root - the first probe invocation, kept as evidence in `/private/tmp/claude-501/perf/probe-ambient-polluted`, was run outside certify.sh's fresh root and correctly FAILED).
* `MAVEN-B2C-THROWS-ACQUISITION-FAILURE-001` final wording: **ENVIRONMENTAL ACQUISITION FAILURE - the deterministic mirror reproducer now passes; the exact historical HTTP-429 causality is not proven.**
