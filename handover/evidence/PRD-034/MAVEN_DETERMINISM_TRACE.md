# PRD-034 - deterministic Maven provisioning: the trace the owner asked for BEFORE implementing (2026-11-02)

Owner ruling: option B (certification-owned deterministic Maven provisioning), real acquisition path preserved, **no product acquisition-semantics change merely
for certification**, prefer an existing Maven repository/mirror seam, no custom protocol, no bypass by seeding the test's `.m2`. This document is the requested
trace of the existing seams. **Result: no existing seam reaches the contained acquisition path, so the routing needs an owner choice (section 3). Nothing
is implemented yet.** Central is still HTTP 429 (`MAVEN_CENTRAL_429_EVIDENCE.txt`, 14:27Z).

## 1. How contained Maven gets its repositories today (TRACED)

| Question | Answer | Where |
|---|---|---|
| Where do artifacts come from? | The run's EMPTY ephemeral dependency directory is mounted at `MAVEN_CACHE_MOUNT` and used as `-Dmaven.repo.local`; an online ACQUISITION phase (`mvn dependency:go-offline`, or `mvn <goals>` online) fills it from Maven Central, then execution runs `mvn -o`. Nothing is seeded. | `kriya/tools/dependency_cache.py create_ephemeral_cache`, `dependency_execution.py:822,846`, `validate.py:1714-1899` |
| Is there a mirror / settings / repository seam? | **No.** The trusted container setup script writes the image's GLOBAL `settings.xml` with a `<proxies>` block ONLY, and says why: "Deliberately NO `<mirrors>`/`<repositories>` override ... a hostile pom.xml's own `<repository>` declaration still resolves to whatever real host it names, which the firewall+proxy ACL then allows or denies" (SEC-006). | `kriya/tools/containment_oci.py:906-930` |
| How does a request leave the container? | HTTPS `CONNECT` through a per-run Squid (`kriya-acq-proxy-base:1`), default-deny, exact hostnames from `autonomy.acquisition_registry_hosts` (default: `repo.maven.apache.org`, `pypi.org`, `files.pythonhosted.org`, Gradle hosts). Squid tunnels TLS; it cannot serve a different origin. | `containment_oci.py:781-1100,1630-1690`, `config.py:492-520` |
| Can Maven 3.9 re-point `central` by a property/env? | No (`maven.repo.central` exists only in Maven 4). A project can override a repository whose id is `central` in its OWN pom (`<repositories>` and `<pluginRepositories>`), or use a `-s` settings file. | Maven behaviour (INFERRED from documentation; not exercised) |
| How are checksums validated? | By Maven itself against Central's published `.sha1` sidecars (the warm host `~/.m2` has 6,309 of them). Kriya adds no artifact digest check. | MEASURED on `~/.m2` |
| Existing local-registry test fixture? | **None.** Every acquisition test names the real `repo.maven.apache.org`. | grep of `tests/` |
| Host mvn path | Host Maven tests use `~/.m2` (April seed + `scripts/seed_offline_maven.sh`); that is a different surface from the contained one. | `scripts/seed_offline_maven.sh` |

Scale (MEASURED): 13 test files / 26 tests carry `REAL_OCI_MAVEN`; 42 test files contain a literal pom; the slow real-Maven families (d3/d4/d5/d6, b2c/b3, greenfield
end-to-end, 001b, direct_plan) add more containers that acquire online. The warm host `~/.m2` holds 2,352 jars / 3,963 poms / 1.7 GB accumulated since February: a
candidate POOL with Central checksum sidecars, not a minimal or trusted closure.

## 2. Why the obvious seams do not work without choosing something

* **Pom-level `<repository id="central">` override in fixtures**: needs the mirror host in the Squid ACL, i.e. in `acquisition_registry_hosts`. A test that builds
  `PolymorphicValidator(path)` with the default config gets the packaged default list, so every such test would also need a config override: tens of tests plus
  42 pom-bearing files. And the host must be reachable from the proxy's Docker network (`host.docker.internal` on Docker Desktop; `--add-host` on hosted Linux).
* **DNS/hosts override of `repo.maven.apache.org` to a local server**: Squid tunnels TLS, so the JVM would need a certificate it trusts for that name; the
  image trust store is image-owned (digest pinned). Rejected.
* **Wrapping `docker` on PATH to inject env/mounts**: bypasses the product's launch path and its leak checks. Rejected.
* **Seeding the test's `.m2`/ephemeral cache**: explicitly forbidden by the ruling.

## 3. Options (owner choice of the routing seam)

**S1 (recommended): a default-OFF, operator-owned registry mirror setting in the product.** `autonomy.acquisition_registry_mirror: Optional[str]` (default `None`
= byte-identical behaviour today). When set, the SAME trusted setup script renders a `<mirror>` into the global `settings.xml` (it is operator configuration,
not repository content, so the SEC-006 invariant "repository content cannot enlarge authority" is untouched); its host must also be in `acquisition_registry_hosts`
(the existing rule, so Squid admits it); the field is classified in `kriya/config/authority.py` (SEC-009: it changes where code is fetched from, so it needs the owner's approval like
any authority field). The certification repository is a standard file-based Maven repository served by `python -m http.server` (host process, no long-lived
service) and provisioned identically on hosted Linux. The certification conftest injects the mirror for default-config tests (a test-harness input read only by conftest,
never a product trust path). Cost: a product change (config.py, containment_oci.py, authority.py, docs) -> new product revision, new tests + mutants, and everything
after PRD-033's `4a9273e` identity is re-baselined. It is also the feature the config comment already anticipates ("a private/internal registry needs an explicit
additional entry").

**S2: no product change, fixture-level.** Feasible only for tests that build their own config; the default-config tests cannot name the mirror host without a default change.
Large and incomplete; not recommended.

**S3: record/replay inside the test process.** Violates "preserve the real acquisition path". Rejected.

## 4. What is NOT yet known / not started (honest)

* The exact artifact closure: it must be derived by running the mandatory real-Maven tests against a RECORDING mirror, which needs Central (429 now) or the
  warm pool as the source; bootstrap is not certification.
* PyPI (pip acquisition tests), the Gradle distribution/plugin hosts and the Semgrep image pull are the same class of external dependence (INFERRED; not measured
  and not in this ruling's scope).
* `MAVEN-B2C-THROWS-ACQUISITION-FAILURE-001` stays OPEN, not attributed to the 429.

## 5. Plan once the seam is chosen (no work started)

1. Closure discovery: serve the pool through a recording mirror, run the mandatory real-Maven tests once, record every GAV (type/classifier).
2. `scripts/certification_maven_repo.py`: build a standard repository from the closure, verify each file against Central's published checksum (bootstrap, once, when Central answers),
   write `tests/certification/maven_repository.manifest.json` (GAV, sha256, source URL, dependency purpose, repository content digest, provisioning and serving commands).
3. Hosted CI reconstructs the same set from the manifest (coordinates + digests), then serves it with the same command.
4. Negative controls (permanent tests): Central made unreachable -> a representative real-Maven test still passes; one required artifact removed -> the intended acquisition failure.
5. Re-run the exact `[throws]` reproducer from an empty cache and classify.
