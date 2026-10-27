ARG HERMES_IMAGE
FROM ${HERMES_IMAGE}
USER root
RUN /opt/hermes/.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 14)'
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && usermod -d /home/hermes -s /bin/bash hermes && passwd -d hermes \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/bin/sh", "-c", "mkdir -p /run/sshd && exec /usr/sbin/sshd -D -e -f /tool-ssh/sshd_config"]
