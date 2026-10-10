#!/bin/zsh
# judge_c3_failed.sh: the FALSE_NEGATIVE check for every -c3 run Kriya did not pass - reconstruct the staged candidate(s)
# from the sealed store and judge them with the frozen EXTERNAL oracle (no model call). Output into the -c3 directories.
set -u; J=$HOME/kriya-m1-live/backend-final-closure-005/repair-003/final_candidate_judge_c3.sh
for spec in "primary T2 last" "primary T3 last" "primary T4 last" "c2 S1_A last" "c2 S3_A last" "c2 S3_A 2" "c2 S3_A 3" "c2 S4_A last" "c2 S4_A 3" "c2 S4_B last"; do
  set -- ${=spec}; echo "######## judge $1 $2 attempt=$3 $(date +%T)"; $J $1 $2 $3; echo "judge exit=$?"
done
echo "######## judge done $(date +%T)"
