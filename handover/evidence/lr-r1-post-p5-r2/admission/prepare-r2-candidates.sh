#!/bin/zsh
# LR-R1 post-P5 validation R2: candidate workspaces on upstream revisions (not the CAGC benchmark bases) + configs
# (v5 operator config with ONLY paths.skills/state/memory changed). No model is run here; no SEC-009 record written.
set -eu
D=$HOME/kriya-m1-live
TEMPLATE=$HOME/.kriya/operator/provider-contract-v5-production.yaml
SEEDS=$HOME/.kriya/state/dependency-cache/maven
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
ws r2c-py-moreit       $HOME/kriya-bench/more-itertools               1ea82a7
ws r2c-py-httpx        $HOME/kriya-bench/httpx                        b5addb6
ws r2c-py-tomli        $HOME/kriya-bench-r2/tomli                     8479ed2
ws r2c-java-lang-a     $HOME/kriya-bench/commons-lang                 4ee346e59 addd73c1921a46e6
ws r2c-java-lang-b     $HOME/kriya-bench/commons-lang                 4ee346e59 addd73c1921a46e6
ws r2c-java-cli        $HOME/kriya-bench-r2/commons-cli               d95484f   addd73c1921a46e6
ws r2c-spring-xml      $HOME/kriya-bench/spring-framework-petclinic   09351b3   4feb4f91e96e2954
echo prepared
