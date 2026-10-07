#!/bin/zsh
# LR-R1 post-P5 R2: two more Python candidates (upstream, unmodified; both declare runtime dependencies in
# pyproject.toml, so Kriya installs them plus pytest). No model run; no SEC-009 record written.
set -eu
D=$HOME/kriya-m1-live
TEMPLATE=$HOME/.kriya/operator/provider-contract-v5-production.yaml
ws() {
  local T=$1 SRC=$2 BASE=$3
  git clone -q --no-hardlinks $SRC $D/ws/$T
  git -C $D/ws/$T checkout -q --detach $BASE
  git -C $D/ws/$T rev-parse HEAD > $D/ws/$T.base
  git -C $D/ws/$T remote remove origin
  mkdir -p $D/memory/$T
  sed -e "s#^  skills: .*#  skills: $D/skills#" -e "s#^  state: .*#  state: $D/state#" \
      -e "s#^  memory: .*#  memory: $D/memory/$T#" $TEMPLATE > $D/config/$T.yaml
}
ws r2c-py-inflect     $HOME/kriya-bench-r2/inflect     262a247
ws r2c-py-cssselect2  $HOME/kriya-bench-r2/cssselect2  dc2690c
echo prepared
