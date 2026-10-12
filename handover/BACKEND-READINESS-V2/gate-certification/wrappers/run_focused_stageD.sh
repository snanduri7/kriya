#!/bin/zsh
# BACKEND-READINESS-V2 Stage D focused live: ONE run per task named, strictly serial, label -v4a (earlier labels untouched). Same run scripts
# (p5_run_v2.sh / c2_run_v2.sh) as the frozen runs; the oracle is judged through evaluate_v3.sh, which classifies every oracle exit
# (PASS / REJECT / ENVIRONMENT_UNAVAILABLE / INDETERMINATE) before it can be read as a verdict. A Kriya SUCCESS that the oracle
# REJECTS stops the driver (exit 99); an oracle that could not judge is reported INVALID, never classified.
set -u
V2=$HOME/kriya-m1-live/backend-readiness-v2; R=$HOME/kriya-m1-live/backend-readiness-001; R4=$HOME/kriya-m1-live/backend-readiness-004/cohort-002
INVALID=0; K=$HOME/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo; LABEL=-v4a; TAG=ws-v4a; COOL=${COOL:-120}; W=$(cd "$(dirname "$0")" && pwd)
echo "######## stage D focused live start $(date +%F' '%T) kriya $(git -C $K branch --show-current) $(git -C $K rev-parse HEAD)"
for T in "$@"; do case $T in T*) O=$V2/primary/P5-$T$LABEL;; *) O=$V2/cohort-002/C2-$T$LABEL;; esac; [ -e $O ] && { echo "ABORT: $O exists"; exit 1; }; done
guard() { local OUT=$1 STATUS ACC REG
  STATUS=$(python3 -c "import json;r=json.load(open('$OUT/result.json'));print(r.get('status'), r.get('quality_gates_passed'))" 2>/dev/null)
  ACC=$(python3 -c "import json;print(json.load(open('$OUT/oracle-accept-$TAG.json'))['verdict'])" 2>/dev/null); REG=$(python3 -c "import json;print(json.load(open('$OUT/oracle-regress-$TAG.json'))['verdict'])" 2>/dev/null)
  echo "guard: kriya=[$STATUS] accept_verdict=$ACC regress_verdict=$REG"
  case "$ACC/$REG" in PASS/PASS|REJECT/*|*/REJECT|PASS/REJECT) ;; *) echo "INVALID: oracle verdict missing or unjudgeable in $OUT"; INVALID=1;; esac
  [ -z "$STATUS" ] && { echo "INVALID: no Kriya result.json in $OUT"; INVALID=1; }
  if [[ "$STATUS" == "success True" && ( "$ACC" == "REJECT" || "$REG" == "REJECT" ) ]]; then echo "!!!!!!!! FALSE_SUCCESS CANDIDATE in $OUT"; exit 99; fi; }
for T in "$@"; do
  echo "######## $T$LABEL $(date +%F' '%T)"
  case $T in
    T*) $V2/p5_run_v2.sh $T $LABEL; echo "run exit=$?"; $W/evaluate_v3.sh primary $T $R/tasks/$T/ws $TAG $LABEL; EV=$?; echo "evaluate exit=$EV"; [ $EV -ne 0 ] && INVALID=1; guard $V2/primary/P5-$T$LABEL;;
    *)  $V2/c2_run_v2.sh $T $LABEL; echo "run exit=$?"; $W/evaluate_v3.sh c2 $T $R4/tasks/$T/ws $TAG $LABEL; EV=$?; echo "evaluate exit=$EV"; [ $EV -ne 0 ] && INVALID=1; guard $V2/cohort-002/C2-$T$LABEL;;
  esac
  sleep $COOL
done
echo "######## stage D focused live done $(date +%F' '%T) invalid=$INVALID"
exit $INVALID
