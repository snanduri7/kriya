#!/bin/zsh
# Wave-5 A4: the signed live tests with no defensible current evidence, ONCE, at the Gate-E qualified identity. Background, no polling.
set -u
H=/Users/sriramnanduri/kriya-gate-e-2026-11-01; source $H/env.sh
O=$H/evidence/a4_live; [ -e $O/DONE ] && { echo "ABORT: already ran"; exit 1; }
export KRIYA_BATCH5_EVIDENCE_DIR=$O/batch5 KRIYA_BATCH6_EVIDENCE_DIR=$O/batch6 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b-kriya-e52213655394 KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=$H/scripts/live_a4
cd $O/run || exit 2
python -m pytest -p no:cacheprovider -c /dev/null --rootdir=$O/run -ra -s --junitxml=$O/junit_batch5.xml $H/scripts/live_a4/test_a4_batch5.py > $O/batch5.txt 2>&1; echo "batch5 exit=$?" | tee -a $O/exits.txt
python -m pytest -p no:cacheprovider -c /dev/null --rootdir=$O/run -ra -s --junitxml=$O/junit_batch6.xml $H/scripts/live_a4/test_a4_batch6.py > $O/batch6.txt 2>&1; echo "batch6 exit=$?" | tee -a $O/exits.txt
touch $O/DONE
