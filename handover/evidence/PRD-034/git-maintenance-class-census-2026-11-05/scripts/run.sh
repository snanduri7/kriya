#!/bin/sh
# usage: run.sh <label> <extra pytest args...>
cd /home/runner/now
label=$1; shift
for i in 1 2 3 4 5; do
  PYTHONPATH=/home/runner/now KRIYA_GIT_QUIESCENCE=report:/tmp/ov_$label.jsonl /home/runner/venv/bin/python -m pytest -q -n 10 --dist loadgroup -p no:cacheprovider "$@" @overlap_tests.txt -m "not live_model" 2>&1 | grep -E "^FAILED|passed|failed" | sed "s/^/[$label run $i] /"
done
