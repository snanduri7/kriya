#!/bin/zsh
# QWEN3.8 /8 QUALIFICATION - NATIVE-32768 PROFILE (one run; records only in qualifications-qwen38).
set -u
D=$HOME/kriya-m1-live; source $D/env-qwen38.sh; E=$D/evidence/qwen38-qual-native32768; CFG=$D/config/qwen38-qualification.yaml
[ "$(shasum -a 256 $CFG | cut -d' ' -f1)" = "f92836975c50c35ca5ccc51b20331fbaac2ffe8c2ffb941f60ff426e8d561c22" ] || { echo "ABORT: config digest"; exit 1; }
cd $D/ws/qwen38-qual || exit 2
kriya --config $CFG authority inspect 2>&1 | grep -q "CURRENT and covers all pending fields" || { echo "ABORT: SEC-009 not CURRENT"; exit 1; }
{ date -u +%FT%TZ; pmset -g therm; } > $E/pmset_before.txt 2>&1
curl -s http://localhost:11434/api/ps > $E/api_ps_before.json
( while true; do print -r -- "$(date -u +%FT%TZ) $(pmset -g therm 2>&1 | tr '\n' ' ')"; sleep 10; done ) > $E/pmset_samples.txt 2>&1 &
SAMPLER=$!
S=$(date +%s)
kriya --config $CFG model qualify --model qwen3.8:27b --out $E/qualification_record.json > $E/qualify_console.log 2>&1; RC=$?
echo "exit=$RC wall_seconds=$(( $(date +%s) - S ))" > $E/qualify_exit.txt
kill $SAMPLER 2>/dev/null
{ date -u +%FT%TZ; pmset -g therm; } > $E/pmset_after.txt 2>&1
curl -s http://localhost:11434/api/ps > $E/api_ps_after.json
ls -la $D/qualifications-qwen38 > $E/qualification_store_listing.txt
cat $E/qualify_exit.txt
