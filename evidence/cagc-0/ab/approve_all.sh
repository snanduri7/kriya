#!/bin/zsh
# OWNER STEP (SEC-009): approve each CAGC-0 A/B arm workspace's config. Each config is the v5 operator config with
# ONLY paths.skills/state/memory changed (review: diff ~/.kriya/operator/provider-contract-v5-production.yaml
# ~/kriya-cagc-ab/<arm>/config/<task>.yaml). Approvals land in the arm's own KRIYA_AUTHORITY_HOME; none is copied.
AB=$HOME/kriya-cagc-ab
for N in A B; do
  ( source $AB/$N/env.sh
    for T in $(ls $AB/$N/ws); do
      echo "== $N $T"; (cd $AB/$N/ws/$T && kriya --config $AB/$N/config/$T.yaml authority approve --confirm 2>&1 | tail -1)
    done )
done
