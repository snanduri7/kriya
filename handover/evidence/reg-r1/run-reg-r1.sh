#!/bin/zsh
# REG-R1 Part 1: BASE-1, BASE-2, CANDIDATE-1, CANDIDATE-2 - each in a FRESHLY created git worktree at the live-shaped
# path (live: baseline in .kriya/worktree, POST in a fresh sibling candidate worktree), Kriya a049c02 PolymorphicValidator.
# No model calls. Refuses to overwrite evidence.
set -u
D=$HOME/kriya-m1-live; E=$D/evidence/reg-r1/runs; R=$D/reg-r1/gr1-graphify; CFG=$D/config/qwen38-matched-developer.yaml
CAND_ENGINE=$D/evidence/gr1q-graphify/independent/staged_candidate_engine.py
[ -e $E ] && { echo "ABORT: $E exists"; exit 1; }
[ "$(shasum -a 256 $CAND_ENGINE | cut -d' ' -f1)" = e47eacba60c19860270cb8c46fe95f2443a897eb5ffe0fddd921ed1cf0426034 ] || { echo "ABORT: candidate digest"; exit 1; }
mkdir -p $E $D/reg-r1/state $D/reg-r1/logs
source $D/env-gr1q.sh
export KRIYA_STATE_DIR=$D/reg-r1/state KRIYA_LOG_DIR=$D/reg-r1/logs
{ docker version --format '{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}'; docker info --format '{{.OperatingSystem}} cpus={{.NCPU}} mem={{.MemTotal}}'; uname -a; } > $E/host_environment.txt 2>&1
fresh() {  # $1 = BASE|CAND
  local P
  if [ $1 = BASE ]; then P=.kriya/worktree; else P=.kriya/worktrees/candidate-reg-r1; fi
  git -C $R worktree remove --force $P 2>/dev/null; rm -rf $R/$P
  git -C $R worktree add -q --detach $P 67f99bd0059dd1bac9e44382907ef9f10098b39f || exit 3
  [ $1 = CAND ] && cp $CAND_ENGINE $R/$P/graphify/extractors/engine.py
  print -r -- "$R/$P"
}
for spec in BASE-1:BASE BASE-2:BASE CANDIDATE-1:CAND CANDIDATE-2:CAND; do
  L=${spec%%:*}; K=${spec##*:}; P=$(fresh $K)
  { echo "label=$L kind=$K path=$P"; git -C $P rev-parse HEAD 'HEAD^{tree}'; git -C $P status --porcelain --ignored; shasum -a 256 $P/graphify/extractors/engine.py; } > $E/$L.pre_state.txt
  docker images --digests --no-trunc > $E/$L.docker_images.txt 2>&1
  echo "== $L start $(date +%T)"
  (cd $D/ws/qwen3coder-roleplacement-qual && python $D/evidence/reg-r1/reg_r1_repro.py $CFG $R $E $L $K) > $E/$L.harness.log 2>&1
  echo "== $L exit=$? end $(date +%T)"; tail -1 $E/$L.harness.log
  { git -C $P status --porcelain --ignored; } > $E/$L.post_state.txt
  ls $P/.kriya/venv/lib/python*/site-packages 2>/dev/null | grep -E "dist-info$" | sort > $E/$L.venv_dists.txt
done
echo "== done $(date +%T)"
