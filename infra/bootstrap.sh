#!/bin/bash

set -euo pipefail

dnf install -y docker git
dnf install -y --releasever=latest python3.14 python3.14-pip
systemctl enable --now docker
usermod -aG docker ec2-user

install -d -m 0750 -o ec2-user -g ec2-user /srv/hermes-lab
install -d -m 0700 -o ec2-user -g ec2-user /srv/hermes-lab/hermes
install -d -m 0700 -o ec2-user -g ec2-user /srv/hermes-lab/findings

cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF

systemctl restart docker
touch /srv/hermes-lab/bootstrap-complete
