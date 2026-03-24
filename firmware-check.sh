#!/bin/bash
# DIRIGERA Firmware Check via Docker Container
# Nutzung: ./firmware-check.sh [--dry-run]
docker exec dirigera-bridge python /app/firmware-check.py "$@"
