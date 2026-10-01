# Kriya production certification: **CERTIFIED**

- every mandatory stage passed

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "27.5.1",  "docker_server": "27.5.1",  "git": "git version 2.54.0",  "git_revision": "5727f9993c9e6f9cad9055674988ea9180895c3e",  "gradle":  |
| release_identity | RECORDED | 337e1eec5a5b530b7e5c681e066bdf8db332c24763c1c6ac992e07eea07d4e04 |
| static | PASS |  |
| pytest | PASS | 7215 passed, 72 deselected, 177 warnings in 1896.02s (0:31:36) |
| images | PASS | [pinned-images] 1 present, 0 missing |
| scanner | PASS | 27 passed, 7260 deselected, 2 warnings in 102.88s (0:01:42) |
| release | PASS |  |
| doctor | RECORDED | production doctor JSON archived |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 7215 | 7215 | 0 | 0 | 0 | 0 |
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
  "git_revision": "5727f9993c9e6f9cad9055674988ea9180895c3e",
  "gradle": null,
  "java": "openjdk version \"17.0.10\" 2024-01-16",
  "machine": "arm64",
  "maven": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
  "os": "macOS-26.7-arm64-arm-64bit-Mach-O",
  "pip_freeze_sha256": "38fcbab5212d9f1001c314541fbdbb5fca3e5761fe22dc668393df3984b8af7c",
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
  "digest": "337e1eec5a5b530b7e5c681e066bdf8db332c24763c1c6ac992e07eea07d4e04",
  "environment": {
    "architecture": "arm64",
    "os": "darwin",
    "python": "3.14.6"
  },
  "kriya": {
    "dirty": false,
    "revision": "5727f9993c9e6f9cad9055674988ea9180895c3e"
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
