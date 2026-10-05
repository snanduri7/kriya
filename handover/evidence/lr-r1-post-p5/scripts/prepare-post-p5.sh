#!/bin/zsh
# LR-R1 post-P5 live success validation: 5 fresh workspaces + configs (v5 operator config with ONLY
# paths.skills/state/memory changed). Never writes an SEC-009 record (owner step: approve-post-p5.sh).
set -eu
D=$HOME/kriya-m1-live
TEMPLATE=$HOME/.kriya/operator/provider-contract-v5-production.yaml
SEEDS=$HOME/.kriya/state/dependency-cache/maven
B=$HOME/kriya-bench-live/matrix/ws
key() { python3 -c "import hashlib,os,sys;print(hashlib.sha256(os.path.realpath(sys.argv[1]).encode()).hexdigest()[:16])" $1 }
ws() {  # ws <name> <source repo> <base> [maven seed]
  local T=$1 SRC=$2 BASE=$3 SEED=${4:-}
  git clone -q --no-hardlinks $SRC $D/ws/$T
  git -C $D/ws/$T checkout -q --detach $BASE
  git -C $D/ws/$T rev-parse HEAD > $D/ws/$T.base
  git -C $D/ws/$T remote remove origin
  mkdir -p $D/memory/$T
  sed -e "s#^  skills: .*#  skills: $D/skills#" -e "s#^  state: .*#  state: $D/state#" \
      -e "s#^  memory: .*#  memory: $D/memory/$T#" $TEMPLATE > $D/config/$T.yaml
  if [ -n "$SEED" ]; then cp -R $SEEDS/$SEED $D/state/dependency-cache/maven/$(key $D/ws/$T); fi
}
ws v5-t1-httpx-headers-count     $B/python-error-invalidurl 3de191518
ws v5-t2-moreit-count-unique     $B/python-symbol-one       0054e02
ws v5-t3-lang-ascii-whitespace   $B/java-symbol-chop        5cba51c7e addd73c1921a46e6
ws v5-t4-lang-count-true         $B/java-symbol-chop        5cba51c7e addd73c1921a46e6
ws v5-t5-petclinic-pet-count     $B/spring-boot-pagesize    500158f73 7c1a0b653ba7a0b3
sed -e "s#$D/venv/bin#$D/venv-p5/bin#" -e "s#Kriya ceb9943#Kriya 6530138 (M1+P1+P4+P5)#" $D/env.sh > $D/env-p5.sh
sed -e 's#source $D/env.sh#source $D/env-p5.sh#' $D/scripts/run.sh > $D/scripts/run-p5.sh && chmod +x $D/scripts/run-p5.sh
echo prepared
