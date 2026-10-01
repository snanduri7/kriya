#!/bin/sh
# Delegate cgroup v2 controllers (as docker:dind does, so --memory works),
# start the container's own docker daemon, then run the given command.
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then mkdir -p /sys/fs/cgroup/init; xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || :; sed -e "s/ / +/g" -e "s/^/+/" < /sys/fs/cgroup/cgroup.controllers > /sys/fs/cgroup/cgroup.subtree_control; fi
dockerd >/var/log/dockerd.log 2>&1 &
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
chmod 666 /var/run/docker.sock
exec sh -c "$*"
