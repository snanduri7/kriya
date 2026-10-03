#!/bin/zsh
# CAGC-0 whole-feature A/B preparation (KRIYA_CAGC v0.7 §13.1). Builds two arms that differ ONLY in the Kriya
# revision: Arm A = the CAGC branch base, Arm B = the CAGC branch head. Per arm, isolated: Kriya venv (installed
# from a clean clone at the arm's revision), task workspaces (fresh clones of the bench task bases, own .kriya/),
# KRIYA_STATE_DIR (run history, context-recall certification, Kriya-owned Maven caches), KRIYA_LOG_DIR,
# paths.memory (vector + graph index, per task), KRIYA_AUTHORITY_HOME, KRIYA_STATIC_ANALYSIS_HOME,
# KRIYA_MCP_APPROVAL_HOME. Shared read-only: KRIYA_QUALIFICATION_HOME (default ~/.kriya/qualifications), Ollama and
# its pinned models, the v5 operator config template (only paths.* differ per arm/task).
# Maven caches are seeded from ONE snapshot per task, copied identically into both arms. Python project venvs live
# in each workspace's own .kriya/venv and are created by each arm's own run from the same declarations
# (a virtualenv embeds absolute paths, so it cannot be copied between workspaces).
# Never writes or copies an SEC-009 authority record: approval is the owner's step (MANIFEST.md).
# usage: prepare.sh <arm-A-rev> <arm-B-rev>
set -eu
AB=$HOME/kriya-cagc-ab
REPO=/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode
MATRIX=$HOME/kriya-bench-live/matrix
TEMPLATE=$HOME/.kriya/operator/provider-contract-v5-production.yaml
SKILLS=$HOME/.kriya/operator/provider-contract-v3/skills
TASKS=(java-symbol-fraction java-behavior-accents java-symbol-chop spring-boot-pagesize spring-xml-pettypes-cache
       python-behavior-maxsplit python-symbol-one python-symbol-ichunked python-symbol-zipb python-error-invalidurl)
# One Maven cache snapshot per task (the commons-lang tasks share the project's cache).
typeset -A MAVEN_SEED
MAVEN_SEED=(java-symbol-fraction addd73c1921a46e6 java-behavior-accents addd73c1921a46e6
            java-symbol-chop addd73c1921a46e6 spring-boot-pagesize 7c1a0b653ba7a0b3
            spring-xml-pettypes-cache b725354cd3211fc1)
key() { python3 -c "import hashlib,os,sys;print(hashlib.sha256(os.path.realpath(sys.argv[1]).encode()).hexdigest()[:16])" $1 }

arm() {  # arm <name> <rev>
  local N=$1 REV=$2 D=$AB/$1
  [ -e $D ] && { echo "refusing: $D exists"; exit 2; }
  mkdir -p $D/{ws,state,logs,memory,authority,static-analysis,mcp-approvals,config}
  git clone -q --no-hardlinks $REPO $D/src && git -C $D/src checkout -q --detach $REV
  python3 -m venv $D/venv
  $D/venv/bin/pip install -q --upgrade pip
  (cd $D/src && KRIYA_BUILD_REQUIRE_CLEAN=1 $D/venv/bin/pip install -q .)
  cp -R $SKILLS $D/skills
  for T in $TASKS; do
    git clone -q --no-hardlinks $MATRIX/ws/$T $D/ws/$T
    git -C $D/ws/$T checkout -q --detach $(git -C $MATRIX/ws/$T rev-parse HEAD)
    git -C $D/ws/$T remote remove origin
    mkdir -p $D/memory/$T
    sed -e "s#^  skills: .*#  skills: $D/skills#" -e "s#^  state: .*#  state: $D/state#" \
        -e "s#^  memory: .*#  memory: $D/memory/$T#" $TEMPLATE > $D/config/$T.yaml
    if (( ${+MAVEN_SEED[$T]} )); then
      mkdir -p $D/state/dependency-cache/maven
      cp -R $HOME/.kriya/state/dependency-cache/maven/${MAVEN_SEED[$T]} $D/state/dependency-cache/maven/$(key $D/ws/$T)
    fi
  done
  cat > $D/env.sh <<EOF
# Arm $N ($REV): source before any kriya command of this arm.
export KRIYA_STATE_DIR=$D/state KRIYA_LOG_DIR=$D/logs KRIYA_AUTHORITY_HOME=$D/authority
export KRIYA_STATIC_ANALYSIS_HOME=$D/static-analysis KRIYA_MCP_APPROVAL_HOME=$D/mcp-approvals
unset KRIYA_QUALIFICATION_HOME KRIYA_CERTIFICATION_HOME   # shared read-only defaults (~/.kriya/...)
export PATH=$D/venv/bin:\$PATH
EOF
  echo "arm $N ready: $($D/venv/bin/kriya version --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("commit"),d.get("tree"),d.get("dirty"))')"
}

arm A $1
arm B $2
