# Kriya production certification: **NOT_CERTIFIED**

- pytest: FAIL
- release: FAIL

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "27.5.1",  "docker_server": "27.5.1",  "git": "git version 2.54.0",  "git_revision": "016cabac87d1c18294c45b53b55cde11c87e6410",  "gradle":  |
| static | PASS |  |
| pytest | FAIL | 1 failed, 6815 passed, 61 deselected, 173 warnings in 2041.76s (0:34:01) |
| scanner | PASS | 27 passed, 6850 deselected, 2 warnings in 99.92s (0:01:39) |
| release | FAIL | see release.txt |
| doctor | RECORDED | production doctor JSON archived |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 6816 | 6815 | 1 | 0 | 0 | 0 |
| scanner | 27 | 27 | 0 | 0 | 0 | 0 |

## Production doctor (recorded as reported)

```json
{
  "failed_required": [
    "config.load"
  ],
  "production_ready": false,
  "statuses": {
    "FAIL": 1
  }
}
```

## Environment

```json
{
  "docker_client": "27.5.1",
  "docker_server": "27.5.1",
  "git": "git version 2.54.0",
  "git_revision": "016cabac87d1c18294c45b53b55cde11c87e6410",
  "gradle": null,
  "java": "openjdk version \"17.0.10\" 2024-01-16",
  "machine": "arm64",
  "maven": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
  "os": "macOS-26.7-arm64-arm-64bit-Mach-O",
  "pip_freeze_sha256": "963a451ee65b38f253e5326feaa22645533368153b7bf04e9cf2f323beebe860",
  "python": "3.14.6",
  "python_executable": "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/python",
  "semgrep": "1.178.0",
  "uid_is_root": false
}
```
