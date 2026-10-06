#!/bin/zsh
# OWNER STEP (SEC-009): approve the GR-R1 canonical Graphify workspace config into ~/kriya-m1-live/authority.
# Review first: diff ~/.kriya/operator/provider-contract-v5-production.yaml ~/kriya-m1-live/config/gr1-graphify.yaml
#   (expected: only paths.skills / paths.state / paths.memory differ)
D=$HOME/kriya-m1-live; source $D/env-gr1.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"; kriya version | head -2
T=gr1-graphify; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
