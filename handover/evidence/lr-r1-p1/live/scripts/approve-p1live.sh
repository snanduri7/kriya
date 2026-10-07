#!/bin/zsh
# OWNER STEP (SEC-009): digest-bound approval of the P1 live-confirmation workspace's config, written to
# ~/kriya-m1-live/authority (KRIYA_AUTHORITY_HOME); ~/.kriya/authority is not touched. Review first:
#   diff ~/.kriya/operator/provider-contract-v5-production.yaml ~/kriya-m1-live/config/p1live-lang-countwords.yaml
D=$HOME/kriya-m1-live; source $D/env-p1.sh; T=p1live-lang-countwords
echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"
(cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
