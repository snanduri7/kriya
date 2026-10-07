#!/bin/zsh
# qwen3.8 PRIMARY CODING MODEL PROFILE COMPARISON: the ONE authorized Graphify run (Kriya a049c02, matched config 7e595207,
# same goal/contract/acceptance/B3 as the canonical run). Refuses to run twice; aborts before any model call unless every gate matches.
set -u
D=$HOME/kriya-m1-live; source $D/env-gr1q.sh; T=gr1q-graphify
W=$D/ws-qwen38/gr1-graphify; CFG=$D/config/qwen38-matched-developer.yaml; LOG=$D/logs/$T; OUT=$D/evidence/$T
GOAL_FILE=$HOME/kriya-live-validation/graphify-canonical-853aa43/goal.txt
P=$HOME/kriya-wt/p3d/handover/evidence/gr0/graphify_prospective
[ -e $OUT ] && { echo "ABORT: $OUT exists - the comparison run already happened (no second run is authorized)"; exit 1; }
mkdir -p $OUT; cd $W || exit 2
python $D/scripts/gr1q_prerun_check.py $OUT/prerun_check.json > $OUT/prerun_check.txt 2>&1; PRE=$?; cat $OUT/prerun_check.txt
[ $PRE -eq 0 ] || { echo "ABORT: pre-run check failed - STOP BEFORE MODEL CALL"; exit 1; }
cp $GOAL_FILE $OUT/goal.txt
kriya --config $CFG model status > $OUT/model_status.txt 2>&1 || { tail -20 $OUT/model_status.txt; echo "ABORT: a configured model is not QUALIFIED - STOP BEFORE MODEL CALL"; exit 1; }
echo "model status: all roles QUALIFIED"
ARGS=(--requirements $P/graphify_requirements_PROPOSED_unapproved.json --acceptance $P/kriya_acceptance_graphify.py
      --acceptance-approval $P/graphify_approval_APPROVED_owner.json)
echo "invocation: kriya --config $CFG generate -f $GOAL_FILE -y ${ARGS[*]}" | tee $OUT/invocation.txt
{ docker ps -a --format '{{.Names}} {{.Status}}'; docker network ls --format '{{.Name}}'; } > $OUT/resources_before.txt 2>&1
kriya --config $CFG evidence show --json > $OUT/stores.before.json
echo "== $T start $(date +%T) base $(git rev-parse --short HEAD) kriya $(kriya version --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["commit"][:9])')"
S=$(date +%s); kriya --config $CFG analyze . > $LOG.analyze.log 2>&1; echo "analyze exit=$? seconds=$(( $(date +%s) - S ))"
git status --porcelain --ignored > $OUT/git_status_after_analyze.txt
S=$(python3 -c 'import time;print(time.time())')
KRIYA_RECORDER_TIMING_OUT=$OUT/timing.json PYTHONPATH=$D/shim kriya --config $CFG generate -f "$GOAL_FILE" -y "${ARGS[@]}" > $LOG.generate.log 2>&1; GEN=$?
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
fi
{ docker ps -a --format '{{.Names}} {{.Status}}'; docker network ls --format '{{.Name}}'; } > $OUT/resources_after.txt 2>&1
echo "== $T end $(date +%T). Next: freeze-gr1q.sh (never evaluate before freezing)"
