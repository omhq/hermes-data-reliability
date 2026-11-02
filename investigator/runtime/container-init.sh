#!/usr/bin/env bash
set -euo pipefail

# Keep systemd and the nested daemon inside this container's cgroup namespace.
mount --make-rprivate /
if ! test -w /sys/fs/cgroup/cgroup.procs; then
    umount /sys/fs/cgroup
    mount -t cgroup2 none /sys/fs/cgroup
fi

mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "storage-driver": "overlay2",
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
EOF

cp /opt/hermes-lab-source/investigator/runtime/hermes-local-host.service \
    /etc/systemd/system/hermes-local-host.service
exec /sbin/init
