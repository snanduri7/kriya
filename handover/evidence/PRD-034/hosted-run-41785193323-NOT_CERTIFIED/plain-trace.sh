#!/bin/sh
# builds the plain-job environment (declared ranges, unpinned) in the Linux container and runs the 7 plain-only failing tests
docker exec -u runner -w /home/runner kriya-linux-ctr sh -c '
set -e
rm -rf venv-plain; python3 -m venv venv-plain
cd tree && ../venv-plain/bin/pip install -q -e ".[dev]" 2>&1 | tail -2
../venv-plain/bin/pip freeze | grep -iE "^(click|pydantic|openai|pytest|mcp|starlette|numpy)==" 
../venv-plain/bin/python -m pytest -p no:cacheprovider -p no:xdist -o addopts= -q tests/contracts/test_config_resolution_contract.py::test_resolution_forms_are_byte_identical_to_the_pre_d3_golden tests/state_machine/test_sm_kernel_golden.py tests/test_cli_registration.py tests/test_outcome.py tests/test_reg_r1_pytest_regression_authority.py tests/test_s7_containment_census.py 2>&1 | grep -E "^(FAILED|[0-9]+ (passed|failed))|passed|failed" | cut -c1-300
'
