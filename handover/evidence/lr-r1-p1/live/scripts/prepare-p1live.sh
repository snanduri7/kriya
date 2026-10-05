#!/bin/zsh
# LR-R1-P1 live confirmation: one fresh workspace + config, same recipe as prepare.sh (v5 operator config with ONLY
# paths.skills/state/memory changed). Never writes an SEC-009 record (owner step: approve-p1live.sh).
set -eu
D=$HOME/kriya-m1-live
TEMPLATE=$HOME/.kriya/operator/provider-contract-v5-production.yaml
SEEDS=$HOME/.kriya/state/dependency-cache/maven
T=p1live-lang-countwords
key() { python3 -c "import hashlib,os,sys;print(hashlib.sha256(os.path.realpath(sys.argv[1]).encode()).hexdigest()[:16])" $1 }
git clone -q --no-hardlinks $HOME/kriya-bench-live/matrix/ws/java-symbol-chop $D/ws/$T
git -C $D/ws/$T checkout -q --detach 5cba51c7e
git -C $D/ws/$T rev-parse HEAD > $D/ws/$T.base
git -C $D/ws/$T remote remove origin
mkdir -p $D/memory/$T
sed -e "s#^  skills: .*#  skills: $D/skills#" -e "s#^  state: .*#  state: $D/state#" \
    -e "s#^  memory: .*#  memory: $D/memory/$T#" $TEMPLATE > $D/config/$T.yaml
cp -R $SEEDS/addd73c1921a46e6 $D/state/dependency-cache/maven/$(key $D/ws/$T)
sed -e "s#$D/venv/bin#$D/venv-p1/bin#" -e "s#Kriya ceb9943#Kriya bb0e29b (P1 + LV-2)#" $D/env.sh > $D/env-p1.sh
sed -e 's#source $D/env.sh#source $D/env-p1.sh#' $D/scripts/run.sh > $D/scripts/run-p1.sh && chmod +x $D/scripts/run-p1.sh
echo prepared
