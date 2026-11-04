# PRD-034 external-dependency sweep (read-only; owner ruling 2026-11-02 section 10)

Purpose: record, per mandatory certification tier, which live external service is needed, whether its content is pinned, and whether a transient outage or rate limit
could invalidate a multi-hour canonical run - so the next equivalent of the Maven Central 429 is not discovered five hours into C3 run 2.
Method: static trace of `scripts/certify.sh`, `.github/workflows/ci.yml`, `scripts/*.sh`, `kriya/tools/containment_oci.py`, `kriya/static_analysis/adapters/semgrep.py` and the
network-naming tests; nothing was run against the services (Central is already throttling this machine). Status labels: TRACED (read in code), MEASURED (observed), INFERRED.
Nothing here was implemented except Maven.

| # | Tier / stage | External host(s) during EXECUTION | Content pinned? | Needed by the canonical run? | Cold-host behaviour | Deterministic source |
|---|---|---|---|---|---|---|
| 1 | **Contained Maven acquisition** (REAL_OCI_MAVEN, d3/d4/d5/d6, b2c/b3, greenfield e2e, direct-plan, creator provenance; census jvm/real_toolchain + docker) | `repo.maven.apache.org` (MEASURED 429) | **Now: yes** - frozen repository + manifest (this work) | Yes, every test starts from an empty ephemeral cache | needs the frozen repo served; reconstructable from the manifest by `fetch` | `scripts/certification_maven_repo.py` + `autonomy.acquisition_registry_mirror` (IMPLEMENTED; closure discovery in progress) |
| 2 | Host Maven tests (`_HAS_OFFLINE_STACK`: Ignite 2.18.0 / JUnit 5.8.2 + lifecycle closure in `~/.m2`) | none at execution (offline); Central at PROVISIONING | coordinates only (`seed_offline_maven.sh` pins versions, no digests) | No (offline) - but provisioning is the same live Central | hosted: `seed_offline_maven.sh` hits Central before the run | **GAP (provisioning)**: seed from the frozen repository (manifest-verified bytes) instead of Central |
| 3 | **Python/PyPI - contained pip acquisition** (`test_dependency_execution` six==1.16.0, `test_sec005_*`, `test_runtime_verification`, `test_backend_readiness_004_venv_reuse`, ver004 ... - 8 test files name pip) | `pypi.org`, `files.pythonhosted.org` (TRACED: tests pass these as `registry_hosts`) | versions named in test fixtures; **bytes not pinned** | **Yes** (docker-tier tests acquire live) | cold or warm, always live | **UNRESOLVED** - same class as the Maven finding; no PyPI mirror seam exists (the new mirror is Maven-only by design) |
| 4 | **Release stage** (`scripts/verify_release.sh`: `pip install -r requirements.txt`) | `pypi.org` | versions pinned by pip-compile; **no `--hash`** (0 hashes in `requirements.txt`) | **Yes** (mandatory stage, runs after pytest) | live every time | **UNRESOLVED** - a PyPI outage fails `release` after hours of testing |
| 5 | **Gradle container tier** (`test_gradle_wrapper_containment_001::test_15_...`: wrapper distribution + registry dependency, acquisition phase) | `services.gradle.org`, `plugins.gradle.org`, `plugins-artifacts.gradle.org`, Central | none | **Yes** for test_15 (4 other tier tests measured/inspected as not acquiring: INFERRED) | live | **UNRESOLVED** |
| 6 | **Product-built containment images** (`kriya-acq-proxy-base:1`, `kriya-acq-tools--*:1`: `apt-get install squid ... iptables ...` on first use) | Debian mirrors (`deb.debian.org`) | base image digest-pinned; **apt package versions float** | Local: no (images cached, MEASURED present). **Hosted cold runner: yes, inside the pytest stage** | first acquisition builds them | **UNRESOLVED for hosted** - pre-build them in provisioning (they are built by the product's own `_ensure_image`) so the run does not depend on apt |
| 7 | live_static_analysis (Semgrep 1.178.0) | none at execution: `--metrics=off --disable-version-check`, rule packs are local digest-bound files (TRACED `semgrep.py:102,610`) | image digest pinned; pipx `semgrep==1.178.0` (version) | No | provisioning: PyPI + Docker Hub | OK at execution |
| 8 | Docker images (maven/gradle/python/debian + semgrep) | Docker Hub at PROVISIONING only (`pinned_images.py --pull`); `images` stage verifies, never pulls | digest-pinned (`tests/certification/pinned_images.txt`) | No (provisioning) | anonymous pull rate limit on shared runner IPs (INFERRED) | pinned; provisioning risk only |
| 9 | Host Maven distribution (CI step: `curl archive.apache.org ... maven-3.9.9` - HISTORICAL; now `scripts/certification_maven_toolchain.py provision`, pinned SHA-512 verified before extraction, Maven 3.10.0) | `archive.apache.org` | URL pinned; checksum not verified in the step (TRACED, `ci.yml` ~184) | Provisioning | live | minor |
| 10 | `kriya doctor --production --json` | none required (reports Ollama/embedding reachability as it finds it) | n/a | recorded, never a gate | n/a | OK |

## Conclusion (honest)

* **Maven** (row 1): resolved by this work once the frozen repository is built and attested.
* **Rows 3, 4, 5, 6 are the same class as the Maven finding**: a mandatory canonical stage that depends on an unpinned live service (PyPI, Gradle, Debian mirrors), where a transient
  outage or rate limit can invalidate certification. Owner ruling section 13 makes this a STOP: **another mandatory ecosystem has equivalent unresolved external nondeterminism.**
  The ruling does not authorise mirrors for them ("do not automatically implement mirrors for all ecosystems"); an owner decision is needed per row (see the report).
* Row 6 matters for the hosted run only; row 4 is the cheapest to remove (`pip install --require-hashes` from a hashed lock, or a pinned wheelhouse).
* Ordering risk found: `release` (row 4) runs at the END of C3, so a PyPI failure would surface only after the multi-hour pytest stage.
