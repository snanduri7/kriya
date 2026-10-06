#!/bin/zsh
# OWNER STEP (SEC-009): approve the post-P3 validation workspaces' configs into ~/kriya-m1-live/authority
# (KRIYA_AUTHORITY_HOME); ~/.kriya/authority is not touched. Review first:
#   for f in ~/kriya-m1-live/config/p3v-*.yaml; do diff ~/.kriya/operator/provider-contract-v5-production.yaml $f; done
D=$HOME/kriya-m1-live; source $D/env-p3c.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"
for T in p3v-a1-py-freezegun p3v-a2-py-freezegun p3v-a3-java-lang p3v-a4-java-cli p3v-a5-spring-xml p3v-b1-java-lang p3v-b2-py-cssselect2; do
  echo "== $T"; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
done
