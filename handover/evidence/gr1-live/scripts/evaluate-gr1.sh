#!/bin/zsh
# Independent post-run evaluation - ONLY after freeze. Never part of Kriya's decision process.
# The external evaluator is run, never read: only its printed result is used.
set -u
D=$HOME/kriya-m1-live; T=gr1-graphify; W=$D/ws/$T; OUT=$D/evidence/$T; F=$OUT/frozen; E=$OUT/external
V=$HOME/kriya-live-validation/val001-g1-graphify-c3406
[ -f $F/FROZEN ] || { echo "ABORT: freeze first"; exit 1; }
[ -e $E ] && { echo "ABORT: already evaluated"; exit 1; }
mkdir -p $E; export PYTHONDONTWRITEBYTECODE=1
"$V/g1_venv/bin/python3" "$V/g1_evidence/acceptance/check_acceptance.py" --repo "$W" > $E/evaluator_output.txt 2>&1; echo "evaluator_exit=$?" >> $E/evaluator_output.txt
( cd $W && "$V/g1_venv/bin/python3" -m pytest -p no:cacheprovider tests/test_csharp_type_resolution.py tests/test_csharp_member_calls.py -q ) > $E/regression_output.txt 2>&1; echo "regression_exit=$?" >> $E/regression_output.txt
git -C $W status --porcelain --ignored > $E/git_status_after_evaluation.txt
tail -12 $E/evaluator_output.txt; tail -2 $E/regression_output.txt
