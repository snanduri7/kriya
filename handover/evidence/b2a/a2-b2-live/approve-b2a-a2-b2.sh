#!/bin/zsh
# OWNER STEP (SEC-009): approve the A2 and B2 workspaces' configs into ~/kriya-m1-live/authority. Review first:
#   for t in b2a-a2-py-freezegun b2a-b2-py-cssselect2; do diff ~/.kriya/operator/provider-contract-v5-production.yaml ~/kriya-m1-live/config/$t.yaml; done
D=$HOME/kriya-m1-live; source $D/env-b2a.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"; kriya version | head -2
for T in b2a-a2-py-freezegun b2a-b2-py-cssselect2; do echo "== $T"; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1); done
