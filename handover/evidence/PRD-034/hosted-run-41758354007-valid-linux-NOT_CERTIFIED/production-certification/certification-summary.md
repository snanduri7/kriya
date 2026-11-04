# Kriya production certification: **NOT_CERTIFIED**

- static: FAIL
- pytest: FAIL
- tier_execution: FAIL
- pytest: 5 unexpected skip(s)

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "28.0.4",  "docker_server": "28.0.4",  "git": "git version 2.56.0",  "git_revision": "9f7940963b5039ae05ce67c7be41fa998800566b",  "gradle":  |
| release_identity | RECORDED | c2e0d9e5d5605af9fea85e3d84ad85100d3ec8eca4838cc751e47a79024bf301 |
| static | FAIL | see ruff.txt / pylint.txt |
| maven_repository | PASS | content digest c557384d52b7d3f196b3a79c3477a0441068717117dfd4560948d417bd25dec3 served on 0.0.0.0:52757 |
| python_wheelhouse | PASS | {"hashed_requirements_sha256": "b4583335692ccfa8aa19e9387f8c8beb186f83745e1057654195e13eb710fd58", "wheelhouse_files": "70", "wheelhouse_sha256": "d998578dcaeeb |
| product_images | PASS | 11 lines; ids recorded (provision: scripts/provision_containment_images.py build) |
| collection | PASS | [collection-audit] PASS: 19894 selected / 45 deselected |
| perf_probe | PASS | 3 classes; see perf-probe/perf-probe.json |
| pytest | FAIL | [shards] FAIL: 35 failed, 19791 passed, 0 skipped, 68 errors across 4 shards (merge audit OK) |
| maven_served | PASS | 36971 requests, 1744 distinct files, 0 bytes-differ refusals; statuses {'200': 36907, '404': 64} |
| python_served | PASS | 322 requests, 7 distinct files, 0 bytes-differ refusals; statuses {'200': 322} |
| images_unchanged | PASS | no product image was built or rebuilt during the run |
| tier_execution | FAIL | population identical: yes; [tier-execution] FAIL: {"docker": {"failed": 4, "missing": 0, "passed": 281, "skipped": 0}, "gradle_container": {"failed": 0, "missin |
| scanner | PASS | semgrep 1.178.0; live_static_analysis tier passed in the canonical run |
| images | PASS | [pinned-images] 10 present, 0 missing |
| release | PASS |  |
| doctor | RECORDED | production doctor JSON archived (mode: packaged_defaults) |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 19894 | 19791 | 35 | 68 | 0 | 5 |

## Population

- canonical selection `not live_model`: 19894 selected, 45 deselected (of 19939); tiers selected: {"docker": 285, "gradle_container": 5, "jvm": 88, "live_static_analysis": 11, "real_toolchain": 5}
- tier execution: {"docker": {"failed": 4, "missing": 0, "passed": 281, "skipped": 0}, "gradle_container": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}, "jvm": {"failed": 5, "missing": 0, "passed": 83, "skipped": 0}, "live_static_analysis": {"failed": 0, "missing": 0, "passed": 11, "skipped": 0}, "real_toolchain": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}}
- doctor mode: {"config": "", "mode": "packaged_defaults", "trust_file": "", "workspace": ""}
- deterministic Maven repository: manifest content digest `c557384d52b7d3f196b3a79c3477a0441068717117dfd4560948d417bd25dec3` (1773 files, attested: True); the run fetched 1744 distinct files in 36971 requests {"200": 36907, "404": 64}; requests outside the frozen set: 10; bytes-differ refusals: 0
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
  "docker_client": "28.0.4",
  "docker_server": "28.0.4",
  "git": "git version 2.56.0",
  "git_revision": "9f7940963b5039ae05ce67c7be41fa998800566b",
  "gradle": "Welcome to Gradle 9.8.2!",
  "java": "openjdk version \"17.0.21\" 2026-10-20",
  "machine": "x86_64",
  "maven": "Apache Maven 3.9.9 (8e8579a9e76f7d015ee5ec7bfcdc97d260186937)",
  "os": "Linux-7.0.0-1017-azure-x86_64-with-glibc2.39",
  "pip_freeze_sha256": "c8a6cca56c02c20fcd4ac68f39407c448777807c8b9122d6a0d3b9bdd324aa29",
  "python": "3.12.16",
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
  "digest": "c2e0d9e5d5605af9fea85e3d84ad85100d3ec8eca4838cc751e47a79024bf301",
  "environment": {
    "architecture": "x86_64",
    "os": "linux",
    "python": "3.12.16"
  },
  "kriya": {
    "dirty": false,
    "revision": "9f7940963b5039ae05ce67c7be41fa998800566b"
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
