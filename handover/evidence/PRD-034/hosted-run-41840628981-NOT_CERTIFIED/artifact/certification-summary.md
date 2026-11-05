# Kriya production certification: **NOT_CERTIFIED**

- pytest: FAIL
- EXTERNAL_PRIVATE_FIXTURE_REQUIRED (PRD034-F9B-SCENARIO01) - NOT A PASS: `tests/test_demo_v2_owner_state.py::test_reset_all_is_idempotent_and_leaves_no_service` needs a fixture that may not be hosted; PRD-034 is CERTIFIED only when `scripts/certification_private_gate.py verify` binds a private-gate evidence record to this exact Kriya revision/config.

| Stage | Status | Detail |
|---|---|---|
| maven_toolchain | PASS | {"archive_sha512": "908b1501bfb420bf7c8affb855534a9c407fd6099367bfb9f2f2dcb8e9799102bffb84518cde74c679bd76870247c6528683abdd620581bffa90f95d92d175aa", "archive_ |
| environment | RECORDED | {  "container_maven": {    "maven:3.9-eclipse-temurin-17": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",    "maven:3.9-eclipse-temurin-21":  |
| release_identity | RECORDED | c4bc3720c14ede73e5ae6d6004abd7beda17bf50f9ceb3cc38224aab2df1edc8 |
| static | PASS |  |
| maven_repository | PASS | content digest 8b12496df2f87eaf89b0f40375b3881dd3ccb7634829e699b0d5806d9874337d served on 0.0.0.0:46385 |
| python_wheelhouse | PASS | {"hashed_requirements_sha256": "b4583335692ccfa8aa19e9387f8c8beb186f83745e1057654195e13eb710fd58", "wheelhouse_files": "70", "wheelhouse_sha256": "d998578dcaeeb |
| product_images | PASS | 11 lines; ids recorded (provision: scripts/provision_containment_images.py build) |
| collection | PASS | [collection-audit] PASS: 19990 selected / 45 deselected |
| perf_probe | PASS | 3 classes; see perf-probe/perf-probe.json |
| pytest | FAIL | [shards] FAIL: 2 failed, 19985 passed, 3 skipped, 0 errors across 4 shards (merge audit OK) |
| maven_served | PASS | 36971 requests, 1744 distinct files, 0 bytes-differ refusals; statuses {'200': 36907, '404': 64} |
| python_served | PASS | 322 requests, 7 distinct files, 0 bytes-differ refusals; statuses {'200': 322} |
| images_unchanged | PASS | no product image was built or rebuilt during the run |
| tier_execution | PASS | [tier-execution] PASS: {"docker": {"failed": 0, "missing": 0, "passed": 286, "skipped": 0}, "gradle_container": {"failed": 0, "missing": 0, "passed": 5, "skippe |
| scanner | PASS | semgrep 1.178.0; live_static_analysis tier passed in the canonical run |
| images | PASS | [pinned-images] 10 present, 0 missing |
| release | PASS |  |
| doctor | RECORDED | production doctor JSON archived (mode: packaged_defaults) |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 19990 | 19985 | 2 | 0 | 3 | 0 |

## Population

- canonical selection `not live_model`: 19990 selected, 45 deselected (of 20035); tiers selected: {"docker": 286, "gradle_container": 5, "jvm": 88, "live_static_analysis": 11, "real_toolchain": 5}
- tier execution: {"docker": {"failed": 0, "missing": 0, "passed": 286, "skipped": 0}, "gradle_container": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}, "jvm": {"failed": 0, "missing": 0, "passed": 88, "skipped": 0}, "live_static_analysis": {"failed": 0, "missing": 0, "passed": 11, "skipped": 0}, "real_toolchain": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}}
- doctor mode: {"config": "", "mode": "packaged_defaults", "trust_file": "", "workspace": ""}
- authoritative host Maven: Apache Maven 3.10.0 (c43a36b8d67be7e0805a411bc0898af1a51f5472) at `/home/runner/kriya-maven-authority` (pinned archive SHA-512 `908b1501bfb420bf7c8affb855534a9c...`, tree digest `45d9be390ef2d8f4993661e5b172a7a852bf7e2da6cac451da9041d222e0596a`); container Maven versions are separate supplied-toolchain identities (see environment.json)
- deterministic Maven repository: manifest content digest `8b12496df2f87eaf89b0f40375b3881dd3ccb7634829e699b0d5806d9874337d` (1999 files, attested: True); the run fetched 1744 distinct files in 36971 requests {"200": 36907, "404": 64}; requests outside the frozen set: 10; bytes-differ refusals: 0
- frozen Python wheelhouse: hashed lock sha256 `b4583335692ccfa8aa19e9387f8c8beb186f83745e1057654195e13eb710fd58`, 70 files, wheelhouse digest `d998578dcaeeb88bc4b36df38cd470ad6ecad75c2867d4d4894cfc283f4b493e` (release stage: --no-index --require-hashes, fresh venv)

## Production doctor (recorded as reported)

```json
{
  "failed_required": [
    "profile.production"
  ],
  "production_ready": false,
  "statuses": {
    "FAIL": 1,
    "PASS": 24,
    "UNAVAILABLE": 5,
    "WARN": 4
  }
}
```

## Environment

```json
{
  "container_maven": {
    "maven:3.9-eclipse-temurin-17": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
    "maven:3.9-eclipse-temurin-21": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
    "maven:3.9-eclipse-temurin-8": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)"
  },
  "docker_client": "28.0.4",
  "docker_server": "28.0.4",
  "git": "git version 2.56.0",
  "git_revision": "1de8732b319a7f495ded50d33b0ea838c03f14a3",
  "gradle": "Welcome to Gradle 9.8.2!",
  "java": "openjdk version \"17.0.21\" 2026-10-20",
  "machine": "x86_64",
  "maven": "Apache Maven 3.10.0 (c43a36b8d67be7e0805a411bc0898af1a51f5472)",
  "maven_authority": {
    "archive_sha512": "908b1501bfb420bf7c8affb855534a9c407fd6099367bfb9f2f2dcb8e9799102bffb84518cde74c679bd76870247c6528683abdd620581bffa90f95d92d175aa",
    "archive_url": "https://archive.apache.org/dist/maven/maven-3/3.10.0/binaries/apache-maven-3.10.0-bin.tar.gz",
    "maven_home": "/home/runner/kriya-maven-authority",
    "tree_sha256": "45d9be390ef2d8f4993661e5b172a7a852bf7e2da6cac451da9041d222e0596a",
    "version": "3.10.0",
    "version_banner": "Apache Maven 3.10.0 (c43a36b8d67be7e0805a411bc0898af1a51f5472)"
  },
  "os": "Linux-7.0.0-1017-azure-x86_64-with-glibc2.39",
  "pip_freeze_sha256": "840357fa4f8f9d9fd7c78e62bcf67c301fede82913d4b6cfa94e7d88b780695e",
  "python": "3.12.15",
  "python_executable": "/home/runner/work/kriya/kriya/.venv/bin/python",
  "semgrep": "1.178.0",
  "uid_is_root": false
}
```

## Release identity (a material change makes this certification stale)

```json
{
  "containment": {
    "backend": "oci",
    "runtime_version": "28.0.4"
  },
  "digest": "c4bc3720c14ede73e5ae6d6004abd7beda17bf50f9ceb3cc38224aab2df1edc8",
  "environment": {
    "architecture": "x86_64",
    "os": "linux",
    "python": "3.12.15"
  },
  "kriya": {
    "dirty": false,
    "revision": "1de8732b319a7f495ded50d33b0ea838c03f14a3"
  },
  "platform": {
    "capabilities": [
      [
        "file_lock_crash_safe",
        "enforced",
        "posix-flock"
      ],
      [
        "posix_rlimit_as",
        "enforced",
        "posix-setrlimit"
      ],
      [
        "posix_rlimit_cpu",
        "enforced",
        "posix-setrlimit"
      ],
      [
        "process_tree_termination",
        "enforced",
        "posix-process-group"
      ],
      [
        "uid_gid_identity",
        "enforced",
        "posix-uid-gid"
      ]
    ],
    "family": "posix",
    "providers": {
      "host_identity": "posix-uid-gid",
      "process_control": "posix-process-group",
      "resource_limits": "posix-setrlimit",
      "workspace_lock": "posix-flock"
    }
  },
  "version": 1
}
```
