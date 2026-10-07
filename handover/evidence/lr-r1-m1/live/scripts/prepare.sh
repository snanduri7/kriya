#!/bin/zsh
# LR-R1-M1 bounded live validation: workspaces + per-workspace configs. Each config is the v5 operator config with
# ONLY paths.skills/state/memory changed (plus evidence.attempt_recorder.capture: off for the capture-off control).
# Never writes an SEC-009 authority record (owner step: approve.sh). Shared read-only: qualification records,
# Ollama and its pinned models, Maven seed snapshots (copied).
set -eu
D=$HOME/kriya-m1-live
TEMPLATE=$HOME/.kriya/operator/provider-contract-v5-production.yaml
SEEDS=$HOME/.kriya/state/dependency-cache/maven
[ -d $D/skills ] || cp -R $HOME/.kriya/operator/provider-contract-v3/skills $D/skills
key() { python3 -c "import hashlib,os,sys;print(hashlib.sha256(os.path.realpath(sys.argv[1]).encode()).hexdigest()[:16])" $1 }
ws() {  # ws <name> <source repo> <base> [maven seed] [capture]
  local T=$1 SRC=$2 BASE=$3 SEED=${4:-} CAPTURE=${5:-}
  git clone -q --no-hardlinks $SRC $D/ws/$T
  git -C $D/ws/$T checkout -q --detach $BASE
  git -C $D/ws/$T rev-parse HEAD > $D/ws/$T.base
  git -C $D/ws/$T remote remove origin
  mkdir -p $D/memory/$T
  sed -e "s#^  skills: .*#  skills: $D/skills#" -e "s#^  state: .*#  state: $D/state#" \
      -e "s#^  memory: .*#  memory: $D/memory/$T#" $TEMPLATE > $D/config/$T.yaml
  [ -n "$CAPTURE" ] && printf 'evidence:\n  attempt_recorder:\n    capture: %s\n' $CAPTURE >> $D/config/$T.yaml
  if [ -n "$SEED" ]; then
    mkdir -p $D/state/dependency-cache/maven
    cp -R $SEEDS/$SEED $D/state/dependency-cache/maven/$(key $D/ws/$T)
  fi
}
B=$HOME/kriya-bench-live/matrix/ws
ws run1-httpx-isvalid       $B/python-error-invalidurl 3de191518
ws run2-lang-countwords     $B/java-symbol-chop        5cba51c7e addd73c1921a46e6
ws run3-petclinic           $B/spring-boot-pagesize    500158f73 7c1a0b653ba7a0b3
ws ctrl-httpx-isvalid-off   $B/python-error-invalidurl 3de191518 "" off
cat > $D/env.sh <<ENV
# LR-R1-M1 live validation (Kriya ceb9943): source before any kriya command here.
export KRIYA_STATE_DIR=$D/state KRIYA_LOG_DIR=$D/logs KRIYA_AUTHORITY_HOME=$D/authority
export KRIYA_STATIC_ANALYSIS_HOME=$D/static-analysis KRIYA_MCP_APPROVAL_HOME=$D/mcp-approvals
unset KRIYA_QUALIFICATION_HOME KRIYA_CERTIFICATION_HOME PYTHONPATH   # shared read-only defaults (~/.kriya/...)
export PATH=$D/venv/bin:\$PATH
ENV
echo prepared
