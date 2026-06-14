#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root." >&2
    exit 1
fi

apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-cjk \
    libnss3 \
    locales \
    postgresql \
    python3 \
    python3-venv \
    xvfb

id stage1-2gis >/dev/null 2>&1 || useradd --system --home /var/lib/stage1-2gis --shell /usr/sbin/nologin stage1-2gis
install -d -o stage1-2gis -g stage1-2gis -m 0750 /opt/stage1-2gis /opt/stage1-2gis/releases /opt/stage1-2gis/browsers
install -d -o root -g stage1-2gis -m 0750 /etc/stage1-2gis
install -m 0644 deploy/stage1-2gis-worker.service /etc/systemd/system/stage1-2gis-worker.service
install -m 0644 deploy/stage1-2gis-tmpfiles.conf /etc/tmpfiles.d/stage1-2gis.conf
systemd-tmpfiles --create /etc/tmpfiles.d/stage1-2gis.conf
systemctl daemon-reload

echo "Base packages installed. Deploy a release, configure /etc/stage1-2gis/stage1-2gis.env, then enable the service."
