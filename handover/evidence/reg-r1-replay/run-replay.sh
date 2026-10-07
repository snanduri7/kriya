#!/bin/zsh
# REG-R1 deterministic replay of e47eacba under the certified code. No model calls.
set -u
D=$HOME/kriya-m1-live; E=$D/evidence/reg-r1-replay; R=$D/reg-r1/gr1-graphify; CFG=$D/config/qwen38-matched-developer.yaml
CAND_ENGINE=$D/evidence/gr1q-graphify/independent/staged_candidate_engine.py; WT=$HOME/kriya-wt/p3d
[ "$(shasum -a 256 $CAND_ENGINE | cut -d' ' -f1)" = e47eacba60c19860270cb8c46fe95f2443a897eb5ffe0fddd921ed1cf0426034 ] || { echo "ABORT: candidate digest"; exit 1; }
source $D/env-gr1q.sh
export KRIYA_STATE_DIR=$D/reg-r1/state-replay KRIYA_LOG_DIR=$D/reg-r1/logs-replay PYTHONPATH=$WT
mkdir -p $KRIYA_STATE_DIR $KRIYA_LOG_DIR
for P in .kriya/worktree .kriya/worktrees/candidate-reg-r1; do
  git -C $R worktree remove --force $P 2>/dev/null; rm -rf $R/$P
  git -C $R worktree add -q --detach $P 67f99bd0059dd1bac9e44382907ef9f10098b39f || exit 3
done
cp $CAND_ENGINE $R/.kriya/worktrees/candidate-reg-r1/graphify/extractors/engine.py
{ echo "certified_code=$(git -C $WT rev-parse HEAD)"; git -C $WT log --oneline -3; python -c "import kriya,os;print(os.path.dirname(kriya.__file__))"; } > $E/identity.txt
(cd $D/ws/qwen3coder-roleplacement-qual && python $E/replay_candidate.py $CFG $R $E) > $E/replay.log 2>&1; echo "exit=$?" >> $E/replay.log
tail -60 $E/replay.log
