#!/bin/zsh
# OWNER STEP (SEC-009): approve the FS-1C1 A1 sentinel workspace config into ~/kriya-m1-live/authority.
# Review first: diff ~/.kriya/operator/provider-contract-v5-production.yaml ~/kriya-m1-live/config/fs1c1-a1-py-freezegun.yaml
D=$HOME/kriya-m1-live; source $D/env-fs1c1.sh; echo "KRIYA_AUTHORITY_HOME=$KRIYA_AUTHORITY_HOME"; kriya version | head -2
T=fs1c1-a1-py-freezegun; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
