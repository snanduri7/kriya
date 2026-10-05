#!/bin/zsh
# One bounded live validation run: analyze, then generate under the timing shim; collect the evidence.
# usage: run.sh <workspace name> "<goal>"
set -u
T=$1; GOAL=$2; D=$HOME/kriya-m1-live; source $D/env.sh
W=$D/ws/$T; CFG=$D/config/$T.yaml; LOG=$D/logs/$T; OUT=$D/evidence/$T; mkdir -p $OUT; cd $W || exit 2
git checkout -q --detach $(cat $D/ws/$T.base) && git reset -q --hard && git clean -qfd || exit 3
print -r -- "$GOAL" > $OUT/goal.txt
kriya --config $CFG evidence show --json > $OUT/stores.before.json
echo "== $T start $(date +%T) base $(git rev-parse --short HEAD) kriya $(kriya version --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["commit"][:9])')"
S=$(date +%s); kriya --config $CFG analyze . > $LOG.analyze.log 2>&1; echo "analyze exit=$? seconds=$(( $(date +%s) - S ))"
S=$(python3 -c 'import time;print(time.time())')
KRIYA_RECORDER_TIMING_OUT=$OUT/timing.json PYTHONPATH=$D/shim kriya --config $CFG generate "$GOAL" -y > $LOG.generate.log 2>&1; GEN=$?
E=$(python3 -c 'import time;print(time.time())')
python3 -c "import json;json.dump({'generate_exit':$GEN,'wall_seconds':$E-$S},open('$OUT/wall.json','w'))"
echo "generate exit=$GEN wall_seconds=$(python3 -c "print(round($E-$S,1))")"
kriya --config $CFG evidence show --json > $OUT/stores.after.json
RUN=$(python3 -c "import json;b=set(json.load(open('$OUT/stores.before.json'))['runs']);a=json.load(open('$OUT/stores.after.json'))['runs'];n=[r for r in a if r not in b];print(n[0] if len(n)==1 else '')")
echo "run_id=${RUN:-NONE}"; print -r -- "${RUN:-}" > $OUT/run_id.txt
if [ -n "$RUN" ]; then
  kriya --config $CFG evidence verify $RUN --json > $OUT/verify.json; echo "verify exit=$?"
  kriya --config $CFG evidence show $RUN --json > $OUT/show.json
  kriya --config $CFG evidence explain $RUN --json > $OUT/explain.json; echo "explain exit=$?"
  du -sk $D/state/attempt-evidence/$RUN > $OUT/store_du.txt; cat $OUT/store_du.txt
fi
git status --short | grep -v '^??' | head > $OUT/git_status.txt; git log --oneline -3 > $OUT/git_log.txt
echo "== $T end $(date +%T)"
