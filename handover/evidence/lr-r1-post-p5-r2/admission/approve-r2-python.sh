#!/bin/zsh
# OWNER STEP (SEC-009): approve the two extra R2 Python candidates into ~/kriya-m1-live/authority; ~/.kriya/authority
# is not touched. Review first:
#   for f in ~/kriya-m1-live/config/r2c-py-inflect.yaml ~/kriya-m1-live/config/r2c-py-cssselect2.yaml; do diff ~/.kriya/operator/provider-contract-v5-production.yaml $f; done
D=$HOME/kriya-m1-live; source $D/env-p5.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"
for T in r2c-py-inflect r2c-py-cssselect2; do
  echo "== $T"; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
done
