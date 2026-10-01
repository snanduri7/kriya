# Kriya production certification: **CERTIFIED**

- every mandatory stage passed

| Stage | Status | Detail |
|---|---|---|
| environment | RECORDED | {  "docker_client": "27.5.1",  "docker_server": "27.5.1",  "git": "git version 2.54.0",  "git_revision": "3903deaba286caeb90e431559af65c3b19657fc4",  "gradle":  |
| static | PASS |  |
| pytest | PASS | 6816 passed, 61 deselected, 173 warnings in 1709.85s (0:28:29) |
| scanner | PASS | 27 passed, 6850 deselected, 2 warnings in 100.03s (0:01:40) |
| release | PASS |  |
| doctor | RECORDED | production doctor JSON archived |

| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |
|---|---|---|---|---|---|---|
| pytest | 6816 | 6816 | 0 | 0 | 0 | 0 |
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
  "git_revision": "3903deaba286caeb90e431559af65c3b19657fc4",
  "gradle": null,
  "java": "openjdk version \"17.0.10\" 2024-01-16",
  "machine": "arm64",
  "maven": "Apache Maven 3.9.16 (2bdd9fddda4b155ebf8000e807eb73fd829a51d5)",
  "os": "macOS-26.7-arm64-arm-64bit-Mach-O",
  "pip_freeze_sha256": "11326b8bd026626f8365cf130220ecb2fc091be04fa0a323ceeb2e1015b4790a",
  "python": "3.14.6",
  "python_executable": "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/python",
  "semgrep": "1.178.0",
  "uid_is_root": false
}
```
