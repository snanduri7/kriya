#!/bin/zsh
# OWNER STEP (SEC-009): approve the B2-a A1 sentinel workspace config into ~/kriya-m1-live/authority.
# Review first: diff ~/.kriya/operator/provider-contract-v5-production.yaml ~/kriya-m1-live/config/b2a-a1-py-freezegun.yaml
D=$HOME/kriya-m1-live; source $D/env-b2a.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"; kriya version | head -2
T=b2a-a1-py-freezegun; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
