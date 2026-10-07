#!/bin/zsh
# OWNER STEP (SEC-009): approve the B3 A3 live workspace config into ~/kriya-m1-live/authority.
# Review first: diff ~/.kriya/operator/provider-contract-v5-production.yaml ~/kriya-m1-live/config/b3-a3-java-lang.yaml
D=$HOME/kriya-m1-live; source $D/env-b3.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"; kriya version | head -2
T=b3-a3-java-lang; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
