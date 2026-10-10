#!/bin/zsh
# run_final_12_c3.sh: the final backend-readiness measurement after the THIRD repair cycle, on ONE executable revision -
# certified product 3844540 (checkout main 6c917fa = 3844540 + one records-only evidence commit; product tree byte-identical,
# asserted below). The complete frozen primary six, then the complete frozen cohort-2 six - each ONE Kriya run labelled -c3
# (p5_run.sh / c2_run_005.sh refuse a second run, a manifest mismatch, a dirty workspace, an unqualified model, a dirty or
# non-main Kriya checkout), then the frozen EXTERNAL oracle on the task workspace, tagged ws-c3, written into the labelled
# directory. The earlier P5-<T>, -r2, -final and C2-<S>, -final evidence is never touched. Serial by construction.
# Classification stays by hand (CLASSIFICATION.md per run, cohort-002/CLASSIFICATION_RULES.md), EXCEPT the one mechanical
# stop the owner requires: a Kriya SUCCESS whose external oracle rejects the tree (ACCEPT or REGRESS exit != 0) is a
# FALSE_SUCCESS candidate - the driver stops before the next task (exit 99).
set -u
F=$HOME/kriya-m1-live/backend-final-closure-005; P=$F/primary; C5=$F/cohort-002; RP=$F/repair-002
R=$HOME/kriya-m1-live/backend-readiness-001; R4=$HOME/kriya-m1-live/backend-readiness-004/cohort-002
K=$HOME/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo
LABEL=-c3; TAG=ws-c3; PRODUCT=38445408b7ad5a1a1dfd588dd54ee11406185799; CHECKOUT=6c917fa77ec27a3b0a23b353bd1770b4eda5bfa1
echo "######## final 12 (c3) start $(date +%F' '%T) kriya main $(git -C $K rev-parse HEAD)"
# Binding precondition: the checkout is exactly the pushed main, and its product tree is byte-identical to the certified executable.
[ "$(git -C $K rev-parse HEAD)" = "$CHECKOUT" ] || { echo "ABORT: Kriya checkout is $(git -C $K rev-parse HEAD), expected $CHECKOUT"; exit 1; }
git -C $K diff --quiet $PRODUCT HEAD -- kriya plugins tests scripts pyproject.toml || { echo "ABORT: product tree differs from $PRODUCT"; exit 1; }
[ "$(git -C $K rev-parse HEAD)" = "$(git -C $K rev-parse origin/main)" ] || { echo "ABORT: main != origin/main"; exit 1; }
echo "binding: checkout $CHECKOUT == origin/main; product tree == $PRODUCT (kriya plugins tests scripts pyproject.toml identical)"
for T in T1 T2 T3 T4 T5 T6 S1_A S2_A S3_A S4_A S4_B S6_A; do
  case $T in T*) O=$P/P5-$T$LABEL;; *) O=$C5/C2-$T$LABEL;; esac
  [ -e $O ] && { echo "ABORT: $O exists (one run only)"; exit 1; }
done
guard() {  # guard <out dir> <tag>: stop on a success the oracle rejects; print the by-hand classification inputs
  local OUT=$1 TAG=$2 STATUS ACC REG LEAK
  STATUS=$(python3 -c "import json;r=json.load(open('$OUT/result.json'));print(r.get('status'), r.get('quality_gates_passed'))" 2>/dev/null)
  ACC=$(python3 -c "import json;print(json.load(open('$OUT/oracle-accept-$TAG.json'))['exit'])" 2>/dev/null); REG=$(python3 -c "import json;print(json.load(open('$OUT/oracle-regress-$TAG.json'))['exit'])" 2>/dev/null)
  LEAK=$(python3 -c "import json;print(json.load(open('$OUT/leak_check.json')).get('verdict'))" 2>/dev/null)
  echo "guard: kriya=[$STATUS] accept_exit=$ACC regress_exit=$REG leak=${LEAK:-n/a} tree_entries_after=$(wc -l < $OUT/git_status_after_generate.txt | tr -d ' ') verify=$(python3 -c "import json;print(json.load(open('$OUT/verify.json')).get('verified', json.load(open('$OUT/verify.json')).get('status')))" 2>/dev/null)"
  if [[ "$STATUS" == "success True" && ( "$ACC" != "0" || "$REG" != "0" ) ]]; then echo "!!!!!!!! FALSE_SUCCESS CANDIDATE in $OUT - execution stopped for classification"; exit 99; fi
}
for T in T1 T2 T3 T4 T5 T6; do
  echo "######## $T$LABEL $(date +%F' '%T)"
  $P/p5_run.sh $T $LABEL; echo "p5_run exit=$?"
  $RP/p5_evaluate_final.sh $T $R/tasks/$T/ws $TAG $LABEL; echo "p5_evaluate exit=$?"
  guard $P/P5-$T$LABEL $TAG
done
for S in S1_A S2_A S3_A S4_A S4_B S6_A; do
  echo "######## $S$LABEL $(date +%F' '%T)"
  $C5/c2_run_005.sh $S $LABEL; echo "c2_run exit=$?"
  $RP/c2_evaluate_final.sh $S $R4/tasks/$S/ws $TAG $LABEL; echo "c2_evaluate exit=$?"
  guard $C5/C2-$S$LABEL $TAG
done
echo "######## final 12 (c3) done $(date +%F' '%T)"
