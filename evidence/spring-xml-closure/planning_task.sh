#!/bin/zsh
# usage: planning_task.sh <label> <ws/store task> <goal task> <config name>
set -u
L=$1; WT=$2; GT=$3; CFG=$4; X=${0:a:h}; M=$HOME/kriya-bench-live/matrix; W=$M/ws/$WT; O=$X/plan_runs/$L
K=/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin
GOAL=$(awk -F'\t' -v t=$GT '$1==t {print $2}' $M/tasks.tsv)
mkdir -p $O; cd $W || exit 2
tar cf $O/kriya_backup.tar .kriya
find .kriya -type f -not -path '.kriya/worktree*' -exec shasum {} + | sort > $O/control_before.sha
ls .kriya/control/plans .kriya/control/planning-diagnostics > $O/before.list 2>/dev/null
STASHED=0; if [ -n "$(git status --short | grep -v '^??')" ]; then git stash -q && STASHED=1; fi
$K/kriya --config $M/store/$CFG.yaml analyze . > $O/analyze.log 2>&1
$K/python $X/planning_only.py $O/calls.json --config $M/store/$CFG.yaml generate "$GOAL" -y > $O/generate.log 2>&1; tail -1 $O/generate.log | cut -c1-300
for d in plans planning-diagnostics; do for f in .kriya/control/$d/*; do grep -qx "$(basename $f)" $O/before.list || cp $f $O/; done; done
cp .kriya/control/decisions.jsonl $O/decisions.jsonl 2>/dev/null
[ $STASHED = 1 ] && git stash pop -q
rm -rf .kriya && tar xf $O/kriya_backup.tar && git worktree prune
find .kriya -type f -not -path '.kriya/worktree*' -exec shasum {} + | sort > $O/control_after.sha
cmp -s $O/control_before.sha $O/control_after.sha && echo "control restored identical" || echo "CONTROL DIFFERS"
git status --short | grep -v '^??' | head -3
