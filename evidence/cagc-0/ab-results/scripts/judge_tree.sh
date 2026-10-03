#!/bin/zsh
# Untouched-base / known-good-fix judge reference (CAGC-0 closure, step 1). Same judge commands, test selection
# and acceptance rule as ~/kriya-cagc-ab/judge_ab.sh, applied to a fresh tree instead of an A/B workspace:
#   base  = a fresh clone of the task base commit, untouched
#   fixed = that base plus the known-good fix commit's non-test changes (mined tasks only)
# usage: judge_tree.sh <task> <base|fixed>
set -u
T=$1; V=$2; AB=$HOME/kriya-cagc-ab; M=$HOME/kriya-bench-live/matrix; J=$M/judge; R=$HOME/kriya-cagc-ab-judge
TREE=$R/$T/$V; OUT=$R/$T/$V.out; mkdir -p $OUT
JUDGE_PY=/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/python
BASE=$(cat $AB/A/$T.base)
rm -rf "${TREE:?}"; git clone -q --no-hardlinks $M/ws/$T $TREE && git -C $TREE checkout -q --detach $BASE
if [ $V = fixed ]; then
  FIX=$(git -C $TREE log --format=%s | grep -m1 -oE "(base: |parent of )[0-9a-f]+" | grep -oE "[0-9a-f]+$")
  [ -z "$FIX" ] && { echo "JUDGE $T fixed: NO_FIX_COMMIT" | tee $OUT/judge.result; exit 0; }
  FIXSRC=$(git -C $TREE diff --name-only $FIX^ $FIX | grep -vE '(^|/)(src/test/|tests?/)' | grep -vE '\.(md|rst)$')
  git -C $TREE diff $FIX^ $FIX -- ${(f)FIXSRC} | git -C $TREE apply || { echo "JUDGE $T fixed: FIX_DOES_NOT_APPLY" | tee $OUT/judge.result; exit 0; }
  echo "$FIX ${(f)FIXSRC}" > $OUT/fix.txt
fi
case $T in
  python-*)
    case $T in python-error-invalidurl) PY=$HOME/kriya-bench-live/jv-httpx-error-7c0cda153d30/bin/python;; *) PY=$J/jv-more/bin/python;; esac
    (cd $TREE && PYTHONDONTWRITEBYTECODE=1 $PY -m pytest -q -p no:cacheprovider -rf $(cat $J/$T.tests) 2>&1) > $OUT/judge.log
    grep -E '^(SUB)?FAILED' $OUT/judge.log | sed 's/ - .*//' | sort > $OUT/after_failed
    if cmp -s $OUT/after_failed $J/$T.gold_failed; then RES=PASS; else RES=FAIL; fi
    echo "JUDGE $T $V: $RES (failed=$(wc -l < $OUT/after_failed | tr -d ' '))" | tee $OUT/judge.result; exit 0;;
  spring-boot-pagesize) TEST=OwnerPageSizeJudgeTests; DEST=src/test/java/org/springframework/samples/petclinic/owner; SRC=$J/$T/OwnerPageSizeJudgeTests.java;;
  spring-xml-pettypes-cache) TEST=PetTypesCacheJudgeTests; DEST=src/test/java/org/springframework/samples/petclinic/service; SRC=$J/$T/PetTypesCacheJudgeTests.java;;
  java-symbol-fraction) TEST=FractionTest;;
  java-behavior-accents) TEST=StringUtilsTrimStripTest;;
  java-symbol-chop) TEST=StringUtilsTest;;
esac
[ -n "${SRC:-}" ] && [ -f "${SRC:-}" ] && cp $SRC $TREE/$DEST/
source $AB/A/env.sh
cd $AB/A/ws/$T && $JUDGE_PY - "$TREE" "$AB/A/config/$T.yaml" "$TEST" "$OUT/judge.log" <<'EOF' 2>&1 | grep "^@@\|JUDGE" > $OUT/judge.raw
import os, re, sys
from kriya.config.config import load_config
from kriya.tools.validate import PolymorphicValidator
root, config, test, log = sys.argv[1:]
cfg = load_config(config)
r = PolymorphicValidator(root, original_workspace_path=os.getcwd(), autonomy_cfg=cfg.autonomy).run_tests(target_test=test)
out = r["output"]; open(log, "w").write(out)
totals = re.findall(r"Tests run: \d+, Failures: \d+, Errors: \d+, Skipped: \d+(?!, Time)", out)
print("@@", "success", r["success"], totals[-1:])
print("JUDGE: SOLVED" if r["success"] and totals and ", Failures: 0, Errors: 0" in totals[-1] else "JUDGE: NOT SOLVED")
EOF
RES=$(grep -q "JUDGE: SOLVED" $OUT/judge.raw && echo PASS || echo FAIL)
echo "JUDGE $T $V: $RES $(grep '^@@' $OUT/judge.raw)" | tee $OUT/judge.result
