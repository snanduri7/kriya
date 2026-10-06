#!/bin/zsh
# LR-R1 post-P3 bounded live validation (Kriya 7e4de32): 7 fresh workspaces (never applied before) + configs
# (v5 operator config with ONLY paths.skills/state/memory changed). No model call; no SEC-009 record written.
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
# A1/A2/B2 replaced before any model call (C0 preconditions; see TASK_SELECTION_POST_P3.md):
# inflect tests cannot import the package when one file runs alone (pytest importlib mode + launcher);
# cssselect2's only test file has xfail cases (reported skipped: C0 needs every case passed).
ws p3v-a1-py-freezegun     $HOME/kriya-bench-p3v/freezegun                   92d61b3
ws p3v-a2-py-freezegun     $HOME/kriya-bench-p3v/freezegun                   92d61b3
ws p3v-a3-java-lang        $HOME/kriya-bench/commons-lang                    4ee346e59 addd73c1921a46e6
ws p3v-a4-java-cli         $HOME/kriya-bench-r2/commons-cli                  d95484f   addd73c1921a46e6
ws p3v-a5-spring-xml       $HOME/kriya-bench/spring-framework-petclinic      09351b3   4feb4f91e96e2954
ws p3v-b1-java-lang        $HOME/kriya-bench/commons-lang                    4ee346e59 addd73c1921a46e6
ws p3v-b2-py-cssselect2    $HOME/kriya-bench-r2/cssselect2                   dc2690c
echo prepared
