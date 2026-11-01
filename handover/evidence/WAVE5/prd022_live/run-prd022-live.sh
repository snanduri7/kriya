#!/bin/zsh
# Wave-5 PRD-022 redirect->corrective-retry LIVE acceptance, ONCE, at the Gate-E qualified identity. Background, no polling.
set -u
H=/Users/sriramnanduri/kriya-gate-e-2026-11-01; source $H/env.sh
O=$H/evidence/w5_prd022/live; [ -e $O/DONE ] && { echo "ABORT: already ran"; exit 1; }
mkdir -p $O
export KRIYA_PRD022_EVIDENCE_DIR=$O KRIYA_PRD022_RETRY=live KRIYA_LIVE_QUALIFIED_CONFIG=$HOME/.kriya/operator/graphify-qwen3coder-native-production.yaml KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b-kriya-e52213655394 KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 PYTHONDONTWRITEBYTECODE=1
cd $KRIYA_R || exit 2
git rev-parse HEAD > $O/head.txt; git status --short >> $O/head.txt
ulimit -n 256
.venv/bin/python -m pytest -p no:cacheprovider -m live_model -ra -s --junitxml=$O/junit.xml tests/test_live_prd022_redirect_chain.py > $O/out.txt 2>&1; echo "exit=$?" | tee $O/exit.txt
touch $O/DONE
