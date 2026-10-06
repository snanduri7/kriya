#!/bin/zsh
# QWEN3-CODER ROLE-PLACEMENT /8 QUALIFICATION (explicit_agent_llms identity 375715b7 in the matched qwen3.8 config). ONE execution only.
set -u
D=$HOME/kriya-m1-live; source $D/env-qwen3coder-rp.sh; E=$D/evidence/qwen3coder-rp-qual/run; CFG=$D/config/qwen38-matched-developer.yaml
[ -e $E ] && { echo "ABORT: $E exists - one formal execution only"; exit 1; }
[ "$(shasum -a 256 $CFG | cut -d' ' -f1)" = "7e5952077c45a7e579dac398ec8095c9e5485ea11a3dc94a706fab0480f622d4" ] || { echo "ABORT: config digest"; exit 1; }
mkdir -p $E; cd $D/ws/qwen3coder-roleplacement-qual || exit 2
kriya --config $CFG authority inspect 2>&1 | grep -q "CURRENT and covers all pending fields" || { echo "ABORT: SEC-009 not CURRENT"; exit 1; }
ls -la $D/qualifications-qwen3coder-roleplacement > $E/store_before.txt
{ date -u +%FT%TZ; pmset -g therm; } > $E/pmset_before.txt 2>&1
curl -s http://localhost:11434/api/ps > $E/api_ps_before.json
( while true; do print -r -- "$(date -u +%FT%TZ) $(pmset -g therm 2>&1 | tr '\n' ' ')"; sleep 10; done ) > $E/pmset_samples.txt 2>&1 &
SAMPLER=$!
S=$(date +%s)
kriya --config $CFG model qualify --model qwen3-coder:30b-kriya-e52213655394 --out $E/qualification_record.json > $E/qualify_console.log 2>&1; RC=$?
echo "exit=$RC wall_seconds=$(( $(date +%s) - S ))" > $E/qualify_exit.txt
kill $SAMPLER 2>/dev/null
{ date -u +%FT%TZ; pmset -g therm; } > $E/pmset_after.txt 2>&1
curl -s http://localhost:11434/api/ps > $E/api_ps_after.json
ls -la $D/qualifications-qwen3coder-roleplacement > $E/store_after.txt
cat $E/qualify_exit.txt
