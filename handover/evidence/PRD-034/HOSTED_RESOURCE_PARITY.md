# PRD-034 hosted resource parity (owner ruling §5), built before editing CI

Method: every resource a mandatory certification test depends on was taken from the tests' own `skip`/`skipif` reasons (census: `tests/_certification_collection.py`), the image literals in `tests/` and `kriya/`, and `scripts/certify.sh`. Statuses: MEASURED (observed on this machine) / TRACED (read in code) / INFERRED (not provable without the hosted run). A cold hosted runner is never assumed to hold a warm developer cache; a mandatory test is never made optional or allowlisted because the runner starts cold.

| Tier / test family | Required resource | Local source | Cold-CI source | Skip / fail condition |
|---|---|---|---|---|
| all JVM tests (86 in the census) | JDK 17 | Temurin 17.0.10 (MEASURED) | `actions/setup-java` temurin 17 | `shutil.which("javac") is None` skip -> UNEXPECTED SKIP FAILS certification |
| real_toolchain (5), host Maven tests | Maven on PATH | 3.9.16 (MEASURED) | pinned 3.9.9 tarball (`ci.yml` step) | `which("mvn") is None` skip -> fails certification. Versions differ (local 3.9.16, hosted 3.9.9): evidence is NOT transferred between them |
| Docker tier (283 selected) | Docker daemon | 27.5.1, daemon up (MEASURED) | preinstalled on `ubuntu-latest` (INFERRED) | `docker daemon not reachable` skip -> fails certification; `certify.sh` marks pytest UNAVAILABLE first |
| Gradle containers (5) | `gradle:8-jdk8/17/21` | present (MEASURED) | `pinned_images.py --pull` by digest, tagged | image absent -> test error/skip -> fails. Host Gradle is NOT required: no `which("gradle")` branch in `kriya/` or `tests/` (MEASURED grep); `gradle` is MISSING on this machine and the Gradle tier is container-only |
| Maven containers (`REAL_OCI_MAVEN`, creator provenance, real_toolchain, acquisition) | `maven:3.9-eclipse-temurin-8/17/21` | present (MEASURED) | pinned by digest, tagged (NEW) | `image_present()` false -> skipif -> fails certification (was: only semgrep was pulled) |
| MCP containment (`REAL_OCI_MCP`), python venv scope | `python:3.10-slim`, `python:3.12-slim` | present (MEASURED) | pinned by digest, tagged (NEW) | as above |
| acquisition proxy / tools bases | `debian:bookworm-slim` | present (MEASURED) | pinned by digest, tagged (NEW) | as above |
| `kriya-acq-tools--*`, `kriya-acq-proxy-base:1` | built by the product on demand `FROM` the bases above (`kriya/tools/containment_oci.py`, `apt-get` over the network at build) | present (MEASURED) | built on first use by the existing containment-image owner (TRACED); needs runner network access (INFERRED available) | build failure -> test fails |
| live_static_analysis (11) | Semgrep **1.178.0** exactly, plus image `semgrep/semgrep@sha256:32e45996...` | `~/.local/bin/semgrep` 1.178.0 (MEASURED) | `pipx install semgrep==1.178.0`, image pulled by digest | `certify.sh` pytest stage UNAVAILABLE unless exactly 1.178.0 |
| Spring/Ignite post-check and mutants (4) | `~/.m2`: `ignite-core 2.18.0`, `junit-jupiter 5.8.2` + the lifecycle closure for host AND contained Maven | warm seed from April (MEASURED present; NOT used as the CI source) | NEW `scripts/seed_offline_maven.sh`: runs the reference pom's `test` online with host Maven then the pinned Maven image, from Maven Central by the pom's pinned coordinates; fails if the two artifacts are missing | `_HAS_OFFLINE_STACK` false -> skipif -> fails certification |
| release stage | `python -m build`, venv, network for `pip install -r requirements.txt` | working here (to be confirmed in preflight) | lock-file venv + network | stage FAIL |
| production doctor | packaged defaults, or an owner-approved config for an exact workspace | needs the owner's SEC-009 approval for the production profile | `packaged_defaults` mode (no approval; reports `production_ready` as it is) | SEC-009 denial -> stage FAIL with the exact owner command |

## What is NOT proven

- INFERRED: that the table is the complete set of cold-runner dependencies. It is derived statically; only the hosted run can prove it. The first hosted run may expose a further unexpected skip, which is then a PRD-034 defect to fix at its provisioning owner (never an allowlist entry).
- INFERRED: the seeded closure suffices for the contained Maven's plugin resolution offline (both Mavens are run once online, which is the deterministic way to seed it; the mutant test is the check).
- Digest pins resolve the multi-arch index digest recorded from this machine's local images; a hosted amd64 pull by that digest selects the amd64 manifest (standard registry behavior, not exercised here).
