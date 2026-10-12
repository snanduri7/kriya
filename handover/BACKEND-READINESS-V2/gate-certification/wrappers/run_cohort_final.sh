#!/bin/zsh
# BACKEND-READINESS-V2 FINAL frozen 12-case cohort (owner authorization): every case once, strictly serial, label -v6f, executable =
# branch backend/readiness-v2 (the run scripts refuse a dirty tree or another branch). Same run scripts (p5_run_v2.sh / c2_run_v2.sh) as
# every earlier frozen run; the oracle is judged through evaluate_v3.sh, which classifies each exit (PASS / REJECT /
# ENVIRONMENT_UNAVAILABLE / INDETERMINATE) first. NO case is rerun and the driver never stops early: a Kriya success the oracle REJECTS
# is recorded as FALSE_SUCCESS (readiness FAIL) and the remaining cases are still measured. Refuses to start if any -v6f output exists.
set -u
V2=$HOME/kriya-m1-live/backend-readiness-v2; R=$HOME/kriya-m1-live/backend-readiness-001; R4=$HOME/kriya-m1-live/backend-readiness-004/cohort-002
K=$HOME/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo; LABEL=-v6f; TAG=ws-v6f; COOL=${COOL:-120}; W=$(cd "$(dirname "$0")" && pwd)
CASES=(T1 T2 T3 T4 T5 T6 S1_A S2_A S3_A S4_A S4_B S6_A)
[ -f $V2/cohort-final/FREEZE.json ] || { echo "ABORT: no freeze record"; exit 1; }
[ "$(git -C $K rev-parse HEAD)" = "$(python3 -c "import json;print(json.load(open('$V2/cohort-final/FREEZE.json'))['head'])")" ] || { echo "ABORT: HEAD differs from the freeze record"; exit 1; }
for T in $CASES; do case $T in T*) O=$V2/primary/P5-$T$LABEL;; *) O=$V2/cohort-002/C2-$T$LABEL;; esac; [ -e $O ] && { echo "ABORT: $O exists"; exit 1; }; done
echo "######## FINAL COHORT start $(date +%F' '%T) kriya $(git -C $K branch --show-current) $(git -C $K rev-parse HEAD)"
INVALID=0; FALSE_SUCCESS=0
for T in $CASES; do
  echo "######## $T$LABEL $(date +%F' '%T)"
  case $T in
    T*) OUT=$V2/primary/P5-$T$LABEL; $V2/p5_run_v2.sh $T $LABEL; echo "run exit=$?"; $W/evaluate_v3.sh primary $T $R/tasks/$T/ws $TAG $LABEL; EV=$?;;
    *)  OUT=$V2/cohort-002/C2-$T$LABEL; $V2/c2_run_v2.sh $T $LABEL; echo "run exit=$?"; $W/evaluate_v3.sh c2 $T $R4/tasks/$T/ws $TAG $LABEL; EV=$?;;
  esac
  echo "evaluate exit=$EV"; [ $EV -ne 0 ] && INVALID=$((INVALID+1))
  STATUS=$(python3 -c "import json;r=json.load(open('$OUT/result.json'));print(r.get('status'), r.get('quality_gates_passed'))" 2>/dev/null)
  ACC=$(python3 -c "import json;print(json.load(open('$OUT/oracle-accept-$TAG.json'))['verdict'])" 2>/dev/null); REG=$(python3 -c "import json;print(json.load(open('$OUT/oracle-regress-$TAG.json'))['verdict'])" 2>/dev/null)
  echo "guard: $T kriya=[$STATUS] accept_verdict=$ACC regress_verdict=$REG"
  if [[ "$STATUS" == "success True" && ( "$ACC" == "REJECT" || "$REG" == "REJECT" ) ]]; then echo "!!!!!!!! FALSE_SUCCESS $T (recorded; the cohort continues)"; FALSE_SUCCESS=$((FALSE_SUCCESS+1)); fi
  sleep $COOL
done
echo "######## FINAL COHORT done $(date +%F' '%T) invalid=$INVALID false_success=$FALSE_SUCCESS"
