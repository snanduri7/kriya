#!/bin/zsh
# POST-REG-R1 REG-R1 smoke gate for arm $1 (a|b): scratch copy of the frozen base (never the arm workspace). No model.
set -u
A=$1; D=$HOME/kriya-m1-live; N=postreg-$A; S=$D/postreg-smoke-$A; R=$S/gr1-graphify; OUT=$D/evidence/postreg/smoke-$A
SRC=$HOME/kriya-live-validation/graphify-canonical-853aa43/workspace
[ -e $S ] && { echo "ABORT: $S exists"; exit 1; }
mkdir -p $S $OUT; git init -q $R && git -C $R fetch -q --no-tags $SRC 67f99bd0059dd1bac9e44382907ef9f10098b39f && git -C $R checkout -q --detach 67f99bd0059dd1bac9e44382907ef9f10098b39f
git -C $R worktree add -q --detach .kriya/worktree 67f99bd0059dd1bac9e44382907ef9f10098b39f || exit 3
source $D/env-$N.sh
export KRIYA_STATE_DIR=$S/state KRIYA_LOG_DIR=$S/logs; mkdir -p $KRIYA_STATE_DIR $KRIYA_LOG_DIR
WS=$D/ws-$N/gr1-graphify; BEFORE=$(git -C $WS status --porcelain --ignored | shasum -a 256)
cd $WS && python $D/scripts/postreg_smoke.py $D/config/$N.yaml $R $OUT/smoke.json > $OUT/smoke.log 2>&1; echo "exit=$?" >> $OUT/smoke.log
[ "$BEFORE" = "$(git -C $WS status --porcelain --ignored | shasum -a 256)" ] && echo "arm workspace untouched" >> $OUT/smoke.log
grep -v -E "WARNING|INFO" $OUT/smoke.log | tail -40
