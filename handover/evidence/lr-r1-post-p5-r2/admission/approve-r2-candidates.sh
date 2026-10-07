#!/bin/zsh
# OWNER STEP (SEC-009): approve the R2 candidate workspaces' configs into ~/kriya-m1-live/authority
# (KRIYA_AUTHORITY_HOME); ~/.kriya/authority is not touched. Review first:
#   for f in ~/kriya-m1-live/config/r2c-*.yaml; do diff ~/.kriya/operator/provider-contract-v5-production.yaml $f; done
D=$HOME/kriya-m1-live; source $D/env-p5.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"
for T in r2c-py-moreit r2c-py-httpx r2c-py-tomli r2c-java-lang-a r2c-java-lang-b r2c-java-cli r2c-spring-xml; do
  echo "== $T"; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
done
