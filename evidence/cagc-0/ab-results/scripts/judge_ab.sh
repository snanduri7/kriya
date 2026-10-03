#!/bin/zsh
# Fixed held-out judge for one A/B run, identical for both arms: the arm's workspace exactly as Kriya left it
# (every file except Kriya's .kriya/ state and build output), the bench task's own judge tests and acceptance rule,
# judged by ONE fixed judge implementation (the main checkout's PolymorphicValidator for JVM tasks, the bench's
# pinned judge venvs for Python). usage: judge_ab.sh <A|B> <task> <label>
set -u
N=$1; T=$2; L=$3; AB=$HOME/kriya-cagc-ab; M=$HOME/kriya-bench-live/matrix; J=$M/judge
W=$AB/$N/ws/$T; OUT=$AB/$N/evidence/$T.$L; mkdir -p $OUT
JUDGE_PY=/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/python
case $T in
  python-*)
    case $T in python-error-invalidurl) V=$HOME/kriya-bench-live/jv-httpx-error-7c0cda153d30/bin/python;; *) V=$J/jv-more/bin/python;; esac
    (cd $W && PYTHONDONTWRITEBYTECODE=1 $V -m pytest -q -p no:cacheprovider -rf $(cat $J/$T.tests) 2>&1) > $OUT/judge.log
    grep -E '^(SUB)?FAILED' $OUT/judge.log | sed 's/ - .*//' | sort > $OUT/after_failed
    if cmp -s $OUT/after_failed $J/$T.gold_failed; then R=SOLVED; else R=NOT_SOLVED; fi
    echo "JUDGE $N $T $L: $R (after_failed=$(wc -l < $OUT/after_failed | tr -d ' ') gold_failed=$(wc -l < $J/$T.gold_failed | tr -d ' '))" | tee $OUT/judge.result
    exit 0;;
  spring-boot-pagesize) TEST=OwnerPageSizeJudgeTests; DEST=src/test/java/org/springframework/samples/petclinic/owner; SRC=$J/$T/OwnerPageSizeJudgeTests.java;;
  spring-xml-pettypes-cache) TEST=PetTypesCacheJudgeTests; DEST=src/test/java/org/springframework/samples/petclinic/service; SRC=$J/$T/PetTypesCacheJudgeTests.java;;
  java-symbol-fraction) TEST=FractionTest;;
  java-behavior-accents) TEST=StringUtilsTrimStripTest;;
  java-symbol-chop) TEST=StringUtilsTest;;
esac
D=$(mktemp -d /tmp/cagc-judge.XXXX)
rsync -a --exclude .kriya --exclude target $W/ $D/after/
[ -n "${SRC:-}" ] && cp $SRC $D/after/$DEST/
source $AB/$N/env.sh
cd $W && $JUDGE_PY - "$D/after" "$AB/$N/config/$T.yaml" "$TEST" "$OUT/judge.log" <<'EOF' 2>&1 | grep "^@@\|JUDGE" > $OUT/judge.raw
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
R=$(grep -q "JUDGE: SOLVED" $OUT/judge.raw && echo SOLVED || echo NOT_SOLVED)
echo "JUDGE $N $T $L: $R $(grep '^@@' $OUT/judge.raw)" | tee $OUT/judge.result
rm -rf $D
