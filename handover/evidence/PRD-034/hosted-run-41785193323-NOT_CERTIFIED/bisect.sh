#!/bin/sh
docker exec -u runner -w /home/runner/tree kriya-linux-ctr sh -c '
P=../venv-plain/bin
T="python -m pytest -p no:cacheprovider -p no:xdist -o addopts= -q"
run() { name="$1"; shift; echo "### $name"; $P/python -m pytest -p no:cacheprovider -p no:xdist -o addopts= -q "$@" 2>&1 | grep -E "passed|failed" | tail -1; }
GOLD="tests/contracts/test_config_resolution_contract.py::test_resolution_forms_are_byte_identical_to_the_pre_d3_golden tests/state_machine/test_sm_kernel_golden.py tests/test_outcome.py"
CLI="tests/test_cli_registration.py"
S7="tests/test_s7_containment_census.py::test_every_registry_exception_is_an_exactly_collected_node_id"
RR="tests/test_reg_r1_pytest_regression_authority.py::test_gate_result_retains_raw_stdout_stderr_and_junit_and_the_decision_record_its_artifacts"
echo BASELINE-PLAIN; run gold $GOLD; run cli $CLI; run s7 $S7; run regr1 $RR
echo "== pin pydantic 2.13.4 / core 2.46.4"; $P/pip install -q --no-deps pydantic==2.13.4 pydantic-core==2.46.4 2>&1 | tail -1; run gold-with-lock-pydantic $GOLD
$P/pip install -q --no-deps pydantic==2.14.2 pydantic-core==2.50.2 2>&1 | tail -1
echo "== pin click 8.4.2"; $P/pip install -q --no-deps click==8.4.2 2>&1 | tail -1; run cli-with-lock-click $CLI
$P/pip install -q --no-deps click==8.5.0 2>&1 | tail -1
echo "== pin pytest 9.1.1"; $P/pip install -q --no-deps pytest==9.1.1 2>&1 | tail -1; run s7-with-lock-pytest $S7; run regr1-with-lock-pytest $RR
$P/pip install -q --no-deps pytest==9.2.0 2>&1 | tail -1
echo "== pin pytest-asyncio 1.4.0"; $P/pip install -q --no-deps pytest-asyncio==1.4.0 2>&1 | tail -1; run regr1-with-lock-asyncio $RR; run s7-with-lock-asyncio $S7
$P/pip install -q --no-deps pytest-asyncio==1.4.1.post1 2>&1 | tail -1
'
