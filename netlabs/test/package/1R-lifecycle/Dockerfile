# Copyright (C) 2026 Donnie V. Savage
# Package acceptance base: no OpenEIGRP files or packages are preinstalled.
FROM debian:latest
ENV DEBIAN_FRONTEND=noninteractive container=docker
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        systemd systemd-sysv dbus iproute2 mount python3 binutils ca-certificates && \
    rm -rf /var/lib/apt/lists/*
# The official Debian Docker base denies all maintainer-script service actions.
# This disposable image runs systemd: allow package stop and try-restart.
RUN rm -f /usr/sbin/policy-rc.d
COPY init.sh /usr/local/sbin/openeigrp-package-init
RUN chmod 0755 /usr/local/sbin/openeigrp-package-init
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/usr/local/sbin/openeigrp-package-init"]
CMD ["/lib/systemd/systemd"]
