#!/bin/zsh
# OWNER STEP (SEC-009): digest-bound approval of the five post-P5 validation workspaces' configs, written to
# ~/kriya-m1-live/authority (KRIYA_AUTHORITY_HOME); ~/.kriya/authority is not touched. Review first:
#   for f in ~/kriya-m1-live/config/v5-*.yaml; do diff ~/.kriya/operator/provider-contract-v5-production.yaml $f; done
D=$HOME/kriya-m1-live; source $D/env-p5.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"
for T in v5-t1-httpx-headers-count v5-t2-moreit-count-unique v5-t3-lang-ascii-whitespace v5-t4-lang-count-true v5-t5-petclinic-pet-count; do
  echo "== $T"; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
done
