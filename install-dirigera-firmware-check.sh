#!/bin/bash
set -e
cd "$(dirname "$0")"
sudo cp systemd/dirigera-firmware-check.service systemd/dirigera-firmware-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dirigera-firmware-check.timer
echo "Timer installiert."
systemctl list-timers dirigera-*
