#!/bin/zsh
# evaluate_v3.sh <primary|c2> <TID> <TREE> <TAG> <LABEL>: the frozen EXTERNAL oracle (accept, regress) on a scratch copy of TREE, exactly as
# p5_evaluate_v2.sh / c2_evaluate_v2.sh, PLUS the qualification-environment classification (BACKEND-READINESS-V2 Stage D2): every
# oracle exit is classified PASS / REJECT / ENVIRONMENT_UNAVAILABLE / INDETERMINATE from its log (oracle_preflight.py) BEFORE it can
# be read as a verdict. The JSON keeps the old keys (tree, scratch, exit) and adds verdict + reason. ENVIRONMENT_UNAVAILABLE and
# INDETERMINATE are INVALID FOR THIS RUN: never a SAFE_FAILURE, a GENUINE or an oracle rejection. The oracle scripts are not touched.
# Test seams: PREFLIGHT_SCRIPT overrides the classifier; ORACLE_DIR overrides the directory holding accept.sh / regress.sh.
set -u
KIND=$1; T=$2; TREE=$3; TAG=$4; LABEL=$5
H=$HOME/kriya-m1-live; V2=$H/backend-readiness-v2; PRE=${PREFLIGHT_SCRIPT:-$(cd "$(dirname "$0")/.." && pwd)/oracle_preflight.py}
if [ "$KIND" = primary ]; then D=$H/backend-readiness-001/tasks/$T; OUT=$V2/primary/P5-$T$LABEL; else D=$H/backend-readiness-004/cohort-002/tasks/$T; OUT=$V2/cohort-002/C2-$T$LABEL; fi
OUT=${OUT_DIR:-$OUT}; ORACLES=${ORACLE_DIR:-$D/oracle}; SCRATCH=${SCRATCH_DIR:-$V2/scratch}
mkdir -p $SCRATCH $OUT; INVALID=0
for K in accept regress; do
  SCR=$(mktemp -d $SCRATCH/$T-$TAG-$K-XXXX); rsync -a --exclude .kriya $TREE/ $SCR/
  echo "== $K ($TAG) scratch=$SCR"; S=$(date +%s); $ORACLES/$K.sh $TREE $SCR > $OUT/$K-$TAG.txt 2>&1; RC=$?
  VERDICT=$(python3 $PRE classify $RC $OUT/$K-$TAG.txt); CLS=$?
  # Only the documented classifier exit codes are verdicts; a crashed classifier (traceback = exit 1, usage = 2, empty output)
  # is itself an unjudgeable run, never a REJECT.
  case $CLS in 0|1|3|4) ;; *) CLS=4;; esac
  [ -z "$VERDICT" ] && { VERDICT=$'INDETERMINATE\tthe classifier produced no verdict'; CLS=4; }
  case ${VERDICT%%$'\t'*} in PASS) [ $CLS -eq 0 ] || { VERDICT=$'INDETERMINATE\tclassifier exit and verdict disagree'; CLS=4; };; REJECT) [ $CLS -eq 1 ] || { VERDICT=$'INDETERMINATE\tclassifier exit and verdict disagree'; CLS=4; };; esac
  echo "$K exit=$RC seconds=$(( $(date +%s) - S )) verdict=${VERDICT%%$'\t'*}"; grep -E "_EXIT=|TOTALS|HIDDEN|FORBIDDEN|NOOP|COMPAT|tests=" $OUT/$K-$TAG.txt | tail -4
  python3 - "$TREE" "$SCR" "$RC" "$VERDICT" "$OUT/oracle-$K-$TAG.json" <<'PY'
import json, sys
tree, scr, rc, verdict, path = sys.argv[1:6]
name, _, reason = verdict.partition("\t")
json.dump({"tree": tree, "scratch": scr, "exit": int(rc), "verdict": name, "reason": reason,
           "counts_as_candidate_verdict": name in ("PASS", "REJECT")}, open(path, "w"))
PY
  [ $CLS -ge 3 ] && INVALID=1
done
[ $INVALID -eq 1 ] && echo "QUALIFICATION INVALID FOR THIS RUN: the oracle could not judge the candidate (environment/indeterminate); repeat after repair, do not classify"
exit $INVALID
