#!/bin/zsh
# One A/B run: arm, task, label (index + generate under the arm's own venv, state, logs, index and authority).
# usage: run_ab.sh <A|B> <task> <label>; tasks run in A, B, B, A order (KRIYA_CAGC §13.1).
set -u
N=$1; T=$2; L=$3; AB=$HOME/kriya-cagc-ab; source $AB/$N/env.sh
GOAL=$(awk -F'\t' -v t=$T '$1==t {print $2}' $HOME/kriya-bench-live/matrix/tasks.tsv)
W=$AB/$N/ws/$T; CFG=$AB/$N/config/$T.yaml; LOG=$AB/$N/logs/$T.$L; cd $W || exit 2
# Every replicate starts from the task base: tracked files reset, untracked non-ignored files removed; ignored
# Kriya state (.kriya/, the project venv, build output) is kept - the same treatment in both arms.
git checkout -q --detach $(cat $AB/$N/$T.base) && git reset -q --hard && git clean -qfd || exit 3
echo "== $N $T [$L] start $(date +%T) base $(git rev-parse --short HEAD) kriya $(kriya version --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["commit"][:9])')"
S=$(date +%s); kriya --config $CFG analyze . > $LOG.analyze.log 2>&1; echo "analyze exit=$? seconds=$(( $(date +%s) - S ))"
S=$(date +%s); kriya --config $CFG generate "$GOAL" -y > $LOG.generate.log 2>&1; GEN=$?
echo "generate exit=$GEN wall_seconds=$(( $(date +%s) - S ))"
git status --short | grep -v '^??' | head; git log --oneline -1
echo "== $N $T [$L] end $(date +%T)"
