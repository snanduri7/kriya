# Wave 0 Remaining Verification

PRD-003 is verified. Before beginning PRD-004, close the Wave 0 gate:

1. Execute the 13 real-CLI security tests now that they resolve the active
   environment's `kriya` console script.
2. Execute PRD-002's source/sdist/wheel clean-install verification.

Commands from the target checkout:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_sec003_mcp_env_isolation.py \
  tests/test_sec004_mcp_lifecycle.py \
  tests/test_sec009_config_authority.py \
  tests/test_sec009_p2_authority_approval.py \
  tests/test_tool002_p2_real_cli.py

KRIYA_PYTHON="$PWD/.newvenv/bin/python" bash scripts/verify_release.sh
```

The first command may run more than 13 tests; all must execute without the
`.venv/bin/kriya` skip. Preserve the release script's printed evidence folder.
