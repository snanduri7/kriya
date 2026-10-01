# Kriya production certification: **CERTIFIED**

- every mandatory stage passed

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "27.5.1",  "docker_server": "27.5.1",  "git": "git version 2.54.0",  "git_revision": "580625a45188721b9c88d2acaae11b06a26308b0",  "gradle":  |
| static | PASS |  |
| pytest | PASS | 6857 passed, 72 deselected, 177 warnings in 1694.11s (0:28:14) |
| scanner | PASS | 27 passed, 6902 deselected, 2 warnings in 101.95s (0:01:41) |
| release | PASS |  |
| doctor | RECORDED | production doctor JSON archived |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 6857 | 6857 | 0 | 0 | 0 | 0 |
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
  "git_revision": "580625a45188721b9c88d2acaae11b06a26308b0",
  "gradle": null,
  "java": "openjdk version \"17.0.10\" 2024-01-16",
  "machine": "arm64",
  "maven": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
  "os": "macOS-26.7-arm64-arm-64bit-Mach-O",
  "pip_freeze_sha256": "e5bc1deaac0cfb1c0b0547557c8a2e9ff2d497a4554d2c39a44344f73b9c2a81",
  "python": "3.14.6",
  "python_executable": "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/python",
  "semgrep": "1.178.0",
  "uid_is_root": false
}
```
