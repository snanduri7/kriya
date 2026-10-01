# Kriya production certification: **CERTIFIED**

- every mandatory stage passed

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "27.5.1",  "docker_server": "27.5.1",  "git": "git version 2.54.0",  "git_revision": "7bc686f37383e057dda3143db5e1e6193262c01f",  "gradle":  |
| release_identity | RECORDED | 5a569dc8923640682fa248d0317d484cebe837d2f0bd03d006b076764ad7346f |
| static | PASS |  |
| pytest | PASS | 7219 passed, 72 deselected, 172 warnings in 1930.72s (0:32:10) |
| images | PASS | [pinned-images] 1 present, 0 missing |
| scanner | PASS | 27 passed, 7264 deselected, 2 warnings in 103.60s (0:01:43) |
| release | PASS |  |
| doctor | RECORDED | production doctor JSON archived |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 7219 | 7219 | 0 | 0 | 0 | 0 |
| scanner | 27 | 27 | 0 | 0 | 0 | 0 |

## Production doctor (recorded as reported)

```json
{
  "failed_required": [
    "context.recall_certification",
    "model.qualification"
  ],
  "production_ready": false,
  "statuses": {
    "FAIL": 2,
    "PASS": 24,
    "WARN": 5
  }
}
```

## Environment

```json
{
  "docker_client": "27.5.1",
  "docker_server": "27.5.1",
  "git": "git version 2.54.0",
  "git_revision": "7bc686f37383e057dda3143db5e1e6193262c01f",
  "gradle": null,
  "java": "openjdk version \"17.0.10\" 2024-01-16",
  "machine": "arm64",
  "maven": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
  "os": "macOS-26.7-arm64-arm-64bit-Mach-O",
  "pip_freeze_sha256": "64575fe49eb21f7fca3f3b62f91b1079f42c853ed8700aab0db06c9c79afaebd",
  "python": "3.14.6",
  "python_executable": "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/python",
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
  "digest": "5a569dc8923640682fa248d0317d484cebe837d2f0bd03d006b076764ad7346f",
  "environment": {
    "architecture": "arm64",
    "os": "darwin",
    "python": "3.14.6"
  },
  "kriya": {
    "dirty": false,
    "revision": "7bc686f37383e057dda3143db5e1e6193262c01f"
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
