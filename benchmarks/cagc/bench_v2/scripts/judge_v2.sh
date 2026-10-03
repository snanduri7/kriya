#!/bin/zsh
# bench v2 judge qualification: the same validator judge as ~/kriya-cagc-ab-judge/judge_tree.sh, on a fresh tree.
#   base = fresh clone at the manifest base; ref = base + the manifest reference (commit's non-test changes, or patch)
# usage: judge_v2.sh <task id> <base|ref> <workspace source> <base commit> <ref: commit sha | patch path> <judge test>
#        [held-out judge file] [held-out dest dir] [A/B config/ws task name]
set -u
T=$1; V=$2; SRC=$3; BASE=$4; REF=$5; TEST=$6; HELD=${7:-}; DEST=${8:-}; CFGT=${9:-$T}
AB=$HOME/kriya-cagc-ab; R=$HOME/kriya-cagc-bench-v2/judge-out; TREE=$R/$T/$V; OUT=$R/$T/$V.out; mkdir -p $OUT
JUDGE_PY=/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/python
# The validator the Java judges run is THIS checkout's Kriya (never whatever the venv's editable install points at).
export PYTHONPATH=${0:A:h:h:h:h:h}
rm -rf "${TREE:?}"; git clone -q --no-hardlinks $SRC $TREE && git -C $TREE checkout -q --detach $BASE || exit 2
if [ $V = ref ]; then
  if [ -f "$REF" ]; then git -C $TREE apply $REF || { echo "JUDGE $T ref: REF_DOES_NOT_APPLY" | tee $OUT/judge.result; exit 0; }
  else
    FIXSRC=$(git -C $TREE diff --name-only $REF^ $REF | grep -vE '(^|/)(src/test/|tests?/)' | grep -vE '\.(md|rst)$')
    git -C $TREE diff $REF^ $REF -- ${(f)FIXSRC} | git -C $TREE apply || { echo "JUDGE $T ref: REF_DOES_NOT_APPLY" | tee $OUT/judge.result; exit 0; }
  fi
fi
git -C $TREE diff --stat > $OUT/applied.stat
[ -n "$HELD" ] && cp $HELD $TREE/$DEST/
source $AB/A/env.sh
cd $AB/A/ws/$CFGT && $JUDGE_PY - "$TREE" "$AB/A/config/$CFGT.yaml" "$TEST" "$OUT/judge.log" <<'PY' 2>&1 | grep "^@@\|JUDGE" > $OUT/judge.raw
import os, re, sys
import kriya
print("@@ kriya", os.path.dirname(kriya.__file__))
from kriya.config.config import load_config
from kriya.tools.validate import PolymorphicValidator
root, config, test, log = sys.argv[1:]
cfg = load_config(config)
r = PolymorphicValidator(root, original_workspace_path=os.getcwd(), autonomy_cfg=cfg.autonomy).run_tests(target_test=test)
out = r["output"]; open(log, "w").write(out)
totals = re.findall(r"Tests run: \d+, Failures: \d+, Errors: \d+, Skipped: \d+(?!, Time)", out)
print("@@", "success", r["success"], totals[-1:])
print("JUDGE: SOLVED" if r["success"] and totals and ", Failures: 0, Errors: 0" in totals[-1] else "JUDGE: NOT SOLVED")
PY
RES=$(grep -q "JUDGE: SOLVED" $OUT/judge.raw && echo PASS || echo FAIL)
echo "JUDGE $T $V: $RES $(grep '^@@' $OUT/judge.raw)" | tee $OUT/judge.result
