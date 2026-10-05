#!/bin/zsh
# OWNER STEP (SEC-009): approve each validation workspace's config with the digest-bound approval command.
# Each config is the v5 operator config with ONLY paths.skills/state/memory changed (+ evidence capture: off for
# the control). Review first:  for f in ~/kriya-m1-live/config/*.yaml; do diff ~/.kriya/operator/provider-contract-v5-production.yaml $f; done
# Approvals land in ~/kriya-m1-live/authority (KRIYA_AUTHORITY_HOME); ~/.kriya/authority is not touched.
D=$HOME/kriya-m1-live; source $D/env.sh
for T in run1-httpx-isvalid run2-lang-countwords run3-petclinic ctrl-httpx-isvalid-off; do
  echo "== $T"; (cd $D/ws/$T && kriya --config $D/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
done
