#!/bin/zsh
# Freeze POST-REG-R1 arm $1's terminal state before any external evaluation.
set -u
A=$1; D=$HOME/kriya-m1-live; N=postreg-$A; T=$N; W=$D/ws-$N/gr1-graphify; OUT=$D/evidence/postreg/run-$A; F=$OUT/frozen; BASE=$(cat $D/ws-$N/gr1-graphify.base)
[ -f $OUT/wall.json ] || { echo "ABORT: the run has not terminated"; exit 1; }
[ -e $F ] && { echo "ABORT: already frozen"; exit 1; }
mkdir -p $F
{ echo "frozen_utc $(date -u +%FT%TZ)"; echo "HEAD $(git -C $W rev-parse HEAD)"; echo "tree $(git -C $W rev-parse 'HEAD^{tree}')"; echo "run_id $(cat $OUT/run_id.txt)"; } > $F/identity.txt
git -C $W status --porcelain --ignored > $F/git_status.txt
git -C $W log --format='%H %P %an %s' $BASE..HEAD > $F/commits_since_base.txt
git -C $W diff --stat $BASE HEAD > $F/changed_files.txt; git -C $W diff $BASE HEAD > $F/committed_diff.patch
git -C $W diff > $F/uncommitted_diff.patch; git -C $W worktree list > $F/worktrees.txt
[ -d $W/.kriya/control ] && cp -R $W/.kriya/control $F/workspace_kriya_control
RUN=$(cat $OUT/run_id.txt); [ -n "$RUN" ] && [ -d $D/logs-$N/runs/$RUN ] && cp -R $D/logs-$N/runs/$RUN $F/run_log
cp $D/logs-$N/$T.generate.log $D/logs-$N/$T.analyze.log $F/ 2>/dev/null
{ echo "== containers"; docker ps -a --format '{{.Names}} {{.Status}} {{.Image}}'; echo "== networks"; docker network ls --format '{{.Name}}';
  echo "== processes naming the workspace"; pgrep -fl "$W" || true; echo "== workspace lock"; ls -la $W/.kriya/run.lock 2>&1; } > $F/cleanup_check.txt 2>&1
( cd $F && find . -type f ! -name MANIFEST.sha256 -exec shasum -a 256 {} + | sort -k2 > MANIFEST.sha256 )
touch $F/FROZEN; echo "Frozen at $F. Next: evaluate-postreg.sh $A"
