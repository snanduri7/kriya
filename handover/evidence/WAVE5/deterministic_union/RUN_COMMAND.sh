#!/bin/zsh
# Wave-5 current-revision PRD-020..029 deterministic union, run ONCE. cwd must be Kriya-rebuild-v2.
cd /Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2 || exit 99
H=/Users/sriramnanduri/kriya-gate-e-2026-11-01/evidence/wave5_deterministic
ulimit -n 256
.venv/bin/pytest -q -n 8 --dist loadgroup -p no:cacheprovider -rA --junitxml=$H/junit.xml $(cat $H/union_targets.txt | tr '\n' ' ') > $H/pytest_output.txt 2>&1
echo "exit=$?" > $H/exit_code.txt
