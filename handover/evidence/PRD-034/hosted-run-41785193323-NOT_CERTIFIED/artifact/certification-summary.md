# Kriya production certification: **NOT_CERTIFIED**

- pytest: FAIL
- tier_execution: FAIL
- pytest: 1 unexpected skip(s)

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "28.0.4",  "docker_server": "28.0.4",  "git": "git version 2.56.0",  "git_revision": "04ac1fdc63d6cd3691686ac9365ce3d8d49500d4",  "gradle":  |
| release_identity | RECORDED | 7dd7e273f3dd7118f86af718250d38d369b0095b3ade9de4c0e72db0c1fdf106 |
| static | PASS |  |
| maven_repository | PASS | content digest c557384d52b7d3f196b3a79c3477a0441068717117dfd4560948d417bd25dec3 served on 0.0.0.0:36707 |
| python_wheelhouse | PASS | {"hashed_requirements_sha256": "b4583335692ccfa8aa19e9387f8c8beb186f83745e1057654195e13eb710fd58", "wheelhouse_files": "70", "wheelhouse_sha256": "d998578dcaeeb |
| product_images | PASS | 11 lines; ids recorded (provision: scripts/provision_containment_images.py build) |
| collection | PASS | [collection-audit] PASS: 19914 selected / 45 deselected |
| perf_probe | PASS | 3 classes; see perf-probe/perf-probe.json |
| pytest | FAIL | [shards] FAIL: 4 failed, 19907 passed, 2 skipped, 1 errors across 4 shards (merge audit OK) |
| maven_served | PASS | 36971 requests, 1744 distinct files, 0 bytes-differ refusals; statuses {'200': 36907, '404': 64} |
| python_served | PASS | 322 requests, 7 distinct files, 0 bytes-differ refusals; statuses {'200': 322} |
| images_unchanged | PASS | no product image was built or rebuilt during the run |
| tier_execution | FAIL | population identical: yes; [tier-execution] FAIL: {"docker": {"failed": 1, "missing": 0, "passed": 285, "skipped": 0}, "gradle_container": {"failed": 0, "missin |
| scanner | PASS | semgrep 1.178.0; live_static_analysis tier passed in the canonical run |
| images | PASS | [pinned-images] 10 present, 0 missing |
| release | PASS |  |
| doctor | RECORDED | production doctor JSON archived (mode: packaged_defaults) |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 19914 | 19907 | 4 | 1 | 2 | 1 |

## Population

- canonical selection `not live_model`: 19914 selected, 45 deselected (of 19959); tiers selected: {"docker": 286, "gradle_container": 5, "jvm": 88, "live_static_analysis": 11, "real_toolchain": 5}
- tier execution: {"docker": {"failed": 1, "missing": 0, "passed": 285, "skipped": 0}, "gradle_container": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}, "jvm": {"failed": 2, "missing": 0, "passed": 86, "skipped": 0}, "live_static_analysis": {"failed": 0, "missing": 0, "passed": 11, "skipped": 0}, "real_toolchain": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}}
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
  "git_revision": "04ac1fdc63d6cd3691686ac9365ce3d8d49500d4",
  "gradle": "Welcome to Gradle 9.8.2!",
  "java": "openjdk version \"17.0.21\" 2026-10-20",
  "machine": "x86_64",
  "maven": "Apache Maven 3.9.9 (8e8579a9e76f7d015ee5ec7bfcdc97d260186937)",
  "os": "Linux-7.0.0-1017-azure-x86_64-with-glibc2.39",
  "pip_freeze_sha256": "c0e9ea40e326bf6eacd78656f23be12e66a8fdc36eae6e198b940b811fe9166d",
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
  "digest": "7dd7e273f3dd7118f86af718250d38d369b0095b3ade9de4c0e72db0c1fdf106",
  "environment": {
    "architecture": "x86_64",
    "os": "linux",
    "python": "3.12.15"
  },
  "kriya": {
    "dirty": false,
    "revision": "04ac1fdc63d6cd3691686ac9365ce3d8d49500d4"
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
