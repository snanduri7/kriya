#!/bin/zsh
# CAGC-0 A/B matrix driver: per task, A -> B -> B -> A (labels r1..r4), each run followed by evidence capture
# and the fixed judge, before the next replicate resets the workspace. Never edits Kriya, rules, tasks,
# configs, models, qualification records or the judge. usage: matrix.sh [task ...] (default: all, fixed order)
set -u
AB=$HOME/kriya-cagc-ab
if (( $# )); then TASKS=($@); else
  TASKS=(java-symbol-fraction java-behavior-accents java-symbol-chop spring-boot-pagesize spring-xml-pettypes-cache
         python-behavior-maxsplit python-symbol-one python-symbol-ichunked python-symbol-zipb python-error-invalidurl)
fi
for T in $TASKS; do [ -d $AB/A/ws/$T ] && [ -d $AB/B/ws/$T ] || { echo "unknown task $T"; exit 2; }; done
ORDER=(A:r1 B:r2 B:r3 A:r4)
for T in $TASKS; do
  for P in $ORDER; do
    N=${P%%:*}; L=${P##*:}; OUT=$AB/$N/evidence/$T.$L; W=$AB/$N/ws/$T
    [ -f $OUT/done ] && { echo "skip $N $T $L (done)"; continue; }
    mkdir -p $OUT
    # Kriya's own workspace records (decision ledger etc.): remember their length to keep only this run's lines.
    typeset -A BEFORE; BEFORE=()
    for f in $W/.kriya/**/*.jsonl(N); do BEFORE[$f]=$(wc -l < $f); done
    python3 -c "import sqlite3,sys,os;p=sys.argv[1];print('\n'.join(r[0] for r in sqlite3.connect('file:'+p+'?mode=ro',uri=True).execute('SELECT run_id FROM runs')) if os.path.exists(p) else '')" $AB/$N/state/traces.db > $OUT/.runs_before
    $AB/run_ab.sh $N $T $L > $OUT/run.txt 2>&1
    cp $AB/$N/logs/$T.$L.generate.log $AB/$N/logs/$T.$L.analyze.log $OUT/ 2>/dev/null
    # This run's trace rows (every runs row written during it, enforce/subtask rows included).
    python3 - $AB/$N/state/traces.db $OUT/.runs_before $OUT/traces.json <<'EOF'
import json, sqlite3, sys
db, before, out = sys.argv[1:]
import os
seen = set(open(before).read().split())
if not os.path.exists(db):
    json.dump([], open(out, "w")); print("traces rows captured: 0 (no traces.db)"); sys.exit(0)
con = sqlite3.connect("file:" + db + "?mode=ro", uri=True); cols = [r[1] for r in con.execute("PRAGMA table_info(runs)")]
rows = [dict(zip(cols, row)) for row in con.execute("SELECT * FROM runs") if row[0] not in seen]
json.dump(rows, open(out, "w"), default=str)
print(f"traces rows captured: {len(rows)}")
EOF
    for f in $W/.kriya/**/*.jsonl(N); do
      n=${BEFORE[$f]:-0}; rel=${f#$W/.kriya/}; mkdir -p $OUT/kriya/${rel:h}
      tail -n +$((n + 1)) $f > $OUT/kriya/$rel
    done
    (cd $W && git status --porcelain=v1 > $OUT/workspace.status && git diff $(cat $AB/$N/$T.base) > $OUT/workspace.diff \
       && git ls-files --others --exclude-standard -z | xargs -0 tar -cf $OUT/untracked.tar 2>/dev/null; true)
    $AB/judge_ab.sh $N $T $L > $OUT/judge.txt 2>&1
    touch $OUT/done
    echo "$(date +%T) $N $T $L | $(grep -E 'generate exit' $OUT/run.txt) | $(cat $OUT/judge.result 2>/dev/null)"
  done
done
echo MATRIX_COMPLETE
