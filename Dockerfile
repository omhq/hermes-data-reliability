FROM amazonlinux:2023 AS base

RUN dnf install -y --releasever=latest \
        python3.14 python3.14-pip awscli-2 \
        docker systemd openssh-clients shadow-utils sudo tar gzip git \
        iptables util-linux \
    && dnf clean all \
    && /usr/bin/python3.14 -c 'import sys; assert sys.version_info[:2] == (3, 14)'

WORKDIR /opt/hermes-lab-source
COPY collector/requirements.txt /tmp/collector-requirements.txt
RUN /usr/bin/python3.14 -m venv /opt/hermes-lab-collector \
    && /opt/hermes-lab-collector/bin/pip install --no-cache-dir \
        -r /tmp/collector-requirements.txt

COPY collector collector
COPY aws_access aws_access
COPY observations observations
COPY incidents incidents
COPY investigator investigator
COPY tool_boundary tool_boundary
COPY pipeline pipeline
COPY evals evals

FROM base AS setup
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/opt/hermes-lab-collector/bin/python", "-m", "investigator.runtime.local_config"]

FROM base AS test
COPY requirements.txt ./
RUN /usr/bin/python3.14 -m venv /opt/hermes-lab-test \
    && /opt/hermes-lab-test/bin/pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml ./
COPY tests tests
COPY docs/*.md docs/
ENV PATH="/opt/hermes-lab-test/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "unittest", "discover", "-s", ".", "-t", ".", "-v"]

FROM base AS runtime
COPY investigator/runtime/hermes-local-host.service /etc/systemd/system/
RUN useradd --uid 1000 --user-group --create-home ec2-user \
    && usermod -aG docker ec2-user \
    && groupadd --gid 1001 hermes-lab-executor \
    && useradd --system --uid 1001 --gid 1001 --no-create-home \
        --shell /sbin/nologin hermes-lab-executor \
    && systemctl mask systemd-udevd.service systemd-udevd-control.socket \
        systemd-udevd-kernel.socket getty.target console-getty.service \
        systemd-networkd.service systemd-networkd-wait-online.service \
        systemd-resolved.service \
    && systemctl enable docker.service hermes-local-host.service
ENV container=docker
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/bin/bash", "/opt/hermes-lab-source/investigator/runtime/container-init.sh"]
