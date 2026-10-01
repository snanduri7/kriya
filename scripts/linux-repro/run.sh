#!/usr/bin/env bash
# Linux reproduction of the hosted CI runner (ubuntu-24.04, non-root uid
# 1001, its own docker daemon so bind mounts have real Linux uid semantics -
# Docker Desktop's file sharing on macOS hides them).
#
#   scripts/linux-repro/run.sh <git-rev> [pytest args...]
#
# Clones <git-rev> of this repository (tracked files and .git only, like a
# CI checkout - never the working tree's ignored state), installs it into a
# venv and runs pytest as the non-root user. The daemon's image store and
# the runner home persist in docker volumes between runs.
set -euo pipefail
cd "$(dirname "$0")/../.."
REV="${1:?usage: run.sh <git-rev> [pytest args...]}"; shift
docker build -q -t kriya-linux-repro scripts/linux-repro >/dev/null
if ! docker ps --format '{{.Names}}' | grep -qx kriya-linux-ctr; then
  docker rm -f kriya-linux-ctr >/dev/null 2>&1 || true
  # /var/lib/docker on a volume: overlayfs cannot stack on the container's
  # own overlay. --cgroupns=private + entry.sh's delegation: --memory works.
  docker run -d --name kriya-linux-ctr --privileged --cgroupns=private \
    -v kriya-linux-dind-lib:/var/lib/docker -v kriya-linux-runner-home:/home/runner \
    -v "$PWD":/src:ro kriya-linux-repro "sleep infinity" >/dev/null
  sleep 10
fi
docker exec kriya-linux-ctr chown runner:runner /home/runner
docker exec -u runner kriya-linux-ctr sh -c "
  set -e; cd /home/runner; git config --global --add safe.directory '*'
  rm -rf tree && git clone -q /src tree && git -C tree checkout -q '$REV'
  [ -x venv/bin/python ] || python3 -m venv venv
  venv/bin/pip install -q -e 'tree[dev]'
  uname -srm; id; docker version --format 'docker {{.Server.Version}}'; java -version 2>&1 | head -1; mvn -v | head -1; venv/bin/python -V"
docker exec -u runner -w /home/runner/tree kriya-linux-ctr sh -c "PATH=/home/runner/venv/bin:\$PATH python -m pytest -p no:cacheprovider -q $*"
