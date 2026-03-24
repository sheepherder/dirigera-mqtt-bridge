#!/bin/bash
set -e
sudo systemctl disable --now dirigera-firmware-check.timer
sudo rm -f /etc/systemd/system/dirigera-firmware-check.service /etc/systemd/system/dirigera-firmware-check.timer
sudo systemctl daemon-reload
echo "Timer entfernt."
