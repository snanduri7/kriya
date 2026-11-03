# Kriya production certification: **NOT_CERTIFIED**

- pytest: FAIL
- tier_execution: FAIL
- REQUIRED LINUX-HOST EVIDENCE - NOT EXERCISED LOCALLY (OPEN until a linux certification executes it and it passes): `tests/test_d2_m2_fd_inheritance.py::test_linux_a_child_cannot_reopen_the_waiters_socket_through_proc`

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "27.5.1",  "docker_server": "27.5.1",  "git": "git version 2.54.0",  "git_revision": "ad1a795ece1d74d5abfe045b35c1488f7a347cd0",  "gradle":  |
| release_identity | RECORDED | f10a1902f4c6262c59ce65789a2d77df317067684402faafec270a04180be9db |
| static | PASS |  |
| maven_repository | PASS | content digest c557384d52b7d3f196b3a79c3477a0441068717117dfd4560948d417bd25dec3 served on 127.0.0.1:61158 |
| python_wheelhouse | PASS | {"hashed_requirements_sha256": "b4583335692ccfa8aa19e9387f8c8beb186f83745e1057654195e13eb710fd58", "wheelhouse_files": "70", "wheelhouse_sha256": "749cb00672adb |
| product_images | PASS | 11 lines; ids recorded (provision: scripts/provision_containment_images.py build) |
| collection | PASS | [collection-audit] PASS: 19855 selected / 45 deselected |
| perf_probe | PASS | 3 classes; see perf-probe/perf-probe.json |
| pytest | FAIL | [shards] FAIL: 2 failed, 19852 passed, 1 skipped, 0 errors across 4 shards (merge audit OK) |
| maven_served | PASS | 36971 requests, 1744 distinct files, 0 bytes-differ refusals; statuses {'200': 36907, '404': 64} |
| python_served | PASS | 312 requests, 7 distinct files, 0 bytes-differ refusals; statuses {'200': 312} |
| images_unchanged | PASS | no product image was built or rebuilt during the run |
| tier_execution | FAIL | population identical: yes; [tier-execution] FAIL: {"docker": {"failed": 1, "missing": 0, "passed": 284, "skipped": 0}, "gradle_container": {"failed": 0, "missin |
| scanner | PASS | semgrep 1.178.0; live_static_analysis tier passed in the canonical run |
| images | PASS | [pinned-images] 10 present, 0 missing |
| release | PASS |  |
| thresholds | RECORDED | [thresholds] RECORDED 0955d5b610a10c4f |
| doctor | RECORDED | production doctor JSON archived (mode: operator_config) |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 19855 | 19852 | 2 | 0 | 1 | 0 |

## Population

- canonical selection `not live_model`: 19855 selected, 45 deselected (of 19900); tiers selected: {"docker": 285, "gradle_container": 5, "jvm": 88, "live_static_analysis": 11, "real_toolchain": 5}
- tier execution: {"docker": {"failed": 1, "missing": 0, "passed": 284, "skipped": 0}, "gradle_container": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}, "jvm": {"failed": 0, "missing": 0, "passed": 88, "skipped": 0}, "live_static_analysis": {"failed": 0, "missing": 0, "passed": 11, "skipped": 0}, "real_toolchain": {"failed": 0, "missing": 0, "passed": 5, "skipped": 0}}
- doctor mode: {"config": "/Users/sriramnanduri/.kriya/operator/graphify-qwen3coder-native-production.yaml", "mode": "operator_config", "trust_file": "/Users/sriramnanduri/kriya-gate-e-2026-11-01/authority/5fff250e61e7fe3fe41942b5047c39653f561ce14bfd249bc67a1527c3da94e6.json", "workspace": "/Users/sriramnanduri/.kriya/operator/cert-doctor-workspace"}
- deterministic Maven repository: manifest content digest `c557384d52b7d3f196b3a79c3477a0441068717117dfd4560948d417bd25dec3` (1773 files, attested: True); the run fetched 1744 distinct files in 36971 requests {"200": 36907, "404": 64}; requests outside the frozen set: 10; bytes-differ refusals: 0
- frozen Python wheelhouse: hashed lock sha256 `b4583335692ccfa8aa19e9387f8c8beb186f83745e1057654195e13eb710fd58`, 70 files, wheelhouse digest `749cb00672adb9eabe100b9439a651b74443b2c3c9895269074d5d45bcdc5aa4` (release stage: --no-index --require-hashes, fresh venv)
- thresholds: /Users/sriramnanduri/.kriya/operator/release-thresholds-wave7.yaml sha256 0955d5b610a10c4f3aeed230de57b85c0bc671cacfa80836173e5a1a2adb08ac loader digest 075d5dce37a8c383a49e88fd314b023e0b6df922e557a79b2da9c99fe8449d24; effective [{"comparator": "max", "metric": "adjudication.false_success_rate", "min_samples": 1, "value": 0.0}, {"comparator": "max", "metric": "adjudication.regression_escape_rate", "min_samples": 1, "value": 0.0}]

## Production doctor (recorded as reported)

```json
{
  "failed_required": [
    "context.recall_certification"
  ],
  "production_ready": false,
  "statuses": {
    "FAIL": 1,
    "PASS": 30,
    "WARN": 3
  }
}
```

## Environment

```json
{
  "docker_client": "27.5.1",
  "docker_server": "27.5.1",
  "git": "git version 2.54.0",
  "git_revision": "ad1a795ece1d74d5abfe045b35c1488f7a347cd0",
  "gradle": null,
  "java": "openjdk version \"17.0.10\" 2024-01-16",
  "machine": "arm64",
  "maven": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
  "os": "macOS-27.0.1-arm64-arm-64bit-Mach-O",
  "pip_freeze_sha256": "555c8d2955c8118b8a5ff9b36d8f28a6759ca323366ef32a071a4df1b4bb5dcb",
  "python": "3.14.6",
  "python_executable": "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2/.venv/bin/python",
  "semgrep": "1.178.0",
  "uid_is_root": false
}
```

## Release identity (a material change makes this certification stale)

```json
{
  "containment": {
    "backend": "oci",
    "runtime_version": "27.5.1"
  },
  "digest": "f10a1902f4c6262c59ce65789a2d77df317067684402faafec270a04180be9db",
  "environment": {
    "architecture": "arm64",
    "os": "darwin",
    "python": "3.14.6"
  },
  "kriya": {
    "dirty": false,
    "revision": "ad1a795ece1d74d5abfe045b35c1488f7a347cd0"
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
        "advisory",
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
