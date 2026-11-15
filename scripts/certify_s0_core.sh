#!/usr/bin/env bash
# S0 certification core (architecture closure, owner ruling 2026-11-15): the tests that prove the product and certification
# machinery slice S0 changed - environment-vs-candidate classification (R-13), the finite and managed launch stage, the
# zero-charge rule of the retry machine, the gates that carry them. NOT "everything that ever failed": a test joins only when
# it protects a product or certification invariant.
#
#   scripts/certify_s0_core.sh <evidence-dir>
#
# Run from the root of a CLEAN worktree pinned to the commit under certification, with its own .venv
# (python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"). Runs every phase even after a failure, writes one log per
# phase and <evidence-dir>/summary.txt, and exits non-zero when any phase failed. Nothing is retried.
set -u
EVIDENCE="${1:?usage: scripts/certify_s0_core.sh <evidence-dir>}"
mkdir -p "$EVIDENCE"
SUMMARY="$EVIDENCE/summary.txt"
FAILED=0

if [ -n "$(git status --porcelain)" ]; then
    echo "refused: the tracked tree is not clean" >&2
    exit 2
fi
{ echo "commit: $(git rev-parse HEAD)"; echo "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; } > "$SUMMARY"

phase() {  # phase <name> <command...>
    local name="$1"; shift
    "$@" > "$EVIDENCE/$name.log" 2>&1
    local rc=$?
    [ "$rc" -eq 0 ] || FAILED=1
    { echo "== $name: exit=$rc :: $*"; grep -E "^(FAILED|ERROR) " "$EVIDENCE/$name.log"; tail -n 1 "$EVIDENCE/$name.log"; } >> "$SUMMARY"
}

ulimit -n 256
PYTEST=(.venv/bin/pytest -q -p no:cacheprovider)
PARALLEL=(-n 6 --dist loadgroup)

# Real-daemon files: their own serial pass, never under the parallel load of the other phases (they skip without a daemon).
REAL_DOCKER=(tests/test_finite_launch_target_start.py tests/test_s4_1_managed_service_view.py tests/test_service_runtime_oci.py)

phase ruff .venv/bin/ruff check .
phase pylint .venv/bin/pylint kriya plugins/core_tools tests
# retry / recovery / gates / runtime verification / lifecycle: the whole tier (includes test_runtime_verification.py, test_gate_orchestrator.py)
phase state_machine "${PYTEST[@]}" "${PARALLEL[@]}" -m state_machine
phase workflow "${PYTEST[@]}" "${PARALLEL[@]}" tests/test_workflow.py
phase s0_invariants "${PYTEST[@]}" "${PARALLEL[@]}" \
    tests/test_r13_environment_verdict.py tests/test_non_candidate_failure_scoped_reset_001.py \
    tests/test_bind_source_diagnostic_001.py tests/test_bind_source_relaunch_001.py \
    tests/test_service_runtime.py tests/test_managed_service_hitl_fail_closed.py tests/test_oi55_managed_service_continues.py \
    tests/test_oi142_gate_view.py tests/test_finite_command_artifact_preparation.py tests/test_repair_003_f1_runtime_environment_stop.py \
    tests/test_process_controller.py tests/test_backlog_registry.py tests/test_strict_doubles.py
phase architecture "${PYTEST[@]}" "${PARALLEL[@]}" tests/architecture
phase real_docker_serial "${PYTEST[@]}" -p no:xdist "${REAL_DOCKER[@]}"

{ echo "finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo "result: $([ "$FAILED" -eq 0 ] && echo PASS || echo FAIL)"; } >> "$SUMMARY"
cat "$SUMMARY"
exit "$FAILED"
